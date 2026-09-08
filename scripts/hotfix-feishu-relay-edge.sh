#!/usr/bin/env bash
# Fast-path a small feishu-adapter code change onto the edge without a CI
# build, a GHCR push, or an image transfer: rsync the source straight to
# the edge and build the image there, using the docker layer cache that is
# already warm from the last real release (only the npm-install layer is
# ever slow, and it only reruns when package.json changes).
#
# This is deliberately NOT a tracked release: the health endpoint reports a
# synthetic "local-..." git_sha, not a real commit, and nothing here ever
# publishes to GHCR or touches PLAN_COMPLETION_MATRIX. Use it to iterate,
# then once the change is verified, commit it, tag an edge-*.* release, and
# run deploy-feishu-relay-edge-release.sh so the edge goes back to a
# pinned, auditable image with real provenance.
set -euo pipefail

usage() {
  echo "usage: $0 [--apply]" >&2
  echo "  without --apply: rsyncs and prints what would run, changes nothing on the edge" >&2
  exit 2
}

apply=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    *) usage ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
secrets_env="${RELAY_EDGE_SECRETS_ENV:-/etc/feishu-relay-edge/secrets.env}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
rsync_ssh=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

# Run the adapter's own suite before shipping anything untested.
( cd "$repo_root/feishu-adapter" && node --test *.test.mjs ) || {
  echo "feishu-adapter test suite failed; not syncing to the edge" >&2
  exit 1
}

echo "syncing feishu-adapter source (no node_modules, no tests) to $edge_host:$edge_dir/feishu-adapter/ ..."
if [[ "$apply" == true ]]; then
  rsync -az --delete -e "${rsync_ssh[*]}" \
    --exclude 'node_modules' --exclude '*.test.mjs' \
    "$repo_root/feishu-adapter/" "$edge_host:$edge_dir/feishu-adapter/"
  rsync -az -e "${rsync_ssh[*]}" \
    "$repo_root/deploy/feishu-relay-edge/Dockerfile.adapter" "$edge_host:$edge_dir/Dockerfile.adapter"
  rsync -az -e "${rsync_ssh[*]}" \
    "$repo_root/config/source-registry.json" "$edge_host:$edge_dir/config/source-registry.json"
  # rsync's own directory creation only goes one level deep; make sure the
  # parent exists before syncing into it.
  "${ssh_command[@]}" "$edge_host" "mkdir -p '$edge_dir/frontend/dist'"
  rsync -az --delete -e "${rsync_ssh[*]}" \
    "$repo_root/frontend/dist/" "$edge_host:$edge_dir/frontend/dist/"
else
  rsync -azn --delete -e "${rsync_ssh[*]}" \
    --exclude 'node_modules' --exclude '*.test.mjs' \
    "$repo_root/feishu-adapter/" "$edge_host:$edge_dir/feishu-adapter/"
  echo "dry run only; append --apply to actually sync, build and restart"
  exit 0
fi

work_tree_sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo nogit)"
git -C "$repo_root" diff --quiet -- feishu-adapter config/source-registry.json 2>/dev/null || work_tree_sha="${work_tree_sha}-dirty"
local_sha="local-$(date -u +%Y%m%dT%H%M%SZ)-${work_tree_sha}"
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$local_sha" "$built_at" <<'REMOTE'
set -euo pipefail
edge_dir="$1"
runtime_env="$2"
secrets_env="$3"
local_sha="$4"
built_at="$5"

test -f "$runtime_env"
test -f "$secrets_env"
update_env() {
  key="$1"
  value="$2"
  temp="$(mktemp "${runtime_env}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$runtime_env" > "$temp"
  install -m 0640 "$temp" "$runtime_env"
  rm -f "$temp"
}
cd "$edge_dir"
# A hotfix build must never be pushed anywhere or mistaken for a pinned
# release, so it drops any GHCR override and builds compose's own local
# image tag straight from the synced source.
sed -i '/^FEISHU_ADAPTER_IMAGE=/d' "$runtime_env"
update_env APP_GIT_SHA "$local_sha"
update_env APP_RELEASE hotfix
update_env APP_BUILD_CREATED_AT "$built_at"
docker compose --env-file "$runtime_env" --env-file "$secrets_env" build feishu-adapter
docker compose --env-file "$runtime_env" --env-file "$secrets_env" up -d --no-deps feishu-adapter
for attempt in {1..30}; do curl -fsS http://127.0.0.1:18300/health >/tmp/feishu-relay-hotfix-health.json && break; sleep 2; done
test -s /tmp/feishu-relay-hotfix-health.json
grep -Fq '"status":"ok"' /tmp/feishu-relay-hotfix-health.json
grep -Fq "\"git_sha\":\"${local_sha}\"" /tmp/feishu-relay-hotfix-health.json
cat /tmp/feishu-relay-hotfix-health.json
echo
echo 'hotfix build applied on the edge (untracked; not a release)'
REMOTE

cat >&2 <<'NOTE'

This built and ran untracked source directly on the edge for fast
iteration. Once the change is verified: commit it, tag an edge-*.*
release, wait for release-edge-images.yml to publish the GHCR image, then
run scripts/deploy-feishu-relay-edge-release.sh so the edge goes back to a
pinned image and the health endpoint reports a real commit SHA again.
NOTE
