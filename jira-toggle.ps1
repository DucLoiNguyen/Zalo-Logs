param(
    [Parameter(Position = 0)][ValidateSet('on', 'off', 'status')][string]$Action = 'status',
    [switch]$FromNow
)
# Bat/tat day ticket len Jira (jira.enabled trong cs_config.json cua Hermes). -FromNow: dat lai moc start = bay gio khi bat.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$args2 = @($Action)
if ($FromNow -and $Action -eq 'on') { $args2 += '--from-now' }
wsl -d Ubuntu-24.04 -- python3 /mnt/d/zalo-logger/hermes-ticket/scripts/jira_toggle.py @args2
