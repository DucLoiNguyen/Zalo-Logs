"""Test kiểm tra phủ (triage_cover.py), luồng 2 bước và chuỗi dự phòng của triage_llm.py. Không gọi mạng ngoài."""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

from test_triage_llm import Base, SAMPLE_CFG, task, ct, tl  # noqa: F401  (Base dựng thư mục tạm + model giả)

import triage_cover as cv  # noqa: E402


def msg(mid, who="KH", name="Khách A", text="x", new=True, minute=0):
    from datetime import datetime, timedelta
    return {"id": mid, "role": who, "name": name, "text": text, "new": new, "when": datetime(2026, 11, 10, 9, 0) + timedelta(minutes=minute)}


class CoverUnitTests(unittest.TestCase):
    def test_nhan_dien_tin_can_phu(self):
        self.assertEqual(cv.needs_cover(msg("1", text="[Tập tin: ds.xlsx]  📎 ds.xlsx (chưa tải)")), "tệp")
        self.assertEqual(cv.needs_cover(msg("2", text="liên hệ 0912345678 giúp em")), "SĐT/email")
        self.assertEqual(cv.needs_cover(msg("3", text="gửi về a.b@tphcm.gov.vn")), "SĐT/email")
        self.assertEqual(cv.needs_cover(msg("4", text="Nhờ TẠO TÀI KHOẢN cho trường A")), "từ khoá")
        self.assertEqual(cv.needs_cover(msg("5", text="em không vào được hệ thống")), "từ khoá")
        self.assertEqual(cv.needs_cover(msg("6", text="Quên mật khẩu ạ")), "từ khoá")

    def test_tin_duoc_mien(self):
        file = "[Tập tin: ds.xlsx]  📎 ds.xlsx (chưa tải)"
        self.assertIsNone(cv.needs_cover(msg("1", text="Cảm ơn anh chị")))
        self.assertIsNone(cv.needs_cover(msg("2", who="NV", text=file)))                                  # nhân viên
        self.assertIsNone(cv.needs_cover(msg("3", name="Nhân Viên B - Luatvietnammisa", text=file)))        # người gửi Misa
        self.assertIsNone(cv.needs_cover(msg("4", text="[ĐÃ THU HỒI] " + file)))                          # đã thu hồi
        self.assertIsNone(cv.needs_cover(msg("5", text=file, new=False)))                                  # không thuộc TIN MỚI

    def test_uncovered_tep_phai_phu_truc_tiep_con_tin_chu_duoc_phu_theo_tep(self):
        file = msg("f", text="[Tập tin: ds.xlsx]  📎 ds.xlsx (chưa tải)", minute=1)
        note = msg("n", text="Nhờ tạo tài khoản cho trường A", minute=0)
        other = msg("o", name="Người khác", text="Nhờ tạo tài khoản cho trường B", minute=1)
        far = msg("far", text="Nhờ tạo tài khoản cho trường C", minute=30)
        msgs = [note, file, other, far]
        self.assertEqual([m["id"] for m in cv.uncovered(msgs, [])], ["n", "f", "o", "far"])
        covered = [{"request_message_id": "f", "source_message_ids": ["f"]}]
        self.assertEqual([m["id"] for m in cv.uncovered(msgs, covered)], ["o", "far"])   # n: cùng người, 1 phút trước tệp đã phủ
        self.assertEqual([m["id"] for m in cv.uncovered(msgs, [], exempt={"f", "o", "far"})], [])  # not_ours / not_requests
        self.assertEqual([m["id"] for m in cv.uncovered([file, msg("f2", text=file["text"], minute=1)], covered)], ["f2"])  # tệp thứ hai KHÔNG được phủ nhờ tệp thứ nhất

    def test_synth_task_la_low_confidence_va_hop_le(self):
        cfg = json.loads(SAMPLE_CFG.read_text(encoding="utf-8"))
        t = cv.synth_task(msg("f", text="[Tập tin: ds.xlsx]  📎 ds.xlsx"), "g1", cfg, "customer", {"Khách A": "c_01"})
        self.assertTrue(t["title"].startswith("[Cần xem] "))
        self.assertLess(t["confidence"], 0.6)
        self.assertEqual(t["customer_zalo_id"], "c_01")
        self.assertEqual(ct.validate(t, cfg), [])


def file_row(mid, name, ts, sender=("c_01", "Khách A")):
    return {"message_id": mid, "from_id": sender[0], "from_name": sender[1], "text": f"[Tập tin: {name}]", "timestamp": ts, "type": "file",
            "attachment": {"name": name, "path": None}}


def text_row(mid, text, ts, sender=("c_01", "Khách A")):
    return {"message_id": mid, "from_id": sender[0], "from_name": sender[1], "text": text, "timestamp": ts, "type": "text"}


class FocusWindowTests(unittest.TestCase):
    def window(self, n=30):
        lines = ["# Nhóm: g1 — Test", "# Thành viên (NV = nhân viên, KH = khách, HT = hệ thống): KH c1 = An; KH c2 = Bình; NV u1 = Cường", "",
                 "## NGỮ CẢNH (đã xử lý trước đó — KHÔNG tạo ticket từ phần này)"]
        lines += [f"[c{i}] Thứ 2 2026-11-10 08:{i:02d} | KH An: ngữ cảnh {i}" for i in range(1, 6)]
        lines += ["", "## TIN MỚI (dòng 1–30) — chỉ tạo/cập nhật ticket từ phần này"]
        lines += [f"[m{i}] Thứ 2 2026-11-10 09:{i:02d} | KH Bình: tin {i}" for i in range(1, n + 1)]
        lines += ["[sự kiện] tin m12 vừa bị THU HỒI", "", f"# LAST_LINE={n}"]
        return "\n".join(lines)

    def test_giu_tin_lien_quan_va_5_dong_lan_can(self):
        out = tl.focus_window(self.window(), {"m15"})
        for i in range(10, 21):
            self.assertRegex(out, rf"KH Bình: tin {i}(\n|$)")
        for i in (9, 21):
            self.assertNotRegex(out, rf"KH Bình: tin {i}(\n|$)")
        self.assertIn("## TIN MỚI", out)
        self.assertNotIn("LAST_LINE", out)
        self.assertNotIn("KH An", out.split("## TIN MỚI")[1])
        self.assertNotIn("Cường", out)          # thành viên không còn trong cửa sổ bị lược khỏi dòng '# Thành viên'

    def test_doan_xa_nhau_co_dau_luoc_va_su_kien_di_kem_ma_tin(self):
        out = tl.focus_window(self.window(), {"m2", "m25", "m12"})
        self.assertIn("[…]", out)
        self.assertIn("[sự kiện] tin m12 vừa bị THU HỒI", out)    # sự kiện của một tin được giữ lại
        self.assertRegex(out, r"KH Bình: tin 25(\n|$)")
        for i in (18, 19):   # m12 ± 5 dừng ở 17, m25 ± 5 bắt đầu từ 20
            self.assertNotRegex(out, rf"KH Bình: tin {i}(\n|$)")

    def test_khong_khop_ma_nao_thi_tra_nguyen_cua_so(self):
        w = self.window()
        self.assertEqual(tl.focus_window(w, {"zzz"}), w)
        self.assertEqual(tl.focus_window(w, set()), w)


class CoverageFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.env.update({"TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})
        now = time.time() - 600
        # m1: lời nhắn chữ, m2: tệp (mỗi tệp một ticket), m3: NV trả lời
        self.history("g1", [text_row("m1", "Nhờ tạo tài khoản cho trường A", now), file_row("m2", "ds.xlsx", now + 10),
                            text_row("m3", "CMC đã tạo tài khoản", now + 60, ("u_200", "Lợi"))])

    def models(self):
        return [r["model"] for r in self.llm.requests]

    def good(self, **kw):
        return {"tasks": [task(request_message_id="m2", source_message_ids=["m1", "m2"], **kw)], "notes_update": ""}

    def test_da_phu_thi_khong_hoi_lai(self):
        self.llm.reply(self.good())
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)
        self.assertIn("g1:m2", self.ledger())

    def test_bo_sot_thi_hoi_lai_cung_model_kem_ma_tin(self):
        self.llm.reply({"tasks": [], "notes_update": ""})
        self.llm.reply(self.good())
        self.run_main()
        self.assertEqual(self.models(), ["gemini-test", "gemini-test"])
        asked = self.llm.requests[1]["messages"][-1]["content"]
        self.assertIn("TIN CHƯA ĐƯỢC PHỦ", asked)
        self.assertIn("- m2 |", asked)
        self.assertEqual(self.cursor("g1"), 3)
        self.assertIn("g1:m2", self.ledger())

    def test_van_sot_thi_hoi_model_du_phong(self):
        self.llm.reply({"tasks": []})
        self.llm.reply({"tasks": []})
        self.llm.reply(self.good())
        self.run_main()
        self.assertEqual(self.models(), ["gemini-test", "gemini-test", "haiku-test"])
        self.assertIn("g1:m2", self.ledger())

    def test_van_sot_thi_tao_ticket_can_xem_roi_moi_doi_cursor(self):
        for _ in range(3):
            self.llm.reply({"tasks": []})
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.llm.requests), 3)
        self.assertEqual(self.cursor("g1"), 3)                 # đã xử lý xong mới dời
        self.assertEqual(self.ledger(), {})                    # confidence 0.4 => low_confidence, không thành ticket
        self.assertIn("low_confidence", out)
        self.assertIn("[Cần xem]", out)
        review = (self.state / "review.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(review), 1)                       # chỉ tin có tệp; lời nhắn chữ đi kèm được phủ theo tệp
        self.assertIn("[Cần xem]", json.loads(review[0])["title"])

    def test_not_requests_khang_dinh_thi_khong_tao_ticket_can_xem(self):
        self.llm.reply({"tasks": []})
        self.llm.reply({"tasks": [], "not_requests": ["m1", "m2"]})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 2)
        self.assertEqual(self.cursor("g1"), 3)
        self.assertFalse((self.state / "review.jsonl").exists())

    def test_trung_model_du_phong_thi_bo_buoc_du_phong(self):
        self.env["TRIAGE_FALLBACK_MODEL"] = "gemini-test"
        for _ in range(2):
            self.llm.reply({"tasks": []})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 2)
        self.assertTrue((self.state / "review.jsonl").exists())

    def test_review_csv_duoc_xuat_ra_state_export(self):
        self.env["TRIAGE_FALLBACK_MODEL"] = "gemini-test"
        for _ in range(2):
            self.llm.reply({"tasks": []})
        self.run_main()
        import csv
        with open(self.state / "export" / "review.csv", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Thời gian", "Nhóm", "Mã tin", "Trích", "Lý do"])
        self.assertGreaterEqual(len(rows), 2)
        when, group, mid, quote, reason = rows[1]
        self.assertRegex(when, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertIn(mid, ("m1", "m2"))
        self.assertTrue(quote)
        self.assertIn("không phân loại được", reason)

    def test_misa_duoc_mien(self):
        now = time.time() - 600
        self.history("g1", [file_row("m1", "ds.xlsx", now, ("c_99", "Nhân Viên B - Luatvietnammisa"))])
        self.llm.reply({"tasks": []})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(self.cursor("g1"), 1)

    def test_hai_tep_hai_ticket_khong_phu_nhau(self):
        now = time.time() - 600
        self.history("g1", [file_row("m1", "a.xlsx", now), file_row("m2", "b.xlsx", now + 5)])
        self.llm.reply({"tasks": [task(request_message_id="m1", source_message_ids=["m1"])]})
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m2"], unit="Trường B", title="[Trường B] Tạo tài khoản: b")]})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 2)             # m2 không được coi là phủ nhờ m1
        self.assertEqual({"g1:m1", "g1:m2"}, set(self.ledger()))

    def test_review_jsonl_khong_ghi_khi_dry_run(self):
        for _ in range(3):
            self.llm.reply({"tasks": []})
        f = self.tmp / "t.json"
        f.write_text(json.dumps([cv.synth_task(cv.parse_messages("## TIN MỚI\n[m2] Thứ 3 2026-11-10 09:00 | KH Khách A: x 📎 a")[0], "g1", json.loads(SAMPLE_CFG.read_text(encoding="utf-8")), "customer", {})]), encoding="utf-8")
        self.script("create_tickets.py", str(f), "--dry-run")
        self.assertFalse((self.state / "review.jsonl").exists())


class TwoStepTests(Base):
    def setUp(self):
        super().setUp()
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})
        now = time.time() - 600
        self.history("g1", [text_row("m1", "Nhờ tạo tài khoản cho trường A", now), file_row("m2", "ds.xlsx", now + 10),
                            text_row("m3", "CMC đã tạo tài khoản", now + 60, ("u_200", "Lợi")), text_row("m4", "Cảm ơn anh chị", now + 90)])

    @staticmethod
    def labels(**by_id):
        return {"labels": [{"message_id": k, **v} for k, v in by_id.items()]}

    def test_hai_buoc_phan_loai_roi_dien_chi_tiet(self):
        self.llm.reply(self.labels(m1={"label": "request", "anchor": "m2"}, m2={"label": "request"}, m4={"label": "none"}))
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m1", "m2"])], "notes_update": ""})
        rc, out = self.run_main()
        self.assertEqual(len(self.llm.requests), 2)
        classify, detail = self.llm.requests
        self.assertIn("Nhãn", classify["messages"][0]["content"])
        self.assertIn("m1, m2, m4", classify["messages"][1]["content"])      # chỉ tin KH; m3 là NV
        self.assertIn("YÊU CẦU CẦN ĐIỀN CHI TIẾT", detail["messages"][1]["content"])
        self.assertIn("mỏ neo m2 | nhãn request | tin liên quan: m1, m2", detail["messages"][1]["content"])
        self.assertEqual(self.cursor("g1"), 4)
        self.assertIn("g1:m2", self.ledger())
        self.assertIn("2 lần gọi", out)

    def test_hai_buoc_chia_lo(self):
        now = time.time() - 900
        self.history("g1", [file_row(f"m{i}", f"f{i}.xlsx", now + i * 70) for i in range(1, 11)])
        self.env["TRIAGE_BATCH"] = "4"
        self.llm.reply(self.labels(**{f"m{i}": {"label": "request"} for i in range(1, 11)}))
        for lo, hi in ((1, 5), (5, 9), (9, 11)):
            self.llm.reply({"tasks": [task(request_message_id=f"m{i}", source_message_ids=[f"m{i}"], unit=f"Đơn vị {i}", title=f"[Đơn vị {i}] Tạo tài khoản: f{i}") for i in range(lo, hi)]})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 4)              # 1 phân loại + 3 lô (4, 4, 2)
        self.assertEqual(len(self.ledger()), 10)
        self.assertEqual(self.cursor("g1"), 10)

    def test_not_ours_khong_qua_buoc_2_va_duoc_mien_phu(self):
        self.llm.reply(self.labels(m1={"label": "not_ours", "anchor": "m2"}, m2={"label": "not_ours"}, m4={"label": "none"}))
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(self.cursor("g1"), 4)
        self.assertFalse((self.state / "review.jsonl").exists())

    def test_nhan_none_nhung_co_tep_thi_kiem_tra_phu_hoi_lai(self):
        self.llm.reply(self.labels(m1={"label": "none"}, m2={"label": "none"}, m4={"label": "none"}))
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m1", "m2"])]})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 2)
        self.assertIn("TIN CHƯA ĐƯỢC PHỦ", self.llm.requests[1]["messages"][-1]["content"])
        self.assertIn("g1:m2", self.ledger())

    def test_resolved_in_chat_van_tao_ticket_trang_thai_resolved(self):
        self.llm.reply(self.labels(m1={"label": "resolved_in_chat", "anchor": "m2"}, m2={"label": "resolved_in_chat"}, m4={"label": "none"}))
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m1", "m2"], status_hint="open", resolved_evidence_message_id="m3")]})  # model quên hint: nhãn bước 1 quyết định; m3 là tin NV sau tin neo
        _, out = self.run_main()
        self.assertEqual(self.ledger()["g1:m2"]["status"], "resolved")
        self.assertEqual(self.cursor("g1"), 4)
        self.assertIn("đã xong trong chat", out)

    def test_buoc_2_chi_gui_tin_lien_quan_va_mac_dinh_la_2_buoc_gemini(self):
        now = time.time() - 3000
        self.history("g1", [text_row(f"m{i}", f"yêu cầu số {i}", now + i * 5) if i != 15 else file_row("m15", "ds.xlsx", now + 75) for i in range(1, 31)])
        self.llm.reply(self.labels(m15={"label": "request"}))
        self.llm.reply({"tasks": [task(request_message_id="m15", source_message_ids=["m15"])]})
        import os
        from unittest import mock
        import contextlib
        import io
        with mock.patch.dict(os.environ, self.env), mock.patch.object(__import__("sys"), "argv", ["triage_llm.py"]), \
                contextlib.redirect_stdout(io.StringIO()):
            for k in ("TRIAGE_STEPS", "TRIAGE_LLM_MODEL", "TRIAGE_FALLBACK_MODEL"):
                os.environ.pop(k, None)
            os.environ["TRIAGE_MAX_LINES"] = "40"
            tl.main()
        classify, detail = self.llm.requests
        self.assertEqual(classify["model"], tl.DEFAULT_MODEL)       # mặc định: Gemini, 2 bước
        self.assertIn("Nhãn", classify["messages"][0]["content"])
        body = detail["messages"][1]["content"]
        self.assertIn("yêu cầu số 10", body)                       # ±5 dòng quanh m15
        self.assertIn("yêu cầu số 20", body)
        self.assertNotIn("yêu cầu số 2 ", body + " ")              # tin xa bị lược
        self.assertNotIn("yêu cầu số 29", body)
        self.assertGreater(len(classify["messages"][1]["content"]), len(body))   # bước 1 vẫn có cả cửa sổ

    def test_update_dung_mo_neo_ticket_dang_mo(self):
        self.llm.reply(self.labels(m2={"label": "request"}))
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m1", "m2"])]})
        self.run_main()
        with (self.hist / "g1.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(text_row("m5", "sao chưa ai xử lý tài khoản", time.time()), ensure_ascii=False) + "\n")
        self.llm.reply(self.labels(m5={"label": "update", "ref_ticket": "g1:m2"}))
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m5"], description="khách nhắc lại")]})
        self.run_main()
        self.assertIn("nhãn update (cập nhật ticket g1:m2", self.llm.requests[3]["messages"][1]["content"])
        self.assertIn("m5", self.ledger()["g1:m2"]["seen_ids"])

    def test_phan_loai_sai_ref_ticket_thanh_request(self):
        obj = self.labels(m2={"label": "update", "ref_ticket": "g1:khong-co"})
        got = tl.normalize_labels(obj, ["m2"], {"g1:m9"})
        self.assertEqual(got["m2"]["label"], "request")
        self.assertEqual(tl.normalize_labels({"labels": [{"message_id": "zzz", "label": "request"}]}, ["m2"], set()), {})  # mã lạ bị bỏ


class MisaVaDonViTests(Base):
    def setUp(self):
        super().setUp()
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})

    def test_misa_only_text(self):
        self.assertTrue(cv.misa_only_text("@Nhân viên A Đăng nhập vào luatvietnammisa.aicongvu.gov.vn không được? nhờ hỗ trợ"))
        self.assertTrue(cv.misa_only_text("AI Luật Việt Nam bị lỗi"))
        self.assertFalse(cv.misa_only_text("Clex và Misa đều không vào được"))     # nhắc Clex: vẫn là việc của team
        self.assertFalse(cv.misa_only_text("nhờ tạo tài khoản AI công vụ"))

    def test_tin_misa_duoc_mien_kiem_tra_phu(self):
        m = {"id": "m1", "role": "KH", "name": "K", "new": True, "when": None, "text": "Đăng nhập vào luatvietnammisa.aicongvu.gov.vn không được, tài khoản 0912345678"}
        self.assertIsNone(cv.needs_cover(m))

    def test_not_ours_nhung_nhac_clex_thanh_request(self):
        msgs = [{"id": "m1", "role": "KH", "name": "K", "text": "[Tập tin: Danh sách sử dụng AI CLEX và AI Luật.docx] 📎", "new": True, "when": None}]
        labels = {"m1": {"label": "not_ours", "anchor": "m1", "ref": ""}}
        tl.force_misa(labels, msgs)
        self.assertEqual(labels["m1"]["label"], "request")

    def test_tin_misa_bi_ep_not_ours_du_model_bao_request(self):
        now = time.time() - 600
        self.history("g1", [text_row("m1", "@Lợi Nguyễn Đăng nhập vào luatvietnammisa.aicongvu.gov.vn không được? nhờ hỗ trợ", now)])
        self.llm.reply({"labels": [{"message_id": "m1", "label": "request"}]})
        rc, out = self.run_main()
        self.assertEqual(len(self.llm.requests), 1)             # không qua bước 2, không ticket
        self.assertEqual(self.ledger(), {})
        self.assertEqual(self.cursor("g1"), 1)

    def test_chot_chan_cuoi_bo_ticket_va_tin_misa(self):
        msgs = [{"id": "m1", "role": "KH", "name": "K", "text": "Nhờ tạo tài khoản", "new": True, "when": None},
                {"id": "m2", "role": "KH", "name": "K", "text": "đăng nhập luatvietnammisa.aicongvu.gov.vn không được", "new": True, "when": None}]
        tasks = [task(request_message_id="m1", source_message_ids=["m1", "m2"]), task(request_message_id="m2", source_message_ids=["m2"])]
        out, n = cv.drop_misa(tasks, msgs)
        self.assertEqual(n, 2)
        self.assertEqual([(t["request_message_id"], t["source_message_ids"]) for t in out], [("m1", ["m1"])])

    def test_goi_y_don_vi_vao_prompt_buoc_2(self):
        now = time.time() - 600
        self.history("g1", [text_row("m1", "Nhờ a/c hỗ trợ tạo tài khoản xã Bình An với ạ", now), file_row("m2", "DS dang ky.xlsx", now + 20)])
        self.llm.reply({"labels": [{"message_id": "m1", "label": "request", "anchor": "m1"}, {"message_id": "m2", "label": "request", "anchor": "m1"}]})
        self.llm.reply({"tasks": [task(request_message_id="m1", source_message_ids=["m1", "m2"])]})
        self.run_main()
        self.assertIn("gợi ý đơn vị (từ tên tệp/tin kèm, sai thì sửa): Xã Bình An", self.llm.requests[1]["messages"][1]["content"])


