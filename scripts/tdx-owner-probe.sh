#!/usr/bin/env bash
# Run the TDX route probe from the owner egress over ssh stdin; it writes nothing on the owner.
set -euo pipefail
missing=()
for name in LONGHU_SSH_HOST LONGHU_SSH_PORT LONGHU_SSH_USER LONGHU_SSH_KEY_PATH; do
  [[ -n ${!name:-} ]] || missing+=("$name")
done
if ((${#missing[@]})); then printf '%s\n' "${missing[@]}" >&2; exit 2; fi
ROOT=$(cd "$(dirname "$0")/.." && pwd)
tmpdir=$(mktemp -d); tmp=$tmpdir/payload.py; err=$tmpdir/stderr; trap 'rm -rf "$tmpdir"' EXIT
python3 - "$ROOT" "$tmp" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
protocol = (root / "quant-service/app/datasources/sources/tdx_protocol.py").read_text(encoding="utf-8")
candidates = "\n".join((root / name).read_text(encoding="utf-8") for name in (
    "scripts/data/tdx_host_candidates.txt", "scripts/data/tdx_host_candidates_other.txt"))
driver = (root / "scripts/probe-tdx-routes.py").read_text(encoding="utf-8")
out.write_text(protocol + "\nEMBEDDED_HOSTS_TEXT = " + repr(candidates) + "\n" + driver + "\n", encoding="utf-8")
PY
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
ssh_args=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=20
  -i "$LONGHU_SSH_KEY_PATH" -p "$LONGHU_SSH_PORT" "$LONGHU_SSH_USER@$LONGHU_SSH_HOST" 'python3 -I -')
status=0
ssh "${ssh_args[@]}" "${args[@]}" < "$tmp" > "$tmpdir/output" 2>"$err" || status=$?
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
