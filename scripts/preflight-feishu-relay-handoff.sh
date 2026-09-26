#!/usr/bin/env bash
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../feishu-relay/scripts/edge" && pwd)/preflight-feishu-relay-handoff.sh" "$@"
