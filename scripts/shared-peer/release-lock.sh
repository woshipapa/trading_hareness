#!/usr/bin/env bash
# Mutual exclusion between an owner release and the session guard's heal pass.
#
#   release-lock.sh hold <label> [ttl_seconds]   take the lock; fails if another live release holds it
#   release-lock.sh release <label>              drop the lock when <label> holds it
#   release-lock.sh status                       print "free", "held <label> <expires_epoch>"
#                                                or "stale <label> <expires_epoch>"
#
# On 2026-10-08 the heal timer restarted containers in the middle of a release
# and turned one restart into a lock pile-up; the ad-hoc fix was to stop the
# timer, which stays stopped forever if the release dies half-way. The lock
# expires on its own, so a crashed release silences the guard for at most its
# TTL.
set -euo pipefail

STATE_DIR="${PEER_GUARD_STATE_DIR:-$HOME/.local/state/peer-session-guard}"
LOCK="$STATE_DIR/release.lock"
DEFAULT_TTL=1800
MAX_TTL=7200

usage() {
  sed -n '4,7p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 64
}

now() { date +%s; }

lock_expires=0
lock_label=""
read_lock() {
  [ -f "$LOCK" ] || return 1
  read -r lock_expires lock_label < "$LOCK" || true
  case "$lock_expires" in ''|*[!0-9]*) lock_expires=0 ;; esac
  lock_label=${lock_label:-unknown}
}

valid_label() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }

command=${1:-status}
case "$command" in
  hold)
    label=${2:-}; ttl=${3:-$DEFAULT_TTL}
    valid_label "$label" || { echo "release-lock: label must match [A-Za-z0-9._-]+" >&2; exit 64; }
    [[ "$ttl" =~ ^[0-9]+$ ]] && [ "$ttl" -ge 1 ] && [ "$ttl" -le "$MAX_TTL" ] \
      || { echo "release-lock: ttl must be 1..$MAX_TTL seconds" >&2; exit 64; }
    mkdir -p "$STATE_DIR"
    if read_lock && [ "$lock_expires" -gt "$(now)" ] && [ "$lock_label" != "$label" ]; then
      echo "release-lock: held by $lock_label until $lock_expires" >&2
      exit 2
    fi
    tmp=$(mktemp "$STATE_DIR/.release.lock.XXXXXX")
    printf '%s %s\n' "$(( $(now) + ttl ))" "$label" > "$tmp"
    mv -f "$tmp" "$LOCK"
    echo "held $label"
    ;;
  release)
    label=${2:-}
    valid_label "$label" || { echo "release-lock: label must match [A-Za-z0-9._-]+" >&2; exit 64; }
    if ! read_lock; then
      echo free
    elif [ "$lock_label" = "$label" ]; then
      rm -f "$LOCK"
      echo "released $label"
    else
      echo "release-lock: held by $lock_label, not $label; left in place" >&2
      exit 2
    fi
    ;;
  status)
    if ! read_lock; then
      echo free
    elif [ "$lock_expires" -gt "$(now)" ]; then
      echo "held $lock_label $lock_expires"
    else
      echo "stale $lock_label $lock_expires"
    fi
    ;;
  *) usage ;;
esac
