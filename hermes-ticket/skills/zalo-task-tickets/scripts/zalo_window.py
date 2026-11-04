#!/usr/bin/env python3
"""In ra các tin nhắn Zalo MỚI (sau checkpoint) của một nhóm, kèm ít tin cũ làm ngữ cảnh.

Nguồn: <history-dir>/<group_id>.jsonl, ghi nối theo thứ tự vào DB (zalo-logger `export-hermes`
dùng rowid, nên tin bù đến muộn vẫn nằm SAU checkpoint). Mỗi dòng là một trong:
  tin nhắn : {"message_id","from_id","from_name","text","timestamp"(giây),"type","kind"?,
              "attachment"?: {"name","path"}, "group_name"?, "recalled"?: true}
  sự kiện  : {"type":"recall","message_id"}  |  {"type":"attachment","message_id","attachment":{…}}

Checkpoint = số dòng đã xử lý (không theo thời gian), lưu ở <state>/cursors/<group>.json.
Nhãn người gửi: NV = nhân viên (cs_config.json > staff), KH = khách, HT = hệ thống.
Chế độ nhóm (cs_config.json > groups.<id>.mode): "customer" (mặc định) hoặc "internal".

  zalo_window.py --list-groups
  zalo_window.py --group <group_id>
  zalo_window.py --group <group_id> --advance-to-line N   # sau khi tạo ticket xong
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DEFAULT_HISTORY = Path(os.environ.get("ZALO_HISTORY_DIR", HERMES_HOME / "zalo-history"))
DEFAULT_STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
TZ = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh
WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
CFG: dict = {}


def load_lines(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                out.append({})  # giữ đúng số dòng cho checkpoint
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({})
    return out


def cursor_path(state: Path, group: str) -> Path:
    return state / "cursors" / f"{group}.json"


def read_cursor(state: Path, group: str) -> int:
    p = cursor_path(state, group)
    return int(json.loads(p.read_text(encoding="utf-8")).get("line", 0)) if p.exists() else 0


def write_cursor(state: Path, group: str, line: int) -> None:
    p = cursor_path(state, group)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"line": line}), encoding="utf-8")


def fmt_time(ts: float) -> str:
    d = datetime.fromtimestamp(float(ts), TZ)
    return f"{WEEKDAYS[d.weekday()]} {d:%Y-%m-%d %H:%M}"


def role(uid) -> str:
    if uid in CFG.get("staff", {}):
        return "NV"
    if uid in CFG.get("system_senders", []):
        return "HT"
    return "KH"


def fmt_msg(m: dict, recalled: set, attach: dict) -> str:
    text = (m.get("text") or "").replace("\n", " ⏎ ")
    mid = m.get("message_id", "?")
    if mid in recalled or m.get("recalled"):
        text = f"[ĐÃ THU HỒI] {text}"
    a = attach.get(mid) or m.get("attachment")
    if a:
        text += f"  📎 {a.get('name', '')} ({a.get('path') or 'chưa tải'})"
    return f"[{mid}] {fmt_time(m.get('timestamp', 0))} | {role(m.get('from_id'))} {m.get('from_name') or m.get('from_id')}: {text}"


def fmt_event(e: dict) -> str:
    if e.get("type") == "recall":
        return f"[sự kiện] tin {e.get('message_id')} vừa bị THU HỒI"
    a = e.get("attachment") or {}
    return f"[sự kiện] tin {e.get('message_id')} đã tải xong tệp: {a.get('name', '')} ({a.get('path', '')})"


def is_event(r: dict) -> bool:
    return r.get("type") in ("recall", "attachment") and "from_id" not in r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group")
    ap.add_argument("--history-dir", type=Path, default=DEFAULT_HISTORY)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--config", type=Path, default=DEFAULT_STATE / "cs_config.json")
    ap.add_argument("--context", type=int, default=15, help="số tin cũ đưa kèm làm ngữ cảnh")
    ap.add_argument("--max", type=int, default=300, help="tối đa số dòng mới mỗi lần")
    ap.add_argument("--advance-to-line", type=int)
    ap.add_argument("--list-groups", action="store_true")
    a = ap.parse_args()
    if a.config.exists():
        CFG.update(json.loads(a.config.read_text(encoding="utf-8")))
    groups_cfg = CFG.get("groups", {})

    if a.list_groups:
        for p in sorted(a.history_dir.glob("*.jsonl")):
            g = p.stem
            if groups_cfg.get(g, {}).get("mode") == "ignore":
                continue
            n = len(load_lines(p)) - read_cursor(a.state_dir, g)
            name = groups_cfg.get(g, {}).get("name", "")
            print(f"{g}\t{name}\t{groups_cfg.get(g, {}).get('mode', 'customer')}\t{max(n, 0)} dòng mới")
        return 0
    if not a.group:
        ap.error("cần --group hoặc --list-groups")
    if a.advance_to_line is not None:
        write_cursor(a.state_dir, a.group, a.advance_to_line)
        print(f"OK: checkpoint {a.group} = dòng {a.advance_to_line}")
        return 0

    path = a.history_dir / f"{a.group}.jsonl"
    if not path.exists():
        print(f"Không thấy log: {path}", file=sys.stderr)
        return 1
    rows = load_lines(path)
    cur = read_cursor(a.state_dir, a.group)
    end = min(len(rows), cur + a.max)

    recalled = {r.get("message_id") for r in rows[:end] if r.get("type") == "recall" and is_event(r)}
    attach = {r.get("message_id"): r.get("attachment") for r in rows[:end] if r.get("type") == "attachment" and is_event(r)}
    old = [r for r in rows[:cur] if r and not is_event(r)][-a.context:]
    new = [r for r in rows[cur:end] if r]

    gcfg = groups_cfg.get(a.group, {})
    gname = gcfg.get("name") or next((r.get("group_name") for r in rows if r.get("group_name")), "")
    members = {}
    for r in rows[:end]:
        if r.get("from_id"):
            members.setdefault(r["from_id"], r.get("from_name"))

    print(f"# Nhóm: {a.group} — {gname}")
    print(f"# Chế độ: {gcfg.get('mode', 'customer')} "
          "(customer = yêu cầu của khách → ticket CS; internal = nhóm nội bộ → task nội bộ)")
    print(f"# Bây giờ: {fmt_time(datetime.now(TZ).timestamp())} (giờ Việt Nam)")
    if rows:
        last = next((r for r in reversed(rows) if r.get("timestamp")), None)
        if last:
            print(f"# Tin mới nhất trong log: {fmt_time(last['timestamp'])}")
    print("# Thành viên (NV = nhân viên, KH = khách, HT = hệ thống): "
          + "; ".join(f"{role(k)} {k} = {v}" for k, v in members.items()))
    print()
    print("## NGỮ CẢNH (đã xử lý trước đó — KHÔNG tạo ticket từ phần này)")
    for m in old:
        print(fmt_msg(m, recalled, attach))
    print()
    print(f"## TIN MỚI (dòng {cur + 1}–{end}) — chỉ tạo/cập nhật ticket từ phần này")
    for r in new:
        print(fmt_event(r) if is_event(r) else fmt_msg(r, recalled, attach))
    print()
    print(f"# LAST_LINE={end}" if end > cur else "# Không có tin mới.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
