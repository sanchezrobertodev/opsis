<#
.SYNOPSIS
  File away the Opsis backups a server sends with Taildrop (infra/server/offsite.sh).

.DESCRIPTION
  Taildrop saves incoming files to the Downloads folder.
  - opsis-media-*.tar: bundles of media files. Each is extracted into <Dest>\media (a mirror of the server's
    data\media that never deletes anything) once it reads to the end, then removed. A bundle still arriving, or
    damaged, is left in place and tried again next run.
  - opsis-*.dump: database dumps (pg_dump custom format). They move into <Dest>\db once complete, and the newest
    -KeepDays days are kept, judged by the date in the file name.
  Files named ams-* (what the server sent before the rename to Opsis) are collected the same way.
  Run hourly and at logon by the scheduled task install-backup-task.ps1 registers; a log is kept in <Dest>.

  Restore: copy <Dest>\media back over the server's data/media, put a dump in data/backups and run
  `podman exec opsis-worker python scripts/restore_db.py --replace --file data/backups/<dump> --yes` with the API and
  worker stopped (infra/server/README.md).
#>
param(
    [string]$Source = (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path,
    [string]$Dest = (Join-Path $HOME "Backups\opsis"),
    [int]$KeepDays = 60
)
$ErrorActionPreference = "Stop"
$media = Join-Path $Dest "media"
$db = Join-Path $Dest "db"
New-Item -ItemType Directory -Force -Path $media, $db | Out-Null
$log = Join-Path $Dest "collect.log"
$tar = Join-Path $env:SystemRoot "System32\tar.exe"

function Write-Log([string]$message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" | Add-Content -Path $log -Encoding utf8
}

function Test-Settled([IO.FileInfo]$file) {
    # Taildrop renames a file into place when it's whole; a file still changing is left for the next run
    return ((Get-Date) - $file.LastWriteTime).TotalMinutes -ge 2
}

foreach ($bundle in Get-ChildItem -Path (Join-Path $Source '*') -Include 'opsis-media-*.tar', 'ams-media-*.tar' -File -ErrorAction SilentlyContinue | Sort-Object Name) {
    if (-not (Test-Settled $bundle)) { continue }
    $count = (& $tar -tf $bundle.FullName | Measure-Object).Count
    if ($LASTEXITCODE -ne 0 -or $count -eq 0) {
        Write-Log "not readable yet (still arriving, or damaged): $($bundle.Name) left in $Source"
        continue
    }
    & $tar -xf $bundle.FullName -C $Dest
    if ($LASTEXITCODE -ne 0) {
        Write-Log "could not extract $($bundle.Name); left in $Source"
        continue
    }
    Remove-Item -LiteralPath $bundle.FullName
    Write-Log "extracted $($bundle.Name): $count files ($([math]::Round($bundle.Length / 1MB)) MB)"
}

foreach ($dump in Get-ChildItem -Path (Join-Path $Source '*') -Include 'opsis-*.dump', 'ams-*.dump' -File -ErrorAction SilentlyContinue) {
    if (-not (Test-Settled $dump)) { continue }
    $head = New-Object byte[] 5
    $stream = [IO.File]::OpenRead($dump.FullName)
    try { [void]$stream.Read($head, 0, 5) } finally { $stream.Dispose() }
    if ([Text.Encoding]::ASCII.GetString($head) -ne "PGDMP") {
        Write-Log "not a pg_dump file: $($dump.Name) left in $Source"
        continue
    }
    Move-Item -LiteralPath $dump.FullName -Destination (Join-Path $db $dump.Name) -Force
    Write-Log "collected $($dump.Name) ($([math]::Round($dump.Length / 1KB)) KB)"
}

function Get-DumpDate([IO.FileInfo]$file) {
    # opsis-studio-YYYYMMDD-HHMMSS.dump; fall back to the file time
    if ($file.Name -match '-(\d{8})-(\d{6})\.dump$') {
        return [datetime]::ParseExact("$($Matches[1])$($Matches[2])", 'yyyyMMddHHmmss', $null)
    }
    return $file.LastWriteTime
}

$cutoff = (Get-Date).AddDays(-$KeepDays)
foreach ($old in Get-ChildItem -Path (Join-Path $db '*') -Include 'opsis-*.dump', 'ams-*.dump' -File | Where-Object { (Get-DumpDate $_) -lt $cutoff }) {
    Remove-Item -LiteralPath $old.FullName
    Write-Log "pruned $($old.Name) (older than $KeepDays days)"
}
