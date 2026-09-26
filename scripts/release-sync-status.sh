#!/usr/bin/env bash
# Read-only release inventory of both 47 hosts against one expected Git SHA.
#
# It changes nothing on either host: every remote command is a curl against a
# loopback /health, a readlink, a grep of non-secret release keys, a systemctl
# query or a single SELECT of the Alembic revision.  Run it before a sync to
# see the drift and after a sync to prove both hosts converged
# (docs/RELEASE_SYNC_47.md).
#
#   scripts/release-sync-status.sh                 # expect origin/main
#   scripts/release-sync-status.sh --sha <sha>     # expect a specific commit
#   scripts/release-sync-status.sh --skip-owner    # edge only (or --skip-edge)
#
# Exit status: 0 when every check passes, 1 when any check fails, 2 on usage
# or local setup errors.
set -euo pipefail

usage() {
  echo "usage: $0 [--sha <git-sha>] [--skip-edge] [--skip-owner]" >&2
  exit 2
}

expected_ref=""
skip_edge=false
skip_owner=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) [[ -n "${2:-}" ]] || usage; expected_ref="$2"; shift 2 ;;
    --skip-edge) skip_edge=true; shift ;;
    --skip-owner) skip_owner=true; shift ;;
    *) usage ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for command in git ssh python3; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 2; }
done
if [[ -z "$expected_ref" ]]; then
  git -C "$repo_root" fetch --quiet origin main
  expected_ref="origin/main"
fi
expected_sha="$(git -C "$repo_root" rev-parse --verify "${expected_ref}^{commit}")"

edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_key="${RELAY_EDGE_SSH_KEY:-$HOME/.ssh/feishu_relay_edge_ed25519}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
edge_runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
owner_host="${OWNER_PEER_HOST:-stockpeer@47.110.79.189}"
owner_port="${OWNER_PEER_PORT:-3535}"
owner_key="${OWNER_PEER_SSH_KEY:-$HOME/.ssh/stockpeer_ed25519}"
ssh_options=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15)

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/release-sync-status.XXXXXX")"
trap 'rm -rf -- "$work_dir"' EXIT

# The Alembic heads this commit expects, read from the migration files of the
# expected commit itself (no database or Alembic install needed locally).
git -C "$repo_root" archive "$expected_sha" quant-service/migrations/versions | tar -x -C "$work_dir"
python3 - "$work_dir/quant-service/migrations/versions" > "$work_dir/expected_heads" <<'PY'
import pathlib
import re
import sys

revisions, parents = set(), set()
for path in pathlib.Path(sys.argv[1]).glob("*.py"):
    text = path.read_text(encoding="utf-8")
    revision = re.search(r'^revision\s*=\s*"([^"]+)"', text, re.M)
    down = re.search(r"^down_revision\s*=\s*(.+)$", text, re.M)
    if not revision:
        continue
    revisions.add(revision.group(1))
    if down:
        parents.update(re.findall(r'"([^"]+)"', down.group(1)))
print("\n".join(sorted(revisions - parents)))
PY

if [[ "$skip_edge" != true ]]; then
  if [[ -r "$edge_key" ]]; then
    ssh -i "$edge_key" "${ssh_options[@]}" "$edge_host" bash -s -- "$edge_dir" "$edge_runtime_env" \
      > "$work_dir/edge" 2> "$work_dir/edge.err" <<'REMOTE_EDGE' || echo "@ssh_error $?" >> "$work_dir/edge"
edge_dir="$1"; runtime_env="$2"
printf '@adapter_health %s\n' "$(curl -fsS -m 8 http://127.0.0.1:18300/health 2>/dev/null | tr -d '\n')"
printf '@bridge_health %s\n' "$(curl -fsS -m 8 http://127.0.0.1:8090/health 2>/dev/null | tr -d '\n')"
printf '@hotfix_current %s\n' "$(readlink "$edge_dir/hotfix/current" 2>/dev/null || true)"
grep -E '^(FEISHU_ADAPTER_IMAGE|FEISHU_ADAPTER_HOTFIX_ENABLED|APP_GIT_SHA|APP_RELEASE)=' "$runtime_env" 2>/dev/null \
  | sed 's/^/@runtime_env /'
printf '@retired_quant %s %s\n' "$(systemctl is-active quant-intraday-edge.service 2>/dev/null || true)" \
  "$(systemctl is-enabled quant-intraday-edge.service 2>/dev/null || true)"
REMOTE_EDGE
  else
    echo "@ssh_error key-unreadable:$edge_key" > "$work_dir/edge"
  fi
fi

if [[ "$skip_owner" != true ]]; then
  if [[ -r "$owner_key" ]]; then
    ssh -i "$owner_key" -p "$owner_port" "${ssh_options[@]}" "$owner_host" bash -s \
      > "$work_dir/owner" 2> "$work_dir/owner.err" <<'REMOTE_OWNER' || echo "@ssh_error $?" >> "$work_dir/owner"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DOCKER_HOST="${DOCKER_HOST:-unix://$XDG_RUNTIME_DIR/docker.sock}"
