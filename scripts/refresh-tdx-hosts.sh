#!/usr/bin/env bash
# Run the owner TDX probe, regenerate the local host pool, and print its diff.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
matrix=$(mktemp)
trap 'rm -f "$matrix"' EXIT
status=0
bash "$ROOT/scripts/tdx-owner-probe.sh" --output "$matrix" || status=$?
[[ -s "$matrix" ]] || exit "$status"
python3 "$ROOT/scripts/generate-tdx-hosts.py" "$matrix"
git -C "$ROOT" diff -- quant-service/app/datasources/sources/tdx_hosts.py
exit "$status"
