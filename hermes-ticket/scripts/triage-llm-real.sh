#!/usr/bin/env bash
# Job cron --no-agent CHÍNH THỨC: triage tin Zalo -> ticket bằng triage_llm.py trên STATE THẬT (thay job agent `zalo-ticket-triage`).
# Thứ tự mỗi lượt:
#   1. khoá chống chạy chồng (flock, không chờ): lượt trước còn chạy thì thoát êm (mã 0) + ghi một dòng log;
#   2. (không còn chốt chặn Jira từ 16/11: triage_llm.py không bao giờ gọi Jira, việc đẩy do job `zalo-ticket-jira` lo);
#   3. export-hermes (qua cmd.exe, xem export-hermes.sh): mã 2 (checkpoint hỏng) thì bỏ lượt và báo (mã 1); mã lỗi khác chỉ cảnh báo, vẫn chạy trên lịch sử đã có;
#   4. bỏ lượt (mã 0) nếu nhóm nào còn tin chưa xử lý mà có tin mới trong TRIAGE_QUIET_SECONDS (mặc định 120) giây gần nhất: đợi khách nhắn xong
#      (tệp + lời nhắn đi kèm thường tới cách nhau vài chục giây); kiểm tra làm SAU export để dùng dữ liệu mới nhất.
#      Chỉ bỏ TỐI ĐA TRIAGE_MAX_QUIET_SKIPS (mặc định 2) lượt LIÊN TIẾP; lượt kế vẫn chạy dù nhóm chưa yên lặng (nhóm nhắn liên tục sẽ không bị bỏ đói,
#      trễ tối đa ~45 phút). Bộ đếm là một số nguyên trong `<state>/.triage-real-quiet-skips` (không chứa nội dung tin); đặt về 0 khi lượt thật chạy
#      hoặc khi không còn tin chờ; bỏ lượt thứ 2 liên tiếp thì ghi dòng WARN;
#   5. chạy triage_llm.py trên state thật, tối đa TRIAGE_MAX_RUN_SECONDS (mặc định 1500 giây) để một lượt treo không giữ khoá mãi.
# Tham số dòng lệnh chuyển thẳng cho triage_llm.py (vd --dry-run, --group <id>). Biến thử nghiệm: TRIAGE_EXPORT_SCRIPT, TRIAGE_SKILL_DIR.
set -uo pipefail
export HERMES_HOME="${HERMES_HOME:-/home/posei/.hermes/profiles/ticket}"
export ZALO_HISTORY_DIR="${ZALO_HISTORY_DIR:-/mnt/d/zalo-logger/data/hermes-history}"
export ZALO_TICKETS_STATE="${ZALO_TICKETS_STATE:-$HERMES_HOME/zalo-tickets}"
SKILL="${TRIAGE_SKILL_DIR:-$HERMES_HOME/skills/zalo-task-tickets}"
EXPORT="${TRIAGE_EXPORT_SCRIPT:-$HERMES_HOME/scripts/export-hermes.sh}"
QUIET="${TRIAGE_QUIET_SECONDS:-120}"
MAXRUN="${TRIAGE_MAX_RUN_SECONDS:-1500}"
MAX_SKIPS="${TRIAGE_MAX_QUIET_SKIPS:-2}"
SKIPFILE="$ZALO_TICKETS_STATE/.triage-real-quiet-skips"
DRY=0; for a in "$@"; do [ "$a" = "--dry-run" ] && DRY=1; done   # chạy tay --dry-run không được làm đổi bộ đếm của cron thật
setskips() { [ "$DRY" -eq 1 ] || echo "$1" > "$SKIPFILE"; }
log() { echo "[$(date '+%F %T')] $*"; }

[ -d "$ZALO_TICKETS_STATE" ] || { log "LỖI: không có state $ZALO_TICKETS_STATE"; exit 1; }
[ -f "$SKILL/scripts/triage_llm.py" ] || { log "LỖI: chưa chép skill sang $SKILL"; exit 1; }

# 1. khoá chống chạy chồng
exec 9>"$ZALO_TICKETS_STATE/.triage-real.lock"
flock -n 9 || { log "Lượt trước còn chạy (khoá $ZALO_TICKETS_STATE/.triage-real.lock), bỏ lượt này."; exit 0; }

# 2. (đã gỡ chốt chặn Jira 16/11: Jira bật lại sang site mới; việc đẩy do job `zalo-ticket-jira` riêng lo, triage_llm.py không gọi Jira)

