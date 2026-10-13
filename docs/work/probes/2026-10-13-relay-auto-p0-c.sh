#!/usr/bin/env bash
# W010 step 0, P0-6 and P0-7, run by the user: it notifies the user's own Feishu account twice
# (pulled into 中继自测群 and removed again; one text marked urgent in the app).
# Arguments are fixed: the self-test group and the two-person chat with the user's account.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
out="$here/2026-10-13-relay-auto-p0-c.out.txt"
ssh -i "${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}" -o BatchMode=yes -o ConnectTimeout=10 "${RELAY_EDGE_HOST:-root@47.114.113.152}" \
  '/opt/supervisor/.venv/bin/python - 7696053062053530578 7669767676335541451' < "$here/2026-10-13-relay-auto-p0-c.py" 2>&1 | cut -c1-700 | tee "$out"
echo "saved: $out"
