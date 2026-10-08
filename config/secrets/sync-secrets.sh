#!/usr/bin/env bash
# sync-secrets.sh — 把本地凭据文件安全推到远端
# 用法: bash config/secrets/sync-secrets.sh {owner|edge|all}
#
# 前置: .env.owner / .env.edge 已填好值
# 安全: rsync over SSH，权限 600，不走明文日志
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-}"

# ── 远端坐标 ──────────────────────────────────────────────────────
# 从 .env.local 读 SSH 参数（复用隧道配置）
if [[ -f "$SCRIPT_DIR/.env.local" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$SCRIPT_DIR/.env.local"; set +a
fi

OWNER_HOST="${LONGHU_SSH_HOST:?需要 LONGHU_SSH_HOST}"
OWNER_PORT="${LONGHU_SSH_PORT:-3535}"
OWNER_USER="${LONGHU_SSH_USER:-stockpeer}"
OWNER_KEY="${LONGHU_SSH_KEY_PATH:?需要 LONGHU_SSH_KEY_PATH}"

# edge 是另一台机器（47.114），root@22，用独立密钥
EDGE_HOST="${EDGE_SSH_HOST:-47.114.113.152}"
EDGE_PORT="${EDGE_SSH_PORT:-22}"
EDGE_USER="${EDGE_SSH_USER:-root}"
EDGE_KEY="${EDGE_SSH_KEY_PATH:-${HOME}/.ssh/feishu_relay_edge_ed25519}"

# ── 远端目标路径（用户 home 下，不需要 root）──────────────────────
OWNER_REMOTE_DIR="${OWNER_REMOTE_SECRETS_DIR:-.secrets/owner}"
EDGE_REMOTE_DIR="${EDGE_REMOTE_SECRETS_DIR:-.secrets/edge}"

sync_file() {
  local src="$1" host="$2" port="$3" user="$4" key="$5" remote_dir="$6"
  local fname
  fname="$(basename "$src")"

  if [[ ! -f "$src" ]]; then
    echo "⚠️  跳过：$src 不存在" >&2
    return 1
  fi

  echo "→ 推 $fname → $user@$host:$remote_dir/"
  # 先确保远端目录存在且权限 700
  ssh -o StrictHostKeyChecking=accept-new -p "$port" -i "$key" \
    "$user@$host" "mkdir -p '$remote_dir' && chmod 700 '$remote_dir'"

  # rsync 推文件，然后 ssh chmod 600（兼容老版 rsync 不支持 --chmod）
  rsync -e "ssh -o StrictHostKeyChecking=accept-new -p $port -i $key" \
    "$src" "$user@$host:$remote_dir/$fname"
  ssh -o StrictHostKeyChecking=accept-new -p "$port" -i "$key" \
    "$user@$host" "chmod 600 '$remote_dir/$fname'"

  echo "   ✅ $fname 已推到 $host:$remote_dir/$fname"
}

sync_owner() {
  local env_file="$SCRIPT_DIR/.env.owner"
  sync_file "$env_file" "$OWNER_HOST" "$OWNER_PORT" "$OWNER_USER" "$OWNER_KEY" "$OWNER_REMOTE_DIR"
  # 证书
  if [[ -d "$SCRIPT_DIR/certs" ]]; then
    for cert in "$SCRIPT_DIR/certs"/*; do
      [[ -f "$cert" ]] && sync_file "$cert" "$OWNER_HOST" "$OWNER_PORT" "$OWNER_USER" "$OWNER_KEY" "$OWNER_REMOTE_DIR/certs"
    done
  fi
}

sync_edge() {
  local env_file="$SCRIPT_DIR/.env.edge"
  sync_file "$env_file" "$EDGE_HOST" "$EDGE_PORT" "$EDGE_USER" "$EDGE_KEY" "$EDGE_REMOTE_DIR"
}

case "$TARGET" in
  owner) sync_owner ;;
  edge)  sync_edge ;;
  all)   sync_owner; sync_edge ;;
  *)
    echo "用法: $0 {owner|edge|all}" >&2
    echo "" >&2
    echo "  owner  推 .env.owner + certs/ → 47owner ($OWNER_REMOTE_DIR)" >&2
    echo "  edge   推 .env.edge → 47edge ($EDGE_REMOTE_DIR)" >&2
    echo "  all    两个都推" >&2
    exit 1
    ;;
esac

echo ""
echo "推完后："
echo "  1. 在远端建符号链接（只需做一次）："
echo "     owner: ln -sf ~/$OWNER_REMOTE_DIR/.env.owner ~/compose所在目录/.env"
echo "     edge:  ln -sf ~/$EDGE_REMOTE_DIR/.env.edge  ~/compose所在目录/.env"
echo "  2. 重启服务："
echo "     owner: ssh -p $OWNER_PORT $OWNER_USER@$OWNER_HOST 'cd ~/shared-peer && docker compose restart quant-research'"
echo "     edge:  ssh -p $EDGE_PORT $EDGE_USER@$EDGE_HOST 'cd ~/feishu-relay && docker compose restart'"
