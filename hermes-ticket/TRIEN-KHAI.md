# Đặc tả triển khai: zalo-logger → Hermes → ticket CS

> **Dành cho Claude (VS Code / Claude Code).** Đọc hết file này, sau đó đọc `CLAUDE.md` của zalo-logger, rồi làm lần lượt từng giai đoạn. Xong mỗi giai đoạn thì dừng lại, báo kết quả kiểm tra cho người dùng, và chỉ làm tiếp khi được đồng ý. Mọi giao diện, log và comment viết bằng tiếng Việt, theo phong cách sẵn có của dự án.

Ngày chốt: 04/11/2026. Người yêu cầu: Đức Lợi Nguyễn.

---

## 0. Bối cảnh (đã chốt, không bàn lại)

**Mục tiêu.** Team CS nhận yêu cầu của khách trong các group Zalo. zalo-logger đã lưu được tin nhắn. Giờ cần Hermes Agent đọc các tin đó, hiểu ngữ cảnh, rồi tạo **ticket thô** (JSONL + CSV theo định dạng Jira Service Management). Team chưa có quyền tạo ticket trên JSM, nên tạm thời chưa nối JSM.

**Máy đang chạy:**

| Thành phần | Ở đâu | Ghi chú |
|---|---|---|
| zalo-logger | Windows, `D:\zalo-logger` | Node ≥ 20, `zca-js`, SQLite `data/zalo-logs.db` (WAL), Task Scheduler tự chạy `src/start.js` |
| Hermes Agent v0.21.5 | WSL Ubuntu 24.04, `~/.hermes/hermes-agent` | 2 profile `default` và `cskh`. Gateway chạy qua task Windows `HermesGateway` |
| LLM | 9router `http://localhost:20128/v1` (provider `custom`) | Combo `Ticket` đã tạo dành riêng cho việc này. Combo `ZaloLab` giữ cho `cskh` |

**Các quyết định:**

1. Nhóm `CSA18.9.26` (id `5216811275487163293`) là **nhóm nội bộ**: tin trong nhóm này tạo **task nội bộ**. Ba nhóm còn lại là nhóm khách, tin trong đó tạo **ticket CS**.
2. **Mỗi file danh sách đăng ký tài khoản = 1 ticket**, có đính kèm file.
3. Tạm thời mọi việc giao cho **Đức Lợi Nguyễn** (`1857352237546357054`), **SLA 30 phút**. Sẽ cập nhật sau.
4. Được phép gửi nội dung chat tới LLM qua 9router.
5. Hermes chạy trong một **profile mới `ticket`**, chỉ bật toolset `terminal` và `file`. **Không** đụng tới profile `cskh`, vì profile đó nói chuyện trực tiếp với khách và cố ý không có tool.
6. **Không chạy `hermes update`** trong đợt này.

**Ràng buộc kỹ thuật:**
- **KHÔNG sửa** `src/start.js`, `src/store.js`, `src/db.js`, `src/media.js` (listener đang chạy ổn định). Chỉ thêm file mới, cộng thêm thay đổi nhỏ ở `package.json`, `src/config.js`, `src/paths.js` và `test/selftest.js`.
- Mở DB ở chế độ **chỉ đọc**. Không bao giờ ghi vào `zalo-logs.db`.
- `better-sqlite3` đã được build cho Windows, nên mọi lệnh Node phải chạy trên **Windows** (PowerShell, hoặc `cmd.exe` gọi từ WSL), không chạy bằng node trong WSL.
- Không commit `data/`, `.env`, `exports/`, và cả `hermes-ticket/config/` (có ID và tên nhân viên).
- Theo quy tắc trong `CLAUDE.md`: xong mỗi giai đoạn phải cập nhật `CLAUDE.md` (thêm phần kiến trúc, lệnh, và một dòng nhật ký).

**Vật liệu có sẵn** trong thư mục `hermes-ticket/` cạnh file này:

```
hermes-ticket/
  TRIEN-KHAI.md                     ← file này
  README.md                         ← thiết kế tổng thể (tham khảo)
  config/cs_config.json             ← cấu hình THẬT: nhân viên, nhóm, loại yêu cầu, SLA
  skills/zalo-task-tickets/
    SKILL.md                        ← quy trình Hermes làm theo
    scripts/zalo_window.py          ← lấy tin mới theo checkpoint (đọc JSONL)
    scripts/create_tickets.py       ← kiểm tra, chống trùng, điều phối, SLA, ghi ticket
    scripts/export_jsm.py           ← xuất jsm_import.csv + jsm_requests.json
    scripts/convert_log.py          ← (không cần cho dự án này)
  sample/                           ← dữ liệu giả để test phía Python
```

