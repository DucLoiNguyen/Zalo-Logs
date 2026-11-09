#!/usr/bin/env python3
"""Đẩy ticket Hermes (tickets.jsonl + ledger.jsonl) lên Jira Service Management bằng REST API.

Chỉ đẩy ticket: trạng thái `open` trong ledger, tin neo gửi từ `jira.start` trở đi, và chưa từng đẩy.
Cập nhật của khách (dòng `update`) thành comment nội bộ. Trạng thái đã đẩy ở <state>/jira_state.json
(ghi sau MỖI ticket nên chết giữa chừng không tạo trùng; mô tả ticket có `zalo-ticket-key: <key>`).

Cấu hình (cs_config.json > "jira"; thông tin bí mật chỉ ở .env của profile, KHÔNG ghi vào file cấu hình):
  {"jira": {"enabled": true, "start": "2026-11-06T13:30+07:00", "service_desk_id": "<id>",
            "base_url": "https://<site>.atlassian.net"}}
  .env: JIRA_EMAIL, JIRA_API_TOKEN   (và JIRA_BASE_URL nếu không đặt base_url trong cấu hình)
  staff.<zalo_id>.jira = accountId Jira của nhân viên (hoặc email) để gán người xử lý.
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


def task_description(t: dict, key: str, cfg: dict, h: dict, anchor: datetime, files: list[dict]) -> str:
    """Mô tả theo quy ước của task do huantd tạo (SUP-19…): khối 'Thông tin yêu cầu', 'Nội dung', 'File đính kèm', 'Tin nhắn gốc'."""
    conf = float(t.get("confidence", 1))
    conf_s = "Cao" if conf >= 0.8 else "Trung bình" if conf >= 0.6 else "Thấp"
    loai = LOAI.get(t.get("category") or "", (t.get("category") or "Khác", "khac"))[0]
    g = cfg.get("groups", {}).get(t["group_id"], {})
    gname = f"{g.get('name') or t['group_id']} ({'nội bộ' if t.get('kind') == 'internal' else 'khách hàng'})"
    who = t.get("customer_name") or "-"
    ids = [t["request_message_id"], *[i for i in (t.get("source_message_ids") or []) if i != t["request_message_id"]]]
    orig = []
    for mid in ids:
        m = h["msg"].get(mid)
        if not m:
            continue
        a = m.get("attachment")
        body = f"[Tập tin] {a.get('name')}" if a else (m.get("text") or "").replace("\n", " ").strip()[:300]
        orig.append(f"[{clock(m['timestamp'])}] {m.get('from_name') or m.get('from_id')}: {body}")
    lines = [f"Ticket do AI tạo từ tin nhắn Zalo, cần người kiểm tra. Độ tin cậy: {conf_s}. {t['description']}",
             "Thông tin yêu cầu", f"Loại: {loai}", f"Đơn vị: {t.get('unit') or 'chưa rõ'}",
             f"Người yêu cầu: {who}", f"Tên trên Zalo: {who}", f"Nhóm Zalo: {gname}",
             f"Thời điểm nhận: {anchor:%Y-%m-%d %H:%M:%S}", f"Nền tảng: {t.get('platform') or 'Chưa rõ'}",
             "Số tài khoản: chưa rõ", "Nội dung", t["description"]]
    if t.get("due"):
        lines.append(f"Hạn xử lý (SLA): {parse_time(t['due']).astimezone(TZ):%Y-%m-%d %H:%M}")
    if files:
        lines.append("File đính kèm")
        lines += [f"{f['name']}" + (f" ({clock(f['when'])})" if f["when"] else "") + " - xem mục Attachments của ticket" for f in files]
    lines.append("Tin nhắn gốc")
    lines += orig
    lines += [f"Mã tin Zalo: {', '.join(ids)}", f"zalo-ticket-key: {key}"]
    return "\n".join(lines)


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
    a = ap.parse_args()

    cfg_p = a.state_dir / "cs_config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
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

    state_p = a.state_dir / "jira_state.json"
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
    created = commented = errors = 0
    for key, t in tickets.items():
        led = ledger.get(key)
        if not led or led.get("status", "open") != "open":
            continue  # đã loại (not_ours) hoặc đã xong trong chat
        if key not in state:
            if start and anchor_time(t, led, cache, int(slas.get(t.get("priority") or "medium", 30))) < start:
                continue  # trước mốc bắt đầu đẩy Jira
            if created >= a.limit:
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
                print(f"[dry-run] sẽ tạo: {t['title']} ({issue_type if mode == 'task' else rtype})")
                created += 1
                continue
            h = hist_of(t["group_id"], cache)
            fl = files_of(t, h)
            desc = (task_description(t, key, cfg, h, anchor_time(t, led, cache, 30), fl) if mode == "task"
                    else description_of(t, key))
            due = parse_time(t["due"]).astimezone(TZ).strftime("%Y-%m-%d") if t.get("due") else None
            prio = PRIO.get(t.get("priority") or "medium", "Medium")
            suffix = (jc.get("title_suffix") or "").strip()  # ví dụ "LT": dấu hiệu task do Hermes (của LT) tạo
            title = t["title"].strip()
            summary = (title if not suffix or title.endswith(" " + suffix) else f"{title[:240]} {suffix}")[:250]
            if mode == "task":
                labels = ["ai-drafted", "zalo", "hermes", LOAI.get(t.get("category") or "", (0, "khac"))[1]]
                f0 = {"project": {"key": proj}, "issuetype": {"name": issue_type},
                      "summary": summary, "description": adf(desc), "labels": labels}
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
            acc = staff.get(t.get("assignee_zalo_id") or "", {}).get("jira")
            if acc and "@" in acc:  # email → accountId
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
        # comment cho cập nhật của khách (chỉ với ticket đã đẩy)
        if key in state and not a.dry_run:
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
    if created or commented or errors:
        print(f"Jira: tạo {created}, comment {commented}, lỗi {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
