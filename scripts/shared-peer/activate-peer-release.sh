#!/usr/bin/env bash
set -euo pipefail

repo_archive="${1:?repo archive is required}"
wheelhouse_archive="${2:?wheelhouse archive is required}"
peer_home="${PEER_HOME:-/home/stockpeer}"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
release_store="${PEER_RELEASES_ROOT:-${peer_home}/.local/share/trading-hareness/releases}"
release_root="${release_store}/${release_id}"
repo_target="${release_root}/trading_hareness"
wheelhouse_target="${release_root}/wheelhouse"
current_repo="${peer_home}/trading_hareness"
current_wheelhouse="${peer_home}/wheelhouse"
saved_env="$(mktemp)"
trap 'rm -f "${saved_env}"' EXIT

test -r "${repo_archive}"
test -r "${wheelhouse_archive}"
tar -tf "${repo_archive}" >/dev/null
tar -tf "${wheelhouse_archive}" >/dev/null

if [[ -f "${current_repo}/deploy/shared-peer/.env" ]]; then
  install -m 0600 "${current_repo}/deploy/shared-peer/.env" "${saved_env}"
fi

install -d -m 0755 "${release_store}" "${release_root}"
tar -xf "${repo_archive}" -C "${release_root}"
tar -xf "${wheelhouse_archive}" -C "${release_root}"
# A release is not only the Docker build context. The systemd guard and
# backfill timer execute files from the top-level checkout, and operators need
# the compose/operations/frontend/workflow assets for a reversible handoff.
# Validate the complete tree before switching the current symlink so a partial
# archive can never recreate the 203/EXEC incident.
for required_path in \
  "compose.yaml" "OPERATIONS.md" "frontend" "workflows" "certs" \
  "scripts/peer-session-guard.sh" "scripts/backfill-full-market-daily.sh" \
  "scripts/shared-peer/ensure-batch-tunnel.sh" "scripts/shared-peer/verify-owner-cutover.py"; do
  test -e "${repo_target}/${required_path}" || {
    echo "peer release archive is incomplete: missing ${required_path}" >&2
    exit 1
  }
