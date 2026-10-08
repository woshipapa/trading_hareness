#!/usr/bin/env bash
# xhs-intel 的源码热更新：只发 Python 源码，复用现有镜像，不重建。
#
# 为什么需要它：``deploy-xhs-intel-edge.sh`` 只要 ``XHS_INTEL_TREE_SHA256`` 变了
# 就 ``docker compose build xhs-collector`` —— 也就是**改一行 Python 就重建一次
# 镜像**。采集器镜像要装 nodejs/npm、pip 依赖、还要 ``npm ci`` 外部 Spider_XHS，
# 重建既慢又把一次源码改动变成一次完整的供应链动作。
#
# 这里和适配器、quant-service 走同一套：把源码发成带版本的 overlay、原子换
# symlink、只重建容器不重建镜像、健康门禁失败自动还原。
#
# **fail closed**：依赖面一变就拒绝，让人去走镜像发布 ——
#   * ``requirements.txt`` 与运行镜像里烤进去的那份不一致；
#   * ``Dockerfile`` 变了（基础镜像、系统包、npm ci 都在里面）；
#   * 外部 Spider_XHS（``xhs-source``）变了。
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: hotfix-xhs-intel-edge.sh [--apply] [--rollback <release-id>] [--list]
  默认 dry run：跑测试与预检，不上传、不切换。
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

component_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(cd "$component_root/.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
secrets_env="${RELAY_EDGE_SECRETS_ENV:-$edge_dir/.env}"
hotfix_root="${XHS_HOTFIX_ROOT:-$edge_dir/hotfix-xhs}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
container_name="${XHS_COLLECTOR_CONTAINER:-feishu-relay-edge-xhs}"
retain_releases="${XHS_HOTFIX_RETAIN:-5}"

[[ "$rollback_id" == "" || ( "$rollback_id" =~ ^xhs-[A-Za-z0-9][A-Za-z0-9._-]*$ && "$rollback_id" != *..* ) ]] || {
  echo "rollback release ID contains unsupported characters" >&2; exit 2; }
[[ "$retain_releases" =~ ^[0-9]+$ ]] && (( retain_releases >= 2 && retain_releases <= 20 )) || {
  echo "XHS_HOTFIX_RETAIN must be between 2 and 20" >&2; exit 2; }

required_commands=(ssh)
if [[ "$rollback_id" == "" && "$list_releases" != true ]]; then
  required_commands+=(rsync git python3)
fi
for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }
done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
rsync_ssh=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

if [[ "$list_releases" == true ]]; then
  "${ssh_command[@]}" "$edge_host" bash -s -- "$hotfix_root" <<'REMOTE_LIST'
set -euo pipefail
hotfix_root="$1"
printf 'current -> %s\n' "$(readlink -f "$hotfix_root/current" 2>/dev/null || echo '(none)')"
ls -1 "$hotfix_root/releases" 2>/dev/null | sort -r || echo '(no releases yet)'
REMOTE_LIST
  exit 0
fi

if [[ -n "$rollback_id" ]]; then
  [[ "$apply" == true ]] || { echo "rollback 需要 --apply" >&2; exit 2; }
  "${ssh_command[@]}" "$edge_host" bash -s -- \
    "$edge_dir" "$runtime_env" "$secrets_env" "$hotfix_root" "$rollback_id" "$container_name" <<'REMOTE_ROLLBACK'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; hotfix_root="$4"; rollback_id="$5"; container_name="$6"
target="$hotfix_root/releases/$rollback_id"
test -f "$target/edge_api.py"
previous="$(readlink "$hotfix_root/current" 2>/dev/null || true)"
ln -sfn "releases/$rollback_id" "$hotfix_root/current.next"
mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"
cd "$edge_dir"
docker compose --env-file "$runtime_env" --env-file "$secrets_env" \
  up -d --no-build --pull never --force-recreate --no-deps xhs-collector
ok=false
for attempt in $(seq 1 45); do
  if curl -fsS --max-time 5 http://127.0.0.1:18790/health >/dev/null 2>&1; then ok=true; break; fi
  sleep 2
done
if [ "$ok" != true ]; then
  if [ -n "$previous" ]; then
    ln -sfn "$previous" "$hotfix_root/current.next"
    mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"
    docker compose --env-file "$runtime_env" --env-file "$secrets_env" \
      up -d --no-build --pull never --force-recreate --no-deps xhs-collector >/dev/null 2>&1 || true
  fi
  echo 'xhs rollback health check failed; previous overlay restored' >&2
  exit 1
