# Bật lại task đã dừng bằng stop-logger.ps1 và chạy ngay.
$name = "zalo-logger"
if (-not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
  Write-Host "Chưa đăng ký task $name. Chạy .\setup-autostart.ps1 trước."
  exit 1
}
Enable-ScheduledTask -TaskName $name | Out-Null
# Nếu đã có tiến trình chạy tay thì không chạy thêm (2 listener sẽ đá nhau).
$running = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object { $_.CommandLine -like "*src\start.js*" })
if ($running.Count -gt 0) {
  Write-Host "Đã bật lại tự chạy. Tool đang chạy sẵn (PID $($running.ProcessId -join ', ')), không khởi động thêm."
} else {
  Start-ScheduledTask -TaskName $name
  Write-Host "Đã bật lại tự chạy và khởi động zalo-logger."
}
