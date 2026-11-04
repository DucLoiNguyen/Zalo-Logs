#!/usr/bin/env python3
"""Chuyển log Zalo của bạn (CSV hoặc JSON) sang JSONL chuẩn mà zalo_window.py đọc.

CSV: cần cột thời gian, người gửi, nội dung. Đặt tên cột bằng tham số nếu khác mặc định.
  convert_log.py chat.csv --group team-sales --time-col time --name-col sender --text-col text
JSON: một mảng object, dùng cùng các tham số tên trường.

Thời gian nhận: epoch giây/ms, hoặc ISO ("2026-11-03 09:00", "2026-11-03T09:00:00+07:00");
nếu không có múi giờ thì coi là giờ Việt Nam. Ghi nối vào <history-dir>/<group>.jsonl.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=7))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def parse_ts(v) -> float:
    s = str(v).strip()
    try:
        x = float(s)
        return x / 1000 if x > 1e12 else x
    except ValueError:
        pass
    for fmt in (None, "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            return (d if d.tzinfo else d.replace(tzinfo=TZ)).timestamp()
        except ValueError:
            continue
    raise ValueError(f"không hiểu thời gian: {s}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--group", required=True)
    ap.add_argument("--history-dir", type=Path, default=HERMES_HOME / "zalo-history")
    ap.add_argument("--time-col", default="time")
    ap.add_argument("--name-col", default="sender")
    ap.add_argument("--id-col", default="sender_id")
    ap.add_argument("--text-col", default="text")
    ap.add_argument("--msgid-col", default="message_id")
    a = ap.parse_args()

    if a.src.suffix.lower() == ".csv":
        rows = list(csv.DictReader(a.src.open(encoding="utf-8-sig")))
    else:
        rows = json.loads(a.src.read_text(encoding="utf-8"))

    out = a.history_dir / f"{a.group}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if out.exists():
        seen = {json.loads(l)["message_id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    n = 0
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            ts = parse_ts(r[a.time_col])
            name = r.get(a.name_col, "")
            text = r.get(a.text_col, "")
            mid = r.get(a.msgid_col) or "h" + hashlib.sha1(f"{ts}|{name}|{text}".encode()).hexdigest()[:10]
            if mid in seen:
                continue
            seen.add(mid)
            f.write(json.dumps({"message_id": mid, "from_id": r.get(a.id_col) or name, "from_name": name,
                                "text": text, "timestamp": ts, "type": "text"}, ensure_ascii=False) + "\n")
            n += 1
    print(f"Đã thêm {n} tin vào {out}")


if __name__ == "__main__":
    main()
