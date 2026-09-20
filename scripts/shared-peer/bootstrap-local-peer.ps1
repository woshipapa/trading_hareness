# LEGACY LEAST-PRIVILEGE PROFILE: the current owner peer contract preserves
# existing stock_peer write ACLs. Use -DryRun for audit only; do not run the
# mutating path against shared production unless the owner explicitly asks.
param(
    [string]$RuntimeEnv = "G:\StockPlatform\config\runtime.env",
    [string]$PeerRoot = "G:\StockPlatform\peer",
    [string]$PeerRole = "stock_peer",
    [string]$PeerN8nDatabase = "trading_hareness_peer_n8n",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

foreach ($identifier in @(
    @{ Name = "PeerRole"; Value = $PeerRole },
    @{ Name = "PeerN8nDatabase"; Value = $PeerN8nDatabase }
)) {
    if ($identifier.Value -notmatch '^[a-z_][a-z0-9_]*$') {
        throw "$($identifier.Name) must be a lowercase PostgreSQL identifier"
    }
}

function Read-EnvFile([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2]
        }
    }
    return $values
}

function New-Secret([int]$Bytes = 32) {
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+','-').Replace('/','_')
}

function Set-EnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = @(Get-Content -LiteralPath $Path)
    $replacement = "$Name=$Value"
    $found = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^$([regex]::Escape($Name))=") {
            $lines[$index] = $replacement
            $found = $true
        }
    }
    if (-not $found) { $lines += $replacement }
    Set-Content -LiteralPath $Path -Value $lines -Encoding utf8
}

$runtime = Read-EnvFile $RuntimeEnv
foreach ($name in @("PGADMINUSER", "PGDATABASE", "PGUSER")) {
    if (-not $runtime.$name -or $runtime.$name -notmatch '^[a-z_][a-z0-9_]*$') {
        throw "$name must be a lowercase PostgreSQL identifier"
    }
}
if ($runtime.PGADMINUSER -eq $PeerRole) {
    throw "PGADMINUSER must differ from PeerRole"
}
$postgresRoot = Get-ChildItem -LiteralPath "G:\StockPlatform\runtime" -Directory -Filter "postgresql-*" |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $postgresRoot) { throw "PostgreSQL runtime not found" }
$psql = Join-Path $postgresRoot.FullName "bin\psql.exe"
$createdb = Join-Path $postgresRoot.FullName "bin\createdb.exe"

if ($DryRun) {
    $env:PGPASSWORD = $runtime.PGADMINPASSWORD
    try {
        $auditSql = @"
SELECT
  EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$PeerRole'),
  COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname='$PeerRole'), true),
  COALESCE((SELECT rolcreatedb FROM pg_roles WHERE rolname='$PeerRole'), true),
  COALESCE((SELECT rolcreaterole FROM pg_roles WHERE rolname='$PeerRole'), true),
  COALESCE((SELECT rolreplication FROM pg_roles WHERE rolname='$PeerRole'), true),
  COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname='$PeerRole'), true),
  COALESCE((SELECT rolinherit FROM pg_roles WHERE rolname='$PeerRole'), false),
  COALESCE((SELECT count(*)::bigint FROM pg_auth_members m
              JOIN pg_roles child ON child.oid=m.member
             WHERE child.rolname='$PeerRole'), 0),
  COALESCE((SELECT count(*)::bigint FROM pg_database
             WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname='$PeerRole')
               AND datname NOT IN ('$($runtime.PGDATABASE)', '$PeerN8nDatabase')), 0),
  COALESCE((SELECT count(*)::bigint FROM pg_tablespace
             WHERE spcowner=(SELECT oid FROM pg_roles WHERE rolname='$PeerRole')), 0),
  COALESCE((SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='$($runtime.PGDATABASE)'), 'missing'),
  COALESCE((SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='quant'), 'missing'),
  COALESCE((SELECT count(*)::bigint FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='quant'
               AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname='$PeerRole')), 0),
  COALESCE((SELECT count(*)::bigint FROM pg_proc p
              JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='quant' AND p.prosecdef
               AND has_function_privilege('$PeerRole',p.oid,'EXECUTE')), 0),
  COALESCE((SELECT count(*)::bigint FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='quant'
               AND c.relkind IN ('r','p','v','m','f')
               AND (has_table_privilege('$PeerRole',c.oid,'INSERT')
                 OR has_table_privilege('$PeerRole',c.oid,'UPDATE')
                 OR has_table_privilege('$PeerRole',c.oid,'DELETE')
                 OR has_table_privilege('$PeerRole',c.oid,'TRUNCATE')
                 OR has_table_privilege('$PeerRole',c.oid,'REFERENCES')
                 OR has_table_privilege('$PeerRole',c.oid,'TRIGGER'))), 0),
  COALESCE((SELECT count(*)::bigint FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='quant' AND c.relkind='S'
               AND (has_sequence_privilege('$PeerRole',c.oid,'USAGE')
                 OR has_sequence_privilege('$PeerRole',c.oid,'UPDATE'))), 0),
  CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$PeerRole')
       THEN has_schema_privilege('$PeerRole','quant','CREATE') ELSE false END,
  CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$PeerRole')
         AND to_regclass('quant.canonical_bars_daily') IS NOT NULL
       THEN has_table_privilege('$PeerRole','quant.canonical_bars_daily','INSERT') ELSE false END,
  CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$PeerRole')
         AND to_regclass('quant.canonical_bars_daily') IS NOT NULL
       THEN has_table_privilege('$PeerRole','quant.canonical_bars_daily','UPDATE') ELSE false END,
  CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$PeerRole')
         AND to_regclass('quant.canonical_bars_daily') IS NOT NULL
       THEN has_table_privilege('$PeerRole','quant.canonical_bars_daily','DELETE') ELSE false END
