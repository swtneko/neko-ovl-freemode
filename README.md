# Gift Battle Overlay (thay thế MCA)

Không cần đăng nhập tài khoản nào. Chạy hoàn toàn trên server của bạn.

## Cài đặt

```bash
pip install -r requirements.txt
python server.py
```

Server chạy ở cổng `8000` (sửa trong `server.py` nếu cần).

## Sử dụng

1. **OBS**: thêm Browser Source, URL = `http://IP_SERVER:8000/overlay`
   (chỉnh kích thước Width/Height cho vừa layout).
2. **Điều khiển**: mở `http://IP_SERVER:8000/control` trên máy/điện thoại khác
   - Nhập username TikTok (không có `@`) để bắt đầu nhận comment/gift live.
   - Thêm các idol (tên + link ảnh avatar tuỳ chọn).
   - Bấm "Chọn nhận quà" trên idol nào để toàn bộ gift TikTok gửi vào lúc đó
     được cộng dồn cho idol đó (chỉ 1 idol active cùng lúc).
   - Có thể cộng điểm tay bằng nút +100/+500/+1000 (dùng khi muốn override hoặc test).
   - Đặt thời gian trận, Start/Pause/Reset/Kết thúc. Khi hết giờ, overlay tự
     hiện idol có điểm cao nhất là người thắng.

## Ghi chú kỹ thuật

- Điểm được tính theo `diamond_count * repeat_count` của mỗi gift, chỉ cộng
  khi combo/streak đã kết thúc (tránh cộng trùng khi 1 gift được gửi nhiều lần liên tiếp).
- Nếu tên field của thư viện `TikTokLive` bạn đang dùng khác với bản mới nhất
  (ví dụ do bạn đang dùng version cũ hơn cho `tiktok-event-bridges`), chỉnh lại
  đoạn `on_gift` trong `server.py` cho khớp — logic tổng thể giữ nguyên.
- Toàn bộ state lưu trong RAM (không DB) — khởi động lại server sẽ mất dữ liệu,
  phù hợp vì mỗi trận đấu là độc lập. Nếu cần lưu lịch sử, có thể bổ sung sau.
- Nhiều overlay/control có thể mở cùng lúc, tất cả đồng bộ qua WebSocket.
