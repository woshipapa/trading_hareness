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
OWNER_PORT="${LONGHU_SSH_PORT:-22}"
OWNER_USER="${LONGHU_SSH_USER:-stockpeer}"
OWNER_KEY="${LONGHU_SSH_KEY_PATH:?需要 LONGHU_SSH_KEY_PATH}"

# edge 是另一台机器（47.114），root@22，用独立密钥
EDGE_HOST="${EDGE_SSH_HOST:-116.62.30.225}"
EDGE_PORT="${EDGE_SSH_PORT:-22}"
EDGE_USER="${EDGE_SSH_USER:-root}"
EDGE_KEY="${EDGE_SSH_KEY_PATH:-${HOME}/.ssh/relay_release_ed25519}"

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
  # 发布元数据由 deploy-code-only.sh 写在远端文件里，推送时保留，不能被覆盖
  local keep merged tmpdir
  keep="$(ssh -o StrictHostKeyChecking=accept-new -p "$OWNER_PORT" -i "$OWNER_KEY" "$OWNER_USER@$OWNER_HOST" \
    "grep -E '^PEER_(APP_GIT_SHA|APP_RELEASE|APP_BUILD_CREATED_AT|EXPECTED_RELEASE)=' ~/$OWNER_REMOTE_DIR/.env.owner || true")"
  tmpdir="$(mktemp -d)"; merged="$tmpdir/.env.owner"; umask 077
  grep -vE '^PEER_(APP_GIT_SHA|APP_RELEASE|APP_BUILD_CREATED_AT|EXPECTED_RELEASE)=' "$env_file" > "$merged"
  [[ -n "$keep" ]] && printf '%s\n' "$keep" >> "$merged"
  sync_file "$merged" "$OWNER_HOST" "$OWNER_PORT" "$OWNER_USER" "$OWNER_KEY" "$OWNER_REMOTE_DIR"
  rm -rf "$tmpdir"
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

# paper-edge: 论文数据源采集器的最小凭据子集 → 47edge:/etc/paper-sources-edge.env (root 0600)
# 经 ssh 标准输入传输（值不进命令行参数/进程列表），先写 .new 再原子 mv；只回显键名。
sync_paper_edge_s2() {
  # S2 keys are plain files next to the code (paper_kb/.s2_api_keys, .s2_api_key), not .env.local entries.
  local src="${PAPER_KB_DIR:-$HOME/codebase/literature_maps/paper_kb}" remote_dir="/opt/paper-arxiv-edge/s2" f sent=0
  ssh -o StrictHostKeyChecking=accept-new -p "$EDGE_PORT" -i "$EDGE_KEY" "$EDGE_USER@$EDGE_HOST" "install -d -m 0750 '$remote_dir'"
  for f in .s2_api_keys .s2_api_key; do
    [[ -s "$src/$f" ]] || continue
    ssh -o StrictHostKeyChecking=accept-new -p "$EDGE_PORT" -i "$EDGE_KEY" "$EDGE_USER@$EDGE_HOST" \
      "umask 077 && cat > '$remote_dir/$f.new' && chown root:root '$remote_dir/$f.new' && chmod 600 '$remote_dir/$f.new' && mv -f '$remote_dir/$f.new' '$remote_dir/$f'" \
      < "$src/$f"
    sent=$((sent+1))
  done
  [[ $sent -gt 0 ]] || { echo "⚠️  $src 下没有 .s2_api_keys/.s2_api_key" >&2; return 1; }
  echo "   ✅ 远端文件、权限与行数（不显示值）:"
  ssh -o StrictHostKeyChecking=accept-new -p "$EDGE_PORT" -i "$EDGE_KEY" "$EDGE_USER@$EDGE_HOST" \
    "for f in '$remote_dir'/.s2_api_key*; do [ -f \"\$f\" ] && stat -c '   %U:%G %a %n' \"\$f\" && printf '   lines: ' && wc -l < \"\$f\"; done"
}

sync_paper_edge() {
  local env_file="$SCRIPT_DIR/.env.paper-edge" remote="/etc/paper-sources-edge.env"
  if [[ ! -f "$env_file" ]]; then
    echo "⚠️  缺少 $env_file，先运行: python3 config/secrets/env-split.py --paper-edge" >&2
    return 1
  fi
  echo "→ 推 $(basename "$env_file") → $EDGE_USER@$EDGE_HOST:$remote"
  ssh -o StrictHostKeyChecking=accept-new -p "$EDGE_PORT" -i "$EDGE_KEY" "$EDGE_USER@$EDGE_HOST" \
    "umask 077 && cat > '$remote.new' && chown root:root '$remote.new' && chmod 600 '$remote.new' && mv -f '$remote.new' '$remote'" \
    < "$env_file"
  echo "   ✅ 远端权限与键名（不显示值）:"
  ssh -o StrictHostKeyChecking=accept-new -p "$EDGE_PORT" -i "$EDGE_KEY" "$EDGE_USER@$EDGE_HOST" \
    "stat -c '   %U:%G %a %n' '$remote'; grep -oE '^[A-Z_][A-Z0-9_]*=' '$remote' | tr -d '=' | sed 's/^/   key: /'"
}

case "$TARGET" in
  owner) sync_owner ;;
  edge)  sync_edge ;;
  paper-edge) sync_paper_edge ;;
  paper-edge-s2) sync_paper_edge_s2 ;;
  all)   sync_owner; sync_edge ;;
  *)
    echo "用法: $0 {owner|edge|paper-edge|paper-edge-s2|all}" >&2
    echo "" >&2
    echo "  owner  推 .env.owner + certs/ → 47owner ($OWNER_REMOTE_DIR)" >&2
    echo "  edge   推 .env.edge → 47edge ($EDGE_REMOTE_DIR)" >&2
    echo "  paper-edge 推 .env.paper-edge → 47edge:/etc/paper-sources-edge.env（不包含在 all 里）" >&2
    echo "  all    owner + edge" >&2
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
