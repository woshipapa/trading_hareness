#!/usr/bin/env bash
# Full owner release: new checkout, new image, then the main service before the scheduler.
#
#   deploy-full-release.sh <target-sha> <release-label>            check only (reads owner /health)
#   deploy-full-release.sh <target-sha> <release-label> --apply    release to 47owner
#
# This is RELEASE_SYNC_47 stage F as one command, with what 2026-10-08 taught:
# the release checkout needs an empty certs/ git cannot carry; the heal timer
# must be held off with the guard lock; release metadata is written through the
# .env symlink into ~/.secrets/owner; the tunnel is left alone; the main service
# is healthy before the scheduler starts; and the rollback point is read from
# the host at release time, not remembered. Any failed check rolls back.
set -euo pipefail

usage() {
  sed -n '4,5p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

target_sha="${1:-}"
release_label="${2:-}"
[ $# -ge 2 ] || usage
shift 2
apply=false
while (($#)); do
  case "$1" in
    --apply) apply=true; shift ;;
    *) usage ;;
  esac
done
[[ "$target_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "target must be a full 40-character SHA" >&2; usage; }
[[ "$release_label" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "label must match [A-Za-z0-9._-]+" >&2; usage; }

# --- safe window ---------------------------------------------------------------
# Trading days: no restarts 08:30-15:10, and the scheduler must not restart
# during its 18:45-22:05 post-close run. RELEASE_CLOCK="<1-7> <HHMM>" exists only
# so the tests can pin the clock.
read -r weekday hhmm <<<"${RELEASE_CLOCK:-$(TZ=Asia/Shanghai date '+%u %H%M')}"
minute_of_day=$((10#$hhmm))
in_no_restart_window() {
  { [ "$minute_of_day" -ge 830 ] && [ "$minute_of_day" -le 1510 ]; } \
    || { [ "$minute_of_day" -ge 1845 ] && [ "$minute_of_day" -le 2205 ]; }
}
if [ "$weekday" -le 5 ] && in_no_restart_window; then
  echo "refusing: Beijing $hhmm on a weekday is inside a no-restart window (08:30-15:10, 18:45-22:05)" >&2
  exit 3
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
git cat-file -e "$target_sha^{commit}"
if [ -n "${RELEASE_SKIP_FETCH:-}" ]; then :; else git fetch -q origin main; fi
git merge-base --is-ancestor "$target_sha" "${RELEASE_MAINLINE:-origin/main}" \
  || { echo "refusing: $target_sha is not on ${RELEASE_MAINLINE:-origin/main}; release only merged commits" >&2; exit 4; }

archive="$(mktemp "${TMPDIR:-/tmp}/trading_hareness-${target_sha}.XXXXXX.tar")"
cleanup() { rm -f "$archive"; }
trap cleanup EXIT
git archive --format=tar --prefix=trading_hareness/ -o "$archive" "$target_sha"
# activate-peer-release.sh requires certs/, which .gitignore keeps out of git.
# Only the directory: the certificates travel through config/secrets/sync-secrets.sh.
python3 - "$archive" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], "a") as archive:
    info = tarfile.TarInfo("trading_hareness/certs")
    info.type, info.mode = tarfile.DIRTYPE, 0o755
    archive.addfile(info)
PY
# Read the whole listing first: grep -q closing the pipe early makes tar die of
# SIGPIPE, which pipefail turns into a failed release.
listing="$(tar -tf "$archive")"
grep -qx 'trading_hareness/scripts/shared-peer/activate-peer-release.sh' <<<"$listing"
grep -qx 'trading_hareness/scripts/shared-peer/release-lock.sh' <<<"$listing" \
  || { echo "refusing: $target_sha predates the guard lock; release a newer commit" >&2; exit 5; }

owner_host="${OWNER_PEER_HOST:-stockpeer@47.110.79.189}"
owner_port="${OWNER_PEER_PORT:-3535}"
owner_key="${OWNER_PEER_SSH_KEY:-$HOME/.ssh/stockpeer_ed25519}"

# --- schema gate ----------------------------------------------------------------
# The owner applies migrations itself on the Windows workstation (RELEASE_SYNC_47
# stage D), and code must never run ahead of its schema. Compare the release's
# migration head with the revision the running service reports. Behind is never
# overridable: migrate first. An unknown or unreadable revision passes only when
# RELEASE_ALLOW_SCHEMA_REVISION names exactly what was seen ("none" if nothing),
# so an override cannot outlive the situation it was written for.
# RELEASE_DB_REVISION replaces the owner read (tests; "" means unreadable).
lineage_tool="$(cd "$(dirname "$0")/../.." && pwd)/quant-service/scripts/migration_lineage.py"
migrations_root="$(mktemp -d "${TMPDIR:-/tmp}/release-migrations.XXXXXX")"
cleanup() { rm -f "$archive"; rm -rf "$migrations_root"; }
git archive "$target_sha" quant-service/migrations/versions 2>/dev/null | tar -x -C "$migrations_root" \
  || { echo "refusing: $target_sha has no quant-service/migrations/versions" >&2; exit 6; }
versions_dir="$migrations_root/quant-service/migrations/versions"
code_head="$(python3 "$lineage_tool" "$versions_dir" --head)" \
  || { echo "refusing: the migration lineage at $target_sha is broken (see above)" >&2; exit 6; }
if [ -n "${RELEASE_DB_REVISION+x}" ]; then
  db_revision="$RELEASE_DB_REVISION"
else
  db_revision="$(ssh -i "$owner_key" -p "$owner_port" "$owner_host" \
      'curl -fsS --max-time 20 http://127.0.0.1:15682/health' 2>/dev/null \
    | python3 -c 'import json, sys
lineage = (json.load(sys.stdin).get("owner_storage") or {}).get("database_lineage") or {}
print(lineage.get("alembic_version") or "")' 2>/dev/null || true)"
fi
allowed_revision="${RELEASE_ALLOW_SCHEMA_REVISION:-}"
if [ -z "$db_revision" ]; then
  if [ "$allowed_revision" != none ]; then
    echo "refusing: cannot read the owner's database revision from /health; release head is $code_head." >&2
    echo "  If the service is down and this release is the repair, confirm the revision on the Windows" >&2
    echo "  workstation (alembic current) and rerun with RELEASE_ALLOW_SCHEMA_REVISION=none." >&2
    exit 6
  fi
  schema_status="unverified (allowed)"
else
  set +e
  python3 "$lineage_tool" "$versions_dir" --check "$db_revision" >/dev/null
  check_status=$?
  set -e
  case "$check_status" in
    0) schema_status="at_head" ;;
    3)
      echo "refusing: the owner database is at $db_revision but this release needs $code_head." >&2
      echo "  Apply these on the Windows workstation first (RELEASE_SYNC_47 stage D), then release:" >&2
      python3 "$lineage_tool" "$versions_dir" --pending "$db_revision" | sed 's/^/    /' >&2
      echo "  SQL for review, without a database: alembic -c alembic.ini upgrade $db_revision:head --sql" >&2
      exit 6 ;;
    4)
      if [ "$allowed_revision" != "$db_revision" ]; then
        echo "refusing: the owner database is at $db_revision, which this release's lineage does not contain" >&2
        echo "  (release head $code_head). Either the owner has a migration this repository lacks - recover" >&2
        echo "  its source, never recreate it from memory - or this release is older than the database." >&2
        echo "  Once understood, rerun with RELEASE_ALLOW_SCHEMA_REVISION=$db_revision." >&2
        exit 6
      fi
      schema_status="unknown revision (allowed)" ;;
    *) echo "refusing: could not compare $db_revision with $code_head" >&2; exit 6 ;;
  esac
fi
printf 'schema: owner database %s, release head %s: %s\n' "${db_revision:-unreadable}" "$code_head" "$schema_status"

if [[ "$apply" != true ]]; then
  printf 'full owner release candidate: sha=%s label=%s archive=%s bytes\n' \
    "$target_sha" "$release_label" "$(wc -c < "$archive" | tr -d ' ')"
  exit 0
fi

remote_archive="/tmp/trading_hareness-${release_label}.tar"
scp -q -i "$owner_key" -P "$owner_port" "$archive" "$owner_host:$remote_archive"

ssh -i "$owner_key" -p "$owner_port" "$owner_host" \
  "TARGET_SHA='$target_sha' RELEASE_LABEL='$release_label' ARCHIVE='$remote_archive' bash -s" <<'REMOTE'
set -euo pipefail
export XDG_RUNTIME_DIR="/run/user/$(id -u)" DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"
IMAGE=trading-hareness-peer-quant-research
STATE="$HOME/.local/state/owner-release"
mkdir -p "$STATE"

# --- rollback point, read now ------------------------------------------------
prev_repo="$(readlink -f "$HOME/trading_hareness")"
prev_wheelhouse="$(readlink -f "$HOME/wheelhouse")"
env_file="$(readlink -f "$HOME/trading_hareness/deploy/shared-peer/.env")"
prev_meta="$(grep -E '^PEER_(APP_GIT_SHA|APP_RELEASE|APP_BUILD_CREATED_AT|EXPECTED_RELEASE)=' "$env_file" || true)"
prev_image="$(docker image inspect -f '{{.Id}}' "$IMAGE:latest")"
docker tag "$IMAGE:latest" "$IMAGE:rollback-$RELEASE_LABEL"
printf 'repo=%s\nwheelhouse=%s\nimage=%s\n%s\n' "$prev_repo" "$prev_wheelhouse" "$prev_image" "$prev_meta" \
  > "$STATE/$RELEASE_LABEL.rollback"
echo "rollback point: $prev_repo (image ${prev_image:7:12})"

write_meta() {  # write PEER_APP_* through the symlink into the real file
  python3 - "$env_file" "$@" <<'PY'
import os, sys
path, pairs = sys.argv[1], dict(item.split("=", 1) for item in sys.argv[2:])
lines = [line for line in open(path).read().splitlines() if line.split("=", 1)[0] not in pairs]
tmp = path + ".release-tmp"
with open(tmp, "w") as handle:
    handle.write("\n".join(lines + [f"{k}={v}" for k, v in pairs.items()]) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}

C=(docker compose -f compose.yaml -f compose.intraday-owner.yaml)
up_in_order() {
  # Tunnel untouched; the main service healthy before the scheduler starts.
  "${C[@]}" up -d --no-build --pull never --wait db-tunnel \
    && "${C[@]}" up -d --no-build --pull never --force-recreate --wait quant-research \
    && "${C[@]}" up -d --no-build --pull never --force-recreate --wait quant-research-scheduler
}

lock_tool=""
rolled_back=false
roll_back() {
  [ "$rolled_back" = true ] && return 0
  rolled_back=true
  echo "!!! rolling back to $prev_repo" >&2
  set +e
  ln -sfn "$prev_repo" "$HOME/trading_hareness.next" && mv -Tf "$HOME/trading_hareness.next" "$HOME/trading_hareness"
  ln -sfn "$prev_wheelhouse" "$HOME/wheelhouse.next" && mv -Tf "$HOME/wheelhouse.next" "$HOME/wheelhouse"
  env_file="$(readlink -f "$HOME/trading_hareness/deploy/shared-peer/.env")"
  if [ -n "$prev_meta" ]; then
    # shellcheck disable=SC2086 - one KEY=value pair per line, no spaces in values
    write_meta $prev_meta
  fi
  docker tag "$IMAGE:rollback-$RELEASE_LABEL" "$IMAGE:latest"
  (cd "$HOME/trading_hareness/deploy/shared-peer" && up_in_order) || echo "!!! rollback restart did not report healthy" >&2
}
finish() {
  status=$?
  if [ "$status" -ne 0 ]; then roll_back; fi
  [ -n "$lock_tool" ] && bash "$lock_tool" release "$RELEASE_LABEL" >/dev/null 2>&1 || true
  rm -f "$ARCHIVE" /tmp/wheelhouse-"$RELEASE_LABEL".tar
  exit "$status"
}
trap finish EXIT

# --- activate ---------------------------------------------------------------
tar -C "$HOME" -chf "/tmp/wheelhouse-$RELEASE_LABEL.tar" wheelhouse
tar -xOf "$ARCHIVE" trading_hareness/scripts/shared-peer/activate-peer-release.sh > "$STATE/activate-$RELEASE_LABEL.sh"
RELEASE_ID="$RELEASE_LABEL" bash "$STATE/activate-$RELEASE_LABEL.sh" "$ARCHIVE" "/tmp/wheelhouse-$RELEASE_LABEL.tar"
lock_tool="$HOME/trading_hareness/scripts/shared-peer/release-lock.sh"
bash "$lock_tool" hold "$RELEASE_LABEL" 3600 >/dev/null

compose_dir="$HOME/trading_hareness/deploy/shared-peer"
[ -L "$compose_dir/.env" ] || { echo ".env is no longer a symlink after activation" >&2; exit 1; }
env_file="$(readlink -f "$compose_dir/.env")"
write_meta "PEER_APP_GIT_SHA=$TARGET_SHA" "PEER_APP_RELEASE=$RELEASE_LABEL" "PEER_EXPECTED_RELEASE=$RELEASE_LABEL" \
  "PEER_APP_BUILD_CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- build and restart in order -----------------------------------------------
cd "$compose_dir"
"${C[@]}" config --quiet
"${C[@]}" build quant-research 2>&1 | tail -3
up_in_order

# --- prove it -----------------------------------------------------------------
for port in 15682 15683; do
  ok=false
  for _ in $(seq 1 36); do
    body="$(curl -fsS -m 8 "http://127.0.0.1:$port/health" 2>/dev/null || true)"
    if printf '%s' "$body" | TARGET_SHA="$TARGET_SHA" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
sys.exit(0 if d.get("status") == "ok" and d["build"]["git_sha"] == os.environ["TARGET_SHA"] else 1)' 2>/dev/null; then
      ok=true; break
    fi
    sleep 5
  done
  [ "$ok" = true ] || { echo "port $port did not report ok on $TARGET_SHA" >&2; exit 1; }
done
profile="$(docker exec trading-hareness-peer-quant-research-1 printenv QUANT_RUNTIME_PROFILE)"
[ "$profile" = intraday_edge ] || { echo "main service runs profile '$profile', expected intraday_edge" >&2; exit 1; }
[ -L "$compose_dir/.env" ] && [ -L "$compose_dir/intraday-secrets.env" ] \
  || { echo "credential files are no longer symlinks" >&2; exit 1; }
echo "full release active: $RELEASE_LABEL ($TARGET_SHA); rollback point in $STATE/$RELEASE_LABEL.rollback"
REMOTE
