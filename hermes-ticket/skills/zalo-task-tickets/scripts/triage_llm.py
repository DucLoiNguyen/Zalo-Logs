#!/usr/bin/env python3
"""Triage tin Zalo -> ticket bằng vài lần gọi model có kiểm soát cho mỗi nhóm có tin mới (thay vòng lặp agent của Hermes).

Dùng làm script của cron `--no-agent`. Với mỗi nhóm có dòng mới:
  zalo_window.py (cửa sổ tin) + create_tickets.py --list-open --compact (ticket đang mở của nhóm)
  -> model sinh danh sách ticket (TRIAGE_STEPS=1: một lần; =2: bước 1 phân loại từng tin KH, bước 2 điền chi tiết, ≤ 8 ticket/lần)
  -> KIỂM TRA PHỦ BẰNG MÃ (triage_cover.py): tin KH có 📎 / SĐT / email / từ khoá yêu cầu mà chưa có ticket phủ tới thì hỏi lại
     1 lần (cùng model), rồi 1 lần model dự phòng (TRIAGE_FALLBACK_MODEL, mặc định Haiku); vẫn sót thì tự tạo ticket
     "[Cần xem]" confidence 0.4 (thành low_confidence, người xem) -- xong mới dời cursor
  -> apply_group.py (dry-run, tạo, export_jsm, dời cursor).
Đầu ra (ticket, ledger, CSV, Jira) giữ nguyên như cách cũ; chỉ khác ai sinh ra file JSON.

Cấu hình (biến môi trường):
  TRIAGE_LLM_BASE_URL  mặc định http://localhost:20128/v1 (9router)
  TRIAGE_LLM_API_KEY   khoá 9router (KHÔNG BAO GIỜ in ra)
  TRIAGE_LLM_MODEL     mặc định gemini/gemini-3.5-flash-lite (trỏ thẳng vào model, không qua combo của 9router)
  TRIAGE_FALLBACK_MODEL mặc định cc/claude-haiku-4-5-20251001 (bỏ qua bước dự phòng nếu trùng TRIAGE_LLM_MODEL)
  TRIAGE_STEPS (2; 1 = một lần gọi)  TRIAGE_COVERAGE (1; 0 = tắt kiểm tra phủ)  TRIAGE_BATCH (8 ticket mỗi lần ở bước 2)
  TRIAGE_MAX_LINES (40)  TRIAGE_CONTEXT (6)  TRIAGE_TIMEOUT (120 s)  TRIAGE_GAP (4 s nghỉ giữa các nhóm)
  TRIAGE_MAX_TOKENS (16000)  HERMES_HOME, ZALO_HISTORY_DIR, ZALO_TICKETS_STATE như các script khác.
  TRIAGE_DEBUG_DIR (ghi phản hồi thô của model)  TRIAGE_STATS_FILE (nối thêm 1 dòng JSON thống kê mỗi nhóm; dùng khi thử khô)

Lỗi HTTP/timeout (429, 5xx, ...): dừng cả lượt, KHÔNG dời cursor của nhóm đang làm, thoát 0; lượt cron sau làm tiếp.
JSON sai sau 1 lần sửa: bỏ qua nhóm đó (không dời cursor), làm nhóm khác. Phản hồi bị cắt vì hết token: thu nhỏ cửa sổ rồi hỏi lại.
  triage_llm.py [--dry-run] [--group ID] [--memory-report]
--dry-run: gọi model và in JSON nhưng không chạy apply_group.py (không tạo ticket, không dời cursor).
Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import create_tickets  # noqa: E402  (dùng lại validate() để bắt lỗi trước khi apply)
import triage_cover as cover  # noqa: E402
import triage_memory as tm  # noqa: E402
from triage_unit import unit_hint  # noqa: E402

DEFAULT_MODEL = "gemini/gemini-3.5-flash-lite"
PROMPTS = HERE.parent / "prompts"
PROMPT_FILE = PROMPTS / "triage_system.md"
CLASSIFY_FILE = PROMPTS / "triage_classify.md"
INTERNAL_FILE = PROMPTS / "triage_internal.md"
RESOLVE_FILE = PROMPTS / "triage_resolve.md"
MSG_RE = re.compile(r"^\[([^\]\s]+)\] ")
EVENT_RE = re.compile(r"^\[sự kiện\] tin (\S+)")
LAST_RE = re.compile(r"^# LAST_LINE=(\d+)\s*$", re.M)
LABELS = ("request", "update", "resolved_in_chat", "not_ours", "none")
ACTIONABLE = ("request", "update", "resolved_in_chat")
DEFAULT_FALLBACK = "cc/claude-haiku-4-5-20251001"


class LLMStop(Exception):
    """Lỗi gọi model (HTTP/timeout/mạng): dừng cả lượt."""


def log(*a) -> None:
    print(*a, flush=True)


# ---------------------------------------------------------------- chạy script con
def run(script: str, *args: str) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}  # tiếng Việt không vỡ cả khi chạy trên Windows
    p = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True, encoding="utf-8", env=env)
    return p.returncode, p.stdout, p.stderr


def list_pending_groups() -> list[tuple[str, str, int]]:
    _, out, _ = run("zalo_window.py", "--list-groups")
    res = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[3].split()[0].isdigit() and int(parts[3].split()[0]) > 0:
            res.append((parts[0], parts[1], int(parts[3].split()[0])))
    return res


# ---------------------------------------------------------------- cửa sổ tin
def parse_window(text: str) -> dict:
    """Tách LAST_LINE, tập mã tin trong phần TIN MỚI (và mã tin nhắc trong sự kiện)."""
    m = LAST_RE.search(text)
    new_ids: set[str] = set()
    event_ids: set[str] = set()
    in_new = False
    for line in text.splitlines():
        if line.startswith("## TIN MỚI"):
            in_new = True
            continue
        if not in_new:
            continue
        e = EVENT_RE.match(line)
        if e:
            event_ids.add(e.group(1))
            continue
        mm = MSG_RE.match(line)
        if mm:
            new_ids.add(mm.group(1))
    return {"last_line": int(m.group(1)) if m else None, "new_ids": new_ids, "event_ids": event_ids}


def slim_members(text: str) -> str:
    """Dòng '# Thành viên' liệt kê MỌI người từng nhắn trong nhóm (có thể rất dài): chỉ giữ người xuất hiện trong cửa sổ."""
    lines = text.splitlines()
    body = "\n".join(l for l in lines if not l.startswith("# Thành viên"))
    for i, l in enumerate(lines):
        if l.startswith("# Thành viên"):
            head, _, rest = l.partition(": ")
            keep = [p for p in rest.split("; ") if p.rsplit(" = ", 1)[-1] and p.rsplit(" = ", 1)[-1] in body]
            lines[i] = f"{head}: " + "; ".join(keep)
    return "\n".join(lines)


def focus_window(window: str, ids: set[str], radius: int = 5) -> str:
    """Cửa sổ rút gọn cho bước điền chi tiết: giữ phần tiêu đề, các dòng tin/sự kiện có mã trong `ids` và `radius` dòng lân cận
    mỗi phía (đếm trên dãy dòng tin của cửa sổ), chỗ bị lược ghi "[…]". Không có mã nào khớp thì trả lại nguyên cửa sổ."""
    head: list[str] = []
    headers: dict[str, str] = {}
    content: list[tuple[str, str]] = []
    section = None
    for l in window.splitlines():
        if l.startswith("## NGỮ CẢNH"):
            section, headers["ctx"] = "ctx", l
        elif l.startswith("## TIN MỚI"):
            section, headers["new"] = "new", l
        elif section is None:
            head.append(l)
        elif MSG_RE.match(l) or EVENT_RE.match(l):
            content.append((section, l))
    ident = lambda l: (EVENT_RE.match(l) or MSG_RE.match(l)).group(1)  # noqa: E731
    hit = [i for i, (_, l) in enumerate(content) if ident(l) in ids]
    if not hit:
        return window
    keep = sorted({j for i in hit for j in range(max(0, i - radius), min(len(content), i + radius + 1))})
    out = list(head)
    for sec in ("ctx", "new"):
        picked = [j for j in keep if content[j][0] == sec]
        if not picked:
            continue
        out += ["", headers[sec]]
        prev = None
        for j in picked:
            if prev is not None and j != prev + 1:
                out.append("[…]")
            out.append(content[j][1])
            prev = j
    return slim_members("\n".join(out))


def open_tickets(group: str) -> tuple[str, set[str]]:
    _, out, _ = run("create_tickets.py", "--list-open", "--group", group, "--compact")
    out = out.strip()
    anchors = {l.split(" | ", 1)[0].split(":", 1)[1] for l in out.splitlines() if ":" in l.split(" | ", 1)[0]}
    return out, anchors


# ---------------------------------------------------------------- prompt
def system_prompt(cfg: dict, mode: str = "customer") -> str:
    cats = cfg.get("categories", {})
    lines = [f"- {k}: {v.get('label', k)}" + (f" (ưu tiên mặc định {v['priority']})" if v.get("priority") else "") for k, v in cats.items()]
    base = INTERNAL_FILE if mode == "internal" else PROMPT_FILE   # nhóm nội bộ có prompt riêng (nhân viên giao việc cho nhau)
    return base.read_text(encoding="utf-8").rstrip() + "\n\n## LOẠI (category)\n" + ("\n".join(lines) or "- (không giới hạn)")


def user_prompt(memory: str, window: str, opens: str, extra: str = "") -> str:
    parts = []
    if memory:
        parts.append(memory)
    parts.append("## TICKET ĐANG MỞ CỦA NHÓM (khoá | tiêu đề | khách | đơn vị | hạn)\n" + (opens or "(không có)"))
    parts.append("## CỬA SỔ TIN\n" + window.strip())
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


# ---------------------------------------------------------------- gọi model
def call_llm(messages: list[dict], model: str | None = None) -> tuple[str, dict, str | None]:
    """Trả về (nội dung, usage, finish_reason). finish_reason 'length' nghĩa là phản hồi bị cắt vì hết token đầu ra."""
    base = os.environ.get("TRIAGE_LLM_BASE_URL", "http://localhost:20128/v1").rstrip("/")
    body = {"model": model or os.environ.get("TRIAGE_LLM_MODEL", DEFAULT_MODEL), "messages": messages, "temperature": 0, "stream": False,
            "response_format": {"type": "json_object"}, "max_tokens": int(os.environ.get("TRIAGE_MAX_TOKENS", "16000"))}
    headers = {"Content-Type": "application/json"}
    if os.environ.get("TRIAGE_LLM_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["TRIAGE_LLM_API_KEY"]
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("TRIAGE_TIMEOUT", "120"))) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        e.close()
        raise LLMStop(f"HTTP {e.code} từ model" + (" (kiểm tra TRIAGE_LLM_API_KEY)" if e.code in (401, 403) else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        raise LLMStop(f"không gọi được model: {type(e).__name__}") from None
    try:
        c = data["choices"][0]
        return c["message"]["content"] or "", data.get("usage") or {}, c.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        raise LLMStop("phản hồi model sai định dạng") from None


def parse_json(text: str):
    """Model hay bọc JSON trong hàng rào ```json dù đã yêu cầu response_format: gỡ rồi parse."""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("không có đối tượng JSON") from None
        return json.loads(t[i:j + 1])


class Usage:
    def __init__(self) -> None:
        self.calls = self.prompt = self.completion = 0
        self.by_step: dict[str, list[int]] = {}

    def add(self, u: dict, step: str = "main") -> None:
        p, c = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        self.calls += 1
        self.prompt += p
        self.completion += c
        s = self.by_step.setdefault(step, [0, 0, 0])
        s[0] += 1
        s[1] += p
        s[2] += c


def debug_dump(group: str, step: str, n: int, finish, usage: dict, text: str) -> None:
    """TRIAGE_DEBUG_DIR=<thư mục>: ghi phản hồi thô của model (có dữ liệu khách, chỉ bật khi gỡ lỗi/thử khô)."""
    d = os.environ.get("TRIAGE_DEBUG_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / f"{group}_{n}_{step}.txt").write_text(f"finish_reason={finish}\nusage={json.dumps(usage)}\n\n{text}", encoding="utf-8")


def converse(group: str, messages: list[dict], usage: Usage, step: str, validate, model: str | None = None):
    """Gọi model; validate(obj) trả danh sách lỗi (rỗng = tốt); sai thì nhắc sửa 1 lần.
    Trả về (obj, status): status 'ok' | 'invalid' | 'truncated' (hết token đầu ra: sửa vô ích, phải thu nhỏ cửa sổ)."""
    messages = list(messages)
    errs: list[str] = []
    for attempt in (1, 2):
        text, u, finish = call_llm(messages, model)
        usage.add(u, step)
        debug_dump(group, step, usage.calls, finish, u, text)
        if str(finish or "").lower() in ("length", "max_tokens"):
            return None, "truncated"
        try:
            obj = parse_json(text)
            errs = validate(obj)
        except (ValueError, json.JSONDecodeError) as e:
            obj, errs = None, [f"không đọc được JSON: {e}"]
        if not errs:
            return obj, "ok"
        if attempt == 1:
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "JSON vừa rồi có lỗi:\n" + "\n".join(f"- {e}" for e in errs[:10])
                          + "\nSửa và trả lại JSON đầy đủ, đúng định dạng."}]
    log(f"[{group}] {step}: JSON sai sau 1 lần sửa: " + "; ".join(errs[:3]))
    return None, "invalid"


# ---------------------------------------------------------------- kiểm tra kết quả
def check_result(obj, group: str, win: dict, anchors: set[str], cfg: dict, mode: str) -> tuple[list[dict], str, list[str]]:
    """Trả về (tasks đã chuẩn hoá, notes_update, lỗi). Có lỗi thì tasks không dùng được."""
    if isinstance(obj, list):
        obj = {"tasks": obj}
    if not isinstance(obj, dict) or not isinstance(obj.get("tasks"), list):
        return [], "", ['JSON phải có dạng {"tasks": [...], "notes_update": "..."}']
    notes = obj.get("notes_update") or ""
    errs: list[str] = []
    tasks: list[dict] = []
    ok_request = win["new_ids"] | anchors
    for i, t in enumerate(obj["tasks"]):
        tag = f"tasks[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{tag}: phải là đối tượng")
            continue
        t = dict(t)
        t["group_id"] = group
        if t.get("request_message_id") is not None:
            t["request_message_id"] = str(t["request_message_id"])
        if t.get("source_message_ids"):
            t["source_message_ids"] = [str(x) for x in t["source_message_ids"]]
        t.setdefault("kind", "internal" if mode == "internal" else "cs")
        t.setdefault("status_hint", "open")
        errs += [f"{tag}: {e}" for e in create_tickets.validate(t, cfg)]
        rid = t.get("request_message_id")
        if rid and rid not in ok_request:
            errs.append(f"{tag}: request_message_id {rid} không nằm trong TIN MỚI (cũng không phải mỏ neo của ticket đang mở)")
        ok_source = win["new_ids"] | win["event_ids"] | ({rid} if rid else set())
        bad = [s for s in t.get("source_message_ids") or [] if s not in ok_source]
        if bad:
            errs.append(f"{tag}: source_message_ids {', '.join(bad)} không nằm trong TIN MỚI")
        tasks.append(t)
    return tasks, str(notes), errs


def merge_tasks(tasks: list[dict], new: list[dict]) -> list[dict]:
    """Thêm ticket mới; trùng mỏ neo với ticket đã có thì gộp source_message_ids thay vì tạo thêm."""
    by_rid = {t["request_message_id"]: t for t in tasks}
    for t in new:
        old = by_rid.get(t["request_message_id"])
        if old:
            old["source_message_ids"] = sorted(set(old.get("source_message_ids") or []) | set(t.get("source_message_ids") or []))
        else:
            tasks.append(t)
            by_rid[t["request_message_id"]] = t
    return tasks


# ---------------------------------------------------------------- bước 1: phân loại từng tin KH
def normalize_labels(obj, kh_ids: list[str], open_keys: set[str]) -> dict[str, dict]:
    """{mã tin: {label, anchor, ref}}; nhẹ tay: mã lạ bị bỏ, nhãn lạ -> none, update thiếu/sai ref_ticket -> request."""
    out: dict[str, dict] = {}
    items = obj.get("labels") if isinstance(obj, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or str(it.get("message_id")) not in kh_ids:
            continue
        mid, label = str(it["message_id"]), str(it.get("label") or "none")
        label = label if label in LABELS else "none"
        ref = str(it.get("ref_ticket") or "")
        if label == "update" and ref not in open_keys:
            label = "request"
        anchor = str(it.get("anchor") or mid)
        out[mid] = {"label": label, "anchor": anchor if anchor in kh_ids else mid, "ref": ref if label == "update" else ""}
    return out


def build_items(labels: dict[str, dict], kh_ids: list[str]) -> list[dict]:
    """Gom các tin cùng mỏ neo thành yêu cầu: [{rid, label, ref, members}] theo thứ tự xuất hiện."""
    items: dict[str, dict] = {}
    for mid in kh_ids:
        lab = labels.get(mid)
        if not lab or lab["label"] not in ACTIONABLE:
            continue
        base = labels.get(lab["anchor"])
        if not base or base["label"] not in ACTIONABLE:
            base, anchor = lab, mid
        else:
            anchor = lab["anchor"]
        rid = base["ref"].split(":", 1)[1] if base["label"] == "update" and ":" in base["ref"] else anchor
        it = items.setdefault(rid, {"rid": rid, "label": base["label"], "ref": base["ref"], "members": []})
        it["members"].append(mid)
    return list(items.values())


def force_misa(labels: dict[str, dict], msgs: list[dict]) -> None:
    """Tin nhắc riêng Misa/luatvietnam (không nhắc Clex) luôn là not_ours, bất kể model gắn nhãn gì hay khách tag nhân viên CMC."""
    for m in msgs:
        if m["id"] not in labels:
            continue
        if cover.misa_only_text(m["text"]):
            labels[m["id"]] = {"label": "not_ours", "anchor": m["id"], "ref": ""}
        elif labels[m["id"]]["label"] == "not_ours" and "clex" in cover.fold(m["text"]):
            labels[m["id"]] = {"label": "request", "anchor": m["id"], "ref": ""}   # nhắc Clex (vd. tệp "AI CLEX và AI Luật") thì là việc của team


def item_lines(items: list[dict], hints: dict[str, str] | None = None) -> str:
    out = []
    for it in items:
        upd = f" (cập nhật ticket {it['ref']}, dùng đúng mỏ neo này)" if it["label"] == "update" else ""
        hint = f" | gợi ý đơn vị (từ tên tệp/tin kèm, sai thì sửa): {hints[it['rid']]}" if hints and hints.get(it["rid"]) else ""
        out.append(f"- mỏ neo {it['rid']} | nhãn {it['label']}{upd} | tin liên quan: {', '.join(it['members'])}{hint}")
    return "\n".join(out)


# ---------------------------------------------------------------- một nhóm
def pipeline(group: str, cfg: dict, state: Path, mem: Path, usage: Usage, max_lines: int, steps: int, coverage: bool) -> dict:
    """Hỏi model (1 hoặc 2 bước), kiểm tra phủ, trả về {"status": "ok"|"empty"|"skip"|"truncated", ...}."""
    mode = cfg.get("groups", {}).get(group, {}).get("mode", "customer")
    code, out, err = run("zalo_window.py", "--group", group, "--max", str(max_lines), "--context", os.environ.get("TRIAGE_CONTEXT", "6"))
    if code != 0:
        log(f"[{group}] BỎ QUA: zalo_window.py lỗi: {err.strip()[:200]}")
        return {"status": "skip"}
    win = parse_window(out)
    if win["last_line"] is None:
        return {"status": "empty"}
    opens, anchors = open_tickets(group)
    open_keys = {l.split(" | ", 1)[0] for l in opens.splitlines() if " | " in l}
    window = slim_members(out)
    msgs = cover.parse_messages(window)
    if mode == "internal":
        # nhóm nội bộ: yêu cầu do chính nhân viên (NV) nêu, không có "khách". Coi NV như người yêu cầu cho kiểm tra phủ, và chạy 1 bước
        # (bước phân loại chỉ gắn nhãn tin KH nên sẽ bỏ qua toàn bộ nhóm này).
        steps = 1
        for m in msgs:
            if m["role"] == "NV":
                m["role"] = "KH"
                m["internal"] = True   # chỉ cần phủ tin có tệp/SĐT/email; từ khoá chung ("tài khoản", "cấp"...) quá hay gặp trong chat nhân viên
    members = cover.member_ids(window)
    memory, _stats = tm.build_memory(mem, state, cfg, group, window)
    model_override = (os.environ.get("TRIAGE_INTERNAL_MODEL") or None) if mode == "internal" else None   # model riêng cho nhóm nội bộ
    stats: dict = {"group": group, "steps": steps, "coverage": coverage, "model": model_override or os.environ.get("TRIAGE_LLM_MODEL", DEFAULT_MODEL),
                   "lines": max_lines, "kh_messages": len(cover.kh_ids_to_label(msgs)), "flagged": len([m for m in msgs if cover.needs_cover(m)])}
    sysp = system_prompt(cfg, mode)   # nhóm nội bộ có prompt riêng
    notes, exempt, labels = "", set(), {}
    tasks: list[dict] = []

    holder: dict = {}

    def check(obj):
        t_, n_, errs = check_result(obj, group, win, anchors, cfg, mode)
        holder["tasks"], holder["notes"] = t_, n_
        return errs

    if steps == 2:
        kh_ids = cover.kh_ids_to_label(msgs)
        if kh_ids:
            ids_txt = ", ".join(kh_ids)
            m1 = [{"role": "system", "content": CLASSIFY_FILE.read_text(encoding="utf-8")},
                  {"role": "user", "content": user_prompt(memory, window, opens, "## DANH SÁCH TIN CẦN GẮN NHÃN\n" + ids_txt)}]
            obj, st = converse(group, m1, usage, "classify", lambda o: [] if isinstance(o, dict) and isinstance(o.get("labels"), list)
                               else ['JSON phải có dạng {"labels": [...]}'])
            if st == "truncated":
                return {"status": "truncated"}
            if st != "ok":
                return {"status": "skip"}
            labels = normalize_labels(obj, kh_ids, open_keys)
            force_misa(labels, msgs)
            exempt = {mid for mid, l in labels.items() if l["label"] == "not_ours"}
            stats["labels"] = {mid: l["label"] for mid, l in labels.items()}
            items = build_items(labels, kh_ids)
            hints = {it["rid"]: h for it in items if (h := unit_hint(msgs, it["rid"], it["members"]))}
            batch = max(1, int(os.environ.get("TRIAGE_BATCH", "8")))
            for i in range(0, len(items), batch):
                part = items[i:i + batch]
                expected = {it["rid"] for it in part}
                extra = ("## YÊU CẦU CẦN ĐIỀN CHI TIẾT (đã được phân loại; chỉ trả về đúng các yêu cầu này, mỗi dòng một phần tử trong `tasks`, "
                         "giữ nguyên `request_message_id` là mỏ neo đã cho)\n" + item_lines(part, hints))
                focus = {i for it in part for i in (it["rid"], *it["members"])}  # bước 2 chỉ cần tin liên quan ±5 dòng, không cả cửa sổ
                m2 = [{"role": "system", "content": sysp}, {"role": "user", "content": user_prompt(memory, focus_window(window, focus), opens, extra)}]

                def check2(o, expected=expected):
                    errs = check(o)
                    return errs + [f"request_message_id {t['request_message_id']} không nằm trong danh sách mỏ neo đã cho"
                                   for t in holder["tasks"] if t.get("request_message_id") not in expected]

                obj, st = converse(group, m2, usage, "detail", check2)
                if st == "truncated":
                    return {"status": "truncated"}
                if st != "ok":
                    return {"status": "skip"}
                got = holder["tasks"]
                for t in got:
                    if any(it["rid"] == t["request_message_id"] and it["label"] == "resolved_in_chat" for it in part):
                        t["status_hint"] = "resolved_in_chat"
                merge_tasks(tasks, got)
                notes = notes or holder["notes"]
    else:
        m1 = [{"role": "system", "content": sysp}, {"role": "user", "content": user_prompt(memory, window, opens)}]
        obj, st = converse(group, m1, usage, "main", check, model_override)
        if st == "truncated":
            return {"status": "truncated"}
        if st != "ok":
            return {"status": "skip"}
        tasks, notes = holder["tasks"], holder["notes"]
    stats["tasks_initial"] = len(tasks)
    stats["initial_rids"] = [t["request_message_id"] for t in tasks]

    synthesized: list[dict] = []
    if coverage:
        tasks, synthesized = fill_gaps(group, cfg, win, anchors, mode, msgs, members, window, opens, tasks, exempt, usage, stats)
    if not coverage and not tasks and cover.uncovered(msgs, tasks, exempt):
        # quy tắc chung: cửa sổ có tin cần xử lý mà 0 ticket và chưa qua kiểm tra phủ thì KHÔNG dời cursor (lượt sau thử lại)
        log(f"[{group}] BỎ QUA (không dời cursor): model không trả ticket nào nhưng có tin cần xử lý chưa được phủ (kiểm tra phủ đang tắt?)")
        return {"status": "skip"}
    tasks, stats["misa_dropped"] = cover.drop_misa(tasks, msgs)
    if os.environ.get("TRIAGE_MERGE", "1") != "0":
        tasks, stats["merged"] = cover.merge_split(tasks, msgs)
        if stats["merged"]:
            log(f"[{group}] gộp {stats['merged']} ticket bị tách (cùng người gửi ≤3 phút, cùng loại, cùng đơn vị)")
    if os.environ.get("TRIAGE_RESOLVE", "1") != "0" and mode != "internal":
        tasks += resolve_step(group, state, usage, window, msgs, opens, tasks, stats)
    stats["tasks_final"] = len(tasks)
    stats["synthesized"] = len(synthesized)
    return {"status": "ok", "win": win, "tasks": tasks, "notes": notes, "stats": stats}


def open_lines(opens: str) -> dict[str, str]:
    """{khoá: dòng} của các ticket ĐANG MỞ (bỏ dòng gắn nhãn "(đã đóng)"/"(đã xong trong chat)")."""
    out = {}
    for l in opens.splitlines():
        cols = l.split(" | ")
        if len(cols) >= 2 and not cols[1].startswith("(đã "):
            out[cols[0]] = l
    return out


def resolve_step(group: str, state: Path, usage: Usage, window: str, msgs: list[dict], opens: str, tasks: list[dict], stats: dict) -> list[dict]:
    """Bước hỏi nhỏ: tin NHÂN VIÊN mới có báo đã xử lý xong ticket đang mở nào không? Chỉ chạy khi có tin NV mới (không phải Misa) và có ticket
    open trong nhóm. Mặc định open; chỉ trả ticket cập nhật `resolved_in_chat` khi model nêu khoá ticket cụ thể + tin NV + trích đúng từ tin đó."""
    staff = [m for m in msgs if m["new"] and m["role"] == "NV" and not cover.is_misa(m["name"]) and "[ĐÃ THU HỒI]" not in m["text"]]
    taken = {f"{group}:{t['request_message_id']}" for t in tasks}
    cands = {k: l for k, l in open_lines(opens).items() if k not in taken}
    if not staff or not cands:
        return []
    by_id = {m["id"]: m for m in staff}
    flat = lambda x: re.sub(r"\s+", " ", cover.fold(x.replace("⏎", " "))).strip()  # noqa: E731
    ledger = create_tickets.load_ledger(state)
    open_recs = {k: ledger.get(k, {}) for k in cands}
    holder: dict = {}

    def check(obj):
        res = obj.get("resolved") if isinstance(obj, dict) else None
        if not isinstance(res, list):
            return ['JSON phải có dạng {"resolved": [{"ticket_key": "...", "staff_message_id": "...", "quote": "..."}]}']
        errs, ok = [], []
        for i, r in enumerate(res):
            if not isinstance(r, dict):
                errs.append(f"resolved[{i}]: phải là đối tượng")
                continue
            key, mid, q = str(r.get("ticket_key") or ""), str(r.get("staff_message_id") or ""), str(r.get("quote") or "").strip().strip("…").strip(".")
            if key not in cands:
                errs.append(f"resolved[{i}]: ticket_key {key} không nằm trong danh sách ticket đang mở")
            elif mid not in by_id:
                errs.append(f"resolved[{i}]: staff_message_id {mid} không phải tin nhân viên mới")
            elif len(flat(q)) < 6 or flat(q) not in flat(by_id[mid]["text"]):
                errs.append(f"resolved[{i}]: quote phải là đoạn chép NGUYÊN VĂN từ tin {mid}")
            else:
                ok.append({"key": key, "mid": mid, "quote": q})
        holder["ok"] = ok
        return errs

    def gate(proposed: list[dict]) -> list[dict]:
        """Chốt chặn bằng mã: chỉ đóng khi (a) tin NV nêu đúng đơn vị/tên khách, (b) @tag đúng khách, hoặc (c) chỉ có đúng 1 ticket mở trong
        10 tin gần nhất; tối đa 3 ticket mỗi tin NV (tin chung chung đóng nhiều ticket thì không đóng cái nào)."""
        per_msg: dict[str, list[dict]] = {}
        for r in proposed:
            staff_msg = by_id[r["mid"]]
            rule = cover.resolve_rule(open_recs[r["key"]], staff_msg, msgs, r["key"]) or \
                ("chỉ 1 ticket mở trong 10 tin gần nhất" if cover.resolve_unique(open_recs, staff_msg, msgs) == r["key"] else None)
            if rule:
                per_msg.setdefault(r["mid"], []).append({**r, "rule": rule})
        out = []
        for mid, lst in per_msg.items():
            if len(lst) > 3:
                log(f"[{group}] tin NV {mid} đóng {len(lst)} ticket (> 3): quá chung chung, không đóng cái nào")
                continue
            out += lst
        stats["resolve_proposed"], stats["resolve_rejected"] = len(proposed), len(proposed) - len(out)
        return out

    user = ("## TICKET ĐANG MỞ (khoá | tiêu đề | khách | đơn vị | hạn)\n" + "\n".join(cands.values()) + "\n\n## CỬA SỔ TIN\n" +
            focus_window(window, {m["id"] for m in staff}).strip() + "\n\n## TIN NHÂN VIÊN MỚI CẦN XÉT\n" + ", ".join(m["id"] for m in staff))
    obj, st = converse(group, [{"role": "system", "content": RESOLVE_FILE.read_text(encoding="utf-8")}, {"role": "user", "content": user}], usage, "resolve", check)
    if st != "ok":
        return []
    out, seen = [], set()
    for r in gate(holder["ok"]):
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        prev = ledger.get(r["key"], {})
        out.append({"request_message_id": r["key"].split(":", 1)[1], "group_id": group, "title": prev.get("title") or "(không rõ)",
                    "description": f"Nhân viên báo đã xử lý xong (tin {r['mid']}, căn cứ: {r['rule']}): {r['quote']}", "quote": r["quote"], "customer_name": prev.get("customer"),
                    "kind": prev.get("kind") or "cs", "unit": prev.get("unit"), "platform": prev.get("platform"), "source_message_ids": [r["mid"]],
                    "status_hint": "resolved_in_chat", "confidence": 0.9, "attachments": []})
    stats["resolved"] = len(out)
    return out


def fill_gaps(group, cfg, win, anchors, mode, msgs, members, window, opens, tasks, exempt, usage, stats):
    """Hỏi lại về các tin KH bị bỏ sót (cùng model, rồi model dự phòng); vẫn sót thì tự tạo ticket [Cần xem] confidence 0.4."""
    sysp = system_prompt(cfg)
    current = os.environ.get("TRIAGE_LLM_MODEL", DEFAULT_MODEL)
    fallback = os.environ.get("TRIAGE_FALLBACK_MODEL", DEFAULT_FALLBACK)
    rounds = [("gap1", None)] + ([("gap2", fallback)] if fallback and fallback != current else [])
    exempt = set(exempt)
    stats["missing"] = [len(cover.uncovered(msgs, tasks, exempt))]
    if mode == "internal":
        # nhóm nội bộ: không hỏi lại từng tin (chat nhân viên nhiều tin giống yêu cầu nhưng không phải) mà tạo MỘT ticket [Cần xem] cho cả cửa sổ
        left = cover.uncovered(msgs, tasks, exempt)
        if not left:
            return tasks, []
        syn = cover.synth_summary(left, group, cfg, mode)
        return merge_tasks(tasks, [syn]), [syn]
    for step, model in rounds:
        missing = cover.uncovered(msgs, tasks, exempt)
        if not missing:
            break
        lines = "\n".join(f"- {m['id']} | {m['role']} {m['name']} | {cover.excerpt(m['text'], 140)} | dấu hiệu: {m['reason']}" for m in missing)
        extra = ("## TIN CHƯA ĐƯỢC PHỦ\nCác tin của khách dưới đây trông như yêu cầu nhưng chưa nằm trong ticket nào:\n" + lines +
                 "\nVới mỗi tin: nếu là yêu cầu của team thì tạo ticket trong `tasks` (hoặc nếu thuộc một ticket đã có thì dùng đúng mỏ neo của nó và ghi tin vào "
                 "`source_message_ids`); nếu CHẮC CHẮN không phải yêu cầu của team (chào hỏi, cảm ơn, chỉ nhắc riêng Misa...) thì liệt kê mã tin "
                 "vào `not_requests`. Trả về {\"tasks\": [...], \"not_requests\": [...]}; không được bỏ sót mã nào.")
        msgs_llm = [{"role": "system", "content": sysp},
                    {"role": "user", "content": user_prompt("", focus_window(window, {m["id"] for m in missing}), opens, extra)}]
        holder: dict = {}

        def check(o):
            t, n, errs = check_result(o, group, win, anchors, cfg, mode)
            holder["tasks"], holder["not"] = t, [str(x) for x in (o.get("not_requests") or [])] if isinstance(o, dict) else []
            return errs

        obj, st = converse(group, msgs_llm, usage, step, check, model)
        if st == "ok":
            tasks = merge_tasks(tasks, holder["tasks"])
            exempt |= set(holder["not"])
        stats["missing"].append(len(cover.uncovered(msgs, tasks, exempt)))
    # tự tạo ticket [Cần xem]: tin có 📎 trước; lời nhắn chữ đi kèm tệp đó sau đó được coi là đã phủ (không đẻ thêm ticket)
    left = cover.uncovered(msgs, tasks, exempt)
    synthesized = [cover.synth_task(m, group, cfg, mode, members) for m in left if m["reason"] == "tệp"]
    rest = cover.uncovered(msgs, tasks + synthesized, exempt)
    synthesized += [cover.synth_task(m, group, cfg, mode, members) for m in rest]
    return merge_tasks(tasks, synthesized), synthesized


def process_group(group: str, name: str, cfg: dict, state: Path, mem: Path, usage: Usage, dry_run: bool) -> str:
    """Trả về 'ok' | 'skip' (lỗi riêng nhóm này). LLMStop bay ra ngoài để dừng cả lượt.
    Phản hồi bị cắt vì hết token đầu ra thì thu nhỏ cửa sổ một nửa rồi hỏi lại (tối đa 2 lần); phần còn lại để lượt sau."""
    max_lines = int(os.environ.get("TRIAGE_MAX_LINES", "40"))
    steps = 1 if os.environ.get("TRIAGE_STEPS", "2") == "1" else 2
    coverage = os.environ.get("TRIAGE_COVERAGE", "1") != "0"
    for shrink in range(3):
        r = pipeline(group, cfg, state, mem, usage, max_lines, steps, coverage)
        if r["status"] != "truncated":
            break
        if shrink == 2 or max_lines <= 2:
            log(f"[{group}] BỎ QUA (không dời cursor): phản hồi vẫn bị cắt dù đã thu nhỏ cửa sổ còn {max_lines} dòng")
            return "skip"
        max_lines //= 2
        log(f"[{group}] phản hồi bị cắt vì hết token đầu ra: thu nhỏ cửa sổ còn {max_lines} dòng rồi hỏi lại")
    if r["status"] == "empty":
        return "ok"
    if r["status"] != "ok":
        log(f"[{group}] BỎ QUA (không dời cursor), lượt sau thử lại")
        return "skip"
    win, tasks, notes = r["win"], r["tasks"], r["notes"]
    if os.environ.get("TRIAGE_STATS_FILE"):
        st = dict(r["stats"], label=os.environ.get("TRIAGE_RUN_LABEL"), tokens={k: v for k, v in usage.by_step.items()}, tasks=tasks, dry_run=dry_run)
        with open(os.environ["TRIAGE_STATS_FILE"], "a", encoding="utf-8") as f:
            f.write(json.dumps(st, ensure_ascii=False) + "\n")

    today = datetime.now(create_tickets.TZ).strftime("%Y-%m-%d")
    if r["stats"].get("synthesized"):
        log(f"[{group}] {r['stats']['synthesized']} tin model không phân loại được: đã tạo ticket [Cần xem] (confidence 0.4)")
    if dry_run:
        log(f"[{group}] {name}: DRY-RUN, {len(tasks)} ticket, LAST_LINE={win['last_line']}")
        log(json.dumps(tasks, ensure_ascii=False, indent=2))
        tm.save_notes(mem, group, notes, today)  # vào memory/ của state dry; không in ra màn hình
        return "ok"
    f = Path(tempfile.gettempdir()) / f"zalo_llm_{group}.json"
    f.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    code, out, err = run("apply_group.py", group, str(f), str(win["last_line"]))
    for line in (out + err).strip().splitlines():
        log(f"[{group}] {line}")
    if code != 0:
        return "skip"
    tm.update_senders(mem, state, tasks)
    tm.save_notes(mem, group, notes, today)
    return "ok"


def memory_report(cfg: dict, state: Path) -> None:
    """Đo kích thước phần trí nhớ trên dữ liệu thật (chỉ đọc, không ghi gì): mỗi nhóm một dòng."""
    sysp = system_prompt(cfg)
    log(f"System prompt: {len(sysp)} ký tự ≈ {tm.est_tokens(sysp)} token (ước lượng ký tự/3)")
    _, out, _ = run("zalo_window.py", "--list-groups")
    mem = tm.memory_dir(state, False)
    for line in out.splitlines():
        g, gname = line.split("\t")[:2]
        _, wtext, _ = run("zalo_window.py", "--group", g, "--max", os.environ.get("TRIAGE_MAX_LINES", "40"),
                          "--context", os.environ.get("TRIAGE_CONTEXT", "6"))
        window = slim_members(wtext)
        text, st = tm.build_memory(mem, state, cfg, g, window, save=False)
        opens, _anchors = open_tickets(g)
        total = tm.est_tokens(sysp) + tm.est_tokens(text) + tm.est_tokens(opens) + tm.est_tokens(window)
        log(f"{g} | trí nhớ ≈ {st['tokens']} token (sổ tay {st['notes']} dòng, người gửi {st['senders']}, ví dụ {st['examples']}) | "
            f"ticket mở {len(opens.splitlines())} dòng ≈ {tm.est_tokens(opens)} | cửa sổ ≈ {tm.est_tokens(window)} | tổng prompt ≈ {total} token")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--group", help="chỉ xử lý nhóm này")
    ap.add_argument("--memory-report", action="store_true", help="đo kích thước phần trí nhớ/prompt mỗi nhóm (chỉ đọc, không gọi model)")
    a = ap.parse_args()

    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    state = Path(os.environ.get("ZALO_TICKETS_STATE", home / "zalo-tickets"))
    cfg_file = state / "cs_config.json"
    cfg = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    if a.memory_report:
        memory_report(cfg, state)
        return 0
    mem = tm.memory_dir(state, a.dry_run)

    pending = [g for g in list_pending_groups() if not a.group or g[0] == a.group]
    if not pending:
        log("Không có tin mới.")
        return 0
    usage = Usage()
    gap = float(os.environ.get("TRIAGE_GAP", "4"))
    done = skipped = 0
    try:
        for n, (g, name, _lines) in enumerate(pending):
            if n:
                time.sleep(gap)  # tôn trọng giới hạn số lần gọi/phút của gói free
            if process_group(g, name, cfg, state, mem, usage, a.dry_run) == "ok":
                done += 1
            else:
                skipped += 1
    except LLMStop as e:
        log(f"CẢNH BÁO: {e}. Dừng lượt này (cursor nhóm đang làm giữ nguyên), lượt sau làm tiếp.")
    log(f"Triage xong: {done} nhóm ok, {skipped} nhóm bỏ qua / {len(pending)}. "
        f"Model: {usage.calls} lần gọi, {usage.prompt} token vào, {usage.completion} token ra.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
