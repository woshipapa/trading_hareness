# W025 只读状态路径的往返与重复请求收敛

- state: building
- holder: Codex 主会话，2026-10-16
- goal: 在不缓存旧证据、不改变状态语义和取消语义的前提下，减少研究台刷新造成的重复 HTTP 请求与 owner 数据库状态读往返
- next: owner 需先按 schema gate 应用 `20261016_ow0128`、`20261016_ow0129`、`20261016_ow0130`，再按同一交易日复核状态、策略健康、最新扫描、市场量能、研究概览和 feature-readiness 接口耗时、statement count、索引命中、锁等待和隧道速率；生产发布仍待用户执行
- updated: 2026-10-16
- plan: none
- branch: main
- head: 204f53b6
- release: none

## Requirements

- 用户要求继续优化软件限制内的慢流程，充分利用现有 CPU、内存和网络带宽。
- 研究状态必须保持只读、点时证据和 fail-closed 语义；不能用旧缓存掩盖服务故障。
- 带 `AbortSignal` 的请求必须保持独立，取消一个页面不能取消另一个页面的请求。
- owner 生产发布和数据库写入仍由用户按发布窗口执行，本线只修改源码和测试。
- 2026-10-16：用户要求：“如果是当前软件的限制的，全都可以提高，只要不是硬件的硬限制相关的全部提升！！！”；软件 ceiling 可以提高，但仍须受 owner 硬件、数据库预算、隧道和上游合同约束，并以正确性和可用性压力证据为准。

## Plan

1. 在共享前端 JSON GET 层只合并同一路径的 in-flight promise，响应结束后立即清除。
2. 在 intraday runtime status 的同一只读事务内合并可保持行类型的查询：最新扫描与最新 completed 扫描、最新板块报告与曲线、最新投递与最近 20 条投递历史、两类待重试计数。
3. 用 overlay 测试验证结果形状、查询数量和取消边界；不引入 TTL 或跨请求旧数据缓存。
4. 对 feature-readiness 的独立聚合做 native async fanout，并修复 sync/async pool 生命周期的 single-flight 竞态。

## Steps

