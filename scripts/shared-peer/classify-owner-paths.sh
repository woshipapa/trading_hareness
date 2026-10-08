#!/usr/bin/env bash
# Classify what changed between two commits for the 47owner peer runtime.
#
#   classify-owner-paths.sh <from-sha> <target-sha>
#
# Prints one line per changed path, "<kind>\t<path>": runtime (ships in a
# code-only release), skip (never reaches the owner runtime) or full (needs a
# full release). Run it inside the repository holding both commits. Shared by
# deploy-code-only.sh and scripts/release_plan.py so the two cannot disagree.
set -euo pipefail

from_sha="${1:-}"
target_sha="${2:-}"
[[ "$from_sha" =~ ^[0-9a-f]{40}$ && "$target_sha" =~ ^[0-9a-f]{40}$ ]] \
  || { echo "usage: classify-owner-paths.sh <from-sha> <target-sha> (full SHAs)" >&2; exit 2; }
changed_files="$(git diff --name-only "$from_sha" "$target_sha")"
[[ -n "$changed_files" ]] || exit 0
# Three kinds of path.  Owner runtime source ships in this release.  Paths
# that never reach the owner runtime are skipped: tests and docs carry no
# behaviour, and feishu-relay/ and frontend/ run on the edge (released there by
# the edge overlay).  Everything else - migrations, requirements, Dockerfiles,
# compose, deploy/ and scripts/ (the systemd guards run from the release
# checkout) - needs the full release.
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
        printf 'skip\t%s (comments or docstrings only)\n' "$path"
        continue
      fi
      printf 'full\t%s\n' "$path"; continue ;;
  esac
  case "$path" in
    quant-service/app/*|quant-service/entrypoint.py|quant-service/run_server.py|quant-service/database_bootstrap.py|quant-service/alembic.ini)
      printf 'runtime\t%s\n' "$path" ;;
    quant-service/tests/*|docs/*|*.md|.github/*|feishu-relay/*|frontend/*|xhs-intel/*|scripts/*.test.mjs|scripts/test_*.py)
      printf 'skip\t%s\n' "$path" ;;
    # The exported standalone component: its own image, compose and lock. The
    # owner builds from Dockerfile.peer + requirements.txt and never reads these.
    quant-service/compose.standalone.yaml|quant-service/Dockerfile.standalone|quant-service/standalone/*|\
    quant-service/requirements.lock|quant-service/Makefile|quant-service/component.json)
      printf 'skip\t%s\n' "$path" ;;
    # Developer tooling: Dockerfile.peer never copies quant-service/scripts/.
    quant-service/scripts/*)
      printf 'skip\t%s\n' "$path" ;;
    # Credential tooling for the operator workstation (templates and generators,
    # never values); gitignored files do not appear here at all.
    config/secrets/env-split.py|config/secrets/sync-secrets.sh|config/secrets/env.example|config/secrets/README.md)
      printf 'skip\t%s\n' "$path" ;;
    # The teacher cycle runs on the operator workstation (svc_supervisor's
    # teacher.cycle) and reaches the owner only over HTTP and ssh.
    scripts/teacher_cycle.py|scripts/teacher_review_daily.py|scripts/teacher_harness.py)
      printf 'skip\t%s\n' "$path" ;;
    # Release tooling that runs on the operator workstation, the Windows owner
    # workstation or the edge - never inside the owner peer runtime.
    scripts/release|scripts/release_plan.py|scripts/release_window.py|config/release-windows.json|\
    scripts/shared-peer/classify-owner-paths.sh|\
    scripts/release-sync-status.sh|scripts/shared-peer/deploy-code-only.sh|scripts/shared-peer/deploy-full-release.sh|\
    scripts/windows/*|scripts/*feishu-relay-edge*.sh|scripts/*edge-relay-workflows.sh|scripts/install-edge-import-watchdog.sh)
      printf 'skip\t%s\n' "$path" ;;
    *) printf 'full\t%s\n' "$path" ;;
  esac
done <<< "$changed_files"
