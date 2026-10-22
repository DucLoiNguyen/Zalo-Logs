# zalo-logger

Log tin nhắn các **nhóm Zalo** (tài khoản Zalo cá nhân, không phải OA) vào SQLite, rồi xuất **Excel** theo ngày giống file thống kê mẫu (`Test_Clause_Zalo.docx`).

Dùng thư viện không chính thức [`zca-js`](https://github.com/RFS-ADRENO/zca-js) làm cầu nối (giả lập Zalo Web).

> ⚠ **Rủi ro**: zca-js là API không chính thức, Zalo có thể khoá/cấm tài khoản. Nên dùng **tài khoản phụ**. Log tin nhắn là lưu dữ liệu của nhiều người: chỉ log nhóm cần thiết, báo cho thành viên và đặt thời hạn lưu.

## Cài đặt (Windows + PowerShell)

Cần Node.js 20 trở lên.

```powershell
cd zalo-logger
npm install
npm run login      # quét QR bằng Zalo trên điện thoại, xác nhận trên điện thoại
npm run groups     # xem tên + ID các nhóm (lọc: npm run groups -- <từ khoá>)
```

Mở `config/config.json`, điền **tên hoặc ID** các nhóm cần log:

```json
{
  "timezone": "Asia/Ho_Chi_Minh",
  "groups": ["Tên nhóm A", "1234567890123456789"]
}
```

## Chạy log

```powershell
npm start
```

Giữ cửa sổ PowerShell mở (và máy không ngủ). Mỗi tin mới của các nhóm trong danh sách được ghi vào `data/zalo-logs.db` và in ra màn hình. Dừng bằng `Ctrl+C`.

- **Không mở Zalo Web** bằng cùng tài khoản khi đang chạy, vì listener sẽ bị ngắt (zca-js chỉ cho 1 listener/tài khoản).
- Mỗi lần kết nối (lại), tool tự xin Zalo các tin cũ (theo trang, cho tới hết phần Zalo còn giữ) để **bù tin bị lỡ**; tin trùng được bỏ qua. Tin cũ quá lâu mà Zalo không còn giữ thì không bù được.
- Tin bị thu hồi được đánh dấu "[Tin nhắn đã thu hồi]" trong Excel, nội dung gốc vẫn giữ trong DB.

Khi sửa mã nguồn, dùng `npm run dev` (nodemon) để tự khởi động lại mỗi khi lưu file trong `src/`, `config/` hoặc `.env`. Mỗi lần khởi động lại tool đăng nhập lại và bù tin cũ, nên chỉ dùng khi phát triển.

## Xuất Excel

Không cần đăng nhập Zalo, đọc thẳng từ DB:

```powershell
node src/export.js                                  # hôm nay, tất cả nhóm
node src/export.js --date 2026-10-20                # 1 ngày (cũng nhận: today, yesterday)
node src/export.js --from 2026-10-01 --to 2026-10-22
node src/export.js --date yesterday --group "Tên nhóm A"
```

File ra ở `exports/<tên nhóm>_<ngày>.xlsx`, mỗi nhóm một file, gồm 2 sheet:

| Sheet | Cột |
|---|---|
| Tin nhắn | Ngày · Thời gian · Người gửi · Loại · Nội dung |
| Tổng hợp file đính kèm | STT · Người chat · Tên file · Thời gian |

Nếu báo không ghi được file, hãy đóng file Excel đang mở rồi chạy lại.

## MongoDB Atlas (ghi song song, tuỳ chọn)

Khi có `MONGODB_URI`, `npm start` ghi mỗi tin vào cả SQLite (bản gốc) lẫn Atlas. Không có URI thì chỉ ghi SQLite.

1. Atlas: tạo cluster miễn phí (M0) > **Database Access**: tạo user + mật khẩu > **Network Access**: thêm IP của máy (hoặc `0.0.0.0/0` khi thử) > **Connect > Drivers**: copy chuỗi kết nối.
2. `copy .env.example .env`, điền `MONGODB_URI` (mật khẩu có ký tự đặc biệt phải mã hoá URL).
3. `npm run sync-mongo` đẩy dữ liệu SQLite hiện có lên Atlas (chạy lại bất kỳ lúc nào để bù tin Atlas bị lỡ).

Collection: `groups`, `messages` (unique `groupId + msgId`). Xuất Excel vẫn đọc từ SQLite.

## Bảo mật

`.env` chứa mật khẩu Atlas (đã trong `.gitignore`). `data/` chứa phiên đăng nhập Zalo (`credentials.json`) và toàn bộ tin nhắn. Đã nằm trong `.gitignore`: **không commit, không gửi cho người khác**.

## Cấu trúc

```
src/login.js     đăng nhập QR, lưu phiên
src/groups.js    liệt kê nhóm
src/start.js     lắng nghe và ghi log
src/export.js    xuất Excel (CLI)   |  src/exporter.js  dựng file xlsx
src/format.js    múi giờ + chuyển loại tin (ảnh, file, danh thiếp...) thành chữ
test/selftest.js kiểm tra DB + xuất Excel bằng dữ liệu giả: npm run selftest
```

## Giới hạn đã biết

- Dữ liệu gốc của mỗi tin (loại + nội dung JSON) được lưu nguyên trong DB, nên nếu nhãn loại tin (ví dụ "Danh thiếp") chưa khớp thực tế, chỉ cần sửa `describe()` trong `src/format.js` rồi xuất lại.
- Chưa tải ảnh/file đính kèm về máy, chỉ ghi tên file.
