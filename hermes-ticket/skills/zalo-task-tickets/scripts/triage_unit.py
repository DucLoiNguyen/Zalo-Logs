#!/usr/bin/env python3
"""Gợi ý `unit` (đơn vị của khách) từ tên tệp + tin chữ đi kèm. Chỉ là gợi ý đưa vào prompt, model quyết định.

Tìm các từ khoá đơn vị (xã/phường/thị trấn/Sở/UBND/THPT/THCS/Trường/Bệnh viện/Bảo tàng/Đảng uỷ...) rồi lấy tối đa 5 từ viết hoa đi liền sau,
dừng ở dấu câu hay từ thường gặp ("với", "nhờ", "TPHCM", "danh sách"...). Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import re
import unicodedata

NEAR_SECONDS = 180
EXT_RE = re.compile(r"\.(xlsx|xls|docx|doc|pdf|ods|csv|zip|rar)\b", re.I)
PREFIXES = ("thi tran", "phuong", "xa", "p", "so", "ubnd", "thpt", "thcs", "tieu hoc", "truong", "benh vien", "bao tang", "dang uy")
PREFIX_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(PREFIXES) + r")(?![a-z0-9])\.?")
TOKEN_RE = re.compile(r"[ \t]+([^\s,;:()\[\]\-–—.|/]+)")
# từ dừng cứng (đã bỏ dấu): dù viết hoa vẫn không phải tên đơn vị (tên tệp chung: "Danh sách đăng ký AI TPHCM...")
STOP = {"tphcm", "hcm", "tp", "danh", "sach", "ds", "dang", "ai", "cong", "vu", "dky", "dk", "tk", "dot", "tai", "khoan", "nen", "tang",
        "ubnd", "thpt", "thcs", "so", "xa", "phuong", "truong"}
# tên dài (Bảo tàng tỉnh X, Bệnh viện đa khoa X): cho phép vài từ mô tả viết thường đi trước tên riêng
LONG = {"ubnd", "thpt", "thcs", "so", "truong", "benh vien", "bao tang", "dang uy", "tieu hoc"}
DESC = {"tinh", "thanh", "pho", "quan", "huyen", "da", "khoa", "cap", "dai", "hoc", "nhan", "dan"}
CANON = {"so": "Sở", "xa": "Xã", "phuong": "Phường", "p": "Phường", "thi tran": "Thị trấn"}


def fold(s: str) -> str:
    """Bỏ dấu, thường hoá, GIỮ NGUYÊN độ dài (để dùng chỉ số trên chuỗi gốc)."""
    out = []
    for c in s:
        d = "".join(x for x in unicodedata.normalize("NFD", "d" if c in "đĐ" else c.lower()) if unicodedata.category(x) != "Mn")
        out.append(d[:1] or " ")
    return "".join(out)


def clean(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = re.sub(r"\[(?:Tập tin|Hình ảnh|Ảnh)[^\]]*?:?\s*", " ", t).replace("]", " ").replace("📎", " ").replace("_", " ").replace("⏎", " ")
    return EXT_RE.sub(" ", t)


def _from(text: str) -> str | None:
    s = clean(text)
    f = fold(s)
    for m in PREFIX_RE.finditer(f):
        key = m.group(1)
        word = s[m.start(1):m.end(1)]
        if key == "so" and word not in ("Sở", "SỞ"):
            continue
        pos, toks = m.end(), []
        while len(toks) < 5:
            t = TOKEN_RE.match(s, pos)
            if not t:
                break
            tok = t.group(1)
            ft = fold(tok)
            cap = tok[:1].isupper() or tok[:1].isdigit()
            if ft in STOP and cap:
                break
            if not cap and not (key in LONG and ft in DESC):    # tên riêng phải viết hoa: loại "xã em tạo", "phòng nào"
                break
            toks.append(tok)
            pos = t.end()
        while toks and not (toks[-1][:1].isupper() or toks[-1][:1].isdigit()):
            toks.pop()
        if not toks:
            continue
        head = CANON.get(key) or s[m.start(1):m.end(1)]
        if all(not c.islower() for c in "".join(toks)):                 # tên tệp viết HOA hết -> Viết Hoa Từng Từ
            toks = [t if not re.search(r"[aeiouy]", fold(t)) else t.capitalize() for t in toks]   # giữ nguyên chữ viết tắt (VHTT, NNMT)
            head = head.capitalize() if head.isupper() and re.search(r"[aeiouy]", fold(head)) and len(head) > 4 else head
        return " ".join([head, *toks])
    return None


def suggest_unit(texts: list[str]) -> str | None:
    """Gợi ý đầu tiên tìm được trong các văn bản (tin chữ hay tên tệp), theo thứ tự đưa vào."""
    for t in texts:
        u = _from(t)
        if u:
            return u
    return None


def cluster_texts(msgs: list[dict], anchor: str, members: list[str] | None = None) -> list[str]:
    """Văn bản của các tin trong CỤM yêu cầu: tin mỏ neo + tin thành viên + tin cùng người gửi lệch ≤3 phút (chữ và tệp)."""
    by_id = {m["id"]: m for m in msgs}
    ids = [anchor, *(members or [])]
    base = [by_id[i] for i in ids if i in by_id]
    out = [m["text"] for m in base]
    for b in base[:1]:
        for m in msgs:
            if m["id"] not in ids and m["name"] == b["name"] and m.get("when") and b.get("when") and \
                    abs((m["when"] - b["when"]).total_seconds()) <= NEAR_SECONDS:
                out.append(m["text"])
    return out


def unit_hint(msgs: list[dict], anchor: str, members: list[str] | None = None) -> str | None:
    return suggest_unit(cluster_texts(msgs, anchor, members))


if __name__ == "__main__":
    import sys
    print(suggest_unit(sys.argv[1:]) or "(không có gợi ý)")
