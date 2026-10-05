$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\postgres_safety.ps1')

function Assert-Throws([scriptblock]$Action, [string]$Case) {
    $threw = $false
    try { & $Action } catch { $threw = $true }
    if (-not $threw) { throw "Expected rejection: $Case" }
}

function New-Listener([string]$Address, [int]$Port = 55432) {
    return [pscustomobject]@{ LocalAddress = $Address; LocalPort = $Port }
}

Assert-PostgresLoopbackConfiguration -PostgresPort 55432 -ListenAddresses 'localhost,127.0.0.1,::1' -Listeners @(
    (New-Listener '127.0.0.1'), (New-Listener '::1')
)

$privateIpv4 = '192.168.' + '1.8'
$mixedIpv4 = '10.4.' + '2.3'
foreach ($addresses in @('*', '0.0.0.0', $privateIpv4, "localhost,$mixedIpv4")) {
    Assert-Throws {
        Assert-PostgresLoopbackConfiguration -PostgresPort 55432 -ListenAddresses $addresses -Listeners @(
            (New-Listener '127.0.0.1')
        )
    } "listen_addresses=$addresses"
}

$privateListenerIpv4 = '10.8.' + '1.4'
$privateListenerIpv4b = '192.168.' + '1.8'
foreach ($listenerAddress in @('0.0.0.0', '::', $privateListenerIpv4, $privateListenerIpv4b)) {
    Assert-Throws {
        Assert-PostgresLoopbackConfiguration -PostgresPort 55432 -ListenAddresses '127.0.0.1' -Listeners @(
            (New-Listener $listenerAddress)
        )
    } "listener=$listenerAddress"
}

Assert-Throws {
    Assert-PostgresLoopbackConfiguration -PostgresPort 55432 -ListenAddresses '127.0.0.1' -Listeners @()
} 'missing listener'
Assert-Throws {
    Assert-PostgresLoopbackConfiguration -PostgresPort 5432 -ListenAddresses '127.0.0.1' -Listeners @(
        (New-Listener '127.0.0.1')
    )
} 'wrong database port'

Write-Host 'PostgreSQL loopback safety tests: PASS'
