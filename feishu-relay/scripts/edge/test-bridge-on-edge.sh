#!/usr/bin/env bash
# Run the bridge test suite where larkx actually exists (47edge supervisor venv)
# and report the pass count. The workstation lacks the vendored larkx package,
# so `test_*.py` skip locally; this is the real gate before shipping a bridge
# change. Syncs to a temp dir, runs, and cleans up — no production path touched.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
bridge_dir="$repo_root/feishu-relay/bridge"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
venv_python="${LARKX_VENV_PYTHON:-/opt/supervisor/.venv/bin/python}"
remote_dir="/tmp/bridge-test-$$"
remote_home="/tmp/larkx-test-$$"
ssh_opts=(-i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

[[ -r "$edge_key" ]] || { echo "edge SSH key not readable: $edge_key" >&2; exit 2; }
command -v rsync >/dev/null || { echo "rsync is required" >&2; exit 127; }

cleanup() { ssh "${ssh_opts[@]}" "$edge_host" "rm -rf '$remote_dir' '$remote_home'" >/dev/null 2>&1 || true; }
trap cleanup EXIT

rsync -a -e "ssh ${ssh_opts[*]}" "$bridge_dir/" "$edge_host:$remote_dir/"
ssh "${ssh_opts[@]}" "$edge_host" \
	"cd '$remote_dir' && LARKX_HOME='$remote_home' '$venv_python' -m unittest discover -s . -p 'test_*.py' ${*:-}"
