#!/usr/bin/env bash
# Source-only quant-service hot deployment. Dependencies, migrations and the
# base image still require an immutable release.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: scripts/hotfix-quant-service.sh [--apply] [--disable] [--rollback ID] [--list]

Environment:
  QUANT_HOTFIX_COMPOSE_FILES  space-separated compose files (default: ./compose.yaml)
  QUANT_HOTFIX_ENV_FILE        compose env file (default: ./.env)
  QUANT_HOTFIX_HOST_DIR        host overlay root (default: ./hotfix/quant-service)
  QUANT_HOTFIX_SERVICE         one service to recreate (legacy shorthand)
  QUANT_HOTFIX_SERVICES        space-separated services to recreate
                               (default: QUANT_HOTFIX_SERVICE or quant-research)
  QUANT_HOTFIX_HEALTH_URL      one health URL (legacy shorthand)
  QUANT_HOTFIX_HEALTH_URLS     space-separated URLs matching QUANT_HOTFIX_SERVICES
                               (default: QUANT_HOTFIX_HEALTH_URL or 5681/health)
  QUANT_HOTFIX_RETAIN          retained overlay releases (default: 5)
EOF
  exit 2
}

apply=false
disable=false
rollback_id=""
list_releases=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    --disable) disable=true; shift ;;
    --rollback) [[ -n "${2:-}" ]] || usage; rollback_id="$2"; shift 2 ;;
    --list) list_releases=true; shift ;;
    *) usage ;;
  esac
