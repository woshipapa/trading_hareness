#!/usr/bin/env bash
# 单独发布 XHS 的 n8n 调度 workflow（workflows/xhs-intel-edge.json）到 edge。
#
# 为什么需要它：workflow 的导入流程原本只内嵌在 ``deploy-xhs-intel-edge.sh``
# 里 —— 也就是改一个 cron 表达式就要走一次完整镜像发布。源码走
# ``hotfix-xhs-intel-edge.sh``、workflow 走这里，两者都不重建镜像。
#
# 流程与镜像发布里的 workflow 段完全一致：导出现网定义做契约对比，没变化就
# 直接退出；有变化则先备份，停 n8n、用一次性 CLI 容器 import+publish、再启动，
# 任一步失败都回灌备份定义。发布后把 ``XHS_WORKFLOW_SHA256`` 写回 runtime env
# 作为 provenance（采集器容器在下次 recreate 时才会在 /health 里报告新值）。
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: publish-xhs-workflow-edge.sh [--apply]
  默认 dry run：本地校验 candidate 并打印计划，不连接 edge。
EOF
  exit 2
}

apply=false
while (($#)); do
  case "$1" in
    --apply) apply=true; shift ;;
    *) usage ;;
  esac
done

component_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(cd "$component_root/.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_dir="${RELAY_EDGE_DIR:-/opt/feishu-relay-edge}"
runtime_env="${RELAY_EDGE_RUNTIME_ENV:-/etc/feishu-relay-edge/runtime.env}"
secrets_env="${RELAY_EDGE_SECRETS_ENV:-$edge_dir/.env}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
workflow_file="$repo_root/workflows/xhs-intel-edge.json"
workflow_id="xhs-intel-edge-daily-v1"
n8n_container="${XHS_N8N_CONTAINER:-feishu-relay-edge-n8n}"

for command in ssh scp python3 git; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 127; }
done
[[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }
[[ -f "$workflow_file" ]] || { echo "workflow source missing: $workflow_file" >&2; exit 2; }

# 和 relay 的 workflow 发布一样：只发布已提交的 workflow 版本。
git -C "$repo_root" diff --quiet HEAD -- workflows/xhs-intel-edge.json || {
  echo "refusing to publish an uncommitted workflow revision; commit it first" >&2; exit 2; }

workflow_sha256="$(python3 - "$workflow_file" "$workflow_id" <<'PY'
import hashlib
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
if not (isinstance(value, list) and len(value) == 1 and value[0].get('id') == sys.argv[2]):
    raise SystemExit('invalid XHS workflow candidate')
print(hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':')).encode()).hexdigest())
PY
)"
head_sha="$(git -C "$repo_root" rev-parse --verify HEAD)"
printf 'workflow_id=%s\nworkflow_sha256=%s\nbase_git_sha=%s\nedge_host=%s\n' \
  "$workflow_id" "$workflow_sha256" "$head_sha" "$edge_host"

if [[ "$apply" != true ]]; then
  echo "dry run only; append --apply to publish the workflow on edge"
  exit 0
fi

ssh_command=(ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
stage="/tmp/xhs-workflow-deploy-$(date -u +%Y%m%d%H%M%S)-$$"
"${ssh_command[@]}" "$edge_host" "install -d -m 0700 '$stage' '$stage/cli'"
scp -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -q \
  "$workflow_file" "$edge_host:$stage/cli/candidate.json"

"${ssh_command[@]}" "$edge_host" bash -s -- \
  "$edge_dir" "$runtime_env" "$secrets_env" "$stage" "$workflow_id" \
  "$n8n_container" "$workflow_sha256" <<'REMOTE'
set -euo pipefail
edge_dir="$1"; runtime_env="$2"; secrets_env="$3"; stage="$4"
workflow_id="$5"; n8n_container="$6"; workflow_sha256="$7"
cd "$edge_dir"
compose=(docker compose --env-file "$runtime_env" --env-file "$secrets_env")
cleanup() { rm -rf -- "$stage"; }
trap cleanup EXIT

docker exec "$n8n_container" n8n export:workflow --all --output=/tmp/xhs-export.json >/dev/null
docker cp "$n8n_container:/tmp/xhs-export.json" "$stage/export.json" >/dev/null
docker exec --user root "$n8n_container" rm -f /tmp/xhs-export.json
python3 - "$stage" "$workflow_id" <<'PY'
import json
import pathlib
import sys

stage = pathlib.Path(sys.argv[1])
current = [w for w in json.loads((stage / 'export.json').read_text()) if w['id'] == sys.argv[2]]
candidate = json.loads((stage / 'cli/candidate.json').read_text())
if len(candidate) != 1 or candidate[0]['id'] != sys.argv[2]:
    raise SystemExit('invalid XHS workflow candidate')
if current:
    (stage / 'cli/before.json').write_text(json.dumps(current, ensure_ascii=False))
keys = ('id', 'name', 'nodes', 'connections', 'settings', 'active')
def contract(workflows):
    return [{k: w.get(k) for k in keys} for w in workflows]
if contract(current) != contract(candidate):
    (stage / 'changed').touch()
PY
if [ ! -f "$stage/changed" ]; then
  echo 'XHS workflow unchanged; skipping import and restart'
  exit 0
fi

workflow_backup="$edge_dir/backups/xhs-workflow/$(date -u +%Y%m%d-%H%M%S)"
install -d -m 0700 "$workflow_backup"
if [ -f "$stage/cli/before.json" ]; then
  install -m 0600 "$stage/cli/before.json" "$workflow_backup/before.json"
fi

n8n_stopped=false
finish() {
  if [ "$n8n_stopped" = true ]; then "${compose[@]}" start n8n >/dev/null || true; fi
  rm -rf -- "$stage"
}
trap finish EXIT
"${compose[@]}" stop n8n >/dev/null
n8n_stopped=true
workflow_cli=("${compose[@]}" run --rm --no-deps -v "$stage/cli:/xhs-deploy:ro" n8n)
if "${workflow_cli[@]}" import:workflow --input=/xhs-deploy/candidate.json >/dev/null && \
   "${workflow_cli[@]}" publish:workflow --id="$workflow_id" >/dev/null; then
  echo "XHS workflow published; backup=$workflow_backup"
else
  if [ -f "$stage/cli/before.json" ]; then
    "${workflow_cli[@]}" import:workflow --input=/xhs-deploy/before.json >/dev/null || true
    "${workflow_cli[@]}" publish:workflow --id="$workflow_id" >/dev/null || true
  fi
  echo 'XHS workflow publication failed; restored the previous definition' >&2
  exit 1
fi
"${compose[@]}" start n8n >/dev/null
n8n_stopped=false
ok=false
for attempt in $(seq 1 45); do
  if curl -fsS --max-time 5 http://127.0.0.1:5678/healthz >/dev/null 2>&1; then ok=true; break; fi
  sleep 2
done
if [ "$ok" != true ]; then
  echo 'n8n did not come back healthy after the workflow publish' >&2
  exit 1
fi

# provenance：/health 的 release 信息来自采集器环境，下次 recreate 时生效。
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
update_env XHS_WORKFLOW_SHA256 "$workflow_sha256"
echo 'n8n healthy after workflow publish; XHS_WORKFLOW_SHA256 recorded'
REMOTE

echo 'XHS workflow publish finished (no image build/pull, collector untouched)'
