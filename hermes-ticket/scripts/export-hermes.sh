#!/usr/bin/env bash
set -euo pipefail
cd /mnt/c   # tránh cảnh báo UNC của cmd.exe khi cwd nằm trong \wsl$
cmd.exe /c "cd /d D:\zalo-logger && node src\exportHermes.js --quiet" 2>&1 | tr -d "\r"
