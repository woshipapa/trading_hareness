#!/usr/bin/env bash
# Runs 2026-10-14-alias-write-probe.py on the edge with the relay's own session and saves what it prints next to this file.
# It changes one contact's alias (备注名) by one character for about two seconds and puts it back; see the .py for the steps.
# usage: bash docs/work/probes/2026-10-14-alias-write-probe.sh <group id> <the contact's current alias, exactly>
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[ $# -eq 2 ] || { echo "usage: $0 <group id> <alias>" >&2; exit 2; }
out="$here/2026-10-14-alias-write-probe.out.json"
ssh -i /Users/papa/.ssh/feishu_relay_edge_ed25519 -o BatchMode=yes -o ConnectTimeout=10 root@47.114.113.152 \
  "/opt/supervisor/.venv/bin/python - $(printf '%q' "$1") $(printf '%q' "$2")" < "$here/2026-10-14-alias-write-probe.py" > "$out"
cat "$out"
