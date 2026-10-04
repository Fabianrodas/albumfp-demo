$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AlbumFP-Demo'
$postgresData = Join-Path $runtimeRoot 'postgresql'
$postgresMarker = Join-Path $postgresData '.albumfp-demo-cluster'
$postgresPort = 55432

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.env'))) {
    throw 'Run .\setup.ps1 first to create local configuration and the Demo databases.'
}
if (-not (Test-Path -LiteralPath $postgresMarker) -or
    (Get-Content -LiteralPath $postgresMarker -Raw).Trim() -ne 'AlbumFP Demo local cluster') {
    throw 'The Demo PostgreSQL cluster marker is missing; run .\setup.ps1 first.'
}

$entries = Get-Content -LiteralPath (Join-Path $repoRoot '.env') | Where-Object { $_ -match '^\s*[^#\s][^=]*=' }
$settings = $entries | ConvertFrom-StringData
if ($settings.POSTGRES_HOST -ne '127.0.0.1' -or [int]$settings.POSTGRES_PORT -ne $postgresPort -or
    $settings.DEV_HOST -notin @('localhost', '127.0.0.1', '::1') -or
    $settings.FRONTEND_HOST -notin @('localhost', '127.0.0.1', '::1')) {
    throw 'All Demo services must use their configured loopback addresses and PostgreSQL port 55432.'
}

foreach ($entry in $settings.GetEnumerator()) {
    if ($entry.Key -notlike 'POSTGRES_ADMIN_*') {
        Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
    }
}
$env:DEV_HOST = '127.0.0.1'
$env:FRONTEND_HOST = '127.0.0.1'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = [string]$postgresPort

$postgresBin = $null
foreach ($version in @(18, 17, 16, 15, 14)) {
    $candidate = Join-Path $env:ProgramFiles "PostgreSQL\$version\bin"
    if (Test-Path (Join-Path $candidate 'pg_ctl.exe')) { $postgresBin = $candidate; break }
}
if (-not $postgresBin) { throw 'The local PostgreSQL installation was not found.' }
$pgCtl = Join-Path $postgresBin 'pg_ctl.exe'
$wasRunning = $false
& $pgCtl -D $postgresData status *> $null
if ($LASTEXITCODE -eq 0) {
    $wasRunning = $true
} else {
    $listener = Get-NetTCPConnection -LocalPort $postgresPort -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $postgresPort belongs to another process; refusing to connect." }
    & $pgCtl -D $postgresData -l (Join-Path $runtimeRoot 'postgresql.log') -o "-h 127.0.0.1 -p $postgresPort" start
    if ($LASTEXITCODE -ne 0) { throw 'The Demo PostgreSQL cluster could not be started.' }
}

$python = Join-Path $backendDir '.venv\Scripts\python.exe'
$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not (Test-Path -LiteralPath $python) -or -not $npm -or
    -not (Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules'))) {
    if (-not $wasRunning) { & $pgCtl -D $postgresData stop -m fast *> $null }
    throw 'Run .\setup.ps1 to install the local dependencies before starting AlbumFP Demo.'
}

$backendJob = $null
$frontendJob = $null
try {
    $backendJob = Start-Job -Name AlbumFPDemoBackend -ArgumentList $backendDir, $python -ScriptBlock {
        param($WorkingDirectory, $PythonPath)
        Set-Location -LiteralPath $WorkingDirectory
        & $PythonPath app.py
    }
    $frontendJob = Start-Job -Name AlbumFPDemoFrontend -ArgumentList $frontendDir, $npm -ScriptBlock {
        param($WorkingDirectory, $NpmPath)
        Set-Location -LiteralPath $WorkingDirectory
        & $NpmPath run start -- --host 127.0.0.1 --port 4200
    }

    Write-Host 'AlbumFP Demo is starting. Services listen only on loopback.' -ForegroundColor Green
    Write-Host 'App:     http://localhost:4200'
    Write-Host 'Loopback: http://127.0.0.1:4200'
    Write-Host 'Backend: http://127.0.0.1:5000'
    Write-Host 'Press Ctrl+C to stop the Demo jobs and the PostgreSQL cluster started by this script.'

    while ($backendJob.State -eq 'Running' -and $frontendJob.State -eq 'Running') {
        Receive-Job -Job $backendJob -ErrorAction SilentlyContinue
        Receive-Job -Job $frontendJob -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    if ($backendJob.State -ne 'Running') { throw 'The Demo backend stopped; review the output above.' }
    if ($frontendJob.State -ne 'Running') { throw 'The Demo frontend stopped; review the output above.' }
} finally {
    if ($backendJob) { Stop-Job -Job $backendJob -ErrorAction SilentlyContinue; Remove-Job -Job $backendJob -Force -ErrorAction SilentlyContinue }
    if ($frontendJob) { Stop-Job -Job $frontendJob -ErrorAction SilentlyContinue; Remove-Job -Job $frontendJob -Force -ErrorAction SilentlyContinue }
    if (-not $wasRunning) { & $pgCtl -D $postgresData stop -m fast *> $null }
}
