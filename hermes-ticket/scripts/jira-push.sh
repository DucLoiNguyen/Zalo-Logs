#!/usr/bin/env bash
# Job cron --no-agent: đẩy ticket mới lên Jira Service Management (không tốn LLM). Im lặng khi chưa cấu hình
# (cs_config.json > jira.enabled = false hoặc thiếu JIRA_EMAIL/JIRA_API_TOKEN trong .env của profile).
# Chỉ đọc đúng các biến JIRA_* từ .env, không nạp cả file.
set -uo pipefail
export HERMES_HOME="${HERMES_HOME:-/home/posei/.hermes/profiles/ticket}"
export ZALO_HISTORY_DIR="${ZALO_HISTORY_DIR:-/mnt/d/zalo-logger/data/hermes-history}"
export ZALO_TICKETS_STATE="${ZALO_TICKETS_STATE:-$HERMES_HOME/zalo-tickets}"
if [ -f "$HERMES_HOME/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in JIRA_*) export "$k=${v%\"}"; export "$k=${!k#\"}";; esac
  done < <(grep -E '^JIRA_[A-Z_]+=' "$HERMES_HOME/.env")
fi
exec python3 "$HERMES_HOME/skills/zalo-task-tickets/scripts/jira_push.py" --limit "${JIRA_PUSH_LIMIT:-20}"
