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
  scripts/shared-peer/deploy-code-only.sh
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
archive_root="trading_hareness"
tar_transform=()
if [[ "${name}" != "${archive_root}" ]]; then
  # The activation helper always extracts into a release directory whose
  # repository child is named trading_hareness.  Local checkouts are often
  # named n8n, so normalize only the top-level archive component here.
  tar_transform=(-s "/^${name}/${archive_root}/")
fi
# macOS may materialize AppleDouble ``._*`` sidecar files and extended
# attributes while walking a checkout.  They are not source files; carrying
# them into a Linux release can make UTF-8 source tests read binary sidecars
# and produces noisy tar warnings during activation.
export COPYFILE_DISABLE=1
tar -czf "${output_archive}" -C "${parent}" \
  "${tar_transform[@]}" \
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
  --exclude="${name}/._*" \
  --exclude="${name}/*/._*" \
  --exclude="${name}/**/._*" \
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
    if [[ "${entry}" == "${archive_root}/${path}" || "${entry}" == "${archive_root}/${path}/" || "${entry}" == "${archive_root}/${path}/"* ]]; then
      found=0
      break
    fi
  done
  (( found == 0 )) || { echo "archive verification failed: ${path}" >&2; exit 1; }
done
for entry in "${archive_entries[@]}"; do
  [[ "${entry}" != *"/._"* ]] || { echo "archive verification failed: AppleDouble sidecar ${entry}" >&2; exit 1; }
done
printf 'archive=%s\nroot=%s\nrequired_paths=%s\n' "${output_archive}" "${repo_root}" "${#required[@]}"