"@
        $audit = & $psql -At -F '|' -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
            -U $runtime.PGADMINUSER -d $runtime.PGDATABASE -c $auditSql
        if ($LASTEXITCODE -ne 0) { throw "could not read peer role audit" }
        $fields = (($audit | Select-Object -Last 1) -split '\|') | ForEach-Object { $_.Trim() }
        if ($fields.Count -lt 20) { throw "peer role audit returned an incomplete row" }
        [pscustomobject]@{
            DryRun = $true
            PeerRole = $PeerRole
            QuantDatabase = $runtime.PGDATABASE
            RoleExists = $fields[0] -eq "t"
            RoleSuperuser = $fields[1] -eq "t"
            RoleCreateDb = $fields[2] -eq "t"
            RoleCreateRole = $fields[3] -eq "t"
            RoleReplication = $fields[4] -eq "t"
            RoleBypassRls = $fields[5] -eq "t"
            RoleInherit = $fields[6] -eq "t"
            RoleMembershipCount = [int64]$fields[7]
            UnexpectedOwnedDatabaseCount = [int64]$fields[8]
            OwnedTablespaceCount = [int64]$fields[9]
            DatabaseOwner = $fields[10]
            QuantSchemaOwner = $fields[11]
            QuantOwnedObjectCount = [int64]$fields[12]
            SecurityDefinerExecutableCount = [int64]$fields[13]
            WritableQuantRelationCount = [int64]$fields[14]
            WritableQuantSequenceCount = [int64]$fields[15]
            SchemaCreatable = $fields[16] -eq "t"
            CanonicalInsertable = $fields[17] -eq "t"
            CanonicalUpdatable = $fields[18] -eq "t"
            CanonicalDeletable = $fields[19] -eq "t"
            ReadOnlyReady = (
                ($fields[0] -eq "t") -and
                ($fields[1] -eq "f") -and
                ($fields[2] -eq "f") -and
                ($fields[3] -eq "f") -and
                ($fields[4] -eq "f") -and
                ($fields[5] -eq "f") -and
                ($fields[6] -eq "f") -and
                ([int64]$fields[7] -eq 0) -and
                ([int64]$fields[8] -eq 0) -and
                ([int64]$fields[9] -eq 0) -and
                ($fields[10] -ne $PeerRole) -and
                ($fields[11] -ne $PeerRole) -and
                ([int64]$fields[12] -eq 0) -and
                ([int64]$fields[13] -eq 0) -and
                ([int64]$fields[14] -eq 0) -and
                ([int64]$fields[15] -eq 0) -and
                ($fields[16] -eq "f") -and
                ($fields[17] -eq "f") -and
                ($fields[18] -eq "f") -and
                ($fields[19] -eq "f")
            )
        } | ConvertTo-Json -Compress
    }
    finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    return
}

