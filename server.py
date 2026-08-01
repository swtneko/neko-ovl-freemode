"""
MCA-style gift-battle overlay server.
Chạy: pip install -r requirements.txt && python server.py
OBS Browser Source: http://YOUR_SERVER:8000/overlay
Control panel:      http://YOUR_SERVER:8000/control
"""

import asyncio
import json
import uuid
from typing import Optional

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
    },
    # Chế độ nhận điểm
    # mode: "auto" = timer điều khiển bật/tắt nhận điểm
    #       "manual" = bấm tay bật/tắt
    "gift_mode": "auto",       # "auto" | "manual"
    "accepting_gifts": False,  # True = đang nhận điểm từ gift
}

sockets: list[WebSocket] = []


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
        if not state["accepting_gifts"]:
            return
        # Bỏ qua sự kiện giữa chừng của streak (chờ event cuối cùng)
        if event.streaking:
            return

        repeat_count = event.repeat_count or 1
        diamond_count = event.gift.diamond_count if event.gift else 0
        diamonds = repeat_count * diamond_count

        active = get_active_idol()
        if active is not None and diamonds > 0:
            active["score"] += diamonds
            # Broadcast state + gift event cho overlay popup
            await broadcast_state()
            await broadcast_event({
                "type": "gift",
                "idol": active["name"],
                "diamonds": diamonds,
            })

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
                t["finished"] = True
                # Chế độ auto: hết giờ → dừng nhận điểm ngay
                if state["gift_mode"] == "auto":
                    state["accepting_gifts"] = False

        if changed:
            await broadcast_state()


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(timer_loop())


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
        if any(i["name"].lower() == name.lower() for i in state["idols"]):
            continue
        idol = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
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
    if state["gift_mode"] == "auto":
        state["accepting_gifts"] = False
    await broadcast_state()
    return {"ok": True}

@app.post("/api/timer/finish")
async def api_timer_finish():
    t = state["timer"]
    t["running"] = False
    t["finished"] = True
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
