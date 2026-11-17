#!/usr/bin/env python3
"""Điều phối & tạo ticket CS từ các yêu cầu khách hàng mà Hermes trích ra từ group Zalo.

Đầu vào: file JSON (hoặc '-' = stdin), là một mảng yêu cầu theo schema:
  {
    "request_message_id": "m2001",        # BẮT BUỘC: tin nhắn đầu tiên nêu yêu cầu (mỏ neo)
    "group_id": "cs-khach-hang",          # BẮT BUỘC
    "title": "Không đăng nhập được app",  # BẮT BUỘC
    "description": "…",                   # BẮT BUỘC
    "category": "bug",                    # một khoá trong cs_config.json > categories
    "priority": "urgent|high|medium|low" | null,   # null → lấy mặc định của category
    "customer_name": "Chị Hoa - Shop ABC", "customer_zalo_id": "c_01",
    "assignee_zalo_id": "u_201" | null,   # chỉ điền khi NHÂN VIÊN đã nhận trong chat
    "due": "2026-11-05T17:00" | null,     # chỉ điền khi khách/NV nêu hạn; null → tính theo SLA
    "source_message_ids": ["m2001", "m2003"],
    "quote": "trích nguyên văn yêu cầu",
    "status_hint": "open" | "resolved_in_chat",   # resolved_in_chat: vẫn tạo ticket, ghi trạng thái `resolved` (không đẩy Jira)
    "attachments": [{"name": "DS dang ky xa A.xlsx", "path": "media/2026-11-04/…"}],  # tuỳ chọn
    "kind": "cs" | "internal",            # internal = task nội bộ từ nhóm nội bộ (không có khách)
    "confidence": 0.0-1.0
  }

Khoá ticket = group_id + request_message_id. Nếu khoá đã có trong sổ:
  có tin mới (source_message_ids chưa ghi nhận) → CẬP NHẬT ticket (thêm comment), không tạo mới
  không có gì mới → duplicate

Điều phối (khi không có assignee_zalo_id): categories[category].assignee → default_assignee.
Hạn (khi due = null): thời điểm tin request_message_id + sla_minutes[priority] (hoặc sla_hours).

Backend:
  local  (mặc định) ghi <state>/tickets.jsonl
  github --repo owner/name   tạo issue / comment bằng `gh`
  record --external KEY=URL  chỉ ghi sổ sau khi Hermes đã tạo ticket qua MCP (Jira, …)

Lệnh phụ:
  --list-open   in các ticket đang mở (để Hermes nối tin bổ sung vào đúng ticket)
  --dry-run     chỉ kiểm tra & cho biết sẽ làm gì
Chỉ dùng thư viện chuẩn (+ `gh` nếu dùng backend github).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
HISTORY = Path(os.environ.get("ZALO_HISTORY_DIR", HERMES_HOME / "zalo-history"))
TZ = timezone(timedelta(hours=7))
REQUIRED = ("request_message_id", "group_id", "title", "description")
PRIORITIES = ("urgent", "high", "medium", "low")
CLOSED_BY_HUMAN = ("not_ours", "duplicate", "wrong")  # trạng thái do mark_ticket.py ghi; không bao giờ tự mở lại


def review_reason(t: dict) -> str:
    """Vì sao tin này vào hàng đợi "cần người xem" (review.jsonl / review.csv)."""
    if str(t.get("title", "")).startswith("[Cần xem]"):
        return "Model không phân loại được sau nhiều lần hỏi (hệ thống tự tạo mục này)"
    return f"Model không chắc đây là yêu cầu (độ tin cậy {t.get('confidence')})"


def key_of(t: dict) -> str:
    return f"{t['group_id']}:{t['request_message_id']}"


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def append(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_ledger(state: Path) -> dict:
    """Sổ ticket: gộp các bản ghi theo khoá, bản ghi sau ghi đè/gộp bản trước."""
    led: dict[str, dict] = {}
    for r in load_jsonl(state / "ledger.jsonl"):
        cur = led.setdefault(r["key"], {"seen_ids": []})
        seen = {str(i) for i in cur["seen_ids"]} | {str(i) for i in r.get("seen_ids", [])}  # model đôi khi ghi số nguyên
        cur.update(r)
        cur["seen_ids"] = sorted(seen)
    return led


def msg_time(group: str, mid: str, history: Path) -> datetime | None:
    for m in load_jsonl(history / f"{group}.jsonl"):
        if m.get("message_id") == mid:
            return datetime.fromtimestamp(float(m["timestamp"]), TZ)
    return None


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def message_stamps(group: str, history: Path) -> dict[str, float]:
    """message_id -> timestamp (giây) của các tin nhắn thật trong log nhóm (bỏ qua dòng sự kiện không có timestamp)."""
    return {str(m["message_id"]): float(m["timestamp"]) for m in load_jsonl(history / f"{group}.jsonl")
            if m.get("message_id") is not None and m.get("timestamp")}


def print_compact(rows: list[dict], history: Path, now: datetime) -> None:
    """Mỗi ticket một dòng `key | tiêu đề | khách | đơn vị | hạn`, để nhét vào prompt triage cho gọn.

    Chỉ giữ ticket có tin neo trong TRIAGE_OPEN_DAYS ngày gần nhất (mặc định 7) HOẶC chưa quá hạn; tối đa
    TRIAGE_OPEN_MAX dòng (mặc định 20) MỖI NHÓM, mới nhất trước. Ticket đã đóng (closed) / đã xong trong chat (resolved)
    gắn nhãn "(đã đóng)" / "(đã xong trong chat)", chỉ xét theo tin neo. Số dòng còn lại mỗi nhóm in ra stderr."""
    days = float(os.environ.get("TRIAGE_OPEN_DAYS", "7"))
    cap = int(os.environ.get("TRIAGE_OPEN_MAX", "20"))
    cutoff = now - timedelta(days=days)
    by_group: dict[str, list[dict]] = {}
    for r in rows:
        by_group.setdefault(str(r.get("key", "")).split(":", 1)[0], []).append(r)
    cell = lambda v: str(v or "").replace("|", "/").replace("\n", " ")  # noqa: E731
    for group, items in by_group.items():
        stamps = message_stamps(group, history)
        keep = []
        for r in items:
            anchor = stamps.get(str(r.get("key", "")).split(":", 1)[-1])
            when = datetime.fromtimestamp(anchor, TZ) if anchor else (_aware(datetime.fromisoformat(r["at"])) if r.get("at") else now)
            not_overdue = r.get("status", "open") == "open" and bool(r.get("due")) and _aware(datetime.fromisoformat(r["due"])) >= now
            if when >= cutoff or not_overdue:
                keep.append((when, r))
        keep.sort(key=lambda x: x[0], reverse=True)
        for _, r in keep[:cap]:
            cols = [r.get(k) for k in ("key", "title", "customer", "unit", "due")]
            if r.get("status") in ("closed", "resolved"):
                cols[1] = ("(đã đóng) " if r["status"] == "closed" else "(đã xong trong chat) ") + str(cols[1] or "")
            print(" | ".join(cell(c) for c in cols))
        print(f"# nhóm {group}: {min(len(keep), cap)}/{len(items)} ticket mở sau khi lọc", file=sys.stderr)


def validate(t: dict, cfg: dict) -> list[str]:
    errs = [f"thiếu '{k}'" for k in REQUIRED if not t.get(k)]
    if t.get("due"):
        try:
            datetime.fromisoformat(t["due"])
        except ValueError:
            errs.append(f"due không phải ISO: {t['due']}")
    if t.get("priority") and t["priority"] not in PRIORITIES:
        errs.append(f"priority lạ: {t['priority']}")
    cats = cfg.get("categories", {})
    if cats and t.get("category") and t["category"] not in cats:
        errs.append(f"category lạ: {t['category']} (hợp lệ: {', '.join(cats)})")
    a = t.get("assignee_zalo_id")
    if a and cfg.get("staff") and a not in cfg["staff"]:
        errs.append(f"assignee_zalo_id {a} không phải nhân viên trong cs_config.json")
    return errs


def route(t: dict, cfg: dict, history: Path) -> dict:
    """Điền người xử lý, ưu tiên, hạn theo quy tắc trong cs_config.json."""
    cat = cfg.get("categories", {}).get(t.get("category") or "", {})
    assignee = t.get("assignee_zalo_id") or cat.get("assignee") or cfg.get("default_assignee")
    routed_by = "chat" if t.get("assignee_zalo_id") else ("category" if cat.get("assignee") else "default")
    priority = t.get("priority") or cat.get("priority") or "medium"
    due, due_by = t.get("due"), "chat"
    if not due:
        base = msg_time(t["group_id"], t["request_message_id"], history) or datetime.now(TZ)
        mins = cfg.get("sla_minutes", {}).get(priority)
        if mins is None and cfg.get("sla_hours", {}).get(priority) is not None:
            mins = cfg["sla_hours"][priority] * 60
        due = (base + timedelta(minutes=mins)).isoformat(timespec="minutes") if mins else None
        due_by = "sla" if mins else None
    staff = cfg.get("staff", {}).get(assignee or "", {})
    return {"assignee_zalo_id": assignee, "assignee_name": staff.get("name"), "routed_by": routed_by,
            "priority": priority, "due": due, "due_by": due_by,
            "category_label": cat.get("label", t.get("category"))}


def body_of(t: dict, r: dict, key: str) -> str:
    return "\n".join([
        t["description"], "",
        f"**Khách hàng:** {t.get('customer_name') or t.get('customer_zalo_id') or 'chưa rõ'}",
        f"**Loại:** {r['category_label']} · **Ưu tiên:** {r['priority']} · **Hạn:** {r['due'] or 'chưa rõ'} ({r['due_by']})",
        f"**Phụ trách:** {r['assignee_name'] or r['assignee_zalo_id'] or 'chưa phân công'} (theo {r['routed_by']})",
        "", f"> {t.get('quote', '')}", "",
        *([f"**Tệp đính kèm:** " + "; ".join(f"{x.get('name')} ({x.get('path') or 'chưa tải'})" for x in t["attachments"])]
          if t.get("attachments") else []),
        f"Nguồn: Zalo `{t['group_id']}`, tin {', '.join(map(str, t.get('source_message_ids') or [t['request_message_id']]))}",
        f"<!-- zalo-ticket-key: {key} -->",
    ])


def gh(args: list[str]) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return p.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="?", help="file JSON hoặc '-'")
    ap.add_argument("--backend", choices=["local", "github", "record"], default="local")
    ap.add_argument("--repo")
    ap.add_argument("--external", action="append", default=[], help="KEY=URL (backend record)")
    ap.add_argument("--config", type=Path, default=STATE / "cs_config.json")
    ap.add_argument("--state-dir", type=Path, default=STATE)
    ap.add_argument("--history-dir", type=Path, default=HISTORY)
    ap.add_argument("--min-confidence", type=float, default=0.6)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-open", action="store_true")
    ap.add_argument("--group", help="với --list-open: chỉ ticket của nhóm này")
    ap.add_argument("--compact", action="store_true", help="với --list-open: mỗi ticket một dòng `key | tiêu đề | khách | đơn vị | hạn`")
    a = ap.parse_args()

    cfg = json.loads(a.config.read_text(encoding="utf-8")) if a.config.exists() else {}
    ledger = load_ledger(a.state_dir)

    if a.list_open:
        now = datetime.now(TZ)
        rows = []
        for r in ledger.values():
            if r.get("status", "open") != "open" and not (a.compact and r.get("status") in ("closed", "resolved")):
                continue  # compact còn giữ ticket đã đóng / đã xong trong chat (gắn nhãn) để khách nhắn tiếp thì mở lại được đúng ticket
            if a.group and not str(r.get("key", "")).startswith(f"{a.group}:"):
                continue
            if a.compact:
                rows.append(r)
                continue
            row = {k: v for k, v in r.items() if k in ("key", "title", "customer", "assignee", "priority", "due", "url")}
            if r.get("due"):
                d = datetime.fromisoformat(r["due"])
                row["overdue"] = (d if d.tzinfo else d.replace(tzinfo=TZ)) < now
            rows.append(row)
        if a.compact:
            print_compact(rows, a.history_dir, now)
        else:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not a.tasks:
        ap.error("cần file tasks (hoặc --list-open)")

    tasks = json.load(sys.stdin if a.tasks == "-" else open(a.tasks, encoding="utf-8"))
    tasks = tasks.get("tasks", []) if isinstance(tasks, dict) else tasks
    external = dict(e.split("=", 1) for e in a.external)
    out = []

    for t in tasks:
        # model đôi khi ghi message_id dạng số nguyên: chuẩn hoá về chuỗi trước khi so sánh/lưu/xuất
        for k in ("request_message_id", "group_id"):
            if t.get(k) is not None:
                t[k] = str(t[k])
        if t.get("source_message_ids"):
            t["source_message_ids"] = [str(i) for i in t["source_message_ids"]]
        errs = validate(t, cfg)
        if errs:
            out.append({"title": t.get("title"), "status": "invalid", "errors": errs})
            continue
        key = key_of(t)
        ids = sorted(set(t.get("source_message_ids") or []) | {t["request_message_id"]})
        prev = ledger.get(key)
        if prev and prev.get("status") in CLOSED_BY_HUMAN:  # đã loại/đánh dấu (not_ours: yêu cầu của Misa; duplicate; wrong): đừng mở lại
            out.append({"key": key, "title": t["title"], "status": f"skipped_{prev['status']}"})
            continue
        new_ids = [i for i in ids if not prev or i not in prev["seen_ids"]]
        if prev and not new_ids:
            out.append({"key": key, "title": t["title"], "status": "duplicate", "url": prev.get("url")})
            continue
        if not prev and float(t.get("confidence", 1)) < a.min_confidence:
            out.append({"key": key, "title": t["title"], "status": "low_confidence"})
            if not a.dry_run and key not in {r.get("key") for r in load_jsonl(a.state_dir / "review.jsonl")}:
                # không có ticket, nhưng giữ lại hàng đợi "cần người xem" để không mất yêu cầu của khách
                append(a.state_dir / "review.jsonl", {"key": key, "title": t["title"], "description": t.get("description"),
                                                      "quote": t.get("quote"), "customer": t.get("customer_name"),
                                                      "confidence": t.get("confidence"), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                      "reason": review_reason(t)})
            continue
        r = route(t, cfg, a.history_dir)
        action = "would_update" if prev else "would_create"
        if a.dry_run:
            out.append({"key": key, "title": t["title"], "status": action, "new_message_ids": new_ids, **r})
            continue
        try:
            if prev:  # ---- cập nhật ticket đã có
                note = f"Cập nhật từ Zalo (tin {', '.join(new_ids)}): {t['description']}\n\n> {t.get('quote', '')}"
                if a.backend == "github" and prev.get("url", "").startswith("http"):
                    gh(["issue", "comment", prev["url"], "--body", note])
                elif a.backend == "local":
                    append(a.state_dir / "tickets.jsonl", {"key": key, "update": note})
                url = prev.get("url")
            else:     # ---- tạo mới
                if a.backend == "local":
                    url = f"local:{key}"
                    append(a.state_dir / "tickets.jsonl", {**t, **r, "key": key})
                elif a.backend == "github":
                    if not a.repo:
                        ap.error("--backend github cần --repo")
                    gh_login = cfg.get("staff", {}).get(r["assignee_zalo_id"] or "", {}).get("github")
                    cmd = ["issue", "create", "--repo", a.repo, "--title", f"[{r['category_label']}] {t['title']}",
                           "--body", body_of(t, r, key)]
                    url = gh(cmd + (["--assignee", gh_login] if gh_login else []))
                else:
                    url = external.get(key)
                    if not url:
                        out.append({"key": key, "title": t["title"], "status": "missing_external_url"})
                        continue
        except Exception as e:  # noqa: BLE001
            out.append({"key": key, "title": t["title"], "status": "error", "error": str(e)})
            continue
        rec = {"key": key, "url": url, "title": t["title"], "backend": a.backend, "seen_ids": ids,
               "customer": t.get("customer_name") or (prev or {}).get("customer"), "kind": t.get("kind", "cs"),
               # cập nhật ticket cũ thì giữ người nhận/ưu tiên/hạn đã có (trừ khi tin cập nhật nêu rõ)
               "assignee": (r["assignee_name"] or r["assignee_zalo_id"]) if not prev or t.get("assignee_zalo_id") else (prev.get("assignee") or r["assignee_name"] or r["assignee_zalo_id"]),
               "priority": r["priority"] if not prev or t.get("priority") else (prev.get("priority") or r["priority"]),
               "due": r["due"] if not prev or t.get("due") else (prev.get("due") or r["due"]),
               # unit/platform do model điền; cập nhật không có thì giữ giá trị cũ
               "unit": t.get("unit") or (prev or {}).get("unit"), "platform": t.get("platform") or (prev or {}).get("platform"),
               "status": "resolved" if t.get("status_hint") == "resolved_in_chat" else "open",
               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        append(a.state_dir / "ledger.jsonl", rec)
        ledger[key] = {**(prev or {}), **rec}
        # resolved_in_chat vẫn tạo ticket nhưng trạng thái ledger là `resolved` (đã xong trong chat): có trong CSV cho team,
        # KHÔNG đẩy Jira (jira_push chỉ đẩy `open`) và không vào jsm_requests.json
        out.append({**rec, "status": "updated" if prev else "created", "ledger_status": rec["status"],
                    "routed_by": r["routed_by"], "due_by": r["due_by"]})

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if any(o["status"] in ("invalid", "error") for o in out) else 0


if __name__ == "__main__":
    sys.exit(main())
