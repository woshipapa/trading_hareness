# 0007 每个后台任务都登记在某台主机上，退役任务默认不启动

- 状态：生效
- 日期：2026-10-08

## 背景

本机 supervisor 里的 `watchlist.sync` 在 edge 盘中服务退役之后还在运行，连续失败了
一万多次，却没有人注意到；文档里也说不清哪个组件跑在哪台机器上。

## 决定

- `config/components.json` 的 `hosts` 登记四台主机（47owner、owner-windows、47edge、
  workstation）各自运行哪些组件。
- 本机 supervisor 的每个任务都必须归类：owner-writer、owner-reader、tunnel、
  edge-client、local 或 retired。
- 退役任务默认不启动，只有显式设置开关才运行（例如 `WATCHLIST_EDGE_SYNC=1`）。

## 后果

新增后台任务时必须先归类，否则 CI 失败。

## 怎样保证

CI 运行 `scripts/verify_runtime_hosts.py --check`，测试在
`scripts/test_runtime_hosts.py` 和 `scripts/test_supervisor_retired_tasks.py`。
