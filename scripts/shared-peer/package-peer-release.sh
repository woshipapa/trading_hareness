#!/usr/bin/env bash
set -euo pipefail

# Build the peer source archive from the checkout tree, not a short allow-list
# of Docker inputs. This keeps systemd scripts, operator docs and dashboard
# assets in the same immutable release that compose points at.
repo_root="${1:?repository root is required}"
output_archive="${2:?output archive is required}"
repo_root="$(cd "${repo_root}" && pwd -P)"
mkdir -p "$(dirname "${output_archive}")"

required=(
  compose.yaml OPERATIONS.md frontend workflows certs
  scripts/peer-session-guard.sh scripts/backfill-full-market-daily.sh
  scripts/shared-peer/activate-peer-release.sh
  scripts/shared-peer/verify-owner-cutover.py
  deploy/shared-peer/compose.yaml deploy/shared-peer/compose.intraday-owner.yaml
  quant-service/entrypoint.py quant-service/app/owner_peer_contract.py
  quant-service/app/owner_deploy_events.py
)
for path in "${required[@]}"; do
  [[ -e "${repo_root}/${path}" ]] || { echo "missing required release path: ${path}" >&2; exit 1; }
done

parent="$(dirname "${repo_root}")"
name="$(basename "${repo_root}")"
tar -czf "${output_archive}" -C "${parent}" \
  --exclude="${name}/.git" \
  --exclude="${name}/.env" \
  --exclude="${name}/*/.env" \
  --exclude="${name}/node_modules" \
  --exclude="${name}/*/node_modules" \
  --exclude="${name}/frontend/dist" \
  --exclude="${name}/state" \
  --exclude="${name}/backups" \
  --exclude="${name}/logs" \
  --exclude="${name}/artifacts" \
  --exclude="${name}/.pytest_cache" \
  --exclude="${name}/**/__pycache__" \
  --exclude="${name}/**/*.pyc" \
  "${name}"

archive_entries=()
while IFS= read -r entry; do
  archive_entries+=("${entry}")
done < <(tar -tzf "${output_archive}")
for path in "${required[@]}"; do
  found=1
  for entry in "${archive_entries[@]}"; do
    if [[ "${entry}" == "${name}/${path}" || "${entry}" == "${name}/${path}/" || "${entry}" == "${name}/${path}/"* ]]; then
      found=0
      break
    fi
  done
  (( found == 0 )) || { echo "archive verification failed: ${path}" >&2; exit 1; }
done
printf 'archive=%s\nroot=%s\nrequired_paths=%s\n' "${output_archive}" "${repo_root}" "${#required[@]}"
