# CLAUDE.md

Ngữ cảnh dự án cho Claude. **Quy tắc bắt buộc: cuối mỗi phiên làm việc (hoặc sau mỗi thay đổi đáng kể), cập nhật file này**: sửa các mục bên dưới nếu kiến trúc/lệnh/quyết định thay đổi, và thêm 1 dòng vào "Nhật ký làm việc" (ngày, việc đã làm, việc còn dở). Không ghi bí mật (mật khẩu, URI Atlas, cookie) vào file này.

## Mục đích
Log tin nhắn các nhóm Zalo (tài khoản cá nhân, qua `zca-js` không chính thức) vào SQLite và MongoDB Atlas, xuất Excel theo ngày (mẫu: `Test_Clause_Zalo.docx`). Rủi ro: Zalo có thể khoá tài khoản, nên dùng tài khoản phụ.

## Stack
Node >= 20 (máy dev: v25), ES modules, `better-sqlite3`, `mongodb`, `exceljs`, `zca-js`. Giao diện, log, comment bằng tiếng Việt; giữ phong cách này.

## Lệnh
- `npm run login`: đăng nhập QR, lưu `data/credentials.json`
- `npm run groups [-- từ khoá]`: liệt kê nhóm
- `npm start`: lắng nghe và ghi log (SQLite + Atlas nếu có `MONGODB_URI`)
- `npm run export -- [--date|--from --to|--group|--out]`: xuất Excel từ SQLite
- `npm run sync-mongo`: đẩy toàn bộ SQLite lên Atlas (idempotent)
- `npm run send-report [-- --date today]`: xuất + gửi tay báo cáo 1 ngày tới `reportTo`
- `.\setup-autostart.ps1 [-Remove]`: Task Scheduler tự chạy `node src/start.js` khi đăng nhập, cửa sổ console hiện banner + log (task KHÔNG chuyển hướng output). Task có 2 trigger: đăng nhập + lặp mỗi 5 phút (`IgnoreNew`) để chạy lại sau khi watchdog thoát; "restart on failure" của Task Scheduler KHÔNG chạy khi thoát mã khác 0 (đã thử), đừng dựa vào nó
- `npm run stop` / `npm run resume` (`stop-logger.ps1`, `resume-logger.ps1`, UTF-8 BOM): stop = tắt (Disable) task + dừng tiến trình `start.js`, tool không tự chạy lại; resume = bật lại task và chạy ngay (không chạy thêm nếu đã có tiến trình). Không gỡ task. Đóng console/Ctrl+C mà không `stop` thì tối đa 5 phút sau task chạy lại
- `npm run dev`: chạy `start.js` bằng nodemon (tự khởi động lại khi sửa `src/`, `config/`, `.env`; cấu hình ở `nodemon.json`). Dùng khi phát triển, còn chạy thật dùng `npm start`
- `npm run selftest`: test bằng dữ liệu giả, không cần mạng

## Kiến trúc (src/)
- `start.js`: **watchdog**: kết nối Zalo đóng mà 2 phút không nối lại (hoặc 2 phút đầu chưa kết nối được) thì `process.exit(1)`; không áp dụng khi `DuplicateConnection`. Listener `message`/`undo`, backfill mỗi lần kết nối qua WebSocket `requestOldMessages` (phân trang cũ->mới tới khi hết, mọi nhóm cùng lúc), chỉ log nhóm trong `config/config.json`
- `store.js`: **ghi song song**: SQLite là bản gốc (đồng bộ), Atlas là bản sao bắn-và-quên; lỗi Atlas chỉ log cảnh báo, không mất tin
- `db.js`: SQLite WAL, bảng `groups`, `messages` (PK `group_id, msg_id`, `INSERT OR IGNORE`)
- `mongo.js`: kết nối Atlas, db `MONGODB_DB` (mặc định `zalo_logger`), collection `groups` (`_id` = group id) và `messages` (unique `{groupId, msgId}`, index `{groupId, ts}`, `content` lưu object gốc, `recalled` boolean). Nạp `.env` bằng `process.loadEnvFile`
- `syncMongo.js`: đồng bộ bù SQLite -> Atlas
- `daily.js`: sau 0h (kiểm tra mỗi phút + khi vừa kết nối) xuất 1 file nhiều sheet `exports/Zalo_<ngày>.xlsx` (`exportCombined`: mỗi nhóm 1 sheet + sheet "File đính kèm") và gửi file (không kèm chữ) tới `reportTo` trong config. Nếu config có `exportCopyDir` (thư mục OneDrive) thì chép thêm file vào đó (lỗi chép chỉ cảnh báo, không chặn gửi Zalo). Chưa đặt `reportTo` thì chỉ xuất file vào `exports/`; trạng thái ở `data/report-state.json`, bù tối đa 7 ngày
- `export.js` + `exporter.js`: xuất xlsx (2 sheet: "Tin nhắn", "Tổng hợp file đính kèm"), **đọc từ SQLite, không đọc Atlas**
- `format.js`: múi giờ, `describe()` ánh xạ loại tin thành nhãn tiếng Việt (nhãn là suy đoán, sửa ở đây rồi xuất lại)
- `banner.js`: banner ASCII "Zalo Logger" + phiên bản in ở đầu console khi `start.js` chạy (chỉ ASCII để tránh lỗi font trên cmd; tắt màu bằng `NO_COLOR=1`)
- `filelog.js`: `installFileLog()` bọc `console.log/info/warn/error` để vừa in ra màn hình vừa ghi `data/start.log` (UTF-8, bỏ mã màu, quá 5 MB đổi tên `.1`). Chỉ `start.js` gọi. Đừng chuyển hướng output trong task (sẽ làm console trống)
- `groupmatch.js`, `config.js`, `paths.js`, `zaloClient.js`, `login.js`, `groups.js`

