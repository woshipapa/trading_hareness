# 数据分层协议(Data Tiering Protocol)

> **当前生产边界（2026-09-21）**：实时数据面在远端 owner/peer；本地工作站只
> 做分析、回放和远端证据读取。本文件中旧的“本地 edge writer / L0”描述仅用于
> 历史归档和恢复演练，不得据此重新启用本地实时采集。owner 热/冷物理布局以
> [`OWNER_DATABASE_STORAGE.md`](OWNER_DATABASE_STORAGE.md) 和远端机器契约为准。

状态:本地四层协议已实施并实测(2026-08-31)；owner Windows 热/冷切换契约见
`OWNER_DATABASE_STORAGE.md`（2026-09-19）。本文是四层存储的放置规范:哪类数据放哪层、
保留多久、怎么流动。所有延迟与容量数字来自本机实测,不是估计。

适用范围:远端 `intraday_edge`、远端 `research` scheduler，以及本地工作站的
只读分析/回放缓存。全部
行情、证据与研究数据。分析师文本/媒体归档(47 stock-reports :18081)自成体系,
不在本协议内。

## Owner PostgreSQL cutover (2026-09-19)

Owner 的 PostgreSQL 热目录为 `F:\StockPlatformDB\postgresql16`。2026-09-20
owner clarification 明确：冷层只承载证据/审计表，peer 不依赖市场数据的
`_cold` 孪生表；`legacy_source_records` 仍在 owner 的 `stock_cold` 表空间。
peer 不执行 DDL/迁移/分层/备份，本地 `/api/v1/research/storage-tiers` 只报告
实际运行时 schema 与 lineage，不代表 owner 侧存在同名接口。

访问分两条互不重启的 SSH 通道：盘中 `db-tunnel:5432`，批量 COPY/回填/备份/全量
回放走同一个 `db-tunnel:5433` forward（owner reverse `15433 -> 55432`）。兼容性的
`db-batch-tunnel` sidecar 不作为默认 scheduler 路由。任何研究窗口都要
保留 `available_at`/`strategy_available_at`，冷表只是行的物理位置变化，不能改变
点时语义。

机器合同位于 `app/platform/data_product_registry.py`，但这些本地数据产品
声明不再作为 owner 生产启动门。peer 运行时只校验
`canonical_bars_daily` 与 `daily_adjustment_factors` 的真实列，因子语义从
`raw->>'factor_semantics'` 或 Longhu method 计算；缺因子行即缺失并 fail-closed。

## 1. 四层定义

| 层 | 物理位置 | 介质与角色 | 实测延迟 | 容量约束 |
|---|---|---|---|---|
| **L0 edge-hot（历史）** | 47 PG `quant_intraday_edge` | 已退役的历史实时工作集，仅供恢复/归档核对 | 本机毫秒 | 不得重新启用为生产 writer |
| **L1 research-hot（本地分析缓存）** | 本地 Docker PG(`n8n` 库 `quant` schema) | 分析/API/回放缓存，不是生产 system of record | 点查毫秒 | 软上限 36GiB；水位只影响本地分析，不控制远端采集 |
| **L2 warm** | 本地 `~/marketdata/`(parquet + DuckDB catalog) | 分析/回测工作集,列式扫描 | **26–34ms**(单票全历史) | 本地盘(118G 空闲),按需增长 |
| **L3 cold** | 百度网盘 12T `/apps/股票paper存储/` | 归档+异地容灾;**parquet 可 Range 就地查询** | 单票预取 **1.9s**;随机 seek ~1s/次 | 12T,当前用 ~5GB |

L3 的关键实测:dlink 支持 HTTP Range(206 + 正确 Content-Range),读 2101 行仅
3–4 次请求、不必下载整文件;≤8MB 的文件一次预取更快(`PanFile` 已内建)。

## 2. 五条协议规则

**P1 决策热路径只碰 hot。** 盘中 30 秒扫描周期内的一切判定只读 L0 的当场
数据与 L1 的因子快照,禁止触达 L2/L3(1.9s 的冷读会吃掉整个扫描预算)。

**P2 分析扫描只打 L2。** 回测、因子研究、跨年扫描一律读 parquet/DuckDB,
不对 L1 PG 发大扫描——L1 要服务 API 与 30 分钟盘后管线,被全表扫描拖慢
就是生产事故。需要更老的数据:从 L3 整分区 pull 到 L2 再算,不在网盘上跑循环。

