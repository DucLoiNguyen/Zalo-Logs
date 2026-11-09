#!/usr/bin/env bash
# Pre-script của cron zalo-ticket-triage: xuất tin mới từ zalo-logger rồi in sẵn cửa sổ tin cho agent.
# Không có tin mới thì dòng cuối là {"wakeAgent": false} nên Hermes bỏ qua lượt agent (không tốn model).
set -uo pipefail
export HERMES_HOME="${HERMES_HOME:-/home/posei/.hermes/profiles/ticket}"
export ZALO_HISTORY_DIR="${ZALO_HISTORY_DIR:-/mnt/d/zalo-logger/data/hermes-history}"
export ZALO_TICKETS_STATE="${ZALO_TICKETS_STATE:-$HERMES_HOME/zalo-tickets}"
SKILL="$HERMES_HOME/skills/zalo-task-tickets"
"$HERMES_HOME/scripts/export-hermes.sh" || echo "CẢNH BÁO: export-hermes.sh lỗi (mã $?), vẫn xử lý tin đã có."
exec python3 "$SKILL/scripts/triage_prepare.py" --max "${TRIAGE_MAX_LINES:-60}" --context "${TRIAGE_CONTEXT:-8}"