$secrets = Join-Path $PeerRoot "secrets"
New-Item -ItemType Directory -Force -Path $secrets | Out-Null
$peerEnv = Join-Path $secrets "peer.env"
$existing = if (Test-Path $peerEnv) { Read-EnvFile $peerEnv } else { @{} }
$peerPassword = if ($existing.PEER_DB_PASSWORD) { $existing.PEER_DB_PASSWORD } else { New-Secret }
$readKey = if ($existing.QUANT_SHARED_READ_API_KEY) { $existing.QUANT_SHARED_READ_API_KEY } else { New-Secret }
$writeKey = if ($existing.PEER_QUANT_WRITE_API_KEY) { $existing.PEER_QUANT_WRITE_API_KEY } else { New-Secret }
$n8nKey = if ($existing.PEER_N8N_ENCRYPTION_KEY) { $existing.PEER_N8N_ENCRYPTION_KEY } else { New-Secret 48 }

$escapedPassword = $peerPassword.Replace("'", "''")
$env:PGPASSWORD = $runtime.PGADMINPASSWORD
try {
    $unexpectedSharedOwnership = & $psql -At -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
        -U $runtime.PGADMINUSER -d postgres -c @"
SELECT COALESCE(string_agg(object_kind || ':' || object_name, ',' ORDER BY object_kind, object_name), '')
  FROM (
        SELECT 'database' AS object_kind, datname AS object_name
          FROM pg_database
         WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname='$PeerRole')
           AND datname NOT IN ('$($runtime.PGDATABASE)', '$PeerN8nDatabase')
        UNION ALL
        SELECT 'tablespace' AS object_kind, spcname AS object_name
          FROM pg_tablespace
         WHERE spcowner=(SELECT oid FROM pg_roles WHERE rolname='$PeerRole')
       ) unexpected
"@
    if ($LASTEXITCODE -ne 0) { throw "could not audit shared objects owned by $PeerRole" }
    $unexpectedSharedOwnershipText = [string]($unexpectedSharedOwnership | Select-Object -Last 1)
    if ($unexpectedSharedOwnershipText.Trim()) {
        throw "peer role $PeerRole owns unexpected shared objects: $unexpectedSharedOwnershipText"
    }
    $databaseOwner = & $psql -At -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
        -U $runtime.PGADMINUSER -d postgres -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='$($runtime.PGDATABASE)'"
    if (($databaseOwner | Select-Object -Last 1).Trim() -eq $PeerRole) {
        & $psql -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
            -U $runtime.PGADMINUSER -d postgres -c "ALTER DATABASE $($runtime.PGDATABASE) OWNER TO $($runtime.PGADMINUSER)" | Out-Null
    }
    $schemaOwner = & $psql -At -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
        -U $runtime.PGADMINUSER -d $runtime.PGDATABASE -c "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='quant'"
    if (($schemaOwner | Select-Object -Last 1).Trim() -eq $PeerRole) {
        & $psql -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
            -U $runtime.PGADMINUSER -d $runtime.PGDATABASE -c "ALTER SCHEMA quant OWNER TO $($runtime.PGADMINUSER)" | Out-Null
    }
    $roleSql = @"
DO `$peer`$
DECLARE membership record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$PeerRole') THEN
    CREATE ROLE $PeerRole LOGIN NOINHERIT PASSWORD '$escapedPassword';
  ELSE
    ALTER ROLE $PeerRole LOGIN NOINHERIT PASSWORD '$escapedPassword';
  END IF;
  FOR membership IN
      SELECT parent.rolname AS granted_role
        FROM pg_auth_members m
        JOIN pg_roles child ON child.oid=m.member
        JOIN pg_roles parent ON parent.oid=m.roleid
       WHERE child.rolname='$PeerRole'
  LOOP
    EXECUTE format('REVOKE %I FROM %I', membership.granted_role, '$PeerRole');
  END LOOP;
