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
# Chạy node trực tiếp (không chuyển hướng output) để cửa sổ console hiện banner và log;
# tool tự ghi log vào data\start.log (src/filelog.js).
$action = New-ScheduledTaskAction -Execute $node -Argument "src\start.js" -WorkingDirectory $root
# Trigger 1: khi đăng nhập. Trigger 2: lặp mỗi 5 phút; MultipleInstances=IgnoreNew nên khi tool đang chạy thì bỏ qua,
# còn khi tool thoát (watchdog trong start.js thoát với mã lỗi) thì tối đa 5 phút sau được chạy lại.
# (Cài đặt "restart on failure" của Task Scheduler KHÔNG kích hoạt khi tiến trình thoát mã khác 0, đã thử.)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $repeat) -Settings $settings `
  -Description "Log tin nhắn nhóm Zalo" -Force | Out-Null
Write-Host "Đã cài. Tự chạy khi đăng nhập; console hiện log, file log: data\start.log. Chạy thử ngay: Start-ScheduledTask $name"
