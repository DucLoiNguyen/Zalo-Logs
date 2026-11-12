#!/usr/bin/env python3
"""Dựng state shadow cho triage_llm.py: bản sao state thật tại thời điểm hiện tại, với cursor = cursor thật hiện tại.

  setup_shadow.py [--real DIR] [--shadow DIR] [--reset] [--dry-run]

Sao chép: cs_config.json (đặt jira.enabled = false), ledger.jsonl, tickets.jsonl, cursors/*.json.
KHÔNG sao chép: jira_state.json, export/, memory/, review.jsonl. Ghi baseline_keys.json (các khoá ticket đã có lúc sao chép)
để compare_shadow.py chỉ so ticket MỚI sau thời điểm này. Chỉ ĐỌC state thật. Từ chối ghi đè state shadow đã có (trừ --reset,
chỉ xoá đúng thư mục shadow; từ chối nếu đường dẫn shadow trùng state thật hoặc không kết thúc bằng 'shadow').
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes" / "profiles" / "ticket"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", type=Path, default=HERMES_HOME / "zalo-tickets")
    ap.add_argument("--shadow", type=Path, default=HERMES_HOME / "zalo-tickets-shadow")
    ap.add_argument("--reset", action="store_true", help="xoá state shadow cũ (chỉ thư mục shadow) rồi dựng lại")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    real, shadow = a.real.resolve(), a.shadow.resolve()
    if real == shadow or not shadow.name.endswith("shadow"):
        print(f"TỪ CHỐI: đường dẫn shadow không hợp lệ ({shadow}): phải khác state thật và kết thúc bằng 'shadow'.", file=sys.stderr)
        return 2
    if not (real / "ledger.jsonl").exists():
        print(f"LỖI: không thấy {real / 'ledger.jsonl'}", file=sys.stderr)
        return 1
    if shadow.exists() and any(shadow.iterdir()):
        if not a.reset:
            print(f"TỪ CHỐI: {shadow} đã có dữ liệu (dùng --reset nếu muốn dựng lại).", file=sys.stderr)
            return 1
        if not a.dry_run:
            shutil.rmtree(shadow)

    cursors = {p.stem: json.loads(p.read_text(encoding="utf-8")).get("line", 0) for p in sorted((real / "cursors").glob("*.json"))}
    ledger_keys = sorted({json.loads(l)["key"] for l in (real / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()})
    print(f"{'[dry-run] ' if a.dry_run else ''}Dựng {shadow}: {len(ledger_keys)} ticket trong sổ, {len(cursors)} nhóm có cursor")
    for g, c in cursors.items():
        print(f"  nhóm {g}: cursor = dòng {c}")
    if a.dry_run:
        return 0

    (shadow / "cursors").mkdir(parents=True, exist_ok=True)
    for name in ("ledger.jsonl", "tickets.jsonl"):
        if (real / name).exists():
            shutil.copyfile(real / name, shadow / name)
    for g, c in cursors.items():
        (shadow / "cursors" / f"{g}.json").write_text(json.dumps({"line": c}), encoding="utf-8")
    if (real / "cs_config.json").exists():
        cfg = json.loads((real / "cs_config.json").read_text(encoding="utf-8"))
        cfg.setdefault("jira", {})["enabled"] = False  # shadow không bao giờ đẩy Jira
        (shadow / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (shadow / "baseline_keys.json").write_text(json.dumps(
        {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "keys": ledger_keys, "cursors": cursors},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("Xong. State thật không bị ghi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