Các script Python đã chạy thử với dữ liệu giả (tạo ticket, cập nhật ticket, chống trùng, SLA, file đính kèm, thu hồi, xuất CSV). **Không viết lại các script này.** Chỉ sửa khi có lỗi thật, và nếu sửa thì ghi rõ lý do.

---

## 1. Giai đoạn 1: cầu nối `src/exportHermes.js` (trong zalo-logger)

### 1.1 Việc cần làm

- Tạo file `src/exportHermes.js` (ES module, dùng lại `openDb`/`paths`/`describe`/`loadConfig` sẵn có nếu phù hợp).
- Trong `package.json`, thêm `"export-hermes": "node src/exportHermes.js"`.
- Thêm khoá config `hermesHistoryDir`, mặc định `path.join(DATA_DIR, "hermes-history")`. Đọc khoá này trong `src/config.js`, và thêm hằng số vào `src/paths.js` nếu cần.
- Thêm file trạng thái `data/hermes-export-state.json`.

### 1.2 Hành vi

1. Mở DB bằng `new Database(DB_FILE, { readonly: true, fileMustExist: true })`. Không gọi `openDb()` nếu hàm đó chạy `CREATE TABLE` hoặc pragma ghi. Nếu phải tự viết câu truy vấn thì viết ngay trong file này.
2. **Checkpoint = `rowid`** của bảng `messages` (bảng có PK ghép nên vẫn có `rowid` ẩn, và `rowid` tăng theo thứ tự ghi). Lý do: tin được backfill có `ts` cũ nhưng được ghi vào sau. Nếu lọc theo `ts` thì sẽ sót những tin này.
3. Lấy tin mới:
   ```sql
   SELECT m.rowid AS rid, m.group_id, m.msg_id, m.sender_id, m.sender_name, m.msg_type,
          m.content_json, m.ts, m.recalled, g.name AS group_name,
          d.rel_path, d.status AS media_status
     FROM messages m
     LEFT JOIN groups g ON g.group_id = m.group_id
     LEFT JOIN media d  ON d.group_id = m.group_id AND d.msg_id = m.msg_id
    WHERE m.rowid > ? ORDER BY m.rowid
   ```
4. Mỗi tin ghi **nối** một dòng JSON (UTF-8, `ensure_ascii` tắt, `\n`) vào `<hermesHistoryDir>/<group_id>.jsonl`:
   ```json
   {"message_id":"<msg_id>","from_id":"<sender_id>","from_name":"<sender_name>",
    "text":"<describe(msgType, content, false).text>","kind":"<describe(...).kind>",
    "timestamp": <ts / 1000, số giây, có phần thập phân>,"type":"<msg_type>",
    "group_name":"<tên nhóm>","recalled": <true|false>,
    "attachment": {"name":"<tên tệp>","path":"media/<rel_path>"} }
   ```
   - `text` luôn là nội dung gốc (truyền `false` cho `describe`); trạng thái thu hồi chỉ nằm ở `recalled`, để Hermes vẫn hiểu khách đã gửi gì.
   - Chỉ ghi `attachment` khi tin thuộc loại `chat.photo`, `share.file` hoặc `chat.video.msg`. `name` lấy từ `describe().fileName` nếu có, nếu không thì lấy `content.title`. `path` để `null` khi `media_status` khác `ok`.
   - Bỏ qua `msg_type` bằng `chat.undo` và `chat.delete` (đây là tin hệ thống của thao tác thu hồi; trạng thái thu hồi đã nằm ở cột `recalled`).
5. **Sự kiện đến sau** (tin đã được xuất từ trước, giờ mới thay đổi):
   - `recalled` vừa chuyển thành 1 → ghi dòng `{"type":"recall","message_id":"<msg_id>"}` vào file của nhóm đó.
   - File vừa tải xong (`media.status = 'ok'`) cho một tin đã xuất trước đó mà khi xuất còn `path: null` → ghi dòng `{"type":"attachment","message_id":"<msg_id>","attachment":{"name":…,"path":"media/<rel_path>"}}`.
   - Để biết tin nào đã phát sự kiện, giữ trong state hai danh sách `recalledSent` và `mediaSent` (khoá `group_id:msg_id`). Mỗi lần chạy chỉ cần quét các tin có `rowid <= lastRowid` và `ts` trong 7 ngày gần nhất.
