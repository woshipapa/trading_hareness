#!/usr/bin/env bash
# Read-only drift report: both n8n instances against the committed workflow sources.
#
# 本机实例与 workflows/managed/ 镜像逐个契约对比（id/name/nodes/connections/
# settings/active），边缘实例与 workflows/edge-relay/ 及 xhs-intel-edge.json
# 对比；顶层 authored 源另行与运行时比对并标注。不写任何实例，不触发 workflow。
# 全量清单与归属见 workflows/CATALOG.md。
set -euo pipefail

usage() {
  echo "usage: workflow-status.sh [--local-only|--edge-only]" >&2
  exit 2
}

mode=all
case "${1:-}" in
  "") ;;
  --local-only) mode=local ;;
  --edge-only) mode=edge ;;
  *) usage ;;
esac

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
edge_host="${RELAY_EDGE_HOST:-root@47.114.113.152}"
edge_key="${RELAY_EDGE_SSH_KEY:-/Users/papa/.ssh/feishu_relay_edge_ed25519}"
edge_container="${RELAY_EDGE_N8N_CONTAINER:-feishu-relay-edge-n8n}"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/workflow-status.XXXXXX")"
trap 'rm -rf -- "$tmp"' EXIT

if [[ "$mode" != edge ]]; then
  docker compose -f "$root/compose.yaml" exec -T n8n sh -c \
    'n8n export:workflow --all --output=/tmp/wf-status.json >/dev/null 2>&1; cat /tmp/wf-status.json; rm -f /tmp/wf-status.json' \
    > "$tmp/local.json" || { echo "local n8n export failed (is the compose stack up?)" >&2; exit 1; }
fi

relay_status=skipped
if [[ "$mode" != local ]]; then
  [[ -r "$edge_key" ]] || { echo "edge SSH key is not readable: $edge_key" >&2; exit 2; }
  # relay 的四个 workflow 在部署时经过占位符渲染，裸契约对比必然误报；
  # 用组件自己的核验脚本（它会把现网值还原成占位符再 diff）。
  if bash "$root/scripts/verify-edge-relay-workflows.sh" > "$tmp/relay-verify.out" 2>&1; then
    relay_status=ok
  else
    relay_status=drift
  fi
  ssh -i "$edge_key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    "$edge_host" "docker exec $edge_container n8n export:workflow --all --output=/tmp/wf-status.json >/dev/null 2>&1; docker exec $edge_container cat /tmp/wf-status.json; docker exec --user root $edge_container rm -f /tmp/wf-status.json" \
    > "$tmp/edge.json" || { echo "edge n8n export failed" >&2; exit 1; }
fi

python3 - "$root" "$tmp" "$mode" "$relay_status" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(sys.argv[2])
mode = sys.argv[3]
relay_status = sys.argv[4]
KEYS = ("name", "nodes", "connections", "settings", "active")


def contract(workflow):
    return {key: workflow.get(key) for key in KEYS}


def load_list(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else [value]


def sources_from(paths):
    rows = {}
    for path in paths:
        for workflow in load_list(path):
            rows[str(workflow.get("id"))] = (workflow, path.relative_to(root).as_posix())
    return rows


drift = 0


def report(instance, runtime_path, sources, authored=None, known_other=frozenset()):
    global drift
    runtime = {str(w.get("id")): w for w in load_list(runtime_path)}
    print(f"== {instance} ==")
    for wid in sorted(sources):
        expected, origin = sources[wid]
        if wid not in runtime:
            print(f"  MISSING   {wid}  （仓库源 {origin} 不在运行时）")
            drift += 1
        elif contract(runtime[wid]) != contract(expected):
            print(f"  DRIFT     {wid}  运行时 != {origin}")
            drift += 1
        else:
            print(f"  in-sync   {wid}")
        if authored and wid in authored:
            a_workflow, a_origin = authored[wid]
            if wid in runtime and contract(runtime[wid]) != contract(a_workflow):
                print(f"            └─ authored 源 {a_origin} 与运行时不一致（converge 后用 export 刷镜像）")
    for wid in sorted(set(runtime) - set(sources) - set(known_other)):
        note = "未入库"
        if authored and wid in authored:
            note = f"authored 源 {authored[wid][1]} 存在，但未进 managed 镜像"
        print(f"  RUNTIME-ONLY {wid}  「{runtime[wid].get('name', '')}」{note}")
        drift += 1
    print()


if mode != "edge":
    managed = sources_from(sorted((root / "workflows/managed/workflows").glob("*.json")))
    authored = sources_from(sorted(p for p in (root / "workflows").glob("*.json")
                                   if p.name != "xhs-intel-edge.json"))
    report("本机 n8n（镜像基准 workflows/managed/）", tmp / "local.json", managed, authored)
    runtime_ids = {str(w.get("id")) for w in load_list(tmp / "local.json")}
    for wid in sorted(set(authored) - set(managed)):
        if wid not in runtime_ids:
            print(f"  note: authored-only {wid}（{authored[wid][1]}，运行时与镜像都没有）")
    print()

if mode != "local":
    print("== 边缘 n8n ==")
    relay_ids = sorted(p.stem for p in (root / "workflows/edge-relay/workflows").glob("*.json"))
    if relay_status == "ok":
        for wid in relay_ids:
            print(f"  in-sync   {wid}  （verify-edge-relay-workflows，渲染还原后一致）")
    else:
        print(f"  relay workflow 核验失败（共 {len(relay_ids)} 个，verify 在首个漂移处停止）：")
        print("    " + (tmp / "relay-verify.out").read_text(encoding="utf-8").strip().replace("\n", "\n    "))
        drift += 1
    print()
    edge_sources = sources_from([root / "workflows/xhs-intel-edge.json"])
    report("边缘 n8n（XHS，基准 workflows/xhs-intel-edge.json）", tmp / "edge.json", edge_sources,
           known_other=set(relay_ids))

if drift:
    print(f"{drift} 处不一致")
    raise SystemExit(1)
print("all in sync")
PY
