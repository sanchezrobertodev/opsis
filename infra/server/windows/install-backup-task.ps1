<#
.SYNOPSIS
  Register the scheduled task that runs collect-backups.ps1 at logon and every hour.

.DESCRIPTION
  A per-user task (no administrator rights needed) that runs while you are signed in, which is also when Taildrop
  delivers. Re-run to update it; remove it with
  Unregister-ScheduledTask -TaskName "AI Media Studio backup collect".
#>
param([string]$Dest = "E:\Backups\ai-media-studio", [int]$KeepDays = 60)
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "collect-backups.ps1"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Dest `"$Dest`" -KeepDays $KeepDays"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "AI Media Studio backup collect" -Action $action -Trigger $hourly, $logon `
    -Settings $settings -Description "Files the AI Media Studio backups the home server sends with Taildrop into $Dest" `
    -Force | Out-Null
Write-Output "Registered: AI Media Studio backup collect -> $Dest"
