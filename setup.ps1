$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AlbumFP-Demo'
$postgresData = Join-Path $runtimeRoot 'postgresql'
$postgresMarker = Join-Path $postgresData '.albumfp-demo-cluster'
$envFile = Join-Path $repoRoot '.env'
$postgresPort = 55432

function New-LocalToken {
    $bytes = New-Object byte[] 36
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Invoke-CheckedProgram([string]$Path, [string[]]$Arguments) {
    & $Path @Arguments
    if ($LASTEXITCODE -ne 0) { throw "A local setup command failed: $([System.IO.Path]::GetFileName($Path))" }
}

function Read-DemoEnvironment([string]$Path) {
    $entries = Get-Content -LiteralPath $Path | Where-Object { $_ -match '^\s*[^#\s][^=]*=' }
    return ConvertFrom-StringData -StringData ($entries -join [Environment]::NewLine)
}

function Assert-DemoUrl([string]$Value, [string]$ExpectedDatabase) {
    try { $uri = [System.Uri]$Value } catch { throw 'The local PostgreSQL URL is malformed.' }
    $allowedHosts = @('localhost', '127.0.0.1', '::1')
    $userInfo = [System.Uri]::UnescapeDataString($uri.UserInfo)
    $separator = $userInfo.IndexOf(':')
    $username = if ($separator -ge 0) { $userInfo.Substring(0, $separator) } else { '' }
    $password = if ($separator -ge 0) { $userInfo.Substring($separator + 1) } else { '' }
    if ($uri.Scheme -ne 'postgresql+psycopg2' -or $uri.Host -notin $allowedHosts -or
        $uri.Port -ne $postgresPort -or $uri.AbsolutePath -ne "/$ExpectedDatabase" -or
        $uri.Query -or $uri.Fragment -or $username -ne 'albumfp_demo' -or
        $password -ne $settings.POSTGRES_PASSWORD) {
        throw "The $ExpectedDatabase URL must target the local Demo PostgreSQL cluster on port $postgresPort."
    }
}

function Initialize-DemoDatabase([string]$DatabaseName, [string]$Purpose) {
    $env:DATABASE_URL = $settings.DATABASE_URL
    $env:TEST_DATABASE_URL = $settings.TEST_DATABASE_URL
    if ($Purpose -eq 'test') {
        $env:APP_ENV = 'test'
        $env:ALBUMFP_DEMO_TEST_MODE = '1'
    } else {
        $env:APP_ENV = 'development'
        $env:ALBUMFP_DEMO_TEST_MODE = '0'
    }

    $versionOutput = & $psql -X -A -t -d $DatabaseName -v ON_ERROR_STOP=1 -c "SELECT COALESCE(to_regclass('public.alembic_version')::text, '')"
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the $DatabaseName migration marker." }
    $version = ($versionOutput -join '').Trim()
    $objectOutput = & $psql -X -A -t -d $DatabaseName -v ON_ERROR_STOP=1 -c "SELECT ((SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f')) + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public') + (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' AND t.typtype = 'e'))"
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the $DatabaseName schema objects." }
    $schemaObjectCount = [int](($objectOutput -join '').Trim())
    $applicationTableOutput = & $psql -X -A -t -d $DatabaseName -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relname <> 'alembic_version'"
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the $DatabaseName application tables." }
    $applicationTableCount = [int](($applicationTableOutput -join '').Trim())

    Push-Location $backendDir
    try {
        if (-not $version) {
            if ($schemaObjectCount -ne 0) {
                throw "Refusing to initialize a non-empty unversioned $DatabaseName database."
            }
            Invoke-CheckedProgram $python @('-c', 'from schemas.schema import run_schema; run_schema(reset=False)')
            Invoke-CheckedProgram $python @('-m', 'alembic', 'stamp', 'head')
        } elseif ($applicationTableCount -eq 0) {
            throw "Refusing to migrate a versioned $DatabaseName database with no application tables."
        }
        Invoke-CheckedProgram $python @('-m', 'alembic', 'upgrade', 'head')
    } finally {
        Pop-Location
    }
}

$postgresBin = $null
foreach ($version in @(18, 17, 16, 15, 14)) {
    $candidate = Join-Path $env:ProgramFiles "PostgreSQL\$version\bin"
    if ((Test-Path (Join-Path $candidate 'initdb.exe')) -and
        (Test-Path (Join-Path $candidate 'pg_ctl.exe')) -and
        (Test-Path (Join-Path $candidate 'psql.exe')) -and
        (Test-Path (Join-Path $candidate 'createdb.exe'))) {
        $postgresBin = $candidate
        break
    }
}
if (-not $postgresBin) {
    throw 'Install PostgreSQL 14 or newer, then run setup.ps1 again. The Demo uses its own cluster on port 55432.'
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination $envFile
}
$envText = Get-Content -LiteralPath $envFile -Raw
if ($envText.Contains('GENERATED_BY_SETUP')) {
    $databasePassword = New-LocalToken
    $adminPassword = New-LocalToken
    $sessionSecret = New-LocalToken
    $mediaRoot = Join-Path $runtimeRoot 'media'
    $tmpRoot = Join-Path $runtimeRoot 'tmp'
    $quarantineRoot = Join-Path $runtimeRoot 'quarantine'
    $envText = $envText.Replace('FLASK_SECRET_KEY=GENERATED_BY_SETUP', "FLASK_SECRET_KEY=$sessionSecret")
    $envText = $envText.Replace('albumfp_demo:GENERATED_BY_SETUP@', "albumfp_demo:$databasePassword@")
    $envText = $envText.Replace('POSTGRES_PASSWORD=GENERATED_BY_SETUP', "POSTGRES_PASSWORD=$databasePassword")
    $envText = $envText.Replace('POSTGRES_ADMIN_PASSWORD=GENERATED_BY_SETUP', "POSTGRES_ADMIN_PASSWORD=$adminPassword")
    $envText = $envText.Replace('MEDIA_STORAGE_ROOT=../../albumfp-demo-runtime/media', "MEDIA_STORAGE_ROOT=$($mediaRoot.Replace('\', '/'))")
    $envText = $envText.Replace('MEDIA_TMP_ROOT=../../albumfp-demo-runtime/tmp', "MEDIA_TMP_ROOT=$($tmpRoot.Replace('\', '/'))")
    $envText = $envText.Replace('MEDIA_QUARANTINE_ROOT=../../albumfp-demo-runtime/quarantine', "MEDIA_QUARANTINE_ROOT=$($quarantineRoot.Replace('\', '/'))")
    [System.IO.File]::WriteAllText($envFile, $envText, [System.Text.UTF8Encoding]::new($false))
}

$settings = Read-DemoEnvironment $envFile
if ($settings.POSTGRES_HOST -ne '127.0.0.1' -or [int]$settings.POSTGRES_PORT -ne $postgresPort -or
    $settings.POSTGRES_USER -ne 'albumfp_demo' -or $settings.POSTGRES_ADMIN_USER -ne 'albumfp_demo_admin' -or
    $settings.POSTGRES_PASSWORD -like '*GENERATED_BY_SETUP*' -or
    $settings.POSTGRES_ADMIN_PASSWORD -like '*GENERATED_BY_SETUP*' -or
    $settings.FLASK_SECRET_KEY -like '*GENERATED_BY_SETUP*' -or
    $settings.FLASK_SECRET_KEY.Length -lt 32) {
    throw 'The .env PostgreSQL settings must use generated Demo credentials, host 127.0.0.1, and port 55432.'
}
Assert-DemoUrl $settings.DATABASE_URL 'albumfp_demo'
Assert-DemoUrl $settings.TEST_DATABASE_URL 'albumfp_demo_test'

$python = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv (Join-Path $backendDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}
& $python -m pip install -r (Join-Path $backendDir 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies could not be installed.' }

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npm) { throw 'Install Node.js 22 or newer before running setup.ps1.' }
if (Test-Path -LiteralPath (Join-Path $frontendDir 'package-lock.json')) {
    Push-Location $frontendDir
    try {
        & $npm ci --cache (Join-Path $repoRoot '.npm-cache')
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies could not be installed.' }
    } finally { Pop-Location }
}

$mediaDirectories = @($settings.MEDIA_STORAGE_ROOT, $settings.MEDIA_TMP_ROOT, $settings.MEDIA_QUARANTINE_ROOT)
foreach ($configuredPath in $mediaDirectories) {
    $resolvedPath = if ([System.IO.Path]::IsPathRooted($configuredPath)) {
        [System.IO.Path]::GetFullPath($configuredPath)
    } else {
        [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($backendDir, $configuredPath))
    }
    $repoPrefix = $repoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($resolvedPath.Equals($repoRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $resolvedPath.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Media and temporary runtime paths must stay outside the repository.'
    }
    New-Item -ItemType Directory -Force -Path $resolvedPath | Out-Null
}

if (Test-Path -LiteralPath (Join-Path $postgresData 'PG_VERSION')) {
    if (-not (Test-Path -LiteralPath $postgresMarker) -or
        (Get-Content -LiteralPath $postgresMarker -Raw).Trim() -ne 'AlbumFP Demo local cluster') {
        throw 'Refusing to use a PostgreSQL data directory that was not created by AlbumFP Demo.'
    }
} else {
    $leftovers = if (Test-Path -LiteralPath $postgresData) { @(Get-ChildItem -LiteralPath $postgresData -Force) } else { @() }
    if ($leftovers.Count -gt 0) { throw 'The Demo PostgreSQL data directory is non-empty but is not initialized.' }
    New-Item -ItemType Directory -Force -Path $postgresData | Out-Null
    $passwordFile = Join-Path $runtimeRoot ("initdb-" + [guid]::NewGuid().ToString('N') + '.txt')
    try {
        [System.IO.File]::WriteAllText($passwordFile, $settings.POSTGRES_ADMIN_PASSWORD, [System.Text.UTF8Encoding]::new($false))
        Invoke-CheckedProgram (Join-Path $postgresBin 'initdb.exe') @(
            '-D', $postgresData, '-U', $settings.POSTGRES_ADMIN_USER,
            '--auth-local=scram-sha-256', '--auth-host=scram-sha-256',
            "--pwfile=$passwordFile", '--encoding=UTF8', '--no-locale'
        )
        [System.IO.File]::WriteAllText($postgresMarker, 'AlbumFP Demo local cluster', [System.Text.UTF8Encoding]::new($false))
    } finally {
        if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile -Force }
    }
}

$pgCtl = Join-Path $postgresBin 'pg_ctl.exe'
$psql = Join-Path $postgresBin 'psql.exe'
$createdb = Join-Path $postgresBin 'createdb.exe'
$logFile = Join-Path $runtimeRoot 'postgresql.log'
$wasRunning = $false
& $pgCtl -D $postgresData status *> $null
if ($LASTEXITCODE -eq 0) {
    $wasRunning = $true
} else {
    $listener = Get-NetTCPConnection -LocalPort $postgresPort -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $postgresPort is occupied by another process; setup will not connect to it." }
    Invoke-CheckedProgram $pgCtl @('-D', $postgresData, '-l', $logFile, '-o', "-h 127.0.0.1 -p $postgresPort", 'start')
}

$oldPgPassword = $env:PGPASSWORD
$oldPgHost = $env:PGHOST
$oldPgPort = $env:PGPORT
$oldPgUser = $env:PGUSER
try {
    $env:PGPASSWORD = $settings.POSTGRES_ADMIN_PASSWORD
    $env:PGHOST = '127.0.0.1'
    $env:PGPORT = [string]$postgresPort
    $env:PGUSER = $settings.POSTGRES_ADMIN_USER
    $roleSqlPath = Join-Path $runtimeRoot ("role-" + [guid]::NewGuid().ToString('N') + '.sql')
    $escapedPassword = $settings.POSTGRES_PASSWORD.Replace("'", "''")
    $roleExists = & $psql -X -A -t -d postgres -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_roles WHERE rolname = 'albumfp_demo'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not verify the Demo role in its own PostgreSQL cluster.' }
    if (($roleExists -join '').Trim() -eq '1') {
        $roleSql = "ALTER ROLE albumfp_demo WITH LOGIN PASSWORD '$escapedPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE;"
    } else {
        $roleSql = "CREATE ROLE albumfp_demo LOGIN PASSWORD '$escapedPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE;"
    }
    try {
        [System.IO.File]::WriteAllText($roleSqlPath, $roleSql, [System.Text.UTF8Encoding]::new($false))
        Invoke-CheckedProgram $psql @('-X', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-f', $roleSqlPath)
    } finally {
        if (Test-Path -LiteralPath $roleSqlPath) { Remove-Item -LiteralPath $roleSqlPath -Force }
    }

    foreach ($database in @('albumfp_demo', 'albumfp_demo_test')) {
        $exists = & $psql -X -A -t -d postgres -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname = '$database'"
        if ($LASTEXITCODE -ne 0) { throw "Could not verify the $database database." }
        if (($exists -join '').Trim() -ne '1') {
            Invoke-CheckedProgram $createdb @('-h', '127.0.0.1', '-p', [string]$postgresPort, '-U', 'albumfp_demo_admin', '-O', 'albumfp_demo', '-E', 'UTF8', '-T', 'template0', $database)
        }
    }

    $python = Join-Path $backendDir '.venv\Scripts\python.exe'
    $alembicIni = Join-Path $backendDir 'alembic.ini'
    if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $alembicIni)) {
        # Both dedicated databases are bootstrapped only while truly empty.
        # Existing versioned databases are advanced through Alembic normally.
        Initialize-DemoDatabase 'albumfp_demo' 'development'
        Initialize-DemoDatabase 'albumfp_demo_test' 'test'
    }
} finally {
    $env:PGPASSWORD = $oldPgPassword
    $env:PGHOST = $oldPgHost
    $env:PGPORT = $oldPgPort
    $env:PGUSER = $oldPgUser
    if (-not $wasRunning) {
        & $pgCtl -D $postgresData stop -m fast *> $null
    }
}

Write-Host 'AlbumFP Demo setup is complete.' -ForegroundColor Green
Write-Host 'Local database: albumfp_demo (PostgreSQL at 127.0.0.1:55432)'
Write-Host 'Next: .\dev.ps1'