done
[[ "$disable" != true || ( "$rollback_id" == "" && "$list_releases" != true ) ]] || usage
[[ "$rollback_id" == "" || "$rollback_id" =~ ^hotfix-[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "invalid rollback release id" >&2; exit 2;
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
compose_files_raw="${QUANT_HOTFIX_COMPOSE_FILES:-$repo_root/compose.yaml}"
read -r -a compose_files <<< "$compose_files_raw"
for index in "${!compose_files[@]}"; do
  file="${compose_files[$index]}"
  [[ "$file" = /* ]] || file="$repo_root/$file"
  [[ -f "$file" ]] || { echo "compose file not found: $file" >&2; exit 2; }
  compose_files[$index]="$(cd "$(dirname "$file")" && pwd -P)/$(basename "$file")"
done
env_file="${QUANT_HOTFIX_ENV_FILE:-$repo_root/.env}"
[[ "$env_file" = /* ]] || env_file="$repo_root/$env_file"
[[ -f "$env_file" ]] || { echo "compose env file not found: $env_file" >&2; exit 2; }
hotfix_root="${QUANT_HOTFIX_HOST_DIR:-$repo_root/hotfix/quant-service}"
[[ "$hotfix_root" = /* ]] || hotfix_root="$repo_root/$hotfix_root"
hotfix_root="$(mkdir -p "$hotfix_root" && cd "$hotfix_root" && pwd -P)"
services_raw="${QUANT_HOTFIX_SERVICES:-${QUANT_HOTFIX_SERVICE:-quant-research}}"
read -r -a services <<< "$services_raw"
health_urls_raw="${QUANT_HOTFIX_HEALTH_URLS:-${QUANT_HOTFIX_HEALTH_URL:-http://127.0.0.1:5681/health}}"
read -r -a health_urls <<< "$health_urls_raw"
(( ${#services[@]} > 0 && ${#services[@]} == ${#health_urls[@]} )) || {
  echo "QUANT_HOTFIX_SERVICES and QUANT_HOTFIX_HEALTH_URLS must have the same non-zero count" >&2
  exit 2
}
retain="${QUANT_HOTFIX_RETAIN:-5}"
[[ "$retain" =~ ^[0-9]+$ ]] && (( retain >= 2 && retain <= 20 )) || {
  echo "QUANT_HOTFIX_RETAIN must be between 2 and 20" >&2; exit 2;
}
for command in docker curl python3 rsync git; do command -v "$command" >/dev/null || {
  echo "$command is required" >&2; exit 127;
}; done

compose=(docker compose --env-file "$env_file")
for file in "${compose_files[@]}"; do compose+=(--file "$file"); done

list_release_ids() {
  [[ -d "$hotfix_root/releases" ]] || return 0
  local path
  for path in "$hotfix_root"/releases/hotfix-*; do
    [[ -d "$path" ]] || continue
    basename "$path"
  done | sort -r
}

if [[ "$list_releases" == true ]]; then
  list_release_ids
  exit 0
fi

update_env() {
  local key="$1" value="$2" temp
  temp="$(mktemp "${env_file}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$env_file" >"$temp"
  chmod --reference="$env_file" "$temp" 2>/dev/null || chmod 0600 "$temp"
  mv -f "$temp" "$env_file"
}

atomic_replace() {
  # Python's os.replace is available in the base image and on macOS, while
  # mv -T is not portable between BSD and GNU userlands.
  python3 - "$1" "$2" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY
}

restart_and_verify() {
  "${compose[@]}" up -d --no-build --pull never --no-deps --force-recreate "${services[@]}"
  local attempt hotfix_enabled health_url service_name health_index
  hotfix_enabled="$(awk -F= '$1 == "QUANT_HOTFIX_ENABLED" {print $2; exit}' "$env_file" 2>/dev/null || true)"
  for attempt in $(seq 1 45); do
    health_index=0
    for health_url in "${health_urls[@]}"; do
      if ! curl -fsS --max-time 5 "$health_url" >/tmp/quant-hotfix-health.json 2>/dev/null; then
        break
      fi
      python3 - /tmp/quant-hotfix-health.json <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("health status is not ok")
PY
      if [[ "$hotfix_enabled" == "true" ]]; then
        service_name="${services[$health_index]}"
        "${compose[@]}" exec -T "$service_name" test -f /app/hotfix/current/app/main.py
      fi
      ((health_index += 1))
    done
    if (( health_index == ${#health_urls[@]} )); then return 0; fi
    sleep 2
  done
  return 1
}

previous_target="$(readlink "$hotfix_root/current" 2>/dev/null || true)"
previous_enabled="$(awk -F= '$1 == "QUANT_HOTFIX_ENABLED" {print $2; exit}' "$env_file" 2>/dev/null || true)"
previous_enabled="${previous_enabled:-false}"
restore_previous() {
  if [[ -n "$previous_target" && -d "$hotfix_root/$previous_target" ]]; then
    ln -sfn "$previous_target" "$hotfix_root/current.next"
    atomic_replace "$hotfix_root/current.next" "$hotfix_root/current"
    restore_enabled="$previous_enabled"
  else
    rm -f "$hotfix_root/current"
    restore_enabled=false
  fi
  update_env QUANT_HOTFIX_ENABLED "$restore_enabled"
  restart_and_verify >/dev/null 2>&1 || true
}

if [[ "$disable" == true ]]; then
  if [[ "$apply" != true ]]; then
    echo "would disable quant-service source overlay and restart $service"
    exit 0
  fi
  update_env QUANT_HOTFIX_ENABLED false
  restart_and_verify
  echo "quant-service source overlay disabled"
  exit 0
fi

if [[ "$rollback_id" != "" ]]; then
  release_dir="$hotfix_root/releases/$rollback_id"
  [[ -d "$release_dir" && -f "$release_dir/app/main.py" ]] || {
    echo "overlay release not found or incomplete: $rollback_id" >&2; exit 1;
  }
  if [[ "$apply" != true ]]; then
    echo "would activate quant-service overlay $rollback_id and restart $service"
    exit 0
  fi
  ln -sfn "releases/$rollback_id" "$hotfix_root/current.next"
  atomic_replace "$hotfix_root/current.next" "$hotfix_root/current"
  update_env QUANT_HOTFIX_HOST_DIR "$hotfix_root"
  update_env QUANT_HOTFIX_ENABLED true
  if ! restart_and_verify; then
    restore_previous
    echo "overlay rollback failed; previous source restored" >&2
    exit 1
  fi
  echo "quant-service overlay rollback activated: $rollback_id"
  exit 0
fi

release_id="hotfix-$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "$repo_root" rev-parse --short HEAD)"
release_dir="$hotfix_root/releases/$release_id"
printf 'overlay_release=%s\nsource=%s\ncompose_service=%s\nhealth=%s\n' \
  "$release_id" "$repo_root/quant-service/app" "$services_raw" "$health_urls_raw"
if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply to stage and restart without rebuilding the image"
  exit 0
fi

mkdir -p "$hotfix_root/releases"
stage_dir="$(mktemp -d "$hotfix_root/.stage.XXXXXX")"
cleanup() { rm -rf -- "$stage_dir"; }
trap cleanup EXIT
mkdir -p "$stage_dir/app"
rsync -a --delete --safe-links \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$repo_root/quant-service/app/" "$stage_dir/app/"
python3 -m compileall -q "$stage_dir/app"
rm -rf "$stage_dir/app/__pycache__"
printf '%s\n' "$(git -C "$repo_root" rev-parse HEAD)" >"$stage_dir/.base-git-sha"
mv "$stage_dir" "$release_dir"
ln -sfn "releases/$release_id" "$hotfix_root/current.next"
atomic_replace "$hotfix_root/current.next" "$hotfix_root/current"
update_env QUANT_HOTFIX_HOST_DIR "$hotfix_root"
update_env QUANT_HOTFIX_ENABLED true

if ! restart_and_verify; then
  restore_previous
  echo "quant-service hotfix health verification failed; previous source restored" >&2
  exit 1
fi

releases=()
while IFS= read -r old; do
  [[ -n "$old" ]] && releases+=("$old")
done < <(list_release_ids)
if (( ${#releases[@]} > retain )); then
  for old in "${releases[@]:retain}"; do
    [[ "$old" == "$release_id" ]] && continue
    rm -rf -- "$hotfix_root/releases/$old"
  done
fi
echo "quant-service hotfix activated: $release_id (image was not rebuilt)"