END
`$peer`$;
-- The peer is a read-only research consumer of the owner's canonical store.
-- It receives the licensed Longhu rows through the authenticated gateway and
-- must not inherit the application's write role or run production migrations.
ALTER ROLE $PeerRole NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE $PeerRole SET statement_timeout = '15min';
ALTER ROLE $PeerRole SET idle_in_transaction_session_timeout = '5min';
-- REVOKE cannot remove the implicit privileges of an object owner. Older
-- bootstrap releases created peer-owned evidence tables in the owner database;
-- transfer those objects before the grants below so the role is genuinely
-- read-only rather than merely lacking explicit ACL entries.
REASSIGN OWNED BY $PeerRole TO $($runtime.PGADMINUSER);
GRANT CONNECT ON DATABASE $($runtime.PGDATABASE) TO $PeerRole;
REVOKE CREATE ON DATABASE $($runtime.PGDATABASE) FROM $PeerRole;
REVOKE ALL PRIVILEGES ON SCHEMA quant FROM $PeerRole;
GRANT USAGE ON SCHEMA quant TO $PeerRole;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA quant FROM $PeerRole;
GRANT SELECT ON ALL TABLES IN SCHEMA quant TO $PeerRole;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA quant FROM $PeerRole;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA quant FROM $PeerRole;
REVOKE ALL PRIVILEGES ON ALL PROCEDURES IN SCHEMA quant FROM $PeerRole;
ALTER DEFAULT PRIVILEGES FOR ROLE $($runtime.PGUSER) IN SCHEMA quant
  REVOKE ALL ON TABLES FROM $PeerRole;
ALTER DEFAULT PRIVILEGES FOR ROLE $($runtime.PGUSER) IN SCHEMA quant
  GRANT SELECT ON TABLES TO $PeerRole;
ALTER DEFAULT PRIVILEGES FOR ROLE $($runtime.PGUSER) IN SCHEMA quant
  REVOKE ALL ON FUNCTIONS FROM $PeerRole;
