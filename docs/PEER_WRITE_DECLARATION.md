# Peer 写入申报（2026-09-20）

这是 peer 侧对 owner `stock_peer` 角色写入行为的申报，不是权限申请，也不代表这些表都在每次运行中产生行。申报以代码中的 repository/scheduler 调用为准，owner 可用 `pg_stat_statements`、`pg_stat_activity` 和写入审计按表对账。所有写入都是研究证据、同步状态或运行协调数据；不会把供应商响应直接写入交易执行阈值或订单路径。

## 机器可读清单

| 表 | 典型频率/窗口 | 用途 | 写入者/模式 |
|---|---|---|---|
| `quant.runtime_leases` | 每 2 分钟续租，任务开始/结束 | 跨容器任务互斥与故障恢复 | API、scheduler；UPSERT/UPDATE |
| `quant.provider_health` | 每次 provider capability 请求 | 熔断、延迟、最近成功/失败证据 | API、scheduler；UPSERT |
| `quant.provider_api_capabilities` | provider 探针/配置变更 | 声明与实测能力分离 | scheduler；UPSERT |
| `quant.fetch_runs` | 每次同步、补数、重试 | 请求幂等账本、状态与错误 | scheduler；INSERT/UPDATE |
| `quant.raw_market_observations` | 盘中采集；历史补数在 04:00–08:00 | 原始市场观测与 lineage | API、scheduler；INSERT（按唯一键去重） |
| `quant.tushare_raw_records` | Tushare catalog/backfill；04:00–08:00 | 原始供应商行的可重放证据 | scheduler；批量 INSERT |
| `quant.instruments` | 启动/宇宙同步/补数 | 证券主数据投影 | scheduler；按 symbol 排序批量 UPSERT |
| `quant.instrument_lifecycle_evidence` | 宇宙同步/状态回填 | 上市、退市、ST 的 PIT 证据 | scheduler；INSERT 去重 |
| `quant.market_trade_calendar` | 日历同步/缺口补齐 | 交易日与可用性边界 | scheduler；UPSERT |
| `quant.market_bars_daily` | 日线同步；批量窗口 | 非 owner 兼容环境的行情投影 | scheduler；UPSERT |
| `quant.canonical_bars_daily` | canonical 化/补数；批量窗口 | 研究价格投影，缺因子时 fail-closed | scheduler；UPSERT |
| `quant.daily_adjustment_factors` | 因子同步/缺口补齐；批量窗口 | 复权因子证据；语义位于 `raw.factor_semantics` | scheduler；UPSERT，仅真实因子 |
| `quant.daily_fundamentals` | 收盘后/补数 | 基本面研究证据 | scheduler；批量 UPSERT |
| `quant.daily_trade_limits` | 收盘后/补数 | 涨跌停约束证据 | scheduler；批量 UPSERT |
| `quant.security_suspensions` | 收盘后/补数 | 停复牌证据 | scheduler；批量 UPSERT |
| `quant.data_quality_issues` | 每次质量门失败/退役 | 可追溯的阻断与修复问题 | API、scheduler；INSERT/UPDATE |
| `quant.data_snapshots` | 每次研究快照 finalize | 研究窗口 manifest/hash | scheduler；INSERT/UPDATE |
| `quant.recommendation_runs` / `quant.recommendations` | 盘后研究运行 | 研究候选/观察/不交易结果 | scheduler；INSERT；`live_effect=none` |
| `quant.analyst_signals` / `quant.analyst_scorecards` | 归档导入/日周复盘 | 分析师证据与描述性评估 | scheduler；INSERT/UPSERT |
| `quant.sector_*` | 收盘后板块同步/回填 | 点时板块与成员研究证据 | scheduler；批量 UPSERT |
| `quant.intraday_*` | 盘中 2–30 秒/分钟；只保留有界窗口 | 行情、板块、规则输入与信号 replay 证据 | API/intraday runtime；INSERT/UPSERT |
| `quant.watchlist_*` | 盘中扫描、盘后结算 | watchlist 候选、信号、结果 | API、scheduler；INSERT/UPDATE |
| `quant.raw_market_observations`（`provider_key='teacher_review'`） | 每期复盘导入一次；盘后刷新一次 | 老师复盘策略包与盘后结算存档（`teacher_review_pack` / `teacher_review_settlement`） | API、scheduler；INSERT（按唯一键去重） |
| `quant.intraday_watchlists`（`metadata.teacher_review`） | 导入与盘后刷新 | 次日计划合并进观察池行；超容量不写入 | API、scheduler；表锁下 INSERT/UPDATE，过期停用自有行 |
| `quant.raw_market_observations`（`provider_key='quant_scan'`，`capability='watch_scan_tape'`） | 每轮盯盘扫描一行（上午 5 秒） | 全部观察池股票的紧凑逐轮记录（价/高开低/累计量额/均价/量比/换手/封板/分钟指标/板块广度/本轮信号），供复盘与模式归纳 | API；INSERT（按唯一键去重）；原始报价行与规则输入快照改为每股 30 秒抽样、出信号时必存 |
| `quant.raw_market_observations`（`provider_key='quant_scan'`，`capability='watch_daily_review'`） | 盘后刷新一次（或手动 POST /api/v1/watch-reviews/run） | 观察池每只股票的当日复盘（走势/分时路径/逐轮盯盘记录中的竞价、封板开合、量比与板块广度变化/涨停行为/所属行业关系/近期与历史/信号/模式标签）与当日汇总 | API、scheduler；INSERT（按唯一键去重） |
| `quant.intraday_minute_sessions`（`source_name='longhu_intraday_minutes'`） | 盘后复盘一次（当日） | 观察池每只股票当日完整 1 分钟线（09:30–15:00），补齐盘末剖面 36 只上限与尾盘分钟 | scheduler；UPSERT（按 symbol/日期/分钟/来源），不做删除 |
| `quant.raw_archive_offsets` / `quant.raw_archive_batches` | 仅启用 overflow 时；重试安全 | 原始观测离线归档游标与 ACK | archive adapter；事务内 UPDATE/INSERT |