6. Ghi xong các file JSONL thì mới ghi state: `{ "lastRowid": N, "recalledSent": [...], "mediaSent": [...], "updatedAt": "<ISO>" }`. Nếu chương trình chết giữa chừng thì lần sau có thể ghi trùng vài dòng. Như vậy vẫn chấp nhận được, vì phía Hermes tự chống trùng, còn mất tin thì không chấp nhận được.
7. Kiểm tra an toàn: nếu `MAX(rowid) < lastRowid` (DB bị VACUUM hoặc khôi phục) thì in cảnh báo rồi thoát với mã 2, không tự xuất lại.
8. Các tham số dòng lệnh:
   - `--dry-run`: in số tin và số sự kiện sẽ ghi, không ghi gì.
   - `--reset`: xoá state, đổi tên thư mục JSONL cũ thành `hermes-history.bak-<thời gian>`, rồi xuất lại từ đầu. Bắt buộc phải kèm `--yes`.
   - `--quiet`: chỉ in một dòng tổng kết.
9. Kết quả in ra, ví dụ: `export-hermes: 12 tin mới, 1 thu hồi, 3 tệp, 4 nhóm → D:\zalo-logger\data\hermes-history`. Thoát với mã 0.

### 1.3 Test (`npm run selftest`)

Thêm test vào `test/selftest.js`. Test dùng DB giả, không cần mạng:
- Lần 1 ghi đúng số dòng. Lần 2 không ghi thêm dòng nào.
- Tin có `ts` cũ được chèn sau (giả lập backfill) vẫn được xuất ra.
- Đặt `recalled = 1` cho một tin đã xuất: lần chạy sau có đúng một dòng `recall`.
- Thêm dòng `media` với status `ok` cho một tin đã xuất có `path: null`: lần chạy sau có đúng một dòng `attachment`.
- Mở DB thật ở chế độ chỉ đọc khi `start.js` đang chạy thì không lỗi (thử tay một lần, ghi kết quả vào CLAUDE.md).

### 1.4 Xong khi

- [ ] `npm run selftest` đạt.
- [ ] `npm run export-hermes` trên dữ liệu thật: tổng số dòng tin bằng `SELECT COUNT(*) FROM messages WHERE msg_type NOT IN ('chat.undo','chat.delete')`, và chạy lần hai báo 0 tin mới.
- [ ] `start.js` vẫn chạy, log vẫn có tin mới sau khi đã chạy export.
- [ ] Đã cập nhật CLAUDE.md và README (mục "Xuất cho Hermes").
- [ ] Đã thêm `hermes-ticket/config/` vào `.gitignore`.

---

## 2. Giai đoạn 2: cài skill và profile `ticket` (trong WSL)

Các lệnh trong mục này chạy trong **WSL**. Khi viết lệnh, Claude in ra cho người dùng chạy, hoặc tự chạy qua `wsl.exe -e bash -lc "…"` nếu được phép. Trước khi dùng một lệnh `hermes` nào, chạy `--help` của lệnh đó để xác nhận cú pháp trên v0.21.5, vì tài liệu tham chiếu được viết theo bản mới nhất.

1. **Tạo profile:**
   ```bash
   hermes profile create ticket --description "Đọc log Zalo CS và tạo ticket thô"
   hermes -p ticket model            # chọn provider custom → http://localhost:20128/v1, model: Ticket
   ```
   Không clone từ `cskh` và không bật kênh nhắn tin nào cho profile này.
2. **Giới hạn tool:** trong `config.yaml` của profile `ticket`, chỉ bật toolset `terminal` và `file`. Tắt `web`, `browser`, `memory` và mọi MCP. Kiểm tra lại bằng `hermes -p ticket tools` hoặc lệnh tương đương.
3. **Cài skill và cấu hình:**
   ```bash
   hermes profile show ticket        # đọc đường dẫn thư mục profile, gán vào P
   P=~/.hermes/profiles/ticket       # ví dụ — dùng đúng đường dẫn lệnh trên in ra
   mkdir -p "$P/skills" "$P/zalo-tickets"
   cp -r /mnt/d/zalo-logger/hermes-ticket/skills/zalo-task-tickets "$P/skills/"
   cp /mnt/d/zalo-logger/hermes-ticket/config/cs_config.json "$P/zalo-tickets/"
   ```
   Thêm vào `.env` của profile `ticket`:
   ```
   ZALO_HISTORY_DIR=/mnt/d/zalo-logger/data/hermes-history
   ZALO_TICKETS_STATE=<P>/zalo-tickets
   ```
   Kiểm tra: các script Python dùng `HERMES_HOME`. Hãy xác nhận profile `ticket` đặt `HERMES_HOME` trỏ vào thư mục của nó. Nếu không thì dùng hai biến ở trên là đủ.
