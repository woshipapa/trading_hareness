#!/usr/bin/env bash
# Deploy the edge XHS collector and import its n8n schedule.
# The collector stays on edge; local_worker.py calls Paper-KB codex-teleai.
set -euo pipefail

usage() {
  echo "usage: $0 [--apply]" >&2
  echo "  default: validate and print the plan" >&2
  echo "  --apply: upload source, build the collector, import workflow and verify health" >&2
  exit 2
}
apply=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    *) usage ;;
  esac
done

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
repo_root="$(cd "$source_root/.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
secrets_env="${RELAY_EDGE_SECRETS_ENV:-/etc/feishu-relay-edge/secrets.env}"
cookie_file="${XHS_COOKIE_FILE:-/etc/feishu-relay-edge/xhs-cookie}"
cookie_source="${XHS_COOKIE_SOURCE:-$HOME/.config/xhs/xhs-cookie}"
xhs_chat_ids="${XHS_COMMAND_CHAT_IDS:-oc_90f551a54bf45a1e2e9a4dc346100c77}"
xhs_webhook="${XHS_FEISHU_WEBHOOK_URL:-}"
xhs_force_build="${XHS_FORCE_BUILD:-false}"
workflow_id="xhs-intel-edge-daily-v1"

for command in ssh scp tar python3; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }
done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable" >&2; exit 2; }
[[ -d "$source_root/xhs-intel" && -d "$repo_root/Spider_XHS" ]] || {
  echo "xhs-intel or Spider_XHS source is missing" >&2; exit 2;
}
ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
remote_stage="/tmp/xhs-intel-edge-$(date -u +%Y%m%d%H%M%S)-$$"
printf 'edge_host=%s\nremote_stage=%s\nworkflow_id=%s\n' "$edge_host" "$remote_stage" "$workflow_id"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/xhs-edge-upload.XXXXXX")"
cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT
mkdir -p "$tmp_dir/xhs-intel" "$tmp_dir/xhs-source"
cp "$source_root"/xhs-intel/*.py "$source_root"/xhs-intel/requirements.txt "$source_root"/xhs-intel/Dockerfile "$tmp_dir/xhs-intel/"
cp -R "$repo_root/Spider_XHS/." "$tmp_dir/xhs-source/"
rm -rf "$tmp_dir/xhs-source/.git" "$tmp_dir/xhs-source/node_modules" "$tmp_dir/xhs-source/__pycache__"
cp "$source_root/feishu-relay/deploy/edge/docker-compose.yml" "$tmp_dir/docker-compose.yml"
cp "$source_root/workflows/xhs-intel-edge.json" "$tmp_dir/xhs-intel-edge.json"
cp "$source_root/feishu-relay/deploy/edge/xhs-n8n-execution-reconcile.sh" \
  "$source_root/feishu-relay/deploy/edge/xhs-n8n-execution-reconcile.service" \
  "$source_root/feishu-relay/deploy/edge/xhs-n8n-execution-reconcile.timer" "$tmp_dir/"

if [[ "$apply" != true ]]; then
  echo "dry run only; source and Spider_XHS are ready, append --apply to deploy"
  exit 0
fi

[[ -r "$cookie_source" ]] || {
  echo "XHS cookie source is not readable: $cookie_source" >&2
  exit 2
}
[[ -n "$xhs_webhook" ]] || {
  echo "XHS_FEISHU_WEBHOOK_URL must be set for the Feishu delivery lane" >&2
  exit 2
}
COOKIE_SOURCE="$cookie_source" python3 - <<'PY'
import os
from pathlib import Path

value = Path(os.environ["COOKIE_SOURCE"]).read_text(encoding="utf-8").strip()
fields = {item.split("=", 1)[0].strip() for item in value.split(";") if "=" in item}
if not {"a1", "web_session"}.issubset(fields):
    raise SystemExit("XHS cookie source must contain a1 and web_session")
PY

# Keep the worker and edge API on one random token without printing it. This is
# deliberately done only on --apply so a dry run has no secret side effect.
if [[ -n "${XHS_COLLECTOR_TOKEN:-}" ]]; then
  xhs_token="$XHS_COLLECTOR_TOKEN"
else
  xhs_token="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
fi
XHS_TOKEN_FOR_UPDATE="$xhs_token" python3 - "$source_root/.env" <<'PY'
import os, pathlib, sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
key = 'XHS_COLLECTOR_TOKEN='
lines = [line for line in lines if not line.startswith(key)]
lines.append(key + os.environ['XHS_TOKEN_FOR_UPDATE'])
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
path.chmod(0o600)
PY

credentials_file="$tmp_dir/.xhs-credentials"
XHS_TOKEN_FOR_CREDENTIALS="$xhs_token" XHS_WEBHOOK_FOR_CREDENTIALS="$xhs_webhook" \
  python3 - "$credentials_file" <<'PY'
import json
import os
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "collector_token": os.environ["XHS_TOKEN_FOR_CREDENTIALS"],
            "feishu_webhook": os.environ["XHS_WEBHOOK_FOR_CREDENTIALS"],
        },
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
pathlib.Path(sys.argv[1]).chmod(0o600)
PY

tar -C "$tmp_dir" --exclude='__pycache__' --exclude='*.pyc' -cf - . \
  | "${ssh_command[@]}" "$edge_host" "set -euo pipefail; rm -rf '$remote_stage'; install -d -m 0700 '$remote_stage'; tar -xf - -C '$remote_stage'"
scp -q -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes "$cookie_source" "$edge_host:$remote_stage/.xhs-cookie"
scp -q -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes "$credentials_file" "$edge_host:$remote_stage/.xhs-credentials"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$remote_stage" "$workflow_id" \
  "$remote_stage/.xhs-credentials" "$cookie_file" "$xhs_chat_ids" \
  "$remote_stage/.xhs-cookie" "$xhs_force_build" <<'REMOTE'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; stage="$4"; workflow_id="$5"
credentials_stage="$6"; xhs_cookie_file="$7"; xhs_chat_ids="$8"; cookie_stage="${9}"; xhs_force_build="${10}"
bridge_env=/etc/larkagentx-group-relay.env
exec 9>/var/lock/xhs-intel-edge.lock
flock -w 120 9
test -f "$runtime_env"; test -f "$secrets_env"
test -s "$credentials_stage"
readarray -t credentials < <(python3 - "$credentials_stage" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
for key in ("collector_token", "feishu_webhook"):
    value = payload.get(key, "")
    if not isinstance(value, str) or not value:
        raise SystemExit(f"missing XHS credential: {key}")
    print(value)
PY
)
[[ "${#credentials[@]}" == 2 ]] || { echo 'XHS credential bundle is incomplete' >&2; exit 2; }
xhs_token="${credentials[0]}"; xhs_webhook="${credentials[1]}"
rm -f "$credentials_stage"
trap 'rm -rf "$stage"' EXIT
install -d -m 0755 "$edge_dir" /etc/feishu-relay-edge
install -d -m 0750 "$edge_dir/xhs-state"
cp -a "$edge_dir/docker-compose.yml" "$edge_dir/docker-compose.yml.bak-xhs-$(date -u +%Y%m%d-%H%M%S)" 2>/dev/null || true
rm -rf "$edge_dir/xhs-intel" "$edge_dir/xhs-source"
cp -a "$stage/xhs-intel" "$edge_dir/xhs-intel"
cp -a "$stage/xhs-source" "$edge_dir/xhs-source"
cp "$stage/docker-compose.yml" "$edge_dir/docker-compose.yml"
test -s "$cookie_stage"
install -d -m 0700 "$(dirname "$xhs_cookie_file")"
install -o root -g root -m 0600 "$cookie_stage" "$xhs_cookie_file"
python3 - "$xhs_cookie_file" <<'PY'
from pathlib import Path
import sys

value = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
fields = {item.split("=", 1)[0].strip() for item in value.split(";") if "=" in item}
if not {"a1", "web_session"}.issubset(fields):
    raise SystemExit("remote XHS cookie validation failed")
PY

update_env() {
  file="$1"; key="$2"; value="$3"; temp="$(mktemp "${file}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$file" >"$temp"
  chown --reference="$file" "$temp"; chmod --reference="$file" "$temp"; mv -f "$temp" "$file"
}
runner_token="$(awk -F= '$1 == "N8N_RUNNERS_AUTH_TOKEN" { print substr($0, index($0, "=") + 1); exit }' "$secrets_env")"
if [[ -z "$runner_token" ]]; then
  runner_token="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  update_env "$secrets_env" N8N_RUNNERS_AUTH_TOKEN "$runner_token"
fi
update_env "$runtime_env" XHS_COOKIE_FILE "$xhs_cookie_file"
update_env "$runtime_env" XHS_KEYWORDS "AI基础设施,AI加速,系统软件,分布式训练,算力网络,推理优化"
update_env "$runtime_env" XHS_FETCH_LIMIT "5"
update_env "$runtime_env" LARKX_XHS_COMMANDS_ENABLED "true"
update_env "$runtime_env" LARKX_XHS_COMMAND_CHAT_IDS "$xhs_chat_ids"
if [ -f "$bridge_env" ]; then
  update_env "$bridge_env" LARKX_XHS_COMMANDS_ENABLED "true"
  update_env "$bridge_env" LARKX_XHS_COMMAND_CHAT_IDS "$xhs_chat_ids"
fi
update_env "$secrets_env" XHS_COLLECTOR_TOKEN "$xhs_token"
update_env "$secrets_env" XHS_FEISHU_WEBHOOK_URL "$xhs_webhook"
chmod 0600 "$secrets_env"

cd "$edge_dir"
docker compose --env-file "$runtime_env" --env-file "$secrets_env" config --quiet
if [[ "$xhs_force_build" == true ]] || ! docker image inspect feishu-relay-edge-xhs:local >/dev/null 2>&1; then
  docker compose --env-file "$runtime_env" --env-file "$secrets_env" build xhs-collector
else
  echo 'xhs image already present; skipping collector image build'
fi
docker compose --env-file "$runtime_env" --env-file "$secrets_env" up -d --no-deps xhs-collector n8n n8n-runners
for attempt in $(seq 1 45); do
  curl -fsS http://127.0.0.1:18790/health >/dev/null && break
  sleep 2
done
curl -fsS http://127.0.0.1:18790/health >/dev/null
for attempt in $(seq 1 45); do
  curl -fsS http://127.0.0.1:5678/healthz >/dev/null && break
  sleep 2
done
curl -fsS http://127.0.0.1:5678/healthz >/dev/null
runner_registered=false
for attempt in $(seq 1 45); do
  runner_state="$(docker inspect -f '{{.State.Status}}' feishu-relay-edge-n8n-runners 2>/dev/null || true)"
  if [[ "$runner_state" == running ]] && docker logs feishu-relay-edge-n8n 2>&1 | grep -q 'Registered runner'; then
    runner_registered=true
    break
  fi
  sleep 2
done
if [[ "$runner_registered" != true ]]; then
  echo 'n8n task runner did not register after restart' >&2
  docker compose --env-file "$runtime_env" --env-file "$secrets_env" ps >&2 || true
  exit 1
fi
install -o root -g root -m 0755 "$stage/xhs-n8n-execution-reconcile.sh" /usr/local/sbin/xhs-n8n-execution-reconcile.sh
install -o root -g root -m 0644 "$stage/xhs-n8n-execution-reconcile.service" /etc/systemd/system/xhs-n8n-execution-reconcile.service
install -o root -g root -m 0644 "$stage/xhs-n8n-execution-reconcile.timer" /etc/systemd/system/xhs-n8n-execution-reconcile.timer
systemctl daemon-reload
systemctl enable --now xhs-n8n-execution-reconcile.timer
systemctl start --wait xhs-n8n-execution-reconcile.service
container=feishu-relay-edge-n8n
docker cp "$stage/xhs-intel-edge.json" "$container:/tmp/xhs-intel-edge.json"
if ! docker exec "$container" sh -lc "rm -rf /tmp/xhs-export; n8n export:workflow --all --separate --output=/tmp/xhs-export >/dev/null 2>&1 && grep -R -q '$workflow_id' /tmp/xhs-export"; then
  docker exec "$container" n8n import:workflow --input=/tmp/xhs-intel-edge.json
fi
docker exec "$container" n8n publish:workflow --id="$workflow_id" >/dev/null
docker exec --user root "$container" sh -lc "rm -rf /tmp/xhs-export /tmp/xhs-intel-edge.json"
printf 'xhs_edge_health='
curl -fsS http://127.0.0.1:18790/health | python3 -c 'import json,sys; x=json.load(sys.stdin); print(json.dumps({k:x.get(k) for k in ("status","collector","cookie_configured","feishu_webhook_configured","jobs")}, ensure_ascii=False))'
printf 'n8n_health='
curl -fsS http://127.0.0.1:5678/healthz | head -c 300
echo
rm -rf "$stage"
REMOTE

printf 'xhs edge deployed; local worker token updated in ignored n8n/.env\n'
