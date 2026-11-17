#!/usr/bin/env python3
"""Xuất ticket thô (backend local) sang định dạng sẵn sàng cho Jira Service Management.

Đọc  ~/.hermes/zalo-tickets/tickets.jsonl  (+ ledger.jsonl để biết trạng thái)
Ghi  ~/.hermes/zalo-tickets/export/
       jsm_import.csv     → nhập bằng Jira "Import issues from CSV" (mở bằng Excel để xem/test)
       jsm_requests.json  → payload cho POST /rest/servicedeskapi/request (khi đã có quyền)

Ánh xạ:
  category → Request Type : cs_config.json > categories.<cat>.jsm_request_type (mặc định = label)
  priority → Jira Priority: urgent=Highest, high=High, medium=Medium, low=Low
  assignee → staff.<id>.jira (email/accountId)
Cập nhật từ Zalo (khách nhắn thêm) → các cột Comment.

  export_jsm.py [--out DIR] [--service-desk-id 1] [--include-resolved]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
TZ = timezone(timedelta(hours=7))
PRIO = {"urgent": "Highest", "high": "High", "medium": "Medium", "low": "Low"}


def jsonl(p: Path) -> list[dict]:
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []
    for r in rows:  # model đôi khi lưu message_id dạng số nguyên
        if isinstance(r.get("source_message_ids"), list):
            r["source_message_ids"] = [str(i) for i in r["source_message_ids"]]
    return rows


def jira_date(s: str | None) -> str:
    if not s:
        return ""
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=TZ)).astimezone(TZ).strftime("%Y-%m-%d %H:%M")


REVIEW_COLS = ["Thời gian", "Nhóm", "Mã tin", "Trích", "Lý do"]


def export_review(state: Path, out: Path, cfg: dict) -> int:
    """review.jsonl ([Cần xem] + tin model không chắc) -> review.csv cho người xem. Không có review.jsonl thì không ghi gì. Trả số dòng."""
    rows = jsonl(state / "review.jsonl")
    if not rows:
        return 0
    import create_tickets as ct  # cùng thư mục; dùng lại message_stamps để lấy giờ của chính tin đó
    stamps: dict[str, dict] = {}
    groups = cfg.get("groups", {})
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLS)
        for r in sorted(rows, key=lambda x: x.get("at", ""), reverse=True):
            group, _, mid = str(r.get("key", "")).partition(":")
            if group not in stamps:
                stamps[group] = ct.message_stamps(group, Path(os.environ.get("ZALO_HISTORY_DIR", "/mnt/d/zalo-logger/data/hermes-history")))
            ts = stamps[group].get(mid)
            when = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M") if ts else jira_date(r.get("at"))
            quote = " ".join(str(r.get("quote") or r.get("title") or "").split())
            reason = r.get("reason") or ("Model không phân loại được sau nhiều lần hỏi" if str(r.get("title", "")).startswith("[Cần xem]")
                                         else f"Model không chắc đây là yêu cầu (độ tin cậy {r.get('confidence')})")
            w.writerow([when, groups.get(group, {}).get("name") or f"…{group[-4:]}", mid, quote[:200], reason])
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, default=STATE)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--service-desk-id", default="<SERVICE_DESK_ID>")
    ap.add_argument("--include-resolved", action="store_true")
    a = ap.parse_args()
    out = a.out or a.state_dir / "export"
    out.mkdir(parents=True, exist_ok=True)

    cfg_p = a.state_dir / "cs_config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
    staff, cats = cfg.get("staff", {}), cfg.get("categories", {})
    status = {r["key"]: r.get("status", "open") for r in jsonl(a.state_dir / "ledger.jsonl")}

    tickets: dict[str, dict] = {}
    comments: dict[str, list[str]] = {}
    for r in jsonl(a.state_dir / "tickets.jsonl"):
        if "update" in r:
            comments.setdefault(r["key"], []).append(r["update"])
        else:
            tickets[r["key"]] = r

    rows, payloads = [], []
    for key, t in tickets.items():
        st = status.get(key, "open")
        # `closed` (tự đóng / Jira Done) và `resolved` (đã xong trong chat) vẫn nằm trong CSV cho team, có cột Status,
        # không được mất dòng âm thầm. not_ours / duplicate / wrong (do người dùng loại) ra khỏi CSV, trừ khi --include-resolved.
        if st not in ("open", "closed", "resolved") and not a.include_resolved:
            continue
        cat = cats.get(t.get("category") or "", {})
        rtype = cat.get("jsm_request_type") or cat.get("label") or t.get("category") or ""
        assignee = staff.get(t.get("assignee_zalo_id") or "", {}).get("jira", "")
        desc = "\n".join([
            t["description"], "",
            f"Khách hàng: {t.get('customer_name') or ''} (Zalo {t.get('customer_zalo_id') or '?'})",
            f"Trích dẫn: \"{t.get('quote', '')}\"",
            f"Nguồn: Zalo {t['group_id']}, tin {', '.join(map(str, t.get('source_message_ids') or []))}",
            f"zalo-ticket-key: {key}",
        ])
        labels = ["zalo", t.get("category") or "khac"] + (["noi-bo"] if t.get("kind") == "internal" else [])
        files = "; ".join(f"{x.get('name')} | {x.get('path') or 'chưa tải'}" for x in t.get("attachments") or [])
        rows.append({
            "Summary": t["title"], "Description": desc,
            "Issue Type": "Task" if t.get("kind") == "internal" else "Service Request",
            "Request Type": rtype, "Priority": PRIO.get(t.get("priority") or "medium", "Medium"),
            "Assignee": assignee, "Due Date": jira_date(t.get("due")),
            "Labels": " ".join(labels), "Customer": t.get("customer_name") or "",
            "Attachment": files, "Zalo Key": key, "Status": {"resolved": "Đã xong trong chat", "closed": "Closed"}.get(st, "Open"),
            "_comments": comments.get(key, []),
        })
        if st != "open" and not a.include_resolved:
            continue  # payload tạo request JSM chỉ cho ticket đang mở
        payloads.append({
            "_zalo_key": key, "_assignee": assignee, "_comments": comments.get(key, []),
            "_request_type_name": rtype, "_attachments": t.get("attachments") or [],
            "serviceDeskId": a.service_desk_id,
            "requestTypeId": f"<id của request type '{rtype}'>",
            "requestFieldValues": {
                "summary": t["title"], "description": desc,
                **({"duedate": t["due"][:10]} if t.get("due") else {}),
                "priority": {"name": PRIO.get(t.get("priority") or "medium", "Medium")},
                "labels": labels,
            },
        })

    max_c = max((len(r["_comments"]) for r in rows), default=0)
    base_cols = ["Summary", "Description", "Issue Type", "Request Type", "Priority", "Assignee",
                 "Due Date", "Labels", "Customer", "Attachment", "Zalo Key", "Status"]
    with (out / "jsm_import.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(base_cols + ["Comment"] * max_c)  # Jira CSV import cho phép lặp cột Comment
        for r in rows:
            w.writerow([r[c] for c in base_cols] + r["_comments"] + [""] * (max_c - len(r["_comments"])))
    (out / "jsm_requests.json").write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã xuất {len(rows)} ticket → {out}/jsm_import.csv và jsm_requests.json")
    n_review = export_review(a.state_dir, out, cfg)
    if n_review:
        print(f"Đã xuất {n_review} mục cần xem → {out}/review.csv")


if __name__ == "__main__":
    main()
