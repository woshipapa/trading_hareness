#!/usr/bin/env bash
# Deploy the Itougu post-close performance report (daily + weekly) onto the edge.
# Default mode is read-only planning; --apply installs units and enables timers.
#
# The report reuses the credentials and the Feishu sender already deployed with
# itougu-neican, so this deploy never ships a token and never rewrites the
# running relay. It refuses to enable a timer whose preflight did not pass.
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
release_paths=(scripts/itougu_perf_report.py scripts/test_itougu_perf_report.py deploy/itougu-perf-report)

for command in git ssh; do command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }; done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }
[[ "$github_repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "invalid GitHub repository" >&2; exit 2; }
[[ "$github_branch" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "invalid GitHub branch" >&2; exit 2; }

release_sha="$(git -C "$source_root" rev-parse --verify "${release_ref}^{commit}")"
archive_url="https://codeload.github.com/$github_repository/tar.gz/$release_sha"

# The unit tests are the contract for the pairing and fail-closed rules; run
# them against the working tree before shipping the same files.
python3 -m unittest discover -s "$source_root/scripts" -p 'test_itougu_perf_report.py' -q

git -C "$source_root" diff --quiet --ignore-submodules -- "${release_paths[@]}" || {
  echo "refusing to deploy report source with uncommitted changes; commit them first" >&2
  exit 1
}
git -C "$source_root" diff --cached --quiet --ignore-submodules -- "${release_paths[@]}" || {
  echo "refusing to deploy staged but uncommitted report source" >&2
  exit 1
}

# GitHub is the release source of truth; a local-only commit must not reach the edge.
github_sha="$(git -C "$source_root" ls-remote --exit-code origin "refs/heads/$github_branch" | awk 'NR == 1 {print $1}')"
[[ "$github_sha" == "$release_sha" ]] || {
  echo "refusing deploy: origin/$github_branch is $github_sha, requested release is $release_sha" >&2
  exit 1
}

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

printf 'release_sha=%s\nedge_host=%s\nedge_root=%s\ngithub_archive=%s\nunits=%s\n' \
  "$release_sha" "$edge_host" "$edge_root" "$archive_url" \
  "itougu-perf-daily.timer itougu-perf-weekly.timer"
if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply once the revision is reviewed and pushed"
  exit 0
fi

"${ssh_command[@]}" "$edge_host" "bash -s -- '$release_sha' '$archive_url' '$edge_root'" <<'REMOTE'
set -euo pipefail
release_sha="$1"; archive_url="$2"; edge_root="$3"

temp_dir="$(mktemp -d /tmp/itougu-perf-release.XXXXXX)"
case "$temp_dir" in /tmp/itougu-perf-release.*) ;; *) exit 1 ;; esac
trap 'rm -rf -- "$temp_dir"' EXIT

# Stream the exact commit and materialize only the report subset.
curl --fail --location --silent --show-error --retry 3 "$archive_url" \
  | tar -xz -C "$temp_dir" --strip-components=1 --wildcards \
      '*/scripts/itougu_perf_report.py' '*/deploy/itougu-perf-report/*'
test -f "$temp_dir/scripts/itougu_perf_report.py"
test -f "$temp_dir/deploy/itougu-perf-report/itougu-perf-daily.timer"

# The report imports the deployed relay for credentials and Feishu delivery.
# Verify that surface before installing anything that a timer would run.
test -f "$edge_root/itougu_neican_relay.py"
PYTHONDONTWRITEBYTECODE=1 python3 - "$edge_root" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import itougu_neican_relay as relay
required = ("load_headers", "itougu_call", "send_feishu_many", "feishu_token")
missing = [name for name in required if not hasattr(relay, name)]
if missing:
    raise SystemExit("deployed relay is missing required helpers: %s" % ", ".join(missing))
print("relay helper surface ok")
PY

install -m 0755 "$temp_dir/scripts/itougu_perf_report.py" "$edge_root/itougu_perf_report.py"

# Seed the environment file once, reusing the relay's Feishu credentials that
# already live on this host. Secrets are never carried over the wire.
if [[ ! -f /etc/itougu-perf-report.env ]]; then
  install -m 0600 "$temp_dir/deploy/itougu-perf-report/itougu-perf-report.env.example" /etc/itougu-perf-report.env
  for key in FEISHU_APP_ID FEISHU_APP_SECRET; do
    value="$(sed -n "s/^${key}=//p" /etc/itougu-neican.env | head -1)"
    [[ -n "$value" ]] || { echo "cannot seed ${key}: /etc/itougu-neican.env has no value" >&2; exit 1; }
    sed -i "s|^${key}=.*|${key}=${value}|" /etc/itougu-perf-report.env
  done
  echo "created /etc/itougu-perf-report.env from the shipped example"
fi
chmod 600 /etc/itougu-perf-report.env
grep -q '^FEISHU_APP_ID=.\+' /etc/itougu-perf-report.env || { echo "FEISHU_APP_ID is empty in /etc/itougu-perf-report.env" >&2; exit 1; }
grep -q '^ITOUGU_PERF_TARGETS=.\+=.\+' /etc/itougu-perf-report.env || { echo "ITOUGU_PERF_TARGETS has no product=chat mapping" >&2; exit 1; }

install -m 0644 "$temp_dir/deploy/itougu-perf-report/itougu-perf-daily.service" /etc/systemd/system/itougu-perf-daily.service
install -m 0644 "$temp_dir/deploy/itougu-perf-report/itougu-perf-daily.timer" /etc/systemd/system/itougu-perf-daily.timer
install -m 0644 "$temp_dir/deploy/itougu-perf-report/itougu-perf-weekly.service" /etc/systemd/system/itougu-perf-weekly.service
install -m 0644 "$temp_dir/deploy/itougu-perf-report/itougu-perf-weekly.timer" /etc/systemd/system/itougu-perf-weekly.timer

# End-to-end preflight with the real credentials, upstream API and quote
# providers, but delivery suppressed. A timer is only enabled after it passes.
set -a
# shellcheck disable=SC1091
. /etc/itougu-perf-report.env
set +a
preflight_state="$(mktemp /tmp/itougu-perf-preflight.XXXXXX.json)"
trap 'rm -rf -- "$temp_dir" "$preflight_state"' EXIT
PYTHONDONTWRITEBYTECODE=1 ITOUGU_PERF_STATE_FILE="$preflight_state" \
  python3 "$edge_root/itougu_perf_report.py" --daily --dry-run --state-file "$preflight_state"

systemctl daemon-reload
systemctl enable --now itougu-perf-daily.timer itougu-perf-weekly.timer
systemctl list-timers --all --no-pager 'itougu-perf-*'
printf 'installed itougu perf report at %s\n' "$release_sha"
REMOTE

printf 'edge report deploy applied: %s\n' "$release_sha"
