# W025 只读状态路径的往返与重复请求收敛

- state: building
- holder: Codex 主会话，2026-10-16
- goal: 在不缓存旧证据、不改变状态语义和取消语义的前提下，减少研究台刷新造成的重复 HTTP 请求与 owner 数据库状态读往返
- next: 完成本地 overlay 全量回归、前端 typecheck/build，并在后续 owner 发布后按同一交易日复核状态接口耗时、数据库 statement count 和隧道速率；owner 需先应用 `20261016_ow0125`
- updated: 2026-10-16
- plan: none
- branch: main
- head: 5ae3c13d
- release: none

## Requirements

- 用户要求继续优化软件限制内的慢流程，充分利用现有 CPU、内存和网络带宽。
- 研究状态必须保持只读、点时证据和 fail-closed 语义；不能用旧缓存掩盖服务故障。
- 带 `AbortSignal` 的请求必须保持独立，取消一个页面不能取消另一个页面的请求。
- owner 生产发布和数据库写入仍由用户按发布窗口执行，本线只修改源码和测试。

## Plan

1. 在共享前端 JSON GET 层只合并同一路径的 in-flight promise，响应结束后立即清除。
2. 在 intraday runtime status 的同一只读事务内合并可保持行类型的查询：最新扫描与最新 completed 扫描、最新板块报告与曲线、最新投递与最近 20 条投递历史、两类待重试计数。
3. 用 overlay 测试验证结果形状、查询数量和取消边界；不引入 TTL 或跨请求旧数据缓存。

## Steps

- [x] 1 盘点前端重复 GET 和状态读查询序列
- [x] 2 实现共享 GET in-flight 合并及状态查询合并
- [x] 3 增加回归测试并完成 focused overlay 验证
- [ ] 4 完成全量 overlay、前端构建和发布计划
- [ ] 5 后续 owner 发布后回填线上耗时、statement count、连接池等待与隧道速率

## Log

- 2026-10-16 02:19 Asia/Shanghai：共享 JSON client 对无取消信号的相同路径只复用当前 in-flight promise；settle 后删除条目，下一轮仍访问服务端。带 `AbortSignal` 的调用不合并。
- 2026-10-16 02:19 Asia/Shanghai：`intraday_runtime_status` 把 16 个顺序查询收敛为 12 个，保留数据库原生时间戳和 JSONB 行形状；最新状态、投递历史和待重试计数的字段语义不变。
- 2026-10-16 02:21 Asia/Shanghai：最新 focused release-path 回归运行 71 项通过；前端 14 个测试文件、64 项通过，typecheck/build 通过；共享前端同步守卫和组件边界检查通过；尚未执行生产发布。
- 2026-10-16 02:21 Asia/Shanghai：全量 quant overlay 运行 3293 项，唯一失败仍是其他会话 `local_spool_*` 读取未登记 `DOCKER_HOST`、`SPOOL_*`、`XDG_RUNTIME_DIR` 等环境变量；本线状态读和 HTTP 测试未失败。
- 2026-10-16 02:30 Asia/Shanghai：Provider 实时面板新增本地执行器水位展示，直接显示活跃数、worker 上限、排队余量和可用槽位；前端 typecheck/build 通过，未改任何运行上限。
- 2026-10-16 04:18 Asia/Shanghai：运行状态看板的 12 个串行读合并为一个同事务 `RUNTIME_STATUS_SNAPSHOT_SQL`；JSONB envelope 只在进程内恢复约定时间字段，不跨请求缓存。执行计划定位到 rule-input schema 统计的 JSONB 解析，新增 v1/v2 partial indexes 迁移 `20261016_ow0124`，再为 quote source 聚合新增 `20261016_ow0125` source-leading index。本地 compose 探针在等价索引下由约 9.7 秒降至中位 0.095 秒，statement count 为 1。

## Waiting on the user

## Running

## Due

## Evidence

- `frontend-shared/src/http.ts`
- `frontend/src/api/http.test.ts`
- `quant-service/app/intraday_runtime_status.py`
- `quant-service/tests/test_platform_boundaries.py`
- `quant-service/migrations/versions/20261016_ow0124_rule_input_status_indexes.py`
- `quant-service/migrations/versions/20261016_ow0125_runtime_quote_status_index.py`
- `docs/work/probes/2026-10-16-runtime-status-snapshot.py`
- `docs/work/probes/2026-10-16-runtime-status-snapshot.out.txt`
- `docs/decisions/0034-runtime-status-snapshot.md`

## Open questions

- owner 数据库经反向隧道的真实收益要在新源码运行后测量，不能用本地 mock 查询次数替代线上延迟。
- 如果同一状态接口仍是首屏热点，下一步应按 tab 读取或增加只读聚合读模型；在没有线上 statement/锁等待证据前不增加 TTL。

## Resume here

先跑全量 quant overlay 和前端 typecheck/build，再检查组件边界与 git diff；发布计划必须把 owner quant 和 edge dashboard 的 release unit 分开。
