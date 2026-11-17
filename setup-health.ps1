# Đăng ký tác vụ "hermes-health": mỗi 5 phút chạy `node src\health.js` (kiểm tra cron Hermes còn chạy không).
#   .\setup-health.ps1 [-OneDriveDir <thư mục>] [-DryRun]   # cài
#   .\setup-health.ps1 -Remove                               # gỡ
# Quá ngưỡng (triage 40 phút khi chạy 30 phút, 30 phút khi chạy 15 phút) hoặc 9router/WSL không phản hồi thì: toast Windows + ghi
# data\health\HEALTH.txt (+ chép sang -OneDriveDir nếu có). Báo MỘT lần mỗi đợt sự cố rồi báo phục hồi. KHÔNG tự khởi động lại gì
# (không Start-ScheduledTask). Hằng ngày tự ghi data\health\cron-latency-<ngày>.md (chỉ đọc). Không ghi bí mật.
# Chạy ẩn qua conhost --headless (Windows 11) để không nháy cửa sổ mỗi 5 phút.
param([switch]$Remove, [switch]$DryRun, [string]$OneDriveDir = "", [string]$Distro = "Ubuntu-24.04")
$ErrorActionPreference = "Stop"
$name = "hermes-health"
if ($Remove) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Đã gỡ tác vụ $name."
  return
}
$root = $PSScriptRoot
$node = (Get-Command node).Source
$args1 = "src\health.js --distro $Distro"
if ($OneDriveDir) { $args1 += " --onedrive-dir `"$OneDriveDir`"" }
$action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless `"$node`" $args1" -WorkingDirectory $root
$user = "$env:USERDOMAIN\$env:USERNAME"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Write-Host "Tác vụ $name : conhost.exe --headless $node $args1"
Write-Host "  trigger: đăng nhập + lặp mỗi 5 phút (IgnoreNew); thư mục làm việc: $root"
if ($DryRun) { Write-Host "(dry-run) chưa đăng ký."; return }
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $repeat) -Settings $settings `
  -Description "Kiểm tra cron Hermes; báo toast + HEALTH.txt khi quá ngưỡng" -Force | Out-Null
Write-Host "Đã cài. Chạy thử ngay (chỉ in, không báo): node src\health.js --dry-run"
