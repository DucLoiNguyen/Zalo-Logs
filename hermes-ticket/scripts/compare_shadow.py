#!/usr/bin/env python3
"""So ticket của state shadow (triage_llm.py) với ticket thật (agent cũ) theo ngày, xuất data/shadow-compare-<ngày>.md.

  compare_shadow.py [--date YYYY-MM-DD|today] [--real DIR] [--shadow DIR] [--history DIR] [--out-dir DIR] [--window-min 10]

Chỉ so ticket MỚI sau lúc dựng shadow (khoá không có trong shadow/baseline_keys.json) có tin neo thuộc ngày chọn (giờ Việt Nam).
Ghép theo KHÁCH + THỜI GIAN, không theo mỏ neo (hai bên có thể neo vào tin chữ hay tin tệp): cùng nhóm, cùng khách (id Zalo hoặc
tên) và giờ tin neo lệch ≤ --window-min phút, ghép cặp gần nhất. Chỉ đọc cả hai state và lịch sử; chỉ ghi file báo cáo.
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "zalo-task-tickets" / "scripts"))
import create_tickets as ct  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes" / "profiles" / "ticket"))
HISTORY = Path(os.environ.get("ZALO_HISTORY_DIR", "/mnt/d/zalo-logger/data/hermes-history"))


def mask(s: str | None) -> str:
    s = re.sub(r"[\w.+-]+@[\w.-]+", "<email>", s or "")
    return re.sub(r"(?<!\d)(\+?84|0)\d{8,10}(?!\d)", "<sdt>", s).replace("|", "/")


def load(state: Path, baseline: set[str], history: Path, day) -> list[dict]:
    """Ticket mới (không thuộc baseline) có tin neo trong ngày `day`: key, group, when, customer keys, title, category, unit, status."""
    led = ct.load_ledger(state)
    stamps: dict[str, dict] = {}
    out = []
    for r in ct.load_jsonl(state / "tickets.jsonl"):
        key = r.get("key")
        if "update" in r or not key or key in baseline or key not in led:
            continue
        group, anchor = key.split(":", 1)
        if group not in stamps:
            stamps[group] = ct.message_stamps(group, history)
        ts = stamps[group].get(anchor)
        rec = led[key]
        when = datetime.fromtimestamp(ts, ct.TZ) if ts else (ct._aware(datetime.fromisoformat(rec["at"])) if rec.get("at") else None)
        if when is None or when.date() != day:
            continue
        cust = {str(r["customer_zalo_id"])} if r.get("customer_zalo_id") else set()
        if r.get("customer_name"):
            cust.add(str(r["customer_name"]).casefold().strip())
        out.append({"key": key, "group": group, "when": when, "cust": cust, "name": r.get("customer_name") or "", "title": r.get("title") or "",
                    "category": r.get("category"), "unit": r.get("unit") or rec.get("unit"), "status": rec.get("status", "open")})
    return sorted(out, key=lambda x: x["when"])


def match(real: list[dict], shadow: list[dict], minutes: float) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Ghép greedy: với mỗi ticket thật (theo thời gian) lấy ticket shadow chưa ghép cùng nhóm + cùng khách, gần giờ nhất trong ±minutes."""
    free = list(shadow)
    pairs, only_real = [], []
    for r in real:
        cands = [s for s in free if s["group"] == r["group"] and (s["cust"] & r["cust"])
                 and abs((s["when"] - r["when"]).total_seconds()) <= minutes * 60]
        if cands:
            best = min(cands, key=lambda s: abs((s["when"] - r["when"]).total_seconds()))
            free.remove(best)
            pairs.append((r, best))
        else:
            only_real.append(r)
    return pairs, only_real, free


