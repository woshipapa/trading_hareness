[CmdletBinding()]
param(
    [string]$RuntimeEnv = 'G:\StockPlatform\config\runtime.env',
    [string]$ApiBase = 'http://127.0.0.1:5681',
    [string]$SshAlias = 'lightServer1',
    [string]$SshHost = $env:PEER_SSH_HOST,
    [int]$SshPort = 0,
    [string]$SshUser = $env:PEER_SSH_USER,
    [string]$SshKeyPath = $env:PEER_SSH_KEY_PATH,
    [string]$KnownHostsPath = $env:PEER_KNOWN_HOSTS_PATH,
    [int]$RemoteDatabasePort = 15432,
    [int]$RemoteApiPort = 15681,
    [string]$PeerApiBase = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Read-EnvFile([string]$Path) {
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path, [Text.Encoding]::UTF8)) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
    }
    return $values
}
$runtime = Read-EnvFile $RuntimeEnv
$postgresRoot = Get-ChildItem -LiteralPath 'G:\StockPlatform\runtime' -Directory -Filter 'postgresql-*' |
    Sort-Object Name -Descending | Select-Object -First 1
$psql = Join-Path $postgresRoot.FullName 'bin\psql.exe'
$env:PGPASSWORD = $runtime.PGPASSWORD
try {
    $databaseIdentity = (& $psql -w -At -h $runtime.PGHOST -p $runtime.PGPORT -U $runtime.PGUSER `
        -d $runtime.PGDATABASE -c "SELECT current_database()||':'||version_num FROM quant.alembic_version").Trim()
}
finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }

$health = Invoke-RestMethod -Uri "$ApiBase/health" -TimeoutSec 5
$headers = @{ 'X-Quant-Read-Key' = $runtime.QUANT_SHARED_READ_API_KEY }
$quote = Invoke-RestMethod -Uri "$ApiBase/licensed/longhu/quotes?symbols=600664.SH" `
    -Headers $headers -TimeoutSec 35
if (@($quote.rows).Count -ne 1) { throw 'Licensed read gateway did not return the requested quote' }

$sshTarget = $SshAlias
$targetArguments = @('-o', 'BatchMode=yes')
if (-not [string]::IsNullOrWhiteSpace($SshHost)) {
    if ($SshPort -le 0 -and -not [string]::IsNullOrWhiteSpace($env:PEER_SSH_PORT)) {
        $SshPort = [int]$env:PEER_SSH_PORT
    }
    $sshTarget = if ([string]::IsNullOrWhiteSpace($SshUser)) { $SshHost } else { "$SshUser@$SshHost" }
    if ($SshPort -gt 0) { $targetArguments += @('-p', [string]$SshPort) }
}
if (-not [string]::IsNullOrWhiteSpace($SshKeyPath)) {
    $targetArguments += @('-i', $SshKeyPath, '-o', 'IdentitiesOnly=yes')
}
if (-not [string]::IsNullOrWhiteSpace($KnownHostsPath)) {
    $targetArguments += @('-o', 'StrictHostKeyChecking=yes', '-o', "UserKnownHostsFile=$KnownHostsPath")
}
$remotePorts = & ssh.exe @targetArguments $sshTarget `
    "ss -lnt | grep -E '127.0.0.1:($RemoteDatabasePort|$RemoteApiPort)' | wc -l"
if ([int]$remotePorts -lt 2) { throw 'Both reverse-tunnel loopback ports are not available on lightServer' }

$peerHealth = $null
if ($PeerApiBase) {
    $peerHealth = Invoke-RestMethod -Uri "$($PeerApiBase.TrimEnd('/'))/health" -TimeoutSec 10
}

[pscustomobject]@{
    status = 'verified'
    local_database = $databaseIdentity
    local_api = $health.status
    licensed_quote_rows = @($quote.rows).Count
    reverse_tunnel_ports = [int]$remotePorts
    peer_api = if ($peerHealth) { $peerHealth.status } else { 'not_requested' }
    secrets_printed = $false
}
