function Test-LoopbackPostgresAddress([string]$Address) {
    if (-not $Address) { return $false }
    $candidate = $Address.Trim().TrimStart('[').TrimEnd(']')
    if ($candidate -ieq 'localhost') { return $true }
    try { $parsed = [System.Net.IPAddress]::Parse($candidate) } catch { return $false }
    if ($parsed.IsIPv4MappedToIPv6) { $parsed = $parsed.MapToIPv4() }
    return [System.Net.IPAddress]::IsLoopback($parsed)
}

function Assert-PostgresLoopbackConfiguration {
    param(
        [Parameter(Mandatory)][int]$PostgresPort,
        [Parameter(Mandatory)][AllowEmptyString()][string]$ListenAddresses,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Listeners
    )

    if ($PostgresPort -ne 55432) {
        throw 'The Demo PostgreSQL cluster must use port 55432.'
    }

    $configuredAddresses = @($ListenAddresses -split ',' | ForEach-Object { $_.Trim() })
    if ($configuredAddresses.Count -eq 0 -or
        @($configuredAddresses | Where-Object { -not (Test-LoopbackPostgresAddress $_) }).Count -gt 0) {
        throw 'The Demo PostgreSQL listen_addresses setting must contain loopback addresses only.'
    }

    $activeListeners = @($Listeners | Where-Object { $null -ne $_ })
    if ($activeListeners.Count -eq 0) {
        throw 'The Demo PostgreSQL cluster has no local TCP listener on port 55432.'
    }
    foreach ($listener in $activeListeners) {
        if ([int]$listener.LocalPort -ne 55432 -or
            -not (Test-LoopbackPostgresAddress ([string]$listener.LocalAddress))) {
            throw 'The Demo PostgreSQL cluster may listen on loopback only.'
        }
    }
}
