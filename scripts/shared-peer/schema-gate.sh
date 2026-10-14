#!/usr/bin/env bash
# The schema gate of an owner release.
#
#   schema-gate.sh <target-sha>        inside the repository holding the commit
#
# The owner applies migrations itself on the Windows workstation (RELEASE_SYNC_47
# stage D), and code must never run ahead of its schema. Compare the release's
# migration head with the revision the running service reports. Behind is never
# overridable: migrate first. An unknown or unreadable revision passes only when
# RELEASE_ALLOW_SCHEMA_REVISION names exactly what was seen ("none" if nothing),
# so an override cannot outlive the situation it was written for.
# RELEASE_DB_REVISION replaces the owner read (tests; "" means unreadable).
# Prints one "schema:" line when the release may go on; exits 6 when it may not.
# Shared by deploy-full-release.sh and deploy-code-only.sh.
set -euo pipefail

target_sha="${1:-}"
[[ "$target_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "usage: schema-gate.sh <target-sha> (full SHA)" >&2; exit 2; }
tool_root="$(cd "$(dirname "$0")/../.." && pwd)"
owner_host="${OWNER_PEER_HOST:-stockpeer@118.178.133.37}"
owner_port="${OWNER_PEER_PORT:-22}"
owner_key="${OWNER_PEER_SSH_KEY:-$HOME/.ssh/stockpeer_ed25519}"
lineage_tool="$tool_root/quant-service/scripts/migration_lineage.py"
migrations_root="$(mktemp -d "${TMPDIR:-/tmp}/release-migrations.XXXXXX")"
trap 'rm -rf "$migrations_root"' EXIT
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
