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
# The official Feishu API exposes the oc_ chat id, while the personal
# WebSocket emits the numeric chat id. Keep both identities in the command
# lane so an explicit #xhs command can cross either ingress.
xhs_chat_ids="${XHS_COMMAND_CHAT_IDS:-oc_90f551a54bf45a1e2e9a4dc346100c77,7690524560642280650}"
xhs_webhook="${XHS_FEISHU_WEBHOOK_URL:-}"
xhs_force_build="${XHS_FORCE_BUILD:-false}"
xhs_skip_build="${XHS_SKIP_BUILD:-false}"
xhs_base_image="${XHS_BASE_IMAGE:-python:3.11-slim}"
xhs_reuse_base="${XHS_REUSE_BASE:-false}"
workflow_id="xhs-intel-edge-daily-v1"
xhs_source_dir="$repo_root/Spider_XHS"

# Spider_XHS is currently outside this monorepo. A deployment is reproducible
# only when the external checkout is clean and its commit and content digest
# are recorded in the edge component manifest.
xhs_git_sha="$(git -C "$xhs_source_dir" rev-parse HEAD 2>/dev/null || true)"
[[ -n "$xhs_git_sha" ]] || { echo "Spider_XHS is not a Git checkout" >&2; exit 2; }
xhs_source_dirty=false
if [[ -n "$(git -C "$xhs_source_dir" status --porcelain)" ]]; then
  xhs_source_dirty=true
fi
if [[ "$xhs_source_dirty" == true && "${XHS_ALLOW_DIRTY_SOURCE:-false}" != true ]]; then
  echo "Spider_XHS checkout is dirty; commit it or set XHS_ALLOW_DIRTY_SOURCE=true to record a content digest" >&2
  exit 2
fi
xhs_source_digest="$(python3 - "$xhs_source_dir" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
digest = hashlib.sha256()
skip = {'.git', 'node_modules', '__pycache__'}
for path in sorted(p for p in root.rglob('*') if p.is_file() and not any(part in skip for part in p.relative_to(root).parts)):
    rel = path.relative_to(root).as_posix().encode()
    digest.update(len(rel).to_bytes(8, 'big'))
    digest.update(rel)
    data = path.read_bytes()
    digest.update(len(data).to_bytes(8, 'big'))
    digest.update(data)
print(digest.hexdigest())
PY
)"
xhs_intel_digest="$(python3 - "$source_root/xhs-intel" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
digest = hashlib.sha256()
for path in sorted(p for p in root.iterdir() if p.is_file()):
    rel = path.name.encode()
    digest.update(len(rel).to_bytes(8, 'big'))
    digest.update(rel)
    data = path.read_bytes()
    digest.update(len(data).to_bytes(8, 'big'))
    digest.update(data)
print(digest.hexdigest())
PY
)"
workflow_sha256="$(python3 - "$source_root/workflows/xhs-intel-edge.json" <<'PY'
import hashlib
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
print(hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
PY
)"

for command in ssh scp tar python3 git; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }
done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable" >&2; exit 2; }
[[ -d "$source_root/xhs-intel" && -d "$repo_root/Spider_XHS" ]] || {
  echo "xhs-intel or Spider_XHS source is missing" >&2; exit 2;
}
ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
remote_stage="/tmp/xhs-intel-edge-$(date -u +%Y%m%d%H%M%S)-$$"
printf 'edge_host=%s\nremote_stage=%s\nworkflow_id=%s\n' "$edge_host" "$remote_stage" "$workflow_id"
printf 'xhs_git_sha=%s\nxhs_source_digest=%s\nxhs_intel_digest=%s\nxhs_workflow_hash=%s\n' \
  "$xhs_git_sha" "$xhs_source_digest" "$xhs_intel_digest" "$workflow_sha256"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/xhs-edge-upload.XXXXXX")"
cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT
mkdir -p "$tmp_dir/xhs-intel" "$tmp_dir/xhs-source"
cp "$source_root"/xhs-intel/*.py "$source_root"/xhs-intel/requirements.txt "$source_root"/xhs-intel/Dockerfile "$tmp_dir/xhs-intel/"
cp -R "$xhs_source_dir/." "$tmp_dir/xhs-source/"
rm -rf "$tmp_dir/xhs-source/.git" "$tmp_dir/xhs-source/node_modules" "$tmp_dir/xhs-source/__pycache__"
printf '%s\n' "$xhs_git_sha" > "$tmp_dir/xhs-source/.xhs-git-sha"
printf '%s\n' "$xhs_source_digest" > "$tmp_dir/xhs-source/.xhs-source-tree-sha256"
printf '%s\n' "$xhs_source_dirty" > "$tmp_dir/xhs-source/.xhs-source-dirty"
printf '%s\n' "$xhs_intel_digest" > "$tmp_dir/xhs-intel/.xhs-intel-tree-sha256"
cp "$source_root/feishu-relay/deploy/edge/docker-compose.yml" "$tmp_dir/docker-compose.yml"
cp "$source_root/workflows/xhs-intel-edge.json" "$tmp_dir/xhs-intel-edge.json"

if [[ "$apply" != true ]]; then
  echo "dry run only; source and Spider_XHS are ready, append --apply to deploy"
  exit 0
fi

[[ -r "$cookie_source" ]] || {
  echo "XHS cookie source is not readable: $cookie_source" >&2
  exit 2
}
if [[ -z "$xhs_webhook" ]]; then
  echo 'XHS_FEISHU_WEBHOOK_URL not set locally; the edge secrets.env value will be reused'
fi
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

COPYFILE_DISABLE=1 tar -C "$tmp_dir" --exclude='__pycache__' --exclude='*.pyc' --exclude='.xhs-credentials' -cf - . \
  | "${ssh_command[@]}" "$edge_host" "set -euo pipefail; rm -rf '$remote_stage'; install -d -m 0700 '$remote_stage'; tar -xf - -C '$remote_stage'"
scp -q -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes "$cookie_source" "$edge_host:$remote_stage/.xhs-cookie"
scp -q -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes "$credentials_file" "$edge_host:$remote_stage/.xhs-credentials"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$remote_stage" "$workflow_id" \
  "$remote_stage/.xhs-credentials" "$cookie_file" "$xhs_chat_ids" \
  "$remote_stage/.xhs-cookie" "$xhs_force_build" "$xhs_skip_build" "$xhs_base_image" "$xhs_reuse_base" <<'REMOTE'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; stage="$4"; workflow_id="$5"
credentials_stage="$6"; xhs_cookie_file="$7"; xhs_chat_ids="$8"; cookie_stage="${9}"; xhs_force_build="${10}"
xhs_skip_build="${11}"; xhs_base_image="${12}"; xhs_reuse_base="${13}"
bridge_env=/etc/larkagentx-group-relay.env
exec 9>/var/lock/xhs-intel-edge.lock
flock -w 120 9
test -f "$runtime_env"; test -f "$secrets_env"
test -s "$credentials_stage"
readarray -t credentials < <(python3 - "$credentials_stage" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
for key in ("collector_token",):
    value = payload.get(key, "")
    if not isinstance(value, str) or not value:
        raise SystemExit(f"missing XHS credential: {key}")
    print(value)
PY
)
[[ "${#credentials[@]}" == 1 ]] || { echo 'XHS credential bundle is incomplete' >&2; exit 2; }
xhs_token="${credentials[0]}"
xhs_webhook="$(python3 - "$credentials_stage" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("feishu_webhook", ""))
PY
)"
rm -f "$credentials_stage"
trap 'rm -rf "$stage"' EXIT
install -d -m 0755 "$edge_dir" /etc/feishu-relay-edge
install -d -m 0750 "$edge_dir/xhs-state"
test -s "$stage/xhs-source/.xhs-git-sha"
test -s "$stage/xhs-source/.xhs-source-tree-sha256"
test -s "$stage/xhs-intel/.xhs-intel-tree-sha256"
xhs_git_sha="$(cat "$stage/xhs-source/.xhs-git-sha")"
xhs_source_digest="$(cat "$stage/xhs-source/.xhs-source-tree-sha256")"
xhs_intel_digest="$(cat "$stage/xhs-intel/.xhs-intel-tree-sha256")"
xhs_workflow_hash="$(python3 - "$stage/xhs-intel-edge.json" <<'PY'
import hashlib
import json
import sys

value = json.load(open(sys.argv[1], encoding='utf-8'))
print(hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
PY
)"
old_xhs_git_sha="$(awk -F= '$1 == "XHS_SOURCE_GIT_SHA" { print substr($0, index($0, "=") + 1); exit }' "$runtime_env")"
old_xhs_source_digest="$(awk -F= '$1 == "XHS_SOURCE_TREE_SHA256" { print substr($0, index($0, "=") + 1); exit }' "$runtime_env")"
old_xhs_intel_digest="$(awk -F= '$1 == "XHS_INTEL_TREE_SHA256" { print substr($0, index($0, "=") + 1); exit }' "$runtime_env")"
xhs_webhook="${xhs_webhook:-$(awk -F= '$1 == "XHS_FEISHU_WEBHOOK_URL" { print substr($0, index($0, "=") + 1); exit }' "$secrets_env")}"
[[ -n "$xhs_webhook" ]] || { echo 'edge XHS Feishu webhook is not configured' >&2; exit 2; }
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
update_env "$runtime_env" XHS_SOURCE_GIT_SHA "$xhs_git_sha"
update_env "$runtime_env" XHS_SOURCE_TREE_SHA256 "$xhs_source_digest"
update_env "$runtime_env" XHS_INTEL_TREE_SHA256 "$xhs_intel_digest"
update_env "$runtime_env" XHS_WORKFLOW_SHA256 "$xhs_workflow_hash"
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
build_required=false
if [[ "$xhs_skip_build" == true ]]; then
  echo 'using preloaded XHS image; skipping collector image build'
elif [[ "$xhs_force_build" == true ]] || ! docker image inspect feishu-relay-edge-xhs:local >/dev/null 2>&1; then
  build_required=true
elif [[ ! -s "$edge_dir/xhs-manifest.json" || "$old_xhs_git_sha" != "$xhs_git_sha" || "$old_xhs_source_digest" != "$xhs_source_digest" || "$old_xhs_intel_digest" != "$xhs_intel_digest" ]]; then
  build_required=true
fi
if [[ "$xhs_skip_build" != true && "$build_required" == true ]]; then
  BASE_IMAGE="$xhs_base_image" docker compose --env-file "$runtime_env" --env-file "$secrets_env" build \
    --build-arg BASE_IMAGE="$xhs_base_image" --build-arg REUSE_BASE="$xhs_reuse_base" xhs-collector
elif [[ "$xhs_skip_build" != true ]]; then
  echo 'xhs image already present; skipping collector image build'
fi
docker compose --env-file "$runtime_env" --env-file "$secrets_env" up -d --no-deps xhs-collector n8n n8n-runners
for attempt in $(seq 1 45); do
  curl -fsS http://127.0.0.1:18790/health >/dev/null && break
  sleep 2
done
curl -fsS http://127.0.0.1:18790/health >/dev/null
curl -fsS http://127.0.0.1:18790/health > "$stage/xhs-health.json"
python3 - "$xhs_git_sha" "$xhs_source_digest" "$xhs_intel_digest" "$xhs_workflow_hash" "$stage/xhs-health.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[5], encoding="utf-8"))
release = value.get("release") or {}
expected = {
    "xhs_git_sha": sys.argv[1],
    "xhs_source_tree_sha256": sys.argv[2],
    "xhs_intel_tree_sha256": sys.argv[3],
    "xhs_workflow_sha256": sys.argv[4],
}
if value.get("collector") != "Spider_XHS" or any(release.get(key) != item for key, item in expected.items()):
    raise SystemExit("XHS health release identity does not match the staged component")
PY
for attempt in $(seq 1 45); do
  curl -fsS http://127.0.0.1:5678/healthz >/dev/null && break
  sleep 2
done
curl -fsS http://127.0.0.1:5678/healthz >/dev/null
runner_registered=false
for attempt in $(seq 1 45); do
  runner_state="$(docker inspect -f '{{.State.Status}}' feishu-relay-edge-n8n-runners 2>/dev/null || true)"
  if [[ "$runner_state" == running ]] && docker logs feishu-relay-edge-n8n 2>&1 | grep 'Registered runner "launcher-javascript"' >/dev/null; then
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
container=feishu-relay-edge-n8n
compose=(docker compose --env-file "$runtime_env" --env-file "$secrets_env")
install -d -m 0755 "$stage/cli"
install -m 0644 "$stage/xhs-intel-edge.json" "$stage/cli/candidate.json"
docker exec "$container" n8n export:workflow --all --output=/tmp/xhs-export.json >/dev/null
docker cp "$container:/tmp/xhs-export.json" "$stage/export.json"
docker exec --user root "$container" rm -f /tmp/xhs-export.json
python3 - "$stage" "$workflow_id" <<'PY'
import json
import pathlib
import sys

stage = pathlib.Path(sys.argv[1])
current = [w for w in json.loads((stage / 'export.json').read_text()) if w['id'] == sys.argv[2]]
candidate = json.loads((stage / 'cli/candidate.json').read_text())
if len(candidate) != 1 or candidate[0]['id'] != sys.argv[2]:
    raise SystemExit('invalid XHS workflow candidate')
if current:
    (stage / 'cli/before.json').write_text(json.dumps(current, ensure_ascii=False))
keys = ('id', 'name', 'nodes', 'connections', 'settings', 'active')
def contract(workflows):
    return [{k: w.get(k) for k in keys} for w in workflows]
if contract(current) != contract(candidate):
    (stage / 'changed').touch()
PY
workflow_changed=false
if [[ -f "$stage/changed" ]]; then
  workflow_changed=true
  workflow_backup="$edge_dir/backups/xhs-workflow/$(date -u +%Y%m%d-%H%M%S)"
  install -d -m 0700 "$workflow_backup"
  if [[ -f "$stage/cli/before.json" ]]; then
    install -m 0600 "$stage/cli/before.json" "$workflow_backup/before.json"
  fi
  n8n_stopped=false
  restart_on_exit() {
    if [[ "$n8n_stopped" == true ]]; then "${compose[@]}" start n8n >/dev/null || true; fi
    rm -rf "$stage"
  }
  trap restart_on_exit EXIT
  "${compose[@]}" stop n8n
  n8n_stopped=true
  workflow_cli=("${compose[@]}" run --rm --no-deps -v "$stage/cli:/xhs-deploy:ro" n8n)
  if "${workflow_cli[@]}" import:workflow --input=/xhs-deploy/candidate.json && \
     "${workflow_cli[@]}" publish:workflow --id="$workflow_id"; then
    echo "XHS workflow published; backup=$workflow_backup"
  else
    if [[ -f "$stage/cli/before.json" ]]; then
      "${workflow_cli[@]}" import:workflow --input=/xhs-deploy/before.json
      "${workflow_cli[@]}" publish:workflow --id="$workflow_id"
    fi
    echo 'XHS workflow publication failed; restored the previous definition' >&2
    exit 1
  fi
  "${compose[@]}" start n8n
  n8n_stopped=false
else
  echo 'XHS workflow unchanged; skipping import and restart'
fi
for attempt in $(seq 1 45); do
  curl -fsS http://127.0.0.1:5678/healthz >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:5678/healthz >/dev/null
if [[ "$workflow_changed" == true ]]; then
  workflow_loaded=false
  for attempt in $(seq 1 45); do
    if docker logs --since 300s "$container" 2>&1 | grep -q "ID: $workflow_id"; then
      workflow_loaded=true
      break
    fi
    sleep 2
  done
  if [[ "$workflow_loaded" != true ]]; then
    echo "XHS workflow was not activated after n8n restart" >&2
    docker compose --env-file "$runtime_env" --env-file "$secrets_env" ps >&2 || true
    exit 1
  fi
fi
image_digest="$(docker inspect -f '{{.Image}}' feishu-relay-edge-xhs)"
python3 - "$edge_dir/xhs-manifest.json" "$xhs_git_sha" "$xhs_source_digest" "$xhs_intel_digest" "$xhs_workflow_hash" "$image_digest" "$(cat "$stage/xhs-source/.xhs-source-dirty")" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "component": "edge-xhs",
    "xhs_git_sha": sys.argv[2],
    "xhs_source_tree_sha256": sys.argv[3],
    "xhs_intel_tree_sha256": sys.argv[4],
    "xhs_workflow_sha256": sys.argv[5],
    "xhs_image_digest": sys.argv[6],
    "xhs_source_dirty": sys.argv[7] == "true",
}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
printf 'xhs_edge_health='
curl -fsS http://127.0.0.1:18790/health | python3 -c 'import json,sys; x=json.load(sys.stdin); print(json.dumps({k:x.get(k) for k in ("status","collector","cookie_configured","feishu_webhook_configured","jobs","release")}, ensure_ascii=False))'
printf 'n8n_health='
curl -fsS http://127.0.0.1:5678/healthz | head -c 300
echo
rm -rf "$stage"
REMOTE

printf 'xhs edge deployed; local worker token updated in ignored n8n/.env\n'
