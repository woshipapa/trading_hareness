#!/usr/bin/env bash
set -euo pipefail

# Build and start only the independent bulk tunnel.  This deliberately does
# not recreate quant-research-scheduler or change the scheduler's database
# lane; owner migrations and the final 5433 cutover have their own verifier.

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$script_dir/../.." && pwd)
env_file=${PEER_ENV_FILE:-"$repo_dir/deploy/shared-peer/.env"}

if [[ ! -f "$env_file" ]]; then
  printf 'peer env file not found: %s\n' "$env_file" >&2
  exit 2
fi

compose=(
  docker compose
  --env-file "$env_file"
  -f "$repo_dir/deploy/shared-peer/compose.yaml"
  -f "$repo_dir/deploy/shared-peer/compose.intraday-owner.yaml"
)

"${compose[@]}" config --quiet

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  printf '%s\n' '{"status":"dry_run","service":"db-batch-tunnel","scheduler_unchanged":true}'
  exit 0
fi

min_free_kb=${PEER_BATCH_BUILD_MIN_FREE_KB:-1572864}
free_kb=$(df -Pk "$repo_dir" | awk 'NR==2 {print $4}')
if [[ ! "$free_kb" =~ ^[0-9]+$ || "$free_kb" -lt "$min_free_kb" ]]; then
  printf 'insufficient free space for a bounded batch-image build: free_kb=%s required_kb=%s\n' \
    "$free_kb" "$min_free_kb" >&2
  exit 3
fi

# Never retag the legacy session image: the batch entrypoint needs the
# LOCAL_DB_BIND_PORT/ENABLE_API_FORWARD contract.
"${compose[@]}" build db-batch-tunnel
"${compose[@]}" up -d --no-build db-batch-tunnel >/dev/null

container_id=$("${compose[@]}" ps -q db-batch-tunnel)
if [[ -z "$container_id" ]]; then
  printf 'db-batch-tunnel container was not created\n' >&2
  exit 1
fi

health=''
for _ in {1..30}; do
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' "$container_id")
  if [[ "$health" == "healthy" ]]; then
    break
  fi
  if [[ "$health" == "unhealthy" || "$health" == "exited" || "$health" == "dead" ]]; then
    printf 'db-batch-tunnel health=%s\n' "$health" >&2
    exit 1
  fi
  sleep 2
done

if [[ "$health" != "healthy" ]]; then
  printf 'db-batch-tunnel health did not become healthy (last=%s)\n' "$health" >&2
  exit 1
fi

printf '%s\n' '{"status":"healthy","service":"db-batch-tunnel","scheduler_unchanged":true}'
