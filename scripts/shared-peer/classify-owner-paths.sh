#!/usr/bin/env bash
# Classify what changed between two commits for the 47owner peer runtime.
#
#   classify-owner-paths.sh <from-sha> <target-sha>
#
# Prints one line per changed path, "<kind>\t<path>". Run it inside the
# repository holding both commits. Shared by deploy-code-only.sh and
# scripts/release_plan.py so the two cannot disagree.
#
#   runtime       owner runtime source: ships in the code-only overlay
#   migration     a schema migration: ships in the overlay too (the peer never
#                 applies one), and the release goes on only when the owner's
#                 database is already at the target's head (schema-gate.sh)
#   skip          never reaches the owner runtime
#   full          the host's own tree (compose, systemd units, the scripts the
#                 host runs): the full release ships it
#   image         an input of the image: only the image procedure (the full
#                 release) may build
#   tunnel-image  an input of the tunnel image. A full release puts the file in
#                 the host's tree, but no release rebuilds that image or
#                 recreates the tunnel containers: that is done by hand with the
#                 owner, and until then the running tunnels keep the old one.
#   unclassified  no rule names it: the code-only release refuses and asks for a
#                 rule here. It builds nothing: an unknown path is answered by
#                 one line in this file, not by an image build. (The full
#                 release does not read this classification.)
#
# A line may also name a script that is not in the diff: one whose catalog row
# moved across the owner-host boundary between the two commits. While its row
# said another host, changes to it were skipped, so the host's copy may be older
# than any later diff shows. It is `full` until RELEASE_RELABELLED_CURRENT names
# it (space-separated paths), which says the host's copy was checked and is the
# target's.
set -euo pipefail

from_sha="${1:-}"
target_sha="${2:-}"
[[ "$from_sha" =~ ^[0-9a-f]{40}$ && "$target_sha" =~ ^[0-9a-f]{40}$ ]] \
  || { echo "usage: classify-owner-paths.sh <from-sha> <target-sha> (full SHAs)" >&2; exit 2; }
