# CLAUDE.md

Ngữ cảnh dự án cho Claude. Khi kiến trúc/lệnh/quyết định đổi: sửa đúng mục liên quan. Cuối phiên: thêm 1 dòng vào `docs/WORKLOG.md` và cập nhật mục "Việc còn dở". Giữ file này ≤ 120 dòng. Chi tiết riêng máy ở `CLAUDE.local.md` (không commit).

## Mục đích
Log tin nhắn các nhóm Zalo (tài khoản cá nhân, qua `zca-js` không chính thức) vào SQLite và MongoDB Atlas, xuất Excel theo ngày. Rủi ro: Zalo có thể khoá tài khoản, nên dùng tài khoản phụ.
Nghiệp vụ: team CSA (CMC OpenAI) cung cấp trợ lý AI pháp luật **Clex**; Misa ("AI Luật Việt Nam") là đối thủ cùng hỗ trợ khách trên nhóm AI QG. Hermes biến tin khách thành ticket/Jira, xem `hermes-ticket/CLAUDE.md`.

## Stack
Node >= 20 (máy dev v25), ES modules, `better-sqlite3`, `mongodb`, `exceljs`, `zca-js`. Giao diện, log, comment bằng tiếng Việt; giữ phong cách này.

## Quy tắc bắt buộc
- Chạy `npm run selftest` trước khi báo xong; thêm test vào `test/selftest.js` khi thêm tính năng.
- KHÔNG chạy `npm start`/`dev`/`login` khi tool thật đang chạy (mở thêm listener sẽ đá kết nối Zalo đang chạy).
- KHÔNG đọc `.env`, `data/`, `cs_config.json`; không ghi bí mật (mật khẩu, URI Atlas, cookie, token) vào tài liệu hay log.
- `exportHermes.js` phải luôn chỉ đọc SQLite (không dùng `openDb()`, vì nó tạo bảng và pragma ghi).
- Script `.ps1` chạy trên Windows (UTF-8 BOM nếu có tiếng Việt); script trong `hermes-ticket/` chạy trong WSL; sửa skill/script xong phải chép sang profile Hermes.
- Sửa ở `src/` chỉ có hiệu lực sau khi khởi động lại tool (`npm run stop` rồi `npm run resume`).
- Không commit/push khi chưa được người dùng đồng ý.
- Khi cron shadow (`zalo-ticket-triage-shadow`) đang chạy: MỖI lần sửa skill/script Hermes (kể cả chép sang `skills-shadow/`) phải ghi 1 dòng vào `docs/WORKLOG.md` kèm ngày GIỜ (`YYYY-MM-DD HH:MM`) và việc đã đổi, để so sánh shadow biết kết quả từ giờ nào dùng mã nào.

## Lệnh
- `npm run login`: đăng nhập QR, lưu `data/credentials.json`. `npm run groups [-- từ khoá]`: liệt kê nhóm.
- `npm start`: lắng nghe và ghi log (SQLite + Atlas nếu có `MONGODB_URI`). `npm run dev`: nodemon, chỉ khi phát triển.
- `npm run export -- [--date|--from --to|--group|--out]`: xuất Excel từ SQLite. `npm run sync-mongo`: đẩy toàn bộ SQLite lên Atlas (idempotent).
- `npm run send-report [-- --date today]`: xuất + chép `exportCopyDir` + gửi tay tới `reportTo` (không có `reportTo` thì chỉ xuất/chép).
- `npm run download-media [-- --days N]`: tải bù ảnh/file/video của tin trong SQLite (không cần đăng nhập). Tuỳ chọn; `npm start` chỉ tự tải tin mới + 3 ngày gần nhất chưa tải/lỗi.
- `npm run prune-exports [-- --dry-run|--days N|--backup]`: giữ 7 ngày gần nhất trong `exports/`, chỉ xoá tệp đã có bản sao cùng đường dẫn + cùng kích thước ở `exportCopyDir`; `--backup` chép tệp chưa có bản sao rồi mới xoá.
- `npm run export-hermes [-- --dry-run|--quiet|--reset --yes]`: cầu nối sang Hermes; chạy trên Windows (từ WSL gọi qua `cmd.exe /c "cd /d D:\zalo-logger && node src\exportHermes.js --quiet"`).
- `npm run stop` / `npm run resume`: tắt hẳn (Disable task + dừng `start.js`) / bật lại và chạy ngay. Không gỡ task.
- `npm run jira -- on|off|status [-FromNow]`: bật/tắt đẩy ticket lên Jira (đổi `jira.enabled` trong `cs_config.json`, hiệu lực ở lượt cron kế tiếp).
- `.\setup-autostart.ps1 [-Remove]`: Task Scheduler chạy `node src/start.js` khi đăng nhập + lặp mỗi 5 phút (`IgnoreNew`) để chạy lại sau khi watchdog thoát. Task không chuyển hướng output.
- `npm run selftest`: test bằng dữ liệu giả, không cần mạng.

