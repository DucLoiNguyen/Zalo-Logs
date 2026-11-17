#!/usr/bin/env python3
"""Trí nhớ có kiểm soát cho triage_llm.py. Mọi thứ nằm trong <state>/memory/ (người dùng sửa tay được):

  notes/<group_id>.md   sổ tay nhóm: model đề xuất `notes_update`, code ghi thêm ngày, giới hạn 1.500 ký tự
  senders.json          danh bạ người gửi: dựng từ tickets.jsonl + ledger, cập nhật sau mỗi ticket tạo/cập nhật (không dùng model)
  (ví dụ sửa sai không cần file riêng: lấy trực tiếp từ ledger.jsonl, trạng thái not_ours / duplicate / wrong do mark_ticket.py ghi)

Tổng phần trí nhớ đưa vào prompt ≤ ~2.500 token (ước lượng ký tự / 3); vượt thì cắt danh bạ, rồi ví dụ, rồi sổ tay.
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import create_tickets

NOTES_MAX_CHARS = 1500
MEMORY_MAX_TOKENS = 2500
SENDER_LINES_MAX = 12
EXAMPLES_MAX = 5
EXAMPLES_MIN = 3  # nhóm ít ví dụ riêng thì mượn ví dụ nhóm khác (quy tắc Misa dùng chung mọi nhóm)
MISA_RE = re.compile(r"misa|luatvietnam|luật\s*việt\s*nam", re.I)
MARK_TEXT = {"not_ours": "không tạo ticket vì là yêu cầu của Misa", "duplicate": "ticket trùng, không tạo mới",
             "wrong": "ticket tạo sai, không phải yêu cầu thật"}


def est_tokens(text: str) -> int:
    """Ước lượng thô cho tiếng Việt: ~3 ký tự / token (số thật lấy từ `usage` của model)."""
    return (len(text) + 2) // 3


def memory_dir(state: Path, dry_run: bool) -> Path:
    """Thư mục trí nhớ. --dry-run luôn dùng state dry (zalo-tickets-dry) để không làm bẩn trí nhớ thật."""
    if dry_run and not state.name.endswith("-dry"):
        return state.parent / f"{state.name}-dry" / "memory"
    return state / "memory"


# ------------------------------------------------------------------ sổ tay nhóm
def read_notes(mem: Path, group: str) -> str:
    p = mem / "notes" / f"{group}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _norm(line: str) -> str:
    line = re.sub(r"^\s*[-*•]\s*", "", line.strip())
    line = re.sub(r"^\[\d{4}-\d{2}-\d{2}\]\s*", "", line)
    return re.sub(r"\s+", " ", line).lower()


def merge_notes(old: str, update: str, today: str) -> str:
    """Gộp dòng mới (thêm ngày) vào sổ tay cũ, bỏ dòng trùng, giữ ≤ NOTES_MAX_CHARS bằng cách bỏ dòng cũ nhất."""
    lines = [l.rstrip() for l in old.splitlines() if l.strip()]
    seen = {_norm(l) for l in lines}
    for raw in update.splitlines():
        text = re.sub(r"^\s*[-*•]\s*", "", raw.strip()).strip()
        if text and _norm(text) not in seen:
            seen.add(_norm(text))
            lines.append(f"- [{today}] {text}")
    while len("\n".join(lines)) > NOTES_MAX_CHARS and len(lines) > 1:
        lines.pop(0)
    if lines and len(lines[0]) > NOTES_MAX_CHARS:
        lines[0] = lines[0][:NOTES_MAX_CHARS].rstrip()
    return "\n".join(lines) + "\n" if lines else ""


def save_notes(mem: Path, group: str, update: str, today: str) -> bool:
    """Chỉ ghi khi `update` không rỗng và kết quả gộp khác nội dung cũ. Trả về True nếu đã ghi."""
    if not (update or "").strip():
        return False
    old = read_notes(mem, group)
    merged = merge_notes(old, update, today)
    if merged.strip() == old.strip():
        return False
    p = mem / "notes" / f"{group}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(merged, encoding="utf-8")
    return True


# ------------------------------------------------------------------ danh bạ người gửi
def unit_from_title(title: str | None) -> str | None:
    m = re.match(r"\s*\[([^\]]+)\]", title or "")
    return m.group(1).strip() if m else None


def role_of(uid: str, name: str | None, cfg: dict) -> str:
    if uid in cfg.get("staff", {}):
        return "NV"
    if MISA_RE.search(name or ""):
        return "MISA"
    return "KH"


def _touch(sd: dict, cid: str, name, unit, platform, seen) -> None:
    e = sd.setdefault(cid, {})
    for k, v in (("name", name), ("unit", unit), ("platform", platform)):
        if v:
            e[k] = v
    if seen and str(seen) > str(e.get("last_seen", "")):
        e["last_seen"] = str(seen)[:10]


def build_senders(state: Path) -> dict:
    """Dựng danh bạ từ toàn bộ tickets.jsonl (+ngày từ ledger). Ticket cũ chưa có `unit`: lấy từ tiền tố `[Đơn vị]` của tiêu đề."""
    led = create_tickets.load_ledger(state)
    sd: dict = {}
    for row in create_tickets.load_jsonl(state / "tickets.jsonl"):
        cid = row.get("customer_zalo_id")
        if row.get("update") or not cid:
            continue
        seen = led.get(row.get("key"), {}).get("at") or row.get("at")
        _touch(sd, str(cid), row.get("customer_name"), row.get("unit") or unit_from_title(row.get("title")),
               row.get("platform"), seen)
    return sd


def load_senders(mem: Path, state: Path, save: bool = True) -> dict:
    p = mem / "senders.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    sd = build_senders(state)
    if save:
        write_senders(mem, sd)
    return sd


def write_senders(mem: Path, sd: dict) -> None:
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "senders.json").write_text(json.dumps(sd, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def update_senders(mem: Path, state: Path, tasks: list[dict]) -> int:
    """Sau khi apply_group thành công: cập nhật người gửi của các ticket vừa tạo/cập nhật (khoá có trong ledger, trạng thái open)."""
    led = create_tickets.load_ledger(state)
    sd = load_senders(mem, state)
    n = 0
    for t in tasks:
        cid = t.get("customer_zalo_id")
        key = f"{t.get('group_id')}:{t.get('request_message_id')}"
        rec = led.get(key)
        if not cid or not rec or rec.get("status") != "open":
            continue
        _touch(sd, str(cid), t.get("customer_name"), t.get("unit") or unit_from_title(t.get("title")), t.get("platform"), rec.get("at"))
        n += 1
    if n:
        write_senders(mem, sd)
    return n


MEMBER_RE = re.compile(r"(?:NV|KH|HT) (\S+) = (.*)")


def window_members(window: str) -> list[tuple[str, str]]:
    """(id, tên) những người có trong dòng '# Thành viên' của cửa sổ tin."""
    for line in window.splitlines():
        if line.startswith("# Thành viên"):
            return [(m.group(1), m.group(2).strip()) for p in line.partition(": ")[2].split("; ") if (m := MEMBER_RE.match(p))]
    return []


def sender_lines(sd: dict, window: str, cfg: dict) -> list[str]:
    out = []
    for uid, name in window_members(window):
        role = role_of(uid, name, cfg)
        if role == "NV":
            continue  # nhãn NV đã có trong cửa sổ
        e = sd.get(uid, {})
        if role == "MISA":
            out.append(f"- {uid} | {name} | nhân viên Misa (không tạo ticket từ tin của họ)")
        elif e.get("unit"):
            out.append(f"- {uid} | {name} | đơn vị: {e['unit']}" + (f" | thấy lần cuối {e['last_seen']}" if e.get("last_seen") else ""))
    return out[:SENDER_LINES_MAX]


# ------------------------------------------------------------------ ví dụ sửa sai
def example_lines(state: Path, group: str) -> list[str]:
    led = create_tickets.load_ledger(state)
    marked = sorted((r for r in led.values() if r.get("status") in MARK_TEXT), key=lambda r: str(r.get("at", "")), reverse=True)
    mine = [r for r in marked if str(r.get("key", "")).startswith(f"{group}:")][:EXAMPLES_MAX]
    if len(mine) < EXAMPLES_MIN:
        mine += [r for r in marked if r not in mine][: EXAMPLES_MIN - len(mine)]
    out = []
    for r in mine:
        note = f" ({r['note']})" if r.get("note") else ""
        who = f", khách {r['customer']}" if r.get("customer") else ""
        out.append(f"- yêu cầu \"{str(r.get('title', ''))[:100]}\"{who} → {MARK_TEXT[r['status']]}{note}")
    return out


# ------------------------------------------------------------------ ghép vào prompt
def build_memory(mem: Path, state: Path, cfg: dict, group: str, window: str, save: bool = True) -> tuple[str, dict]:
    """Phần trí nhớ của prompt (rỗng nếu chưa có gì) + thống kê. Vượt MEMORY_MAX_TOKENS thì cắt danh bạ, ví dụ, rồi sổ tay."""
    notes = [l for l in read_notes(mem, group).splitlines() if l.strip()]
    if os.environ.get("TRIAGE_NOTES_READ", "0") != "1":
        # mặc định KHÔNG đưa sổ tay vào prompt (vẫn ghi): sổ tay kiểu "X vừa hoàn thành danh sách Y" làm model kết luận hết việc và trả mảng rỗng
        # (đã đo ở nhóm nội bộ: Haiku từ 6 task xuống 0). Đặt TRIAGE_NOTES_READ=1 để đọc lại.
        notes = []
    senders = sender_lines(load_senders(mem, state, save=save), window, cfg)
    examples = example_lines(state, group)

    def render() -> str:
        parts = []
        if notes:
            parts.append("## SỔ TAY NHÓM (ghi tự động từ các lượt trước, có thể đã cũ)\n" + "\n".join(notes))
        if senders:
            parts.append("## NGƯỜI GỬI TRONG CỬA SỔ (id | tên | thông tin đã biết)\n" + "\n".join(senders))
        if examples:
            parts.append("## VÍ DỤ ĐÃ ĐƯỢC NGƯỜI DÙNG SỬA (làm theo)\n" + "\n".join(examples))
        return "\n\n".join(parts)

    text = render()
    while est_tokens(text) > MEMORY_MAX_TOKENS and (senders or examples or len(notes) > 1):
        if senders:
            senders.pop()
        elif examples:
            examples.pop()
        else:
            notes.pop(0)  # bỏ dòng cũ nhất
        text = render()
    stats = {"notes": len(notes), "senders": len(senders), "examples": len(examples), "tokens": est_tokens(text)}
    return text, stats
