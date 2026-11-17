#!/usr/bin/env bash
# Job cron --no-agent: đẩy ticket mới lên Jira (không tốn LLM). Im lặng khi chưa cấu hình
# (cs_config.json > jira.enabled = false hoặc thiếu JIRA_EMAIL/JIRA_API_TOKEN trong .env của profile).
# Site chính = khối `jira`; site phụ (vd bản dự phòng cho site khác) = từng khoá trong `jira_sites`, khoá JIRA_<TÊN>_EMAIL / JIRA_<TÊN>_API_TOKEN.
# Chỉ đọc đúng các biến JIRA_* từ .env, không nạp cả file. Mỗi site chạy độc lập: site này lỗi không chặn site kia.
set -uo pipefail
export HERMES_HOME="${HERMES_HOME:-/home/posei/.hermes/profiles/ticket}"
export ZALO_HISTORY_DIR="${ZALO_HISTORY_DIR:-/mnt/d/zalo-logger/data/hermes-history}"
export ZALO_TICKETS_STATE="${ZALO_TICKETS_STATE:-$HERMES_HOME/zalo-tickets}"
if [ -f "$HERMES_HOME/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in JIRA_*) export "$k=${v%\"}"; export "$k=${!k#\"}";; esac
  done < <(grep -E '^JIRA_[A-Z_0-9]+=' "$HERMES_HOME/.env")
fi
# khoá chống chạy chồng: hai lượt cùng lúc (cron + chạy tay, hoặc lượt trước chưa xong) sẽ tạo trùng task vì cả hai chưa thấy trạng thái của nhau
exec 9>"$ZALO_TICKETS_STATE/.jira-push.lock"
flock -n 9 || { echo "[$(date '+%F %T')] Lượt Jira trước còn chạy, bỏ lượt này."; exit 0; }
PUSH="$HERMES_HOME/skills/zalo-task-tickets/scripts/jira_push.py"
rc=0
python3 "$PUSH" --limit "${JIRA_PUSH_LIMIT:-20}" || rc=1
if [ -f "$ZALO_TICKETS_STATE/cs_config.json" ]; then
  for site in $(python3 -c 'import json,sys; print(" ".join((json.load(open(sys.argv[1],encoding="utf-8")).get("jira_sites") or {}).keys()))' "$ZALO_TICKETS_STATE/cs_config.json" 2>/dev/null); do
    echo "--- site phụ: $site"
    python3 "$PUSH" --site "$site" --limit "${JIRA_PUSH_LIMIT:-20}" || rc=1
  done
fi
exit "$rc"
