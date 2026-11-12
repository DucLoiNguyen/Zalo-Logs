#!/usr/bin/env python3
"""Kiểm tra phủ bằng mã (không dùng model): tin nào của KHÁCH trong TIN MỚI trông như một yêu cầu mà chưa có ticket phủ tới.

Model nhỏ thỉnh thoảng trả mảng rỗng dù cửa sổ có yêu cầu rõ ràng, mà mảng rỗng vẫn làm dời cursor nên yêu cầu mất luôn.
Hàm ở đây tìm ra các tin bị bỏ sót để triage_llm.py hỏi lại (cùng model, rồi model dự phòng) và cuối cùng tự tạo ticket
"[Cần xem]" confidence 0.4 (thành low_confidence, người xem lại) trước khi dời cursor.

Một tin KH "cần phủ" khi: có 📎, hoặc có SĐT/email, hoặc có từ khoá yêu cầu (tạo tài khoản, cấp, đăng ký, mật khẩu, lỗi...).
Được miễn: tin của NV/hệ thống, người gửi Misa/luatvietnam, tin đã thu hồi.
"Phủ tới" = mã tin là mỏ neo hoặc nằm trong source_message_ids của một ticket, hoặc được model khẳng định không phải yêu cầu
(not_requests / nhãn not_ours). Tin chữ (không có 📎) còn được coi là phủ nếu cùng người gửi, trong vòng 3 phút với một tin đã
được phủ (lời nhắn đi kèm tệp); tin có 📎 thì PHẢI được phủ trực tiếp, vì mỗi tệp là một ticket.
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from triage_memory import MISA_RE

MSG_RE = re.compile(r"^\[([^\]\s]+)\] ([^|]*)\| (NV|KH|HT) ([^:]*): (.*)$")
TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# so khớp trên văn bản đã bỏ dấu, thường hoá
KEYWORD_RE = re.compile(r"\b(tao tai khoan|tao tk|tai khoan|tk|cap|cap lai|dang ky|dang ki|mat khau|mk|password|loi|"
                        r"khong vao duoc|khong dang nhap|khong truy cap|reset|gia han|bo sung|cau hinh)\b")
NEAR_SECONDS = 180


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def parse_messages(window: str) -> list[dict]:
    """Các dòng tin trong cửa sổ: {id, role, name, text, new, when}. `new` = nằm trong phần TIN MỚI."""
    out, in_new = [], False
    for line in window.splitlines():
        if line.startswith("## TIN MỚI"):
            in_new = True
            continue
        m = MSG_RE.match(line)
        if not m:
            continue
        t = TIME_RE.search(m.group(2))
        out.append({"id": m.group(1), "role": m.group(3), "name": m.group(4).strip(), "text": m.group(5), "new": in_new,
                    "when": datetime.strptime(t.group(1), "%Y-%m-%d %H:%M") if t else None})
    return out


def member_ids(window: str) -> dict[str, str]:
    """tên -> id Zalo, từ dòng '# Thành viên'."""
    for line in window.splitlines():
        if line.startswith("# Thành viên"):
            res = {}
            for p in line.partition(": ")[2].split("; "):
                m = re.match(r"(?:NV|KH|HT) (\S+) = (.*)", p)
                if m:
                    res[m.group(2).strip()] = m.group(1)
            return res
    return {}


def is_misa(name: str) -> bool:
    return bool(MISA_RE.search(name or ""))


MISA_TEXT_RE = re.compile(r"luatvietnam|misa|ai luat viet nam")


def misa_only_text(text: str) -> bool:
    """Tin nhắc riêng Misa/luatvietnam (tên miền luatvietnammisa.*, "AI Luật Việt Nam") mà không nhắc Clex: không phải việc của team,
    kể cả khi khách tag nhân viên CMC."""
    f = fold(text or "")
    return bool(MISA_TEXT_RE.search(f)) and "clex" not in f


def needs_cover(m: dict) -> str | None:
    """Lý do tin này phải được phủ ('tệp' | 'SĐT/email' | 'từ khoá'), hoặc None nếu được miễn / không giống yêu cầu."""
    if not m["new"] or m["role"] != "KH" or is_misa(m["name"]) or "[ĐÃ THU HỒI]" in m["text"]:
        return None
    if misa_only_text(m["text"]):
        return None
    if "📎" in m["text"]:
        return "tệp"
    if PHONE_RE.search(m["text"]) or EMAIL_RE.search(m["text"]):
        return "SĐT/email"
    if KEYWORD_RE.search(fold(m["text"])) and not m.get("internal"):
        return "từ khoá"
    return None


def kh_ids_to_label(msgs: list[dict]) -> list[str]:
    """Mọi tin KH (không phải Misa, chưa thu hồi) trong TIN MỚI: danh sách phải gắn nhãn ở bước 1."""
    return [m["id"] for m in msgs if m["new"] and m["role"] == "KH" and not is_misa(m["name"]) and "[ĐÃ THU HỒI]" not in m["text"]]


def covered_ids(tasks: list[dict]) -> set[str]:
    out: set[str] = set()
    for t in tasks:
        out.add(str(t.get("request_message_id")))
        out.update(str(i) for i in t.get("source_message_ids") or [])
    return out


def uncovered(msgs: list[dict], tasks: list[dict], exempt: set[str] | None = None) -> list[dict]:
    """Tin cần phủ mà chưa có ticket phủ tới (xem docstring module)."""
    cov = covered_ids(tasks) | (exempt or set())
    covered_msgs = [m for m in msgs if m["id"] in cov]
    missing = []
    for m in msgs:
        reason = needs_cover(m)
        if not reason or m["id"] in cov:
            continue
        if reason != "tệp" and any(c["name"] == m["name"] and c["when"] and m["when"] and
                                   abs((c["when"] - m["when"]).total_seconds()) <= NEAR_SECONDS for c in covered_msgs):
            continue  # lời nhắn đi kèm một tệp/yêu cầu đã có ticket
        missing.append({**m, "reason": reason})
    return missing


UNIT_PREFIX_RE = re.compile(r"^(thi tran|phuong|xa|so|ubnd|thpt|thcs|truong|benh vien|bao tang|dang uy|tieu hoc|mam non)\s+")


def _core(name: str) -> str:
    """Tên đơn vị bỏ tiền tố (Xã/Phường/Sở...), không dấu: 'Xã Bình Yên' -> 'binh yen'."""
    f = " ".join(fold(name or "").split())
    return UNIT_PREFIX_RE.sub("", f)


def resolve_rule(prev: dict, staff: dict, msgs: list[dict], key: str, recent: int = 10) -> str | None:
    """Tin nhân viên `staff` có đủ căn cứ để đóng ticket `key` (bản ghi ledger `prev`) không? Trả tên luật hoặc None:
      (a) tin nêu đúng đơn vị hoặc tên khách của ticket;
      (b) @tag đúng khách;
      (c) trong `recent` tin gần nhất trước tin NV chỉ liên quan đúng MỘT ticket đang mở (số ứng viên do hàm gọi tính: xem resolve_unique).
    Hàm này xét (a) và (b); (c) do resolve_unique xét vì cần so với các ticket khác."""
    text = " ".join(fold(staff["text"]).split())
    customer = " ".join(fold(prev.get("customer") or "").split())
    core = _core(prev.get("unit") or "")
    if customer and "@" + customer in text:
        return "tag đúng khách"
    if (core and len(core) >= 4 and core in text) or (customer and len(customer) >= 4 and customer in text):
        return "nêu đúng đơn vị/tên khách"
    return None


def resolve_unique(opens: dict[str, dict], staff: dict, msgs: list[dict], recent: int = 10) -> str | None:
    """Luật (c): trong `recent` tin ngay trước tin NV (kể cả chính nó), chỉ có đúng MỘT ticket đang mở được nhắc tới
    (tin neo/tin nguồn của ticket nằm trong đó, hoặc khách của ticket có tin trong đó). Trả khoá ticket đó hoặc None."""
    idx = next((i for i, m in enumerate(msgs) if m["id"] == staff["id"]), None)
    if idx is None:
        return None
    window = msgs[max(0, idx - recent + 1): idx + 1]
    ids = {m["id"] for m in window}
    names = {" ".join(fold(m["name"]).split()) for m in window if m["role"] == "KH"}
    hit = []
    for key, prev in opens.items():
        mine = {key.split(":", 1)[-1], *map(str, prev.get("seen_ids") or [])}
        cust = " ".join(fold(prev.get("customer") or "").split())
        if mine & ids or (cust and cust in names):
            hit.append(key)
    return hit[0] if len(hit) == 1 else None


def synth_summary(missing: list[dict], group: str, cfg: dict, mode: str) -> dict:
    """Nhóm nội bộ: MỘT ticket [Cần xem] tổng hợp cho cả cửa sổ (thay vì mỗi tin sót một ticket): liệt kê mã tin + trích ≤100 ký tự."""
    cats = cfg.get("categories", {})
    category = "question" if "question" in cats else (next(iter(cats), None))
    lines = [f"- {m['id']} ({m['name']}, dấu hiệu: {m['reason']}): {excerpt(m['text'], 100)}" for m in missing]
    first = missing[0]
    return {"request_message_id": first["id"], "group_id": group, "title": f"[Cần xem] {len(missing)} tin nhân viên chưa được phân loại"[:100],
            "description": "Hệ thống không tự phân loại được các tin sau trong nhóm nội bộ; cần người xem lại.\n" + "\n".join(lines),
            "category": category, "priority": None, "customer_name": first["name"], "customer_zalo_id": None, "attachments": [],
            "kind": "internal" if mode == "internal" else "cs", "source_message_ids": [m["id"] for m in missing],
            "quote": excerpt(first["text"], 100), "status_hint": "open", "confidence": 0.4, "unit": None, "platform": None}


def drop_misa(tasks: list[dict], msgs: list[dict]) -> tuple[list[dict], int]:
    """Chốt chặn cuối: model có thể tạo ticket (hoặc gộp tin) cho tin riêng Misa ở bất kỳ vòng nào (kể cả vòng hỏi lại).
    Ticket neo vào tin như vậy bị bỏ; tin đó cũng bị gỡ khỏi source_message_ids của ticket khác. Trả (danh sách mới, số mục đã loại)."""
    bad = {m["id"] for m in msgs if m["role"] == "KH" and misa_only_text(m["text"])}
    out, n = [], 0
    for t in tasks:
        if str(t.get("request_message_id")) in bad:
            n += 1
            continue
        src = [i for i in t.get("source_message_ids") or [] if str(i) in bad]
        if src:
            n += len(src)
            t["source_message_ids"] = [i for i in t["source_message_ids"] if str(i) not in bad]
        out.append(t)
    return out, n


def merge_split(tasks: list[dict], msgs: list[dict]) -> tuple[list[dict], int]:
    """Gộp ticket bị model tách: cùng người gửi (tin neo), tin neo cách nhau ≤3 phút (so với bất kỳ tin nào đã gộp), cùng loại và cùng đơn vị
    (so không dấu/hoa thường; một bên chưa có đơn vị thì coi là cùng) => giữ ticket có tin neo sớm nhất, tin còn lại vào source_message_ids. Trạng thái: open nếu một trong hai là open.
    Chỉ gộp ticket có tin neo trong cửa sổ; ticket [Cần xem] (không có đơn vị) và cập nhật ticket cũ không bị gộp. Trả (danh sách mới, số ticket đã gộp)."""
    by_id = {m["id"]: m for m in msgs}
    key = lambda t: fold(str(t.get("unit") or "")).strip()  # noqa: E731
    order = sorted(tasks, key=lambda t: (by_id[str(t["request_message_id"])]["when"] or datetime.max) if str(t.get("request_message_id")) in by_id else datetime.max)
    kept: list[dict] = []
    stamps: dict[int, list[tuple]] = {}   # id(task kept) -> [(người gửi, giờ)]
    merged = 0
    for t in order:
        m = by_id.get(str(t.get("request_message_id")))
        target = None
        if m and m["when"] and not str(t.get("title", "")).startswith("[Cần xem]"):
            for k in kept:
                if k.get("category") == t.get("category") and (key(k) == key(t) or not key(k) or not key(t)) and                         not str(k.get("title", "")).startswith("[Cần xem]") and any(
                        n == m["name"] and abs((w - m["when"]).total_seconds()) <= NEAR_SECONDS for n, w in stamps.get(id(k), [])):
                    target = k
                    break
        if target is None:
            kept.append(t)
            if m and m["when"]:
                stamps[id(t)] = [(m["name"], m["when"])]
            continue
        merged += 1
        if not target.get("unit") and t.get("unit"):
            target["unit"] = t["unit"]
        stamps[id(target)].append((m["name"], m["when"]))
        target["source_message_ids"] = sorted(set(map(str, target.get("source_message_ids") or [])) | set(map(str, t.get("source_message_ids") or [])) |
                                              {str(t["request_message_id"])})
        if t.get("status_hint") != "resolved_in_chat" or target.get("status_hint") != "resolved_in_chat":
            target["status_hint"] = "open"
        if t.get("description") and t["description"] not in (target.get("description") or ""):
            target["description"] = (target.get("description") or "") + " / " + t["description"]
        if t.get("attachments"):
            target["attachments"] = [a for a in (target.get("attachments") or []) if a.get("name") or a.get("path")] + list(t["attachments"])
    return kept, merged


def excerpt(text: str, n: int = 120) -> str:
    t = re.sub(r"\s+", " ", text.replace("⏎", " ")).strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def synth_task(m: dict, group: str, cfg: dict, mode: str, members: dict[str, str]) -> dict:
    """Ticket dự phòng cho tin model không phân loại được sau mọi lần hỏi: confidence 0.4 => low_confidence, cần người xem."""
    cats = cfg.get("categories", {})
    category = "question" if "question" in cats else (next(iter(cats), None))
    ex = excerpt(m["text"], 200)
    return {"request_message_id": m["id"], "group_id": group, "title": ("[Cần xem] " + excerpt(m["text"], 70))[:100],
            "description": f"Hệ thống không tự phân loại được tin này sau nhiều lần hỏi model; cần người xem lại.\nTin ({m['name']}): {ex}",
            "category": category, "priority": None, "customer_name": m["name"], "customer_zalo_id": members.get(m["name"]),
            "source_message_ids": [m["id"]], "quote": ex, "status_hint": "open", "confidence": 0.4,
            "kind": "internal" if mode == "internal" else "cs", "unit": None, "platform": None}