done
chmod 0755 "${repo_target}/scripts/peer-session-guard.sh" "${repo_target}/scripts/backfill-full-market-daily.sh"
test -f "${repo_target}/deploy/shared-peer/compose.yaml"
test -f "${repo_target}/deploy/shared-peer/compose.intraday-owner.yaml"
grep -q 'PEER_REQUIRE_OWNER_SEMANTICS' "${repo_target}/deploy/shared-peer/compose.intraday-owner.yaml"
grep -q 'owner_semantics_required' "${repo_target}/quant-service/entrypoint.py"
test -f "${repo_target}/quant-service/app/owner_peer_contract.py"
grep -q 'api/v1/peer/contract' "${repo_target}/quant-service/app/owner_peer_contract.py"
grep -q 'PEER_OWNER_CONTRACT_MODE' "${repo_target}/quant-service/app/owner_peer_contract.py"
test -f "${repo_target}/quant-service/app/owner_deploy_events.py"
grep -q 'owner_deploy_events' "${repo_target}/quant-service/app/owner_deploy_events.py"
test -f "${repo_target}/deploy/shared-peer/ssh-tunnel-entrypoint.sh"
grep -q 'LOCAL_DB_BIND_PORT' "${repo_target}/deploy/shared-peer/ssh-tunnel-entrypoint.sh"
grep -q '5433:127.0.0.1:15433' "${repo_target}/deploy/shared-peer/ssh-tunnel-entrypoint.sh"
test -f "${repo_target}/scripts/shared-peer/ensure-batch-tunnel.sh"
chmod 0755 "${repo_target}/scripts/shared-peer/ensure-batch-tunnel.sh"
test -x "${repo_target}/scripts/shared-peer/ensure-batch-tunnel.sh"
test -f "${repo_target}/scripts/shared-peer/verify-owner-cutover.py"
grep -q 'no_security_definer_functions' "${repo_target}/scripts/shared-peer/verify-owner-cutover.py"
grep -q 'no_role_memberships' "${repo_target}/scripts/shared-peer/verify-owner-cutover.py"
grep -q 'no_quant_relation_dml' "${repo_target}/scripts/shared-peer/verify-owner-cutover.py"
grep -q 'no_quant_sequence_writes' "${repo_target}/scripts/shared-peer/verify-owner-cutover.py"
test -f "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q '\[switch\]\$DryRun' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA quant' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'REVOKE ALL PRIVILEGES ON SCHEMA quant' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA quant' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'REVOKE ALL PRIVILEGES ON ALL PROCEDURES IN SCHEMA quant' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'CREATE ROLE \$PeerRole LOGIN NOINHERIT' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'ALTER ROLE \$PeerRole LOGIN NOINHERIT' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q "statement_timeout = '15min'" "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q "idle_in_transaction_session_timeout = '5min'" "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'REVOKE %I FROM %I' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'WritableQuantRelationCount' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'WritableQuantSequenceCount' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -Fq 'REASSIGN OWNED BY $PeerRole TO $($runtime.PGADMINUSER)' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -Fq 'ALTER DATABASE $PeerN8nDatabase OWNER TO $PeerRole' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'unexpected shared objects' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'pg_get_userbyid(datdba)' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -Fq 'ALTER DATABASE $($runtime.PGDATABASE) OWNER TO $($runtime.PGADMINUSER)' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'pg_get_userbyid(nspowner)' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -Fq 'ALTER SCHEMA quant OWNER TO $($runtime.PGADMINUSER)' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q "has_schema_privilege('\$PeerRole','quant','CREATE')" "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'rolinherit FROM pg_roles' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'datdba' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'nspowner' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
grep -q 'c.relowner' "${repo_target}/scripts/shared-peer/bootstrap-local-peer.ps1"
test -f "${repo_target}/quant-service/app/owner_storage.py"
grep -q 'owner_runtime_schema_status' "${repo_target}/quant-service/app/owner_storage.py"
grep -q 'OWNER_RUNTIME_REQUIRED_COLUMNS' "${repo_target}/quant-service/app/owner_storage.py"
grep -q 'canonical_bars_daily' "${repo_target}/quant-service/app/owner_storage.py"
grep -q 'daily_adjustment_factors' "${repo_target}/quant-service/app/owner_storage.py"
# The owner contract stores factor semantics in daily_adjustment_factors.raw;
# peer activation must not require owner-only cold twins, materialized state
# columns, guard indexes, or peer migrations that owner never promised.
test -f "${repo_target}/quant-service/app/adjustment_factor_semantics.py"
grep -q 'persisted_factor_semantics_sql' "${repo_target}/quant-service/app/adjustment_factor_semantics.py"
grep -q "raw->>'factor_semantics'" "${repo_target}/quant-service/app/adjustment_factor_semantics.py"
test -f "${repo_target}/quant-service/app/event_research.py"
# Event research now reads the owner factor projection and aliases the
# selected value as ``persisted_adj_factor``.  Do not pin the activation gate
# to the old bar-side ``close*adj_factor`` spelling: accepting the old string
# would let a release bypass the owner Longhu/provider/PIT contract, while
# requiring it would reject the corrected implementation.
grep -q 'persisted_adj_factor' "${repo_target}/quant-service/app/event_research.py"
grep -q 'daily_adjustment_factors' "${repo_target}/quant-service/app/event_research.py"
test -f "${repo_target}/quant-service/app/research_capacity.py"
grep -q '_tiered_capacity_sql' "${repo_target}/quant-service/app/research_capacity.py"
test -f "${repo_target}/quant-service/app/adjustment_factor_semantics.py"
grep -q 'longhu_qfq_derived' "${repo_target}/quant-service/app/adjustment_factor_semantics.py"
test -f "${repo_target}/quant-service/app/owner_factor_repository.py"
grep -q 'owner_persisted_adjustment_factor' "${repo_target}/quant-service/app/owner_factor_repository.py"
grep -q 'read_persisted_factor_controls' "${repo_target}/quant-service/app/full_market_daily_controls_sync.py"
grep -q 'read_persisted_factors' "${repo_target}/quant-service/app/core_daily_control_sync.py"
grep -q 'read_persisted_factors' "${repo_target}/quant-service/app/stock_study_service.py"
grep -q 'read_persisted_factor_controls' "${repo_target}/quant-service/app/main.py"
grep -q 'read_persisted_factor_window' "${repo_target}/quant-service/app/main.py"
grep -q 'factor.available_at <' "${repo_target}/quant-service/app/post_close_strategy_service.py"
test -f "${repo_target}/quant-service/app/instrument_registry.py"
test -f "${repo_target}/quant-service/app/instrument_lock_retry.py"
grep -q 'lock_timeout' "${repo_target}/quant-service/app/instrument_lock_retry.py"
grep -q 'ORDER BY symbol' "${repo_target}/quant-service/app/instrument_registry.py"
test -f "${repo_target}/quant-service/app/replay_readiness_coverage.py"
grep -q 'complete_adjusted' "${repo_target}/quant-service/app/replay_readiness_coverage.py"
grep -q 'EXPECTED_REPLAY_COVERAGE_DEFINITION' "${repo_target}/scripts/shared-peer/verify-owner-cutover.py"
test -f "${wheelhouse_target}/SHA256SUMS"
(
  cd "${wheelhouse_target}"
  tr -d '\r' < SHA256SUMS | sha256sum --check - >/dev/null
)

