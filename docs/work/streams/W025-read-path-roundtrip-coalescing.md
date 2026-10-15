# W025 只读状态路径的往返与重复请求收敛

- state: building
- holder: Codex 主会话，2026-10-16
- goal: 在不缓存旧证据、不改变状态语义和取消语义的前提下，减少研究台刷新造成的重复 HTTP 请求与 owner 数据库状态读往返
- next: owner 需先按 schema gate 应用 `20261016_ow0127`，再按同一交易日复核状态、策略健康、最新扫描和市场量能接口耗时、statement count、索引命中、锁等待和隧道速率；生产发布仍待用户执行
- updated: 2026-10-16
- plan: none
- branch: main
- head: 5e8c615a
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
- [x] 4 完成全量 overlay、前端构建和发布计划（全量仅剩其他会话 `local_spool_*` 环境变量登记失败）
- [ ] 5 后续 owner 发布后回填线上耗时、statement count、连接池等待与隧道速率

## Log

- 2026-10-16 02:19 Asia/Shanghai：共享 JSON client 对无取消信号的相同路径只复用当前 in-flight promise；settle 后删除条目，下一轮仍访问服务端。带 `AbortSignal` 的调用不合并。
- 2026-10-16 02:19 Asia/Shanghai：`intraday_runtime_status` 把 16 个顺序查询收敛为 12 个，保留数据库原生时间戳和 JSONB 行形状；最新状态、投递历史和待重试计数的字段语义不变。
- 2026-10-16 02:21 Asia/Shanghai：最新 focused release-path 回归运行 71 项通过；前端 14 个测试文件、64 项通过，typecheck/build 通过；共享前端同步守卫和组件边界检查通过；尚未执行生产发布。
- 2026-10-16 02:21 Asia/Shanghai：全量 quant overlay 运行 3293 项，唯一失败仍是其他会话 `local_spool_*` 读取未登记 `DOCKER_HOST`、`SPOOL_*`、`XDG_RUNTIME_DIR` 等环境变量；本线状态读和 HTTP 测试未失败。
- 2026-10-16 02:30 Asia/Shanghai：Provider 实时面板新增本地执行器水位展示，直接显示活跃数、worker 上限、排队余量和可用槽位；前端 typecheck/build 通过，未改任何运行上限。
- 2026-10-16 04:18 Asia/Shanghai：运行状态看板的 12 个串行读合并为一个同事务 `RUNTIME_STATUS_SNAPSHOT_SQL`；JSONB envelope 只在进程内恢复约定时间字段，不跨请求缓存。执行计划定位到 rule-input schema 统计的 JSONB 解析，新增 v1/v2 partial indexes 迁移 `20261016_ow0124`，再为 quote source 聚合新增 `20261016_ow0125` source-leading index。本地 compose 探针在等价索引下由约 9.7 秒降至中位 0.095 秒，statement count 为 1。
- 2026-10-16 04:32 Asia/Shanghai：策略健康快照保持单条 SQL；全局行情新鲜度的 `max(observed_at)` 与 90 秒计数新增迁移 `20261016_ow0126` 时间索引。本地同名索引探针中位约 3.7 ms，未使用索引时热身后约 37 ms；仅为本地 PostgreSQL 计划证据，owner 需复测写入成本和锁等待。
- 2026-10-16 04:35 Asia/Shanghai：全量 quant overlay 运行 3311 项，唯一失败仍是其他会话 `local_spool_*` 读取未登记 `DOCKER_HOST`、`SPOOL_*`、`XDG_RUNTIME_DIR` 等环境变量；本线 focused 80 项通过，前端 typecheck/build 和容器内组件边界检查通过；未执行生产发布。
- 2026-10-16 04:12 Asia/Shanghai：最新扫描信号读取新增 `20261016_ow0127` 的 `(scan_id,severity DESC,created_at)` 索引；本地 5 次探针中位约 5.587 ms 降至 0.769 ms，仍保留投递表原有索引。
- 2026-10-16 04:15 Asia/Shanghai：最新扫描的 scan/signals/deliveries 由同步与异步读路径共用的一条 JSONB 快照 SQL 返回，statement count 从 3 降为 1；本地五次中位 4.778 ms，旧三条查询 4.203 ms，线上收益待 owner 隧道复测。远端观察台新增阶段耗时比例条和 overlap 标签，前端全量 66 项通过、构建通过。
- 2026-10-16 04:40 Asia/Shanghai：市场量能复盘的分钟特征、日摘要、板块日流、结果汇总和研究门禁由 `MARKET_FLOW_SNAPSHOT_SQL` 合并为一次 JSONB 快照；同步/异步路径 statement count 从 5 降到 1。结果计数、研究门禁、状态计数和来源标签与旧路径一致；本地五次中位约 788.019 ms，旧五条约 790.734 ms，owner 需用真实隧道复测少四次往返的收益。代码提交 `5e8c615a`，探针 `docs/work/probes/2026-10-16-market-flow-snapshot.py`。

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
- `docs/work/probes/2026-10-16-strategy-health-read.py`
- `docs/work/probes/2026-10-16-strategy-health-read.out.txt`
- `docs/work/probes/2026-10-16-latest-scan-read.py`
- `docs/work/probes/2026-10-16-latest-scan-read.out.txt`
- `docs/work/probes/2026-10-16-latest-scan-snapshot.py`
- `docs/work/probes/2026-10-16-latest-scan-snapshot.out.txt`
- `docs/work/probes/2026-10-16-market-flow-snapshot.py`
- `docs/work/probes/2026-10-16-market-flow-snapshot.out.txt`
- `docs/decisions/0034-runtime-status-snapshot.md`
- `docs/decisions/0035-strategy-health-quote-freshness-index.md`
- `frontend/src/views/research/RemoteRealtimeTab.vue`

## Open questions

- owner 数据库经反向隧道的真实收益要在新源码运行后测量，不能用本地 mock 查询次数替代线上延迟。
- 如果同一状态接口仍是首屏热点，下一步应按 tab 读取或增加只读聚合读模型；在没有线上 statement/锁等待证据前不增加 TTL。

## Resume here

先跑全量 quant overlay 和前端 typecheck/build，再检查组件边界与 git diff；发布计划必须把 owner quant 和 edge dashboard 的 release unit 分开。