class UnitHintTests(unittest.TestCase):
    def test_goi_y_tu_ten_tep_va_tin_chu(self):
        import triage_unit as tu
        cases = {
            "Nhờ a/c hỗ trợ tạo tài khoản xã Bình An với ạ em cảm ơn": "Xã Bình An",
            "[Tập tin: 23 - 10 Danh sách đăng ký AI ho tro cong vu TPHCM P. HOA MAI.xlsx] 📎": "Phường Hoa Mai",
            "[Tập tin: Dang sach dang ky AI xa Cam Thanh.xlsx] 📎": "Xã Cam Thanh",
            "[Tập tin: Bảo tàng tỉnh Sơn Hà - Sở VHTT - TPHCM (2).docx] 📎": "Bảo tàng tỉnh Sơn Hà",
            "[Tập tin: P Minh Khai (2).docx] 📎": "Phường Minh Khai",
            "[Tập tin: PHƯỜNG THANH BÌNH - ĐĂNG KÝ TK NỀN TẢNG AI CÔNG VỤ.xlsx] 📎": "Phường Thanh Bình",
            "Trường THPT Nam Sơn nhờ cấp": "THPT Nam Sơn",
            "Nhờ anh/chị hỗ trợ phường em tạo tài khoản ạ": None,           # "em" không phải tên riêng
            "cho em số điện thoại": None,                                   # "số" không phải Sở
            "[Tập tin: DS_Dky_23102026.xlsx] 📎": None,
        }
        for text, want in cases.items():
            self.assertEqual(tu.suggest_unit([text]), want, text)

    def test_cum_cung_nguoi_gui_trong_3_phut(self):
        import triage_unit as tu
        from datetime import datetime, timedelta
        t0 = datetime(2026, 11, 10, 9, 0)
        msgs = [{"id": "a", "name": "K", "text": "[Tập tin: DS.xlsx] 📎", "when": t0},
                {"id": "b", "name": "K", "text": "tạo tài khoản xã Đông Hưng nhờ", "when": t0 + timedelta(minutes=2)},
                {"id": "c", "name": "K", "text": "xã Xa Lắm", "when": t0 + timedelta(minutes=10)},
                {"id": "d", "name": "Khác", "text": "phường Khác Người", "when": t0 + timedelta(minutes=1)}]
        self.assertEqual(tu.unit_hint(msgs, "a"), "Xã Đông Hưng")


class MergeSplitTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timedelta
        t0 = datetime(2026, 11, 10, 9, 0)
        self.msgs = [{"id": "a", "name": "K", "when": t0}, {"id": "b", "name": "K", "when": t0 + timedelta(minutes=2)},
                     {"id": "c", "name": "K", "when": t0 + timedelta(minutes=4)}, {"id": "d", "name": "K", "when": t0 + timedelta(minutes=30)},
                     {"id": "e", "name": "Khác", "when": t0 + timedelta(minutes=1)}]

    @staticmethod
    def tk(rid, unit="Xã Bình Yên", cat="account_registration", hint="resolved_in_chat", src=None):
        return {"request_message_id": rid, "source_message_ids": src or [rid], "unit": unit, "category": cat, "status_hint": hint, "description": f"mô tả {rid}"}

    def test_gop_cung_nguoi_cung_don_vi_trong_3_phut_theo_chuoi(self):
        out, n = cv.merge_split([self.tk("c"), self.tk("a"), self.tk("b", unit="XA BINH YEN")], self.msgs)   # a-b 2', b-c 2' (chuỗi), không dấu/hoa thường
        self.assertEqual(n, 2)
        self.assertEqual([t["request_message_id"] for t in out], ["a"])
        self.assertEqual(out[0]["source_message_ids"], ["a", "b", "c"])
        self.assertEqual(out[0]["status_hint"], "resolved_in_chat")            # cả ba đều xong
        self.assertIn("mô tả b", out[0]["description"])

    def test_khong_gop_khi_khac_dieu_kien(self):
        tasks = [self.tk("a"), self.tk("d"),                                   # cách 30 phút
                 self.tk("e"),                                                 # người gửi khác
                 self.tk("b", unit="Phường Khác"),                             # khác đơn vị
                 self.tk("c", cat="access_issue"),                             # khác loại
                 self.tk("x")]                                                 # tin neo ngoài cửa sổ
        out, n = cv.merge_split(tasks, self.msgs)
        self.assertEqual((n, len(out)), (0, 6))

    def test_thieu_don_vi_la_cung_don_vi_va_nhan_don_vi_con_lai(self):
        out, n = cv.merge_split([self.tk("a", unit=None), self.tk("b", unit="Xã Bình Yên")], self.msgs)
        self.assertEqual((n, len(out), out[0]["unit"]), (1, 1, "Xã Bình Yên"))
        out, n = cv.merge_split([self.tk("a", unit="Xã A"), self.tk("b", unit="Xã B")], self.msgs)
        self.assertEqual(n, 0)

    def test_can_cu_can_xem_khong_bi_gop(self):
        t = self.tk("b", unit=None)
        t["title"] = "[Cần xem] gì đó"
        self.assertEqual(cv.merge_split([self.tk("a"), t], self.msgs)[1], 0)

    def test_mot_open_thi_ca_cum_open(self):
        out, _ = cv.merge_split([self.tk("a"), self.tk("b", hint="open")], self.msgs)
        self.assertEqual(out[0]["status_hint"], "open")


class InternalGroupTests(Base):
    def test_nhom_noi_bo_chay_1_buoc_va_kiem_tra_phu_tin_cua_nv(self):
        cfg = json.loads(SAMPLE_CFG.read_text(encoding="utf-8"))
        cfg.setdefault("groups", {})["g1"] = {**cfg.get("groups", {}).get("g1", {}), "mode": "internal"}
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})
        now = time.time() - 600
        self.history("g1", [text_row("m1", "Nhờ làm báo cáo tài khoản tuần này", now, ("u_200", "Lợi"))])
        self.llm.reply({"tasks": [task(request_message_id="m1", source_message_ids=["m1"], kind="internal", unit=None, title="Làm báo cáo tài khoản tuần")]})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)             # 1 bước, không có bước phân loại tin KH
        self.assertIn("g1:m1", self.ledger())

    def test_nhom_noi_bo_dung_prompt_rieng_va_model_rieng(self):
        cfg = json.loads(SAMPLE_CFG.read_text(encoding="utf-8"))
        cfg.setdefault("groups", {})["g1"] = {**cfg.get("groups", {}).get("g1", {}), "mode": "internal"}
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_INTERNAL_MODEL": "haiku-noibo"})
        now = time.time() - 600
        self.history("g1", [text_row("m1", "Thu quỹ phòng tháng này, mọi người đóng 100k nhé", now, ("u_200", "Lợi"))])
        self.llm.reply({"tasks": [task(request_message_id="m1", source_message_ids=["m1"], kind="internal", unit=None, category="internal_task", title="Thu quỹ phòng tháng này")]})
        self.run_main()
        req = self.llm.requests[0]
        self.assertEqual(req["model"], "haiku-noibo")
        self.assertIn("NHÓM NỘI BỘ", req["messages"][0]["content"])
        self.assertIn("thu quỹ", req["messages"][0]["content"])
        self.assertNotIn("account_registration", req["messages"][0]["content"].split("## LOẠI")[0])     # không còn luật tệp danh sách đăng ký của nhóm khách

    def test_nhom_noi_bo_khong_ep_phu_theo_tu_khoa(self):
        base = {"id": "m1", "role": "KH", "name": "NV", "new": True, "when": None, "text": "đã cấp tài khoản rồi nhé"}
        self.assertEqual(cv.needs_cover(base), "từ khoá")
        self.assertIsNone(cv.needs_cover({**base, "internal": True}))
        self.assertEqual(cv.needs_cover({**base, "internal": True, "text": "[Tập tin: a.xlsx] 📎"}), "tệp")

    def test_nhom_noi_bo_model_tra_rong_thi_1_ticket_can_xem_tong_hop(self):
        cfg = json.loads(SAMPLE_CFG.read_text(encoding="utf-8"))
        cfg.setdefault("groups", {})["g1"] = {**cfg.get("groups", {}).get("g1", {}), "mode": "internal"}
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})
        now = time.time() - 600
        self.history("g1", [file_row("m1", "a.xlsx", now, ("u_200", "Lợi")), file_row("m2", "b.xlsx", now + 60, ("u_200", "Lợi")),
                            text_row("m3", "chị gọi 0912345678 nhé", now + 100, ("u_200", "Lợi"))])
        self.llm.reply({"tasks": []})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)             # không hỏi lại từng tin
        self.assertEqual(self.ledger(), {})                     # confidence 0.4 => không thành ticket thật
        rv = [json.loads(l) for l in (self.state / "review.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rv), 1)                            # đúng MỘT mục tổng hợp
        self.assertIn("[Cần xem] 3 tin", rv[0]["title"])
        for mid in ("m1", "m2", "m3"):
            self.assertIn(mid, rv[0]["description"])
        self.assertEqual(self.cursor("g1"), 3)


class ResolveStepTests(Base):
    """Bước hỏi nhỏ: tin nhân viên mới có báo đã xử lý xong ticket đang mở nào không."""

    def setUp(self):
        super().setUp()
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})
        now = time.time() - 900
        self.staff_text = "Em đã tạo tài khoản cho trường A rồi ạ, anh chị kiểm tra giúp"
        self.history("g1", [text_row("m0", "Nhờ tạo tài khoản cho trường A", now), text_row("m1", self.staff_text, now + 300, ("u_200", "Lợi"))])
        (self.state / "cursors").mkdir(exist_ok=True)
        (self.state / "cursors" / "g1.json").write_text(json.dumps({"line": 1}), encoding="utf-8")
        with open(self.state / "ledger.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"key": "g1:m0", "status": "open", "title": "[Trường A] Tạo tài khoản: ds", "customer": "Khách A", "kind": "cs",
                                "unit": "Trường A", "platform": "Clex", "priority": "high", "due": "2026-11-12T09:00", "seen_ids": ["m0"],
                                "at": "2026-11-10T01:00:00+00:00"}, ensure_ascii=False) + "\n")

    def ask(self, **kw):
        return {"resolved": [{"ticket_key": "g1:m0", "staff_message_id": "m1", "quote": "Em đã tạo tài khoản cho trường A rồi", **kw}]}

    def test_nv_bao_xong_thi_ticket_open_thanh_resolved_va_giu_nguyen_uu_tien_han(self):
        self.llm.reply(self.ask())
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)             # không có tin khách mới nên không có bước phân loại
        self.assertIn("TIN NHÂN VIÊN MỚI CẦN XÉT", self.llm.requests[0]["messages"][1]["content"])
        rec = self.ledger()["g1:m0"]
        self.assertEqual(rec["status"], "resolved")
        self.assertEqual((rec["priority"], rec["due"], rec["customer"], rec["unit"]), ("high", "2026-11-12T09:00", "Khách A", "Trường A"))
        self.assertIn("m1", rec["seen_ids"])
        self.assertEqual(self.cursor("g1"), 2)

    def test_trich_dan_khong_dung_tin_nv_thi_van_open(self):
        self.llm.reply(self.ask(quote="Đã xong hết rồi nhé mọi người"))
        self.llm.reply(self.ask(quote="Đã xong hết rồi nhé mọi người"))   # sau 1 lần sửa vẫn sai
        self.run_main()
        self.assertEqual(self.ledger()["g1:m0"]["status"], "open")
        self.assertEqual(self.cursor("g1"), 2)

    def test_khoa_ticket_la_hoac_tin_khong_phai_nv_bi_tu_choi(self):
        self.llm.reply(self.ask(ticket_key="g1:khong-co"))
        self.llm.reply({"resolved": []})                         # sửa lại: không có gì
        self.run_main()
        self.assertEqual(self.ledger()["g1:m0"]["status"], "open")
        self.assertEqual(len(self.llm.requests), 2)
        self.assertIn("không nằm trong danh sách ticket đang mở", self.llm.requests[1]["messages"][-1]["content"])

    def test_mac_dinh_open_khi_model_tra_rong(self):
        self.llm.reply({"resolved": []})
        self.run_main()
        self.assertEqual(self.ledger()["g1:m0"]["status"], "open")

    def test_khong_goi_khi_khong_co_tin_nv_hoac_khong_co_ticket_open(self):
        now = time.time() - 900
        self.history("g1", [text_row("m0", "Nhờ tạo tài khoản cho trường A", now), text_row("m1", "Cảm ơn nhé", now + 300)])   # tin mới là của khách
        self.llm.reply({"labels": [{"message_id": "m1", "label": "none"}]})
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)             # chỉ bước phân loại, không có bước resolve
        self.assertNotIn("TIN NHÂN VIÊN MỚI", self.llm.requests[0]["messages"][1]["content"])
        # không có ticket open
        self.history("g1", [text_row("m0", "Nhờ tạo tài khoản cho trường A", now), text_row("m1", self.staff_text, now + 300, ("u_200", "Lợi"))])
        (self.state / "ledger.jsonl").write_text("", encoding="utf-8")
        (self.state / "cursors" / "g1.json").write_text(json.dumps({"line": 1}), encoding="utf-8")
        self.run_main()
        self.assertEqual(len(self.llm.requests), 1)

    def test_tat_bang_bien_moi_truong(self):
        self.env["TRIAGE_RESOLVE"] = "0"
        self.run_main()
        self.assertEqual(len(self.llm.requests), 0)
        self.assertEqual(self.ledger()["g1:m0"]["status"], "open")



