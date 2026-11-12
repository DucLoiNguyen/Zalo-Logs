"""Test shadow mode: setup_shadow.py (dựng state shadow) và compare_shadow.py (so với ticket thật). Không gọi mạng."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT / "skills" / "zalo-task-tickets" / "scripts"))
import create_tickets as ct  # noqa: E402

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=ct.TZ)


def ts(minutes: float) -> float:
    return T0.timestamp() + minutes * 60


def jl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def digest(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


class ShadowBase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name)
        self.real, self.shadow, self.hist = self.tmp / "zalo-tickets", self.tmp / "zalo-tickets-shadow", self.tmp / "history"

    def run_py(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "HERMES_HOME": str(self.tmp / "home"),
                                   "PYTHONDONTWRITEBYTECODE": "1"})


class SetupShadowTests(ShadowBase):
    def seed_real(self):
        jl(self.real / "ledger.jsonl", [{"key": "g1:m1", "status": "open", "title": "a", "seen_ids": ["m1"]},
                                        {"key": "g1:m2", "status": "resolved", "title": "b", "seen_ids": ["m2"]}])
        jl(self.real / "tickets.jsonl", [{"key": "g1:m1", "title": "a", "group_id": "g1"}])
        (self.real / "cursors").mkdir()
        (self.real / "cursors" / "g1.json").write_text(json.dumps({"line": 5}), encoding="utf-8")
        (self.real / "cs_config.json").write_text(json.dumps({"jira": {"enabled": True, "project_key": "SUP"}, "staff": {}}), encoding="utf-8")
        (self.real / "jira_state.json").write_text("{}", encoding="utf-8")
        (self.real / "export").mkdir()
        (self.real / "export" / "jsm_import.csv").write_text("x", encoding="utf-8")

    def test_dung_state_shadow_tu_state_that_khong_ghi_state_that(self):
        self.seed_real()
        before = digest(self.real)
        r = self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.shadow))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(digest(self.real), before)                                    # state thật không bị đụng
        self.assertEqual(json.loads((self.shadow / "cursors" / "g1.json").read_text(encoding="utf-8")), {"line": 5})
        self.assertFalse(json.loads((self.shadow / "cs_config.json").read_text(encoding="utf-8"))["jira"]["enabled"])   # không bao giờ đẩy Jira
        self.assertTrue((self.shadow / "ledger.jsonl").exists() and (self.shadow / "tickets.jsonl").exists())
        for name in ("jira_state.json", "export", "memory", "review.jsonl"):
            self.assertFalse((self.shadow / name).exists(), name)
        base = json.loads((self.shadow / "baseline_keys.json").read_text(encoding="utf-8"))
        self.assertEqual(base["keys"], ["g1:m1", "g1:m2"])

    def test_tu_choi_ghi_de_va_duong_dan_nguy_hiem(self):
        self.seed_real()
        self.assertEqual(self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.shadow)).returncode, 0)
        self.assertEqual(self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.shadow)).returncode, 1)      # đã có dữ liệu
        self.assertEqual(self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.shadow), "--reset").returncode, 0)
        self.assertEqual(self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.real), "--reset").returncode, 2)  # trùng state thật
        self.assertEqual(self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.tmp / "khac"), "--reset").returncode, 2)  # tên không kết thúc 'shadow'
        self.assertTrue((self.real / "ledger.jsonl").exists())

    def test_dry_run_khong_tao_gi(self):
        self.seed_real()
        r = self.run_py("setup_shadow.py", "--real", str(self.real), "--shadow", str(self.shadow), "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.shadow.exists())


class CompareShadowTests(ShadowBase):
    def build(self, state: Path, tickets: list[tuple]) -> None:
        """tickets: (key, customer_id, customer_name, title, category, unit, status)."""
        jl(state / "tickets.jsonl", [{"key": k, "customer_zalo_id": cid, "customer_name": name, "title": t, "category": cat, "unit": unit,
                                      "group_id": k.split(":")[0]} for k, cid, name, t, cat, unit, _ in tickets])
        jl(state / "ledger.jsonl", [{"key": k, "status": st, "title": t, "at": "2026-11-10T03:00:00+00:00", "seen_ids": [k.split(":")[1]]}
                                    for k, _, _, t, _, _, st in tickets])

    def test_ghep_theo_khach_va_thoi_gian_khong_theo_mo_neo(self):
        jl(self.hist / "g1.jsonl", [{"message_id": m, "from_id": "x", "from_name": "x", "text": "t", "timestamp": ts(mins), "type": "text"}
                                    for m, mins in (("r1", 0), ("s1", 3), ("r2", 30), ("s2", 60), ("s3", 90), ("old", -50), ("r4", 120), ("s4", 125))])
        self.build(self.real, [("g1:old", "c9", "Cũ", "đã có từ trước", "question", None, "open"),
                               ("g1:r1", "c1", "Khách A", "[Trường A] Tạo tài khoản: ds", "account", "Trường A", "open"),
                               ("g1:r2", "c2", "Khách B", "[Trường B] Tạo tài khoản: ds", "account", "Trường B", "open"),
                               ("g1:r4", None, "Khách D", "[Trường D] Câu hỏi", "question", "Trường D", "open")])
        self.build(self.shadow, [("g1:old", "c9", "Cũ", "đã có từ trước", "question", None, "open"),
                                 ("g1:s1", "c1", "Khách A", "[Trường A] Tạo tài khoản: danh sách", "account", "Trường A", "resolved"),  # neo khác, lệch 3 phút, khác trạng thái
                                 ("g1:s2", "c2", "Khách B", "[Trường B] Tạo tài khoản: ds", "account", "Trường B", "open"),          # lệch 60 phút: không ghép
                                 ("g1:s3", "c3", "Khách C", "[Trường C] Báo lỗi", "access_issue", "Trường C", "open"),               # chỉ có ở shadow
                                 ("g1:s4", "c4", "Khách D", "[Trường D] Câu hỏi", "question", "Trường D", "open")])                  # khớp theo tên (thật không có id)
        jl(self.shadow / "review.jsonl", [{"key": "g1:zz", "title": "[Cần xem] gì đó", "at": "2026-11-10T04:00:00+00:00"}])
        (self.shadow / "baseline_keys.json").write_text(json.dumps({"created_at": "2026-11-10T00:00:00+00:00", "keys": ["g1:old"]}), encoding="utf-8")
        out = self.tmp / "out"
        r = self.run_py("compare_shadow.py", "--date", "2026-11-10", "--real", str(self.real), "--shadow", str(self.shadow),
                        "--history", str(self.hist), "--out-dir", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = (out / "shadow-compare-2026-11-10.md").read_text(encoding="utf-8")
        self.assertIn("| Thật (agent cũ) | 3 |", text)             # baseline (old) bị loại
        self.assertIn("| Shadow | 4 |", text)
        self.assertIn("| Ghép được | 2 |", text)                    # A (±3 phút, khác mỏ neo) và D (khớp theo tên)
        self.assertIn("| Chỉ có ở thật | 1 |", text)                 # B lệch 60 phút
        self.assertIn("| Chỉ có ở shadow | 2 |", text)              # B' và C
        self.assertIn("Cùng trạng thái (open/resolved/closed): 1/2", text)
        self.assertIn("Cặp ghép được nhưng khác nhau (1)", text)
        self.assertIn("[Cần xem] gì đó", text)
        self.assertEqual(digest(self.real).keys(), {"tickets.jsonl", "ledger.jsonl"})  # không ghi gì vào state thật
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["shadow-compare-2026-11-10.md"])

    def test_ngay_khac_thi_rong_va_thieu_baseline_thi_bao_loi(self):
        self.build(self.real, [("g1:r1", "c1", "A", "t", "q", None, "open")])
        self.build(self.shadow, [])
        r = self.run_py("compare_shadow.py", "--date", "2026-11-10", "--real", str(self.real), "--shadow", str(self.shadow), "--history", str(self.hist), "--out-dir", str(self.tmp))
        self.assertEqual(r.returncode, 1)                           # chưa có baseline_keys.json
        (self.shadow / "baseline_keys.json").write_text(json.dumps({"keys": []}), encoding="utf-8")
        r = self.run_py("compare_shadow.py", "--date", "2026-01-01", "--real", str(self.real), "--shadow", str(self.shadow), "--history", str(self.hist), "--out-dir", str(self.tmp))
        self.assertEqual(r.returncode, 0)
        self.assertIn("thật 0, shadow 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
