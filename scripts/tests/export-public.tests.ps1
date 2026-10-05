$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$exportScript = Join-Path $repoRoot 'scripts\export-public.ps1'
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("albumfp-export-test-" + [guid]::NewGuid().ToString('N'))
$fixture = Join-Path $sandbox 'repo'
$output = Join-Path $sandbox 'public.zip'
$inside = Join-Path $fixture 'inside.zip'
$dirtyOutput = Join-Path $sandbox 'dirty.zip'

function Invoke-Git([string]$Root, [string[]]$Arguments) {
    & git -C $Root @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Git fixture command failed: $($Arguments -join ' ')" }
}

function Invoke-Exporter([string]$Destination) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $exportScript `
        -RepositoryRoot $fixture -OutputPath $Destination | Out-Host
    return $LASTEXITCODE
}

try {
    New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'scripts') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'frontend\node_modules\sample') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'backend\.venv\Scripts') | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot 'scripts\publication_scan.py') `
        -Destination (Join-Path $fixture 'scripts\publication_scan.py')
    Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination (Join-Path $fixture '.env.example')
    Set-Content -LiteralPath (Join-Path $fixture '.gitignore') -Value ".env`nfrontend/node_modules/`nbackend/.venv/"
    Set-Content -LiteralPath (Join-Path $fixture 'README.md') -Value 'Tracked public fixture.'
    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value 'LOCAL_SECRET_SENTINEL'
    Set-Content -LiteralPath (Join-Path $fixture 'frontend\node_modules\sample\index.js') -Value 'ignored dependency'
    Set-Content -LiteralPath (Join-Path $fixture 'backend\.venv\Scripts\python.exe') -Value 'ignored runtime'

    & git -C $fixture init --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the temporary Git fixture.' }
    Invoke-Git $fixture @('config', 'user.name', 'AlbumFP Demo test')
    Invoke-Git $fixture @('config', 'user.email', 'albumfp-demo@example.invalid')
    Invoke-Git $fixture @('add', '.gitignore', '.env.example', 'README.md', 'scripts/publication_scan.py')
    Invoke-Git $fixture @('commit', '--quiet', '-m', 'fixture')

    if ((Invoke-Exporter $inside) -eq 0) { throw 'An export path inside the repository was accepted.' }
    if (Test-Path -LiteralPath $inside) { throw 'The rejected in-repository ZIP was created.' }

    if ((Invoke-Exporter $output) -ne 0) { throw 'Safe export failed for a clean tracked fixture.' }
    if (-not (Test-Path -LiteralPath $output)) { throw 'Safe export did not create the ZIP.' }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($output)
    try {
        $entries = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\\', '/') })
    } finally { $archive.Dispose() }
    foreach ($required in @('README.md', '.env.example', 'scripts/publication_scan.py')) {
        if ($required -notin $entries) { throw "ZIP is missing tracked file: $required" }
    }
    foreach ($entry in $entries) {
        if ($entry -match '(^|/)(\.git|\.env|node_modules|\.venv|venv|postgresql|pgdata|pg_wal|runtime|backups|exports)(/|$)') {
            throw "ZIP contains a forbidden local entry: $entry"
        }
    }
    if ($entries -contains 'LOCAL_SECRET_SENTINEL') { throw 'A local secret entered the ZIP.' }

    Add-Content -LiteralPath (Join-Path $fixture 'README.md') -Value 'dirty worktree'
    if ((Invoke-Exporter $dirtyOutput) -eq 0) { throw 'An export from a dirty worktree was accepted.' }
    if (Test-Path -LiteralPath $dirtyOutput) { throw 'The rejected dirty-worktree ZIP was created.' }

    $digest = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
    Write-Host "SAFE_EXPORT=PASS SHA256=$digest"
} finally {
    $sandboxFullPath = [System.IO.Path]::GetFullPath($sandbox).TrimEnd('\') + '\'
    $tempFullPath = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $sandboxFullPath.StartsWith($tempFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to remove a test directory outside the system temporary directory.'
    }
    if (Test-Path -LiteralPath $sandboxFullPath) { Remove-Item -LiteralPath $sandboxFullPath -Recurse -Force }
}