@unittest.skipIf(sys.platform == "win32", "script bash chạy trong WSL/Linux")
class SyncOnedriveTests(unittest.TestCase):
    def test_chep_review_csv_ke_ca_khi_chua_co_jsm_import(self):
        import subprocess
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            home, dest = Path(d) / "home", Path(d) / "dest"
            (home / "zalo-tickets" / "export").mkdir(parents=True)
            (home / "zalo-tickets" / "export" / "review.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            script = Path(__file__).resolve().parent.parent / "scripts" / "sync-tickets-onedrive.sh"
            env = {**os.environ, "HERMES_HOME": str(home), "TICKETS_ONEDRIVE_DIR": str(dest)}
            r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((dest / "review.csv").read_text(encoding="utf-8"), "a,b\n1,2\n")
            self.assertIn("Đã chép 1 file", r.stdout)
            self.assertEqual(subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env).stdout, "")   # không đổi thì im lặng


class ResolveGateTests(Base):
    """Chốt chặn bằng mã: chỉ đóng ticket khi tin NV nêu đúng đơn vị/tên khách, @tag đúng khách, hoặc chỉ có 1 ticket mở trong 10 tin gần nhất."""

    def setUp(self):
        super().setUp()
        self.env.update({"TRIAGE_STEPS": "2", "TRIAGE_COVERAGE": "1", "TRIAGE_LLM_MODEL": "gemini-test", "TRIAGE_FALLBACK_MODEL": "haiku-test"})
        (self.state / "cursors").mkdir(exist_ok=True)

    def seed(self, tickets, staff_text, gap_msgs=0):
        """tickets: [(mã tin neo, khách, đơn vị)] đều là tin khách cũ; sau đó `gap_msgs` tin chữ của khách khác rồi tin NV (mới)."""
        now = time.time() - 3000
        rows, led = [], []
        for i, (mid, cust, unit) in enumerate(tickets):
            rows.append(text_row(mid, f"Nhờ tạo tài khoản cho {unit}", now + i * 10, (f"c_{mid}", cust)))
            led.append({"key": f"g1:{mid}", "status": "open", "title": f"[{unit}] Tạo tài khoản", "customer": cust, "kind": "cs", "unit": unit,
                        "seen_ids": [mid], "at": "2026-11-10T01:00:00+00:00"})
        for j in range(gap_msgs):
            rows.append(text_row(f"g{j}", "chào mọi người", now + 200 + j, (f"c_x{j}", f"Người lạ {j}")))
        rows.append(text_row("nv1", staff_text, now + 900, ("u_200", "Lợi")))
        self.history("g1", rows)
        (self.state / "cursors" / "g1.json").write_text(json.dumps({"line": len(tickets) + gap_msgs}), encoding="utf-8")
        with open(self.state / "ledger.jsonl", "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in led) + "\n")

    def reply(self, *keys):
        self.llm.reply({"resolved": [{"ticket_key": f"g1:{k}", "staff_message_id": "nv1", "quote": "đã tạo tài khoản"} for k in keys]})

    def status(self):
        return {k: v["status"] for k, v in self.ledger().items()}

    def test_a_nen_dung_don_vi_thi_dong_dung_ticket_do(self):
        self.seed([("t1", "Khách A", "Xã Bình Yên"), ("t2", "Khách B", "Phường Hà Đông")], "Xã Bình Yên đã tạo tài khoản xong rồi nhé")
        self.reply("t1")
        self.run_main()
        self.assertEqual(self.status(), {"g1:t1": "resolved", "g1:t2": "open"})

    def test_b_tag_dung_khach(self):
        self.seed([("t1", "Nguyễn Văn An", "Sở X"), ("t2", "Trần Thị Bình", "Sở Y")], "@Nguyễn Văn An em đã tạo tài khoản rồi nhé")
        self.reply("t1")
        self.run_main()
        self.assertEqual(self.status()["g1:t1"], "resolved")

    def test_c_chi_co_1_ticket_trong_10_tin_gan_nhat(self):
        self.seed([("t1", "Khách A", "Trường A")], "đã tạo tài khoản rồi ạ", gap_msgs=3)
        self.reply("t1")
        self.run_main()
        self.assertEqual(self.status()["g1:t1"], "resolved")

    def test_tin_chung_chung_nhieu_ung_vien_thi_khong_dong(self):
        self.seed([("t1", "Khách A", "Trường A"), ("t2", "Khách B", "Trường B")], "đã tạo tài khoản rồi nhé")
        self.reply("t1", "t2")
        self.run_main()
        self.assertEqual(self.status(), {"g1:t1": "open", "g1:t2": "open"})

    def test_ticket_ngoai_10_tin_gan_nhat_khong_duoc_coi_la_ung_vien_duy_nhat(self):
        self.seed([("t1", "Khách A", "Trường A")], "đã tạo tài khoản rồi ạ", gap_msgs=12)    # ticket t1 cách xa hơn 10 tin
        self.reply("t1")
        self.run_main()
        self.assertEqual(self.status()["g1:t1"], "open")

    def test_mot_tin_nv_dong_2_ticket_tro_len_chi_hop_le_khi_moi_ticket_co_ten_khach_rieng(self):
        # chỉ nêu đơn vị của hai ticket -> không đóng cái nào
        self.seed([("t1", "Nguyễn Văn An", "Xã Đơn Vị1"), ("t2", "Trần Thị Bình", "Xã Đơn Vị2")], "Xã Đơn Vị1 và Xã Đơn Vị2 đã tạo tài khoản xong")
        self.reply("t1", "t2")
        self.run_main()
        self.assertEqual(set(self.status().values()), {"open"})
        # nêu tên khách RIÊNG của từng ticket -> đóng cả hai
        self.seed([("t1", "Nguyễn Văn An", "Xã Đơn Vị1"), ("t2", "Trần Thị Bình", "Xã Đơn Vị2")], "Nguyễn Văn An và Trần Thị Bình đã tạo tài khoản xong")
        self.reply("t1", "t2")
        self.run_main()
        self.assertEqual(set(self.status().values()), {"resolved"})

    def test_hai_ticket_mot_co_tag_mot_chi_co_don_vi_thi_khong_dong_cai_nao(self):
        self.seed([("t1", "Nguyễn Văn An", "Xã Đơn Vị1"), ("t2", "Trần Thị Bình", "Xã Đơn Vị2")], "@Nguyễn Văn An xong rồi, Xã Đơn Vị2 cũng xong")
        self.reply("t1", "t2")
        self.run_main()
        self.assertEqual(set(self.status().values()), {"open"})

    def test_hai_ticket_deu_co_tag_rieng_thi_dong_ca_hai(self):
        self.seed([("t1", "Nguyễn Văn An", "Xã Đơn Vị1"), ("t2", "Trần Thị Bình", "Xã Đơn Vị2")], "@Nguyễn Văn An @Trần Thị Bình đã tạo tài khoản")
        self.reply("t1", "t2")
        self.run_main()
        self.assertEqual(set(self.status().values()), {"resolved"})

    def test_toi_da_3_ticket_moi_tin_nv_du_co_ten_khach_rieng(self):
        names = ["Nguyễn Văn An", "Trần Thị Bình", "Lê Hoàng Cường", "Phạm Minh Dũng"]
        self.seed([(f"t{i}", n, f"Xã Đơn Vị{i}") for i, n in enumerate(names, 1)], ", ".join(names) + " đã tạo tài khoản xong")
        self.reply("t1", "t2", "t3", "t4")
        self.run_main()
        self.assertEqual(set(self.status().values()), {"open"})                    # 4 ticket > 3: không đóng cái nào
        (self.state / "cursors" / "g1.json").write_text(json.dumps({"line": 4}), encoding="utf-8")
        self.reply("t1", "t2", "t3")
        self.run_main()
        self.assertEqual(sorted(k for k, v in self.status().items() if v == "resolved"), ["g1:t1", "g1:t2", "g1:t3"])


class ResolveRuleUnitTests(unittest.TestCase):
    def test_core_bo_tien_to_va_dau(self):
        self.assertEqual(cv._core("Xã Bình Yên"), "binh yen")
        self.assertEqual(cv._core("Sở Tư pháp Hà Nam"), "tu phap ha nam")
        self.assertEqual(cv.resolve_rule({"unit": "Xã Bình Yên"}, {"text": "xa binh yen xong roi"}, [], "g:k"), "nêu đúng đơn vị")
        self.assertEqual(cv.resolve_rule({"customer": "Lê Văn B"}, {"text": "@Lê Văn B nhé"}, [], "g:k"), "tag đúng khách")
        self.assertEqual(cv.resolve_rule({"customer": "Lê Văn B", "unit": "Xã Bình Yên"}, {"text": "Lê Văn B và xa binh yen xong"}, [], "g:k"), "nêu đúng tên khách")   # tên khách thắng đơn vị
        self.assertEqual(cv.STRONG_RULES, ("tag đúng khách", "nêu đúng tên khách"))
        self.assertIsNone(cv.resolve_rule({"unit": "Trường A", "customer": "Khách A"}, {"text": "đã xong"}, [], "g:k"))   # đơn vị quá ngắn, không nêu



class TriagePrepareDefaultsTests(unittest.TestCase):
    def help_text(self, **env):
        import subprocess
        script = __import__("pathlib").Path(__file__).resolve().parent.parent / "skills" / "zalo-task-tickets" / "scripts" / "triage_prepare.py"
        e = {k: v for k, v in os.environ.items() if k not in ("TRIAGE_MAX_LINES", "TRIAGE_CONTEXT")}
        e.update(env, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, encoding="utf-8", env=e).stdout

    def test_mac_dinh_30_dong_4_tin_ngu_canh_va_doc_bien_moi_truong(self):
        t = " ".join(self.help_text().split())
        self.assertIn("(mặc định 30)", t)
        self.assertIn("(mặc định 4)", t)
        t = " ".join(self.help_text(TRIAGE_MAX_LINES="17", TRIAGE_CONTEXT="2").split())
        self.assertIn("(mặc định 17)", t)
        self.assertIn("(mặc định 2)", t)


class DedupGuardTests(Base):
    """Chốt chặn chống trùng: ticket đề xuất mới có neo/tin nguồn đã nằm trong seen_ids của ticket cũ => cập nhật ticket cũ, không tạo mới."""

    OLD = {"key": "g1:m1", "status": "open", "title": "[Trường A] Tạo tài khoản: ds", "customer": "Khách A", "unit": "Trường A", "seen_ids": ["m1", "m2"],
           "kind": "cs", "priority": "high", "due": "2026-11-12T09:00", "at": "2026-11-10T01:00:00+00:00"}

    def setUp(self):
        super().setUp()
        now = time.time() - 900
        self.history("g1", [text_row("m1", "Nhờ tạo tài khoản cho trường A", now), text_row("m2", "Em gửi danh sách", now + 30), text_row("m3", "Thêm 1 tài khoản nữa", now + 60),
                            text_row("m4", "Việc khác hẳn", now + 120)])
        ct.append(self.state / "ledger.jsonl", dict(self.OLD))

    def test_trung_hoan_toan_khong_tao_moi_va_khong_doi_ticket_cu(self):
        self.llm.reply({"tasks": [task(request_message_id="m2", source_message_ids=["m2"], title="tên model đặt khác", unit="Đơn vị khác")]})
        rc, out = self.run_main()
        led = self.ledger()
        self.assertEqual(set(led), {"g1:m1"})                                   # không có g1:m2
        self.assertEqual((led["g1:m1"]["title"], led["g1:m1"]["unit"], led["g1:m1"]["status"]), (self.OLD["title"], "Trường A", "open"))
        self.assertEqual(led["g1:m1"]["seen_ids"], ["m1", "m2"])                # không có tin mới để thêm
        self.assertIn("chốt chặn chống trùng: tin neo m2 đã có trong ticket cũ g1:m1", out)

    def test_trung_mot_phan_cap_nhat_ticket_cu_voi_tin_moi(self):
        self.llm.reply({"tasks": [task(request_message_id="m3", source_message_ids=["m2", "m3"], status_hint="resolved_in_chat")]})
        rc, out = self.run_main()
        led = self.ledger()
        self.assertEqual(set(led), {"g1:m1"})                                   # không tạo g1:m3
        self.assertEqual(led["g1:m1"]["seen_ids"], ["m1", "m2", "m3"])         # thêm đúng tin mới m3
        self.assertEqual(led["g1:m1"]["status"], "open", "trạng thái chỉ đổi qua bước resolve, không qua chốt chặn")
        self.assertEqual((led["g1:m1"]["priority"], led["g1:m1"]["due"]), ("high", "2026-11-12T09:00"))
        self.assertEqual(led["g1:m1"]["title"], self.OLD["title"])
        self.assertIn("(+1 tin mới)", out)

    def test_khong_trung_thi_tao_moi_nhu_cu(self):
        self.llm.reply({"tasks": [task(request_message_id="m4", source_message_ids=["m4"], title="[Trường B] Báo lỗi: x", unit="Trường B")]})
        rc, out = self.run_main()
        self.assertEqual(set(self.ledger()), {"g1:m1", "g1:m4"})
        self.assertNotIn("chốt chặn chống trùng", out)

    def test_ticket_cu_da_resolved_thi_giu_resolved(self):
        ct.append(self.state / "ledger.jsonl", {**self.OLD, "status": "resolved", "at": "2026-11-10T02:00:00+00:00"})
        self.llm.reply({"tasks": [task(request_message_id="m3", source_message_ids=["m3", "m1"])]})   # hint open của model không được mở lại ticket
        self.run_main()
        led = self.ledger()
        self.assertEqual((led["g1:m1"]["status"], "m3" in led["g1:m1"]["seen_ids"]), ("resolved", True))

    def test_dung_mo_neo_cua_ticket_cu_la_cap_nhat_hop_le_khong_bi_dem_la_chot_chan(self):
        self.llm.reply({"tasks": [task(request_message_id="m1", source_message_ids=["m3"])]})
        rc, out = self.run_main()
        self.assertEqual(self.ledger()["g1:m1"]["seen_ids"], ["m1", "m2", "m3"])
        self.assertNotIn("chốt chặn chống trùng", out)

    def test_ham_thuan_nhom_khac_khong_bi_chuyen_va_nhieu_de_xuat_gop_mot(self):
        led = {"g1:m1": dict(self.OLD), "g2:x1": {"key": "g2:x1", "status": "open", "title": "t", "seen_ids": ["x1", "m3"]}}
        tasks = [task(request_message_id="m3", source_message_ids=["m3"]), task(request_message_id="m2", source_message_ids=["m2", "m9"]),
                 task(request_message_id="m4", source_message_ids=["m4"])]
        out, ev = tl.redirect_covered(tasks, led, "g1")
        self.assertEqual([t["request_message_id"] for t in out], ["m3", "m1", "m4"])    # m3 chỉ nằm trong ticket của nhóm g2 nên giữ nguyên; m2 -> g1:m1
        self.assertEqual(ev, [{"group": "g1", "anchor": "m2", "old_key": "g1:m1", "new_ids": ["m9"]}])
        t2, e2 = tl.redirect_covered([task(request_message_id="m2", source_message_ids=["m2", "m9"]), task(request_message_id="m1", source_message_ids=["m1"])],
                                     {"g1:m1": dict(self.OLD)}, "g1")
        self.assertEqual(len(t2), 2)                                                     # m1 là mỏ neo hợp lệ của ticket cũ nên không đổi



class ResolvedEvidenceTests(Base):
    """Ticket MỚI resolved_in_chat chỉ giữ resolved khi model nêu mã tin NV hợp lệ (trong nhóm, sau tin neo, người gửi là NV, không dùng chung)."""

    @staticmethod
    def msg(i, role="KH", name="K"):
        return {"id": i, "role": role, "name": name, "text": "x", "new": True, "when": None}

    def msgs(self):
        return [self.msg("a1"), self.msg("n1", "NV", "Nhân Viên A"), self.msg("a2"), self.msg("n2", "NV", "Nhân Viên A"), self.msg("k9", "KH", "Khách Z"),
                self.msg("mz", "KH", "Quản trị - Luatvietnammisa")]

    @staticmethod
    def rt(anchor, ev=None, hint="resolved_in_chat"):
        t = task(request_message_id=anchor, source_message_ids=[anchor], status_hint=hint)
        if ev is not None:
            t["resolved_evidence_message_id"] = ev
        return t

    def check(self, tasks, ledger=None):
        out, events = tl.validate_resolved(tasks, self.msgs(), ledger or {}, "g1")
        return [t["status_hint"] for t in out], events

    def test_hop_le_giu_resolved(self):
        st, ev = self.check([self.rt("a1", "n1")])
        self.assertEqual((st, ev), (["resolved_in_chat"], []))

    def test_thieu_ma_thi_open(self):
        st, ev = self.check([self.rt("a1")])
        self.assertEqual(st, ["open"])
        self.assertEqual((ev[0]["reason"], ev[0]["anchor"], ev[0]["evidence"]), ("thiếu mã tin nhân viên", "a1", None))

    def test_ma_truoc_tin_neo_thi_open(self):
        st, ev = self.check([self.rt("a2", "n1")])               # n1 đứng trước a2
        self.assertEqual((st, ev[0]["reason"]), (["open"], "mã không nằm sau tin neo"))

    def test_ma_khac_nhom_hoac_khong_co_trong_cua_so_thi_open(self):
        st, ev = self.check([self.rt("a1", "nhom-khac-77")])
        self.assertEqual((st, ev[0]["reason"]), (["open"], "mã không nằm trong cửa sổ của nhóm"))

    def test_nguoi_gui_la_khach_hoac_misa_thi_open(self):
        st, ev = self.check([self.rt("a1", "k9"), self.rt("a2", "mz")])
        self.assertEqual(st, ["open", "open"])
        self.assertEqual({e["reason"] for e in ev}, {"người gửi không phải nhân viên (khách hoặc Misa)"})

    def test_ma_dung_chung_hai_ticket_thi_ca_hai_open(self):
        st, ev = self.check([self.rt("a1", "n2"), self.rt("a2", "n2")])
        self.assertEqual(st, ["open", "open"])
        self.assertEqual([e["reason"] for e in ev], ["mã dùng chung với ticket khác"] * 2)

    def test_ma_da_nam_trong_ticket_cu_cung_nhom_la_dung_chung(self):
        led = {"g1:old": {"key": "g1:old", "status": "open", "seen_ids": ["old", "n2"]}}
        st, ev = self.check([self.rt("a1", "n2")], led)
        self.assertEqual((st, ev[0]["reason"]), (["open"], "mã dùng chung với ticket khác"))

    def test_ma_suy_ra_bang_code_khong_du_de_resolved(self):
        # có tin NV n1 ngay sau a1 nhưng model KHÔNG nêu mã: code không được tự suy ra
        st, ev = self.check([self.rt("a1")])
        self.assertEqual(st, ["open"])

    def test_khong_dong_cham_ticket_open_va_ticket_cap_nhat_ticket_cu(self):
        led = {"g1:a1": {"key": "g1:a1", "status": "resolved", "seen_ids": ["a1"]}}
        st, ev = self.check([self.rt("a1", None), self.rt("a2", None, hint="open")], led)   # a1 là ticket cũ (cập nhật); a2 vốn open
        self.assertEqual((st, ev), (["resolved_in_chat", "open"], []))

    def test_luong_day_du_resolved_hop_le_va_thieu_ma(self):
        now = time.time() - 900
        self.history("g1", [text_row("m1", "Nhờ tạo tài khoản cho trường A", now), text_row("m2", "Đã tạo tài khoản rồi ạ", now + 60, ("u_200", "Lợi")),
                            text_row("m3", "Nhờ tạo tài khoản cho trường B", now + 120)])
        ok = task(request_message_id="m1", source_message_ids=["m1"], status_hint="resolved_in_chat", resolved_evidence_message_id="m2")
        thieu = task(request_message_id="m3", source_message_ids=["m3"], status_hint="resolved_in_chat", unit="Trường B", title="[Trường B] Tạo tài khoản: ds")
        self.llm.reply({"tasks": [ok, thieu]})
        rc, out = self.run_main()
        led = self.ledger()
        self.assertEqual((led["g1:m1"]["status"], led["g1:m3"]["status"]), ("resolved", "open"))
        self.assertIn("ticket mới m3: resolved -> open (thiếu mã tin nhân viên; mã không có)", out)
        rows = {r["key"]: r for r in ct.load_jsonl(self.state / "tickets.jsonl") if "update" not in r}
        self.assertEqual(rows["g1:m1"]["resolved_evidence_message_id"], "m2")      # bằng chứng được lưu cùng ticket


class FakeJiraFull:
    """Jira giả đủ cho jira_push (tạo issue, transition, comment). Ghi (method, path, body)."""

    def __init__(self, two_step=False, already_done=False, no_done_transition=False):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.calls, self.n, self.status = [], 0, {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def _serve(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else None
                path, code, res = self.path, 200, {}
                outer.calls.append((self.command, path, body))
                if "/search/jql" in path:
                    res = {"issues": []}
                elif path == "/rest/api/3/myself":
                    res = {"accountId": "me-123"}
                elif path.startswith("/rest/api/3/project/"):
                    res = {"name": "My Team"}
                elif self.command == "POST" and path == "/rest/api/3/issue":
                    outer.n += 1
                    key = f"KAN-{outer.n}"
                    outer.status[key] = "done" if already_done else "new"
                    code, res = 201, {"key": key}
                elif "/transitions" in path and self.command == "GET":
                    key = path.split("/")[5]
                    if no_done_transition:
                        res = {"transitions": []}
                    elif two_step and outer.status[key] == "new":
                        res = {"transitions": [{"id": "11", "name": "In Progress", "to": {"statusCategory": {"key": "indeterminate"}}}]}
                    else:
                        res = {"transitions": [{"id": "31", "name": "Done", "to": {"statusCategory": {"key": "done"}}}]}
                elif "/transitions" in path and self.command == "POST":
                    key = path.split("/")[5]
                    outer.status[key] = "done" if body["transition"]["id"] == "31" else "indeterminate"
                    code, res = 204, None
                elif "?fields=status" in path:
                    key = path.split("/")[5].split("?")[0]
                    res = {"fields": {"status": {"statusCategory": {"key": "done" if outer.status.get(key) == "done" else "new"}}}}
                elif path.endswith("/comment"):
                    code, res = 201, {}
                data = b"" if res is None else json.dumps(res).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _serve  # noqa: N815

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self, suffix):
        return [(p, b) for m, p, b in self.calls if m == "POST" and p.endswith(suffix)]


class JiraResolvedDoneTests(Base):
    """Ticket resolved (xong trong chat) cũng được đẩy lên Jira rồi chuyển Done; ticket open vẫn để nguyên."""

    def setUp(self):
        super().setUp()
        self.history("g1")
        self.creds = {"JIRA_EMAIL": "t@example.com", "JIRA_API_TOKEN": "khong-that"}

    def jira(self, **kw):
        j = FakeJiraFull(**kw)
        self.addCleanup(j.close)
        cfg = json.loads((self.state / "cs_config.json").read_text(encoding="utf-8"))
        cfg["jira"] = {"enabled": True, "mode": "task", "project_key": "KAN", "base_url": j.url}
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return j

    def create(self, *tasks):
        f = self.tmp / "tasks.json"
        f.write_text(json.dumps(list(tasks), ensure_ascii=False), encoding="utf-8")
        r = self.script("create_tickets.py", str(f))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def push(self, *args):
        return self.script("jira_push.py", *args, extra_env=self.creds)

    def state_j(self):
        return json.loads((self.state / "jira_state.json").read_text(encoding="utf-8"))

    def two(self):
        self.create(task(request_message_id="m1", title="Mở"), task(request_message_id="m2", title="Xong", status_hint="resolved_in_chat", source_message_ids=["m2"]))

    def test_resolved_duoc_tao_roi_chuyen_done_open_thi_khong(self):
        j = self.jira()
        self.two()
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(j.posts("/rest/api/3/issue")), 2)                         # cả hai được tạo
        trs = j.posts("/transitions")
        self.assertEqual(len(trs), 1)                                                   # chỉ ticket resolved bị chuyển
        self.assertEqual(trs[0][1], {"transition": {"id": "31"}})
        self.assertEqual(trs[0][0], "/rest/api/3/issue/KAN-2/transitions")             # KAN-2 = ticket thứ hai (Xong)
        st = self.state_j()
        self.assertTrue(st["g1:m2"]["done"] and "done" not in st["g1:m1"])
        self.assertIn("chuyển Done 1", r.stdout)
        self.assertEqual(len(j.posts("/comment")), 1)                                   # ghi chú lý do chuyển Done

    def test_chay_lai_khong_tao_trung_va_khong_chuyen_done_lan_nua(self):
        j = self.jira()
        self.two()
        self.push()
        before = len(j.calls)
        r = self.push()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(j.posts("/rest/api/3/issue")), 2)
        self.assertEqual(len(j.posts("/transitions")), 1)
        self.assertEqual([c for c in j.calls[before:] if c[0] == "POST"], [])

    def test_ticket_da_day_luc_open_sau_do_resolved_thi_chuyen_done(self):
        j = self.jira()
        self.create(task(request_message_id="m1", title="Mở"))
        self.push()
        self.assertEqual(len(j.posts("/transitions")), 0)
        ct.append(self.state / "ledger.jsonl", {**self.ledger()["g1:m1"], "status": "resolved", "at": "2026-11-16T05:00:00+00:00"})
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p for p, _ in j.posts("/transitions")], ["/rest/api/3/issue/KAN-1/transitions"])
        self.assertTrue(self.state_j()["g1:m1"]["done"])

    def test_issue_co_san_cua_nguoi_khac_khong_bi_chuyen_done(self):
        j = self.jira()
        self.create(task(request_message_id="m1", title="Xong", status_hint="resolved_in_chat", source_message_ids=["m1"]))
        (self.state / "jira_state.json").write_text(json.dumps({"g1:m1": {"issue": "KAN-9", "comments": 0, "existing": True, "at": "2026-11-16T00:00:00+07:00"}}), encoding="utf-8")
        self.assertEqual(self.push().returncode, 0)
        self.assertEqual(j.posts("/transitions"), [])

    def test_workflow_bat_di_vong_thi_di_qua_in_progress_roi_done(self):
        j = self.jira(two_step=True)
        self.create(task(request_message_id="m1", title="Xong", status_hint="resolved_in_chat", source_message_ids=["m1"]))
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([b["transition"]["id"] for _, b in j.posts("/transitions")], ["11", "31"])
        self.assertTrue(self.state_j()["g1:m1"]["done"])

    def test_issue_da_done_san_thi_khong_goi_transition(self):
        j = self.jira(already_done=True)
        self.create(task(request_message_id="m1", title="Xong", status_hint="resolved_in_chat", source_message_ids=["m1"]))
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(j.posts("/transitions"), [])
        self.assertTrue(self.state_j()["g1:m1"]["done"])
        self.assertEqual(j.posts("/comment"), [])                                        # không ghi chú thừa

    def test_khong_co_transition_toi_done_thi_canh_bao_mot_lan_khong_tinh_loi_va_khong_thu_lai(self):
        j = self.jira(no_done_transition=True)
        self.create(task(request_message_id="m1", title="Xong", status_hint="resolved_in_chat", source_message_ids=["m1"]))
        r = self.push()
        self.assertEqual(r.returncode, 0)                                                # không làm job cron báo lỗi
        self.assertIn("CẢNH BÁO: KAN-1 không chuyển được sang Done (không có transition nào đi tới Done)", r.stdout)
        self.assertTrue(self.state_j()["g1:m1"].get("done_skip"))
        self.assertNotIn("done", self.state_j()["g1:m1"])
        n = len(j.calls)
        r2 = self.push()
        self.assertEqual(r2.returncode, 0)
        self.assertNotIn("CẢNH BÁO", r2.stdout)
        self.assertFalse([c for c in j.calls[n:] if "transitions" in c[1]])               # lượt sau không hỏi transition nữa
        self.assertEqual(len(j.posts("/rest/api/3/issue")), 1)

    def test_site_phu_dung_cau_hinh_khoa_va_trang_thai_rieng(self):
        j = self.jira()                                                                  # site chính trỏ tới máy chủ giả #1
        j2 = FakeJiraFull()
        self.addCleanup(j2.close)
        cfg = json.loads((self.state / "cs_config.json").read_text(encoding="utf-8"))
        cfg["jira_sites"] = {"csa": {"enabled": True, "mode": "task", "project_key": "SUP", "base_url": j2.url}}
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.two()
        # thiếu khoá riêng của site phụ: im lặng, không gọi gì
        r = self.script("jira_push.py", "--site", "csa", extra_env=self.creds)
        self.assertEqual((r.returncode, j2.calls), (0, []))
        env = {**self.creds, "JIRA_CSA_EMAIL": "b@example.com", "JIRA_CSA_API_TOKEN": "khong-that"}
        r = self.script("jira_push.py", "--site", "csa", extra_env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(j2.posts("/rest/api/3/issue")), 2)
        self.assertEqual(j.calls, [])                                                   # site chính không bị đụng
        self.assertEqual(len(j2.posts("/transitions")), 1)                              # resolved cũng chuyển Done ở site phụ
        self.assertTrue((self.state / "jira_state_csa.json").exists())
        self.assertFalse((self.state / "jira_state.json").exists())                     # trạng thái tách riêng
        # site chính vẫn tạo đủ, độc lập với site phụ
        self.assertEqual(self.push().returncode, 0)
        self.assertEqual(len(j.posts("/rest/api/3/issue")), 2)
        self.assertEqual(len(j2.posts("/rest/api/3/issue")), 2)

    def test_default_assignee_me_gan_cho_chu_token(self):
        j = self.jira()
        cfg = json.loads((self.state / "cs_config.json").read_text(encoding="utf-8"))
        cfg["jira"]["default_assignee"] = "me"
        for st in cfg.get("staff", {}).values():
            st.pop("jira", None)                                                         # nhân viên chưa có accountId => dùng default_assignee
        (self.state / "cs_config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.create(task(request_message_id="m1", title="Mở"))
        self.assertEqual(self.push().returncode, 0)
        self.assertEqual(len([1 for mt, pa, _ in j.calls if mt == "GET" and pa == "/rest/api/3/myself"]), 1)
        puts = [(pa, b) for mt, pa, b in j.calls if mt == "PUT" and pa.endswith("/assignee")]
        self.assertEqual(puts, [("/rest/api/3/issue/KAN-1/assignee", {"accountId": "me-123"})])

    def test_mo_ta_task_co_dinh_dang_nhan_manh_viec_can_lam(self):
        j = self.jira()
        self.create(task(request_message_id="m1", title="Mở", unit="Phường A"))
        self.assertEqual(self.push().returncode, 0)
        body = next(b for mt, pa, b in j.calls if mt == "POST" and pa == "/rest/api/3/issue")
        doc = body["fields"]["description"]
        self.assertEqual(doc["type"], "doc")
        kinds = [n["type"] for n in doc["content"]]
        self.assertEqual(kinds[0], "panel")                                              # khung lưu ý đầu tiên: việc cần làm
        self.assertIn("bulletList", kinds)
        self.assertIn("blockquote", kinds)

        def walk(n):
            yield n
            for c in n.get("content", []):
                yield from walk(c)
        nodes = list(walk(doc))
        bold = [n["text"] for n in nodes if n.get("type") == "text" and any(mk["type"] == "strong" for mk in n.get("marks", []))]
        self.assertIn("Việc cần làm: ", bold)
        self.assertIn("Đơn vị: ", bold)
        self.assertIn("Phường A", bold)                                                   # đơn vị (đã biết) in đậm
        flat = " ".join(n["text"] for n in nodes if n.get("type") == "text")
        self.assertIn("m1", flat)                                                         # mã tin neo vẫn là chữ thường để chống trùng tìm được
        self.assertIn("zalo-ticket-key: g1:m1", flat.replace("  ", " "))
        self.assertEqual([h for h in nodes if h.get("type") == "heading"][0]["content"][0]["text"], "Thông tin yêu cầu")

    def test_don_vi_chua_ro_thi_khung_canh_bao_va_chu_do(self):
        j = self.jira()
        self.create(task(request_message_id="m1", title="Mở", unit=""))
        self.assertEqual(self.push().returncode, 0)
        doc = next(b for mt, pa, b in j.calls if mt == "POST" and pa == "/rest/api/3/issue")["fields"]["description"]
        self.assertEqual(doc["content"][0]["attrs"]["panelType"], "warning")
        self.assertIn("textColor", json.dumps(doc))

    def test_dry_run_khong_goi_gi_ghi(self):
        j = self.jira()
        self.two()
        r = self.push("--dry-run")
        self.assertIn("sẽ tạo rồi chuyển Done: Xong", r.stdout)
        self.assertIn("sẽ tạo: Mở", r.stdout)
        self.assertEqual([c for c in j.calls if c[0] == "POST"], [])

    def test_not_ours_va_closed_van_khong_day(self):
        j = self.jira()
        self.two()
        self.script("mark_ticket.py", "g1:m1", "not_ours")
        self.script("mark_ticket.py", "g1:m2", "closed")
        self.assertEqual(self.push().returncode, 0)
        self.assertEqual(j.posts("/rest/api/3/issue"), [])


if __name__ == "__main__":
    unittest.main()
