#!/usr/bin/env python3
"""Dựng state shadow cho triage_llm.py: bản sao state thật tại thời điểm hiện tại, với cursor = cursor thật hiện tại.

  setup_shadow.py [--real DIR] [--shadow DIR] [--reset] [--dry-run] [--from-now [--history DIR]]

Sao chép: cs_config.json (đặt jira.enabled = false), ledger.jsonl, tickets.jsonl, cursors/*.json.
KHÔNG sao chép: jira_state.json, export/, memory/, review.jsonl. Ghi baseline_keys.json (các khoá ticket đã có lúc sao chép)
để compare_shadow.py chỉ so ticket MỚI sau thời điểm này. Chỉ ĐỌC state thật. Từ chối ghi đè state shadow đã có (trừ --reset,
chỉ xoá đúng thư mục shadow; từ chối nếu đường dẫn shadow trùng state thật hoặc không kết thúc bằng 'shadow').

--from-now: thay vì lấy cursor thật, đặt cursor shadow của MỌI nhóm có lịch sử nhảy tới CUỐI file (bỏ qua phần tin tồn, shadow chỉ xử lý tin đến từ
giờ). Dùng khi nhóm nào tồn nhiều dòng (shadow sẽ mất nhiều lượt và token để đuổi kịp). Ghi thêm vào baseline_keys.json mục "from_now"
{nhóm: giờ tin cuối lúc dựng} để compare_shadow.py bỏ qua ticket thật có tin neo trước mốc đó (shadow không thấy được các tin đó).
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
    ap.add_argument("--from-now", action="store_true", help="cursor shadow nhảy tới cuối file lịch sử của mọi nhóm (bỏ tin tồn)")
    ap.add_argument("--history", type=Path, default=Path(os.environ.get("ZALO_HISTORY_DIR", "/mnt/d/zalo-logger/data/hermes-history")))
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
    real_cursors = dict(cursors)
    from_now: dict[str, float] = {}
    if a.from_now:
        for p in sorted(a.history.glob("*.jsonl")):
            lines = p.read_text(encoding="utf-8").splitlines()
            cursors[p.stem] = len(lines)
            last = 0.0
            for l in reversed(lines):   # giờ của tin thật cuối cùng (bỏ dòng sự kiện không có timestamp)
                try:
                    last = float(json.loads(l).get("timestamp") or 0)
                except (ValueError, TypeError):
                    last = 0.0
                if last:
                    break
            from_now[p.stem] = last
    print(f"{'[dry-run] ' if a.dry_run else ''}Dựng {shadow}: {len(ledger_keys)} ticket trong sổ, {len(cursors)} nhóm có cursor"
          + (" (--from-now: nhảy tới cuối file)" if a.from_now else ""))
    for g, c in cursors.items():
        extra = f" (cursor thật {real_cursors.get(g, 0)}, bỏ qua {c - real_cursors.get(g, 0)} dòng tồn)" if a.from_now else ""
        print(f"  nhóm {g}: cursor = dòng {c}{extra}")
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
        {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "keys": ledger_keys, "cursors": cursors, **({"from_now": from_now} if a.from_now else {})},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("Xong. State thật không bị ghi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
