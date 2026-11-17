#!/usr/bin/env python3
"""Đẩy ticket Hermes (tickets.jsonl + ledger.jsonl) lên Jira Service Management bằng REST API.

Chỉ đẩy ticket: trạng thái `open` HOẶC `resolved` (đã xong trong chat) trong ledger, tin neo gửi từ `jira.start` trở đi, và chưa từng đẩy.
Ticket `resolved` được tạo rồi chuyển sang trạng thái Done (transition có statusCategory "done"); ticket đã đẩy lúc còn `open` mà sau đó
thành `resolved` thì cũng được chuyển Done. Chỉ với issue do Hermes tạo (không đụng issue có sẵn của người khác).
Cập nhật của khách (dòng `update`) thành comment nội bộ. Trạng thái đã đẩy ở <state>/jira_state.json
(ghi sau MỖI ticket nên chết giữa chừng không tạo trùng; mô tả ticket có `zalo-ticket-key: <key>`).

Cấu hình (cs_config.json > "jira"; thông tin bí mật chỉ ở .env của profile, KHÔNG ghi vào file cấu hình):
  {"jira": {"enabled": true, "start": "2026-11-06T13:30+07:00", "service_desk_id": "<id>",
            "base_url": "https://<site>.atlassian.net"}}
  .env: JIRA_EMAIL, JIRA_API_TOKEN   (và JIRA_BASE_URL nếu không đặt base_url trong cấu hình)
  staff.<zalo_id>.jira = accountId Jira của nhân viên (hoặc email) để gán người xử lý; nhân viên chưa có thì dùng `default_assignee`
  của site (accountId, email, hoặc "me" = chủ API token).
Request Type lấy theo tên `categories.<cat>.jsm_request_type`.

  jira_push.py [--dry-run] [--limit N] [--check] [--list-desks]
`--list-desks` in id/key/tên các service desk tài khoản thấy được, để điền `service_desk_id`.
`--check` chỉ thử kết nối + liệt kê Request Type + kiểm tra ánh xạ category (không tạo gì).
Không có cấu hình/khoá thì thoát im lặng (mã 0) để dùng được làm job cron --no-agent.
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
HISTORY = Path(os.environ.get("ZALO_HISTORY_DIR", HERMES_HOME / "zalo-history"))
TZ = timezone(timedelta(hours=7))
PRIO = {"urgent": "Highest", "high": "High", "medium": "Medium", "low": "Low"}


def _ids_str(r: dict) -> dict:
    """Model đôi khi lưu message_id dạng số nguyên: chuẩn hoá về chuỗi để khớp với lịch sử/Jira."""
    for k in ("request_message_id", "group_id"):
        if r.get(k) is not None:
            r[k] = str(r[k])
    for k in ("source_message_ids", "seen_ids"):
        if isinstance(r.get(k), list):
            r[k] = [str(i) for i in r[k]]
    return r


def jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(_ids_str(json.loads(line)))
                except json.JSONDecodeError:
                    pass
    return out


def parse_time(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


class Jira:
    def __init__(self, base: str, email: str, token: str):
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    def upload(self, issue: str, filename: str, data: bytes):
        """Đính kèm tệp vào issue (multipart; Jira đòi header X-Atlassian-Token: no-check)."""
        boundary = "----zl" + os.urandom(8).hex()
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{self.base}/rest/api/3/issue/{issue}/attachments", method="POST", data=body,
                                     headers={"Authorization": self.auth, "Accept": "application/json",
                                              "X-Atlassian-Token": "no-check",
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": self.auth, "Accept": "application/json",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw[:300]}


NO_DONE = "không có transition nào đi tới Done"


def to_done(jira: "Jira", ik: str) -> tuple[bool, str]:
    """Chuyển issue sang trạng thái Done: lấy transition dẫn tới statusCategory "done"; nếu workflow bắt đi vòng thì đi qua bước "indeterminate"
    (vd In Progress) rồi thử lại (tối đa 3 bước). Issue đã Done sẵn thì coi là xong. Trả (thành công, ghi chú ngắn)."""
    cs, iss = jira.call("GET", f"/rest/api/3/issue/{ik}?fields=status")
    if cs == 200 and (((iss.get("fields") or {}).get("status") or {}).get("statusCategory") or {}).get("key") == "done":
        return True, "đã Done sẵn"
    for _ in range(3):
        c, r = jira.call("GET", f"/rest/api/3/issue/{ik}/transitions")
        if c != 200:
            return False, f"GET transitions {c}"
        trs = r.get("transitions") or []
        cat = lambda x: ((x.get("to") or {}).get("statusCategory") or {}).get("key")  # noqa: E731
        done = [x for x in trs if cat(x) == "done"]
        pick = done[0] if done else next((x for x in trs if cat(x) == "indeterminate"), None)
        if not pick:
            return False, NO_DONE
        c2, _ = jira.call("POST", f"/rest/api/3/issue/{ik}/transitions", {"transition": {"id": str(pick["id"])}})
        if c2 not in (200, 204):
            return False, f"POST transition {c2}"
        if done:
            return True, str(pick.get("name") or "Done")
    return False, "quá 3 bước chuyển trạng thái"


def hist_of(g: str, cache: dict) -> dict:
    """Đọc JSONL lịch sử của nhóm một lần: msg[message_id] = dòng tin; att[message_id] = tệp đã tải xong (sự kiện đến sau)."""
    if g not in cache:
        msg, att = {}, {}
        for r in jsonl(HISTORY / f"{g}.jsonl"):
            if r.get("message_id") and "from_id" in r:
                msg[r["message_id"]] = r
            elif r.get("type") == "attachment" and r.get("message_id"):
                att[r["message_id"]] = r.get("attachment") or {}
        cache[g] = {"msg": msg, "att": att}
    return cache[g]


def anchor_time(t: dict, led: dict, cache: dict, sla: int) -> datetime:
    """Giờ gửi tin neo (để so với jira.start). Tra trong JSONL; không thấy thì suy từ hạn - SLA, rồi từ giờ tạo."""
    m = hist_of(t["group_id"], cache)["msg"].get(t["request_message_id"])
    if m and m.get("timestamp"):
        return datetime.fromtimestamp(float(m["timestamp"]), TZ)
    if t.get("due") and t.get("due_by") == "sla":
        return parse_time(t["due"]) - timedelta(minutes=sla)
    return parse_time(led["at"])


LOAI = {"account_registration": ("Tạo tài khoản", "tao-tai-khoan"), "account_change": ("Thay đổi tài khoản", "thay-doi-tai-khoan"),
        "access_issue": ("Báo lỗi", "bao-loi"), "question": ("Câu hỏi", "cau-hoi"), "report_request": ("Báo cáo", "bao-cao"),
        "complaint": ("Khiếu nại", "khieu-nai"), "internal_task": ("Nội bộ", "noi-bo")}


def clock(ts) -> str:
    return datetime.fromtimestamp(float(ts), TZ).strftime("%H:%M:%S")


def files_of(t: dict, h: dict) -> list[dict]:
    """Tệp đính kèm của ticket: name, path (tương đối thư mục exports, hoặc None nếu chưa tải), giờ gửi."""
    out = []
    for x in t.get("attachments") or []:
        path, when = x.get("path"), None
        for mid in [t["request_message_id"], *(t.get("source_message_ids") or [])]:
            m = h["msg"].get(mid) or {}
            a = h["att"].get(mid) or m.get("attachment") or {}
            if a.get("name") == x.get("name"):
                path = path or a.get("path")
                when = m.get("timestamp")
        out.append({"name": x.get("name"), "path": path, "when": when})
    return out


VIEC = {"account_registration": "Tạo tài khoản theo tệp/danh sách khách gửi", "account_change": "Điều chỉnh tài khoản theo yêu cầu của khách",
        "access_issue": "Kiểm tra và khắc phục lỗi đăng nhập/truy cập cho khách", "question": "Trả lời câu hỏi của khách",
        "report_request": "Chuẩn bị báo cáo theo yêu cầu của khách", "complaint": "Xử lý khiếu nại và phản hồi lại khách",
        "internal_task": "Thực hiện việc nội bộ được giao"}


def task_ctx(t: dict, key: str, cfg: dict, h: dict, anchor: datetime, files: list[dict]) -> dict:
    """Các trường dùng chung để dựng mô tả task (bản chữ thường và bản ADF định dạng)."""
    conf = float(t.get("confidence", 1))
    g = cfg.get("groups", {}).get(t["group_id"], {})
    ids = [t["request_message_id"], *[i for i in (t.get("source_message_ids") or []) if i != t["request_message_id"]]]
    orig = []
    for mid in ids:
        m = h["msg"].get(mid)
        if not m:
            continue
        a = m.get("attachment")
        body = f"[Tập tin] {a.get('name')}" if a else (m.get("text") or "").replace("\n", " ").strip()[:300]
        orig.append((clock(m["timestamp"]), m.get("from_name") or m.get("from_id"), body))
    return {
        "conf": conf, "conf_s": "Cao" if conf >= 0.8 else "Trung bình" if conf >= 0.6 else "Thấp",
        "loai": LOAI.get(t.get("category") or "", (t.get("category") or "Khác", "khac"))[0],
        "gname": f"{g.get('name') or t['group_id']} ({'nội bộ' if t.get('kind') == 'internal' else 'khách hàng'})",
        "who": t.get("customer_name") or "-", "unit": t.get("unit") or "chưa rõ", "platform": t.get("platform") or "Chưa rõ",
        "anchor": f"{anchor:%Y-%m-%d %H:%M:%S}", "ids": ids, "orig": orig, "key": key, "desc": t["description"],
        "due": f"{parse_time(t['due']).astimezone(TZ):%Y-%m-%d %H:%M}" if t.get("due") else None,
        "priority": t.get("priority"), "files": [(f["name"], clock(f["when"]) if f["when"] else None) for f in files],
        "viec": VIEC.get(t.get("category") or "", "Xem yêu cầu của khách và xử lý"),
    }


def task_description(t: dict, key: str, cfg: dict, h: dict, anchor: datetime, files: list[dict]) -> str:
    """Mô tả dạng chữ thường (dùng cho kiểm tra/ghi log). Bản đăng lên Jira là task_description_adf (có định dạng)."""
    c = task_ctx(t, key, cfg, h, anchor, files)
    lines = [f"Ticket do AI tạo từ tin nhắn Zalo, cần người kiểm tra. Độ tin cậy: {c['conf_s']}. {c['desc']}",
             "Thông tin yêu cầu", f"Loại: {c['loai']}", f"Đơn vị: {c['unit']}",
             f"Người yêu cầu: {c['who']}", f"Tên trên Zalo: {c['who']}", f"Nhóm Zalo: {c['gname']}",
             f"Thời điểm nhận: {c['anchor']}", f"Nền tảng: {c['platform']}",
             "Số tài khoản: chưa rõ", "Nội dung", c["desc"]]
    if c["due"]:
        lines.append(f"Hạn xử lý (SLA): {c['due']}")
    if c["files"]:
        lines.append("File đính kèm")
        lines += [f"{n}" + (f" ({w})" if w else "") + " - xem mục Attachments của ticket" for n, w in c["files"]]
    lines.append("Tin nhắn gốc")
    lines += [f"[{w}] {who}: {body}" for w, who, body in c["orig"]]
    lines += [f"Mã tin Zalo: {', '.join(c['ids'])}", f"zalo-ticket-key: {c['key']}"]
    return "\n".join(lines)


# --- dựng ADF có định dạng (bố cục theo task của huantd: khung lưu ý, tiêu đề mục, nhãn in đậm; thêm dòng "Việc cần làm" và nhấn mạnh thông tin cần để xử lý)
def _tx(text: str, bold: bool = False, code: bool = False, red: bool = False) -> dict:
    marks = ([{"type": "strong"}] if bold else []) + ([{"type": "code"}] if code else []) \
        + ([{"type": "textColor", "attrs": {"color": "#de350b"}}] if red else [])
    return {"type": "text", "text": text, **({"marks": marks} if marks else {})}


def _p(*nodes: dict) -> dict:
    return {"type": "paragraph", "content": [n for n in nodes if n.get("text")]}


def _h(text: str) -> dict:
    return {"type": "heading", "attrs": {"level": 3}, "content": [_tx(text)]}


def _li(label: str, value: str, bold_value: bool = True, unknown: bool = False) -> dict:
    return {"type": "listItem", "content": [_p(_tx(label + ": ", bold=True), _tx(value, bold=bold_value and not unknown, red=unknown))]}


def task_description_adf(t: dict, key: str, cfg: dict, h: dict, anchor: datetime, files: list[dict]) -> dict:
    c = task_ctx(t, key, cfg, h, anchor, files)
    unit_unknown = c["unit"] in ("chưa rõ", "Chưa rõ đơn vị")
    low = c["conf"] < 0.6
    head = [_p(_tx("Việc cần làm: ", bold=True), _tx(c["viec"], bold=True), _tx(" — " + c["unit"], bold=True, red=unit_unknown))]
    if c["due"]:
        head.append(_p(_tx("Hạn xử lý (SLA): ", bold=True), _tx(c["due"], bold=True)))
    head.append(_p(_tx("Ticket do AI (Hermes) tạo từ tin nhắn Zalo, cần người kiểm tra. Độ tin cậy: "), _tx(c["conf_s"], bold=True, red=low)
                   , _tx(". Xem file đính kèm và tin nhắn gốc bên dưới trước khi xử lý.")))
    info = [_li("Loại", c["loai"]), _li("Đơn vị", c["unit"], unknown=unit_unknown),
            _li("Người yêu cầu", c["who"]), _li("Nhóm Zalo", c["gname"], bold_value=False),
            _li("Thời điểm nhận", c["anchor"], bold_value=False), _li("Nền tảng", c["platform"], unknown=c["platform"] == "Chưa rõ"),
            _li("Số tài khoản", "chưa rõ (xem tệp đính kèm)", unknown=True)]
    doc = [{"type": "panel", "attrs": {"panelType": "warning" if (low or unit_unknown) else "info"}, "content": head},
           _h("Thông tin yêu cầu"), {"type": "bulletList", "content": info},
           _h("Nội dung"), _p(_tx(c["desc"]))]
    if c["files"]:
        doc.append(_h("File đính kèm"))
        doc.append({"type": "bulletList", "content": [{"type": "listItem", "content": [_p(_tx(n, bold=True), _tx((f" ({w})" if w else "") + " - xem mục Attachments của ticket"))]} for n, w in c["files"]]})
    doc.append(_h("Tin nhắn gốc"))
    if c["orig"]:
        doc.append({"type": "blockquote", "content": [_p(_tx(f"[{w}] {who}: ", bold=True), _tx(body)) for w, who, body in c["orig"]]})
    doc.append(_p(_tx("Mã tin Zalo: "), _tx(", ".join(c["ids"]), code=True)))
    doc.append(_p(_tx("zalo-ticket-key: "), _tx(c["key"], code=True)))
    return {"type": "doc", "version": 1, "content": doc}


def adf(text: str) -> dict:
    """Văn bản thường → Atlassian Document Format (Jira REST v3 bắt buộc cho description/comment)."""
    paras = [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text.split("\n") if line.strip()]
    return {"type": "doc", "version": 1, "content": paras or [{"type": "paragraph", "content": []}]}


def description_of(t: dict, key: str) -> str:
    files = "\n".join(f"- {x.get('name')} ({x.get('path') or 'chưa tải'})" for x in t.get("attachments") or [])
    parts = [t["description"], "",
             f"Khách hàng: {t.get('customer_name') or ''} (Zalo {t.get('customer_zalo_id') or '?'})",
             f"Trích dẫn: \"{t.get('quote', '')}\""]
    if t.get("due"):
        parts.append(f"Hạn xử lý (SLA): {parse_time(t['due']).astimezone(TZ):%Y-%m-%d %H:%M}")
    if t.get("priority"):
        parts.append(f"Mức ưu tiên: {t['priority']}")
    if files:
        parts += ["Tệp đính kèm trên Zalo (tên và đường dẫn trong thư mục media):", files]
    parts += [f"Nguồn: Zalo {t['group_id']}, tin {', '.join(map(str, t.get('source_message_ids') or []))}", f"zalo-ticket-key: {key}"]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=20, help="tối đa số ticket mới mỗi lần chạy")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list-desks", action="store_true", help="liệt kê service desk (id, key, tên) để lấy service_desk_id")
    ap.add_argument("--state-dir", type=Path, default=STATE)
    ap.add_argument("--site", default="", help="tên site phụ trong cs_config.json > jira_sites (vd csa); bỏ trống = site chính (khối jira)")
    a = ap.parse_args()

    cfg_p = a.state_dir / "cs_config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
    if a.site:
        # site phụ (vd bản dự phòng cho site khác): cấu hình ở jira_sites.<tên>, khoá JIRA_<TÊN>_EMAIL / JIRA_<TÊN>_API_TOKEN, trạng thái riêng
        jc = (cfg.get("jira_sites") or {}).get(a.site) or {}
        tag = re.sub(r"\W", "_", a.site).upper()
        base = jc.get("base_url")
        email, token = os.environ.get(f"JIRA_{tag}_EMAIL"), os.environ.get(f"JIRA_{tag}_API_TOKEN")
    else:
        jc = cfg.get("jira") or {}
        base = os.environ.get("JIRA_BASE_URL") or jc.get("base_url")
        email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
    desk = str(jc.get("service_desk_id") or os.environ.get("JIRA_SERVICE_DESK_ID") or "")
    if a.list_desks:  # tra Service Desk ID: chỉ cần base_url + email + token
        if not (base and email and token):
            print("Thiếu base_url (cấu hình jira.base_url hoặc JIRA_BASE_URL), JIRA_EMAIL hoặc JIRA_API_TOKEN.")
            return 1
        code, res = Jira(base, email, token).call("GET", "/rest/servicedeskapi/servicedesk?limit=100")
        if code != 200:
            print(f"LỖI ({code}): {json.dumps(res, ensure_ascii=False)[:300]}")
            return 1
        for v in res.get("values", []):
            print(f"id={v.get('id')}  key={v.get('projectKey')}  tên={v.get('projectName')}")
        if not res.get("values"):
            print("Tài khoản này không thấy service desk nào (cần là agent hoặc admin của project JSM).")
        return 0
    mode = jc.get("mode", "jsm")  # "jsm": request qua servicedeskapi (cần agent JSM) | "task": issue qua Jira REST
    proj = str(jc.get("project_key") or "")
    issue_type = jc.get("issue_type", "Task")
    if not jc.get("enabled") or not (base and email and token and (proj if mode == "task" else desk)):
        return 0  # chưa cấu hình: im lặng
    jira = Jira(base, email, token)
    cats, staff = cfg.get("categories", {}), cfg.get("staff", {})
    slas = cfg.get("sla_minutes") or {}

    types: dict = {}
    if mode == "task":
        st, pr = jira.call("GET", f"/rest/api/3/project/{proj}")
        if st != 200:
            print(f"LỖI kết nối Jira ({st}): {json.dumps(pr, ensure_ascii=False)[:300]}")
            return 1
        if a.check:
            _, pm = jira.call("GET", f"/rest/api/3/mypermissions?projectKey={proj}&permissions=CREATE_ISSUES,ASSIGN_ISSUES,ADD_COMMENTS")
            perms = {k: v.get("havePermission") for k, v in (pm.get("permissions") or {}).items()}
            _, its = jira.call("GET", f"/rest/api/3/issue/createmeta/{proj}/issuetypes")
            names = [x["name"] for x in its.get("issueTypes", its.get("values", []))]
            print(f"Kết nối OK. Project {proj} ({pr.get('name')}), chế độ task, loại '{issue_type}'.")
            print(f"Quyền: {perms}. Loại issue tạo được: {', '.join(names)}")
            if issue_type not in names:
                print(f"CẢNH BÁO: không có loại '{issue_type}' trong project.")
            for zid, s in staff.items():
                if not s.get("jira"):
                    _, us = jira.call("GET", f"/rest/api/3/user/assignable/search?project={proj}&query={urllib.request.quote(s.get('name', ''))}")
                    cand = [f"{u.get('displayName')} ({u.get('accountId')})" for u in us][:3] if isinstance(us, list) else []
                    print(f"  staff {s.get('name')}: chưa có jira; ứng viên: {'; '.join(cand) or 'không thấy'}")
            return 0
    else:
        st, rt = jira.call("GET", f"/rest/servicedeskapi/servicedesk/{desk}/requesttype?limit=100")
        if st != 200:
            print(f"LỖI kết nối Jira ({st}): {json.dumps(rt, ensure_ascii=False)[:300]}")
            return 1
        types = {v["name"]: v["id"] for v in rt.get("values", [])}
        need = {c.get("jsm_request_type") or c.get("label") for c in cats.values()}
        missing = sorted(n for n in need if n not in types)
        if a.check:
            print(f"Kết nối OK. Service desk {desk}: {len(types)} Request Type: {', '.join(types)}")
            print("Thiếu Request Type (tên phải khớp jsm_request_type): " + (", ".join(missing) or "không"))
            return 0

    state_p = a.state_dir / (f"jira_state_{a.site}.json" if a.site else "jira_state.json")
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    start = parse_time(jc["start"]) if jc.get("start") else None

    def save():
        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    ledger = {}
    for r in jsonl(a.state_dir / "ledger.jsonl"):
        ledger[r["key"]] = r  # bản ghi cuối thắng (đúng trạng thái hiện tại)
    tickets, updates = {}, {}
    for r in jsonl(a.state_dir / "tickets.jsonl"):
        if "update" in r:
            updates.setdefault(r["key"], []).append(r["update"])
        else:
            tickets[r["key"]] = r

    cache: dict = {}
    created = commented = errors = waiting = done_n = 0
    # Chế độ dự phòng: Hermes chỉ tạo task sau `backup_grace_minutes` kể từ tin neo nếu trên Jira chưa có task cho tin đó
    # (pilot của huantd tạo trước). 0 hoặc thiếu = tạo ngay.
    grace = timedelta(minutes=float(jc.get("backup_grace_minutes") or 0))
    for key, t in tickets.items():
        led = ledger.get(key)
        status = (led or {}).get("status", "open")
        if not led or status not in ("open", "resolved"):
            continue  # đã loại (not_ours/duplicate/wrong) hoặc đóng (closed); resolved (xong trong chat) vẫn đẩy rồi chuyển Done
        if key not in state:
            if start and anchor_time(t, led, cache, int(slas.get(t.get("priority") or "medium", 30))) < start:
                continue  # trước mốc bắt đầu đẩy Jira
            if created >= a.limit:
                continue
            if grace and datetime.now(TZ) < anchor_time(t, led, cache, 30) + grace:
                waiting += 1  # chế độ dự phòng: chờ huantd/pilot tạo trước, hết thời gian chờ mà chưa có mới tạo thay
                continue
            cat = cats.get(t.get("category") or "", {})
            rtype = cat.get("jsm_request_type") or cat.get("label")
            if mode == "jsm" and rtype not in types:
                print(f"BỎ QUA {key}: không có Request Type '{rtype}' trên service desk")
                errors += 1
                continue
            if mode == "task" and jc.get("dedupe", True):
                # Chống trùng với task tạo tay hoặc pilot AI khác: tìm trên Jira theo mã tin neo của Zalo.
                q = urllib.parse.urlencode({"jql": f'project = {proj} AND text ~ "\\"{t["request_message_id"]}\\""',
                                            "fields": "summary", "maxResults": 1})
                cs, found = jira.call("GET", "/rest/api/3/search/jql?" + q)
                if cs == 200 and found.get("issues"):
                    ek = found["issues"][0]["key"]
                    print(f"{'[dry-run] ' if a.dry_run else ''}BỎ QUA '{t['title'][:50]}': tin {t['request_message_id']} đã có trên Jira ({ek})")
                    if not a.dry_run:
                        state[key] = {"issue": ek, "comments": len(updates.get(key, [])), "existing": True,
                                      "at": datetime.now(TZ).isoformat(timespec="seconds")}
                        save()
                    continue
            if a.dry_run:
                print(f"[dry-run] sẽ tạo{' rồi chuyển Done' if status == 'resolved' else ''}: {t['title']} ({issue_type if mode == 'task' else rtype})")
                created += 1
                continue
            h = hist_of(t["group_id"], cache)
            fl = files_of(t, h)
            desc = (task_description_adf(t, key, cfg, h, anchor_time(t, led, cache, 30), fl) if mode == "task"
                    else description_of(t, key))
            due = parse_time(t["due"]).astimezone(TZ).strftime("%Y-%m-%d") if t.get("due") else None
            prio = PRIO.get(t.get("priority") or "medium", "Medium")
            suffix = (jc.get("title_suffix") or "").strip()  # ví dụ "LT": dấu hiệu task do Hermes (của LT) tạo
            title = t["title"].strip()
            summary = (title if not suffix or title.endswith(" " + suffix) else f"{title[:240]} {suffix}")[:250]
            if mode == "task":
                labels = ["ai-drafted", "zalo", "hermes", LOAI.get(t.get("category") or "", (0, "khac"))[1]]
                f0 = {"project": {"key": proj}, "issuetype": {"name": issue_type},
                      "summary": summary, "description": desc, "labels": labels}
                full = dict(f0, priority={"name": prio}, **({"duedate": due} if due else {}))
                code, res = jira.call("POST", "/rest/api/3/issue", {"fields": full})
                if code == 400:  # màn hình tạo issue không có priority/duedate: thử lại tối giản
                    code, res = jira.call("POST", "/rest/api/3/issue", {"fields": f0})
                ik = res.get("key")
            else:
                fields = {"summary": summary, "description": desc}
                full = dict(fields, priority={"name": prio}, **({"duedate": due} if due else {}))
                body = {"serviceDeskId": desk, "requestTypeId": types[rtype]}
                code, res = jira.call("POST", "/rest/servicedeskapi/request", {**body, "requestFieldValues": full})
                if code == 400:  # form của Request Type không có priority/duedate: thử lại tối giản
                    code, res = jira.call("POST", "/rest/servicedeskapi/request", {**body, "requestFieldValues": fields})
                ik = res.get("issueKey")
            if code not in (200, 201) or not ik:
                print(f"LỖI tạo {key} ({code}): {json.dumps(res, ensure_ascii=False)[:300]}")
                errors += 1
                continue
            state[key] = {"issue": ik, "comments": 0, "at": datetime.now(TZ).isoformat(timespec="seconds")}
            save()
            created += 1
            print(f"Đã tạo {ik}: {t['title']}")
            acc = staff.get(t.get("assignee_zalo_id") or "", {}).get("jira") or jc.get("default_assignee")
            if acc == "me":  # chủ API token (tra một lần qua /myself)
                if "me" not in cache:
                    cm, rm = jira.call("GET", "/rest/api/3/myself")
                    cache["me"] = rm.get("accountId") if cm == 200 else None
                acc = cache["me"]
            elif acc and "@" in acc:  # email → accountId
                c2, r2 = jira.call("GET", f"/rest/api/3/user/search?query={urllib.request.quote(acc)}")
                acc = r2[0]["accountId"] if c2 == 200 and r2 else None
            if acc:
                c3, r3 = jira.call("PUT", f"/rest/api/3/issue/{ik}/assignee", {"accountId": acc})
                if c3 not in (200, 204):
                    print(f"  cảnh báo: chưa gán được người xử lý cho {ik} ({c3})")
            if mode == "jsm":  # chế độ task đã gắn label ngay lúc tạo
                labels = ["zalo", t.get("category") or "khac"] + (["noi-bo"] if t.get("kind") == "internal" else [])
                jira.call("PUT", f"/rest/api/3/issue/{ik}", {"update": {"labels": [{"add": x} for x in labels]}})
        # đính kèm tệp (task do Hermes tạo): thử lại nhiều lượt vì tệp có thể tải về sau lúc ticket được tạo
        if mode == "task" and key in state and not a.dry_run and not state[key].get("existing"):
            media_root = Path(os.environ.get("JIRA_MEDIA_ROOT") or jc.get("media_root") or "/mnt/d/zalo-logger/exports")
            max_b = int(jc.get("max_attachment_mb", 10)) * 1024 * 1024
            done_f = state[key].setdefault("files", {})
            age = datetime.now(TZ) - datetime.fromisoformat(state[key]["at"])
            for f in files_of(t, hist_of(t["group_id"], cache)):
                if f["name"] in done_f:
                    continue
                p = media_root / f["path"] if f["path"] else None
                if not p or not p.is_file() or p.stat().st_size == 0:
                    if age > timedelta(days=3):
                        done_f[f["name"]] = "bỏ qua: không có tệp sau 3 ngày"
                        save()
                    continue  # chưa tải xong, lượt sau thử lại
                if p.stat().st_size > max_b:
                    done_f[f["name"]] = "bỏ qua: quá lớn"
                    jira.call("POST", f"/rest/api/3/issue/{state[key]['issue']}/comment",
                              {"body": adf(f"Tệp '{f['name']}' quá lớn để đính kèm tự động, xem trong thư mục media trên OneDrive.")})
                    save()
                    continue
                code = jira.upload(state[key]["issue"], f["name"], p.read_bytes())
                if code in (200, 201):
                    done_f[f["name"]] = "ok"
                    save()
                    print(f"  đã đính kèm {f['name']} vào {state[key]['issue']}")
                else:
                    print(f"LỖI đính kèm {f['name']} vào {state[key]['issue']} ({code}), sẽ thử lại")
                    errors += 1
        # comment cho cập nhật của khách (chỉ với task do Hermes tạo; task có sẵn của người khác thì không đụng vào)
        if key in state and not a.dry_run and not state[key].get("existing"):
            done = state[key]["comments"]
            for note in updates.get(key, [])[done:]:
                if mode == "task":
                    code, res = jira.call("POST", f"/rest/api/3/issue/{state[key]['issue']}/comment", {"body": adf(note)})
                else:
                    code, res = jira.call("POST", f"/rest/servicedeskapi/request/{state[key]['issue']}/comment",
                                          {"body": note, "public": False})
                if code not in (200, 201):
                    print(f"LỖI comment {state[key]['issue']} ({code}): {json.dumps(res, ensure_ascii=False)[:200]}")
                    errors += 1
                    break
                state[key]["comments"] += 1
                save()
                commented += 1
        # ticket đã xong trong chat (resolved): chuyển issue do Hermes tạo sang Done (một lần; thất bại thì lượt sau thử lại)
        if key in state and not a.dry_run and not state[key].get("existing") and status == "resolved" and not state[key].get("done") and not state[key].get("done_skip"):
            ok, note = to_done(jira, state[key]["issue"])
            if ok:
                state[key]["done"] = True
                state[key]["done_at"] = datetime.now(TZ).isoformat(timespec="seconds")
                save()
                done_n += 1
                print(f"Đã chuyển {state[key]['issue']} sang Done ({note})")
                if note != "đã Done sẵn":
                    jira.call("POST", f"/rest/api/3/issue/{state[key]['issue']}/comment",
                              {"body": adf("Nhân viên đã xử lý xong yêu cầu này ngay trong chat Zalo; Hermes tự chuyển sang Done.")})
            elif note == NO_DONE:
                # workflow/quyền của project không cho tài khoản này đi tới Done (vd project JSM): đánh dấu bỏ qua, không thử lại mãi, không tính lỗi
                state[key]["done_skip"] = NO_DONE
                save()
                print(f"CẢNH BÁO: {state[key]['issue']} không chuyển được sang Done ({note}); bỏ qua ticket này, lượt sau không thử lại")
            else:
                print(f"LỖI chuyển {state[key]['issue']} sang Done: {note}")
                errors += 1
    if waiting and a.dry_run:
        print(f"[dry-run] {waiting} ticket đang chờ (dự phòng, chưa hết {int(grace.total_seconds() // 60)} phút kể từ tin neo)")
    if created or commented or done_n or errors:
        print(f"Jira: tạo {created}, comment {commented}, chuyển Done {done_n}, lỗi {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
