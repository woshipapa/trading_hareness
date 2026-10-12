# 0009 全 A 分钟截面：一分钟存一行，最新截面走内存

- 状态：生效（2026-10-09 操作者批准；第 1–3 步已实现，默认只写分钟文档 `LEVEL1_STORAGE=document`；逐只行保留为历史兼容，存量转换/删除仍是后续步骤）
- 日期：2026-10-09

## 背景

### 链路现状

全 A 分钟截面从 Fuyao 取，每分钟分 2 页、共约 5,558 只，进 30 s 共享缓存。

**在内存里直接使用的：** 盘中扫描、板块下钻、涨停锚点、小杰、盘面雷达。

**写库：** level1 采集默认把这份截面写成一行分钟文档
（`fuyao_ths` / `a_share_minute_cross_section`，`symbol` 为空）。逐只行
（`a_share_prices_snapshot`）只由显式的兼容模式继续写入。

**写进去之后读回来的地方（2026-10-12 逐处核对，见 `docs/archive/2026-10-12-level1-snapshot-readers.md`）：**

- 最新全 A 快照接口：只要最近一分钟；
- 战法卡片：按股票取当日最新和 09:25 的值；
- 分时情绪温度：当日的分钟文档；
- 盘后分钟面板导出（`minute_cross_section_export`）：一天的全部文档；
- 指标自检 `market.minute_documents`：只数文档的存在、间隔和体积。

逐只行的归档、分层和历史回放仍按旧 capability 工作；分钟文档 capability
尚未加入 tiering 或 overflow archive allowlist，当前留在热表。

### 2026-10-09 在 owner 上实测（只读）

| 项目 | 值 |
| --- | --- |
| `raw_market_observations` | 35 GB：表 29 GB、索引 7 GB，占整个库 57 GB 的 61% |
| 其中分钟截面 | 按 0.2% 抽样占 93% 的行；日期 09-07 至今，约 20 个交易日；约 27 GB |
| 每行体积 | `normalized` 738 B + `payload` 948 B：同一份 JSON 存两列 |
| 每行内容 | JSON 原文 1.1 KB。其中 414 B 的 `snapshot_metadata` 每行重复；价格、涨幅、成交额、成交量顶层和 `raw` 各一份；代码有 `symbol`/`ts_code`/`thscode`/`ticker` 四份。有用的信息是每股约 10 个数 |
| 每交易日 | 约 130 万行、约 1.4 GB，外加 8 个索引的维护（唯一约束索引 4.3 GB） |
| 写入耗时 | 13:11 发布后，分钟采集的 35 次写事务全部超过 5 s，最长 53 s；市场事件写入 197 次里 70 次超过 5 s，最长 35 s |
| 连带影响 | 一行的交易日历读取慢到 18 s；控制台的读请求排队超过中继 15 s，显示“部分研究接口暂时不可用” |
| 维护 | 统计重置以来，该表的 autovacuum 和 autoanalyze 从未运行；对方的 `retention_policies` 四条全部关闭，也不覆盖这张表 |

数据库在 Windows 工作站上，owner 经反向 SSH 访问，往返约 52 ms。所以每多一次往返、每多写一 MB，都会实打实地拖慢所有读取。

## 决定（提议）

1. **一分钟一行。**
   - level1 采集改写 capability `a_share_minute_cross_section`：`symbol` 为空，`effective_at` 为分钟。
   - `normalized` 存列式文档 `{fields, symbols, columns:{price:[…], pct_change:[…], open, high, low, prev_close, volume, turnover, price_observed_at}, metadata}`。整分钟共用的元数据只存一次；`payload` 只留供应商批次信息，不再复制整份。
   - 估计每行 0.3–0.6 MB（未压缩的 JSONB），TOAST 压缩后 0.2–0.3 MB。每交易日约 240 行、约 90 MB，约为现在的 1/20–1/30；这些文档暂时留在热表。
   - **不需要 DDL**：沿用现有的 `(capability, symbol, effective_at)` 索引。文档 capability 尚未列入冷热分层和溢出归档，新增 retention allowlist 是后续工作；owner 库目前处在对方的 `20261008_0118` 上，我们新增迁移前必须先拿到它的源文件（见附录 C 2026-10-09 记录）。
2. **读侧只走一个仓库模块 `minute_cross_section`。**
   - 读取函数：`latest(day)`、`latest_async()`、`first_between(start, end)`、`day_documents(day)`、`recent()`（进程内存），以及把文档还原成行的 `rows_of`、`rows_for`。
   - 最新快照接口、战法卡片、分时情绪温度、盘后分钟面板导出都从这里取。
   - 2026-10-09 之前的日子回退读旧的逐只行。
