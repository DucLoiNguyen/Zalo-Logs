"""Test cho triage_llm.py, triage_memory.py, mark_ticket.py và các thêm mới của create_tickets.py.

Không gọi mạng ngoài: model giả là http.server cục bộ. Không đụng state thật: mọi thứ nằm trong thư mục tạm.
Chạy: python -m unittest discover hermes-ticket/tests
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "zalo-task-tickets" / "scripts"
SAMPLE_CFG = ROOT / "sample" / "cs_config.json"
sys.path.insert(0, str(SCRIPTS))
import create_tickets as ct  # noqa: E402
import triage_llm as tl  # noqa: E402
import triage_memory as tm  # noqa: E402


class FakeLLM:
    """Server OpenAI-compatible giả: trả lần lượt các phản hồi đã nạp; ghi lại mọi request."""

    def __init__(self) -> None:
        self.responses: list[tuple] = []  # (status, content, finish_reason)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                status, content, finish = outer.responses.pop(0) if outer.responses else (500, "", None)
                if status != 200:
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                   "usage": {"prompt_tokens": 100, "completion_tokens": 10}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):  # im lặng
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def reply(self, obj, status: int = 200, finish: str | None = "stop") -> None:
        self.responses.append((status, obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), finish))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def task(**kw) -> dict:
    t = {"request_message_id": "m1", "group_id": "g1", "title": "[Trường A] Tạo tài khoản: Danh sách đăng ký",
         "description": "Khách gửi danh sách đăng ký tài khoản.", "category": "account", "unit": "Trường A",
         "platform": "Clex", "customer_name": "Khách A", "customer_zalo_id": "c_01", "source_message_ids": ["m1"],
         "quote": "nhờ tạo tài khoản", "confidence": 0.9, "status_hint": "open", "kind": "cs", "priority": None}
    t.update(kw)
    return t


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.hist = self.tmp / "history"
        self.state = self.tmp / "zalo-tickets"
        self.hist.mkdir()
        self.state.mkdir()
        (self.state / "cs_config.json").write_text(SAMPLE_CFG.read_text(encoding="utf-8"), encoding="utf-8")
        self.llm = FakeLLM()
        self.addCleanup(self.llm.close)
        self.env = {"HERMES_HOME": str(self.tmp), "ZALO_HISTORY_DIR": str(self.hist), "ZALO_TICKETS_STATE": str(self.state),
                    "TRIAGE_LLM_BASE_URL": self.llm.url, "TRIAGE_LLM_MODEL": "test-model", "TRIAGE_GAP": "0",
                    "TRIAGE_TIMEOUT": "20", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                    "TRIAGE_COVERAGE": "0", "TRIAGE_STEPS": "1"}  # các test cũ kiểm tra luồng cơ bản; kiểm tra phủ/2 bước có lớp test riêng

    # ---- dữ liệu
    def history(self, group: str, rows: list[dict] | None = None) -> None:
        now = time.time()
        rows = rows or [
            {"message_id": "m1", "from_id": "c_01", "from_name": "Khách A", "text": "Nhờ tạo tài khoản cho trường A", "timestamp": now - 600, "type": "text"},
            {"message_id": "m2", "from_id": "c_01", "from_name": "Khách A", "text": "Cảm ơn", "timestamp": now - 300, "type": "text"},
        ]
        (self.hist / f"{group}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    def cursor(self, group: str) -> int:
        p = self.state / "cursors" / f"{group}.json"
        return json.loads(p.read_text(encoding="utf-8"))["line"] if p.exists() else 0

    def ledger(self) -> dict:
        return ct.load_ledger(self.state)

    def run_main(self, *args: str) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.env), mock.patch.object(sys, "argv", ["triage_llm.py", *args]), \
                contextlib.redirect_stdout(buf):
            rc = tl.main()
        return rc, buf.getvalue()

    def script(self, name: str, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, **self.env, **(extra_env or {})}
        return subprocess.run([sys.executable, str(SCRIPTS / name), *args], capture_output=True, text=True, encoding="utf-8", env=env)


class TriageFlowTests(Base):
    def test_khong_co_nhom_nao_thi_khong_goi_model(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn("Không có tin mới.", out)
        self.assertEqual(self.llm.requests, [])

    def test_json_hop_le_tao_ticket_va_doi_cursor(self):
        self.history("g1")
        self.llm.reply({"tasks": [task()], "notes_update": ""})
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(self.cursor("g1"), 2)
        rec = self.ledger()["g1:m1"]
        self.assertEqual(rec["status"], "open")
        self.assertEqual((rec["unit"], rec["platform"]), ("Trường A", "Clex"))  # create_tickets lưu unit/platform
        self.assertIn("1 lần gọi", out)

    def test_mang_rong_khi_co_tin_can_xu_ly_va_chua_kiem_tra_phu_thi_khong_doi_cursor(self):
        self.history("g1")                                      # có tin "Nhờ tạo tài khoản…" (giống yêu cầu), kiểm tra phủ tắt
        self.llm.reply({"tasks": [], "notes_update": ""})
        rc, out = self.run_main()
        self.assertEqual(self.cursor("g1"), 0)
        self.assertEqual(self.ledger(), {})
        self.assertIn("không dời cursor", out)

    def test_mang_rong_khi_khong_co_tin_can_xu_ly_van_doi_cursor(self):
        now = time.time()
        self.history("g1", [{"message_id": "m1", "from_id": "c_01", "from_name": "Khách A", "text": "Cảm ơn anh chị", "timestamp": now - 600, "type": "text"}])
        self.llm.reply({"tasks": [], "notes_update": ""})
        self.run_main()
        self.assertEqual(self.cursor("g1"), 1)
        self.assertEqual(self.ledger(), {})

    def test_gui_dung_model_va_prompt_co_cua_so_tin(self):
        self.history("g1")
        self.llm.reply({"tasks": []})
        self.run_main()
        req = self.llm.requests[0]
        self.assertEqual(req["model"], "test-model")
        self.assertEqual(req["temperature"], 0)
        self.assertFalse(req["stream"])
        self.assertEqual(req["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in req["messages"]], ["system", "user"])
        self.assertIn("## TIN MỚI", req["messages"][1]["content"])
        self.assertIn("- account:", req["messages"][0]["content"])  # danh sách LOẠI lấy từ cs_config

    def test_gỡ_hàng_rào_markdown(self):
        self.history("g1")
        self.llm.reply("```json\n" + json.dumps({"tasks": [task()], "notes_update": ""}) + "\n```")
        self.run_main()
        self.assertIn("g1:m1", self.ledger())

    def test_json_sai_lan_1_dung_lan_2(self):
        self.history("g1")
        self.llm.reply("đây không phải JSON")
        self.llm.reply({"tasks": [task()], "notes_update": ""})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 2)
        self.assertEqual(self.cursor("g1"), 2)
        self.assertIn("lỗi", self.llm.requests[1]["messages"][-1]["content"])  # lần 2 kèm danh sách lỗi

    def test_json_sai_2_lan_khong_doi_cursor_nhom_khac_van_chay(self):
        self.history("g1")
        self.history("g2")
        self.llm.reply("hỏng")
        self.llm.reply("hỏng nữa")
        self.llm.reply({"tasks": [task(group_id="g2", request_message_id="m1")], "notes_update": ""})
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.llm.requests), 3)
        self.assertEqual(self.cursor("g1"), 0)
        self.assertEqual(self.cursor("g2"), 2)
        self.assertIn("BỎ QUA", out)

    def test_request_message_id_khong_co_trong_tin_moi_la_loi(self):
        self.history("g1")
        bad = {"tasks": [task(request_message_id="zzz", source_message_ids=["zzz"])], "notes_update": ""}
        self.llm.reply(bad)
        self.llm.reply(bad)
        self.run_main()
        self.assertEqual(self.cursor("g1"), 0)
        self.assertIn("zzz", self.llm.requests[1]["messages"][-1]["content"])
        self.assertEqual(self.ledger(), {})

    def test_cap_nhat_dung_mo_neo_cua_ticket_dang_mo(self):
        self.history("g1")
        self.llm.reply({"tasks": [task()], "notes_update": ""})
        self.run_main()
        rows = [{"message_id": "m3", "from_id": "c_01", "from_name": "Khách A", "text": "sao chưa ai xử lý", "timestamp": time.time(), "type": "text"}]
        with (self.hist / "g1.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rows[0], ensure_ascii=False) + "\n")
        # mỏ neo m1 không nằm trong TIN MỚI nhưng là ticket đang mở -> hợp lệ; nguồn m3 nằm trong TIN MỚI
        self.llm.reply({"tasks": [task(source_message_ids=["m3"], description="Khách nhắc lại")], "notes_update": ""})
        self.run_main()
        self.assertEqual(self.cursor("g1"), 3)
        self.assertIn("m3", self.ledger()["g1:m1"]["seen_ids"])
        self.assertIn("g1:m1 |", self.llm.requests[1]["messages"][1]["content"])  # ticket đang mở có trong prompt

    def test_http_429_dung_luot_khong_doi_cursor(self):
        self.history("g1")
        self.history("g2")
        self.llm.reply("", status=429)
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.llm.requests), 1)  # không thử lại, không sang nhóm g2
        self.assertEqual((self.cursor("g1"), self.cursor("g2")), (0, 0))
        self.assertIn("CẢNH BÁO", out)

    def _many_lines(self, n: int) -> None:
        now = time.time()
        self.history("g1", [{"message_id": f"m{i}", "from_id": "c_01", "from_name": "Khách A", "text": f"yêu cầu số {i}",
                             "timestamp": now - 1000 + i, "type": "text"} for i in range(1, n + 1)])

    def test_phan_hoi_bi_cat_thi_thu_nho_cua_so_roi_hoi_lai(self):
        self._many_lines(8)
        self.env["TRIAGE_MAX_LINES"] = "8"
        self.llm.reply('{"tasks": [{"request_message_id": "m1", "tit', finish="length")  # JSON dở dang vì hết token
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m2"])], "notes_update": ""})
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.llm.requests), 2)  # không phí lần "sửa JSON" cho phản hồi bị cắt
        self.assertIn("## TIN MỚI (dòng 1–8)", self.llm.requests[0]["messages"][1]["content"])
        self.assertIn("## TIN MỚI (dòng 1–4)", self.llm.requests[1]["messages"][1]["content"])  # thu nhỏ một nửa
        self.assertEqual(self.cursor("g1"), 4)  # chỉ dời tới hết phần đã xử lý; 4 dòng còn lại để lượt sau
        self.assertIn("thu nhỏ cửa sổ", out)

    def test_bi_cat_mai_thi_bo_qua_khong_doi_cursor(self):
        self._many_lines(8)
        self.env["TRIAGE_MAX_LINES"] = "8"
        for _ in range(3):
            self.llm.reply("{", finish="length")
        self.run_main()
        self.assertEqual(len(self.llm.requests), 3)  # 8 -> 4 -> 2 dòng rồi bỏ cuộc
        self.assertEqual(self.cursor("g1"), 0)

    def test_debug_dir_ghi_phan_hoi_tho(self):
        self.history("g1")
        self.env["TRIAGE_DEBUG_DIR"] = str(self.tmp / "dbg")
        self.llm.reply({"tasks": [], "notes_update": ""})
        self.run_main()
        files = list((self.tmp / "dbg").glob("g1_*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("finish_reason=stop", files[0].read_text(encoding="utf-8"))

    def test_khong_in_khoa_api(self):
        self.history("g1")
        self.env["TRIAGE_LLM_API_KEY"] = "sk-bi-mat-123"
        self.llm.reply({"tasks": []})
        _, out = self.run_main()
        self.assertNotIn("sk-bi-mat-123", out)

    def test_dry_run_khong_tao_gi_va_ghi_so_tay_vao_state_dry(self):
        self.history("g1")
        self.llm.reply({"tasks": [task()], "notes_update": "- trường A hay gửi danh sách"})
        rc, out = self.run_main("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.cursor("g1"), 0)
        self.assertEqual(self.ledger(), {})
        self.assertNotIn("trường A hay gửi danh sách", out)  # không in sổ tay ra màn hình
        dry = self.tmp / "zalo-tickets-dry" / "memory" / "notes" / "g1.md"
        self.assertTrue(dry.exists())
        self.assertFalse((self.state / "memory").exists())  # trí nhớ thật không bị đụng


class MemoryTests(Base):
    def test_so_tay_ghi_ngay_va_cat_1500_ky_tu(self):
        self.history("g1", [{"message_id": "m1", "from_id": "c_01", "from_name": "Khách A", "text": "Cảm ơn anh chị", "timestamp": time.time() - 600, "type": "text"}])
        self.llm.reply({"tasks": [], "notes_update": "- đơn vị X hay hỏi mật khẩu"})
        self.run_main()
        text = (self.state / "memory" / "notes" / "g1.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"- \[\d{4}-\d{2}-\d{2}\] đơn vị X hay hỏi mật khẩu")
        out = ""
        for i in range(60):
            out = tm.merge_notes(out, f"dòng ghi chú số {i} " + "x" * 80, "2026-11-10")
        self.assertLessEqual(len(out), tm.NOTES_MAX_CHARS)
        self.assertIn("số 59", out)       # dòng mới giữ lại
        self.assertNotIn("số 0 ", out)    # dòng cũ nhất bị bỏ

    def test_so_tay_mac_dinh_khong_vao_prompt_nhung_van_ghi(self):
        mem = self.tmp / "mem"
        self.assertTrue(tm.save_notes(mem, "g1", "- Nhân viên A vừa hoàn thành danh sách Hoa Mai", "2026-11-12"))     # vẫn ghi
        with mock.patch.dict(os.environ, {"TRIAGE_NOTES_READ": "0"}):
            self.assertNotIn("SỔ TAY", tm.build_memory(mem, self.state, {}, "g1", "", save=False)[0])
        env = {k: v for k, v in os.environ.items() if k != "TRIAGE_NOTES_READ"}
        with mock.patch.dict(os.environ, env, clear=True):                                                      # mặc định = 0
            self.assertNotIn("SỔ TAY", tm.build_memory(mem, self.state, {}, "g1", "", save=False)[0])
        with mock.patch.dict(os.environ, {"TRIAGE_NOTES_READ": "1"}):
            self.assertIn("SỔ TAY", tm.build_memory(mem, self.state, {}, "g1", "", save=False)[0])

    def test_prompt_so_tay_cam_ghi_trang_thai_viec_cu_the(self):
        for name in ("triage_system.md", "triage_internal.md"):
            text = (SCRIPTS.parent / "prompts" / name).read_text(encoding="utf-8")
            self.assertIn("CẤM ghi trạng thái xử lý việc cụ thể", text)
            self.assertIn("vấn đề lặp lại và thói quen", text)

    def test_flow_khong_dua_so_tay_vao_prompt_mac_dinh(self):
        self.history("g1")
        tm.save_notes(self.state / "memory", "g1", "- ghi chú cũ về nhóm", "2026-11-10")
        self.llm.reply({"tasks": [task()], "notes_update": "- vấn đề lặp lại mới"})
        self.run_main()
        self.assertNotIn("ghi chú cũ về nhóm", self.llm.requests[0]["messages"][1]["content"])
        self.assertIn("vấn đề lặp lại mới", (self.state / "memory" / "notes" / "g1.md").read_text(encoding="utf-8"))   # vẫn ghi sổ tay

    def test_so_tay_chi_ghi_khi_khac_va_khong_rong(self):
        mem = self.tmp / "mem"
        self.assertFalse(tm.save_notes(mem, "g1", "   ", "2026-11-10"))
        self.assertFalse((mem / "notes").exists())
        self.assertTrue(tm.save_notes(mem, "g1", "- việc A", "2026-11-10"))
        p = mem / "notes" / "g1.md"
        before = p.read_text(encoding="utf-8")
        self.assertFalse(tm.save_notes(mem, "g1", "- việc A", "2026-11-11"))  # trùng nội dung cũ (khác ngày) thì không ghi
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_danh_ba_dung_lan_dau_lay_don_vi_tu_tieu_de(self):
        ct.append(self.state / "tickets.jsonl", {"key": "g1:m1", "title": "[THPT Chế Lan Viên] Tạo tài khoản: DS", "customer_name": "Cô Hoa", "customer_zalo_id": "c_09"})
        ct.append(self.state / "tickets.jsonl", {"key": "g1:m1", "update": "bổ sung"})
        ct.append(self.state / "ledger.jsonl", {"key": "g1:m1", "status": "open", "at": "2026-11-05T01:00:00+00:00", "seen_ids": ["m1"]})
        sd = tm.build_senders(self.state)
        self.assertEqual(sd["c_09"]["unit"], "THPT Chế Lan Viên")
        self.assertEqual(sd["c_09"]["name"], "Cô Hoa")
        self.assertEqual(sd["c_09"]["last_seen"], "2026-11-05")

    def test_danh_ba_cap_nhat_sau_ticket_created(self):
        self.history("g1")
        self.llm.reply({"tasks": [task(customer_zalo_id="c_01", unit="Trường A")], "notes_update": ""})
        self.run_main()
        sd = json.loads((self.state / "memory" / "senders.json").read_text(encoding="utf-8"))
        self.assertEqual(sd["c_01"]["unit"], "Trường A")
        self.assertEqual(sd["c_01"]["name"], "Khách A")

    def test_danh_sach_nguoi_gui_danh_dau_misa_va_bo_nv(self):
        window = "# Thành viên (NV = nhân viên, KH = khách, HT = hệ thống): NV u_200 = Lợi; KH c_02 = Luật Việt Nam Misa; KH c_01 = Khách A\n"
        lines = tm.sender_lines({"c_01": {"unit": "Trường A", "last_seen": "2026-11-09"}}, window, {"staff": {"u_200": {}}})
        text = "\n".join(lines)
        self.assertIn("nhân viên Misa", text)
        self.assertIn("đơn vị: Trường A", text)
        self.assertNotIn("Lợi", text)

    def test_vi_du_sua_sai_tu_ledger_va_mark_ticket(self):
        for i, st in enumerate(["not_ours", "not_ours", "open"]):
            ct.append(self.state / "ledger.jsonl", {"key": f"g1:m{i}", "title": f"[Đơn vị {i}] Tạo tài khoản", "status": st, "at": f"2026-11-0{i + 1}T00:00:00+00:00", "seen_ids": []})
        ct.append(self.state / "ledger.jsonl", {"key": "g2:mx", "title": "[Trùng] Câu hỏi", "status": "open", "at": "2026-11-09T00:00:00+00:00", "seen_ids": []})
        r = self.script("mark_ticket.py", "g2:mx", "duplicate", "--note", "trùng ticket SUP-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = tm.example_lines(self.state, "g1")
        self.assertEqual(len(lines), 3)  # 2 not_ours của g1 + 1 mượn từ nhóm khác (g2)
        self.assertTrue(any("Misa" in l for l in lines))
        self.assertTrue(any("trùng ticket SUP-1" in l for l in lines))
        self.assertFalse(any("Đơn vị 2" in l for l in lines))  # ticket còn open không phải ví dụ

    def test_gioi_han_token_cat_danh_ba_truoc(self):
        for i in range(12):
            ct.append(self.state / "tickets.jsonl", {"key": f"g1:m{i}", "title": f"[Đơn vị số {i}] Tạo tài khoản", "customer_name": f"Khách {i}", "customer_zalo_id": f"c_{i}"})
        members = "; ".join(f"KH c_{i} = Khách {i}" for i in range(12))
        window = f"# Thành viên (...): {members}\n"
        with mock.patch.object(tm, "MEMORY_MAX_TOKENS", 60):
            text, st = tm.build_memory(self.tmp / "mem", self.state, {}, "g1", window)
        self.assertLessEqual(st["tokens"], 60)
        self.assertLess(st["senders"], 12)


class TicketToolsTests(Base):
    def _seed(self, ages_hours: list[float], due_future: bool = False, group: str = "g1") -> None:
        now = time.time()
        rows = [{"message_id": f"m{i}", "from_id": "c_01", "from_name": "K", "text": "x", "timestamp": now - h * 3600, "type": "text"}
                for i, h in enumerate(ages_hours)]
        self.history(group, rows)
        for i, h in enumerate(ages_hours):
            due = (datetime.now(ct.TZ) + timedelta(hours=5 if due_future else -1)).isoformat(timespec="minutes")
            ct.append(self.state / "ledger.jsonl", {"key": f"{group}:m{i}", "title": f"ticket {i}", "customer": "K", "status": "open",
                                                   "at": "2026-01-01T00:00:00+00:00", "due": due, "seen_ids": [f"m{i}"]})

    def test_list_open_khong_tham_so_van_la_json_cu(self):
        self._seed([1, 2])
        r = self.script("create_tickets.py", "--list-open")
        data = json.loads(r.stdout)
        self.assertEqual({d["key"] for d in data}, {"g1:m0", "g1:m1"})
        self.assertTrue(all(d["overdue"] for d in data))

    def test_compact_5_cot_moi_nhat_truoc_va_loc_theo_nhom(self):
        self._seed([5, 1, 3])
        ct.append(self.state / "ledger.jsonl", {"key": "g2:mz", "title": "nhóm khác", "status": "open", "at": "2026-01-01T00:00:00+00:00", "seen_ids": []})
        r = self.script("create_tickets.py", "--list-open", "--group", "g1", "--compact")
        lines = r.stdout.strip().splitlines()
        self.assertEqual([l.split(" | ")[0] for l in lines], ["g1:m1", "g1:m2", "g1:m0"])  # tuổi 1h, 3h, 5h
        self.assertTrue(all(len(l.split(" | ")) == 5 for l in lines))
        self.assertIn("nhóm g1: 3/3", r.stderr)

    def test_compact_gioi_han_so_dong(self):
        self._seed([h / 2 + 1 for h in range(30)])
        r = self.script("create_tickets.py", "--list-open", "--group", "g1", "--compact", extra_env={"TRIAGE_OPEN_MAX": "20"})
        self.assertEqual(len(r.stdout.strip().splitlines()), 20)
        self.assertIn("20/30", r.stderr)

    def test_compact_bo_ticket_cu_qua_han_giu_ticket_cu_chua_qua_han(self):
        self._seed([2, 24 * 10, 24 * 12])  # 1 mới, 2 cũ đều quá hạn
        r = self.script("create_tickets.py", "--list-open", "--group", "g1", "--compact")
        self.assertEqual([l.split(" | ")[0] for l in r.stdout.strip().splitlines()], ["g1:m0"])
        self.assertIn("1/3", r.stderr)
        # cùng dữ liệu nhưng hạn còn ở tương lai -> không bị loại dù tin neo cũ
        for f in (self.state / "ledger.jsonl",):
            f.unlink()
        self._seed([2, 24 * 10, 24 * 12], due_future=True)
        r = self.script("create_tickets.py", "--list-open", "--group", "g1", "--compact")
        self.assertEqual(len(r.stdout.strip().splitlines()), 3)

    def test_mark_ticket_va_khong_mo_lai(self):
        self._seed([1])
        bad = self.script("mark_ticket.py", "g1:khong-co", "wrong")
        self.assertEqual(bad.returncode, 1)
        ok = self.script("mark_ticket.py", "g1:m0", "wrong", "--note", "không phải yêu cầu")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.ledger()["g1:m0"]["status"], "wrong")
        self.assertEqual(self.ledger()["g1:m0"]["title"], "ticket 0")  # bản ghi sau gộp, không mất tiêu đề
        self.assertEqual(self.script("create_tickets.py", "--list-open", "--compact").stdout.strip(), "")
        f = self.tmp / "t.json"
        f.write_text(json.dumps([task(request_message_id="m0", source_message_ids=["m0", "m9"])]), encoding="utf-8")
        out = json.loads(self.script("create_tickets.py", str(f), "--dry-run").stdout)
        self.assertEqual(out[0]["status"], "skipped_wrong")

    def test_ledger_giu_unit_cu_khi_cap_nhat_khong_co_unit(self):
        f = self.tmp / "t.json"
        f.write_text(json.dumps([task()]), encoding="utf-8")
        self.script("create_tickets.py", str(f))
        f.write_text(json.dumps([task(unit=None, platform=None, source_message_ids=["m1", "m2"])]), encoding="utf-8")
        self.script("create_tickets.py", str(f))
        self.assertEqual(self.ledger()["g1:m1"]["unit"], "Trường A")


class FakeJira:
    """Jira giả: ghi lại mọi (method, path); tìm theo `key in (...)` trả về các issue nằm trong `done`."""

    def __init__(self, done: set[str] | None = None) -> None:
        self.requests: list[tuple[str, str]] = []
        self.done = done or set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                outer.requests.append((self.command, self.path))
                body: dict = {"name": "SUP"}
                if "/search/jql" in self.path:
                    import urllib.parse as up
                    jql = up.parse_qs(up.urlparse(self.path).query).get("jql", [""])[0]
                    body = {"issues": [{"key": k, "fields": {"status": {"name": "Done"}}} for k in sorted(outer.done) if k in jql],
                            "isLast": True}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _serve  # noqa: N815

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ClosedStatusTests(Base):
    def _create(self, *tasks: dict) -> None:
        f = self.tmp / "tasks.json"
        f.write_text(json.dumps(list(tasks), ensure_ascii=False), encoding="utf-8")
        r = self.script("create_tickets.py", str(f))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def _jira_cfg(self, jira: FakeJira) -> dict:
        cfg = json.loads((self.state / "cs_config.json").read_text(encoding="utf-8"))
        cfg["jira"] = {"enabled": True, "mode": "task", "project_key": "SUP", "base_url": jira.url}
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return {"JIRA_EMAIL": "t@example.com", "JIRA_API_TOKEN": "khong-that"}

    def test_closed_duoc_mo_lai_khi_khach_nhan_tiep_con_wrong_thi_khong(self):
        self._create(task())
        self.script("mark_ticket.py", "g1:m1", "closed", "--note", "xong")
        f = self.tmp / "t2.json"
        f.write_text(json.dumps([task(source_message_ids=["m1"])]), encoding="utf-8")
        self.assertEqual(json.loads(self.script("create_tickets.py", str(f), "--dry-run").stdout)[0]["status"], "duplicate")  # chưa có tin mới
        f.write_text(json.dumps([task(source_message_ids=["m2"])]), encoding="utf-8")
        self.assertEqual(json.loads(self.script("create_tickets.py", str(f)).stdout)[0]["status"], "updated")
        self.assertEqual(self.ledger()["g1:m1"]["status"], "open")  # mở lại
        self.script("mark_ticket.py", "g1:m1", "wrong")
        f.write_text(json.dumps([task(source_message_ids=["m3"])]), encoding="utf-8")
        self.assertEqual(json.loads(self.script("create_tickets.py", str(f)).stdout)[0]["status"], "skipped_wrong")

    def test_auto_close_dry_run_roi_dong_that(self):
        now = time.time()
        self.history("g1", [{"message_id": m, "from_id": "c_01", "from_name": "K", "text": "x", "timestamp": now - d * 86400, "type": "text"}
                            for m, d in (("mA", 10), ("mB", 10), ("mC", 1))])
        old, recent = "2026-01-01T00:00:00+00:00", datetime.now(ct.TZ).isoformat()
        for k, at in (("mA", old), ("mB", recent), ("mC", recent)):  # mB: tin neo cũ nhưng mới được cập nhật
            ct.append(self.state / "ledger.jsonl", {"key": f"g1:{k}", "title": f"ticket {k}", "status": "open", "at": at, "seen_ids": [k]})
        dry = self.script("mark_ticket.py", "--auto-close-days", "7", "--dry-run", "--list")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("nhóm g1: SẼ ĐÓNG 1 ticket", dry.stdout)
        self.assertIn("g1:mA", dry.stdout)
        self.assertNotIn("g1:mB", dry.stdout)
        self.assertEqual({k: v["status"] for k, v in self.ledger().items()}, {"g1:mA": "open", "g1:mB": "open", "g1:mC": "open"})  # dry-run không ghi
        self.assertRegex(dry.stdout, r"g1:mA \| .+ \| ticket mA \| tin neo 10")  # key | nhóm | tiêu đề | tuổi
        self.script("mark_ticket.py", "--auto-close-days")  # mặc định 5 ngày
        led = self.ledger()
        self.assertEqual((led["g1:mA"]["status"], led["g1:mB"]["status"], led["g1:mC"]["status"]), ("closed", "open", "open"))
        self.assertIn("5", led["g1:mA"]["note"])

    def test_compact_mac_dinh_7_ngay_va_hien_ticket_da_dong(self):
        now = time.time()
        self.history("g1", [{"message_id": m, "from_id": "c_01", "from_name": "K", "text": "x", "timestamp": now - d * 86400, "type": "text"}
                            for m, d in (("m5", 5), ("m9", 9))])
        for k in ("m5", "m9"):
            ct.append(self.state / "ledger.jsonl", {"key": f"g1:{k}", "title": f"ticket {k}", "status": "open", "at": "2026-01-01T00:00:00+00:00", "seen_ids": [k]})
        self.script("mark_ticket.py", "g1:m5", "closed")
        out = self.script("create_tickets.py", "--list-open", "--group", "g1", "--compact").stdout.strip().splitlines()
        self.assertEqual(len(out), 1)  # m9 (9 ngày) bị loại, m5 (5 ngày) còn
        self.assertIn("(đã đóng) ticket m5", out[0])

    def test_export_jsm_giu_dong_closed_va_bo_wrong(self):
        self._create(task(request_message_id="m1", title="Mở"), task(request_message_id="m2", title="Đã đóng"),
                     task(request_message_id="m3", title="Sai"))
        self.script("mark_ticket.py", "g1:m2", "closed")
        self.script("mark_ticket.py", "g1:m3", "wrong")
        r = self.script("export_jsm.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        import csv
        with (self.state / "export" / "jsm_import.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = {x["Summary"]: x["Status"] for x in csv.DictReader(f)}
        self.assertEqual(rows, {"Mở": "Open", "Đã đóng": "Closed"})  # closed còn trong CSV (có cột trạng thái), wrong thì đã loại
        payloads = json.loads((self.state / "export" / "jsm_requests.json").read_text(encoding="utf-8"))
        self.assertEqual([p["_zalo_key"] for p in payloads], ["g1:m1"])

    def test_resolved_in_chat_la_ticket_resolved_co_trong_csv_khong_day_jira_va_mo_lai_khi_khach_nhac(self):
        jira = FakeJira()
        self.addCleanup(jira.close)
        creds = self._jira_cfg(jira)
        self.history("g1")
        self._create(task(request_message_id="m1", title="Ticket còn mở"),
                     task(request_message_id="m2", title="Ticket xong sẵn", status_hint="resolved_in_chat", source_message_ids=["m2"]))
        self.assertEqual(self.ledger()["g1:m2"]["status"], "resolved")   # vẫn tạo ticket, trạng thái resolved
        self.assertEqual(self.ledger()["g1:m1"]["status"], "open")
        import csv
        self.assertEqual(self.script("export_jsm.py").returncode, 0)
        with (self.state / "export" / "jsm_import.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = {x["Summary"]: x["Status"] for x in csv.DictReader(f)}
        self.assertEqual(rows, {"Ticket còn mở": "Open", "Ticket xong sẵn": "Đã xong trong chat"})   # có trong CSV, có cột trạng thái
        payloads = json.loads((self.state / "export" / "jsm_requests.json").read_text(encoding="utf-8"))
        self.assertEqual([p["_zalo_key"] for p in payloads], ["g1:m1"])                              # không vào jsm_requests.json
        push = self.script("jira_push.py", "--dry-run", extra_env=creds)
        self.assertIn("Ticket còn mở", push.stdout)
        self.assertNotIn("Ticket xong sẵn", push.stdout)                                               # KHÔNG đẩy Jira
        self.assertEqual({d["key"] for d in json.loads(self.script("create_tickets.py", "--list-open").stdout)}, {"g1:m1"})
        compact = self.script("create_tickets.py", "--list-open", "--group", "g1", "--compact").stdout
        self.assertIn("(đã xong trong chat) Ticket xong sẵn", compact)                              # compact vẫn hiện để khách nhắc lại thì mở lại được
        f = self.tmp / "t.json"
        f.write_text(json.dumps([task(request_message_id="m2", source_message_ids=["m3"], status_hint="open", description="khách nhắc lại")]), encoding="utf-8")
        self.assertEqual(json.loads(self.script("create_tickets.py", str(f)).stdout)[0]["status"], "updated")
        self.assertEqual(self.ledger()["g1:m2"]["status"], "open")                                   # mở lại khi khách nhắn tiếp

    def test_jira_push_khong_day_ticket_closed(self):
        jira = FakeJira()
        self.addCleanup(jira.close)
        creds = self._jira_cfg(jira)
        self.history("g1")
        self._create(task(request_message_id="m1", title="Ticket còn mở"), task(request_message_id="m2", title="Ticket đã đóng", source_message_ids=["m2"]))
        self.script("mark_ticket.py", "g1:m2", "closed")
        r = self.script("jira_push.py", "--dry-run", extra_env=creds)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ticket còn mở", r.stdout)
        self.assertNotIn("Ticket đã đóng", r.stdout)

    def test_sync_jira_status_chi_doc_va_dong_ticket_done(self):
        jira = FakeJira(done={"SUP-1"})
        self.addCleanup(jira.close)
        creds = self._jira_cfg(jira)
        for k in ("m1", "m2"):
            ct.append(self.state / "ledger.jsonl", {"key": f"g1:{k}", "title": f"ticket {k}", "status": "open", "at": "2026-11-01T00:00:00+00:00", "seen_ids": [k]})
        (self.state / "jira_state.json").write_text(json.dumps({
            "g1:m1": {"issue": "SUP-1", "comments": 0, "existing": True, "at": "2026-11-01T00:00:00+07:00"},   # task của người khác
            "g1:m2": {"issue": "SUP-2", "comments": 0, "at": "2026-11-01T00:00:00+07:00"}}), encoding="utf-8")
        dry = self.script("sync_jira_status.py", "--dry-run", extra_env=creds)
        self.assertIn("SẼ ĐÓNG 1", dry.stdout)
        self.assertEqual(self.ledger()["g1:m1"]["status"], "open")  # dry-run không ghi
        real = self.script("sync_jira_status.py", extra_env=creds)
        self.assertEqual(real.returncode, 0, real.stdout + real.stderr)
        led = self.ledger()
        self.assertEqual((led["g1:m1"]["status"], led["g1:m2"]["status"]), ("closed", "open"))
        self.assertEqual(led["g1:m1"]["note"], "Jira SUP-1 Done")
        self.assertTrue(jira.requests)
        self.assertEqual({m for m, _ in jira.requests}, {"GET"})                     # chỉ đọc: không POST/PUT/DELETE
        self.assertTrue(all("/rest/api/3/search/jql" in p for _, p in jira.requests))  # chỉ tìm kiếm, không đụng issue nào
        self.assertNotIn("khong-that", dry.stdout + real.stdout + real.stderr)


if __name__ == "__main__":
    unittest.main()
