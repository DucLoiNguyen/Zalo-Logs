#!/usr/bin/env python3
"""Bật/tắt đẩy ticket lên Jira (khoá jira.enabled trong cs_config.json).

  jira_toggle.py status
  jira_toggle.py off
  jira_toggle.py on [--from-now]   --from-now: đặt lại mốc jira.start = bây giờ, để KHÔNG đẩy dồn ticket tạo trong lúc tắt

Sửa cả bản chạy thật (profile) lẫn bản trong repo; chỉ đổi đúng 2 khoá `enabled`/`start`, giữ nguyên phần còn lại.
Job cron zalo-ticket-jira đọc cấu hình mỗi 5 phút nên có hiệu lực ở lượt kế tiếp, không cần khởi động lại.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FILES = [
    Path("/home/posei/.hermes/profiles/ticket/zalo-tickets/cs_config.json"),
    Path("/mnt/d/zalo-logger/hermes-ticket/config/cs_config.json"),
]


def set_keys(text: str, enabled: bool, start: str | None) -> str:
    """Sửa tại chỗ trong khối "jira": {...} để giữ nguyên định dạng file."""
    m = re.search(r'("jira"\s*:\s*\{)(.*?)(\n?\s*\})', text, re.S)
    if not m:
        raise SystemExit("không thấy khối \"jira\" trong cấu hình")
    body = m.group(2)
    body = re.sub(r'"enabled"\s*:\s*(true|false)', f'"enabled": {str(enabled).lower()}', body, count=1)
    if start:
        body = re.sub(r'"start"\s*:\s*"[^"]*"', f'"start": "{start}"', body, count=1)
    return text[:m.start(2)] + body + text[m.end(2):]


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in ("on", "off", "status"):
        print(__doc__)
        return 2
    for p in FILES:
        if not p.exists():
            print(f"bỏ qua (không có): {p}")
            continue
        text = p.read_text(encoding="utf-8")
        jc = json.loads(text).get("jira", {})
        if cmd == "status":
            print(f"{'BẬT' if jc.get('enabled') else 'TẮT'} | mốc start={jc.get('start')} | {p}")
            continue
        start = None
        if cmd == "on" and "--from-now" in sys.argv:
            start = datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="minutes")
        new = set_keys(text, cmd == "on", start)
        json.loads(new)  # kiểm tra còn là JSON hợp lệ trước khi ghi
        p.write_text(new, encoding="utf-8")
        jc = json.loads(new)["jira"]
        print(f"{'BẬT' if jc['enabled'] else 'TẮT'} | mốc start={jc.get('start')} | {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
