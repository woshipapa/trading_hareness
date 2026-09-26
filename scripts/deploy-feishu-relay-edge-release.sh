#!/usr/bin/env bash
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../feishu-relay/scripts/edge" && pwd)/deploy-feishu-relay-edge-release.sh" "$@"
