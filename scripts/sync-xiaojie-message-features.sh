#!/usr/bin/env bash
# Pull new 小杰 group messages from the Feishu relay edge ledger into the
# peer's research feature store (quant.raw_market_observations,
# capability xiaojie_message_feature).
#
# The edge read is a single SELECT (sender and mention fields are stripped
# before anything leaves the host); the peer side dedupes on message id, so
# the script is safe to rerun and re-reads a short overlap behind its cursor.
# Research features only: nothing here reaches a live threshold.
#
#   scripts/sync-xiaojie-message-features.sh            # incremental
#   XIAOJIE_SYNC_SINCE=2026-08-01 scripts/sync-xiaojie-message-features.sh
set -euo pipefail

edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_key="${RELAY_EDGE_SSH_KEY:-$HOME/.ssh/feishu_relay_edge_ed25519}"
peer_host="${PEER_SSH_HOST:-stockpeer@47.110.79.189}"
peer_port="${PEER_SSH_PORT:-3535}"
peer_key="${PEER_SSH_KEY:-$HOME/.ssh/stockpeer_ed25519}"
peer_container="${PEER_CONTAINER:-trading-hareness-peer-quant-research-scheduler-1}"
peer_app_dir="${PEER_APP_DIR:-/app/hotfix/current}"
# 小杰夜报 (history relay) and the 小杰交流 group.
sources="${XIAOJIE_SOURCE_KEYS:-xiaojie,relay_9b8fe9b2294641248c26e1405ac063b7}"
overlap="${XIAOJIE_SYNC_OVERLAP_MINUTES:-60}"

[[ "$sources" =~ ^[A-Za-z0-9_,]+$ ]] || { echo "invalid XIAOJIE_SOURCE_KEYS" >&2; exit 2; }
[[ "$overlap" =~ ^[0-9]+$ ]] || { echo "invalid XIAOJIE_SYNC_OVERLAP_MINUTES" >&2; exit 2; }
[[ "$peer_container" =~ ^[A-Za-z0-9_.-]+$ && "$peer_app_dir" =~ ^[A-Za-z0-9_/.-]+$ ]] || { echo "invalid peer target" >&2; exit 2; }

peer_exec() {
  ssh -i "$peer_key" -p "$peer_port" -o BatchMode=yes -o IdentitiesOnly=yes "$peer_host" \
    "export XDG_RUNTIME_DIR=/run/user/\$(id -u); export DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/docker.sock; \
     docker exec -i -w $peer_app_dir -e PYTHONPATH=$peer_app_dir -e QUANT_BACKGROUND_TASKS_ENABLED=false \
     $peer_container python -m app.xiaojie_message_features $1"
}

since="${XIAOJIE_SYNC_SINCE:-}"
if [[ -z "$since" ]]; then
  since="$(peer_exec cursor | tail -1)"
  [[ "$since" == "none" ]] && since="1970-01-01T00:00:00+00:00"
fi
[[ "$since" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}([T\ ][0-9:.]+)?([+-][0-9:]+|Z)?$ ]] || { echo "invalid cursor: $since" >&2; exit 2; }

ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes "$edge_host" \
  "sudo -u postgres psql -d n8n_relay -X -q -At -v ON_ERROR_STOP=1 -v sources='$sources' -v since='$since' -v overlap='$overlap'" <<'SQL' \
  | peer_exec ingest
SELECT json_build_object(
         'source_message_id', source_message_id, 'source_key', source_key, 'source_chat_id', source_chat_id,
         'source_create_time', source_create_time, 'created_at', created_at,
         'message', message - 'sender' - 'mentions')
  FROM feishu_group_relay_messages
 WHERE source_key = ANY(string_to_array(:'sources', ','))
   AND created_at > :'since'::timestamptz - make_interval(mins => :'overlap'::int)
 ORDER BY created_at;
SQL
