# Đăng ký lại 2 tác vụ Task Scheduler của Hermes (9router, HermesGateway) để TỰ SỐNG LẠI khi tiến trình thoát hoặc máy vừa ngủ dậy.
#   .\setup-hermes-autostart.ps1 -DryRun     # chỉ in kế hoạch, không đổi gì
#   .\setup-hermes-autostart.ps1             # cài (sao lưu XML gốc lần đầu vào data\task-backup\<tên>-original.xml)
#   .\setup-hermes-autostart.ps1 -Rollback   # trả về đúng định nghĩa gốc đã sao lưu
#
# Vì sao: hai tác vụ cũ chỉ có trigger "khi đăng nhập"; khi wsl.exe bị kết thúc (máy ngủ, SIGINT từ Relay) thì tác vụ về "Ready" và không
# chạy lại cho đến lần đăng nhập sau. Cài đặt "restart on failure" của Task Scheduler không kích hoạt khi thoát kiểu này (đã thử, xem CLAUDE.md).
# Cách làm giống setup-autostart.ps1 của logger: trigger đăng nhập + lặp mỗi 5 phút với MultipleInstances=IgnoreNew (đang chạy thì bỏ qua,
# đã thoát thì tối đa 5 phút sau chạy lại) + trigger sự kiện Kernel-Power 107 ("hệ thống thức dậy sau khi ngủ") để chạy lại ngay.
# Thứ tự: 9router lên trước; HermesGateway chờ http://localhost:20128/v1/models trả 200 (tối đa 10 phút rồi vẫn chạy để job không cần model còn chạy).
# Không cần quyền admin cho phần đăng ký tác vụ; bật nhật ký Task Scheduler (wevtutil) CẦN admin: nếu thiếu quyền, script in lệnh để chạy tay.
param([switch]$Rollback, [switch]$DryRun, [string]$Distro = "Ubuntu-24.04", [string]$BackupDir = (Join-Path $PSScriptRoot "data\task-backup"))

$ErrorActionPreference = "Stop"
$names = @("9router", "HermesGateway")
$user = "$env:USERDOMAIN\$env:USERNAME"

function Backup-Original([string]$name) {
  New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
  $file = Join-Path $BackupDir "$name-original.xml"
  if (Test-Path $file) { Write-Host "  [$name] đã có bản gốc: $file (giữ nguyên, không ghi đè)"; return }
  if ($DryRun) { Write-Host "  [$name] (dry-run) sẽ lưu XML gốc vào $file"; return }
  Export-ScheduledTask -TaskName $name | Out-File -FilePath $file -Encoding Unicode
  Write-Host "  [$name] đã lưu XML gốc: $file"
}

if ($Rollback) {
  foreach ($n in $names) {
    $file = Join-Path $BackupDir "$n-original.xml"
    if (-not (Test-Path $file)) { Write-Warning "Không thấy $file, bỏ qua $n"; continue }
    if ($DryRun) { Write-Host "(dry-run) sẽ khôi phục $n từ $file"; continue }
    Register-ScheduledTask -TaskName $n -Xml (Get-Content -Raw -Path $file) -Force | Out-Null
    Write-Host "Đã khôi phục $n từ $file"
  }
  return
}

# --- hành động
$act9 = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -e bash -lc `"source ~/.nvm/nvm.sh; exec 9router -H 127.0.0.1 -n -l --skip-update`""
$wait = 'n=0; until curl -sf -m 3 -o /dev/null http://localhost:20128/v1/models || [ $n -ge 120 ]; do n=$((n+1)); sleep 5; done; exec hermes gateway run'
$actGw = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -e bash -lc `"export PATH=`$HOME/.local/bin:`$PATH; $wait`""

# --- trigger: đăng nhập + lặp 5 phút + thức dậy sau khi ngủ
$resumeXml = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Kernel-Power''] and EventID=107]]</Select></Query></QueryList>'
function New-Triggers([string]$delay) {
  $logon = New-ScheduledTaskTrigger -AtLogOn -User $user
  $logon.Delay = $delay
  $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
  $cls = Get-CimClass -Namespace Root/Microsoft/Windows/TaskScheduler -ClassName MSFT_TaskEventTrigger
  $resume = New-CimInstance -CimClass $cls -ClientOnly
  $resume.Enabled = $true
  $resume.Subscription = $resumeXml
  $resume.Delay = $delay
  return @($logon, $repeat, $resume)
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

$plan = @(
  @{ Name = "9router";       Action = $act9;  Delay = "PT0S";  Desc = "9router (proxy model cho Hermes); tự chạy lại sau khi thoát/ngủ dậy" },
  @{ Name = "HermesGateway"; Action = $actGw; Delay = "PT20S"; Desc = "Hermes gateway (cron); chờ 9router sẵn sàng; tự chạy lại sau khi thoát/ngủ dậy" }
)
foreach ($p in $plan) {
  Write-Host "== $($p.Name)"
  Backup-Original $p.Name
  Write-Host "  hành động : wsl.exe $($p.Action.Arguments)"
  Write-Host "  trigger   : đăng nhập (trễ $($p.Delay)), lặp mỗi 5 phút (IgnoreNew), Kernel-Power 107 (trễ $($p.Delay))"
  if ($DryRun) { continue }
  Register-ScheduledTask -TaskName $p.Name -Action $p.Action -Trigger (New-Triggers $p.Delay) -Settings $settings -Principal $principal `
    -Description $p.Desc -Force | Out-Null
  Write-Host "  đã đăng ký."
}

# --- nhật ký Task Scheduler (mặc định tắt): cần admin
if ($DryRun) { Write-Host "(dry-run) sẽ bật nhật ký: wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true"; return }
$ErrorActionPreference = "Continue"   # wevtutil ghi lỗi ra stderr khi thiếu quyền: chỉ cảnh báo, không dừng
$out = & wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true 2>&1
if ($LASTEXITCODE -eq 0) { Write-Host "Đã bật nhật ký Task Scheduler (Microsoft-Windows-TaskScheduler/Operational)." }
else { Write-Warning "Chưa bật được nhật ký Task Scheduler (cần quyền admin). Mở PowerShell 'Run as administrator' và chạy: wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true"; Write-Host ($out | Out-String).Trim() }
Write-Host "Xong. Kiểm tra: Get-ScheduledTask 9router,HermesGateway | Get-ScheduledTaskInfo"
