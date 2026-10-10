#!/usr/bin/env bash
# Run the TDX route probe inside the owner quant-research container, over ssh; it writes nothing on the owner.
#
# The container's image holds the app package that scripts/probe-tdx-routes.py imports, so only the driver travels: with
# the host candidate files embedded it goes over ssh stdin to `bash -s`, which sets up the owner's rootless Docker and
# pipes the driver into `docker exec -i <container> python -B -` (no bytecode is written). The container and Docker
# are reached as scripts/release-sync-status.sh and scripts/tdx-promote.py reach them.
set -euo pipefail
missing=()
for name in LONGHU_SSH_HOST LONGHU_SSH_PORT LONGHU_SSH_USER LONGHU_SSH_KEY_PATH; do
  [[ -n ${!name:-} ]] || missing+=("$name")
done
if ((${#missing[@]})); then printf '%s\n' "${missing[@]}" >&2; exit 2; fi
ROOT=$(cd "$(dirname "$0")/.." && pwd)
args=(--hosts-file embedded --egress owner)
output=
while (($#)); do
  case $1 in
    --output)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
      output=$2; shift 2;;
    --profile|--require|--min-usable-hosts|--samples|--interval|--hist-date)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
      args+=("$1" "$2"); shift 2;;
    *) echo "unsupported argument: $1" >&2; exit 2;;
  esac
done
[[ -n $output ]] || { echo "missing required --output" >&2; exit 2; }
tmpdir=$(mktemp -d); script=$tmpdir/owner.sh; err=$tmpdir/stderr; trap 'rm -rf "$tmpdir"' EXIT
python3 - "$ROOT" "$script" "${args[@]}" <<'PY'
import hashlib, pathlib, shlex, sys
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2]); args = sys.argv[3:]
candidates = "\n".join((root / name).read_text(encoding="utf-8") for name in (
    "scripts/data/tdx_host_candidates.txt", "scripts/data/tdx_host_candidates_other.txt"))
driver = (root / "scripts/probe-tdx-routes.py").read_text(encoding="utf-8")
payload = "EMBEDDED_HOSTS_TEXT = " + repr(candidates) + "\n" + driver + "\n"
end = "TDX_ROUTE_PROBE_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]    # derived from the payload it ends
out.write_text(
    'export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"\n'
    'export DOCKER_HOST="${DOCKER_HOST:-unix://$XDG_RUNTIME_DIR/docker.sock}"\n'
    f"docker exec -i trading-hareness-peer-quant-research-1 python -B - {shlex.join(args)} <<'{end}'\n{payload}{end}\n",
    encoding="utf-8")
PY
ssh_args=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=20
  -i "$LONGHU_SSH_KEY_PATH" -p "$LONGHU_SSH_PORT" "$LONGHU_SSH_USER@$LONGHU_SSH_HOST" bash -s)
status=0
ssh "${ssh_args[@]}" < "$script" > "$tmpdir/output" 2>"$err" || status=$?
# A sweep below its threshold exits 2 but still printed its matrix: keep it, it is the evidence.
if [[ -s $tmpdir/output ]]; then mv "$tmpdir/output" "$output"; fi
# The remote summary and any ssh error go to stderr with the connection values replaced literally.
python3 -c '
import os, sys
text = sys.stdin.read()
key = os.environ["LONGHU_SSH_KEY_PATH"]
for value, label in ((os.path.expanduser(key), "[ssh-key]"), (key, "[ssh-key]"),
                     (os.environ["LONGHU_SSH_HOST"], "[ssh-host]"), (os.environ["LONGHU_SSH_USER"], "[ssh-user]")):
    text = text.replace(value, label)
sys.stderr.write(text)
' < "$err"
exit "$status"