## 运行约束

- 批量迁移、补数、COPY、全量回填只走 `db-tunnel:5433`，并在 `04:00–08:00 Asia/Shanghai`；盘中会话走 `5432`。
- owner 发布公告从 `quant.owner_deploy_events` 读取，不把发布中的短暂断线记成业务故障；若存在未终结的 `starting` 且 `surfaces.shared_tunnel=true`，暂停写入并等待同一 `deploy_id` 的 `completed/failed` 行。
- `daily_adjustment_factors` 不写恒等占位值；缺失日用“没有因子行”表达。因子能否进入研究价格由 `raw->>'factor_semantics'` 与 provider/正值检查决定。
- `quant.runtime_leases` 是预期的高频小写入；其余写入应有对应 scheduler/run ledger。异常写入由 owner `/api/v1/peer/errors` 与 peer ERROR 日志双向发现。
- 本申报不包含 owner 冷层表；`stock_cold` 中的关系是 owner 运维证据，`stock_peer` 不依赖也不写入。
- 热/冷分层：peer 在 `GET /api/v1/research/storage-tiering` 声明哪些证据是当日实时计算所需、各保留几个交易日在热层（`app/storage_tiering_policy.py`）。research scheduler 的 `storage_tiering_mover`（5433 批量通道，交易日 09:00–15:45 不运行）收盘后把已收盘交易日复制到 `stock_cold` 冷孪生表（`*_cold`），热窗口过后且冷副本主键与校验列（raw 为 `payload_sha256`）一致才从热层删除，期间两层重叠。**只有 owner 授予 `stock_peer` 冷孪生表 SELECT,INSERT 后才会动作**；未授权时只报告 `awaiting_owner_grant`。owner 也可以不授权、改由自己的任务按同一策略执行。

## 对账输出建议

每周按 `application_name`（`peer-quant-research`、`peer-quant-research-scheduler`）统计 `INSERT/UPDATE/DELETE` 的 relation，和本表的“典型写入者/用途”比对。新表第一次出现时先落为 `undeclared`，不自动阻断研究服务；确认用途后更新本文件和 release receipt。
