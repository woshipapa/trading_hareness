#!/usr/bin/env bash
set -euo pipefail

: "${LONGHU_SSH_HOST:?Set LONGHU_SSH_HOST in the shell environment}"
: "${LONGHU_SSH_USER:?Set LONGHU_SSH_USER in the shell environment}"
: "${LONGHU_SSH_KEY_PATH:?Set LONGHU_SSH_KEY_PATH in the shell environment}"

local_port="${LONGHU_LOCAL_PORT:-15682}"
remote_port="${LONGHU_REMOTE_PORT:-15682}"
ssh_port="${LONGHU_SSH_PORT:-22}"

exec ssh -NT \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -i "${LONGHU_SSH_KEY_PATH}" \
  -p "${ssh_port}" \
  -L "127.0.0.1:${local_port}:127.0.0.1:${remote_port}" \
  "${LONGHU_SSH_USER}@${LONGHU_SSH_HOST}"
