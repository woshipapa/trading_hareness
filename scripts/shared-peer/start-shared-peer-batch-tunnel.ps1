param(
    [string]$SshAlias = "lightServer1",
    [string]$SshHost = $env:PEER_SSH_HOST,
    [int]$SshPort = 0,
    [string]$SshUser = $env:PEER_SSH_USER,
    [string]$SshKeyPath = $env:PEER_SSH_KEY_PATH,
    [string]$KnownHostsPath = $env:PEER_KNOWN_HOSTS_PATH,
    [int]$RemoteDatabasePort = 15433,
    [int]$LocalDatabasePort = 55432
)

$ErrorActionPreference = "Stop"
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$sshTarget = $SshAlias
$targetArguments = @()
if (-not [string]::IsNullOrWhiteSpace($SshHost)) {
    if ($SshPort -le 0 -and -not [string]::IsNullOrWhiteSpace($env:PEER_SSH_PORT)) {
        $SshPort = [int]$env:PEER_SSH_PORT
    }
    $sshTarget = if ([string]::IsNullOrWhiteSpace($SshUser)) { $SshHost } else { "$SshUser@$SshHost" }
    if ($SshPort -gt 0) { $targetArguments += @("-p", [string]$SshPort) }
}
if (-not [string]::IsNullOrWhiteSpace($SshKeyPath)) {
    $targetArguments += @("-i", $SshKeyPath, "-o", "IdentitiesOnly=yes")
}
if (-not [string]::IsNullOrWhiteSpace($KnownHostsPath)) {
    $targetArguments += @("-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=$KnownHostsPath")
}
$arguments = @(
    "-NT",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "Compression=yes",
    "-R", "127.0.0.1:$RemoteDatabasePort`:127.0.0.1:$LocalDatabasePort"
) + $targetArguments + @($sshTarget)

# This process is intentionally independent of the API/5432 tunnel. A bulk
# COPY or reconnect cannot reset the intraday session lane.
& $ssh @arguments
exit $LASTEXITCODE
