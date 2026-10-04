# Neko Overlay · Freemode

Overlay gift battle cho TikTok Live, chạy ngay trên máy bạn, **không cần đăng nhập** tài khoản nào. Nhận gift từ phòng live, cộng điểm cho idol đang được chọn và hiển thị lên OBS.

---

## 1. Cài đặt (Windows)

### Bước 1: Cài Python (chỉ làm một lần)

Tải Python 3.10 trở lên tại https://www.python.org/downloads/ rồi cài đặt.
**Quan trọng:** ở màn hình cài đặt đầu tiên, tick vào ô **"Add python.exe to PATH"**.

### Bước 2: Tải code về

1. Vào trang GitHub của dự án, bấm nút xanh **Code** → **Download ZIP**.
2. Giải nén file ZIP ra một thư mục, ví dụ `D:\neko-ovl-freemode`.

### Bước 3: Mở CMD trong thư mục vừa giải nén

Mở thư mục đó trong File Explorer (thấy các file `server.py`, `requirements.txt`, thư mục `static`), bấm vào **thanh địa chỉ** phía trên, gõ `cmd` rồi nhấn **Enter**. CMD sẽ mở sẵn đúng thư mục.

### Bước 4: Cài thư viện và chạy

```bat
pip install -r requirements.txt
python server.py
```

Thấy dòng `Uvicorn running on http://0.0.0.0:8000` là server đã chạy. **Giữ nguyên cửa sổ CMD này**, tắt cửa sổ là tắt server.

Mở trình duyệt, vào http://localhost:8000/control để bắt đầu dùng.

### Những lần chạy sau

Chỉ cần mở CMD trong thư mục (Bước 3) rồi gõ:

```bat
python server.py
```

Muốn dừng server: bấm `Ctrl + C` trong cửa sổ CMD.

> Muốn đổi cổng 8000: `set PORT=9000` rồi `python server.py`.
> Dùng từ máy khác trong mạng LAN (điện thoại, máy OBS riêng): thay `localhost` bằng IP của máy chạy server, ví dụ `http://192.168.1.10:8000/control`.

---

## 2. Sử dụng

### Các địa chỉ

| Địa chỉ | Dùng để |
|---|---|
| `http://localhost:8000/control` | Bảng điều khiển |
| `http://localhost:8000/overlay/popup` | Widget idol + đồng hồ (OBS, gợi ý 660 × 180) |
| `/overlay/rank?...` | Bảng xếp hạng dạng lưới (OBS) |

Link cho OBS nằm ở cuối trang control, mục **OBS Browser Source**. Bấm vào link để copy.

### Các bước chạy một trận

**1. Thêm idol** (khối "Thêm Idol" trong control)
- Tab **Thêm 1**: nhập tên, có thể thêm TikTok ID (bấm **🔍 Kiểm tra avatar** để tự lấy ảnh) hoặc dán link ảnh avatar.
- Tab **Hàng loạt**: mỗi dòng một tên, dạng `Tên` hoặc `Tên|tiktok_id`.
- Tab **Import/Export**: mỗi dòng `tên|username|điểm|live/out|link avatar`. Bấm **Export** để xuất danh sách ra ô văn bản, copy lại để lưu.
- Bấm **🔄 Tìm avatar còn thiếu** để gán avatar cho các idol đã có TikTok ID.

**2. Chọn idol nhận gift**: bấm **Chọn** ở idol đó, nút đổi thành **★**. Gift chỉ cộng cho idol đang ★, và luôn phải có đúng một idol ★.

**3. Kết nối TikTok**: nhập username phòng live (không cần `@`) ở khối **TikTok Live**, bấm **▶**. Khi hiện `✔ Đã nối` là xong. Phòng phải đang live.

**4. Đặt thời gian**: ở khối **Thời gian**, nhập phút và giây, bấm **Đặt**. Ô **⏳ Thống kê** là số giây vẫn nhận quà sau khi hết giờ (mặc định 3 giây).

**5. (Tuỳ chọn) Mục tiêu điểm**: bật công tắc **🎯 Mục tiêu điểm**, nhập số 💎 cần đạt, bấm **✔ Áp dụng**.

**6. Mở overlay trong OBS**: thêm **Browser Source**, dán link đã copy. Nền trong suốt sẵn, không cần Chroma Key.

**7. Bắt đầu**: bấm **▶** ở khối Thời gian. Đồng hồ chạy và bắt đầu nhận gift. Hết giờ, overlay hiện `Thành công` hoặc `Thất bại` (nếu có mục tiêu).

### Trong trận

| Muốn làm | Cách làm |
|---|---|
| Đổi lượt idol | Bấm **Chọn** ở idol khác |
| Sửa điểm | Ô **±Số điểm** + **Cộng** (số âm để trừ), hoặc ô **Tổng điểm** + **Đặt điểm** |
| Loại idol | Bấm **Loại** (★ tự chuyển sang idol tiếp theo). **Hồi sinh** để đưa lại |
| Tìm idol | Ô **🔍 Tìm idol...** |
| Xoá idol | Nút **✕**, hoặc **☑ Chọn** để xoá nhiều, **🗑 Xóa tất** để xoá hết |
| Chuẩn bị trận mới | **🔄 Reset tất cả** (đưa điểm mọi idol về 0 và timer về đầu) |

Chế độ nhận điểm có hai kiểu:
- **🤖 Tự động** (mặc định): bấm ▶ ở timer là bắt đầu nhận điểm, hết giờ tự dừng.
- **👆 Thủ công**: tự bấm **▶ Bắt đầu** / **⏹ Dừng** để mở và chốt nhận điểm, không phụ thuộc đồng hồ.

### Chỉnh bảng xếp hạng

Khối **Widget Xếp hạng** trong control cho chọn xếp **Dọc / Ngang**, số idol mỗi cột, và phạm vi TOP. Mỗi lần đổi, link ở mục OBS được cập nhật. Hãy **copy lại link mới** dán vào OBS.

---

## 3. Lưu ý quan trọng

- **Tắt server là mất hết dữ liệu** (idol, điểm). Trước khi tắt, vào **Import/Export → Export** và copy lại danh sách.
- **Bấm ▶ lại sau khi Pause** sẽ làm điểm hiển thị trên widget popup về 0 (điểm tổng trên bảng xếp hạng vẫn giữ). Tránh Pause giữa trận.
- Server **không có mật khẩu**. Chỉ dùng trong máy hoặc mạng nội bộ, đừng mở cổng ra internet.
- Không lấy được avatar tự động thì dán trực tiếp link ảnh vào ô avatar.

## 4. Gặp lỗi?

| Triệu chứng | Cách xử lý |
|---|---|
| `'pip' / 'python' is not recognized` | Chưa tick "Add to PATH" lúc cài Python. Cài lại Python và tick ô đó |
| OBS không hiện gì | Mở thử link ngay trên trình duyệt. Nếu OBS ở máy khác, dùng IP của máy chạy server thay cho `localhost` |
| Đã nối TikTok nhưng điểm không tăng | Kiểm tra khối Nhận điểm có đang `🟢 Nhận điểm` không, và có idol nào đang ★ không |
| Báo lỗi đỏ khi kết nối TikTok | Kiểm tra username đúng chưa và phòng có đang live không. Thử cập nhật: `pip install -U TikTokLive` |
| Lỗi cổng 8000 đang bị dùng | Đổi cổng: `set PORT=9000` rồi `python server.py` |
