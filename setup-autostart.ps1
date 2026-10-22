# Đăng ký tự chạy `npm start` khi đăng nhập Windows (Task Scheduler, không cần quyền admin).
#   .\setup-autostart.ps1            # cài
#   .\setup-autostart.ps1 -Remove    # gỡ
param([switch]$Remove)
$name = "zalo-logger"
if ($Remove) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Đã gỡ tác vụ $name."
  return
}
$root = $PSScriptRoot
$node = (Get-Command node).Source
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c `"`"$node`" src\start.js >> data\start.log 2>&1`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
  -Description "Log tin nhắn nhóm Zalo" -Force | Out-Null
Write-Host "Đã cài. Tự chạy khi đăng nhập; log ở data\start.log. Chạy thử ngay: Start-ScheduledTask $name"
