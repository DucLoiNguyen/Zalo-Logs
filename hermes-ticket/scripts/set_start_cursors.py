#!/usr/bin/env python3
"""Đặt checkpoint ban đầu của zalo_window.py để Hermes chỉ tạo ticket từ một mốc thời gian trở đi.

Mỗi nhóm: cursor = chỉ số dòng đầu tiên có `timestamp` >= mốc (nếu chưa có dòng nào ≥ mốc thì = tổng số dòng).
Lịch sử cũ vẫn nằm trong JSONL làm ngữ cảnh nhưng không sinh ticket.
AN TOÀN: nhóm đã có cursor thì BỎ QUA (tránh kéo lùi tiến độ của Hermes), trừ khi có --force.

  set_start_cursors.py --from 2026-11-05            # 00:00 giờ Việt Nam
  set_start_cursors.py --from 2026-11-05 --dry-run
Dùng biến môi trường ZALO_HISTORY_DIR và ZALO_TICKETS_STATE như zalo_window.py. Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HISTORY = Path(os.environ.get("ZALO_HISTORY_DIR", HERMES_HOME / "zalo-history"))
STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
TZ = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD, 00:00 giờ Việt Nam")
    ap.add_argument("--history-dir", type=Path, default=HISTORY)
    ap.add_argument("--state-dir", type=Path, default=STATE)
    ap.add_argument("--force", action="store_true", help="ghi đè cursor đã có")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cutoff = datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=TZ).timestamp()

    for p in sorted(a.history_dir.glob("*.jsonl")):
        rows = []
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line) if line.strip() else {})
                except json.JSONDecodeError:
                    rows.append({})
        first = next((i for i, r in enumerate(rows) if float(r.get("timestamp") or 0) >= cutoff), len(rows))
        cur = a.state_dir / "cursors" / f"{p.stem}.json"
        if cur.exists() and not a.force:
            old = json.loads(cur.read_text(encoding="utf-8")).get("line")
            print(f"{p.stem}: đã có cursor = {old}, bỏ qua (dùng --force để ghi đè)")
            continue
        print(f"{p.stem}: {len(rows)} dòng, cursor -> {first}{'  (dry-run)' if a.dry_run else ''}")
        if not a.dry_run:
            cur.parent.mkdir(parents=True, exist_ok=True)
            cur.write_text(json.dumps({"line": first}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
