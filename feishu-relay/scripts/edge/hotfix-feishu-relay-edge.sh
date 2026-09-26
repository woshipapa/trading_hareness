#!/usr/bin/env bash
# Fast source-only edge hotfix deployment.
# A validated source overlay is mounted read-only over the immutable adapter
# image, and the matching LarkAgentX Python bridge is selected by a stable
# systemd entrypoint. This path never builds or pulls an image.
set -euo pipefail

usage() {
  echo "usage: $0 [--apply] [--rollback <release-id>] [--list]" >&2
  echo "  default: test and print the no-build overlay plan" >&2
  echo "  --apply: stage, validate, activate and restart the edge" >&2
  echo "  --rollback ID: activate a retained overlay release (with --apply)" >&2
  echo "  --list: list retained overlay release IDs" >&2
  exit 2
}

apply=false
rollback_id=""
list_releases=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    --rollback)
      [[ -n "${2:-}" ]] || usage
      rollback_id="$2"
      shift 2
      ;;
    --list) list_releases=true; shift ;;
    *) usage ;;
  esac
done

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
repo_root="$(cd "$project_root/.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
secrets_env="${RELAY_EDGE_SECRETS_ENV:-/etc/feishu-relay-edge/secrets.env}"
hotfix_root="${RELAY_EDGE_HOTFIX_ROOT:-$edge_dir/hotfix}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
container_name="${RELAY_EDGE_ADAPTER_CONTAINER:-feishu-relay-edge-adapter}"
retain_releases="${RELAY_EDGE_HOTFIX_RETAIN:-5}"

[[ "$rollback_id" == "" || ( "$rollback_id" =~ ^hotfix-[A-Za-z0-9][A-Za-z0-9._-]*$ && "$rollback_id" != *..* ) ]] || {
  echo "rollback release ID contains unsupported characters" >&2
  exit 2
}
[[ "$retain_releases" =~ ^[0-9]+$ ]] && (( retain_releases >= 2 && retain_releases <= 20 )) || {
  echo "RELAY_EDGE_HOTFIX_RETAIN must be between 2 and 20" >&2
  exit 2
}
required_commands=(ssh)
if [[ "$rollback_id" == "" && "$list_releases" != true ]]; then
  required_commands+=(node npm python3 rsync git)
fi
for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }
done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
rsync_ssh=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

if [[ "$list_releases" == true ]]; then
  [[ "$rollback_id" == "" ]] || usage
  "${ssh_command[@]}" "$edge_host" bash -s -- "$hotfix_root" <<'REMOTE_LIST'
set -euo pipefail
hotfix_root="$1"
if [ ! -d "$hotfix_root/releases" ]; then
  echo '(no retained overlay releases)'
else
  find "$hotfix_root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r
fi
REMOTE_LIST
  exit 0
fi

if [[ "$rollback_id" != "" ]]; then
  if [[ "$apply" != true ]]; then
    printf 'would activate overlay %s on %s; append --apply\n' "$rollback_id" "$edge_host"
    exit 0
  fi
  "${ssh_command[@]}" "$edge_host" bash -s -- \
    "$edge_dir" "$runtime_env" "$secrets_env" "$hotfix_root" "$rollback_id" "$container_name" <<'REMOTE_ROLLBACK'
set -euo pipefail
edge_dir="$1"
runtime_env="$2"
secrets_env="$3"
hotfix_root="$4"
rollback_id="$5"
container_name="$6"
exec 9>/var/lock/feishu-relay-edge-hotfix.lock
flock -w 120 9
release_dir="$hotfix_root/releases/$rollback_id"
bridge_python=/opt/supervisor/.venv/bin/python
test -f "$runtime_env"
test -f "$secrets_env"
test -f "$release_dir/adapter/index.mjs"
test -f "$release_dir/adapter/package.json"
test -f "$release_dir/source-registry.json"
test -x "$bridge_python"
base_image_id="$(docker inspect -f '{{.Image}}' "$container_name")"
base_package_hash="$(docker exec "$container_name" sha256sum /app/package.json | awk '{print $1}')"
candidate_package_hash="$(sha256sum "$release_dir/adapter/package.json" | awk '{print $1}')"
if [ "$base_package_hash" != "$candidate_package_hash" ]; then
  echo 'rollback refused: retained overlay dependencies differ from the current image' >&2
  exit 42
