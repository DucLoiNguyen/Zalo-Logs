#!/usr/bin/env python3
"""Lấy trạng thái từ Jira về: ticket local đang `open` mà issue Jira tương ứng đã Done thì ghi dòng ledger `closed`.

CHỈ ĐỌC Jira (đúng một loại lời gọi: GET /rest/api/3/search/jql). Không comment, không đổi trạng thái/nhãn/người xử lý,
không đính kèm; áp dụng cho mọi issue trong jira_state.json, kể cả task do người khác (pilot huantd...) tạo.
Một câu JQL cho mỗi ≤ 50 issue: `key in (SUP-1, SUP-2, ...) AND statusCategory = Done`.

  sync_jira_status.py [--dry-run]

Cấu hình như jira_push.py (cs_config.json > jira, JIRA_EMAIL / JIRA_API_TOKEN trong môi trường). Chưa cấu hình thì thoát im lặng (mã 0).
`closed` khác not_ours/duplicate/wrong: khách nhắn tiếp trên cùng mỏ neo thì ticket được mở lại.
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_tickets as ct  # noqa: E402
import jira_push  # noqa: E402
from mark_ticket import mark  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE = Path(os.environ.get("ZALO_TICKETS_STATE", HERMES_HOME / "zalo-tickets"))
CHUNK = 50


def done_issues(jira, issue_keys: list[str]) -> dict[str, str]:
    """issue_key -> tên trạng thái, chỉ các issue có statusCategory = Done. Mỗi lời gọi là một GET."""
    found: dict[str, str] = {}
    for i in range(0, len(issue_keys), CHUNK):
        part = issue_keys[i:i + CHUNK]
        token = None
        while True:
            q = {"jql": f"key in ({', '.join(part)}) AND statusCategory = Done", "fields": "status", "maxResults": 100}
            if token:
                q["nextPageToken"] = token
            code, res = jira.call("GET", "/rest/api/3/search/jql?" + urllib.parse.urlencode(q))
            if code != 200:
                raise RuntimeError(f"Jira trả {code}: {json.dumps(res, ensure_ascii=False)[:200]}")
            for it in res.get("issues", []):
                found[it["key"]] = ((it.get("fields") or {}).get("status") or {}).get("name", "Done")
            token = res.get("nextPageToken")
            if res.get("isLast", True) or not token:
                break
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--state-dir", type=Path, default=STATE)
    a = ap.parse_args()

    cfg_p = a.state_dir / "cs_config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
    jc = cfg.get("jira") or {}
    base = os.environ.get("JIRA_BASE_URL") or jc.get("base_url")
    email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
    if not (base and email and token):
        return 0  # chưa cấu hình: im lặng, dùng được làm job cron --no-agent
    sp = a.state_dir / "jira_state.json"
    state = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}

    ledger = ct.load_ledger(a.state_dir)
    by_issue: dict[str, list[str]] = {}
    for key, s in state.items():
        if ledger.get(key, {}).get("status", "open") == "open" and s.get("issue"):
            by_issue.setdefault(s["issue"], []).append(key)
    if not by_issue:
        print("Không có ticket mở nào đã đẩy/khớp với Jira.")
        return 0
    try:
        done = done_issues(jira_push.Jira(base, email, token), sorted(by_issue))
    except (RuntimeError, OSError) as e:
        print(f"CẢNH BÁO: không lấy được trạng thái Jira: {e}")
        return 0

    per_group: dict[str, int] = {}
    n = 0
    for ik, st in sorted(done.items()):
        for key in by_issue.get(ik, []):
            per_group[key.split(":", 1)[0]] = per_group.get(key.split(":", 1)[0], 0) + 1
            n += 1
            if not a.dry_run:
                mark(a.state_dir, key, "closed", f"Jira {ik} {st}")
    for g, c in sorted(per_group.items()):
        print(f"nhóm {g}: {'SẼ ĐÓNG' if a.dry_run else 'Đã đóng'} {c} ticket (Jira Done)")
    print(f"{'[dry-run] ' if a.dry_run else ''}Tổng: {n} ticket đóng theo Jira, trên {len(by_issue)} issue đã kiểm tra")
    return 0


if __name__ == "__main__":
    sys.exit(main())
