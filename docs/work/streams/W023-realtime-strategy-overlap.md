# W023 实时采集与策略计算 overlap

- state: building
- holder: Codex 主会话，2026-10-15
- goal: 审查并在不改变点时语义、研究只读边界和 owner 单写规则的前提下，让实时行情采集、持久化和策略计算形成有界的异步流水线，减少等待数据库写入后才计算的空档
- next: 在下一个 owner 代码发布后读取同一交易日的 scan_performance 阶段耗时与事务审计，再决定是否拆分策略纯计算与扫描写事务
- updated: 2026-10-16
- plan: docs/work/plans/realtime-strategy-overlap.md
- branch: main
- head: 00d237d8
- release: none

## Requirements

- 用户要求继续审查实时抓取、存储和策略计算之间的 overlap 与异步加速。
- 保留 raw evidence 先落盘、point-in-time availability、研究只读、fail-closed、幂等键和 owner 单写边界。
- 不让策略计算反过来阻塞行情采集；队列必须有界，写库失败、策略失败和重启都能重放或明确丢弃原因。

## Plan

1. 画出 capture → normalize → persist → strategy 的调用与线程/连接池关系，标记同步阻塞和重复读取。
2. 区分可以在内存中提前计算的纯策略输入、必须等待持久化 receipt 的研究结果、以及不能并行的单写操作。
3. 先实现一个有界的 in-process handoff 或批内 overlap seam，保留 durable evidence 作为唯一事实源；添加纯函数与生命周期测试。
4. 用 overlay 测试和本地 trace 验证：采集延迟、persist 延迟、策略开始延迟、队列深度、丢弃/重放计数、CPU/RSS 和数据库往返。
5. 发布前走 owner code-only；若涉及 migration，先走 schema gate。上线后再按同一交易日验证真实 overlap。

## Steps

- [x] 1 追踪实时采集、持久化、策略触发和调度调用链
- [x] 2 建立 overlap 设计和资源/故障预算
- [x] 3 实现最小可回退的异步流水线
- [ ] 4 回归测试、性能测量和工作记录
- [ ] 5 生成发布计划，等待 owner 运行证据

## Log