fi
if [ -f "$release_dir/.base-git-sha" ]; then
  rollback_sha="$(tr -d '\n' < "$release_dir/.base-git-sha")"
  [[ "$rollback_sha" =~ ^[0-9a-fA-F]{7,64}$ ]] || exit 1
else
  rollback_sha=""
fi

update_env() {
  key="$1"; value="$2"; temp="$(mktemp "${runtime_env}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$runtime_env" > "$temp"
  chown --reference="$runtime_env" "$temp"
  chmod --reference="$runtime_env" "$temp"
  mv -f "$temp" "$runtime_env"
}
if [ -f "$release_dir/ops/larkagentx-bridge-entrypoint.sh" ] && [ -f "$release_dir/ops/larkagentx-group-relay-hotfix.conf" ]; then
  chmod 0755 "$hotfix_root" "$hotfix_root/releases"
  install -m 0755 "$release_dir/ops/larkagentx-bridge-entrypoint.sh" /opt/larkagentx/bridge-entrypoint.sh
  install -d -m 0755 /etc/systemd/system/larkagentx-group-relay.service.d
  install -m 0644 "$release_dir/ops/larkagentx-group-relay-hotfix.conf" \
    /etc/systemd/system/larkagentx-group-relay.service.d/10-hotfix-entrypoint.conf
  systemctl daemon-reload
fi
previous_target="$(readlink "$hotfix_root/current" 2>/dev/null || true)"
previous_runtime_env="$(mktemp "${runtime_env}.rollback.XXXXXX")"
cp -p "$runtime_env" "$previous_runtime_env"
trap 'rm -f -- "$previous_runtime_env"' EXIT
ln -sfn "releases/$rollback_id" "$hotfix_root/current.next"
mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"
update_env FEISHU_ADAPTER_HOTFIX_ENABLED true
update_env FEISHU_ADAPTER_HOTFIX_DIR "$hotfix_root"
if [ -n "$rollback_sha" ]; then update_env APP_GIT_SHA "$rollback_sha"; fi
update_env APP_RELEASE "hotfix-rollback-$rollback_id"
update_env APP_BUILD_CREATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
restart_runtime() {
  cd "$edge_dir"
  docker compose --env-file "$runtime_env" --env-file "$secrets_env" \
    up -d --no-build --pull never --force-recreate --no-deps feishu-adapter || return 1
  systemctl restart larkagentx-group-relay.service
}
restore_runtime() {
  if [[ "$previous_target" == releases/hotfix-* ]] && [ -d "$hotfix_root/$previous_target" ]; then
    ln -sfn "$previous_target" "$hotfix_root/current.next"
    mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"
  else
    rm -f "$hotfix_root/current"
  fi
  restore_env_temp="$(mktemp "${runtime_env}.restore.XXXXXX")"
  cp -p "$previous_runtime_env" "$restore_env_temp"
  mv -f "$restore_env_temp" "$runtime_env"
  restart_runtime >/dev/null 2>&1 || true
}
rollback_is_healthy() {
  curl -fsS http://127.0.0.1:18300/health >/tmp/feishu-relay-hotfix-health.json 2>/dev/null || return 1
  systemctl is-active --quiet larkagentx-group-relay.service || return 1
  curl -fsS http://127.0.0.1:8090/health >/tmp/larkagentx-hotfix-health.json 2>/dev/null || return 1
  [ "$(docker inspect -f '{{.Image}}' "$container_name")" = "$base_image_id" ] || return 1
  "$bridge_python" - /tmp/feishu-relay-hotfix-health.json /tmp/larkagentx-hotfix-health.json "$rollback_id" "$release_dir" <<'PY'
import json
import pathlib
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    adapter = json.load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    bridge = json.load(stream)
if adapter.get("status") != "ok" or adapter.get("runtime_source") != "source-overlay":
    raise SystemExit(1)
if (adapter.get("build") or {}).get("release") != "hotfix-rollback-" + sys.argv[3]:
    raise SystemExit(1)
if bridge.get("status") != "ok" or int(bridge.get("listen_chat_count", 0)) < 1:
    raise SystemExit(1)
if (bridge.get("websocket") or {}).get("state") not in {"connecting", "connected"}:
    raise SystemExit(1)
if pathlib.Path(sys.argv[4], "bridge/bridge.py").is_file() and (bridge.get("runtime_source") != "source-overlay" or bridge.get("release") != sys.argv[3]):
    raise SystemExit(1)
PY
}
if ! restart_runtime; then
  restore_runtime
  echo 'overlay rollback restart failed; previous runtime restored' >&2
  exit 1
