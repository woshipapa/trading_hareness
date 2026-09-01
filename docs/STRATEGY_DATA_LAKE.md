# Strategy evidence lake

The Baidu Netdisk personal application is a large immutable evidence tier, not
the live strategy database. PostgreSQL remains the hot point-in-time serving
store; local Parquet/DuckDB is the warm analytical cache; Netdisk is the durable
source, replay and experiment archive.

## Layered model

| Layer | Contents | Local policy | Cloud representation |
|---|---|---|---|
| L0 raw | provider payloads, analyst source reports/messages | short bounded cache | lossless `jsonl.zst` |
| L1 canonical | daily/minute bars, controls, order book, sector membership | frequently reused daily data stays hot; high-frequency data ages to warm | typed `parquet` with Zstandard |
| L2 features | versioned features, scan envelopes and frozen rule inputs | retain the current validation window | typed `parquet` with input/source hashes |
| L3 signals | candidates, rejected controls and state transitions | compact decision ledger stays hot | typed `parquet` by strategy/model |
| L4 outcomes | 5m/15m/30m/close/next-session, MFE/MAE and feasibility | stays hot for calibration | typed `parquet` by horizon/model |
| L5 reviews | experiments, ablations, analyst/strategy reviews | summaries stay hot | immutable run bundle plus metrics |
| L6 catalog | schemas, manifests, provenance, quality and restore receipts | local catalog | small JSON manifests |

The machine-readable placement contract lives in
`quant-service/app/platform/data_product_registry.py`. A dataset referenced by
a strategy or runtime task must be registered there before release validation
accepts it.

## Stable path layout

Use ASCII path segments below the existing application root so tooling does not
depend on UI-localized names:

```text
/apps/股票paper存储/quant-lake/v1/
  raw/dataset=<key>/provider=<provider>/available_date=YYYY-MM-DD/hour=HH/
  canonical/dataset=<key>/exchange_date=YYYY-MM-DD/symbol_bucket=NN/
  features/dataset=<key>/model_version=<version>/exchange_date=YYYY-MM-DD/
  strategies/strategy_key=<key>/model_version=<version>/run_date=YYYY-MM-DD/
  outcomes/strategy_key=<key>/model_version=<version>/horizon=<h>/exchange_date=YYYY-MM-DD/
  analyst/analyst_id=<id>/available_date=YYYY-MM-DD/
  manifests/dataset=<key>/schema_version=<version>/partition_id=<id>.json
```

Objects are immutable. A writer uploads to a unique staging prefix, verifies
bytes/hash, writes the final manifest last, then commits the manifest identity
to PostgreSQL. Existing objects are not accepted merely because their byte
length matches. Restore always targets a staging schema or warm cache and must
verify schema version, SHA-256, row count, min/max time and point-in-time
availability fields.

## Strategy improvements enabled by retained evidence

## 龙头研究的数据规模与分层预算

当前的 `dragon-leader-score-v1`（涨停/连板/地天板盘后影子评分）和
`ten-day-leader-vwap-coordination-shadow-v1`（十日榜、板块外力、VWAP
盘中确认）共享以下输入闭包。评分可以在证据不完整时返回
`partial_shadow`，但不能把缺失字段当作零分：

| 证据组 | 最小可运行规模 | 建议研究规模 | 放置 |
|---|---:|---:|---|
| 全 A 日线、复权、涨跌停、停复牌、ST | 同日 ≥5,000 只；十日排名至少 11 个交易日 | 120 个交易日以上，日线至少 3 年 | L1 热；完整历史 L2/L3 |
| 涨停池、开板/回封、跌停与连板梯队 | 每个交易日两个来源的全量去重并集（通常约 50–300 只） | 保留全部落选行和来源响应，至少 2 年 | L0 原始 + L1 事件 |
| 点时板块成员与板块资金流 | 每个候选必须有 `known_at` 的精确成员映射 | 所有候选及 Top30 同伴，跨 120 个交易日 | L1 PIT；L3 快照 |
| 集合竞价与开盘证据 | 全 A 每日约 5,500 行 | 全 A + 候选的 2 年历史 | L0 原始，L2 Parquet |
| 1 分钟候选路径 | 每日涨停池全量（约 100–300 只） | 候选加 1:2 匹配负样本，至少 120 个交易日 | L2 温；L3/L4 结果 |
| 结果与可成交性 | 每个候选/对照 5m、15m、30m、收盘、次日 | 每个子策略 ≥100 正样本 + ≥100 对照，跨 ≥120 交易日 | L4/L5 热摘要，原始路径 L3 |

按 250 个交易日/年估算，日线约 138 万行/年；全 A 集合竞价约 138
万行/年；100–300 只涨停池股票的 1 分钟路径约 600–1,800 万行/年，
加 1:2 对照后约 1,800–5,400 万行/年。Zstandard Parquet 的三年研究湖
大致为 15–40GB（实际以列宽和供应商字段为准），百度网盘容量足够；这
些增长只增加回放覆盖，不改变任何实盘阈值。

2026-09-01 的现场盘点：`market_bars_minute` 为 696,739 行/1,301 只
股票/56 个交易日，`ten_day_leader_rotation_intraday_observations` 为
17,240 行/177 只股票/7 个交易日；最新 `strategy_pattern_runs` 是
2026-08-25 且状态 `blocked`。因此当前龙头评分只能作为 `partial_shadow`，
不能宣称已经完成龙头策略验证。下一次回填应优先补齐全量涨停池、集合竞价、
开板/回封事件和候选+对照分钟路径，而不是继续扩大盘口 Level-2。

分层调整为：L0 只保留原始响应和当日工作集；L1 保留全 A 日线/控制面、
精确 PIT 成员和紧凑 gold 结果；L2 保存候选与匹配对照的 Zstandard
Parquet/DuckDB 工作集；L3 以不可变分区保存完整原始事件、分钟路径和每次
落选原因；L4/L5 只保留可校准的结果、实验和复盘摘要；L6 manifest 必须
记录 schema、SHA-256、行数、时间范围和 `strategy_available_at`。盘中路径
仍只读 L0/L1，绝不等待百度冷读。

1. Intraday watchlist confirmation: retain every accepted and rejected
   snapshot, provider coverage, time-of-day state and 5m/15m/30m/close/next
   outcomes. This supplies unbiased controls for walk-forward timing
   challengers instead of learning only from fired alerts.
2. Ten-day leader rotation: retain exact as-known-at sector membership, all
   top-30 peers, minute VWAP/volume paths, one-word-board feasibility and next
   session MFE/MAE. This can test sector diffusion and leader lifecycle without
   hindsight mappings.
3. Xiaojie leader flow: preserve each qualitative source span beside the
   structured snapshot, mode and parameter hash. Evaluate modes separately by
   market regime and record every tried parameter set in the experiment ledger.
4. Post-close candidates and minute patterns: archive the full eligible
   universe and rejected controls, corporate-action inputs and minute paths.
   Use purged walk-forward splits and selection-bias-corrected metrics; never
   fit on the same dates used for reporting.
5. Analyst overlays: join only at `strategy_available_at`; compare market-only
   and bounded analyst-shadow legs. `stated_at` remains source/replay evidence
   and cannot make a claim available earlier.
6. Market regimes: retain breadth, board flows, previous-limit premium, index
   minute paths and style/sector transitions. Report every strategy outcome by
   regime instead of using one unconditional threshold across all sessions.

Large storage increases sample coverage, not decision authority. Every new
variant remains `live_effect=none`; promotion still requires independent days,
purged walk-forward evaluation, costs/feasibility, honest trial counts and a
separate promotion record.