- 2026-10-15 23:19 Asia/Shanghai: 完成调用链审查；确认 capture 内部已有 provider fan-out。新增 membership/quote capture overlap、minute/board overlap、xiaojie evaluation/receipt barrier/delivery overlap；为每个 seam 加并发回归测试。watchlist 纯规则计算仍与单事务绑定，登记 B093 等待 stage timings 和三阶段协议设计。
- 2026-10-16 01:53 Asia/Shanghai: 增加 `intraday-scan-timing-v1` 只读遥测。每次 scan 在持久化前记录 session/watchlist、pre-capture、capture/membership、enrichment/strategy-prepare 和 signal-persist 阶段，提交后补写 delivery/research、shadow-rotation 与最终总耗时；遥测 JSONB 合并失败只标记 degraded，不改变主扫描结果。空观察池和 blocked scan 也保存 terminal timing。RemoteRealtime 观察台展示总耗时、阶段耗时和并行边界。
- 2026-10-16 01:56 Asia/Shanghai: 200 次确定性本地 probe 显示 capture/membership overlap 中位数约 1.808x、p95 约 2.377x；minute/board overlap 中位数约 1.765x、p95 约 2.345x。该 probe 只验证编排层的有界 I/O overlap，不冒充 provider 吞吐或 owner 线上收益。
- 2026-10-16 02:00 Asia/Shanghai: provider health 写入从 capture 返回前移出主等待点，改为每个 scan 一个有界 task 集合；策略链路立即消费 immutable capture，runtime 在响应前统一 join，失败不会留下 detached task，也不会改变 scan eligibility。
- 2026-10-16 02:41 Asia/Shanghai: primary signal receipt 提交后，ten-day shadow rotation 改为与 alert delivery/teacher research 并行；shadow 仍只写 research evidence，独立失败继续降级，响应前统一 join。新增事件型回归验证串行实现会等待死锁、并行实现可完成。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/ARCHITECTURE.md`
- `docs/work/FACTS.md` F053
- `docs/archive/2026-10-15-platform-architecture-audit.md`
- `quant-service/app/intraday_watch_quote_capture.py`: all-A、Tencent、Eastmoney、本地 reference、Longhu 已经在一次 capture 内部扇出。
- `quant-service/app/intraday_watchlist_scan_service.py`: membership 与 quote capture、minute enrichment 与 board cache、xiaojie evaluation 与主 signal persistence/delivery 已形成有界 overlap。
- `quant-service/app/xiaojie_leader_flow_runtime.py`: 纯评估完成后通过 `before_persist` receipt barrier 才写入带 `scan_id` 的研究记录。
- `quant-service/tests/test_intraday_watchlist_scan_service.py`: 三个互相等待的并发回归测试，串行实现会死锁，覆盖批内 overlap 和研究分支 overlap。
- `quant-service/app/intraday_scan_timing.py` 与 `quant-service/tests/test_intraday_scan_timing.py`: 阶段键、毫秒归一化和只读边界的纯函数契约。
- `docs/work/probes/2026-10-16-realtime-overlap-benchmark.out.txt`: 200 次本地 overlap 编排测量输出。

## Findings

- capture 内的 provider fan-out 已存在，不能继续无条件提高 provider 并发；owner 的 licensed 路径仍遵守既有 6--8 in-flight ceiling。
- 精确 point-in-time membership 读取只依赖 scan timestamp，行情 provider 不依赖它；两者现在并行，完成后才建立 peer mapping，语义不变。
- minute enrichment 与已持久化的 board-flow cache 是独立读取；两者现在并行，任一失败都会取消兄弟任务并把异常交给原有 scan failure path。
- xiaojie 使用同一 immutable all-A capture 提前做 session/reference 和纯评估；在第一笔带 `scan_id` 的候选写入前等待主 watchlist receipt barrier，再与 Feishu signal delivery 并行，任务在响应前 join，状态写入和返回值保持一致。
- provider health 只属于观测面，不参与信号资格；它现在与 capture 后的成员映射、分钟增强和策略准备重叠，最后统一回收，避免辅助数据库写入抵消 capture overlap。
- `scan_performance` 是 research-only、`live_effect=none` 的版本化证据；初始 scan receipt 先保存已知阶段，主事务提交后用一次轻量 JSONB merge 保存最终阶段，避免把遥测等待引入主信号事务。
- receipt barrier 被取消时恢复小杰的进程内评估游标，避免主事务回滚留下未落库的状态。
- `persist_scan_signals` 仍在同一 DB transaction 内执行 `prepare_inputs`、纯 `generate_signals`、事件去重与写入。拆成 prepare/evaluate/commit 三阶段会改变 scan receipt、foreign-key 可见性和失败恢复，需要独立设计与测量，暂不以提高并发换取语义风险。
- 新任务没有增加 provider worker 或 DB executor 上限；它们复用现有 bounded async read pool、provider deadlines 和 database boundary，避免把 200 Mbps 带宽误当成 provider quota。

## Verification

- `COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path QUANT_TESTS="tests.test_intraday_watchlist_scan_service tests.test_all_a_snapshot_eligibility tests.test_xiaojie_leader_flow_runtime"`: 25 passed。
- `COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path QUANT_TESTS="tests.test_intraday_watchlist_scan_runtime tests.test_intraday_monitor_service tests.test_intraday_watch_quote_capture tests.test_ingestion_and_provider_runtime tests.test_intraday_scan_signal_persistence tests.test_intraday_scan_persistence_runtime"`: 81 passed。
- Full overlay ran 3284 tests; the only failure was the pre-existing unassociated local-spool settings registry (`DOCKER_HOST`, `SPOOL_DIR`, `XDG_RUNTIME_DIR` and related keys), from other dirty worktree files; no W023 test failed.
- `git diff --check` and targeted `py_compile` passed; component boundary check reports only `quant-research`。
- 新一轮 focused overlay：12 tests passed（scan timing、watchlist service、runtime adapter）。全量 overlay 运行 3292 tests，仍只有工作区其他会话 `local_spool_*` 读取未登记环境变量的既有 settings failure；frontend `npm run typecheck && npm run build` 通过。
- health handoff 变更后的 focused overlay：13 tests passed（含 runtime health-task join）。
- health handoff 后的完整 overlay：3293 tests 运行，仍只有同一项 `test_settings.RegistryTests.test_every_key_read_in_the_app_is_declared` 失败；未出现 scan/runtime 新失败。
- `scripts/release plan 7fa7c3f9` 只读结果：owner 因此前容量提交中 2 个 compose/env 路径仍被归类为 FULL（owner schema 已在 `20261015_ow0122`，窗口允许）；edge dashboard/adapter 也因目标范围内其他会话路径被列出。未执行任何 `--apply`，不能把该计划当作已发布。

## Open questions

- watchlist 规则使用 capture 后的内存行情，但其 point-in-time local inputs 和 event state 仍在 `persist_scan_signals` 的同步事务中读取；B093 负责拆分前的测量和协议。
- capture 的精确 membership、board cache 使用 bounded async read pool；signal persistence 使用 bounded database executor；当前没有提高任一上限。
- 主 watchlist signals 必须等 durable receipt 和 event write 完成后才可投递；xiaojie 可先做纯评估，但其 `scan_id` 写入等 receipt barrier，随后与投递并行，仍在响应前 join。

## Resume here

先读 `intraday_*capture*`、`watchlist_scan*`、`strategy_pattern_mining*`、`runtime_tasks.py` 和对应测试，沿一条实时行情从 provider 到策略输出追踪。