## Kiến trúc (src/)
Luồng chính: `start.js` → `store.js` (SQLite + Atlas) → `daily.js` / `media.js` → `exportHermes.js`.
- `start.js`: listener `message`/`undo`; backfill mỗi lần kết nối qua `requestOldMessages`; chỉ log nhóm trong `config/config.json`; watchdog thoát mã 1 nếu mất kết nối quá 2 phút (trừ `DuplicateConnection`).
- `store.js`: ghi song song; SQLite là bản gốc (đồng bộ), Atlas bắn-và-quên (lỗi Atlas chỉ cảnh báo). `db.js`: SQLite WAL, bảng `groups`, `messages` (PK `group_id, msg_id`, `INSERT OR IGNORE`), `media`.
- `mongo.js`: Atlas, db `MONGODB_DB` (mặc định `zalo_logger`), collection `groups` và `messages` (unique `{groupId, msgId}`); nạp `.env` bằng `process.loadEnvFile`. `syncMongo.js`: đồng bộ bù.
- `daily.js`: sau 0h xuất `exports/Zalo_<ngày>.xlsx` (nhiều sheet), gửi nếu có `reportTo`, chép sang `exportCopyDir` (lỗi chỉ cảnh báo), rồi dọn `exports/` qua `prune.js`; trạng thái `data/report-state.json` (`lastSentDay`, `copyPending` = ngày chép lỗi, thử chép lại 10 phút/lần), bù tối đa 7 ngày.
- `media.js`: tải ảnh/file/video từ link trong nội dung tin (HTTP thường, không cần đăng nhập) vào `exports/media/<ngày>/<nhóm>/<HHmmss>_<tên>`; hàng đợi 2 tệp, tệp > `mediaMaxMB` bị bỏ qua, tối đa 5 lần thử; chỗ giữ bằng tệp rỗng `wx` nên `copyMediaDay` bỏ qua tệp 0 byte; `start.js` chờ hàng đợi tải xong (tối đa 2 phút) trước khi chạy báo cáo. Atlas không lưu media. `downloadMedia.js` là CLI tải bù.
- `exportHermes.js`: chỉ đọc SQLite, nối tin mới vào `<hermesHistoryDir>/<group_id>.jsonl`; checkpoint = `rowid` (không phải `ts`) ở `data/hermes-export-state.json`; sự kiện đến sau `recall`/`attachment`; `MAX(rowid) < checkpoint` thì thoát mã 2.
- `export.js` + `exporter.js`: xuất xlsx từ SQLite (không đọc Atlas); hyperlink tệp là đường dẫn tương đối `media/<ngày>/<nhóm>/<tệp>`. `prune.js` + `pruneExports.js`: dọn `exports/`.
- `format.js`: múi giờ, nhãn tiếng Việt `describe()`. `banner.js`: banner ASCII khi chạy. `filelog.js`: ghi thêm `data/start.log`, xoay vòng sang `.1` khi quá 5 MB (kể cả lúc đang chạy).
- `groupmatch.js`, `config.js`, `paths.js`, `zaloClient.js`, `login.js`, `groups.js`.

