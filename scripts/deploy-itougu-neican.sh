#!/usr/bin/env bash
# Deploy the Itougu internal-reference relay onto the edge poller host.
# Default mode is read-only planning; --apply installs and restarts the service.
#
# Only the two relay modules and their static watermark asset are shipped. The
# systemd unit and the auth file stay as deployed. Both modules are
# contract-checked before install, so a
# revision that drops a helper the poller or the report needs cannot land.
set -euo pipefail

usage() {
  echo "usage: $0 <git-sha-or-tag> [--apply]" >&2
  exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
release_ref="$1"
apply=false
[[ "${2:-}" == "--apply" ]] && apply=true
[[ "${2:-}" == "" || "${2:-}" == "--apply" ]] || usage

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
edge_host="${ITOUGU_EDGE_HOST:-root@47.114.113.152}"
edge_key="${ITOUGU_EDGE_SSH_KEY:-$HOME/.ssh/feishu_relay_edge_ed25519}"
edge_root="${ITOUGU_EDGE_ROOT:-/opt/itougu-neican}"
github_repository="${ITOUGU_EDGE_GITHUB_REPOSITORY:-woshipapa/trading_hareness}"
github_branch="${ITOUGU_EDGE_GITHUB_BRANCH:-main}"
release_paths=(scripts/itougu_neican_relay.py scripts/itougu_public_article_relay.py scripts/itougu-momo-watermark.png scripts/test_itougu_poll_schedule.py scripts/deploy-itougu-neican.sh)

for command in git ssh; do command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }; done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }
[[ "$github_repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "invalid GitHub repository" >&2; exit 2; }
[[ "$github_branch" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "invalid GitHub branch" >&2; exit 2; }

release_sha="$(git -C "$source_root" rev-parse --verify "${release_ref}^{commit}")"
archive_url="https://codeload.github.com/$github_repository/tar.gz/$release_sha"

python3 -m unittest discover -s "$source_root/scripts" -p 'test_itougu_*.py' -q

for path in "${release_paths[@]}"; do
  git -C "$source_root" diff --quiet --ignore-submodules -- "$path" || {
    echo "refusing to deploy relay source with uncommitted changes: $path" >&2
    exit 1
  }
  git -C "$source_root" diff --cached --quiet --ignore-submodules -- "$path" || {
    echo "refusing to deploy staged but uncommitted relay source: $path" >&2
    exit 1
  }
done

github_sha="$(git -C "$source_root" ls-remote --exit-code origin "refs/heads/$github_branch" | awk 'NR == 1 {print $1}')"
[[ "$github_sha" == "$release_sha" ]] || {
  echo "refusing deploy: origin/$github_branch is $github_sha, requested release is $release_sha" >&2
  exit 1
}

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

printf 'release_sha=%s\nedge_host=%s\nedge_root=%s\ngithub_archive=%s\nservice=%s\n' \
  "$release_sha" "$edge_host" "$edge_root" "$archive_url" "itougu-neican.service"
if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply once the revision is reviewed and pushed"
  exit 0
fi

"${ssh_command[@]}" "$edge_host" "bash -s -- '$release_sha' '$archive_url' '$edge_root'" <<'REMOTE'
set -euo pipefail
release_sha="$1"; archive_url="$2"; edge_root="$3"

temp_dir="$(mktemp -d /tmp/itougu-neican-release.XXXXXX)"
case "$temp_dir" in /tmp/itougu-neican-release.*) ;; *) exit 1 ;; esac
trap 'rm -rf -- "$temp_dir"' EXIT

curl --fail --location --silent --show-error --retry 3 "$archive_url" \
  | tar -xz -C "$temp_dir" --strip-components=1 --wildcards \
      '*/scripts/itougu_neican_relay.py' '*/scripts/itougu_public_article_relay.py' '*/scripts/itougu-momo-watermark.png'
candidate="$temp_dir/scripts/itougu_neican_relay.py"
candidate_articles="$temp_dir/scripts/itougu_public_article_relay.py"
candidate_watermark="$temp_dir/scripts/itougu-momo-watermark.png"
test -f "$candidate"
test -f "$candidate_articles"
test -s "$candidate_watermark"

# Contract checks run against the candidate before anything is installed:
# the midday window is the change being shipped, and the report deployed
# alongside this relay imports these helpers from it.
PYTHONDONTWRITEBYTECODE=1 python3 - "$temp_dir/scripts" <<'PY'
import sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
import itougu_neican_relay as relay

missing = [n for n in ("load_headers", "itougu_call", "send_feishu", "feishu_token")
           if not hasattr(relay, n)]
if missing:
    raise SystemExit("candidate relay drops helpers used by the report: %s" % ", ".join(missing))
card = relay.build_product_card("contract", "body")
assert card.get("schema") == "2.0", "product relay must emit Card JSON 2.0"
assert card.get("config", {}).get("enable_forward_interaction") is False, "forward interaction must stay disabled"
assert card.get("body", {}).get("elements"), "product card body must contain an element"
assert relay.WATERMARK_IMAGE_FILE.name == "itougu-momo-watermark.png", "candidate must use the shipped watermark asset"

import itougu_public_article_relay as articles
missing = [n for n in ("poll_views", "trigger_from_push", "retry_pending")
           if not hasattr(articles, n)]
if missing:
    raise SystemExit("candidate article relay drops helpers: %s" % ", ".join(missing))

midday = datetime(2026, 9, 8, 12, 0, tzinfo=relay.CST)          # 周二午休
session = datetime(2026, 9, 8, 10, 0, tzinfo=relay.CST)
overnight = datetime(2026, 9, 8, 3, 0, tzinfo=relay.CST)
assert relay.poll_plan(midday, interval=15, off_hours_interval=600)[0], "midday break must poll"
assert relay.poll_plan(session, interval=15, off_hours_interval=600)[0], "session must poll"
assert not relay.poll_plan(overnight, interval=15, off_hours_interval=600)[0], "overnight must stay quiet"
print("relay contract ok: midday polling enabled")
PY

# Real credentials, real upstream, delivery suppressed.
set -a
# shellcheck disable=SC1091
. /etc/itougu-neican.env
set +a
PYTHONDONTWRITEBYTECODE=1 python3 "$candidate" --once --dry-run >/dev/null
echo "candidate dry-run ok"

for module in itougu_neican_relay.py itougu_public_article_relay.py itougu-momo-watermark.png; do
  if [[ -e "$edge_root/$module" ]]; then
    install -m 0600 "$edge_root/$module" "$edge_root/$module.before-$release_sha"
  fi
done
install -m 0755 "$candidate" "$edge_root/itougu_neican_relay.py"
install -m 0755 "$candidate_articles" "$edge_root/itougu_public_article_relay.py"
install -m 0644 "$candidate_watermark" "$edge_root/itougu-momo-watermark.png"
systemctl restart itougu-neican.service
sleep 3
systemctl is-active --quiet itougu-neican.service || { echo "relay failed to come back" >&2; exit 1; }

# Verify the running copy, not the candidate.
PYTHONDONTWRITEBYTECODE=1 python3 - "$edge_root" <<'PY'
import sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
import itougu_neican_relay as relay
import itougu_public_article_relay as articles
now = datetime(2026, 9, 8, 12, 30, tzinfo=relay.CST)
should_poll, sleep_seconds = relay.poll_plan(now, interval=15, off_hours_interval=600)
assert should_poll and sleep_seconds == 15, (should_poll, sleep_seconds)
assert callable(articles.poll_views)
print("installed relay polls the midday break every %.0fs" % sleep_seconds)
PY
journalctl -u itougu-neican.service -n 5 --no-pager -o cat
printf 'installed relay + article relay %s (backups: *.before-%s)\n' "$release_sha" "$release_sha"
REMOTE

printf 'edge relay deploy applied: %s\n' "$release_sha"