## Quyết định và lưu ý
- Chỉ 1 listener/tài khoản: mở Zalo Web cùng lúc sẽ ngắt kết nối.
- Bí mật: `data/`, `.env`, `exports/` đều trong `.gitignore`. Cấu hình Atlas ở `.env` (mẫu: `.env.example`: `MONGODB_URI`, `MONGODB_DB`).
- Atlas cần whitelist IP (Network Access) và user DB (Database Access).
- **Gửi vào "Cloud của tôi" (chat với chính mình) KHÔNG được**: zca-js trả lỗi 114 "Tham số không hợp lệ" cả với chữ lẫn file khi `threadId` = UID của mình. `reportTo` phải là user khác hoặc nhóm.
- `api.getGroupChatHistory` (HTTP) đã bị Zalo gỡ, trả 404; đừng dùng lại. Backfill dùng `listener.requestOldMessages(ThreadType.Group, lastMsgId)` + sự kiện `old_messages` (cửa sổ tin Zalo còn giữ phía server, không giới hạn 50).
- Chưa tải ảnh/file đính kèm, chỉ ghi tên file.
- `exports/~$*.xlsx` là file tạm của Excel, bỏ qua.

## Nhật ký làm việc
- 2026-10-22: Đọc toàn bộ dự án; tạo CLAUDE.md; thêm MongoDB Atlas ghi song song (`mongo.js`, `store.js`, `syncMongo.js`, `.env.example`). Selftest đạt.
- 2026-10-22 (sau): Atlas M0 cluster `zalo-logger` (user `zalo_logger`, Network Access mở `0.0.0.0/0`) đã kết nối thật; `sync-mongo` đẩy 88 tin/3 nhóm lên, chạy lại không trùng. **Còn dở:** chưa test `npm start` ghi song song với tin live; nên đổi mật khẩu user Atlas cũ (đã lộ qua ảnh) và xoá user Admin tự tạo.
- 2026-10-22 (tối): thêm báo cáo ngày (`daily.js`, `exportCombined`, `sendReport.js`, selftest cập nhật) và `setup-autostart.ps1` (đã đăng ký task `zalo-logger`, chưa chạy). **Còn dở:** chưa chọn `reportTo` vì gửi vào Cloud của tôi bị Zalo từ chối; chưa test gửi thật.
- 2026-10-22 (khuya): chưa có `reportTo` nên báo cáo sau 0h chỉ xuất `exports/Zalo_<ngày>.xlsx`, không gửi Zalo.
- 2026-10-22 (khuya): thêm nodemon (devDependency) + `npm run dev`.
- 2026-10-22 (khuya): sửa lỗi 404 khi bù tin (xem Quyết định); lần chạy thử bù thêm 10 tin chưa có (SQLite 88 -> 98).
- 2026-10-24: thêm banner khi khởi chạy. Phát hiện tool đã dừng sau 00:00:41 ngày 24/10 (không rõ lý do, nghi máy tắt/ngủ); task autostart chỉ chạy khi đăng nhập Windows.
- 2026-10-27: sửa console trống khi chạy theo lịch (cách C): thêm `filelog.js`, task chạy node trực tiếp, đã đăng ký lại và chạy thử OK. Tool bị Ctrl+C lúc 00:06 nên đã chạy lại bằng task. **Còn dở:** chưa có watchdog tự khởi động lại khi kết nối Zalo đóng mà không nối lại; chưa có `reportTo`.
- 2026-10-28: kết nối Zalo chết từ 09:39 (code 1006, không nối lại) lần 2 (lần 1: 25/10 07:16). Thêm watchdog vào `start.js` (đã thử ép đóng: thoát mã 1) và trigger lặp 5 phút cho task (đã thử cơ chế). Chạy lại, bù 55 tin. **Còn dở:** chưa quan sát watchdog ngoài đời thật; chưa có `reportTo`; chưa rõ vì sao `retryOnClose` đôi khi không nối lại.
- 2026-10-28 (chiều): đặt `reportTo` = user Nguyễn Thúy Hòa (SĐT tra bằng `api.findUser`) trong `config/config.json`; báo cáo 0h sẽ gửi file cho người này. **Còn dở:** cần khởi động lại tool để nhận config; chưa test gửi thật (Zalo có thể từ chối nếu chưa là bạn bè).
- 2026-10-30: thêm `exportCopyDir` trong config (`config.js`, `daily.js`, `start.js`, `sendReport.js`): báo cáo ngày được chép thêm vào thư mục OneDrive `...\Premier Service Team - Tạo tài khoản GOV_260916\zalo`. **Còn dở:** cần khởi động lại tool để nhận config; chưa test chép thật.
- 2026-10-31 (sau): bỏ `reportTo` khỏi `config/config.json` theo yêu cầu: 0h chỉ xuất `exports/Zalo_<ngày>.xlsx` và chép vào `exportCopyDir`, không gửi Zalo (code gửi vẫn còn, bật lại bằng cách thêm `reportTo`). Đã khởi động lại tool.
- 2026-10-31: thêm `npm run stop` / `npm run resume` (dừng hẳn không tự bật lại, không gỡ task); đã thử stop -> resume OK. Việc chép sang OneDrive đã thử ngày 30/10 (OK); gửi Zalo cho `reportTo` vẫn chưa thử thật.