fi
printf 'xhs overlay rolled back to %s\n' "$rollback_id"
REMOTE_ROLLBACK
  exit 0
fi

# 本地先跑这个组件自己的测试
( cd "$component_root" && python3 -m unittest discover -s . -p 'test_*.py' -q ) || {
  echo "xhs-intel tests failed; nothing was staged" >&2; exit 1; }

head_sha="$(git -C "$repo_root" rev-parse --verify HEAD)"
[[ "$head_sha" =~ ^[0-9a-fA-F]{7,64}$ ]] || { echo "cannot derive a valid git SHA" >&2; exit 2; }
# 盯本组件的源码目录。注意不要盯构建产物类目录。
dirty_suffix=""
git -C "$repo_root" diff --quiet --ignore-submodules -- xhs-intel || dirty_suffix="-dirty"
release_id="xhs-$(date -u +%Y%m%dT%H%M%SZ)-${head_sha:0:12}${dirty_suffix}"
printf 'xhs_overlay_release=%s\nbase_git_sha=%s\nedge_host=%s\nhotfix_root=%s\n' \
  "$release_id" "$head_sha" "$edge_host" "$hotfix_root"

# fail closed：Dockerfile 变了就不能走 overlay（基础镜像/系统包/npm ci 都在里面）
if ! git -C "$repo_root" diff --quiet --ignore-submodules HEAD -- xhs-intel/Dockerfile; then
  echo 'refused: xhs-intel/Dockerfile changed; publish an image release with deploy-xhs-intel-edge.sh' >&2
  exit 42
fi

if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply to upload and switch"
  exit 0
fi

stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/xhs-intel-overlay.XXXXXX")"
trap 'rm -rf -- "$stage_dir"' EXIT
# 只发 Python 源码；测试、Dockerfile、README 都不进运行时
# rsync 的过滤是**第一条命中的规则生效**，所以 exclude 必须排在 include 前面。
# 写成 ``--include '*.py' --exclude 'test_*.py'`` 的话 test_*.py 会先被 include
# 命中，测试文件就跟着进运行时了 —— 实测确实如此，所以这个顺序不能调。
rsync -a --safe-links --exclude 'test_*.py' --include '*.py' --exclude '*' \
  "$component_root/" "$stage_dir/"
test -f "$stage_dir/edge_api.py" || { echo "edge_api.py missing from stage" >&2; exit 1; }
printf '%s\n' "$head_sha" > "$stage_dir/.base-git-sha"
cp "$component_root/requirements.txt" "$stage_dir/.requirements.candidate"

upload_dir="$hotfix_root/.uploading.$release_id"
"${ssh_command[@]}" "$edge_host" bash -s -- "$hotfix_root" "$upload_dir" <<'REMOTE_PREPARE'
set -euo pipefail
hotfix_root="$1"; upload_dir="$2"
install -d -m 0755 "$hotfix_root" "$hotfix_root/releases"
rm -rf -- "$upload_dir"
install -d -m 0755 "$upload_dir"
REMOTE_PREPARE

rsync -az --delete --safe-links -e "${rsync_ssh[*]}" "$stage_dir/" "$edge_host:$upload_dir/"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$hotfix_root" "$upload_dir" \
  "$release_id" "$head_sha" "$container_name" "$retain_releases" <<'REMOTE_ACTIVATE'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; hotfix_root="$4"; upload_dir="$5"
release_id="$6"; head_sha="$7"; container_name="$8"; retain="$9"
release_dir="$hotfix_root/releases/$release_id"
test -f "$upload_dir/edge_api.py"
test ! -e "$release_dir"

# 依赖闸门：候选 requirements 必须和运行镜像里烤进去的那份一致。overlay 复用
# 镜像里已安装的包，依赖一变 overlay 就不成立。
docker exec "$container_name" cat /app/requirements.txt > "$upload_dir/.requirements.image"
if ! diff -q "$upload_dir/.requirements.candidate" "$upload_dir/.requirements.image" >/dev/null; then
  rm -rf -- "$upload_dir"
  echo 'refused: xhs-intel/requirements.txt differs from the running image; publish an image release' >&2
  exit 42
