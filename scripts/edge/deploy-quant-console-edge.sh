#!/usr/bin/env bash
# 把 quant 控制台（frontend/，归 quant-research 组件）作为**独立发布单元**发到 edge。
#
# 为什么要独立：这个 SPA 的源码属 quant-research，却一直被打包进 feishu-relay 的
# 原子发布目录（``hotfix/current/quant-frontend-dist``）。后果有两个方向：
#
#   * 一次只为 xhs 或分析师改动的中继热部署，会顺带重新构建并发布 quant 控制台；
#   * quant 这边改完前端，必须等一次中继发布才生效。
#
# 现在它有自己的 ``hotfix/quant-console/{releases,current}``。沿用中继那套安全动作：
# 预检、原子换 symlink、保留旧版可回滚、失败自动还原。它只写 quant-console 这棵树，
# 碰不到中继的 releases；中继的脚本也不再碰它。
#
# ``./hotfix`` 本来就整体挂进容器，所以不需要新增 compose 卷，只改
# ``QUANT_FRONTEND_DIST`` 指向这里。
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: deploy-quant-console-edge.sh [--apply] [--rollback <release-id>] [--list]
  默认 dry run：只构建与预检，不上传、不切换。
EOF
  exit 2
}

apply=false
rollback_id=""
list_releases=false
while (($#)); do
  case "$1" in
    --apply) apply=true; shift ;;
    --rollback) [[ -n "${2:-}" ]] || usage; rollback_id="$2"; shift 2 ;;
    --list) list_releases=true; shift ;;
    *) usage ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
hotfix_root="${RELAY_EDGE_HOTFIX_ROOT:-$edge_dir/hotfix}"
console_root="${QUANT_CONSOLE_EDGE_ROOT:-$hotfix_root/quant-console}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
retain_releases="${QUANT_CONSOLE_RETAIN:-5}"

[[ "$rollback_id" == "" || ( "$rollback_id" =~ ^console-[A-Za-z0-9][A-Za-z0-9._-]*$ && "$rollback_id" != *..* ) ]] || {
  echo "rollback release ID contains unsupported characters" >&2; exit 2; }
[[ "$retain_releases" =~ ^[0-9]+$ ]] && (( retain_releases >= 2 && retain_releases <= 20 )) || {
  echo "QUANT_CONSOLE_RETAIN must be between 2 and 20" >&2; exit 2; }

required_commands=(ssh)
if [[ "$rollback_id" == "" && "$list_releases" != true ]]; then
  required_commands+=(npm rsync git)
fi
for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }
done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
rsync_ssh=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

if [[ "$list_releases" == true ]]; then
  "${ssh_command[@]}" "$edge_host" bash -s -- "$console_root" <<'REMOTE_LIST'
set -euo pipefail
console_root="$1"
printf 'current -> %s\n' "$(readlink -f "$console_root/current" 2>/dev/null || echo '(none)')"
ls -1 "$console_root/releases" 2>/dev/null | sort || echo '(no releases yet)'
REMOTE_LIST
  exit 0
fi

if [[ -n "$rollback_id" ]]; then
  [[ "$apply" == true ]] || { echo "rollback 需要 --apply" >&2; exit 2; }
  "${ssh_command[@]}" "$edge_host" bash -s -- "$console_root" "$rollback_id" <<'REMOTE_ROLLBACK'
set -euo pipefail
console_root="$1"; rollback_id="$2"
target="$console_root/releases/$rollback_id"
test -f "$target/index.html"
ln -sfn "releases/$rollback_id" "$console_root/current.next"
mv -Tf "$console_root/current.next" "$console_root/current"
printf 'quant console rolled back to %s\n' "$rollback_id"
REMOTE_ROLLBACK
  exit 0
fi

# 只构建这一个 SPA。它是资源构建，不碰 Docker。
( cd "$repo_root/frontend" && npm run build ) || {
  echo "quant console build failed; nothing was staged" >&2; exit 1; }
test -f "$repo_root/frontend/dist/index.html" || {
  echo "frontend/dist/index.html missing after build" >&2; exit 1; }

head_sha="$(git -C "$repo_root" rev-parse --verify HEAD)"
[[ "$head_sha" =~ ^[0-9a-fA-F]{7,64}$ ]] || { echo "cannot derive a valid git SHA" >&2; exit 2; }
# 盯**源码**目录。frontend/dist 在 .gitignore 里，拿它当 pathspec 等于没检查。
dirty_suffix=""
git -C "$repo_root" diff --quiet --ignore-submodules -- frontend || dirty_suffix="-dirty"
release_id="console-$(date -u +%Y%m%dT%H%M%SZ)-${head_sha:0:12}${dirty_suffix}"
printf 'quant_console_release=%s\nbase_git_sha=%s\nedge_host=%s\nconsole_root=%s\n' \
  "$release_id" "$head_sha" "$edge_host" "$console_root"

if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply to upload and switch"
  exit 0
fi

upload_dir="$console_root/.uploading.$release_id"
"${ssh_command[@]}" "$edge_host" bash -s -- "$console_root" "$upload_dir" <<'REMOTE_PREPARE'
set -euo pipefail
console_root="$1"; upload_dir="$2"
mkdir -p "$console_root/releases"
rm -rf -- "$upload_dir"
mkdir -p "$upload_dir"
REMOTE_PREPARE

rsync -az --delete --safe-links -e "${rsync_ssh[*]}" \
  "$repo_root/frontend/dist/" "$edge_host:$upload_dir/"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$console_root" "$upload_dir" "$release_id" "$head_sha" "$retain_releases" <<'REMOTE_ACTIVATE'
set -euo pipefail
console_root="$1"; upload_dir="$2"; release_id="$3"; head_sha="$4"; retain="$5"
release_dir="$console_root/releases/$release_id"
test -f "$upload_dir/index.html"
test ! -e "$release_dir"
printf '%s\n' "$head_sha" > "$upload_dir/.base-git-sha"
mv -T "$upload_dir" "$release_dir"
previous="$(readlink -f "$console_root/current" 2>/dev/null || true)"
ln -sfn "releases/$release_id" "$console_root/current.next"
mv -Tf "$console_root/current.next" "$console_root/current"
test -f "$console_root/current/index.html" || {
  if [ -n "$previous" ]; then
    ln -sfn "releases/$(basename "$previous")" "$console_root/current.next"
    mv -Tf "$console_root/current.next" "$console_root/current"
  fi
  echo 'activation check failed; previous console restored' >&2
  exit 1
}
# 只保留最近几个，旧的可回滚
ls -1dt "$console_root/releases"/* 2>/dev/null | tail -n +"$((retain + 1))" | while read -r stale; do
  [ "$stale" = "$release_dir" ] && continue
  [ "$stale" = "$previous" ] && continue
  rm -rf -- "$stale"
done
printf 'quant console active: %s\n' "$release_id"
REMOTE_ACTIVATE

echo 'quant console published; it is a separate release unit from the Feishu relay'
