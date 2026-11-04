# Hermes Agent điều phối ticket CS từ group Zalo

Bối cảnh: team CS nhận yêu cầu của khách trong một group Zalo, và tin nhắn của group đã được log lại. Mục tiêu là để Hermes Agent đọc log, hiểu từng yêu cầu, phân loại, giao cho đúng người, đặt hạn theo SLA, rồi tạo ticket. Khi khách nhắn thêm về một yêu cầu cũ, Hermes cập nhật ticket cũ chứ không tạo ticket mới.

Mọi tính năng Hermes nhắc tới đây đã được đối chiếu với repo chính thức `NousResearch/hermes-agent` (thư mục `website/docs`), ngày 04/11/2026.

---

## 0. Dùng luôn Hermes và bot Zalo đã cài trên máy

Bạn không cần cài lại gì. Chỉ cần thêm skill này vào Hermes đang có (`cp -r skills/zalo-task-tickets ~/.hermes/skills/`). Nếu bạn dùng profile riêng, chép vào thư mục skills của profile đó.

Nếu bot Zalo của bạn chạy bằng plugin `hermes-plugin-zalo`, plugin này có sẵn tính năng lưu log nhóm vào `~/.hermes/zalo-history/<group_id>.jsonl`, đúng định dạng skill đọc. Trước khi dùng, có ba điểm cần biết (mình rút ra từ đọc mã nguồn plugin, `zalo/adapter.py`):

1. Plugin chỉ lưu log khi nhóm đã bật **`/follow`**. Bạn gõ `/follow` trong group; lệnh này chỉ chạy với user nằm trong `ZALO_ALLOWED_USERS`.
2. Tin nhắn được giữ trong bộ nhớ và **chỉ ghi ra file khi gom đủ 500 tin** của một nhóm. Nếu gateway khởi động lại trước khi đủ 500 tin, các tin đó bị mất, và file log sẽ xuất hiện rất muộn. Cách sửa nhanh: trong `~/.hermes/plugins/zalo/zalo/adapter.py`, hàm `save_message`, đổi `> 500` thành `>= 1` để ghi ngay từng tin.
3. Thời gian của mỗi tin là lúc bot nhận được tin, không phải lúc khách gửi. Với chế độ long-polling thì hai thời điểm này gần như trùng nhau.

Nếu bạn log bằng cách khác thì dùng `convert_log.py` để chuyển sang định dạng trên.

## 1. Luồng hoạt động

```
 Group Zalo CS ── công cụ log của bạn ──► CSV/JSON ──► convert_log.py
                                                         │
                       ~/.hermes/zalo-history/<group>.jsonl
                                                         │  cron mỗi 15–30 phút
                                                         ▼
 Hermes Agent + skill "zalo-task-tickets"
   1. zalo_window.py          tin MỚI (gắn nhãn NV/KH) + ngữ cảnh + ticket đang mở
   2. LLM đọc hội thoại       gom tin thành từng YÊU CẦU, phân loại, đánh giá mức gấp
   3. create_tickets.py       điều phối theo cs_config.json: người phụ trách, ưu tiên, hạn SLA
                              chống trùng; tin nhắc lại → CẬP NHẬT ticket cũ
   4. Tạo/cập nhật ticket     Jira/Linear/Notion… qua MCP, hoặc GitHub Issues, hoặc file local
   5. Dời checkpoint + báo cáo   ticket mới, ticket khách nhắc lại, ticket quá hạn, mục cần người xem
```

Việc được chia hai phần rõ ràng. **LLM** làm phần cần hiểu ngôn ngữ: tin nào là yêu cầu, thuộc loại gì, có gấp không, tin nào nối vào yêu cầu cũ. **Script** làm phần phải chính xác tuyệt đối: giao cho ai theo quy tắc, hạn SLA, không tạo trùng, không mất tin khi lỗi. Nhờ vậy, khách nhắn "giao cho anh X đi" cũng không đổi được người xử lý.

---

## 2. Cấu hình điều phối: `cs_config.json`

Đây là file bạn chỉnh để khớp với team của mình (mẫu: [`sample/cs_config.json`](sample/cs_config.json)):

```json
{
  "staff":      { "u_200": {"name": "Lợi", "team": "CS", "jira": "loi@congty.vn"}, "...": {} },
  "categories": {
    "bug":     {"label": "Lỗi kỹ thuật",       "assignee": "u_201", "priority": "high"},
    "billing": {"label": "Thanh toán/Hoá đơn", "assignee": "u_202", "priority": "medium"},
    "...": {}
  },
  "default_assignee": "u_200",
  "sla_hours": {"urgent": 2, "high": 4, "medium": 24, "low": 72}
}
```