# --no-renames: a moved file is a deletion and an addition, and both names are classified.
changed_files="$(git -c core.quotepath=off diff --no-renames --name-only "$from_sha" "$target_sha")"
[[ -n "$changed_files" ]] || exit 0
# scripts/CATALOG.md says which host runs each script (generated; `generate_scripts_catalog.py --check` keeps it
# complete, and test_owner_path_classes.py ties the rows of the scripts the owner host runs to its units). Only a
# script that runs on the owner host needs more than an overlay. A script deleted by the target is in the catalog
# of the commit it comes from.
catalog_at() {
  if git cat-file -e "$1:scripts/CATALOG.md" 2>/dev/null; then
    git show "$1:scripts/CATALOG.md" |
      awk -F'|' '/^\| `/ { name = $2; host = $4; gsub(/^ *`|` *$/, "", name); gsub(/^ +| +$/, "", host); print name "\t" host }'
  elif grep -q '^scripts/' <<<"$changed_files"; then
    echo "note: $1 has no scripts/CATALOG.md, so its scripts have no row" >&2
  fi
}
target_catalog="$(catalog_at "$target_sha")"
from_catalog="$(catalog_at "$from_sha")"
label_in() { awk -F'\t' -v script="${2#scripts/}" '$1 == script { print $2; exit }' <<<"$1"; }
# The labels are named: a new or mistyped one is refused, not skipped.
host_class() {
  case "$1" in
    "") echo none ;;
    47owner*) echo owner ;;
    workstation*|owner-windows|"never run"|"no host runs it") echo other ;;
    *) echo unknown ;;
  esac
}
relabelled() {
  local before after
  before="$(host_class "$(label_in "$from_catalog" "$1")")"
  after="$(host_class "$(label_in "$target_catalog" "$1")")"
  [[ "$before" != none && "$after" != none && "$before" != "$after" && ( "$before" == owner || "$after" == owner ) ]]
}
acknowledged() { [[ " ${RELEASE_RELABELLED_CURRENT:-} " == *" $1 "* ]]; }
script_kind() {
  local label
  label="$(label_in "$target_catalog" "$1")"
  [[ -n "$label" ]] || label="$(label_in "$from_catalog" "$1")"
  if relabelled "$1" && ! acknowledged "$1"; then
    echo full
    return
  fi
  case "$(host_class "$label")" in
    owner) echo full ;;
    other) echo skip ;;
    *) echo unclassified ;;
  esac
}
# A migration whose code is unchanged - only comments or docstrings differ -
# changes no schema, so it is skipped.
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
      else
        printf 'migration\t%s\n' "$path"
      fi ;;
    quant-service/app/*|quant-service/entrypoint.py|quant-service/run_server.py|quant-service/database_bootstrap.py)
      printf 'runtime\t%s\n' "$path" ;;
    # Tests and docs carry no behaviour; feishu-relay/, frontend/, frontend-shared/ and xhs-intel/ run on the
    # edge and are released there.
    # Under scripts/ this is exactly what the catalog's generator leaves out (tests and package markers; `*`
    # crosses `/` here as its pattern does), so everything else under scripts/ has a row to be classified by.
    quant-service/tests/*|tests/*|docs/*|*.md|.github/*|feishu-relay/*|frontend/*|frontend-shared/*|xhs-intel/*|\
    scripts/*.test.mjs|scripts/test_*|scripts/*/test_*|scripts/__init__.py|scripts/*/__init__.py)
      printf 'skip\t%s\n' "$path" ;;
    # What alembic runs with, and the reconciliation of the owner's legacy schema.
    quant-service/alembic.ini|quant-service/migrations/*)
      printf 'migration\t%s\n' "$path" ;;
    # What Dockerfile.peer installs or is. (The wheelhouse and the base image are outside git.)
    quant-service/requirements.txt|quant-service/Dockerfile.peer|.dockerignore)
      printf 'image\t%s\n' "$path" ;;
    deploy/shared-peer/Dockerfile.ssh-tunnel|deploy/shared-peer/ssh-tunnel-entrypoint.sh)
      printf 'tunnel-image\t%s\n' "$path" ;;
    # The host's tree: compose and the systemd units.
    deploy/shared-peer/*)
      printf 'full\t%s\n' "$path" ;;
    # The exported standalone component and the workstation image: their own Dockerfile, compose and lock. The
    # owner builds from Dockerfile.peer + requirements.txt and never reads these.
    quant-service/compose.standalone.yaml|quant-service/Dockerfile.standalone|quant-service/standalone/*|\
    quant-service/requirements.lock|quant-service/Makefile|quant-service/component.json|quant-service/Dockerfile)
      printf 'skip\t%s\n' "$path" ;;
    # Developer tooling: Dockerfile.peer never copies quant-service/scripts/.
    quant-service/scripts/*)
      printf 'skip\t%s\n' "$path" ;;
    # Credential tooling for the operator workstation (templates and generators, never values); gitignored files
    # do not appear here at all.
    config/secrets/*|.env.example)
      printf 'skip\t%s\n' "$path" ;;
    # The workstation's own stack and the other hosts' deployment files: the local compose, the LaunchAgents,
    # the n8n workflows, the server and gateway configs, the retired edge runtime.
    compose.yaml|launchd/*|workflows/*|deploy/intraday-edge/*|deploy/post-close/*|deploy/Caddyfile|\
    deploy/quant-research-gateway-backend.conf|deploy/quant-research-gateway.nginx.conf|deploy/stockbrain-local-gateway.nginx.conf|\
    deploy/compose.server.yaml|deploy/compose.quant-handoff.yaml)
      printf 'skip\t%s\n' "$path" ;;
    # Repository metadata, registries the checks read, saved evidence.
    .gitignore|.gitattributes|hotfix/.gitkeep|config/components.json|config/release-windows.json|\
    config/longhu_vendor.example.json|config/wechat-biz-routes.json|artifacts/*|scripts/data/*)
      printf 'skip\t%s\n' "$path" ;;
    scripts/*)
      printf '%s\t%s\n' "$(script_kind "$path")" "$path" ;;
    *) printf 'unclassified\t%s\n' "$path" ;;
  esac
done <<< "$changed_files"
[[ "$target_catalog" != "$from_catalog" ]] || exit 0
while IFS=$'\t' read -r name _; do
  path="scripts/$name"
  if [[ -n "$name" ]] && ! grep -qxF "$path" <<<"$changed_files" && relabelled "$path" && ! acknowledged "$path"; then
    printf 'full\t%s (not in this diff: its catalog row moved across the owner-host boundary)\n' "$path"
  fi
done <<< "$target_catalog"
