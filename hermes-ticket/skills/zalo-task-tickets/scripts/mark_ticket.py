#!/usr/bin/env python3
"""Đánh dấu ticket trong sổ (thay cho việc sửa tay ledger.jsonl).

  mark_ticket.py <key> not_ours|duplicate|wrong|closed [--note "lý do"]
  mark_ticket.py --auto-close-days [N] [--dry-run] [--list]      # N mặc định 5

- not_ours : yêu cầu của đối thủ (Misa), không phải việc của team.      } KHÔNG bao giờ tự mở lại
- duplicate: trùng một ticket khác.                                    } (create_tickets.py trả skipped_<trạng thái>)
- wrong    : ticket tạo sai (không phải yêu cầu thật, gom sai...).     }
- closed   : đã xong / hết hạn theo dõi. KHÁC các loại trên: khách nhắn tiếp (tin mới trên cùng mỏ neo) thì ticket được MỞ LẠI.
Chỉ nối thêm MỘT dòng vào <state>/ledger.jsonl (không sửa/xoá dòng cũ; bản ghi sau gộp đè bản trước), nên
ticket không còn nằm trong --list-open (closed vẫn hiện, gắn nhãn "(đã đóng)", trong cửa sổ TRIAGE_OPEN_DAYS).
Các dòng này cũng là "ví dụ sửa sai" cho triage_llm.py (trừ closed). Không đụng Jira: task đã đẩy lên Jira phải đóng tay
(hoặc để sync_jira_status.py đọc trạng thái Done từ Jira về).

--auto-close-days N: đóng các ticket `open` mà CẢ tin neo LẪN lần cập nhật cuối đều cũ hơn N ngày.
  --dry-run chỉ in số ticket sẽ đóng theo từng nhóm (--list in thêm từng ticket), không ghi gì.
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_tickets as ct  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
HISTORY = Path(os.environ.get("ZALO_HISTORY_DIR", HERMES_HOME / "zalo-history"))
MARKS = ("not_ours", "duplicate", "wrong", "closed")
DEFAULT_AUTO_CLOSE_DAYS = 5


def mark(state: Path, key: str, status: str, note: str = "") -> dict:
    """Nối một dòng đánh dấu vào ledger.jsonl và trả về dòng đó."""
    rec = {"key": key, "status": status, "note": note.strip(),
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ct.append(state / "ledger.jsonl", rec)
    return rec


def find_auto_close(state: Path, history: Path, days: float, now: datetime | None = None) -> list[dict]:
    """Ticket `open` có tin neo VÀ lần cập nhật cuối cũ hơn `days` ngày. Không tìm thấy tin neo trong log thì chỉ xét lần cập nhật."""
    now = now or datetime.now(ct.TZ)
    cutoff = now - timedelta(days=days)
    stamps: dict[str, dict] = {}
    names: dict[str, str] = {}
    out = []
    for key, r in ct.load_ledger(state).items():
        if r.get("status", "open") != "open":
            continue
        group, anchor_id = key.split(":", 1)
        if group not in stamps:
            stamps[group] = ct.message_stamps(group, history)
        ts = stamps[group].get(anchor_id)
        anchor = datetime.fromtimestamp(ts, ct.TZ) if ts else None
        last = ct._aware(datetime.fromisoformat(r["at"])) if r.get("at") else None
        if (anchor and anchor >= cutoff) or (last and last >= cutoff) or (not anchor and not last):
            continue
        if group not in names:
            names[group] = next((m["group_name"] for m in ct.load_jsonl(history / f"{group}.jsonl") if m.get("group_name")), group)
        out.append({"key": key, "group": group, "group_name": names[group], "title": r.get("title", ""),
                    "anchor_days": round((now - anchor).total_seconds() / 86400, 1) if anchor else None,
                    "idle_days": round((now - last).total_seconds() / 86400, 1) if last else None})
    return sorted(out, key=lambda x: (x["group"], x["key"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", nargs="?", help="khoá ticket dạng group_id:request_message_id")
    ap.add_argument("status", nargs="?", choices=MARKS)
    ap.add_argument("--note", default="", help="lý do ngắn (đưa vào ví dụ sửa sai cho model)")
    ap.add_argument("--auto-close-days", type=float, nargs="?", const=DEFAULT_AUTO_CLOSE_DAYS, default=None, metavar="N")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true", help="với --auto-close-days: in từng ticket")
    ap.add_argument("--state-dir", type=Path, default=STATE)
    ap.add_argument("--history-dir", type=Path, default=HISTORY)
    a = ap.parse_args()

    if a.auto_close_days is not None:
        days = a.auto_close_days
        todo = find_auto_close(a.state_dir, a.history_dir, days)
        by_group: dict[str, int] = {}
        for t in todo:
            by_group[t["group"]] = by_group.get(t["group"], 0) + 1
            if a.list:  # key | nhóm | tiêu đề | tuổi
                print(f"{t['key']} | {t['group_name']} | {t['title'][:80]} | tin neo {t['anchor_days']} ngày, cập nhật cuối {t['idle_days']} ngày")
        verb = "SẼ ĐÓNG" if a.dry_run else "Đã đóng"
        for g, n in sorted(by_group.items()):
            print(f"nhóm {g}: {verb} {n} ticket")
        print(f"{'[dry-run] ' if a.dry_run else ''}Tổng: {len(todo)} ticket mở quá {days:g} ngày không hoạt động")
        if not a.dry_run:
            for t in todo:
                mark(a.state_dir, t["key"], "closed", f"tự đóng sau {days:g} ngày không hoạt động")
        return 0

    if not a.key or not a.status:
        ap.error("cần <key> <status>, hoặc --auto-close-days")
    ledger = a.state_dir / "ledger.jsonl"
    if a.key not in ct.load_ledger(a.state_dir):
        print(f"LỖI: không có khoá '{a.key}' trong {ledger}", file=sys.stderr)
        return 1
    mark(a.state_dir, a.key, a.status, a.note)
    print(f"OK: {a.key} -> {a.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
