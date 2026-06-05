# Registers the Morning Energy Report as a daily 06:00 Windows Task.
# Wakes the PC from sleep, and runs as soon as possible if a run was missed.
$ErrorActionPreference = 'Stop'

$taskName = 'MorningEnergyReport'
$bat = 'C:\Users\TomDay\MorningReport\run_report.bat'

$action  = New-ScheduledTaskAction -Execute $bat -WorkingDirectory (Split-Path $bat)
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00am

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# Run as the current user, only when logged on (Outlook COM needs an interactive session).
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'Daily morning energy-market briefing emailed via Outlook.' -Force | Out-Null

Write-Output "Registered '$taskName' for 06:00 daily (wake-to-run + run-if-missed)."
