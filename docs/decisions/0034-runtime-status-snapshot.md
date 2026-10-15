# 0034 运行状态看板使用单次快照查询

- 状态：生效
- 日期：2026-10-16

## 背景

运行状态看板的只读接口在同一事务中串行读取 provider 健康、行情新鲜度、扫描、板块、投递、日终摘要和观察池等证据。此前虽已合并部分查询，仍要经过 12 次数据库往返；owner 数据库经远端隧道时，状态页本身会成为 10 秒级慢读。执行计划还显示 `intraday_rule_input_snapshots` 的 schema 版本统计会反复解析大型 `inputs` JSONB。

## 决定

把整组状态证据收敛为一次只读 `RUNTIME_STATUS_SNAPSHOT_SQL`，在 PostgreSQL 的同一快照内返回 12 个 JSONB envelope 行。Python 侧只恢复 envelope、分区键和时间字段；不跨请求缓存旧证据，sync/async 路径使用同一 SQL 和同一结果组装函数。

schema 版本计数拆成可索引的独立 `count(*)` 子查询，并新增迁移 `20261016_ow0124` 的 v1/v2 partial indexes；行情来源聚合再由 `20261016_ow0125` 增加 source-leading index。总行数和最新时间仍精确读取，未知 schema 版本不会被静默归入 v2。owner 必须先通过 `20261016_ow0125` schema gate，才能发布依赖这些迁移的代码。

## 后果

状态接口的数据库 statement count 从 12 降为 1，仍只占用一个读事务和一个连接。JSONB envelope 会把 PostgreSQL 时间值暂时编码为字符串，组装函数只对约定的顶层时间字段恢复带时区 `datetime`，因此年龄计算、故障状态和前端证据格式保持不变。单次查询仍会扫描真实证据表；索引减少 JSONB 解析，但不把本地测试耗时当作 owner 隧道收益。

本地 compose 数据库探针在同一数据集上观察到：未使用 status indexes 时中位约 9.7 秒；应用等价 v1/v2 partial indexes 后约 0.79 秒；再加 source-leading quote index 后中位约 0.095 秒，statement count 始终为 1。该结果只证明本地执行计划和 statement count，线上必须在发布后按同一交易日复测。

## 怎样保证

- `test_platform_boundaries.py` 验证 sync/async 只执行一次语句、结果分区和待重试计数保持原契约。
- `test_rule_input_status_indexes_migration.py` 验证迁移链、两个 partial index 和可逆 downgrade。
- `test_runtime_quote_status_index_migration.py` 验证 quote status index 的迁移链和可逆 downgrade。
- `docs/work/probes/2026-10-16-runtime-status-snapshot.py` 是只读本地测量工具；输出不能替代 owner 的慢事务、锁等待和隧道速率证据。
- 该投影仍只读，不调用 provider、不改变策略阈值、不改变告警投递状态，也不改变 research-only/live-effect 边界。
