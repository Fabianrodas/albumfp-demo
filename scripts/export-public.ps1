param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'

function Test-ForbiddenArchiveEntry([string]$EntryName) {
    $name = $EntryName.Replace('\', '/').TrimStart('/').ToLowerInvariant()
    $parts = @($name.Split('/', [System.StringSplitOptions]::RemoveEmptyEntries))
    if ($parts.Count -eq 0 -or $name.StartsWith('../') -or $name -match '^[a-z]:/') {
        return $true
    }
    if ($parts -contains '..' -or $parts -contains '.git') { return $true }
    foreach ($part in $parts) {
        if (($part -eq '.env' -or $part.StartsWith('.env.')) -and $name -ne '.env.example') {
            return $true
        }
        if ($part -in @('node_modules', '.venv', 'venv', '.angular', '.pytest_cache',
                '.mypy_cache', '.ruff_cache', '.npm-cache', '.codex-local', '.superpowers',
                'playwright-report', 'test-results', 'coverage')) {
            return $true
        }
    }
    if ($name -match '^(?:media|tmp|quarantine|exports|backups|postgresql|pgdata|pg_wal)(?:/|$)') {
        return $true
    }
    if ($name -match '^backend/(?:storage|uploads|data|runtime|tmp|quarantine|postgresql|pgdata|pg_wal)(?:/|$)') {
        return $true
    }
    if ($name -match '(?:^|/)(?:\.agents|\.aws|\.codex|\.ssh|deploy|deployment|infrastructure|infra|operations|ops|rollback)(?:/|$)') {
        return $true
    }
    if ($name -match '(?:\.dump|\.backup|\.bak|\.log|\.sqlite|\.sqlite3|\.db|\.sql\.gz|\.sql\.zip)$') {
        return $true
    }
    if ($name -match '\.sql$' -and $name -ne 'backend/schemas/schema.sql') {
        return $true
    }
    return $false
}

function Assert-OutsideRepository([string]$Path, [string]$Root) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    $rootPrefix = $rootFull.TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'The export ZIP must be written outside the repository.'
    }

    $cursor = Split-Path -Parent $fullPath
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'The export path cannot use a symbolic link or junction.'
            }
        }
        $parent = Split-Path -Parent $cursor
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-PhysicalRepositoryRoot([string]$Root) {
    $cursor = [System.IO.Path]::GetFullPath($Root)
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'The repository path cannot use a symbolic link or junction.'
            }
        }
        $parent = Split-Path -Parent $cursor
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

$root = (Resolve-Path -LiteralPath $RepositoryRoot).Path
Assert-PhysicalRepositoryRoot $root
if (-not (Test-Path -LiteralPath (Join-Path $root '.git'))) {
    throw "Repository root is not a Git checkout: $root"
}

$status = & git -C $root status --porcelain=v1 --untracked-files=all
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Git worktree status.' }
if (-not [string]::IsNullOrWhiteSpace(($status -join ''))) {
    throw 'Safe export requires a clean Git worktree and index.'
}

$python = Get-Command python -ErrorAction Stop
$scanner = Join-Path $root 'scripts\publication_scan.py'
if (-not (Test-Path -LiteralPath $scanner)) { throw 'The publication scanner is missing.' }
& $python.Source $scanner
if ($LASTEXITCODE -ne 0) { throw 'The publication scan failed; no ZIP was created.' }

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is unavailable; pass an output path outside the repository.'
    }
    $exportDirectory = Join-Path $env:LOCALAPPDATA 'AlbumFP-Demo\exports'
    $head = (& git -C $root rev-parse --short=12 HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the current Git commit.' }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutputPath = Join-Path $exportDirectory "albumfp-demo-public-$head-$stamp.zip"
}

$output = [System.IO.Path]::GetFullPath($OutputPath)
Assert-OutsideRepository $output $root
if ([System.IO.Path]::GetExtension($output) -ne '.zip') {
    throw 'The export output path must end in .zip.'
}
if (Test-Path -LiteralPath $output) { throw 'The export output already exists; choose a new path.' }

$parent = Split-Path -Parent $output
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
Assert-OutsideRepository $output $root

& git -C $root archive --format=zip "--output=$output" HEAD
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue
    throw 'Git could not create the tracked-file archive.'
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($output)
try {
    $entries = @($archive.Entries)
    if ($entries.Count -eq 0) { throw 'The tracked-file archive is empty.' }
    foreach ($entry in $entries) {
        if (Test-ForbiddenArchiveEntry $entry.FullName) {
            throw "The archive contains a forbidden local or private path: $($entry.FullName)"
        }
    }
} catch {
    $archive.Dispose()
    Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue
    throw
} finally {
    if ($archive) { $archive.Dispose() }
}

$digest = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
Write-Host "SAFE_EXPORT=PASS SHA256=$digest"
