"""
MCA-style gift-battle overlay server.
Chạy: pip install -r requirements.txt && python server.py
OBS Browser Source: http://YOUR_SERVER:8000/overlay
Control panel:      http://YOUR_SERVER:8000/control
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Optional
from urllib.parse import quote

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, GiftEvent
import logging

# Suppress lỗi CompetitionEvent parse từ thư viện TikTokLive (bug của thư viện, không ảnh hưởng gift)
logging.getLogger("TikTokLive").setLevel(logging.CRITICAL)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# --------------------------------------------------------------------------
# In-memory state
# --------------------------------------------------------------------------

GIFT_GRACE_SECONDS = 3   # Số giây đệm sau khi hết giờ để flush quà còn đang bay

state = {
    "room": None,
    "connected": False,
    "last_error": None,
    "idols": [],
    "timer": {
        "duration": 300,
        "remaining": 300,
        "running": False,
        "finished": False,
        "finishing": False,   # True = đang trong grace period "Thống kê"
        "grace_remaining": 0, # Đếm ngược grace period
        "grace_config": 3,    # Số giây thống kê do user đặt (đọc bởi control)
    },
    # Chế độ nhận điểm
    # mode: "auto" = timer điều khiển bật/tắt nhận điểm
    #       "manual" = bấm tay bật/tắt
    "gift_mode": "auto",       # "auto" | "manual"
    "accepting_gifts": False,  # True = đang nhận điểm từ gift

    # Chế độ mục tiêu điểm
    "target": {
        "enabled": False,       # True = đang dùng chế độ mục tiêu
        "score": 1000,          # Mục tiêu cần đạt (diamonds)
        "reached": False,       # True = đã đạt mục tiêu (vẫn tiếp tục nhận điểm)
    },
}

sockets: list[WebSocket] = []

# Queue để tách việc nhận gift event khỏi broadcast — tránh block event loop
_gift_queue: asyncio.Queue = None  # khởi tạo trong startup


async def broadcast_state():
    dead = []
    payload = json.dumps({"type": "state", "data": state})
    for ws in sockets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in sockets:
            sockets.remove(ws)


async def broadcast_event(event: dict):
    dead = []
    payload = json.dumps(event)
    for ws in sockets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in sockets:
            sockets.remove(ws)


def get_idol(idol_id: str):
    return next((i for i in state["idols"] if i["id"] == idol_id), None)


def get_active_idol():
    return next((i for i in state["idols"] if i["active"]), None)


# --------------------------------------------------------------------------
# TikTok profile avatar lookup
# --------------------------------------------------------------------------

def normalize_tiktok_id(value: str) -> str:
    value = (value or "").strip()
    value = value.split("?")[0].rstrip("/")
    if "/@" in value:
        value = value.split("/@", 1)[1]
    elif value.startswith("@"):
        value = value[1:]
    elif value.startswith("https://www.tiktok.com/"):
        value = value.rsplit("/", 1)[-1]
    return value.lstrip("@").strip()


_TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}

# Session dùng chung để giữ cookie giữa các lần gọi — TikTok thường trả về
# một trang "rút gọn" không nhúng JSON nếu request không có cookie hợp lệ.
_tiktok_session = requests.Session()
_tiktok_session.headers.update(_TIKTOK_HEADERS)
_tiktok_cookies_ready = False


def _ensure_tiktok_cookies():
    """
    Ghé trang chủ TikTok một lần để lấy cookie (ttwid, tt_csrf_token, ...).
    Request tới trang profile mà không có cookie này rất hay bị TikTok trả về
    một trang rỗng/chặn thay vì trang thật.
    """
    global _tiktok_cookies_ready
    if _tiktok_cookies_ready:
        return
    try:
        r = _tiktok_session.get("https://www.tiktok.com/", timeout=15)
        logging.info(
            "TikTok avatar: bootstrap cookie -> HTTP %s, %d cookie(s)",
            r.status_code, len(_tiktok_session.cookies),
        )
    except requests.RequestException as exc:
        logging.warning("TikTok avatar: bootstrap cookie lỗi: %s", exc)
    _tiktok_cookies_ready = True


def _looks_like_block_page(html: str) -> bool:
    head = html[:4000].lower()
    markers = ["verify to continue", "captcha", "checking your browser", "access denied", "something went wrong"]
    return any(m in head for m in markers)


def _find_tiktok_user_info(obj):
    """Đệ quy tìm object user chứa avatar trong JSON lồng nhau của TikTok."""
    if isinstance(obj, dict):
        if "avatarLarger" in obj or "avatarMedium" in obj:
            return obj
        if "userInfo" in obj and isinstance(obj["userInfo"], dict):
            found = _find_tiktok_user_info(obj["userInfo"])
            if found:
                return found
            user = obj["userInfo"].get("user")
            if isinstance(user, dict):
                return user
        for value in obj.values():
            result = _find_tiktok_user_info(value)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_tiktok_user_info(item)
            if result:
                return result
    return None


def _extract_tiktok_json(html: str):
    """Trả về (data, script_name) từ script JSON nhúng trong trang, hoặc (None, lý do lỗi)."""
    for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
        match = re.search(
            rf'<script id="{script_id}"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not match:
            continue
        try:
            return json.loads(match.group(1)), script_id
        except (json.JSONDecodeError, ValueError) as exc:
            return None, f"tìm thấy script {script_id} nhưng JSON lỗi ({exc})"
    return None, "không tìm thấy script __UNIVERSAL_DATA_FOR_REHYDRATION__/SIGI_STATE"


def _fallback_regex_avatar(html: str) -> str:
    """Phương án dự phòng: mò trực tiếp trong HTML nếu cách đọc JSON không ăn."""
    patterns = [
        r'"avatarLarger":"(https?:\\/\\/[^"\\]+)',
        r'"avatarMedium":"(https?:\\/\\/[^"\\]+)',
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.I)
        if m:
            avatar = m.group(1).replace("\\/", "/").replace("&amp;", "&")
            if avatar.startswith("http"):
                return avatar
    return ""


def fetch_tiktok_avatar(tiktok_id: str) -> str:
    """
    Lấy link avatar TikTok:
    1) Bootstrap cookie qua trang chủ (chỉ 1 lần).
    2) Đọc JSON (__UNIVERSAL_DATA_FOR_REHYDRATION__ / SIGI_STATE) nhúng trong trang.
    3) Nếu không được, dò regex avatarLarger/og:image trong HTML thô làm dự phòng.
    4) Thử lại tối đa 2 lần nếu lần đầu không ra kết quả.
    """
    username = normalize_tiktok_id(tiktok_id)
    if not username:
        return ""

    _ensure_tiktok_cookies()

    url = f"https://www.tiktok.com/@{quote(username)}"
    last_html = ""
    for attempt in range(1, 3):
        try:
            resp = _tiktok_session.get(url, timeout=15, allow_redirects=True)
        except requests.RequestException as exc:
            logging.warning("TikTok avatar: lỗi kết nối tới @%s (lần %d): %s", username, attempt, exc)
            continue

        if resp.status_code != 200:
            logging.warning(
                "TikTok avatar: @%s trả về HTTP %s (lần %d, có thể user không tồn tại hoặc bị chặn)",
                username, resp.status_code, attempt,
            )
            continue

        html = resp.text
        last_html = html
        logging.info("TikTok avatar: @%s -> tải trang OK (lần %d), %d ký tự HTML", username, attempt, len(html))

        data, info = _extract_tiktok_json(html)
        if data is not None:
            user = _find_tiktok_user_info(data)
            if user:
                avatar = user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb") or ""
                if str(avatar).startswith("http"):
                    return avatar
                logging.warning("TikTok avatar: @%s -> có %s nhưng object user không có avatar hợp lệ (lần %d)", username, info, attempt)
            else:
                logging.warning("TikTok avatar: @%s -> có %s nhưng không tìm thấy userInfo/avatarLarger trong JSON (lần %d)", username, info, attempt)
        else:
            logging.warning("TikTok avatar: @%s -> %s (lần %d)", username, info, attempt)

        fallback = _fallback_regex_avatar(html)
        if fallback:
            logging.info("TikTok avatar: @%s -> lấy được avatar qua regex dự phòng (lần %d)", username, attempt)
            return fallback

    if last_html:
        if _looks_like_block_page(last_html):
            logging.warning("TikTok avatar: @%s -> có vẻ TikTok trả trang chặn bot/captcha (IP server có thể bị TikTok chặn)", username)
        snippet = re.sub(r"\s+", " ", last_html[:400]).strip()
        logging.warning("TikTok avatar: @%s -> đoạn đầu HTML nhận được: %r", username, snippet)

    return ""


# --------------------------------------------------------------------------
# TikTok Live connection
# --------------------------------------------------------------------------

current_client: Optional[TikTokLiveClient] = None
current_task: Optional[asyncio.Task] = None


async def disconnect_room():
    global current_client, current_task
    if current_client is not None:
        try:
            await current_client.disconnect()
        except Exception:
            pass
    if current_task is not None:
        current_task.cancel()
    current_client = None
    current_task = None
    state["connected"] = False


async def connect_room(unique_id: str):
    global current_client, current_task
    await disconnect_room()

    client = TikTokLiveClient(unique_id=unique_id)

    @client.on(ConnectEvent)
    async def on_connect(_event):
        state["connected"] = True
        state["last_error"] = None
        await broadcast_state()

    @client.on(DisconnectEvent)
    async def on_disconnect(_event):
        state["connected"] = False
        await broadcast_state()

    @client.on(GiftEvent)
    async def on_gift(event: GiftEvent):
        # Chỉ cộng điểm khi đang trong trạng thái accepting_gifts
        # (bao gồm cả grace period finishing)
        if not state["accepting_gifts"] and not state["timer"].get("finishing"):
            return
        # Bỏ qua sự kiện giữa chừng của streak (chờ event cuối cùng)
        if event.streaking:
            return

        repeat_count = event.repeat_count or 1
        diamond_count = event.gift.diamond_count if event.gift else 0
        diamonds = repeat_count * diamond_count

        if diamonds > 0:
            # Đưa vào queue — không await broadcast tại đây để tránh block
            if _gift_queue is not None:
                await _gift_queue.put(diamonds)

    current_client = client
    state["room"] = unique_id
    state["last_error"] = None
    current_task = asyncio.create_task(_run_client(client))
    await broadcast_state()


async def _run_client(client: TikTokLiveClient):
    try:
        await client.start()
    except Exception as e:
        state["connected"] = False
        state["last_error"] = str(e)
        await broadcast_state()


# --------------------------------------------------------------------------
# Timer loop — tự động bật/tắt nhận điểm khi mode = "auto"
# --------------------------------------------------------------------------

async def timer_loop():
    while True:
        await asyncio.sleep(1)
        t = state["timer"]
        changed = False

        if t["running"] and t["remaining"] > 0:
            t["remaining"] -= 1
            changed = True

            if t["remaining"] <= 0:
                t["remaining"] = 0
                t["running"] = False
                # Chế độ auto: vào grace period "Thống kê" thay vì tắt ngay
                if state["gift_mode"] == "auto":
                    t["finishing"] = True
                    t["grace_remaining"] = GIFT_GRACE_SECONDS
                    # Vẫn giữ accepting_gifts = True trong grace period
                else:
                    t["finished"] = True

        elif t.get("finishing") and t.get("grace_remaining", 0) > 0:
            # Đang trong grace period — đếm ngược, vẫn nhận quà
            t["grace_remaining"] -= 1
            changed = True
            if t["grace_remaining"] <= 0:
                # Hết grace period → thực sự kết thúc
                t["finishing"] = False
                t["finished"] = True
                if state["gift_mode"] == "auto":
                    state["accepting_gifts"] = False

        if changed:
            await broadcast_state()


async def gift_processor():
    """
    Drain gift queue và batch-broadcast để giảm số lần broadcast.
    Cộng điểm ngay lập tức khi nhận từ queue, gom nhiều quà trong cùng
    một tick thành 1 lần broadcast_state duy nhất.
    """
    while True:
        # Chờ ít nhất 1 quà
        diamonds = await _gift_queue.get()

        # Drain thêm các quà đã sẵn trong queue (không chờ)
        batch = diamonds
        while not _gift_queue.empty():
            try:
                batch += _gift_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        active = get_active_idol()
        if active is not None and batch > 0:
            active["score"] += batch

            # Kiểm tra mục tiêu điểm
            tgt = state["target"]
            target_just_reached = False
            if tgt["enabled"] and not tgt["reached"]:
                session_score = active["score"] - active.get("session_base", 0)
                if session_score >= tgt["score"]:
                    tgt["reached"] = True
                    target_just_reached = True

            await broadcast_state()
            await broadcast_event({
                "type": "gift",
                "idol": active["name"],
                "diamonds": batch,
            })
            if target_just_reached:
                await broadcast_event({"type": "target_reached", "idol": active["name"]})


@app.on_event("startup")
async def on_startup():
    global _gift_queue
    _gift_queue = asyncio.Queue()
    asyncio.create_task(timer_loop())
    asyncio.create_task(gift_processor())


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/overlay")
async def overlay_page():
    return FileResponse("static/overlay.html")

@app.get("/overlay/rank")
async def overlay_rank_page():
    return FileResponse("static/overlay_rank.html")

@app.get("/overlay/popup")
async def overlay_popup_page():
    return FileResponse("static/overlay_popup.html")


@app.get("/control")
async def control_page():
    return FileResponse("static/control.html")


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    sockets.append(websocket)
    await websocket.send_text(json.dumps({"type": "state", "data": state}))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in sockets:
            sockets.remove(websocket)


# --------------------------------------------------------------------------
# REST API
# --------------------------------------------------------------------------

class RoomIn(BaseModel):
    unique_id: str

class IdolIn(BaseModel):
    name: str
    avatar: str = ""
    tiktok_id: str = ""

class BatchIdolIn(BaseModel):
    names: list[str]

class PointsIn(BaseModel):
    delta: int

class DurationIn(BaseModel):
    seconds: int

class ModeIn(BaseModel):
    mode: str  # "auto" | "manual"


@app.get("/api/state")
async def api_get_state():
    return state


# --- Room ---

@app.post("/api/room")
async def api_connect_room(body: RoomIn):
    await connect_room(body.unique_id.strip())
    return {"ok": True}

@app.post("/api/room/disconnect")
async def api_disconnect_room():
    await disconnect_room()
    state["room"] = None
    await broadcast_state()
    return {"ok": True}


# --- Idol ---

@app.post("/api/idol")
async def api_add_idol(body: IdolIn):
    idol = {
        "id": uuid.uuid4().hex[:8],
        "name": body.name,
        "tiktok_id": normalize_tiktok_id(body.tiktok_id),
        "avatar": body.avatar,
        "score": 0,
        "active": len(state["idols"]) == 0,
        "eliminated": False,
    }
    state["idols"].append(idol)
    await broadcast_state()
    return idol

@app.post("/api/idol/batch")
async def api_add_batch(body: BatchIdolIn):
    added = []
    for name in body.names:
        name = name.strip()
        if not name:
            continue
        tiktok_id = ""
        if "|" in name:
            name, raw_id = name.split("|", 1)
            name = name.strip()
            tiktok_id = normalize_tiktok_id(raw_id)
        if not name or any(i["name"].lower() == name.lower() for i in state["idols"]):
            continue
        idol = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "tiktok_id": tiktok_id,
            "avatar": "",
            "score": 0,
            "active": len(state["idols"]) == 0 and len(added) == 0,
            "eliminated": False,
        }
        state["idols"].append(idol)
        added.append(idol)
    if added:
        await broadcast_state()
    return {"added": len(added)}


# --- TikTok avatar ---

@app.get("/api/tiktok/avatar")
async def api_tiktok_avatar(username: str):
    username = normalize_tiktok_id(username)
    if not username:
        return {"ok": False, "error": "Thiếu TikTok ID"}
    try:
        avatar = await asyncio.to_thread(fetch_tiktok_avatar, username)
        if not avatar:
            return {"ok": False, "error": "Không tìm thấy avatar từ TikTok", "username": username}
        return {"ok": True, "username": username, "avatar": avatar}
    except Exception as exc:
        logging.warning("TikTok avatar lookup failed for @%s: %s", username, exc)
        return {"ok": False, "error": "TikTok không cho phép truy cập avatar lúc này", "username": username}


@app.post("/api/idol/fill_avatars")
async def api_fill_avatars():
    """
    Quét toàn bộ idol đang có tiktok_id nhưng chưa có avatar, tự động
    tìm và gán avatar tương ứng cho từng người.
    """
    targets = [i for i in state["idols"] if i.get("tiktok_id") and not i.get("avatar")]
    filled = []
    failed = []
    for idol in targets:
        try:
            avatar = await asyncio.to_thread(fetch_tiktok_avatar, idol["tiktok_id"])
        except Exception as exc:
            logging.warning("TikTok avatar (fill_avatars) lỗi @%s: %s", idol["tiktok_id"], exc)
            avatar = ""
        if avatar:
            idol["avatar"] = avatar
            filled.append({"id": idol["id"], "name": idol["name"], "avatar": avatar})
        else:
            failed.append({"id": idol["id"], "name": idol["name"], "tiktok_id": idol["tiktok_id"]})
    if filled:
        await broadcast_state()
    return {
        "ok": True,
        "checked": len(targets),
        "filled": len(filled),
        "failed": len(failed),
        "filled_list": filled,
        "failed_list": failed,
    }

@app.delete("/api/idol/all")
async def api_delete_all_idols():
    state["idols"] = []
    await broadcast_state()
    return {"ok": True}

class BulkDeleteIn(BaseModel):
    ids: list[str]

@app.post("/api/idol/bulk_delete")
async def api_bulk_delete(body: BulkDeleteIn):
    id_set = set(body.ids)
    state["idols"] = [i for i in state["idols"] if i["id"] not in id_set]
    if state["idols"] and not any(i["active"] for i in state["idols"]):
        state["idols"][0]["active"] = True
    await broadcast_state()
    return {"ok": True}


@app.delete("/api/idol/{idol_id}")
async def api_remove_idol(idol_id: str):
    state["idols"] = [i for i in state["idols"] if i["id"] != idol_id]
    if state["idols"] and not any(i["active"] for i in state["idols"]):
        state["idols"][0]["active"] = True
    await broadcast_state()
    return {"ok": True}

@app.post("/api/idol/{idol_id}/active")
async def api_set_active(idol_id: str):
    for i in state["idols"]:
        i["active"] = (i["id"] == idol_id)
    await broadcast_state()
    return {"ok": True}

@app.post("/api/idol/{idol_id}/points")
async def api_add_points(idol_id: str, body: PointsIn):
    idol = get_idol(idol_id)
    if idol:
        idol["score"] = max(0, idol["score"] + body.delta)
        await broadcast_state()
    return {"ok": True}

@app.post("/api/idol/{idol_id}/reset")
async def api_reset_idol(idol_id: str):
    idol = get_idol(idol_id)
    if idol:
        idol["score"] = 0
        await broadcast_state()
    return {"ok": True}

class SetScoreIn(BaseModel):
    score: int

@app.post("/api/idol/{idol_id}/set_score")
async def api_set_score(idol_id: str, body: SetScoreIn):
    idol = get_idol(idol_id)
    if idol:
        idol["score"] = max(0, body.score)
        await broadcast_state()
    return {"ok": True}

@app.post("/api/idol/{idol_id}/eliminate")
async def api_eliminate(idol_id: str):
    import time
    idol = get_idol(idol_id)
    if idol:
        idol["eliminated"] = True
        idol["eliminated_at"] = time.time()  # timestamp để sort thứ tự OUT
        # nếu idol đang active thì chuyển active sang idol khác chưa bị loại
        if idol.get("active"):
            idol["active"] = False
            nxt = next((i for i in state["idols"] if not i.get("eliminated") and i["id"] != idol_id), None)
            if nxt:
                nxt["active"] = True
        await broadcast_state()
    return {"ok": True}

@app.post("/api/idol/{idol_id}/revive")
async def api_revive(idol_id: str):
    idol = get_idol(idol_id)
    if idol:
        idol["eliminated"] = False
        idol["eliminated_at"] = None  # reset khi hồi sinh
        await broadcast_state()
    return {"ok": True}


# --- Timer ---

def reset_session_scores():
    """Chụp điểm hiện tại của mỗi idol làm base → popup hiển thị từ 0"""
    for i in state["idols"]:
        i["session_base"] = i.get("score", 0)
    # Reset trạng thái đã đạt mục tiêu khi bắt đầu phiên mới
    state["target"]["reached"] = False

@app.post("/api/timer/duration")
async def api_set_duration(body: DurationIn):
    t = state["timer"]
    t["duration"] = body.seconds
    t["remaining"] = body.seconds
    t["running"] = False
    t["finished"] = False
    state["accepting_gifts"] = False
    reset_session_scores()
    await broadcast_state()
    return {"ok": True}

@app.post("/api/timer/start")
async def api_timer_start():
    t = state["timer"]
    if t["remaining"] <= 0:
        t["remaining"] = t["duration"]
    t["running"] = True
    t["finished"] = False
    t["finishing"] = False
    t["grace_remaining"] = 0
    reset_session_scores()
    # Chế độ auto: bấm Start timer → tự bật nhận điểm
    if state["gift_mode"] == "auto":
        state["accepting_gifts"] = True
    await broadcast_state()
    return {"ok": True}

@app.post("/api/timer/pause")
async def api_timer_pause():
    state["timer"]["running"] = False
    # Chế độ auto: tạm dừng timer → tạm dừng nhận điểm
    if state["gift_mode"] == "auto":
        state["accepting_gifts"] = False
    await broadcast_state()
    return {"ok": True}

@app.post("/api/timer/reset")
async def api_timer_reset():
    t = state["timer"]
    t["remaining"] = t["duration"]
    t["running"] = False
    t["finished"] = False
    t["finishing"] = False
    t["grace_remaining"] = 0
    if state["gift_mode"] == "auto":
        state["accepting_gifts"] = False
    await broadcast_state()
    return {"ok": True}

class GraceIn(BaseModel):
    seconds: int

@app.post("/api/timer/grace")
async def api_set_grace(body: GraceIn):
    global GIFT_GRACE_SECONDS
    GIFT_GRACE_SECONDS = max(0, min(30, body.seconds))
    state["timer"]["grace_config"] = GIFT_GRACE_SECONDS
    await broadcast_state()
    return {"ok": True, "grace_seconds": GIFT_GRACE_SECONDS}

@app.post("/api/timer/finish")
async def api_timer_finish():
    t = state["timer"]
    t["running"] = False
    t["finished"] = True
    t["finishing"] = False
    t["grace_remaining"] = 0
    if state["gift_mode"] == "auto":
        state["accepting_gifts"] = False
    await broadcast_state()
    return {"ok": True}


# --- Gift mode ---

@app.post("/api/gifts/mode")
async def api_set_gift_mode(body: ModeIn):
    if body.mode not in ("auto", "manual"):
        return {"ok": False, "error": "mode phải là auto hoặc manual"}
    state["gift_mode"] = body.mode
    # Chuyển sang manual → không tự động thay đổi accepting_gifts
    await broadcast_state()
    return {"ok": True}

@app.post("/api/gifts/start")
async def api_gifts_start():
    state["accepting_gifts"] = True
    reset_session_scores()
    await broadcast_state()
    return {"ok": True}

@app.post("/api/gifts/stop")
async def api_gifts_stop():
    state["accepting_gifts"] = False
    await broadcast_state()
    return {"ok": True}


# --- Target score ---

class TargetIn(BaseModel):
    enabled: bool
    score: int = 1000

@app.post("/api/target")
async def api_set_target(body: TargetIn):
    state["target"]["enabled"] = body.enabled
    state["target"]["score"] = max(1, body.score)
    state["target"]["reached"] = False
    await broadcast_state()
    return {"ok": True}

@app.post("/api/target/reset")
async def api_reset_target():
    state["target"]["reached"] = False
    await broadcast_state()
    return {"ok": True}


# --- Reset all ---

@app.post("/api/reset_all")
async def api_reset_all():
    for i in state["idols"]:
        i["score"] = 0
    t = state["timer"]
    t["remaining"] = t["duration"]
    t["running"] = False
    t["finished"] = False
    state["accepting_gifts"] = False
    await broadcast_state()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)