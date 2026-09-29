#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: deploy-code-only.sh <target-sha> <release-label> --from-sha <active-sha> [--apply]
EOF
  exit 2
}

target_sha="${1:-}"
release_label="${2:-}"
shift 2 || usage
from_sha=""
apply=false
while (($#)); do
  case "$1" in
    --from-sha) from_sha="${2:-}"; shift 2 ;;
    --apply) apply=true; shift ;;
    *) usage ;;
  esac
done
[[ "$target_sha" =~ ^[0-9a-f]{40}$ ]] || usage
[[ "$from_sha" =~ ^[0-9a-f]{40}$ ]] || usage
[[ "$release_label" =~ ^[A-Za-z0-9._-]+$ ]] || usage

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
git cat-file -e "$target_sha^{commit}"
git cat-file -e "$from_sha^{commit}"
[[ -z "$(git status --porcelain)" ]] || { echo 'worktree is dirty' >&2; exit 1; }

changed_files="$(git diff --name-only "$from_sha" "$target_sha")"
[[ -n "$changed_files" ]] || { echo 'target SHA has no changes' >&2; exit 1; }
# Three kinds of path.  Owner runtime source ships in this release.  Paths
# that never reach the owner runtime are skipped: tests and docs carry no
# behaviour, and feishu-relay/ and frontend/ run on the edge (released there by
# the edge overlay).  Everything else - migrations, requirements, Dockerfiles,
# compose, deploy/ and scripts/ (the systemd guards run from the release
# checkout) - needs the full release.
runtime_files=""
skipped_files=""
# A migration whose code is unchanged - only comments or docstrings differ -
# changes no schema, so it does not force a full release.  Anything else in a
# migration does.
migration_code_unchanged() {
  python3 - "$from_sha" "$target_sha" "$1" <<'PY'
import ast, subprocess, sys

def code(sha, path):
    try:
        text = subprocess.run(["git", "show", f"{sha}:{path}"], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        return None
    tree = ast.parse(text)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)

before, after = code(sys.argv[1], sys.argv[3]), code(sys.argv[2], sys.argv[3])
sys.exit(0 if before is not None and before == after else 1)
PY
}
while IFS= read -r path; do
  case "$path" in
    quant-service/migrations/versions/*.py)
      if migration_code_unchanged "$path"; then
        skipped_files+="$path (comments or docstrings only)"$'\n'
        continue
      fi
      echo "full release required for: $path" >&2; exit 1 ;;
  esac
  case "$path" in
    quant-service/app/*|quant-service/entrypoint.py|quant-service/run_server.py|quant-service/database_bootstrap.py|quant-service/alembic.ini)
      runtime_files+="$path"$'\n' ;;
    quant-service/tests/*|docs/*|*.md|.github/*|feishu-relay/*|frontend/*|scripts/*.test.mjs|scripts/test_*.py)
      skipped_files+="$path"$'\n' ;;
    # Release tooling that runs on the operator workstation, the Windows owner
    # workstation or the edge - never inside the owner peer runtime.
    scripts/release-sync-status.sh|scripts/shared-peer/deploy-code-only.sh|scripts/windows/*|\
    scripts/*feishu-relay-edge*.sh|scripts/*edge-relay-workflows.sh|scripts/install-edge-import-watchdog.sh)
      skipped_files+="$path"$'\n' ;;
    *) echo "full release required for: $path" >&2; exit 1 ;;
  esac
done <<< "$changed_files"

if [[ "$apply" != true ]]; then
  printf 'code-only release candidate: sha=%s label=%s from=%s\n' "$target_sha" "$release_label" "$from_sha"
  printf 'owner runtime files:\n%s' "${runtime_files:-  (none: this release only records the new SHA)
}"
  printf 'not owner runtime (skipped; edge paths ship with the edge overlay):\n%s' "${skipped_files:-  (none)
}"
  exit 0
fi

owner_host="${OWNER_PEER_HOST:-stockpeer@47.110.79.189}"
owner_port="${OWNER_PEER_PORT:-3535}"
owner_key="${OWNER_PEER_SSH_KEY:-$HOME/.ssh/stockpeer_ed25519}"
compose_dir="${OWNER_COMPOSE_DIR:-/home/stockpeer/trading_hareness/deploy/shared-peer}"
archive="$(mktemp "/tmp/trading-hareness-code-${target_sha}.XXXXXX.tar.gz")"
stage="$(mktemp -d)"
cleanup() { rm -rf "$stage" "$archive"; }
trap cleanup EXIT

git archive "$target_sha" \
  quant-service/app quant-service/entrypoint.py quant-service/run_server.py \
  quant-service/database_bootstrap.py quant-service/alembic.ini quant-service/migrations |
  tar -x --strip-components=1 -C "$stage"
tar -C "$stage" -czf "$archive" .
scp -q -i "$owner_key" -P "$owner_port" "$archive" \
  "$owner_host:/tmp/$(basename "$archive")"

remote_archive="/tmp/$(basename "$archive")"
ssh -i "$owner_key" -p "$owner_port" "$owner_host" \
  "TARGET_SHA='$target_sha' RELEASE_LABEL='$release_label' COMPOSE_DIR='$compose_dir' ARCHIVE='$remote_archive' bash -s" <<'REMOTE'
set -euo pipefail
release_root="$HOME/trading_hareness/hotfix/quant-service/releases/$RELEASE_LABEL"
current_root="$HOME/trading_hareness/hotfix/quant-service/current"
previous_root=""
if [ -e "$current_root" ]; then previous_root="$(readlink -f "$current_root")"; fi
rm -rf "$release_root"
mkdir -p "$release_root"
tar -xzf "$ARCHIVE" -C "$release_root"
test -f "$release_root/app/main.py"
test -f "$release_root/entrypoint.py"
ln -sfn "$release_root" "${current_root}.next"
mv -Tf "${current_root}.next" "$current_root"

set_env() {
  key="$1"; value="$2"; file="$COMPOSE_DIR/.env"; tmp="${file}.tmp"
  awk -F= -v key="$key" -v value="$value" '
    BEGIN { replaced=0 }
    $1 == key { if (!replaced) { print key "=" value; replaced=1 }; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$file" > "$tmp"
  chmod 0600 "$tmp"
  mv -f "$tmp" "$file"
}
set_env PEER_APP_GIT_SHA "$TARGET_SHA"
set_env PEER_APP_RELEASE "$RELEASE_LABEL"
set_env PEER_EXPECTED_RELEASE "$RELEASE_LABEL"
set_env PEER_APP_BUILD_CREATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
set_env QUANT_HOTFIX_ENABLED true

cd "$COMPOSE_DIR"
C=(docker compose --env-file .env -f compose.yaml -f compose.intraday-owner.yaml)
"${C[@]}" config --quiet
if ! "${C[@]}" up -d --no-build --pull never --force-recreate --wait db-tunnel quant-research quant-research-scheduler; then
  if [ -n "$previous_root" ]; then
    ln -sfn "$previous_root" "${current_root}.next"
    mv -Tf "${current_root}.next" "$current_root"
    "${C[@]}" up -d --no-build --pull never --force-recreate --wait quant-research quant-research-scheduler || true
  fi
  exit 1
fi
rm -f "$ARCHIVE"
printf 'code-only release active: %s\n' "$RELEASE_LABEL"
REMOTE

echo 'source release applied; verify owner health and routes before declaring success'