fi
rollback_health_ok=false
for attempt in $(seq 1 45); do
  if rollback_is_healthy; then rollback_health_ok=true; break; fi
  sleep 2
done
if [ "$rollback_health_ok" != true ]; then
  restore_runtime
  echo 'overlay rollback health verification failed; previous runtime restored' >&2
  exit 1
fi
docker exec "$container_name" test -f /app/hotfix/current/adapter/index.mjs
echo "overlay rollback activated: $rollback_id"
REMOTE_ROLLBACK
  exit 0
fi

# The fast path still runs the complete adapter suite and parses the bridge
# sources before any upload. Build both independently owned frontends locally;
# these are asset builds only and never invoke Docker.
# Rollback intentionally bypasses this local check so an emergency recovery
# is not blocked by an unrelated workstation worktree failure.
( cd "$project_root/adapter" && node --test *.test.mjs ) || {
	echo "feishu-relay adapter tests failed; nothing was staged" >&2
	exit 1
}
( cd "$repo_root/frontend" && npm run build ) || {
	echo "quant frontend build failed; nothing was staged" >&2
	exit 1
}
( cd "$project_root/dashboard" && npm run build ) || {
	echo "Feishu dashboard build failed; nothing was staged" >&2
	exit 1
}
python3 - "$project_root/bridge/bridge.py" \
  "$project_root/bridge/larkagentx_image_property.py" \
  "$project_root/bridge/proto_wire.py" \
  "$project_root/bridge/event_spool.py" \
  "$project_root/bridge/owner_lock.py" \
  "$project_root/bridge/source_filter.py" <<'PY'
import ast
import pathlib
import sys

for name in sys.argv[1:]:
    ast.parse(pathlib.Path(name).read_text(encoding="utf-8"), filename=name)
PY
PYTHONPATH="$project_root/bridge" python3 -m unittest discover \
  -s "$project_root/bridge" -p 'test_*.py' -q

head_sha="$(git -C "$repo_root" rev-parse --verify HEAD)"
[[ "$head_sha" =~ ^[0-9a-fA-F]{7,64}$ ]] || { echo "cannot derive a valid git SHA" >&2; exit 2; }
head_short="${head_sha:0:12}"
dirty_suffix=""
if ! git -C "$repo_root" diff --quiet --ignore-submodules -- \
  feishu-relay frontend/dist feishu-relay/dashboard/dist; then
  dirty_suffix="-dirty"
fi
release_id="hotfix-$(date -u +%Y%m%dT%H%M%SZ)-${head_short}${dirty_suffix}-$RANDOM"
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'overlay_release=%s\nbase_git_sha=%s\nedge_host=%s\nhotfix_root=%s\n' \
  "$release_id" "$head_sha" "$edge_host" "$hotfix_root"
if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply to stage and activate with --no-build"
  exit 0
fi

stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/feishu-relay-overlay.XXXXXX")"
cleanup() { rm -rf -- "$stage_dir"; }
trap cleanup EXIT
mkdir -p "$stage_dir/adapter" "$stage_dir/frontend-dist" "$stage_dir/quant-frontend-dist" "$stage_dir/bridge" "$stage_dir/ops"