- `staff` dùng để phân biệt nhân viên với khách: ai không có trong danh sách đều được coi là khách. Mỗi nhân viên được ánh xạ sang tài khoản trên hệ thống ticket.
- **Thứ tự quyết định người xử lý:** trước hết là nhân viên đã nhận việc trong chat ("để em kiểm tra"), sau đó đến người phụ trách của category, cuối cùng là `default_assignee`.
- **Thứ tự quyết định hạn:** hạn nêu trong chat ("trước 5h chiều nay") được ưu tiên. Nếu không có, hạn = thời điểm khách gửi yêu cầu + `sla_hours` theo mức ưu tiên.

---

## 3. Kết quả chạy thử với dữ liệu mẫu

Group mẫu có 11 tin, chia hai đợt ([`batch1`](sample/cs-khach-hang.batch1.jsonl), [`batch2`](sample/cs-khach-hang.batch2.jsonl)). Đầu ra LLM mong muốn nằm trong `expected_batch1.json` và `expected_batch2.json`. Script cho ra:

| Đợt | Chat | Kết quả |
|---|---|---|
| 1 | Chị Hoa: không đăng nhập được, đang có đơn gấp. Lợi hỏi SĐT | **Tạo** ticket Lỗi kỹ thuật → Phong (theo category), urgent, hạn 11:00 (SLA 2h) |
| 1 | Anh Nam hỏi hoá đơn VAT. Trang nhận "báo trước 5h chiều nay" | **Tạo** ticket → Trang (nhận trong chat), hạn 17:00 (theo chat) |
| 1 | Bác Sơn đề xuất xuất Excel theo tuần | **Tạo** ticket Đề xuất tính năng → Hà, low, hạn +72h |
| 1 | Chị Linh hỏi cách đổi địa chỉ, Lợi trả lời, khách cảm ơn | **Không tạo**, ghi nhận đã giải quyết trong chat |
| 2 | Chị Hoa: "Sao chưa ai xử lý, mất 2 đơn rồi" | **Cập nhật** ticket đăng nhập (thêm comment) |
| 2 | Anh Nam: "hoá đơn tháng 9 cũng sai MST" | **Cập nhật** ticket VAT |
| 2 | Chạy lại đợt 2 lần nữa | `duplicate`, không tạo hay sửa gì |

Lệnh `--list-open` còn đánh dấu những ticket đã **quá hạn**, để Hermes nhắc trong báo cáo.

Phần script đã chạy thật và cho đúng kết quả trên. Phần LLM (đọc chat → JSON) chưa chạy thật, vì môi trường của mình không có khoá provider.

---

## 4. Nối với hệ thống ticket

### Giai đoạn hiện tại: chưa có quyền Jira, dùng ticket thô

Chạy backend `local` (mặc định), sau đó chạy `export_jsm.py`:

```bash
python3 ~/.hermes/skills/zalo-task-tickets/scripts/export_jsm.py
# → ~/.hermes/zalo-tickets/export/jsm_import.csv   (mở bằng Excel để xem; sau này import CSV vào JSM)
# → ~/.hermes/zalo-tickets/export/jsm_requests.json (payload cho POST /rest/servicedeskapi/request)
```

Xem kết quả mẫu tại [`sample/export/jsm_import.csv`](sample/export/jsm_import.csv). Các cột trong file theo đúng trường của JSM: Summary, Description, Request Type, Priority (Highest/High/Medium/Low), Assignee, Due Date, Labels. Mỗi lần khách nhắn thêm được ghi thành một cột Comment. Request Type lấy từ `categories.<cat>.jsm_request_type` trong `cs_config.json`. Khi đã có JSM, bạn chỉ cần sửa tên cho khớp với Request Type thật.

Khi đã có quyền, có hai cách chuyển dữ liệu sang Jira:
1. Import CSV một lần cho dữ liệu đã có: dùng *Import issues from CSV* trên Jira, ánh xạ cột *Request Type* sang Customer Request Type. Thao tác này cần quyền admin project.
2. Tạo tự động từ đó trở đi: `hermes mcp install atlassian`, rồi đổi bước 5 trong skill sang dùng MCP. Tên Request Type vẫn giữ như trong `cs_config.json`.

