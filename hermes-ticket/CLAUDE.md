# Hermes: biến tin Zalo thành ticket (WSL)

Ngữ cảnh cho phần Hermes của dự án. Chi tiết riêng của máy (đường dẫn profile, id job, tên người, site Jira) nằm ở `CLAUDE.local.md` ở gốc repo. Không ghi bí mật vào đây.

## Bối cảnh nghiệp vụ
- Team CSA (CMC OpenAI) cung cấp trợ lý AI pháp luật **Clex** cho khách; **Misa** (sản phẩm "AI Luật Việt Nam", viết luatvietnam/LuatVietnamMisa) là đối thủ cùng hỗ trợ khách trên nhóm AI QG nên chat trộn yêu cầu hai bên.
- Quyết định 05/11: chỉ loại yêu cầu **nhắc riêng** Misa/luatvietnam (không nhắc Clex). Yêu cầu chung chung vẫn tạo ticket; nhắc cả hai thì giữ. Tin của người gửi có tên chứa Misa/luatvietnam không tạo ticket.
- Ticket đã loại có trạng thái `not_ours` trong `ledger.jsonl` (thêm dòng ledger mới, không sửa `tickets.jsonl`); không vào CSV/Jira; `create_tickets.py` trả `skipped_not_ours`.
- Nhận diện nhân viên Misa mới dựa vào chữ trong tên, chưa có danh sách ID.

