#!/usr/bin/env bash
# Verify the pinned upstream LarkAgentX files on 47edge still match the digests
# recorded in feishu-relay/larkx-pin.sha256. A drift means the bridge's
# overrides may no longer line up with upstream and must be re-reviewed before
# shipping; this exits non-zero so the hotfix path can fail closed.
#
#   bash feishu-relay/scripts/edge/verify-larkx-pin.sh            # check
#   bash feishu-relay/scripts/edge/verify-larkx-pin.sh --update   # re-record
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
pin_file="$repo_root/feishu-relay/larkx-pin.sha256"
edge_host="${RELAY_EDGE_HOST:-root@116.62.30.225}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/relay_release_ed25519}"
source_dir="${LARKX_SOURCE_DIR:-/opt/larkagentx/source}"
ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

[[ -r "$edge_key" ]] || { echo "edge SSH key not readable: $edge_key" >&2; exit 2; }

# The tracked files are exactly the non-comment lines of the pin file.
files_list=$(grep -vE '^\s*#' "$pin_file" | awk '{print $2}' | grep -v '^$' | tr '\n' ' ')
file_count=$(echo "$files_list" | wc -w | tr -d ' ')
[[ "$file_count" -gt 0 ]] || { echo "no files listed in $pin_file" >&2; exit 2; }

remote_digests=$("${ssh_command[@]}" "$edge_host" "cd '$source_dir' && for f in $files_list; do sha256sum \"\$f\" 2>/dev/null || echo 'MISSING  '\$f; done")

if [[ "${1:-}" == "--update" ]]; then
	{
		sed -n '/^\s*#/p' "$pin_file"
		echo "$remote_digests"
	} > "$pin_file.tmp"
	mv "$pin_file.tmp" "$pin_file"
	echo "updated $pin_file from $edge_host:$source_dir"
	exit 0
fi

drift=0
while read -r expected_hash path; do
	[[ -z "$path" ]] && continue
	actual=$(echo "$remote_digests" | awk -v p="$path" '$2==p{print $1}')
	if [[ -z "$actual" ]]; then
		echo "MISSING on edge: $path" >&2; drift=1
	elif [[ "$actual" != "$expected_hash" ]]; then
		echo "DRIFT: $path (expected ${expected_hash:0:12}…, got ${actual:0:12}…)" >&2; drift=1
	fi
done < <(grep -vE '^\s*#' "$pin_file")

if [[ "$drift" != 0 ]]; then
	echo "upstream larkx pin drift detected — re-review bridge overrides (build_ws_url / _gateway_post / decode cmd gating / pull_history / QrLogin), then --update" >&2
	exit 1
fi
echo "larkx pin verified: $file_count files match"