| Hệ thống | Cách nối |
|---|---|
| **Jira** (Service Management / Software) | MCP chính thức trong catalog: `hermes mcp install atlassian` (đăng nhập OAuth) |
| **Linear, Notion, Asana, ClickUp, Monday, Airtable, Todoist, GitLab** | Có sẵn trong catalog MCP: `hermes mcp install <tên>` |
| **GitHub Issues** | Có sẵn trong script: `--backend github --repo owner/repo` |
| **Zendesk, Freshdesk, Trello, CRM nội bộ** | Không có trong catalog. Có hai cách: cấu hình một MCP server bên thứ ba trong `mcp_servers`, hoặc mình thêm một backend REST nhỏ vào `create_tickets.py` |
| **Chưa chọn** | `--backend local`, ghi ra `~/.hermes/zalo-tickets/tickets.jsonl` |

---

## 5. Chạy Hermes

**Khoá API:** Hermes không có khoá riêng. Bạn cấu hình khoá của một nhà cung cấp LLM bằng `hermes setup` (OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek, Nous Portal…) hoặc dùng mô hình local. Dữ liệu chat của khách sẽ được gửi tới nhà cung cấp đó, nên bạn cần kiểm tra chính sách dữ liệu của công ty.

**Chạy tự động bằng cron** (cách khuyến nghị cho CS):
```bash
hermes cron create "every 15m" \
  "Xử lý mọi group Zalo có tin mới theo skill zalo-task-tickets, tạo/cập nhật ticket (backend local) rồi chạy export_jsm.py, báo cáo ticket mới, ticket khách nhắc lại và ticket quá hạn." \
  --skill zalo-task-tickets
```

**Gọi từ công cụ log của bạn:** công cụ log có thể gọi Hermes ngay sau mỗi đợt ghi, thay vì chờ cron. Có hai cách:
- **API server tương thích OpenAI:** thêm `API_SERVER_ENABLED=true` và `API_SERVER_KEY=...` vào `~/.hermes/.env`, chạy `hermes gateway`, rồi gọi `POST http://127.0.0.1:8642/v1/runs`.
- **Webhook:** cổng 8644, có xác thực HMAC, và có thể kích hoạt thẳng job cron ở trên.

**Duyệt tay:** `hermes chat -s zalo-task-tickets -q "Xử lý group cs-khach-hang"`. Hermes sẽ đưa danh sách để bạn xác nhận trước khi tạo ticket.

**Về quyền:** khi chạy cron hoặc webhook thì không có ai bấm duyệt. Vì vậy MCP server tạo ticket cần để `trust: full` (đây là mặc định). Nếu muốn có người duyệt, hãy cho Hermes chỉ gửi đề xuất về một kênh chat để bạn xác nhận.

---

## 6. Cài thử (khoảng 5 phút)

```bash
cp -r skills/zalo-task-tickets ~/.hermes/skills/
mkdir -p ~/.hermes/zalo-history ~/.hermes/zalo-tickets
cp sample/cs_config.json ~/.hermes/zalo-tickets/
cp sample/cs-khach-hang.batch1.jsonl ~/.hermes/zalo-history/cs-khach-hang.jsonl
# log thật của bạn: python3 skills/zalo-task-tickets/scripts/convert_log.py log.csv --group <id> \
#   --time-col <cột giờ> --name-col <cột tên> --id-col <cột id> --text-col <cột nội dung>

hermes chat -s zalo-task-tickets -q "Xử lý group cs-khach-hang (backend local)"
```

---

## 7. Cần bạn cho biết

1. **Bot Zalo của bạn có chạy bằng plugin `hermes-plugin-zalo` không**, và đã có file trong `~/.hermes/zalo-history/` chưa? Bạn có thể chạy `ls ~/.hermes/plugins ~/.hermes/zalo-history` để kiểm tra.
2. **5–10 dòng log mẫu** (đã che thông tin khách), để mình chỉnh `convert_log.py` cho khớp. Log có lưu ID người gửi, hay chỉ có tên?
3. **Danh sách nhân viên trong group, các loại yêu cầu và người phụ trách từng loại, SLA**, để điền `cs_config.json`.
4. **Tự động hay có duyệt:** tạo ticket luôn, hay gửi đề xuất để bạn duyệt trước? Có cần trả lời khách trong group (ví dụ "Đã ghi nhận, mã ticket CS-123") không? Nếu cần thì phải có bot Zalo trong group.