"@
    & $psql -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
        -U $runtime.PGADMINUSER -d $runtime.PGDATABASE -c $roleSql | Out-Null
    $exists = & $psql -At -h $runtime.PGHOST -p $runtime.PGPORT -U $runtime.PGADMINUSER `
        -d postgres -c "SELECT 1 FROM pg_database WHERE datname='$PeerN8nDatabase'"
    if ($exists -ne "1") {
        & $createdb -h $runtime.PGHOST -p $runtime.PGPORT -U $runtime.PGADMINUSER `
            -O $PeerRole $PeerN8nDatabase
    } else {
        # REASSIGN OWNED also changes shared database ownership. Restore the
        # deliberately separate peer n8n store after repairing the owner DB.
        & $psql -v ON_ERROR_STOP=1 -h $runtime.PGHOST -p $runtime.PGPORT `
            -U $runtime.PGADMINUSER -d postgres `
            -c "ALTER DATABASE $PeerN8nDatabase OWNER TO $PeerRole" | Out-Null
    }
    $privilegeReceipt = & $psql -At -h $runtime.PGHOST -p $runtime.PGPORT -U $runtime.PGADMINUSER `
        -d $runtime.PGDATABASE -c @"
SELECT has_schema_privilege('$PeerRole','quant','CREATE'),
       COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname='$PeerRole'), true),
       COALESCE((SELECT rolcreatedb FROM pg_roles WHERE rolname='$PeerRole'), true),
       COALESCE((SELECT rolcreaterole FROM pg_roles WHERE rolname='$PeerRole'), true),
       COALESCE((SELECT rolreplication FROM pg_roles WHERE rolname='$PeerRole'), true),
       COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname='$PeerRole'), true),
       COALESCE((SELECT rolinherit FROM pg_roles WHERE rolname='$PeerRole'), true),
       COALESCE((SELECT count(*)::bigint FROM pg_auth_members m
                   JOIN pg_roles child ON child.oid=m.member
                  WHERE child.rolname='$PeerRole'), 0),
       COALESCE((SELECT datdba = (SELECT oid FROM pg_roles WHERE rolname='$PeerRole')
                   FROM pg_database WHERE datname=current_database()), true),
       COALESCE((SELECT nspowner = (SELECT oid FROM pg_roles WHERE rolname='$PeerRole')
                   FROM pg_namespace WHERE nspname='quant'), true),
  COALESCE(EXISTS (SELECT 1 FROM pg_class c
                          JOIN pg_namespace n ON n.oid=c.relnamespace
                         WHERE n.nspname='quant'
                           AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname='$PeerRole')), true),
       COALESCE((SELECT count(*)::bigint FROM pg_proc p
                   JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname='quant' AND p.prosecdef
                    AND has_function_privilege('$PeerRole',p.oid,'EXECUTE')), 0),
       COALESCE((SELECT count(*)::bigint FROM pg_class c
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname='quant'
                    AND c.relkind IN ('r','p','v','m','f')
                    AND (has_table_privilege('$PeerRole',c.oid,'INSERT')
                      OR has_table_privilege('$PeerRole',c.oid,'UPDATE')
                      OR has_table_privilege('$PeerRole',c.oid,'DELETE')
                      OR has_table_privilege('$PeerRole',c.oid,'TRUNCATE')
                      OR has_table_privilege('$PeerRole',c.oid,'REFERENCES')
                      OR has_table_privilege('$PeerRole',c.oid,'TRIGGER'))), 0),
       COALESCE((SELECT count(*)::bigint FROM pg_class c
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname='quant' AND c.relkind='S'
                    AND (has_sequence_privilege('$PeerRole',c.oid,'USAGE')
                      OR has_sequence_privilege('$PeerRole',c.oid,'UPDATE'))), 0),
       CASE WHEN to_regclass('quant.canonical_bars_daily') IS NULL THEN false
            ELSE has_table_privilege('$PeerRole','quant.canonical_bars_daily','INSERT') END,
       CASE WHEN to_regclass('quant.canonical_bars_daily') IS NULL THEN false
            ELSE has_table_privilege('$PeerRole','quant.canonical_bars_daily','UPDATE') END,
       CASE WHEN to_regclass('quant.canonical_bars_daily') IS NULL THEN false
            ELSE has_table_privilege('$PeerRole','quant.canonical_bars_daily','DELETE') END
"@
    if ($LASTEXITCODE -ne 0) { throw "could not verify read-only privileges for $PeerRole" }
    $privilegeFlags = (($privilegeReceipt | Select-Object -Last 1) -split '\|') | ForEach-Object { $_.Trim().ToLowerInvariant() }
    $invalidReceipt = $privilegeFlags.Count -lt 17
    if (-not $invalidReceipt) {
        $unsafeBooleanIndexes = @(0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 14, 15, 16)
        $invalidReceipt = (
            [int64]$privilegeFlags[7] -gt 0 -or
            [int64]$privilegeFlags[11] -gt 0 -or
            [int64]$privilegeFlags[12] -gt 0 -or
            [int64]$privilegeFlags[13] -gt 0 -or
            ($unsafeBooleanIndexes | Where-Object { $privilegeFlags[$_] -eq 't' }).Count -gt 0
        )
    }
    if ($invalidReceipt) {
        throw "peer role $PeerRole still inherits or has role memberships, owns an owner object, has executable SECURITY DEFINER routines, or has quant schema/relation/sequence write privileges; inspect object ownership and role memberships"
    }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

Set-EnvValue $RuntimeEnv "QUANT_SHARED_READ_API_KEY" $readKey
@(
    "PEER_DB_USER=$PeerRole",
    "PEER_DB_PASSWORD=$peerPassword",
    "PEER_QUANT_DATABASE=$($runtime.PGDATABASE)",
    "PEER_N8N_DATABASE=$PeerN8nDatabase",
    "PEER_QUANT_WRITE_API_KEY=$writeKey",
    "QUANT_SHARED_READ_API_KEY=$readKey",
    "PEER_N8N_ENCRYPTION_KEY=$n8nKey",
    "PEER_BACKGROUND_TASKS_ENABLED=false"
) | Set-Content -LiteralPath $peerEnv -Encoding utf8

& icacls $secrets /inheritance:r /grant:r "$env:USERNAME`:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null
[pscustomobject]@{
    PeerRole = $PeerRole
    QuantDatabase = $runtime.PGDATABASE
    N8nDatabase = $PeerN8nDatabase
    SecretFile = $peerEnv
    SharedReadGatewayConfigured = $true
}