main=trading-hareness-peer-quant-research-1
printf '@release_link %s\n' "$(readlink -f "$HOME/trading_hareness" 2>/dev/null || true)"
printf '@main_health %s\n' "$(curl -fsS -m 8 http://127.0.0.1:15682/health 2>/dev/null | tr -d '\n')"
printf '@scheduler_health %s\n' "$(curl -fsS -m 8 http://127.0.0.1:15683/health 2>/dev/null | tr -d '\n')"
printf '@main_profile %s\n' "$(docker inspect "$main" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | sed -n 's/^QUANT_RUNTIME_PROFILE=//p' | head -1)"
printf '@alembic %s\n' "$(docker exec "$main" python -c '
import psycopg
with psycopg.connect() as connection:
    print(",".join(sorted(row[0] for row in connection.execute("SELECT version_num FROM quant.alembic_version"))))
' 2>/dev/null | tr -d '\n')"
printf '@intraday_secrets %s\n' "$(test -s "$HOME/trading_hareness/deploy/shared-peer/intraday-secrets.env" && echo present || echo missing)"
REMOTE_OWNER
  else
    echo "@ssh_error key-unreadable:$owner_key" > "$work_dir/owner"
  fi
fi

python3 - "$expected_sha" "$work_dir" "$skip_edge" "$skip_owner" <<'PY'
import json
import pathlib
import sys

expected, work, skip_edge, skip_owner = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3] == "true", sys.argv[4] == "true"
heads = [line for line in (work / "expected_heads").read_text().split() if line]
rows, failures = [], 0


def sections(name):
    path = work / name
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("@"):
            key, _, value = line[1:].partition(" ")
            result.setdefault(key, []).append(value.strip())
    return result


def first(data, key):
    values = data.get(key) or [""]
    return values[0]


def health(raw):
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def check(host, item, observed, ok, expected_text):
    global failures
    rows.append((host, item, observed if observed not in (None, "") else "<none>", "PASS" if ok else "FAIL", expected_text))
    if not ok:
        failures += 1


def sha_matches(value):
    value = str(value or "").lower()
    return len(value) >= 7 and expected.lower().startswith(value)


if not skip_edge:
    edge = sections("edge")
    if "ssh_error" in edge:
        check("edge", "ssh", first(edge, "ssh_error"), False, "reachable")
    else:
        adapter = health(first(edge, "adapter_health"))
        build = (adapter or {}).get("build") or {}
        check("edge", "adapter /health", (adapter or {}).get("status"), (adapter or {}).get("status") == "ok", "ok")
        check("edge", "adapter git_sha", build.get("git_sha"), sha_matches(build.get("git_sha")), expected[:12])
        check("edge", "adapter release", build.get("release"), not str(build.get("release") or "").startswith("hotfix"), "pinned (not hotfix*)")
        check("edge", "adapter runtime_source", (adapter or {}).get("runtime_source") or "image",
              (adapter or {}).get("runtime_source") != "source-overlay", "not source-overlay")
        env = dict(item.split("=", 1) for item in edge.get("runtime_env", []) if "=" in item)
        check("edge", "runtime.env image", env.get("FEISHU_ADAPTER_IMAGE"), expected[:12] in str(env.get("FEISHU_ADAPTER_IMAGE") or ""),
              f"...:{expected[:12]}...")
        check("edge", "runtime.env overlay", env.get("FEISHU_ADAPTER_HOTFIX_ENABLED") or "unset",
              str(env.get("FEISHU_ADAPTER_HOTFIX_ENABLED") or "false").lower() != "true", "false/unset")
        bridge = health(first(edge, "bridge_health"))
        bridge_release = str((bridge or {}).get("release") or "")
        check("edge", "larkagentx bridge", (bridge or {}).get("status"), (bridge or {}).get("status") == "ok", "ok")
        check("edge", "bridge release", bridge_release or "<none>",
              expected[:12] in bridge_release and "-dirty" not in bridge_release, f"overlay built from clean {expected[:12]}")
        active, enabled = (first(edge, "retired_quant") + " ").split(" ", 1)
        check("edge", "retired quant-intraday-edge", f"{active}/{enabled.strip()}",
              active != "active" and enabled.strip() != "enabled", "inactive/disabled")

if not skip_owner:
    owner = sections("owner")
    if "ssh_error" in owner:
        check("owner", "ssh", first(owner, "ssh_error"), False, "reachable")
    else:
        link = first(owner, "release_link")
        check("owner", "~/trading_hareness", link, bool(link), "release symlink")
        for key, label in (("main_health", "quant-research :15682"), ("scheduler_health", "scheduler :15683")):
            payload = health(first(owner, key))
            build = (payload or {}).get("build") or {}
            check("owner", f"{label} /health", (payload or {}).get("status"), (payload or {}).get("status") == "ok", "ok")
            check("owner", f"{label} git_sha", build.get("git_sha"), sha_matches(build.get("git_sha")), expected[:12])
        check("owner", "main runtime profile", first(owner, "main_profile"), first(owner, "main_profile") == "intraday_edge",
              "intraday_edge")
        revisions = [value for value in first(owner, "alembic").split(",") if value]
        check("owner", "alembic revision", ",".join(revisions), sorted(revisions) == sorted(heads), ",".join(heads))
        check("owner", "intraday-secrets.env", first(owner, "intraday_secrets"), first(owner, "intraday_secrets") == "present",
              "present")

width = [max(len(str(row[index])) for row in rows + [("host", "check", "observed", "result", "expected")]) for index in range(5)]
header = ("host", "check", "observed", "result", "expected")
for row in [header] + rows:
    print("  ".join(str(value).ljust(width[index]) for index, value in enumerate(row)))
print(f"\nexpected sha: {expected}")
print(f"{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(0 if failures == 0 else 1)
PY