# package.json is copied for a compatibility check; npm is never run here.
rsync -a --delete --safe-links \
  --exclude 'node_modules' --exclude '*.test.mjs' --exclude '*.log' \
  "$project_root/adapter/" "$stage_dir/adapter/"
rsync -a --delete --safe-links "$project_root/dashboard/dist/" "$stage_dir/frontend-dist/"
rsync -a --delete --safe-links "$repo_root/frontend/dist/" "$stage_dir/quant-frontend-dist/"
install -m 0644 "$project_root/config/source-registry.json" "$stage_dir/source-registry.json"
for bridge_file in "$project_root"/bridge/*.py; do
  bridge_name="$(basename "$bridge_file")"
  case "$bridge_name" in
    test_*.py) continue ;;
  esac
  install -m 0644 "$bridge_file" "$stage_dir/bridge/$bridge_name"
done
install -m 0755 "$project_root/deploy/edge/larkagentx-bridge-entrypoint.sh" \
  "$stage_dir/ops/larkagentx-bridge-entrypoint.sh"
install -m 0644 "$project_root/deploy/edge/larkagentx-group-relay-hotfix.conf" \
  "$stage_dir/ops/larkagentx-group-relay-hotfix.conf"
install -m 0644 "$project_root/deploy/edge/feishu-relay-dashboard.nginx.conf" \
  "$stage_dir/ops/feishu-relay-dashboard.conf"
printf '%s\n' "$head_sha" > "$stage_dir/.base-git-sha"
test -f "$stage_dir/adapter/package.json"
test -f "$stage_dir/adapter/index.mjs"
test -f "$stage_dir/source-registry.json"
test -f "$stage_dir/frontend-dist/index.html"
test -f "$stage_dir/quant-frontend-dist/index.html"
test -f "$stage_dir/bridge/bridge.py"
test -f "$stage_dir/bridge/larkagentx_image_property.py"
test -f "$stage_dir/bridge/proto_wire.py"
test -f "$stage_dir/bridge/event_spool.py"
test -f "$stage_dir/bridge/owner_lock.py"
test -f "$stage_dir/bridge/source_filter.py"
test -x "$stage_dir/ops/larkagentx-bridge-entrypoint.sh"
test -f "$stage_dir/ops/larkagentx-group-relay-hotfix.conf"
test -f "$stage_dir/ops/feishu-relay-dashboard.conf"

upload_dir="$hotfix_root/staging/${release_id}.uploading"
"${ssh_command[@]}" "$edge_host" bash -s -- "$edge_dir" "$hotfix_root" "$upload_dir" <<'REMOTE_PREPARE'
set -euo pipefail
edge_dir="$1"; hotfix_root="$2"; upload_dir="$3"
test -d "$edge_dir"
install -d -m 0755 "$hotfix_root" "$hotfix_root/releases"
install -d -m 0700 "$hotfix_root/staging"
# The bridge runs as larkagentx and reads only the public source overlay. Keep
# the source tree traversable while retaining a private upload scratch area.
chmod 0755 "$hotfix_root" "$hotfix_root/releases"
# An interrupted upload is never active; remove only stale, uniquely named
# staging directories so repeated fixes cannot accumulate partial copies.
find "$hotfix_root/staging" -mindepth 1 -maxdepth 1 -type d -name '*.uploading' -mmin +60 -exec rm -rf -- {} +
find "$edge_dir" -maxdepth 1 -type f -name '.docker-compose.yml.*.uploading' -mmin +60 -delete
rm -rf -- "$upload_dir"
install -d -m 0750 "$upload_dir"
REMOTE_PREPARE

# Keep the compose command synchronized without invoking its build graph. The
# file is uploaded under a unique name and validated before an atomic rename;
# an interrupted transfer can never leave a half-written Compose file.
compose_stage="$edge_dir/.docker-compose.yml.${release_id}.uploading"
rsync -az -e "${rsync_ssh[*]}" \
  "$project_root/deploy/edge/docker-compose.yml" \
  "$edge_host:$compose_stage"
"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$compose_stage" <<'REMOTE_COMPOSE'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; compose_stage="$4"
test -f "$compose_stage"
docker compose --env-file "$runtime_env" --env-file "$secrets_env" \
  -f "$compose_stage" config --quiet
REMOTE_COMPOSE
rsync -az --delete --safe-links -e "${rsync_ssh[*]}" \
  "$stage_dir/" "$edge_host:$upload_dir/"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$hotfix_root" "$upload_dir" \
  "$compose_stage" "$release_id" "$head_sha" "$built_at" "$container_name" "$retain_releases" <<'REMOTE_ACTIVATE'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; hotfix_root="$4"
upload_dir="$5"; compose_stage="$6"; release_id="$7"; head_sha="$8"; built_at="$9"
container_name="${10}"; retain_releases="${11}"
exec 9>/var/lock/feishu-relay-edge-hotfix.lock
flock -w 120 9
release_dir="$hotfix_root/releases/$release_id"
upload_moved=false
compose_moved=false
runtime_committed=false
compose_backup="$edge_dir/.docker-compose.yml.$release_id.previous"
nginx_conf=/etc/nginx/conf.d/feishu-relay-dashboard.conf
nginx_conf_backup="$edge_dir/.feishu-relay-dashboard.conf.$release_id.previous"
nginx_conf_changed=false
previous_runtime_env=""
cleanup_upload() {
  if [ "$upload_moved" != true ]; then rm -rf -- "$upload_dir"; fi
  if [ "$compose_moved" = true ] && [ "$runtime_committed" != true ] && [ -f "$compose_backup" ]; then
    mv -f "$compose_backup" "$edge_dir/docker-compose.yml" || true
  fi
  if [ "$runtime_committed" != true ]; then rm -f -- "$compose_stage" "$compose_backup"; fi
  if [ "$nginx_conf_changed" = true ] && [ "$runtime_committed" != true ]; then
    if [ -f "$nginx_conf_backup" ]; then install -m 0644 "$nginx_conf_backup" "$nginx_conf"; else rm -f "$nginx_conf"; fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
    rm -f "$nginx_conf_backup"
  fi
  if [ -n "$previous_runtime_env" ]; then rm -f -- "$previous_runtime_env"; fi
}
trap cleanup_upload EXIT

test -f "$runtime_env"
test -f "$secrets_env"
test -f "$upload_dir/adapter/index.mjs"
test -f "$upload_dir/adapter/package.json"
test -f "$upload_dir/source-registry.json"
test -f "$upload_dir/frontend-dist/index.html"
test -f "$upload_dir/quant-frontend-dist/index.html"
test -f "$upload_dir/bridge/bridge.py"
test -f "$upload_dir/bridge/larkagentx_image_property.py"
test -f "$upload_dir/bridge/proto_wire.py"
test -f "$upload_dir/bridge/event_spool.py"
test -f "$upload_dir/bridge/owner_lock.py"
test -f "$upload_dir/bridge/source_filter.py"
test -x "$upload_dir/ops/larkagentx-bridge-entrypoint.sh"
test -f "$upload_dir/ops/larkagentx-group-relay-hotfix.conf"
test -f "$upload_dir/ops/feishu-relay-dashboard.conf"
test -f "$upload_dir/.base-git-sha"
test -f "$compose_stage"
test ! -e "$release_dir"

# The edge nginx serves the same origin for both SPAs. Install the candidate
# config before restarting the adapter, then restore it automatically if any
# later activation or health gate fails.
if [ -f "$nginx_conf" ]; then cp -p "$nginx_conf" "$nginx_conf_backup"; fi
nginx_conf_changed=true
install -m 0644 "$upload_dir/ops/feishu-relay-dashboard.conf" "$nginx_conf"
if ! nginx -t >/dev/null 2>&1 || ! systemctl reload nginx; then
  if [ -f "$nginx_conf_backup" ]; then install -m 0644 "$nginx_conf_backup" "$nginx_conf"; else rm -f "$nginx_conf"; fi
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  nginx_conf_changed=false
  rm -f "$nginx_conf_backup"
  echo 'hotfix refused: nginx dashboard configuration failed validation or reload' >&2
  exit 42
fi

# A dependency manifest change belongs to an immutable image release.
base_image="$(docker inspect -f '{{.Config.Image}}' "$container_name")"
base_image_id="$(docker inspect -f '{{.Image}}' "$container_name")"
base_package_hash="$(docker exec "$container_name" sha256sum /app/package.json | awk '{print $1}')"
candidate_package_hash="$(sha256sum "$upload_dir/adapter/package.json" | awk '{print $1}')"
if [ "$base_package_hash" != "$candidate_package_hash" ]; then
  echo 'hotfix refused: feishu-relay/adapter/package.json changed; publish an immutable image release' >&2
  exit 42
fi

# The edge host intentionally has no Node installation. Use the exact Node
# runtime from the existing base image to validate the staged candidate.
docker run --rm --pull never -v "$upload_dir:/overlay:ro" --entrypoint sh "$base_image" -c \
  'set -eu; for file in /overlay/adapter/*.mjs; do node --check "$file"; done; node -e "JSON.parse(require(\"node:fs\").readFileSync(process.argv[1], \"utf8\"))" /overlay/source-registry.json'
bridge_python=/opt/supervisor/.venv/bin/python
test -x "$bridge_python"
PYTHONDONTWRITEBYTECODE=1 "$bridge_python" - "$upload_dir/bridge/bridge.py" "$upload_dir/bridge/larkagentx_image_property.py" "$upload_dir/bridge/proto_wire.py" "$upload_dir/bridge/event_spool.py" "$upload_dir/bridge/owner_lock.py" "$upload_dir/bridge/source_filter.py" <<'PY'
import ast
import pathlib
import sys

for name in sys.argv[1:]:
    ast.parse(pathlib.Path(name).read_text(encoding="utf-8"), filename=name)
PY
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$upload_dir/bridge" "$bridge_python" - <<'PY'
import bridge
import event_spool
import larkagentx_image_property
import owner_lock
import proto_wire
import source_filter
PY
grep -Eq '^[0-9a-fA-F]{7,64}$' "$upload_dir/.base-git-sha"
find "$upload_dir" -type d -exec chmod 0755 {} +
find "$upload_dir" -type f -exec chmod 0644 {} +
chmod 0755 "$upload_dir/ops/larkagentx-bridge-entrypoint.sh"
mv "$upload_dir" "$release_dir"
upload_moved=true

# Install the stable systemd hook from the already validated candidate. The
# unit itself never changes per hotfix; only the release pointer does.
bridge_env=/etc/larkagentx-group-relay.env
test -f "$bridge_env"
systemctl cat larkagentx-group-relay.service >/dev/null
install -m 0755 "$release_dir/ops/larkagentx-bridge-entrypoint.sh" /opt/larkagentx/bridge-entrypoint.sh
install -d -m 0755 /etc/systemd/system/larkagentx-group-relay.service.d
install -m 0644 "$release_dir/ops/larkagentx-group-relay-hotfix.conf" \
  /etc/systemd/system/larkagentx-group-relay.service.d/10-hotfix-entrypoint.conf
bridge_env_temp="$(mktemp "${bridge_env}.XXXXXX")"
awk -v value="$hotfix_root" '
  BEGIN { found=0 }
  index($0, "LARKX_BRIDGE_HOTFIX_ROOT=") == 1 { print "LARKX_BRIDGE_HOTFIX_ROOT=" value; found=1; next }
  { print }
  END { if (!found) print "LARKX_BRIDGE_HOTFIX_ROOT=" value }
' "$bridge_env" > "$bridge_env_temp"
chown --reference="$bridge_env" "$bridge_env_temp"
chmod --reference="$bridge_env" "$bridge_env_temp"
mv -f "$bridge_env_temp" "$bridge_env"
systemctl daemon-reload

# Keep a recoverable copy of the Compose contract. If activation or health
# verification fails, the previous file is restored before either process is
# restarted, so a rejected candidate cannot change the next boot behavior.
test -f "$edge_dir/docker-compose.yml"
test ! -e "$compose_backup"
cp -p "$edge_dir/docker-compose.yml" "$compose_backup"
mv -f "$compose_stage" "$edge_dir/docker-compose.yml"
compose_moved=true

previous_target="$(readlink "$hotfix_root/current" 2>/dev/null || true)"
previous_release="${previous_target#releases/}"
if [[ "$previous_target" != releases/hotfix-* || ! "$previous_release" =~ ^hotfix-[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then previous_release=""; fi
previous_runtime_env="$(mktemp "${runtime_env}.hotfix.XXXXXX")"
cp -p "$runtime_env" "$previous_runtime_env"
ln -s "releases/$release_id" "$hotfix_root/current.next"
mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"

update_env() {
  key="$1"; value="$2"; temp="$(mktemp "${runtime_env}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$runtime_env" > "$temp"
  chown --reference="$runtime_env" "$temp"
  chmod --reference="$runtime_env" "$temp"
  mv -f "$temp" "$runtime_env"
}
update_env FEISHU_ADAPTER_HOTFIX_ENABLED true
update_env FEISHU_ADAPTER_HOTFIX_DIR "$hotfix_root"
update_env APP_GIT_SHA "$head_sha"
update_env APP_RELEASE "hotfix-$release_id"
update_env APP_BUILD_CREATED_AT "$built_at"

restart_adapter() {
  cd "$edge_dir"
  docker compose --env-file "$runtime_env" --env-file "$secrets_env" \
    up -d --no-build --pull never --force-recreate --no-deps feishu-adapter
}
restart_bridge() {
  systemctl restart larkagentx-group-relay.service
}
restore_previous() {
  if [ -n "$previous_release" ] && [ -d "$hotfix_root/releases/$previous_release" ]; then
    ln -sfn "releases/$previous_release" "$hotfix_root/current.next"
    mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"
  else
    rm -f "$hotfix_root/current"
  fi
  restore_env_temp="$(mktemp "${runtime_env}.restore.XXXXXX")"
  cp -p "$previous_runtime_env" "$restore_env_temp"
  mv -f "$restore_env_temp" "$runtime_env"
  if [ "$compose_moved" = true ] && [ -f "$compose_backup" ]; then
    mv -f "$compose_backup" "$edge_dir/docker-compose.yml"
    compose_moved=false
  fi
  if [ "$nginx_conf_changed" = true ]; then
    if [ -f "$nginx_conf_backup" ]; then install -m 0644 "$nginx_conf_backup" "$nginx_conf"; else rm -f "$nginx_conf"; fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
    nginx_conf_changed=false
  fi
}
if ! restart_adapter || ! restart_bridge; then
  restore_previous
  restart_adapter >/dev/null || true
  restart_bridge >/dev/null 2>&1 || true
  echo "hotfix container restart failed; previous runtime restored" >&2
  exit 1
fi

health_file=/tmp/feishu-relay-hotfix-health.json
bridge_health_file=/tmp/larkagentx-hotfix-health.json
config_file=/tmp/feishu-relay-hotfix-config.json
bridge_python=/opt/supervisor/.venv/bin/python
bridge_is_healthy() {
  systemctl is-active --quiet larkagentx-group-relay.service || return 1
  curl -fsS http://127.0.0.1:8090/health > "$bridge_health_file" 2>/dev/null || return 1
  "$bridge_python" - "$bridge_health_file" "$release_id" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
if payload.get("status") != "ok":
    raise SystemExit(1)
if int(payload.get("listen_chat_count", 0)) < 1:
    raise SystemExit(1)
if payload.get("runtime_source") != "source-overlay" or payload.get("release") != sys.argv[2]:
    raise SystemExit(1)
# LarkAgentX reports `connecting` until its first decoded event because the
# upstream client does not expose an on-open callback. The active systemd
# process plus a started connection attempt is the restart gate; a decoded
# event changes this field to `connected`.
if (payload.get("websocket") or {}).get("state") not in {"connecting", "connected"}:
    raise SystemExit(1)
PY
}
webhook_is_healthy() {
  curl -fsS http://127.0.0.1:18300/api/group-relay/status > "$config_file" 2>/dev/null || return 1
  docker exec -i "$container_name" node -e '
    const fs = require("node:fs");
    const payload = JSON.parse(fs.readFileSync(0, "utf8"));
    if (payload.webhook_config?.all_webhook_keywords_loaded !== true) process.exit(1);
  ' < "$config_file"
}
health_ok=false
for attempt in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:18300/health > "$health_file" 2>/dev/null \
    && docker exec -i "$container_name" node -e '
      const fs = require("node:fs");
      const [expectedSha, expectedRelease] = process.argv.slice(1);
      const payload = JSON.parse(fs.readFileSync(0, "utf8"));
      if (payload.status !== "ok" || payload.runtime_source !== "source-overlay") process.exit(1);
      if (payload.build?.git_sha?.toLowerCase() !== expectedSha.toLowerCase()) process.exit(1);
      if (payload.build?.release !== expectedRelease) process.exit(1);
    ' "$head_sha" "hotfix-$release_id" < "$health_file" \
    && [ "$(docker inspect -f '{{.Image}}' "$container_name" 2>/dev/null || true)" = "$base_image_id" ] \
    && bridge_is_healthy \
    && webhook_is_healthy
  then
    health_ok=true
    break
  fi
  sleep 2
done

if [ "$health_ok" != true ]; then
  restore_previous
  restart_adapter >/dev/null || true
  restart_bridge >/dev/null 2>&1 || true
  echo "hotfix health verification failed; previous runtime restored" >&2
  exit 1
fi

if ! test -f "$hotfix_root/current/adapter/index.mjs" \
  || ! docker exec "$container_name" test -f /app/hotfix/current/adapter/index.mjs \
  || ! test -f "$hotfix_root/current/bridge/bridge.py" \
  || ! test -f "$hotfix_root/current/bridge/proto_wire.py" \
  || ! test -f "$hotfix_root/current/bridge/event_spool.py" \
  || ! test -f "$hotfix_root/current/bridge/owner_lock.py" \
  || ! test -f "$hotfix_root/current/bridge/source_filter.py" \
  || ! test -r /opt/larkagentx/bridge-entrypoint.sh; then
  restore_previous
  restart_adapter >/dev/null || true
  restart_bridge >/dev/null 2>&1 || true
  echo "hotfix file verification failed; previous runtime restored" >&2
  exit 1
fi
rm -f "$compose_backup"
compose_moved=false
rm -f "$nginx_conf_backup"
nginx_conf_changed=false
runtime_committed=true

# Retain a bounded rollback window and protect the active pointer.
active_path="$(readlink -f "$hotfix_root/current")"
find "$hotfix_root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr \
  | awk -v keep="$retain_releases" 'NR > keep { sub(/^[^ ]+ /, ""); print }' \
  | while IFS= read -r old_dir; do
      [ -n "$old_dir" ] || continue
      [ "$(readlink -f "$old_dir")" = "$active_path" ] && continue
      case "$old_dir" in
        "$hotfix_root/releases"/*) rm -rf -- "$old_dir" ;;
        *) echo "refusing unexpected prune path" >&2; exit 1 ;;
      esac
    done

echo "overlay hotfix activated: $release_id (no image build/pull)"
REMOTE_ACTIVATE

cat >&2 <<'NOTE'

The edge is running the versioned source overlay on its existing adapter
image. It is retained for rollback. Once verified, commit and publish an
immutable edge release with feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.sh; that
command disables the overlay before starting the pinned image.
NOTE
