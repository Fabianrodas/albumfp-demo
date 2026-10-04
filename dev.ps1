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
$settings = ConvertFrom-StringData -StringData ($entries -join [Environment]::NewLine)
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

function Assert-LoopbackPortAvailable([int]$Port) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse('127.0.0.1'), $Port)
    try { $listener.Start() }
    catch { throw "Loopback port $Port is already in use; refusing to start the Demo on another process." }
    finally { $listener.Stop() }
}

Assert-LoopbackPortAvailable 4200
Assert-LoopbackPortAvailable 5000

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

$backendProcess = $null
$frontendProcess = $null
$taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
$backendStdout = Join-Path $runtimeRoot 'backend.stdout.log'
$backendStderr = Join-Path $runtimeRoot 'backend.stderr.log'
$frontendStdout = Join-Path $runtimeRoot 'frontend.stdout.log'
$frontendStderr = Join-Path $runtimeRoot 'frontend.stderr.log'
foreach ($log in @($backendStdout, $backendStderr, $frontendStdout, $frontendStderr)) {
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
}
try {
    $backendProcess = Start-Process -FilePath $python -ArgumentList @('app.py') -WorkingDirectory $backendDir `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $backendStdout -RedirectStandardError $backendStderr
    $frontendProcess = Start-Process -FilePath $npm -ArgumentList @('run', 'start', '--', '--port', '4200') `
        -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $frontendStdout -RedirectStandardError $frontendStderr

    Write-Host 'AlbumFP Demo is starting. Services listen only on loopback.' -ForegroundColor Green
    Write-Host 'App:     http://localhost:4200'
    Write-Host 'Loopback: http://127.0.0.1:4200'
    Write-Host 'Backend: http://127.0.0.1:5000'
    Write-Host 'Press Ctrl+C to stop these Demo services and the PostgreSQL cluster started by this script.'
    Write-Host "Logs:    $runtimeRoot"

    while (-not $backendProcess.HasExited -and -not $frontendProcess.HasExited) {
        $backendProcess.Refresh()
        $frontendProcess.Refresh()
        if ($backendProcess.HasExited -or $frontendProcess.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($backendProcess.HasExited) { throw "The Demo backend stopped; review $backendStderr." }
    if ($frontendProcess.HasExited) { throw "The Demo frontend stopped; review $frontendStderr." }
} finally {
    foreach ($process in @($frontendProcess, $backendProcess)) {
        if ($process) {
            $process.Refresh()
            if (-not $process.HasExited) {
                & $taskkill /PID $process.Id /T /F *> $null
                $process.WaitForExit(5000)
            }
        }
    }
    if (-not $wasRunning) { & $pgCtl -D $postgresData stop -m fast *> $null }
}
