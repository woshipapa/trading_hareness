#!/usr/bin/env bash
set -euo pipefail

runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
workflow_id="xhs-intel-edge-daily-v1"
stale_after="${XHS_N8N_STALE_AFTER:-10 minutes}"

[[ "$stale_after" =~ ^[0-9]+\ (minutes|hours)$ ]] || {
  echo "invalid XHS_N8N_STALE_AFTER" >&2
  exit 2
}

[[ -r "$runtime_env" ]] || { echo "runtime env is not readable" >&2; exit 2; }
pguser=""; pgdatabase=""; pgpassword=""
while IFS='=' read -r key value; do
  case "$key" in
    RELAY_PGUSER) pguser="$value" ;;
    RELAY_PGDATABASE) pgdatabase="$value" ;;
    RELAY_PGPASSWORD) pgpassword="$value" ;;
  esac
done < "$runtime_env"
if [[ -z "$pguser" || -z "$pgdatabase" || -z "$pgpassword" ]]; then
  echo "remote relay database settings are incomplete" >&2
  exit 2
fi

exec 9>/var/lock/xhs-n8n-execution-reconcile.lock
flock -w 30 9

candidate_count="$(PGPASSWORD="$pgpassword" psql -h 127.0.0.1 -p 5432 -U "$pguser" -d "$pgdatabase" -Atqc \
  "SELECT count(*) FROM execution_entity WHERE \"workflowId\"='$workflow_id' AND status='running' AND \"startedAt\" < now() - interval '$stale_after'")"
if [[ "$candidate_count" == 0 ]]; then
  exit 0
fi

audit_dir="$edge_dir/backups/n8n-executions/$(date -u +%Y%m%d-%H%M%S)-xhs-watchdog"
install -d -m 0750 "$audit_dir"
PGPASSWORD="$pgpassword" psql -h 127.0.0.1 -p 5432 -U "$pguser" -d "$pgdatabase" -At -F $'\t' -c \
  "SELECT id,\"workflowId\",\"startedAt\" FROM execution_entity WHERE \"workflowId\"='$workflow_id' AND status='running' AND \"startedAt\" < now() - interval '$stale_after' ORDER BY \"startedAt\"" > "$audit_dir/before.tsv"
chmod 0600 "$audit_dir/before.tsv"
PGPASSWORD="$pgpassword" psql -h 127.0.0.1 -p 5432 -U "$pguser" -d "$pgdatabase" -Atqc \
  "UPDATE execution_entity SET status='crashed',\"stoppedAt\"=now() WHERE \"workflowId\"='$workflow_id' AND status='running' AND \"startedAt\" < now() - interval '$stale_after' RETURNING id" > "$audit_dir/updated_ids.tsv"
chmod 0600 "$audit_dir/updated_ids.tsv"
echo "reconciled $candidate_count stale XHS n8n execution(s); audit=$audit_dir"