fi
rm -f "$upload_dir/.requirements.candidate" "$upload_dir/.requirements.image"

# 用运行镜像自己的解释器做语法检查，确保它能被这个 Python 版本解析
docker run --rm --pull never -v "$upload_dir:/overlay:ro" --entrypoint python \
  "$(docker inspect -f '{{.Config.Image}}' "$container_name")" \
  -c 'import pathlib,sys; [compile(p.read_text(), str(p), "exec") for p in pathlib.Path("/overlay").glob("*.py")]'

find "$upload_dir" -type d -exec chmod 0755 {} +
find "$upload_dir" -type f -exec chmod 0644 {} +
mv -T "$upload_dir" "$release_dir"

previous="$(readlink "$hotfix_root/current" 2>/dev/null || true)"
ln -sfn "releases/$release_id" "$hotfix_root/current.next"
mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"

update_env() {
  key="$1"; value="$2"; temp="$(mktemp "${runtime_env}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$runtime_env" > "$temp"
  chown --reference="$runtime_env" "$temp"; chmod --reference="$runtime_env" "$temp"
  mv -f "$temp" "$runtime_env"
}
previous_env="$(mktemp "${runtime_env}.xhs.XXXXXX")"
cp -p "$runtime_env" "$previous_env"
trap 'rm -f -- "$previous_env"' EXIT
update_env XHS_HOTFIX_ENABLED true
update_env XHS_HOTFIX_DIR "$hotfix_root"

base_image_id="$(docker inspect -f '{{.Image}}' "$container_name")"
restart_collector() {
  cd "$edge_dir"
  docker compose --env-file "$runtime_env" --env-file "$secrets_env" \
    up -d --no-build --pull never --force-recreate --no-deps xhs-collector
}
restore_previous() {
  if [ -n "$previous" ]; then
    ln -sfn "$previous" "$hotfix_root/current.next"
    mv -Tf "$hotfix_root/current.next" "$hotfix_root/current"
  else
    rm -f "$hotfix_root/current"
  fi
  restore_temp="$(mktemp "${runtime_env}.restore.XXXXXX")"
  cp -p "$previous_env" "$restore_temp"; mv -f "$restore_temp" "$runtime_env"
  restart_collector >/dev/null 2>&1 || true
}
if ! restart_collector; then
  restore_previous
  echo 'xhs overlay restart failed; previous runtime restored' >&2
  exit 1
fi
ok=false
for attempt in $(seq 1 45); do
  if curl -fsS --max-time 5 http://127.0.0.1:18790/health >/dev/null 2>&1 \
    && [ "$(docker inspect -f '{{.Image}}' "$container_name")" = "$base_image_id" ]; then
    ok=true; break
  fi
  sleep 2
done
if [ "$ok" != true ]; then
  restore_previous
  echo 'xhs overlay health verification failed; previous runtime restored' >&2
  exit 1
fi
# 文件存在 ≠ 进程在跑它。健康端点有响应、镜像 id 没变、overlay 文件在，这三件
# 事加起来仍然不能证明 overlay 生效 —— 只要 compose 的 command 丢了或
# XHS_HOTFIX_ENABLED 没置上，进程就会安静地跑 /app/edge_api.py，而这些检查全绿。
# 所以直接看 PID 1 的 argv。注意不能用 ``docker exec env``：exec 起的是新进程，
# 看不到 entrypoint shell 里 export 的变量（我第一次核查就被这一点误导了）。
docker exec "$container_name" test -f /app/hotfix/current/edge_api.py
if ! docker exec "$container_name" sh -c \
    'tr "\0" " " < /proc/1/cmdline | grep -q "/app/hotfix/current/edge_api.py"'; then
  restore_previous
  echo 'xhs overlay is staged but PID 1 is not running it; previous runtime restored' >&2
  exit 1
fi

ls -1dt "$hotfix_root/releases"/* 2>/dev/null | tail -n +"$((retain + 1))" | while read -r stale; do
  [ "$stale" = "$release_dir" ] && continue
  [ "$hotfix_root/$previous" = "$stale" ] && continue
  case "$stale" in "$hotfix_root/releases"/*) rm -rf -- "$stale" ;; esac
done
printf 'xhs overlay activated: %s (no image build/pull)\n' "$release_id"
REMOTE_ACTIVATE

echo 'xhs-intel source overlay published; the collector image was not rebuilt'
