#!/usr/bin/env bash
# Job cron --no-agent: triage tin Zalo -> ticket bằng triage_llm.py (vài lần gọi model có kiểm soát, thay vòng lặp agent của Hermes).
#
# SHADOW MODE (mặc định): ghi vào state riêng $HERMES_HOME/zalo-tickets-shadow, KHÔNG đẩy Jira, KHÔNG đụng state thật,
# dùng bản skill riêng $HERMES_HOME/skills-shadow/ (job cũ zalo-ticket-triage không bị ảnh hưởng).
# Không gọi export-hermes.sh: checkpoint xuất tin (data/hermes-export-state.json) dùng chung với job cũ nên chạy song song dễ ghi trùng;
# script đọc lịch sử JSONL mà job cũ đã xuất (trễ tối đa một chu kỳ 15 phút).
# Mặc định: 2 bước, model Gemini trực tiếp, kiểm tra phủ, dự phòng Haiku (xem docstring triage_llm.py).
set -uo pipefail
export HERMES_HOME="${HERMES_HOME:-/home/posei/.hermes/profiles/ticket}"
export ZALO_HISTORY_DIR="${ZALO_HISTORY_DIR:-/mnt/d/zalo-logger/data/hermes-history}"
export ZALO_TICKETS_STATE="${ZALO_TICKETS_STATE:-$HERMES_HOME/zalo-tickets-shadow}"
SKILL="${TRIAGE_SKILL_DIR:-$HERMES_HOME/skills-shadow/zalo-task-tickets}"

# chốt an toàn: không cho chạy vào state thật khi chưa chuyển chính thức
if [ "$(readlink -f "$ZALO_TICKETS_STATE")" = "$(readlink -f "$HERMES_HOME/zalo-tickets")" ] && [ "${TRIAGE_ALLOW_REAL:-0}" != "1" ]; then
  echo "TỪ CHỐI: ZALO_TICKETS_STATE trỏ vào state thật. Shadow mode phải dùng state riêng (TRIAGE_ALLOW_REAL=1 chỉ đặt khi đã chuyển chính thức)."
  exit 0
fi
[ -d "$ZALO_TICKETS_STATE" ] || { echo "Chưa có state shadow ($ZALO_TICKETS_STATE): chạy setup_shadow.py trước."; exit 0; }
[ -f "$SKILL/scripts/triage_llm.py" ] || { echo "Chưa chép skill sang $SKILL"; exit 0; }

# mỗi lúc chỉ một lượt (lượt trước chưa xong thì bỏ lượt này)
exec 9>"$ZALO_TICKETS_STATE/.triage.lock"
flock -n 9 || { echo "Lượt trước còn chạy, bỏ qua."; exit 0; }

# Chỉ nạp các biến TRIAGE_* cần thiết từ .env của profile (không nạp cả file, không in giá trị).
if [ -f "$HERMES_HOME/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in TRIAGE_LLM_API_KEY|TRIAGE_LLM_BASE_URL|TRIAGE_LLM_MODEL|TRIAGE_INTERNAL_MODEL|TRIAGE_NOTES_READ) export "$k=${v%\"}"; export "$k=${!k#\"}";; esac
  done < <(grep -E '^(TRIAGE_LLM_API_KEY|TRIAGE_LLM_BASE_URL|TRIAGE_LLM_MODEL|TRIAGE_INTERNAL_MODEL|TRIAGE_NOTES_READ)=' "$HERMES_HOME/.env")
fi
# mặc định khi .env không đặt: Gemini cho nhóm khách, Haiku cho nhóm nội bộ (Gemini trả rỗng ở nhóm nội bộ), KHÔNG đưa sổ tay vào prompt
export TRIAGE_LLM_MODEL="${TRIAGE_LLM_MODEL:-${TRIAGE_SHADOW_MODEL:-gemini/gemini-3.5-flash-lite}}"
export TRIAGE_INTERNAL_MODEL="${TRIAGE_INTERNAL_MODEL:-cc/claude-haiku-4-5-20251001}"
export TRIAGE_NOTES_READ="${TRIAGE_NOTES_READ:-0}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$SKILL/scripts/triage_llm.py"