## Gotchas
- Chỉ 1 listener/tài khoản: mở Zalo Web cùng lúc sẽ ngắt kết nối.
- Gửi vào "Cloud của tôi" (chat với chính mình) KHÔNG được: zca-js trả lỗi 114 "Tham số không hợp lệ". `reportTo` phải là user khác hoặc nhóm.
- `api.getGroupChatHistory` (HTTP) đã bị Zalo gỡ (404); backfill dùng `listener.requestOldMessages(ThreadType.Group, lastMsgId)` + sự kiện `old_messages`.
- Task Scheduler "restart on failure" KHÔNG chạy khi thoát mã khác 0; đừng dựa vào nó. `LastTaskResult` 2147946720 (0x800710E0) là bình thường (lần lặp bị bỏ vì tool đang chạy).
- Ảnh/file/video: link Zalo có thể hết hạn (thử được tin 5 ngày trước, chưa biết thời hạn thật), nên tải ngay khi tin đến. `exports/media` chứa dữ liệu nhạy cảm và OneDrive đồng bộ lên đám mây.
- Bí mật: `data/`, `.env`, `exports/`, `config/config.json` (mẫu: `config/config.example.json`) đều trong `.gitignore`. Atlas cần whitelist IP và user DB. `exports/~$*.xlsx` là file tạm của Excel, bỏ qua.
- Ngày không có tin thì không có file `exports/Zalo_<ngày>.xlsx`, không phải lỗi.
- Checkpoint theo `rowid` vì tin bù có `ts` cũ nhưng ghi sau; lọc theo `ts` sẽ sót.

## Việc còn dở
- Atlas: đổi mật khẩu/xoá user Admin đã lộ qua ảnh; cân nhắc thu hẹp Network Access `0.0.0.0/0`; chưa tự đồng bộ Mongo định kỳ (chỉ ghi song song + `sync-mongo` tay).
- Gửi Zalo qua `reportTo` chưa từng test thật; chưa gặp ca mất kết nối 1006 tự nhiên nào mới để quan sát watchdog.
- Media: chưa chạy `download-media` cho tin cũ (tuỳ chọn); chưa thử video thật; chưa rõ thời hạn link Zalo; chưa mở xlsx bằng Excel thật để xem hyperlink tương đối.
- Dọn `exports/`: 11 báo cáo xlsx cũ khác kích thước với bản trên OneDrive (chưa rõ bản nào đúng) nên chưa xoá; chờ quyết định có chạy `--backup` không; chưa quan sát lần dọn tự động đầu tiên (sau 0h ngày 10/11).
- Backfill: chưa xác minh thứ tự trang của `requestOldMessages` nên chưa dừng sớm khi gặp tin đã có.
- Lịch sử git đã viết lại ngày 09/11 để xoá `hermes-ticket/config/cs_config.json` (mã commit đổi hết; clone khác phải clone lại). Còn bản sao lưu cũ `D:\zalo-logs-backup-2026-11-09.bundle` chứa dữ liệu nhạy cảm: xoá khi không cần nữa. `TRIEN-KHAI.md` vẫn ghi ID của người nhận ticket mặc định (của chính chủ repo).
- Hermes: nhận diện nhân viên Misa chỉ theo tên; ticket "Thu quỹ phòng" (nội bộ) và ticket trùng THPT Hướng Hóa chưa xử lý; ticket quá hạn chưa có người nhận; `unit`/`platform` do model điền có thể sai.
- Hermes: `triage_llm.py` (thay agent, 2 bước + kiểm tra phủ) chỉ mới chạy thử/shadow, chưa thay job cũ; chờ chấm tay `data/triage-llm-label.md` và so sánh `compare_shadow.py`. `resolved_in_chat` đã chốt: vẫn tạo ticket trạng thái `resolved` (mã mới nằm ở nhánh `triage-llm` + skill shadow, chưa chép sang skill của job cũ). Chờ duyệt đóng 16 ticket mở quá 5 ngày (`mark_ticket.py --auto-close-days --dry-run --list`; 1 ticket là yêu cầu Misa, nên `not_ours`).
- Jira: đang BẬT chế độ dự phòng (chờ 20 phút, chỉnh được); chưa có task nào do Hermes tạo thật; accountId mới điền cho 1 nhân viên; có thể tạo trùng nếu chỉ mục Jira chậm.
- Mã triage_llm/shadow đã commit trên nhánh `triage-llm`, chưa merge, chưa push; chưa theo dõi đủ 5 ngày làm việc.

## Tài liệu chi tiết
- `hermes-ticket/CLAUDE.md`: Hermes, cron, Jira. `hermes-ticket/TRIEN-KHAI.md`: triển khai. `docs/WORKLOG.md`: nhật ký làm việc.