## Thành phần
- `skills/zalo-task-tickets/`: skill + script Python (`zalo_window.py`, `triage_prepare.py`, `apply_group.py`, `create_tickets.py`, `export_jsm.py`, `jira_push.py`, ...); `SKILL.md` dùng `$HERMES_HOME/...` (profile không dùng `~/.hermes/skills`).
- `config/cs_config.json`: nhân viên, nhóm, loại yêu cầu, SLA 30 phút, khối `jira`. Có ID/tên thật; nằm trong `.gitignore`, đã gỡ khỏi git và đã xoá khỏi lịch sử GitHub (09/11).
- `scripts/`: shell cho cron (`export-hermes.sh`, `triage-prep.sh`, `triage-llm.sh`, `jira-push.sh`, `sync-tickets-onedrive.sh`), `jira_toggle.py`, `set_start_cursors.py`, `setup_shadow.py`, `compare_shadow.py`. Test Python ở `tests/` (`python -m unittest discover -s hermes-ticket/tests`, chạy cả trong CI).
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
- `resolved_in_chat` (nhân viên đã xử lý trong chat) vẫn tạo ticket với trạng thái `resolved`: có trong CSV (cột Status "Đã xong trong chat"), KHÔNG đẩy Jira, không vào `jsm_requests.json`; khách nhắc lại thì ticket mở lại.
- Trạng thái ledger (dòng sau đè dòng trước): `open`, `resolved`, `closed` (hết hạn theo dõi / Jira Done; mở lại khi khách nhắn tiếp), và `not_ours`/`duplicate`/`wrong` (người dùng loại, không bao giờ mở lại). Ghi bằng `mark_ticket.py <key> <trạng thái>`; `mark_ticket.py --auto-close-days [N=5] [--dry-run --list]` đóng ticket `open` quá N ngày; `sync_jira_status.py` (chỉ đọc Jira) đóng ticket có issue Jira đã Done.
- Xem kết quả: `<profile>/cron/output/<id job>/*.md`, `hermes -p ticket cron list|runs|status|doctor`; tạm dừng/chạy lại `cron pause|resume|run <id>`. Hermes v0.21.5 không có `--timezone`; `--script` tìm trong `scripts/` của profile.
- Job `zalo-ticket-onedrive` (5 phút, `--no-agent`): chép `jsm_import.csv` + `jsm_requests.json` sang `<OneDrive>\zalo\tickets\` khi nội dung đổi; bản CSV chép đi đổi `| media/` thành `| ../media/`.

## Triage bằng `triage_llm.py` (thay agent) và shadow mode
- `skills/zalo-task-tickets/scripts/triage_llm.py` (cron `--no-agent`, bọc bởi `scripts/triage-llm.sh`): mỗi nhóm có tin mới = vài lần gọi model qua 9router, không vòng lặp agent. Mặc định 2 bước (`TRIAGE_STEPS=2`): bước 1 gắn nhãn từng tin khách (`prompts/triage_classify.md`), bước 2 điền chi tiết ≤ 8 ticket/lần, chỉ gửi tin liên quan ±5 dòng (`prompts/triage_system.md`). Model mặc định `gemini/gemini-3.5-flash-lite` (trỏ thẳng, không qua combo `Ticket`), dự phòng Haiku. Biến `TRIAGE_*` ở docstring đầu file; khoá `TRIAGE_LLM_API_KEY` nằm trong `.env` của profile.
- **Kiểm tra phủ bằng mã** (`triage_cover.py`): tin khách có 📎/SĐT/email/từ khoá yêu cầu mà chưa có ticket phủ tới → hỏi lại 1 lần, rồi hỏi Haiku, vẫn sót thì tự tạo ticket `[Cần xem]` confidence 0.4 (thành `low_confidence`, ghi vào `<state>/review.jsonl`) rồi mới dời cursor. Lý do: Gemini flash-lite thỉnh thoảng trả mảng rỗng dù có yêu cầu rõ, mảng rỗng vẫn dời cursor nên yêu cầu mất.
- **Gộp ticket bị tách bằng mã** (`merge_split` trong `triage_cover.py`, sau khi model trả JSON và kiểm tra phủ, trước khi tạo): cùng người gửi tin neo, cách nhau ≤3 phút (theo chuỗi), cùng loại và cùng đơn vị (so không dấu) → giữ ticket neo sớm nhất, tin còn lại vào `source_message_ids`; một trong hai `open` thì cả cụm `open`. Một bên chưa có đơn vị thì coi là cùng. Tắt bằng `TRIAGE_MERGE=0`. Chốt chặn cuối `drop_misa` gỡ ticket/tin riêng Misa do bất kỳ vòng nào tạo ra. Nhóm `internal`: chạy 1 bước, NV được coi là người yêu cầu cho kiểm tra phủ (chỉ tin có tệp/SĐT/email); không hỏi lại từng tin mà tạo MỘT ticket `[Cần xem]` tổng hợp cho cả cửa sổ (mã tin + trích ≤100 ký tự, vào `review.jsonl`). Quy tắc chung: cửa sổ có tin cần xử lý mà 0 ticket và kiểm tra phủ đang tắt thì KHÔNG dời cursor.
- **Bước "nhân viên đã xử lý xong ticket nào?"** (`resolve_step`, `prompts/triage_resolve.md`, sau bước phân loại/điền chi tiết; tắt bằng `TRIAGE_RESOLVE=0`): chỉ chạy khi có tin NV mới (không phải Misa) và có ticket `open` trong nhóm (nhóm `internal` bỏ qua). Mặc định `open`; chỉ cập nhật `resolved_in_chat` khi model nêu khoá ticket đang mở + mã tin NV + `quote` chép nguyên văn từ tin đó (sai thì hỏi lại 1 lần rồi bỏ). Cập nhật ticket cũ giữ nguyên người nhận/ưu tiên/hạn/khách. Chốt chặn bằng mã (`resolve_rule`/`resolve_unique`): chỉ đóng khi tin NV (a) nêu đúng đơn vị/tên khách, (b) @tag đúng khách, hoặc (c) chỉ có 1 ticket mở trong 10 tin gần nhất; tối đa 3 ticket mỗi tin NV (nhiều hơn = quá chung chung, không đóng cái nào).
- **Nhóm `internal`** có prompt riêng `prompts/triage_internal.md` (NV giao việc cho nhau; thu quỹ/nhắc việc chung vẫn tạo task `internal_task`) và có thể dùng model riêng qua `TRIAGE_INTERNAL_MODEL`.
- **`review.csv`** (`export_jsm.py` → `<state>/export/review.csv`: Thời gian, Nhóm, Mã tin, Trích, Lý do) = bản CSV của `review.jsonl` ([Cần xem]); `sync-tickets-onedrive.sh` chép sang thư mục OneDrive `zalo/tickets/`. Script `.sh` phải lưu LF (CRLF làm bash lỗi `pipefail`).
- Tin nhắc riêng Misa (`luatvietnammisa.*`, "AI Luật Việt Nam", không có "Clex") luôn bị ép `not_ours` bằng mã (`force_misa`), kể cả khi khách tag nhân viên CMC. `triage_unit.py` gợi ý `unit` từ tên tệp + tin chữ cùng cụm (cùng người gửi ≤3 phút) rồi đưa vào prompt bước 2 làm gợi ý (đo trên sổ thật: có gợi ý 54% ticket, khớp `unit` trong sổ 85%; `unit` trong sổ do model điền nên chỉ là mốc tham khảo).
- Trí nhớ ở `<state>/memory/` (`triage_memory.py`): `notes/<nhóm>.md` (sổ tay do model đề xuất, ≤ 1.500 ký tự), `senders.json` (danh bạ khách dựng từ ticket), ví dụ sửa sai lấy từ ledger (`not_ours`/`duplicate`/`wrong`). Tổng ≤ ~2.500 token.
- Phản hồi bị cắt vì hết token: tự thu nhỏ cửa sổ rồi hỏi lại. Lỗi 429/5xx: dừng lượt, không dời cursor.
- **Shadow mode** (chưa thay job cũ): `setup_shadow.py` dựng `<profile>/zalo-tickets-shadow` (bản sao state thật, cursor = hiện tại, `jira.enabled=false`); skill riêng ở `<profile>/skills-shadow/` (job cũ không bị ảnh hưởng); `triage-llm.sh` mặc định chỉ chạy vào state shadow (từ chối state thật), không gọi `export-hermes.sh` (checkpoint dùng chung với job cũ), không đẩy Jira. `compare_shadow.py --date <ngày>` so ticket shadow với ticket thật (khách + giờ ±10 phút, không theo mỏ neo) ra `data/shadow-compare-<ngày>.md`. Khi shadow đang chạy, mỗi lần sửa/chép skill phải ghi ngày giờ vào `docs/WORKLOG.md` (xem CLAUDE.md gốc). Cron shadow tên `zalo-ticket-triage-shadow` (every 15m, `--no-agent`) do người dùng tự tạo.
- **Quay lại / chuyển chính thức:** job cũ `zalo-ticket-triage` vẫn giữ nguyên. Chuyển chính thức = pause job cũ + tạo job `triage-llm.sh` với `ZALO_TICKETS_STATE` là state thật và `TRIAGE_ALLOW_REAL=1`, kèm chạy `export-hermes.sh` trước; quay lại = ngược lại.

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
- Thử khô bằng cách phát lại cửa sổ cũ có "hindsight": cửa sổ chứa cả câu trả lời của nhân viên mà agent cũ chưa thấy lúc xử lý, nên so với ticket cũ sẽ lệch (nhiều `resolved_in_chat`); hai bên cũng neo ticket vào tin chữ hay tin tệp khác nhau, so theo mỏ neo ra nhiễu (dùng `compare_shadow.py`: khách + giờ).
- `max_tokens` 4096 từng cắt JSON khi có ~20 ticket (nay 16000 + tự thu nhỏ cửa sổ). Gemini flash-lite cùng prompt có lúc trả `{"tasks": []}`: đừng tin một lần gọi, luôn bật kiểm tra phủ.
- 9router: token OAuth Claude hết hạn (401, không tự làm mới) thì combo rơi hết xuống Gemini free (RPM 15, TPM 250K, RPD 500) và chạm hạn mức; cần đăng nhập lại trong dashboard 9router.
- Gọi WSL từ Git Bash bị đổi đường dẫn (`/mnt/...` thành `D:/Program Files/Git/...`): ghi file `.sh` tạm rồi chạy `wsl -d Ubuntu-24.04 -- bash <file>` (`bash -l` khi cần `hermes`); ghi file không BOM.
