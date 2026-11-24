#Requires -Version 5.1
<#
.SYNOPSIS
  Create a new, non-overwriting PostgreSQL and media backup.
.DESCRIPTION
  No retention cleanup is performed. Failed attempts remain for inspection and
  lack COMPLETE.txt; only directories with COMPLETE.txt are complete backups.
  Chroma is not backed up. The SQL and media copies are not a single atomic
  snapshot: pause application writes externally if consistency is required.
  Backups contain sensitive data; protect the output directory and copies.
.EXAMPLE
  powershell -NoProfile -File scripts\backup.ps1 -OutDir D:\backups
#>
[CmdletBinding()]
param(
    [int]$Keep = 5,
    [string]$OutDir = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'docker-compose.yml'
if (-not $OutDir) { $OutDir = Join-Path $root 'backup' }
if ($PSBoundParameters.ContainsKey('Keep')) {
    Write-Warning '-Keep is deprecated and ignored. No backups are deleted.'
}

# Use a GUID so a previous backup is never reused or overwritten. The marker
# is created exclusively before any other file is written into the directory.
$target = Join-Path $OutDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null
    $markerPath = Join-Path $target 'ATTEMPT.txt'
    $marker = [System.IO.File]::Open($markerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $marker.Dispose()
    Write-Host "Backup attempt: $target"

    # Use the raw stdout byte stream: PowerShell 5.1 native pipelines convert
    # binary output to text and can corrupt UTF-8 SQL or alter newlines.
    $dbFile = Join-Path $target 'database.sql'
    $file = [System.IO.File]::Open($dbFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'docker'
        # Windows command-line quoting: double backslashes immediately before
        # quotes and at the end of the quoted compose path.
        $quotedCompose = '"' + ($compose -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
        $psi.Arguments = "compose -f $quotedCompose exec -T postgres pg_dump -U portfolio portfolio"
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $psi
        if (-not $process.Start()) { throw 'Could not start docker.' }
        try {
            $stderr = $process.StandardError.ReadToEndAsync()
            $process.StandardOutput.BaseStream.CopyTo($file)
            $process.WaitForExit()
            $errorText = $stderr.GetAwaiter().GetResult()
            if ($process.ExitCode -ne 0) { throw "pg_dump failed (exit $($process.ExitCode)): $errorText" }
        } finally { $process.Dispose() }
    } finally { $file.Dispose() }
    if ((Get-Item -LiteralPath $dbFile).Length -eq 0) { throw 'pg_dump produced empty output.' }

    $mediaTarget = Join-Path $target 'media'
    New-Item -ItemType Directory -Path $mediaTarget -ErrorAction Stop | Out-Null
    & docker compose -f $compose cp 'backend:/app/data/media/.' $mediaTarget
    if ($LASTEXITCODE -ne 0) { throw "Media copy failed (exit $LASTEXITCODE)." }

    # Record every file's size and SHA-256 for later integrity checks. Include
    # hidden files and use relative paths; JSON preserves Unicode filenames.
    $entries = @(Get-ChildItem -LiteralPath $mediaTarget -File -Recurse -Force | ForEach-Object {
        [ordered]@{ path = 'media/' + $_.FullName.Substring($mediaTarget.Length).TrimStart([char[]]'\/').Replace('\','/'); bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
    $db = Get-Item -LiteralPath $dbFile
    $manifest = [ordered]@{
        format = 1
        createdUtc = [DateTime]::UtcNow.ToString('o')
        database = [ordered]@{ path = 'database.sql'; bytes = $db.Length; sha256 = (Get-FileHash -LiteralPath $dbFile -Algorithm SHA256).Hash }
        media = $entries
        limitations = 'Database and media are not an atomic snapshot; Chroma is excluded.'
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $target 'manifest.json'), (ConvertTo-Json -InputObject $manifest -Depth 6), $utf8)
    $instructions = @'
This backup contains sensitive data. Restrict access and keep an offline copy.
Verify database.sql and every media file against manifest.json before restoring.
Chroma is NOT included. SQL and media are not an atomic snapshot; stop writes
before creating a backup when a consistent application snapshot is required.

Restore ONLY to a separate, disposable, empty PostgreSQL database and media
location first. Confirm the destination explicitly: restoring overwrites or
changes its data. Do not run these commands against production by accident.
From a Windows cmd.exe prompt, with an isolated compose project selected:
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U portfolio -d portfolio < database.sql
  docker compose cp media/. backend:/app/data/media/
Check both commands' exit codes and verify application data and media. Use an
empty target: pg_dump plain SQL is not a replacement for dropping old objects,
and copying media does not remove files that exist only at the destination.
Rebuild the excluded Chroma index separately according to application procedure.
'@
    [System.IO.File]::WriteAllText((Join-Path $target 'RESTORE.txt'), $instructions, $utf8)
    # Create the completion marker last. Never delete even an incomplete attempt.
    $complete = [System.IO.File]::Open((Join-Path $target 'COMPLETE.txt'), [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $complete.Dispose()
    Write-Host "Backup complete: $target ($($entries.Count) media files)."
} catch {
    Write-Error "Backup failed; incomplete attempt retained at '$target': $_" -ErrorAction Continue
    exit 1
}
