# Hermes: biến tin Zalo thành ticket (WSL)

Ngữ cảnh cho phần Hermes của dự án. Chi tiết riêng của máy (đường dẫn profile, id job, tên người, site Jira) nằm ở `CLAUDE.local.md` ở gốc repo. Không ghi bí mật vào đây.

## Bối cảnh nghiệp vụ
- Team CSA (CMC OpenAI) cung cấp trợ lý AI pháp luật **Clex** cho khách; **Misa** (sản phẩm "AI Luật Việt Nam", viết luatvietnam/LuatVietnamMisa) là đối thủ cùng hỗ trợ khách trên nhóm AI QG nên chat trộn yêu cầu hai bên.
- Quyết định 05/11: chỉ loại yêu cầu **nhắc riêng** Misa/luatvietnam (không nhắc Clex). Yêu cầu chung chung vẫn tạo ticket; nhắc cả hai thì giữ. Tin của người gửi có tên chứa Misa/luatvietnam không tạo ticket.
- Ticket đã loại có trạng thái `not_ours` trong `ledger.jsonl` (thêm dòng ledger mới, không sửa `tickets.jsonl`); không vào CSV/Jira; `create_tickets.py` trả `skipped_not_ours`.
- Nhận diện nhân viên Misa mới dựa vào chữ trong tên, chưa có danh sách ID.

## Thành phần
- `skills/zalo-task-tickets/`: skill + script Python (`zalo_window.py`, `triage_prepare.py`, `apply_group.py`, `create_tickets.py`, `export_jsm.py`, `jira_push.py`, ...); `SKILL.md` dùng `$HERMES_HOME/...` (profile không dùng `~/.hermes/skills`).
- `config/cs_config.json`: nhân viên, nhóm, loại yêu cầu, SLA 30 phút, khối `jira`. Có ID/tên thật; nằm trong `.gitignore` và đã gỡ khỏi chỉ mục git, nhưng vẫn còn trong lịch sử đã push (commit `e2692c5`).
- `scripts/`: shell cho cron (`export-hermes.sh`, `triage-prep.sh`, `jira-push.sh`, `sync-tickets-onedrive.sh`), `jira_toggle.py`, `set_start_cursors.py`.
- Bản chạy thật nằm ở profile `ticket` của Hermes. **Sửa file trong repo xong phải chép sang profile** (skills, scripts, `cs_config.json`).
- Tài liệu triển khai: `TRIEN-KHAI.md`.

## Profile `ticket`
- Chỉ bật toolset `terminal` + `file` (cho cả `cli` lẫn `cron`); model là combo `Ticket` qua 9router (`provider: custom`).
- `.env` của profile: `ZALO_HISTORY_DIR` (thư mục JSONL của `exportHermes.js`), `ZALO_TICKETS_STATE`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. Trong terminal tool, `HERMES_HOME` = thư mục profile.
- State ở `<profile>/zalo-tickets/`: `cs_config.json`, `cursors/`, `tickets.jsonl`, `ledger.jsonl`, `export/` (`jsm_import.csv`, `jsm_requests.json`), `jira_state.json`.
- Thư mục `zalo-tickets-dry/` cùng profile là nơi chạy thử, không đụng state thật.

