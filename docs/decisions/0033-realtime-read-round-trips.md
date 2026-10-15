# 0033 实时与研究读模型往返收敛

- 状态：生效
- 日期：2026-10-16

## 背景

实时扫描准备和研究看板读接口仍有几处重复或串行的数据库往返。扫描在同一事务中已经生成纸面组合快照，却再次读取持仓和最新快照；市场事件分页为了返回 `total` 通常执行第二条 `count(*)`；策略形态页的 board 与龙虎榜上下文彼此独立，却沿同一条数据库桥串行执行。owner 数据库经远端隧道访问时，这些等待会抵消本地 CPU、内存和网络带宽带来的收益。

## 决定

1. 纸面组合快照可以在需要时把本次已读取的原始持仓行作为进程内返回值传给同一事务的后续读模型；这些行不进入持久化 payload，外部证据格式保持不变。扫描准备同时用一个标量查询读取 cash 与上一笔 equity。
2. 市场事件列表使用 `count(*) over()` 在非空页中携带总数并剥离内部列；超出末页的空页保留一次精确 `count(*)` 回退，因此分页契约不变。同步和异步读模型保持同一行为。
3. 策略形态页的 board context 与龙虎榜 context 通过现有有界数据库 runner 并行读取；各自的事务、超时和错误传播边界不变。
4. 为事件和策略形态读路径增加幂等索引迁移 `20261016_ow0123`。依赖该迁移的 owner 代码只能在 schema gate 确认库已到该版本后发布。
5. 实时扫描在 session/watchlist gate 通过后立即启动只读 board-flow freshness 读取，让它与行情抓取和精确 membership 读取重叠；任务仍由当前 scan 持有，在 join 前失败会取消，不形成 detached background work。
6. 最新扫描控制台读取增加迁移 `20261016_ow0127` 的 `(scan_id, severity DESC, created_at)` 索引，避免按最新 scan 读取信号时扫描整张事件表；持久化写入顺序和返回排序保持不变。
7. 最新扫描 endpoint 的 scan、signals 和 deliveries 通过一条 `LATEST_SCAN_SNAPSHOT_SQL` 返回 JSONB envelope，同步/异步读路径共用它；JSONB 只在读边界解码，空扫描仍返回三个空/空列表字段。
8. 市场量能复盘 endpoint 的当日分钟特征、近 20 日摘要、板块日流、板块结果和研究门禁通过一条 `MARKET_FLOW_SNAPSHOT_SQL` 返回 JSONB envelope；同步/异步读路径共用它，仍按请求日期取证据，不引入跨请求缓存。

## 后果

正常事件分页从两次 SQL 往返降为一次；只有 offset 超出结果集时才执行回退 count。扫描准备省去同一事务中的持仓和快照重复读取。两个独立的策略上下文读共享等待时间，慢的一支仍受原有 30 秒 runner 超时约束。索引会增加少量写入维护成本，需结合 owner 的真实执行计划和锁等待复核。

实时扫描的 board-flow 读不再等待 provider capture 完成；当行情抓取或 membership 读取较慢时，状态证据可以在同一等待窗口内完成。board 读取仍使用扫描时间点计算年龄，失败仍沿原有 scan failure path 传播，任务取消和重放边界不变。

最新扫描的信号读取只需要扫描目标 scan 的索引范围；本地 `EXPLAIN (ANALYZE)` 的执行时间由约 4.7 ms 降至约 0.2 ms，5 次实际查询探针的中位数由 5.587 ms 降至 0.769 ms。索引会增加每条信号写入的维护成本，owner 必须结合实际写入吞吐、锁等待和索引命中率复核。

最新扫描 JSONB 快照把数据库 statement count 从 3 降到 1；本地热身后的五次中位数约为 4.778 ms，原三条查询约为 4.203 ms，增加的本地 JSONB 组装成本预期由 owner 反向隧道少两次往返抵消。该收益必须在 owner 上线后用真实隧道延迟复测，不能把本地 CPU 微差当作线上结论。

市场量能快照把 statement count 从 5 降到 1；本地 compose PostgreSQL 的五次中位数约为 788.019 ms，旧五条查询约为 790.734 ms。结果保留了同一请求日期、500 条板块上限、20 条日摘要上限和完整结果汇总；本地 JSONB 聚合略有开销，但 owner 经反向隧道可少四次往返，需上线后按真实隧道延迟和锁等待复测。

这些改动只优化研究读模型、证据落盘和纸面扫描准备，不改变策略资格、point-in-time 边界、事件幂等顺序、provider 并发上限或任何 live effect。

## 怎样保证

- `test_intraday_scan_repository.py` 和 `test_paper_execution.py` 验证复用行不会改变持仓状态和持久化 payload。
- `test_market_event_reads.py` 覆盖非空页单往返和空页精确计数回退。
- `test_strategy_pattern_read_model.py` 用互相等待的 runner 验证两个上下文确实重叠，避免回归成串行调用。
- `test_intraday_watchlist_scan_service.py` 用互相等待的 capture/board tasks 验证 board freshness 读取在行情抓取仍进行时已启动，并验证最终状态仍来自已 join 的结果。
- `test_scan_signal_read_index_migration.py` 验证最新扫描信号索引的迁移链和可逆 downgrade；本地 `EXPLAIN (ANALYZE, BUFFERS)` 作为计划证据保存在工作记录中。
- `docs/work/probes/2026-10-16-latest-scan-read.py` 在本地临时建立并删除等价索引，记录 5 次查询中位数；输出不代表 owner 隧道或交易日高峰。
- `docs/work/probes/2026-10-16-latest-scan-snapshot.py` 对比三条旧查询和一条 JSONB 快照，验证返回行数与 statement count；本地测量包含 JSONB 组装成本。
- `docs/work/probes/2026-10-16-market-flow-snapshot.py` 对比五条旧查询和一条 JSONB 快照，验证返回计数与 statement count；本地测量包含 JSONB 聚合成本，不代表 owner 隧道收益。
- overlay 回归、`py_compile`、组件边界检查和 `git diff --check` 在提交前运行；owner 当前仍需先应用 `20261016_ow0123`，线上收益需发布后用真实慢事务、执行计划和隧道等待复测。