# nạp các biến TRIAGE_* cần thiết từ .env của profile (không nạp cả file, không in giá trị)
if [ -f "$HERMES_HOME/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in TRIAGE_LLM_API_KEY|TRIAGE_LLM_BASE_URL|TRIAGE_LLM_MODEL|TRIAGE_INTERNAL_MODEL|TRIAGE_NOTES_READ) export "$k=${v%\"}"; export "$k=${!k#\"}";; esac
  done < <(grep -E '^(TRIAGE_LLM_API_KEY|TRIAGE_LLM_BASE_URL|TRIAGE_LLM_MODEL|TRIAGE_INTERNAL_MODEL|TRIAGE_NOTES_READ)=' "$HERMES_HOME/.env")
fi
export TRIAGE_LLM_MODEL="${TRIAGE_LLM_MODEL:-gemini/gemini-3.5-flash-lite}"
export TRIAGE_INTERNAL_MODEL="${TRIAGE_INTERNAL_MODEL:-cc/claude-haiku-4-5-20251001}"
export TRIAGE_NOTES_READ="${TRIAGE_NOTES_READ:-0}"
export TRIAGE_MAX_LINES="${TRIAGE_MAX_LINES:-40}"
export PYTHONDONTWRITEBYTECODE=1

# 3. export tin mới từ zalo-logger (Windows)
"$EXPORT" > /tmp/triage-real-export.$$ 2>&1
rc=$?
if [ "$rc" -eq 2 ]; then
  log "LỖI: export-hermes thoát mã 2 (checkpoint hỏng: MAX(rowid) < checkpoint). Bỏ lượt này, cần người xử lý (npm run export-hermes -- --reset --yes sau khi kiểm tra)."
  tail -n 3 /tmp/triage-real-export.$$ | cut -c1-200; rm -f /tmp/triage-real-export.$$
  exit 1
elif [ "$rc" -ne 0 ]; then
  log "CẢNH BÁO: export-hermes lỗi (mã $rc), vẫn xử lý trên lịch sử đã có."
  tail -n 3 /tmp/triage-real-export.$$ | cut -c1-200
fi
rm -f /tmp/triage-real-export.$$

# 4. đợi khách nhắn xong: còn tin chưa xử lý mà tin mới nhất < QUIET giây thì bỏ lượt (tối đa MAX_SKIPS lượt liên tiếp)
#    mã thoát của bước kiểm tra: 0 = có tin chờ và đã yên lặng; 1 = có tin chờ nhưng chưa yên lặng; 3 = không còn tin chờ
python3 - "$ZALO_HISTORY_DIR" "$ZALO_TICKETS_STATE" "$QUIET" <<'PY'
import json, sys, time
from pathlib import Path
hist, state, quiet = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
now = time.time()
pending = False
busy = None
for f in sorted(hist.glob("*.jsonl")):
    lines = f.read_text(encoding="utf-8").splitlines()
    cur_f = state / "cursors" / f"{f.stem}.json"
    cur = json.loads(cur_f.read_text(encoding="utf-8")).get("line", 0) if cur_f.exists() else 0
    if len(lines) <= cur:
        continue                       # nhóm này không có tin chờ xử lý
    pending = True
    newest = 0.0
    for l in reversed(lines):
        try:
            newest = float(json.loads(l).get("timestamp") or 0)
        except (ValueError, TypeError):
            continue
        if newest:
            break
    if newest and now - newest < quiet and busy is None:
        busy = f"nhóm {f.stem[-4:]}: tin mới nhất cách đây {int(now - newest)} giây (< {int(quiet)} giây)"
if busy:
    print(busy)
    sys.exit(1)
sys.exit(0 if pending else 3)
PY
qrc=$?
if [ "$qrc" -eq 1 ]; then
  skips=$(cat "$SKIPFILE" 2>/dev/null || echo 0)
  case "$skips" in ''|*[!0-9]*) skips=0;; esac
  if [ "$skips" -lt "$MAX_SKIPS" ]; then
    skips=$((skips + 1))
    setskips "$skips"
    log "Có tin mới trong ${QUIET} giây gần nhất (khách có thể còn đang nhắn), bỏ lượt này (liên tiếp $skips/$MAX_SKIPS); lượt sau xử lý."
    [ "$skips" -ge 2 ] && log "WARN: đã bỏ $skips lượt liên tiếp vì nhóm chưa yên lặng; lượt kế tiếp sẽ chạy dù còn tin mới."
    exit 0
  fi
  log "Đã bỏ $skips lượt liên tiếp: chạy lượt này dù nhóm chưa yên lặng."
fi
setskips 0     # lượt thật sẽ chạy, hoặc không còn tin chờ: đặt lại bộ đếm

# 5. triage trên STATE THẬT
timeout "$MAXRUN" python3 "$SKILL/scripts/triage_llm.py" "$@"
rc=$?
[ "$rc" -eq 124 ] && log "CẢNH BÁO: triage_llm.py chạy quá ${MAXRUN} giây nên bị dừng (cursor chỉ dời với nhóm đã xong)."
exit "$rc"