def same(a, b) -> bool:
    return str(a or "").casefold().strip() == str(b or "").casefold().strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="today")
    ap.add_argument("--real", type=Path, default=HERMES_HOME / "zalo-tickets")
    ap.add_argument("--shadow", type=Path, default=HERMES_HOME / "zalo-tickets-shadow")
    ap.add_argument("--history", type=Path, default=HISTORY)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--window-min", type=float, default=10)
    a = ap.parse_args()

    day = datetime.now(ct.TZ).date() if a.date == "today" else datetime.strptime(a.date, "%Y-%m-%d").date()
    base_p = a.shadow / "baseline_keys.json"
    if not base_p.exists():
        print(f"LỖI: không có {base_p} (chạy setup_shadow.py trước).", file=sys.stderr)
        return 1
    base = json.loads(base_p.read_text(encoding="utf-8"))
    baseline = set(base["keys"])
    real, shadow = load(a.real, baseline, a.history, day), load(a.shadow, baseline, a.history, day)
    pairs, only_real, only_shadow = match(real, shadow, a.window_min)
    review = [r for r in ct.load_jsonl(a.shadow / "review.jsonl") if str(r.get("at", ""))[:10] == str(day)]

    n = len(pairs)
    pct = lambda k: f"{k}/{n}" + (f" = {k / n:.0%}" if n else "")  # noqa: E731
    cat = sum(same(r["category"], s["category"]) for r, s in pairs)
    unit = sum(same(r["unit"], s["unit"]) for r, s in pairs if r["unit"] and s["unit"])
    unit_den = sum(1 for r, s in pairs if r["unit"] and s["unit"])
    st = sum(r["status"] == s["status"] for r, s in pairs)
    L = [f"# So sánh shadow và ticket thật ngày {day}", "",
         f"Shadow dựng lúc {base.get('created_at')} (cursor thật lúc đó). Chỉ so ticket mới, tin neo trong ngày {day} (giờ VN). "
         f"Ghép theo nhóm + khách + giờ tin neo lệch ≤ {a.window_min:g} phút.", "",
         "| | Số ticket |", "|---|---|", f"| Thật (agent cũ) | {len(real)} |", f"| Shadow | {len(shadow)} |", f"| Ghép được | {n} |",
         f"| Chỉ có ở thật | {len(only_real)} |", f"| Chỉ có ở shadow | {len(only_shadow)} |", f"| Shadow: tin cần người xem (`review.jsonl`) | {len(review)} |", "",
         "## Đồng ý trên các cặp ghép được", "", f"- Cùng loại (category): {pct(cat)}",
         f"- Cùng trạng thái (open/resolved/closed): {pct(st)}",
         f"- Cùng đơn vị (khi cả hai có): {unit}/{unit_den}", ""]
    row = lambda t: f"| {t['when']:%H:%M} | …{t['group'][-4:]} | {mask(t['name'])} | {t['status']} | {mask(t['title'])[:90]} |"  # noqa: E731
    hdr = ["| Giờ | Nhóm | Khách | Trạng thái | Tiêu đề |", "|---|---|---|---|---|"]
    L += [f"## Chỉ có ở thật ({len(only_real)}): shadow bỏ sót?", ""] + (hdr + [row(t) for t in only_real] if only_real else ["(không có)"]) + [""]
    L += [f"## Chỉ có ở shadow ({len(only_shadow)}): ticket thừa hoặc agent cũ bỏ sót?", ""] + (hdr + [row(t) for t in only_shadow] if only_shadow else ["(không có)"]) + [""]
    diff = [(r, s) for r, s in pairs if not same(r["category"], s["category"]) or r["status"] != s["status"]
            or (r["unit"] and s["unit"] and not same(r["unit"], s["unit"]))]
    L += [f"## Cặp ghép được nhưng khác nhau ({len(diff)})", ""]
    if diff:
        L += ["| Giờ | Khách | Thật: loại / trạng thái / đơn vị | Shadow: loại / trạng thái / đơn vị |", "|---|---|---|---|"]
        L += [f"| {r['when']:%H:%M} | {mask(r['name'])} | {r['category']} / {r['status']} / {mask(r['unit'])} | {s['category']} / {s['status']} / {mask(s['unit'])} |"
              for r, s in diff]
    else:
        L.append("(không có)")
    if review:
        L += ["", f"## Shadow: cần người xem ({len(review)})", ""] + [f"- {mask(r.get('title'))}" for r in review]
    out_dir = a.out_dir or a.history.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"shadow-compare-{day}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Đã ghi {out}: thật {len(real)}, shadow {len(shadow)}, ghép {n}, chỉ thật {len(only_real)}, chỉ shadow {len(only_shadow)}, cần xem {len(review)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