3. **最新截面走内存。** 采集进程保留最近一分钟的截面，最新快照接口优先从内存返回，数据库只用于冷启动和兜底。
4. **冷数据按天导出。**
   - 每日收盘后，把当日的分钟文档导出成 Parquet（zstd，代码 × 分钟的宽表，约 10–20 MB/天），作为研究和回测的面板数据。
   - 热库保留 20 个交易日（沿用 `HOT_SESSIONS`），之后由分层任务处理。
5. **清理存量。**
   - 切换后，收盘后分批把 09-07 起的逐只截面按天转成分钟文档；校验通过后，再分批删除旧行。
   - 用普通 VACUUM 回收为可复用空间，不用会锁表的 VACUUM FULL。
   - 请对方为这张表恢复 autovacuum，并跑一次 ANALYZE。
6. **读取保护。**
   - 中继研究读超时改为 30 s，盘面页错峰刷新：均已在 `bd74073` 完成。
   - 重的读接口加 60 s 服务端缓存，或改成盘后预计算：因子评估约 15 s，分析师技能约 7 s。

## 后果

- **写入**：每分钟从 5,558 行 × 1.7 KB 加 8 个索引的维护，变为 1 行约 0.2 MB，写事务预计降到 1 s 以内，耗时主要是传输。
- **增长**：每交易日从约 1.4 GB 降到约 90 MB；存量清理后，约 25 GB 变为可复用空间。
- **读取**：
  - 最新快照：从读 5,558 行并逐行解析 JSON，变为读内存或 1 行；
  - 战法卡片：从 2×N 次索引探测变为 2 行；
  - 雷达回放：一天 240 行。
- **研究**：分钟面板可以按“字段 × 代码 × 分钟”一次取出，供策略组合和派生指标。
- **代价**：取单只股票的分钟序列，要读整天的文档再从中抽取。不过 240 行、约 50 MB 在内存里切片，仍比现在的 240 次逐行查询快。

## 风险与回退

- **采集开关**：`LEVEL1_STORAGE=document|per_symbol|both`，默认 `document`；未知值也回退到 `document`。出问题时可显式切回逐只写或双写。
- **双读过渡**：读侧找不到分钟文档时回退到旧逐只行，旧行在分层到期前一直保留，作为缓冲。
- **读取方**：Step 1 审计了 52 个 `FROM quant.raw_market_observations` site；没有 `rows-only, current day` reader。最新快照和战法卡片已 document-first，逐只行只保留历史 fallback。
- **发布**：按规定的窗口发布，并带回滚点。

## 分步

1. **无 DDL，约 1 天。**
   - 分钟文档写入，加采集开关；
   - `minute_cross_section` 模块，迁移三处读取方；
   - 最新截面走内存；
   - 单元测试和真实 Postgres 测试。
2. **收盘后执行。** 存量转换、删除脚本（分批、可续跑、只在收盘后运行），并与对方协调 VACUUM 和 ANALYZE。
3. **研究面板和接口提速。** 每日 Parquet 导出、研究面板接口、重读接口的缓存或预计算。
4. **可选。** 需要 DDL，要等与对方的迁移链对齐之后：专用分区表，用二进制压缩矩阵（float32 + zstd）进一步缩小。

## 实现（2026-10-09）

- 第 1 步：`app/minute_cross_section.py`。包含分钟文档、`LEVEL1_STORAGE` 开关、最新截面走内存、最新快照和战法卡片的文档读取；见 `6bd729b`。
- 第 2 步：`python -m app.minute_cross_section_backfill convert|verify|delete|radar --from --to [--apply]`。只在安全时段运行，删除前必须校验通过；见 `7a5b0a1`。
- 第 3 步：盘后阶段 `minute_panel_export` 导出 Parquet，加上 `GET /api/v1/market/minute-panel`，并给因子评估、分析师技能的读接口加 120 s 缓存；见 `848b48b`。
- 指标健康新增 `market.minute_documents`，检查存在、连续性和单份体积；采集结果记录逐只写入与文档写入各自的耗时。

## 怎样保证

- **体积**：采集结果记录每分钟写入的行数和字节数，并接入指标健康。
- **数据面检查**：指标健康新增两项：分钟截面每分钟的行数必须为 1，写事务耗时不超过 5 s。
- **逐只写法**：只能通过开关显式选择，测试断言未设置或未知值时默认是文档写法。