**P3 L3 先验证后删除(verify-then-prune)。** 任何数据离开 L1 前:导出 →
上传 → **从网盘读回核对行数** → 登记 catalog(`fs_id`+sha256)→ 才允许
DELETE;删除后 VACUUM FULL 归还磁盘。敏感数据(全库 dump、凭据、私域消息)
gpg 加密后上传;行情 parquet 明文(加密流无法 Range 就地查)。
catalog(`~/marketdata/catalog/catalog.duckdb` 的 `partitions` 表)是 L3 的
唯一索引——**没登记等于没归档**。

**P4 保留窗口由统计门槛决定,不由磁盘决定。** 正式验证需要 60 交易日 +
200 个成熟信号(`POLICY_MIN_*`),所以证据保留 = 门槛 × 安全系数(≥365 天)。
磁盘吃紧的正确动作是归档到 L3,不是缩窗——缩窗等于销毁未来的验证资格。

**P5 point-in-time 字段随数据走所有层。** `available_at` /
`strategy_available_at` 必须进入每个 parquet 分区;从 L3 回灌的数据凭这些
字段回放,不引入未来函数。`stated_at` 是复盘证据,永远不是策略可见时间。

## 3. 盘中(intraday_edge) placement（当前数据面在远端）

本节的实时数据由远端 owner/peer 的 `intraday_edge` 产生并写入 owner
PostgreSQL；L0/L1 列只描述证据复制或本地分析缓存，不表示本地工作站运行
provider 轮询。远端 writer 停止时，本地不得自行接管，必须按 owner 发布/恢复
流程处理。

盘中唯一策略 `intraday_watchlist_confirmation` 与 5 个实时循环的数据放置:

| 数据 | 产生 | L0(47) | L1(本地) | L3 | 保留依据 |
|---|---|---|---|---|---|
| 观察池 + 因子快照 | PUT watchlist 时 45 天 hydration | ✅ 工作集 | ✅ 系统记录 | — | 小而热,永久 |
| 30s 全 A 横截面 → `intraday_quote_observations` | 腾讯,每 30s | 活动窗口 | **90d** | ✅ 年度归档 | 盘中证据主体 |
| `intraday_rule_input_snapshots` | 每次规则评估 | 活动窗口 | **120d** | 年度归档 | 回放规则输入；以 `observed_at` 分区 |
| `intraday_signal_events` | 触发时 | 活动窗口 | **365d** | 年度归档 | 成熟度统计 |
| rt_min 复核(≤4 只/轮) | 超级 Tushare | 活动窗口 | 365d | — | 原始分钟证据 |
| 盘口快照(fast quote/order book) | 腾讯 | 活动窗口 | **180d** | — | 体积大、复用低 |
| 板块流 5min(board curve/rotation) | 东财 | 活动窗口 | **365d** | — | 板块共振依据 |

流动:L0 →(evidence journal,forced-command SSH,**每 120s** pull)→ L1;
L1 超窗 →(年度分区归档脚本)→ L3。journal 是投递日志,本地保留 30 天即可
(`EDGE_CHANGE_JOURNAL_RETENTION_DAYS=30`)。

盘中判定的输入闭包:当场横截面(L0)+ 观察池配置(L0)+ 日线因子快照(L1,
45 天窗口预热)。**没有任何盘中路径读 L2/L3。**

## 4. 盘后(research)placement

8 个研究策略的数据放置(按 `strategy_registry` 的 evidence_datasets):

| 数据 | L1 | L2 | L3 | 消费方 |
|---|---|---|---|---|
| `canonical_bars_daily`(3 年/280 万行) | owner NVMe 保留 365 天，旧行在 `_cold`；独立本地 research profile 可保留全量 | ✅ 年度分区 + **by_symbol 5662 只** | ✅ 双布局备份 | limit_up_continuation、全部日线因子 |
| `market_bars_minute`(56 天/47 万行,持续回填) | ✅ 全量,**不删**(最稀缺证据) | 按需导出 | ✅ 备份(20.5MB) | 分钟形态挖掘、ten_day VWAP、回放引擎 |
| `daily_fundamentals` / `trade_limits` / `adj_factor` / `security_suspensions` | owner NVMe 保留 365 天，旧行与 bars 原子进入各自 `_cold` | ✅ 年度分区 | ✅ | 控制平面、涨跌停/停牌判定、复权研究 |
| `tushare_raw_records`(raw blob) | **仅 90d**(217 万行) | — | ✅ 全历史 50 分区 | 42 处读多取最新;历史仅审计/重派生 |
| `raw_market_observations`(raw blob) | **仅 180d**(135 万行) | — | ✅ 全历史 4 分区 | 同上 |
| 龙虎榜/涨停列表/moneyflow | ✅ 全量(上游即上限) | 按需 | — | xiaojie、龙头轮动 |
| 候选/提案/信号/复盘(gold 层) | ✅ 永久(体积小) | — | 随全库加密备份 | API、看板、审计 |