- [x] 1 盘点前端重复 GET 和状态读查询序列
- [x] 2 实现共享 GET in-flight 合并及状态查询合并
- [x] 3 增加回归测试并完成 focused overlay 验证
- [x] 4 完成全量 overlay、前端构建和发布计划（全量仅剩其他会话 `local_spool_*` 环境变量登记失败）
- [x] 5 feature-readiness 并行读取、连接池生命周期锁、0130 覆盖索引迁移和 1/2/4/8 压力探针
- [x] 6 盘后 Longhu 收盘请求去重、收盘 review overlap、日线补齐重复读收敛和有界 stage 并行探针
- [ ] 7 后续 owner 发布后回填线上耗时、statement count、连接池等待与隧道速率

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
- 2026-10-16 04:49 Asia/Shanghai：定位 `/api/v1/research/overview` 的主要慢点为历史容量估算；本地旧路径约 8 秒，其中 canonical bars 按日去重约 7.7 秒。新增 `20261016_ow0128` 的有效 bars 覆盖索引、`20261016_ow0129` 的 fundamentals/limits 窄 symbol 索引，并利用 canonical `(symbol,trading_date)` 主键将覆盖统计的 `count(DISTINCT symbol)` 改为等价 `count(*)`。原生 async 历史估算的七个独立只读聚合与概览快照并发；本地三次历史估算约 2.88--3.40 秒中位 2.935 秒，coverage 与同步 estimator、dataset 行数逐项相等；探针为 `docs/work/probes/2026-10-16-research-overview-history.py`。
- 2026-10-16 04:49 Asia/Shanghai：重启本地 quant 服务后 `/api/v1/research/overview` 三次返回 200，耗时约 2.45--2.77 秒；这是本地 compose 证据，owner 隧道收益、p95 和锁等待仍需发布后复核。
- 2026-10-16 04:50 Asia/Shanghai：本线 focused overlay 回归 86 项通过；全量 quant overlay 3314 项中唯一失败仍是其他会话 `local_spool_*` 读取未登记 `DOCKER_HOST`、`OFFICIAL_HEALTH_URL`、`SPOOL_*`、`XDG_RUNTIME_DIR` 等环境变量；前端 14 个测试文件、66 项通过，typecheck/build 通过；未执行生产发布。
- 2026-10-16：发现多个首屏请求可同时触发 psycopg sync/async pool `open()`，在 async pool 上复现 `AssertionError: _pool_full_event`；加入 sync `threading.Lock`、async `asyncio.Lock`，并让 `replace_pool` 持锁完成新池首次打开。focused overlay 回归 145 项通过。
- 2026-10-16：`feature_readiness` 拆为 8 个 feature 聚合加 1 个 universe 统计并发读取；本地 overlay 三次中位 1,532.029 ms，legacy UNION 对照 1,716.112 ms，返回 8 项且 `decision_ready=true`。证据脚本和输出为 `docs/work/probes/2026-10-16-feature-readiness-overlap.py` / `.out.txt`，owner 还未套用 0130 索引迁移。
- 2026-10-16：当前源码 ASGI 压力矩阵 `/api/v1/data-readiness/features` 的 1/2/4/8 并发均 200；p50 分别约 1,672/2,248/3,704/7,514 ms，8 并发 p95 约 7,855 ms，慢事务日志达到 5--8 秒。async pool 达到 16 个连接但无等待，说明继续提高 owner 默认池会先放大 4 核数据库竞争；证据为 `docs/work/probes/2026-10-16-feature-readiness-stress.py` / `.out.txt`。
- 2026-10-16：更正上一条“重启本地 quant 服务后 curl 2.45--2.77 秒”的记录：该长驻容器使用 baked image，未执行当前源码 overlay，不能作为当前代码证据。当前源码 overlay 直接测得 history/overview 并行约 1,715 ms、runtime status snapshot 约 244 ms；后续报告均以 overlay 或明确标注的 baked image 为准。
- 2026-10-16：只读执行 `scripts/release plan a809ced7`：owner 当前库版本 `20261015_ow0122`，目标 `20261016_ow0130`，需先应用 0123--0130；计划因其他会话的 host-tree 路径判为 FULL，未执行 `--apply`。edge adapter/dashboard 与 xhs 也有其他会话改动，保持独立发布单元。
- 2026-10-16：修正 0130 downgrade：删除覆盖索引后恢复 0129 的两个窄 `symbol` 索引；Alembic offline `20261016_ow0130:20261016_ow0129 --sql` 输出与 0129 schema 契约一致。focused overlay 145 项通过，head `2f26a916`。
- 2026-10-16：更正 release plan 目标为最新提交 `2f26a916`：结论不变，owner 当前 `20261015_ow0122`，目标 `20261016_ow0130`，需先应用 0123--0130；因其他会话 host-tree 路径仍判 FULL，未执行 `--apply`。
- 2026-10-16：盘后 Longhu 的 `all_a_universe` 与 `full_market_daily` 通过按交易日的成功短时 memo 共享一次底层 close；失败/blocked 不缓存且停机取消 in-flight。收盘 review 的指数上下文与 market snapshot 并行启动；daily close 的 roster+bars 改为一次 left join，并复用当日 bars，避免无变化时重复读取。
- 2026-10-16：post-close orchestrator 对清理/日历/分析师文本、两类 analyst outcomes、四类温度/ETF/市场时点研究启用显式 bounded groups；当前源码合成探针 20 次中位 635.246 ms → 269.285 ms，峰值并发 1 → 4。探针只测编排层，不代表 owner/provider 吞吐；证据为 `docs/work/probes/2026-10-16-post-close-overlap.py` / `.out.txt`，决定记录为 `docs/decisions/0038-post-close-coalescing-and-bounded-overlap.md`。
- 2026-10-16：远端观察台将轮询分成实时水位/扫描 15 秒和盘后研究/晋级结果 60 秒两档；手动刷新仍读取全部面板，任何失败仍清空对应面板，避免旧证据伪装成当前状态。定时读取从每 15 秒 11 个请求降为每 15 秒 5 个加每 60 秒 6 个，按调度频率计算平均请求量减少约 41%；前端 14 个测试文件、67 项通过，typecheck/build 通过，提交 `19ff8322`。
- 2026-10-16 09:08 Asia/Shanghai：开盘前冻结。状态快照、feature-readiness 并行读取、overview 索引和 0128--0130 迁移已完成本地验证；owner 仍在 `20261015_ow0122`，目标 `20261016_ow0130`，生产发布及线上 statement/锁等待/隧道速率复核未完成。
- 2026-10-16 09:22 Asia/Shanghai：为 `/api/v1/data-readiness/features` 增加服务端 in-flight single-flight，只共享活动计算，不保留结果 TTL。源码 overlay 压测 1/2/4/8 并发全部 200；8 并发 p95 约 1,711.6 ms，较此前同一路径约 7,855 ms 的 p95 明显下降，连接池回到 `pool_size=9,waiting=0`。证据为 `docs/work/probes/2026-10-16-feature-readiness-singleflight.out.txt`；仍需 owner 发布后复测。

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
- `docs/work/probes/2026-10-16-research-overview-history.py`
- `docs/work/probes/2026-10-16-research-overview-history.out.txt`
- `docs/work/probes/2026-10-16-feature-readiness-overlap.py`
- `docs/work/probes/2026-10-16-feature-readiness-overlap.out.txt`
- `docs/work/probes/2026-10-16-feature-readiness-stress.py`
- `docs/work/probes/2026-10-16-post-close-overlap.py`
- `docs/decisions/0038-post-close-coalescing-and-bounded-overlap.md`
- `docs/work/probes/2026-10-16-feature-readiness-stress.out.txt`
- `quant-service/migrations/versions/20261016_ow0128_canonical_coverage_index.py`
- `quant-service/migrations/versions/20261016_ow0129_coverage_symbol_indexes.py`
- `docs/decisions/0036-research-overview-history-overlap.md`
- `docs/decisions/0034-runtime-status-snapshot.md`
- `docs/decisions/0035-strategy-health-quote-freshness-index.md`
- `frontend/src/views/research/RemoteRealtimeTab.vue`

## Open questions

- owner 数据库经反向隧道的真实收益要在新源码运行后测量，不能用本地 mock 查询次数替代线上延迟；0130 迁移应用后还要复核索引命中和写入开销。
- 如果同一状态接口仍是首屏热点，下一步应按 tab 读取或增加只读聚合读模型；在没有线上 statement/锁等待证据前不增加 TTL。

## Resume here

先跑全量 quant overlay 和前端 typecheck/build，再检查组件边界与 git diff；发布计划必须把 owner quant 和 edge dashboard 的 release unit 分开。0130 迁移和本轮生命周期/feature-readiness 改动仍待用户按 release plan 发布后做线上验证。