## Luồng chạy tự động
- Gateway `HermesGateway` (task Windows) là gateway **đa profile**, cron của profile `ticket` chạy nhờ nó; không cần task Windows riêng.
- Cursor của `zalo_window.py` là **số dòng** JSONL (không phải message_id); `--advance-to-line` từ chối giá trị ngoài `cursor..tổng số dòng`. Mốc bắt đầu: chỉ tin từ 23:52 ngày 04/11 mới thành ticket.
- Job `zalo-ticket-triage` (15 phút, có agent): pre-script `triage-prep.sh` = `export-hermes.sh` + `triage_prepare.py` (in cửa sổ tin mới của mọi nhóm, tối đa 60 dòng/nhóm, kèm `--list-open`).
- Không có tin mới thì pre-script in `{"wakeAgent": false}` và Hermes bỏ qua lượt agent (không tốn model).
- Agent chỉ ghi JSON ra `/tmp/zalo_cs_<nhóm>.json` rồi chạy MỘT lệnh `apply_group.py <nhóm> <json> <LAST_LINE>` = dry-run + tạo + `export_jsm` + dời cursor; lỗi thì dừng, không dời cursor.
- Prompt job ép agent chỉ chạy script của skill (không `search_files`), vì từng đi lạc.
- `resolved_in_chat` (nhân viên đã xử lý trong chat) thì không tạo ticket.
- Xem kết quả: `<profile>/cron/output/<id job>/*.md`, `hermes -p ticket cron list|runs|status|doctor`; tạm dừng/chạy lại `cron pause|resume|run <id>`. Hermes v0.21.5 không có `--timezone`; `--script` tìm trong `scripts/` của profile.
- Job `zalo-ticket-onedrive` (5 phút, `--no-agent`): chép `jsm_import.csv` + `jsm_requests.json` sang `<OneDrive>\zalo\tickets\` khi nội dung đổi; bản CSV chép đi đổi `| media/` thành `| ../media/`.

## Jira (job `zalo-ticket-jira`, 5 phút, `--no-agent`)
- `jira_push.py` đẩy ticket `open` lên project **SUP** bằng REST v3, loại `Task` (`mode: "task"`; tài khoản không có vai trò agent JSM nên `servicedeskapi` trả 403; chế độ `jsm` vẫn còn trong mã).
- Chỉ đẩy ticket có tin neo từ `jira.start`. Bật/tắt: `npm run jira -- on|off|status [-FromNow]` (đổi `jira.enabled`, hiệu lực ở lượt cron kế tiếp). `-FromNow` đặt lại `jira.start` để không đẩy dồn.
- **Chế độ dự phòng** (09/11): chỉ tạo khi quá `jira.backup_grace_minutes` (20) phút kể từ tin neo mà Jira chưa có task cho tin đó. 0 = tạo ngay.
- **Chống trùng:** trước khi tạo tìm trên Jira theo mã tin neo; thấy rồi thì ghi `existing: true` và bỏ qua. Task có sẵn của người khác thì **không comment/đính kèm**. Dựa vào tìm kiếm văn bản nên task tạo trễ hoặc chỉ mục chậm vẫn có thể bị tạo trùng.
- Quy ước tiêu đề/nội dung bắt chước task của pilot AI khác: `[Đơn vị] Tạo tài khoản: …` (agent điền `unit`, `platform` trong JSON), mô tả có khối Thông tin yêu cầu/Nội dung/File đính kèm/Tin nhắn gốc/Mã tin Zalo; labels `ai-drafted, zalo, hermes, <loại>`; `duedate` theo SLA.
- `jira.title_suffix` ("LT") nối vào cuối mọi tiêu đề để phân biệt task của Hermes.
- Tệp đính kèm tải lên từ `exports/media` (tối đa 10 MB, thử lại tới 3 ngày nếu tệp chưa tải); cập nhật của khách thành comment (chỉ với task do Hermes tạo).
- Người nhận = `staff.*.jira` (accountId); nhân viên chưa điền thì task không có người nhận. Người báo cáo = chủ API token.
- Tool: `jira_push.py --check|--dry-run|--list-desks`. Không bao giờ in/ghi token ra log hay tài liệu.

## Lỗi đã gặp (đừng lặp lại)
- Agent ghi `message_id` dạng số nguyên: đã chuẩn hoá về chuỗi ở `create_tickets.py`, `export_jsm.py`, `jira_push.py`.
- Agent truyền nhầm `message_id` làm cursor: đã có chốt chặn ở `zalo_window.py`.
- 9router: token OAuth Claude hết hạn (401, không tự làm mới) thì combo rơi hết xuống Gemini free (RPM 15, TPM 250K, RPD 500) và chạm hạn mức; cần đăng nhập lại trong dashboard 9router.
- Gọi WSL từ Git Bash bị đổi đường dẫn (`/mnt/...` thành `D:/Program Files/Git/...`): ghi file `.sh` tạm rồi chạy `wsl -d Ubuntu-24.04 -- bash <file>` (`bash -l` khi cần `hermes`); ghi file không BOM.