研究访问模式:
- **单票研究**(如"某票符不符合潜龙出海"):L2 `by_symbol/<code>.parquet`
  26ms;本地缺失时 L3 冷读 1.9s。分钟级判定读 L1 `market_bars_minute`。
- **全市场横截面**:L2 年度分区(DuckDB 跨文件 SQL,2ms 级)。
- **长回测**:全部在 L2;更老数据 pull-from-L3 → L2 → 计算 → 结果(gold)写 L1。

## 5. 上游历史深度(硬上限,回填补不动)

| 数据 | 最早 | 深度 | 说明 |
|---|---|---|---|
| daily / daily_basic / stk_limit / adj_factor / suspend_d | 2023-08-15 | 3 年 | |
| index_daily / top_list / top_inst / limit_list_ths | 2024-08-15 | 2 年 | |
| moneyflow_cnt_ths / moneyflow_ind_ths | 2025-08-15 | **1 年封顶** | 套餐决定;sector 回填对更早日期恒失败 |
| stk_mins(历史 1 分钟线) | 约 2 年 | 单票单请求 | 只回填涨停池+基准(约 44 只/日),全市场不现实 |

含义:moneyflow 类策略最长回看 1 年(约 240 交易日,已够 60 日门槛);
分钟级回放的覆盖由回填批次决定,当前 2026-06 起。

## 6. 生命周期作业与工具

| 作业 | 现状 | 工具 |
|---|---|---|
| L0→L1 journal pull | owner API 路径；旧 edge pull 已退役 | `scripts/pull-intraday-edge-evidence.sh`（仅历史兼容，不应安装 LaunchAgent） |
| 盘后管线 | launchd 每 30min(沪时门禁) | `scripts/run-post-close-pipeline.sh` |
| owner 热→冷分层 | owner 每日 06:00；peer 只验收、绝不执行 | `trading-hareness-storage-tiers` + `/api/v1/research/storage-tiers` |
| L1→L2 日线导出 | 手动/待例行化 | `scripts/marketdata/export_pg_to_parquet.py` |
| L1→L3 证据归档 | 手动/待例行化 | `archive_and_prune.py`(--archive-only / --apply)、`archive_raw_records.py` |
| L2→L3 上传 | 手动 | `upload_parquet_to_pan.py`、`upload_by_symbol.py`(并发) |
| 全库加密备份 | 手动 | `backup_pg_to_pan.sh`(dump→zstd→gpg→1G 分片;口令 `~/.config/feishu-relay/pgbackup-passphrase.txt`,**丢失即备份作废**) |
| 分钟线回填 | 手动 | 容器内 `python -m app.minute_backfill_cli`(涨停池+基准,断点续跑) |
| 年度日线回填 | 手动 | `python -m app.annual_daily_backfill --skip-sector-events`(fetch_runs 检查点) |
| 冷读验证 | 抽查 | `verify_cold_read.py`、`query.py --cold` |

建议例行化(尚未做):每日收盘后 L1→L2 增量导出;每周 L1→L3 证据归档 +
加密全库备份;归档后 VACUUM。

## 7. 容量水位协议(L1)

- 预算:36 GiB 软上限,估算 = 库大小 × 1.35。
- **65% = 归档水位**:安排最大的 raw/证据表归档。
- **80% = 告警水位**:系统自动暂停非必要高频采集(已实测触发过)——
  到这里说明归档欠账了。
- 当前:18 GB 库 ≈ 67.8% 预算(VACUUM `raw_market_observations` 后更低)。

## 8. 网络与韧性纪律(实测教训)

- 网盘上传:分片必须重试(4 次指数退避);>1GB 文件用断点续传脚本,
  失败重跑自动跳过已传分片。
- 刚上传的文件 `filemetas` 有索引延迟,取 dlink 必须重试(已内建)。
- 跨几十分区的长归档:单分区失败跳过并保留在 PG,不终止整轮。
- 47 只有 3.4G 内存:**绝不在 47 上 docker load/build 大镜像**,不把 47 当
  数据湖——edge 永远是瘦的。
