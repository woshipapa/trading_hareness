#!/usr/bin/env bash
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../feishu-relay/scripts/sources/itougu" && pwd)/deploy-itougu-perf-report.sh" "$@"
