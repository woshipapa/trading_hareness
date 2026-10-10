#!/usr/bin/env bash
set -euo pipefail
missing=()
for name in LONGHU_SSH_HOST LONGHU_SSH_PORT LONGHU_SSH_USER LONGHU_SSH_KEY_PATH; do
  [[ -n ${!name:-} ]] || missing+=("$name")
done
if ((${#missing[@]})); then printf '%s\n' "${missing[@]}" >&2; exit 2; fi
ROOT=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp); err=$(mktemp); trap 'rm -f "$tmp" "$err"' EXIT
python3 - "$ROOT" "$tmp" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
protocol = (root / "quant-service/app/datasources/sources/tdx_protocol.py").read_text(encoding="utf-8")
candidates = (root / "scripts/data/tdx_host_candidates.txt").read_text(encoding="utf-8")
driver = (root / "scripts/probe-tdx-routes.py").read_text(encoding="utf-8")
out.write_text(protocol + "\nEMBEDDED_HOSTS_TEXT = " + repr(candidates) + "\n" + driver + "\n", encoding="utf-8")
PY
args=(--hosts-file embedded --egress owner)
while (($#)); do
  case $1 in
    --profile|--require|--min-usable-hosts|--samples|--interval|--hist-date)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
      args+=("$1" "$2"); shift 2;;
    *) echo "unsupported argument: $1" >&2; exit 2;;
  esac
done
ssh_args=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=20
  -i "$LONGHU_SSH_KEY_PATH" -p "$LONGHU_SSH_PORT" "$LONGHU_SSH_USER@$LONGHU_SSH_HOST" 'python3 -I -')
if ! ssh "${ssh_args[@]}" "${args[@]}" < "$tmp" > /dev/stdout 2>"$err"; then
  sed -e "s/${LONGHU_SSH_HOST}/[ssh-host]/g" -e "s/${LONGHU_SSH_USER}/[ssh-user]/g" -e "s#${LONGHU_SSH_KEY_PATH}#[ssh-key]#g" "$err" >&2
  exit 1
fi
if [[ -s $err ]]; then
  sed -e "s/${LONGHU_SSH_HOST}/[ssh-host]/g" -e "s/${LONGHU_SSH_USER}/[ssh-user]/g" -e "s#${LONGHU_SSH_KEY_PATH}#[ssh-key]#g" "$err" >&2
fi
