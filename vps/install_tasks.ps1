# Registers two Task Scheduler tasks that start the bot at user logon.
#   powershell -ExecutionPolicy Bypass -File vps\install_tasks.ps1
#   powershell -ExecutionPolicy Bypass -File vps\install_tasks.ps1 -Uninstall
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI,
# so Thai text would be garbled. Explanation lives in vps\README.md.
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot
$tasks = [ordered]@{
    'JTrade-Scheduler'   = 'start_scheduler.bat'
    'JTrade-ExitMonitor' = 'start_exit_monitor.bat'
}

if ($Uninstall) {
    foreach ($name in $tasks.Keys) {
        Stop-ScheduledTask       -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "removed $name"
    }
    return
}

# --- pre-flight checks (warn only, never change system settings) -------------
$problems = 0
$tz = (Get-TimeZone).Id
if ($tz -ne 'SE Asia Standard Time') {
    Write-Warning ("Time zone is '$tz'. trades_log.csv, MAX_DAILY_LOSS day boundary and " +
                   "cooldowns use local machine time and were recorded in UTC+7. " +
                   "Set it with:  Set-TimeZone -Id 'SE Asia Standard Time'")
    $problems++
}
foreach ($f in '.env', 'trades_log.csv') {
    if (-not (Test-Path (Join-Path $root $f))) {
        Write-Warning "$f not found in $root - copy the latest one from the Mac first."
        $problems++
    }
}

# --- register ----------------------------------------------------------------
$user = "$env:USERDOMAIN\$env:USERNAME"
foreach ($name in $tasks.Keys) {
    $bat = Join-Path $PSScriptRoot $tasks[$name]
    $action    = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $root
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = 'PT30S'   # let the MT5 terminal and network come up first
    # Interactive = runs in the logged-on session; the MT5 terminal needs a desktop session.
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    # ExecutionTimeLimit 0 = no limit. The default (72 h) would silently kill the bot every 3 days.
    # IgnoreNew = never start a second copy (two schedulers would open duplicate orders).
    $settings  = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "registered $name -> $bat"
}

Write-Host ''
if ($problems) { Write-Warning "$problems pre-flight warning(s) above - fix them before starting." }
Write-Host 'Start now without logging off:  Start-ScheduledTask JTrade-Scheduler; Start-ScheduledTask JTrade-ExitMonitor'