4. **Cầu nối gọi từ WSL:** tạo file `<P>/scripts/export-hermes.sh`:
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   cmd.exe /c "cd /d D:\zalo-logger && node src\exportHermes.js --quiet" 2>&1 | tr -d '\r'
   ```
   Chạy thử `bash <P>/scripts/export-hermes.sh`. Nếu `cmd.exe` không có (WSL interop bị tắt), báo người dùng và chuyển sang cách dự phòng: tạo task Windows `ZaloExportHermes` chạy `npm run export-hermes` mỗi 5 phút. Có thể làm theo mẫu `setup-autostart.ps1`.
5. **Thử khô trên một ngày thật** (chưa ghi ticket):
   ```bash
   S="$P/skills/zalo-task-tickets/scripts"
   python3 "$S/zalo_window.py" --list-groups
   hermes -p ticket chat -s zalo-task-tickets -q "Xử lý nhóm 7934340105422269668 theo skill zalo-task-tickets nhưng CHỈ chạy create_tickets.py --dry-run, không tạo ticket, không dời checkpoint. Liệt kê kết quả."
   ```
   Đưa kết quả cho người dùng đối chiếu với thực tế.

### Xong khi

- [ ] Profile `ticket` chỉ có tool terminal và file, model là `Ticket`.
- [ ] `export-hermes.sh` chạy được từ WSL.
- [ ] Ở lần thử khô: ít nhất 90% yêu cầu thật có ticket, không quá 10% ticket thừa. Mỗi file danh sách là một ticket. Tin trong CSA thành task nội bộ. Người được giao là Đức Lợi Nguyễn. Hạn là thời điểm gửi tin + 30 phút. Người dùng đã xác nhận kết quả.

---

## 3. Giai đoạn 3: chạy tự động

1. **Gateway cho profile `ticket`.** Cron chỉ chạy khi gateway của profile đó đang chạy. Thêm một task Windows (giống `HermesGateway`) chạy `wsl.exe -e bash -lc "hermes -p ticket gateway"`. Hỏi người dùng trước khi tạo task.
2. **Tạo job:**
   ```bash
   hermes -p ticket cron create "every 15m" \
     "Chạy skill zalo-task-tickets cho mọi nhóm có dòng mới: tạo/cập nhật ticket bằng backend local, rồi chạy export_jsm.py. Báo cáo ngắn: ticket mới, ticket được cập nhật, ticket quá hạn, mục cần người xem, thời điểm tin mới nhất." \
     --skill zalo-task-tickets --script export-hermes.sh --timezone Asia/Ho_Chi_Minh
   ```
   `--script` là pre-run script đặt trong `scripts/` của profile. Nếu v0.21.5 không có `--script` cho job agent, đưa lệnh `bash …/export-hermes.sh` vào bước 1 của SKILL.md (đã có sẵn ở đó).
3. **Chép file cho team:** thêm vào prompt của job: chép `<P>/zalo-tickets/export/jsm_import.csv` sang `/mnt/c/Users/posei/OneDrive - C-OPENAI/Premier Service Team - Tạo tài khoản GOV_260916/zalo/tickets/`. Trước khi chép, xác nhận đường dẫn OneDrive với người dùng.
4. **Theo dõi 5 ngày làm việc.** Mỗi ngày kiểm tra `hermes -p ticket cron list` và đuôi log của job. Nếu kết quả trống, hoặc tin mới nhất cũ hơn 2 giờ trong giờ làm việc, thì báo người dùng.

### Xong khi

- [ ] Chạy liên tục 5 ngày làm việc không mất yêu cầu nào.
- [ ] `jsm_import.csv` trên OneDrive cập nhật trong vòng 15 phút sau khi khách nhắn.

---

## 4. Giai đoạn 4 (sau này, khi có quyền JSM)

Chưa làm trong đợt này. Ghi lại để khỏi quên:
- Import `jsm_import.csv`. Ánh xạ cột *Request Type* theo `categories.*.jsm_request_type`.
- Chạy `hermes -p ticket mcp install atlassian`, rồi đổi bước 5 của skill sang dùng MCP.
- Điền `staff.*.jira` trong `cs_config.json`. Cập nhật lại phân công và SLA khi team chốt.

---

## 5. Những điều không được làm

- Không sửa các file listener của zalo-logger. Không ghi vào DB. Không chạy `VACUUM`.
- Không thêm tool cho profile `cskh`, và không chạy job dưới profile `default`.
- Không gửi **nội dung** file đính kèm (danh sách công chức) cho LLM. Chỉ gửi tên file và đường dẫn.
- Không chạy `hermes update`.
- Không commit dữ liệu thật: `data/`, `exports/`, `hermes-ticket/config/`.
- Không tự tạo task Windows hay sửa Task Scheduler khi chưa hỏi người dùng.
