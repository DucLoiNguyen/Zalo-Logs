# Dừng tool và chặn Task Scheduler tự chạy lại (khác `setup-autostart.ps1 -Remove`: task vẫn còn, bật lại bằng resume-logger.ps1).
$name = "zalo-logger"
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Disable-ScheduledTask -TaskName $name | Out-Null
  Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
} else {
  Write-Host "Chưa đăng ký task $name (chạy .\setup-autostart.ps1), chỉ dừng tiến trình đang chạy tay (nếu có)."
}
# Dừng cả tiến trình chạy tay (npm start / npm run dev) nếu còn.
$procs = Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
  Where-Object { $_.CommandLine -like "*src\start.js*" }
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
$left = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object { $_.CommandLine -like "*src\start.js*" })
if ($left.Count -eq 0) {
  Write-Host "Đã dừng zalo-logger và tắt tự chạy lại. Bật lại: npm run resume"
} else {
  Write-Host "⚠ Vẫn còn tiến trình start.js (PID $($left.ProcessId -join ', ')). Đóng cửa sổ đó hoặc Ctrl+C."
  exit 1
}
