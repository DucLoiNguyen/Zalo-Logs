#!/usr/bin/env python3
"""Một lệnh thay cho chuỗi: dry-run -> tạo/cập nhật ticket -> export JSM -> dời checkpoint.

  apply_group.py <group_id> <file_json> <LAST_LINE>

An toàn: nếu dry-run có mục `invalid`/`error` thì dừng, KHÔNG tạo gì và KHÔNG dời checkpoint (in lý do để agent
sửa file JSON rồi chạy lại). Tạo xong mà có lỗi thì cũng không dời checkpoint. Chạy lại sau lỗi là an toàn
vì create_tickets.py chống trùng theo khoá. Mã thoát 0 = đã xong và đã dời checkpoint.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(script: str, *args: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True, encoding="utf-8")
    return p.returncode, (p.stdout + p.stderr).strip()


def statuses(text: str) -> list[dict]:
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    group, file, last = sys.argv[1], sys.argv[2], sys.argv[3]
    if not last.isdigit():
        print(f"LAST_LINE không hợp lệ: {last}")
        return 2

    code, out = run("create_tickets.py", file, "--dry-run")
    bad = [s for s in statuses(out) if s.get("status") in ("invalid", "error")]
    if code != 0 or bad:
        print("DỪNG: dry-run có lỗi, chưa tạo gì và chưa dời checkpoint. Sửa file JSON rồi chạy lại.")
        print(out)
        return 1

    code, out = run("create_tickets.py", file)
    res = statuses(out)
    if code != 0 or any(s.get("status") in ("invalid", "error") for s in res):
        print("DỪNG: tạo ticket bị lỗi, KHÔNG dời checkpoint (chạy lại an toàn, không trùng).")
        print(out)
        return 1

    code, out2 = run("export_jsm.py")
    if code != 0:
        print("DỪNG: export_jsm.py lỗi, KHÔNG dời checkpoint.")
        print(out2)
        return 1

    code, out3 = run("zalo_window.py", "--group", group, "--advance-to-line", last)
    if code != 0:
        print(out3)
        return 1

    count = {}
    for s in res:
        count[s.get("status")] = count.get(s.get("status"), 0) + 1
    print(f"XONG nhóm {group}: {json.dumps(count, ensure_ascii=False)}; checkpoint = {last}")
    for s in res:
        if s.get("status") in ("created", "updated"):
            tag = s["status"] + (" (đã xong trong chat)" if s.get("ledger_status") == "resolved" else "")
            print(f"- {tag}: {s.get('title')} | {s.get('assignee')} | hạn {s.get('due')}")
        elif s.get("status") == "low_confidence":
            print(f"- low_confidence (cần người xem): {s.get('title')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
