<#
.SYNOPSIS
  Registers (or removes) the studio's two Windows scheduled tasks:
  - nightly at 03:30: database backup (scripts/backup_db.py), log in data/logs/backup.log
  - Mondays at 09:00: provider schema check (scripts/fetch_provider_schemas.py), report in data/reports/
  Both run as you, only while you're signed in, and catch up at the next sign-in or wake if a run was missed.
  The backup needs Docker running.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/schedule_tasks.ps1          # register or update
  powershell -ExecutionPolicy Bypass -File scripts/schedule_tasks.ps1 -Remove  # remove both
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root "data\logs"
$tasks = @(
  @{ Name = "AI Media Studio - nightly database backup"
     Script = "scripts/backup_db.py"; Log = "backup.log"
     Trigger = New-ScheduledTaskTrigger -Daily -At "03:30" },
  @{ Name = "AI Media Studio - weekly provider schema check"
     Script = "scripts/fetch_provider_schemas.py"; Log = "schema-drift.log"
     Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "09:00" }
)

foreach ($t in $tasks) {
  if ($Remove) {
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed: $($t.Name)"
    continue
  }
  $uv = (Get-Command uv -ErrorAction Stop).Source
  New-Item -ItemType Directory -Force $logs | Out-Null
  $log = Join-Path $logs $t.Log
  $command = "& '$uv' run python $($t.Script) *>> '$log'"
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -Command `"$command`""
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
  Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $t.Trigger -Settings $settings `
    -Description "AI Media Studio ($($t.Script)); see CLAUDE.md, Backups and schema checks." -Force | Out-Null
  Write-Output "Registered: $($t.Name) -> $log"
}
