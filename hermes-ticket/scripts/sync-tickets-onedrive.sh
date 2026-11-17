#!/usr/bin/env bash
# Chép jsm_import.csv + jsm_requests.json + review.csv (tin [Cần xem]) sang thư mục OneDrive cho team (chỉ khi nội dung đổi).
# Chạy bằng cron --no-agent (không tốn LLM); không in gì khi không có thay đổi.
# CSV nằm ở zalo\tickets\ còn media ở zalo\media\, nên bản CSV chép đi được đổi "| media/" thành "| ../media/"
# để đường dẫn tệp đính kèm mở được từ chính thư mục tickets\. Bản gốc trong profile giữ nguyên.
set -euo pipefail
SRC="${HERMES_HOME:-/home/posei/.hermes/profiles/ticket}/zalo-tickets/export"
DEST="${TICKETS_ONEDRIVE_DIR:-/mnt/c/Users/posei/OneDrive - C-OPENAI/Premier Service Team - Tạo tài khoản GOV_260916/zalo/tickets}"
[ -f "$SRC/jsm_import.csv" ] || [ -f "$SRC/review.csv" ] || exit 0     # chưa có ticket nào
mkdir -p "$DEST"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
n=0
if [ -f "$SRC/jsm_import.csv" ]; then
  sed 's#| media/#| ../media/#g' "$SRC/jsm_import.csv" > "$tmp"
  if ! cmp -s "$tmp" "$DEST/jsm_import.csv"; then cp "$tmp" "$DEST/jsm_import.csv"; n=$((n + 1)); fi
fi
if [ -f "$SRC/review.csv" ] && ! cmp -s "$SRC/review.csv" "$DEST/review.csv"; then
  cp "$SRC/review.csv" "$DEST/review.csv"; n=$((n + 1))
fi
if [ -f "$SRC/jsm_requests.json" ] && ! cmp -s "$SRC/jsm_requests.json" "$DEST/jsm_requests.json"; then
  cp "$SRC/jsm_requests.json" "$DEST/jsm_requests.json"; n=$((n + 1))
fi
[ "$n" -gt 0 ] && echo "Đã chép $n file ticket sang $DEST"
exit 0