if [[ -s "${saved_env}" ]]; then
  install -m 0600 "${saved_env}" "${repo_target}/deploy/shared-peer/.env"
fi
# Environment bundles are commonly produced on the Windows owner host. Strip
# CRLF before Linux shells source the file; otherwise a trailing CR can become
# part of an HTTP header value and make an otherwise valid static API key fail.
if [[ -f "${repo_target}/deploy/shared-peer/.env" ]]; then
  sed -i 's/\r$//' "${repo_target}/deploy/shared-peer/.env"
else
  install -m 0600 /dev/null "${repo_target}/deploy/shared-peer/.env"
fi

set_env_if_placeholder() {
  local key="$1"
  local value="$2"
  local env_path="${repo_target}/deploy/shared-peer/.env"
  local current
  current="$(awk -F= -v wanted_key="$key" '$1 == wanted_key {print substr($0,index($0,"=")+1); exit}' "${env_path}" || true)"
  if [[ -n "${current}" && "${current}" != "unknown" && "${current}" != "unset" ]]; then
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  awk -F= -v wanted_key="$key" -v replacement="$value" '
    BEGIN { replaced=0 }
    $1 == wanted_key { if (!replaced) { print wanted_key "=" replacement; replaced=1 }; next }
    { print }
    END { if (!replaced) print wanted_key "=" replacement }
  ' "${env_path}" >"${tmp}"
  chmod 0600 "${tmp}"
  mv -f "${tmp}" "${env_path}"
}

set_env_if_placeholder PEER_APP_RELEASE "${release_id}"
app_release="$(awk -F= '$1 == "PEER_APP_RELEASE" {print substr($0,index($0,"=")+1); exit}' "${repo_target}/deploy/shared-peer/.env" || true)"
set_env_if_placeholder PEER_EXPECTED_RELEASE "${app_release:-${release_id}}"
set_env_if_placeholder PEER_APP_BUILD_CREATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
grep -q '^PEER_WHEELHOUSE_PATH=' "${repo_target}/deploy/shared-peer/.env" || \
  printf '\nPEER_WHEELHOUSE_PATH=%s\n' "${current_wheelhouse}" >> "${repo_target}/deploy/shared-peer/.env"

if [[ "$(id -u)" -eq 0 ]]; then
  chown -R stockpeer:stockpeer "${release_root}"
fi
if [[ -e "${current_repo}" && ! -L "${current_repo}" ]]; then
  mv "${current_repo}" "${release_root}/previous-working-tree"
fi
ln -sfn "${repo_target}" "${current_repo}.next"
mv -Tf "${current_repo}.next" "${current_repo}"
if [[ -e "${current_wheelhouse}" && ! -L "${current_wheelhouse}" ]]; then
  mv "${current_wheelhouse}" "${release_root}/previous-wheelhouse"
fi
ln -sfn "${wheelhouse_target}" "${current_wheelhouse}.next"
mv -Tf "${current_wheelhouse}.next" "${current_wheelhouse}"

printf 'release=%s\nrepo=%s\nwheelhouse=%s\n' "${release_id}" "${repo_target}" "${wheelhouse_target}"
