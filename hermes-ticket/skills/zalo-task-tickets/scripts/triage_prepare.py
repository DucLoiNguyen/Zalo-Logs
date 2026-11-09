#!/usr/bin/env python3
"""Bước chuẩn bị chạy TRƯỚC agent (dùng làm pre-script của cron) để agent bớt phải gọi model.

In một lần: cửa sổ tin mới của MỌI nhóm có dòng mới + danh sách ticket đang mở. Nếu không nhóm nào
có tin mới, dòng cuối là `{"wakeAgent": false}` để Hermes bỏ qua hẳn lượt agent (không tốn model).
Mỗi lượt chỉ lấy tối đa --max dòng mới mỗi nhóm; phần còn lại để lượt sau (checkpoint không nhảy cóc).

  triage_prepare.py [--max 60] [--context 8]
Chỉ dùng thư viện chuẩn. Biến môi trường như zalo_window.py (ZALO_HISTORY_DIR, ZALO_TICKETS_STATE).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(script: str, *args: str) -> str:
    p = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
    return p.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=60, help="tối đa số dòng mới mỗi nhóm trong một lượt")
    ap.add_argument("--context", type=int, default=8, help="số tin cũ đưa kèm làm ngữ cảnh")
    a = ap.parse_args()

    pending = []  # (group_id, tên, số dòng mới)
    for line in run("zalo_window.py", "--list-groups").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            n = int(parts[3].split()[0])
            if n > 0:
                pending.append((parts[0], parts[1], n))

    if not pending:
        print("Không có tin mới.")
        print(json.dumps({"wakeAgent": False}))
        return 0

    print(f"# CÓ TIN MỚI Ở {len(pending)} NHÓM: " + "; ".join(f"{g} ({name}): {n} dòng" for g, name, n in pending))
    print("# Mỗi nhóm bên dưới đã kèm LAST_LINE. Không cần chạy lại zalo_window.py hay --list-open.\n")
    for g, _name, _n in pending:
        print(run("zalo_window.py", "--group", g, "--max", str(a.max), "--context", str(a.context)))
    print("## TICKET ĐANG MỞ (create_tickets.py --list-open)")
    print(run("create_tickets.py", "--list-open"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
