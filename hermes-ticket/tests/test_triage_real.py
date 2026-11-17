"""Test wrapper triage-llm-real.sh (cron chính thức trên state thật): khoá chống chạy chồng, đợi khách nhắn xong 2 phút, mã thoát 2 của export,
từ chối khi Jira bật, đúng state thật. Dùng skill giả + export giả, không gọi mạng. Chỉ chạy trên Linux/WSL (cần bash + flock)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "triage-llm-real.sh"
LINUX = sys.platform != "win32" and shutil.which("flock") is not None


class SeLfLF(unittest.TestCase):
    def test_script_luon_dung_LF(self):
        self.assertNotIn(b"\r\n", SCRIPT.read_bytes())


@unittest.skipUnless(LINUX, "cần bash + flock (WSL/Linux)")
class WrapperTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.home = Path(self._t.name) / "home"
        self.state = self.home / "zalo-tickets"
        self.hist = Path(self._t.name) / "history"
        (self.state / "cursors").mkdir(parents=True)
        self.hist.mkdir()
        (self.state / "cs_config.json").write_text(json.dumps({"jira": {"enabled": False}}), encoding="utf-8")
        skill = Path(self._t.name) / "skill" / "scripts"
        skill.mkdir(parents=True)
        self.marker = Path(self._t.name) / "marker.json"
        (skill / "triage_llm.py").write_text(
            "import json, os, sys, time\n"
            f"time.sleep(float(os.environ.get('STUB_SLEEP', '0')))\n"
            f"json.dump({{'argv': sys.argv[1:], 'state': os.environ.get('ZALO_TICKETS_STATE'), 'max_lines': os.environ.get('TRIAGE_MAX_LINES')}}, open(r'{self.marker}', 'w'))\n"
            "print('Triage xong: stub')\n", encoding="utf-8")
        self.export_called = Path(self._t.name) / "export_called"
        exp = Path(self._t.name) / "export.sh"
        exp.write_text(f"#!/usr/bin/env bash\ntouch {self.export_called}\necho export-stub\nexit ${{STUB_EXPORT_RC:-0}}\n", encoding="utf-8")
        exp.chmod(0o755)
        self.env = {**os.environ, "HERMES_HOME": str(self.home), "ZALO_HISTORY_DIR": str(self.hist), "TRIAGE_SKILL_DIR": str(Path(self._t.name) / "skill"),
                    "TRIAGE_EXPORT_SCRIPT": str(exp), "PYTHONIOENCODING": "utf-8"}
        self.env.pop("ZALO_TICKETS_STATE", None)

    def history(self, last_ts: float, cursor: int = 0, n: int = 3) -> None:
        rows = [{"message_id": f"m{i}", "from_id": "c", "from_name": "K", "text": "x", "timestamp": last_ts - (n - 1 - i), "type": "text"} for i in range(n)]
        rows.append({"event": "recall", "message_id": "m0"})   # dòng cuối là sự kiện không có timestamp: phải bỏ qua
        (self.hist / "g1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        (self.state / "cursors" / "g1.json").write_text(json.dumps({"line": cursor}), encoding="utf-8")

    def run_w(self, *args, **extra):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", env={**self.env, **{k: str(v) for k, v in extra.items()}})

    def test_chay_tren_state_that_va_chuyen_tham_so(self):
        self.history(time.time() - 3600)
        r = self.run_w("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(m["argv"], ["--dry-run"])
        self.assertEqual(m["state"], str(self.home / "zalo-tickets"))          # state THẬT, không phải shadow
        self.assertTrue(m["state"].endswith("/zalo-tickets") and "shadow" not in m["state"])
        self.assertEqual(m["max_lines"], "40")
        self.assertTrue(self.export_called.exists(), "phải export trước khi triage")

    def test_khoa_chan_chay_chong_thoat_em_va_ghi_log(self):
        self.history(time.time() - 3600)
        holder = subprocess.Popen(["flock", "-x", str(self.state / ".triage-real.lock"), "sleep", "6"])
        self.addCleanup(lambda: (holder.kill(), holder.wait()))
        time.sleep(0.5)
        r = self.run_w()
        self.assertEqual(r.returncode, 0)
        self.assertIn("Lượt trước còn chạy", r.stdout)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.export_called.exists(), "bị chặn thì không export")

    def test_khach_vua_nhan_trong_2_phut_thi_bo_luot(self):
        self.history(time.time() - 30)
        r = self.run_w()
        self.assertEqual(r.returncode, 0)
        self.assertIn("Có tin mới trong 120 giây", r.stdout)
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.export_called.exists(), "kiểm tra yên lặng làm SAU export")

    def skips(self):
        f = self.state / ".triage-real-quiet-skips"
        return f.read_text(encoding="utf-8").strip() if f.exists() else None

    def test_bo_toi_da_2_luot_lien_tiep_luot_3_van_chay_va_dat_lai(self):
        self.history(time.time() - 30)                                         # nhóm nhắn liên tục: luôn chưa yên lặng
        r1 = self.run_w()
        self.assertEqual((r1.returncode, self.skips(), self.marker.exists()), (0, "1", False))
        self.assertIn("bỏ lượt này (liên tiếp 1/2)", r1.stdout)
        self.assertNotIn("WARN", r1.stdout)                                    # mới bỏ 1 lượt: chưa cảnh báo
        self.history(time.time() - 30)
        r2 = self.run_w()
        self.assertEqual((r2.returncode, self.skips(), self.marker.exists()), (0, "2", False))
        self.assertIn("bỏ lượt này (liên tiếp 2/2)", r2.stdout)
        self.assertIn("WARN: đã bỏ 2 lượt liên tiếp", r2.stdout)               # bỏ lượt thứ 2 liên tiếp: một dòng WARN
        self.assertEqual(r2.stdout.count("WARN"), 1)
        self.history(time.time() - 30)
        r3 = self.run_w()
        self.assertEqual(r3.returncode, 0, r3.stdout)
        self.assertTrue(self.marker.exists(), "lượt thứ 3 phải chạy dù chưa yên lặng")
        self.assertIn("chạy lượt này dù nhóm chưa yên lặng", r3.stdout)
        self.assertEqual(self.skips(), "0")                                    # đặt lại sau lượt thật

    def test_dat_lai_ve_0_sau_luot_that_chay_khi_da_yen_lang(self):
        (self.state / ".triage-real-quiet-skips").write_text("1\n", encoding="utf-8")
        self.history(time.time() - 600)
        self.assertEqual(self.run_w().returncode, 0)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.skips(), "0")

    def test_khong_co_tin_cho_thi_khong_dem_va_dat_lai(self):
        self.history(time.time() - 5, cursor=4)                                # tin rất mới nhưng cursor đã ở cuối: không có tin chờ
        self.assertEqual(self.run_w().returncode, 0)
        self.assertEqual(self.skips(), "0")                                    # không tăng
        (self.state / ".triage-real-quiet-skips").write_text("2\n", encoding="utf-8")
        self.marker.unlink()
        self.assertEqual(self.run_w().returncode, 0)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.skips(), "0")                                    # hết tin chờ => đặt lại

    def test_dry_run_tay_khong_lam_doi_bo_dem(self):
        self.history(time.time() - 30)
        r = self.run_w("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("bỏ lượt này (liên tiếp 1/2)", r.stdout)                 # vẫn báo, nhưng không ghi bộ đếm
        self.assertIsNone(self.skips())
        (self.state / ".triage-real-quiet-skips").write_text("2\n", encoding="utf-8")
        self.assertEqual(self.run_w("--dry-run").returncode, 0)
        self.assertEqual(self.skips(), "2", "dry-run không đặt lại bộ đếm")

    def test_tep_dem_chi_chua_so_nguyen_khong_co_noi_dung_tin(self):
        self.history(time.time() - 30)
        self.run_w()
        self.assertRegex(self.skips() or "", r"^\d+$")

    def test_nguong_so_luot_bo_chinh_duoc(self):
        self.history(time.time() - 30)
        r = self.run_w(TRIAGE_MAX_QUIET_SKIPS=0)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.marker.exists(), "MAX=0: không bao giờ bỏ lượt vì yên lặng")

    def test_tin_cu_hon_2_phut_thi_chay(self):
        self.history(time.time() - 150)
        self.assertFalse(self.run_w().returncode)
        self.assertTrue(self.marker.exists())

    def test_khong_co_tin_cho_xu_ly_thi_tin_moi_khong_chan(self):
        self.history(time.time() - 5, cursor=4)                                # cursor đã ở cuối file (3 tin + 1 sự kiện)
        self.assertFalse(self.run_w().returncode)
        self.assertTrue(self.marker.exists())

    def test_nguong_yen_lang_chinh_duoc(self):
        self.history(time.time() - 30)
        self.run_w(TRIAGE_QUIET_SECONDS=10)
        self.assertTrue(self.marker.exists())

    def test_export_thoat_ma_2_thi_bo_luot_va_bao(self):
        self.history(time.time() - 3600)
        r = self.run_w(STUB_EXPORT_RC=2)
        self.assertEqual(r.returncode, 1)
        self.assertIn("export-hermes thoát mã 2", r.stdout)
        self.assertFalse(self.marker.exists())

    def test_export_loi_khac_chi_canh_bao_va_van_chay(self):
        self.history(time.time() - 3600)
        r = self.run_w(STUB_EXPORT_RC=1)
        self.assertEqual(r.returncode, 0)
        self.assertIn("CẢNH BÁO: export-hermes lỗi (mã 1)", r.stdout)
        self.assertTrue(self.marker.exists())

    def test_jira_bat_van_chay_binh_thuong_khong_con_chot_chan(self):
        self.history(time.time() - 3600)
        (self.state / "cs_config.json").write_text(json.dumps({"jira": {"enabled": True}}), encoding="utf-8")
        r = self.run_w()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("jira.enabled", r.stdout)
        self.assertTrue(self.marker.exists() and self.export_called.exists())

    def test_qua_thoi_han_thi_dung_va_nha_khoa(self):
        self.history(time.time() - 3600)
        r = self.run_w(STUB_SLEEP=5, TRIAGE_MAX_RUN_SECONDS=1)
        self.assertEqual(r.returncode, 124)
        self.assertIn("quá 1 giây", r.stdout)
        self.assertEqual(self.run_w().returncode, 0)                           # khoá đã nhả: lượt sau chạy được


if __name__ == "__main__":
    unittest.main()
