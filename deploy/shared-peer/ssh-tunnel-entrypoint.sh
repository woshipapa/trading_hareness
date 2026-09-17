#!/bin/sh
set -eu

: "${PEER_SSH_HOST:?PEER_SSH_HOST is required}"
: "${PEER_SSH_PORT:?PEER_SSH_PORT is required}"
: "${PEER_SSH_USER:?PEER_SSH_USER is required}"
: "${REMOTE_DB_PORT:?REMOTE_DB_PORT is required}"
: "${REMOTE_API_PORT:?REMOTE_API_PORT is required}"

test -r /run/secrets/peer_ssh_key
test -r /run/secrets/known_hosts
install -m 0600 /run/secrets/peer_ssh_key /tmp/peer_ssh_key

# PEER_SSH_HOST is sometimes a routing-internal address that moves
# independently of known_hosts' stable public identity; PEER_SSH_HOST_KEY_ALIAS
# tells ssh which known_hosts entry actually applies. Optional: omitted
# entirely when unset, so PEER_SSH_HOST being the stable address is unaffected.
set -- -NT \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/run/secrets/known_hosts
if [ -n "${PEER_SSH_HOST_KEY_ALIAS:-}" ]; then
  set -- "$@" -o "HostKeyAlias=${PEER_SSH_HOST_KEY_ALIAS}"
fi
exec ssh "$@" \
  -i /tmp/peer_ssh_key \
  -p "${PEER_SSH_PORT}" \
  -L "127.0.0.1:5432:127.0.0.1:${REMOTE_DB_PORT}" \
  -L "127.0.0.1:5681:127.0.0.1:${REMOTE_API_PORT}" \
  "${PEER_SSH_USER}@${PEER_SSH_HOST}"
