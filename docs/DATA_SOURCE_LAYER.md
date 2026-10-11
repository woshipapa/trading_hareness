# 数据源层（app/datasources）

更新：2026-10-11（第 3.1、6.1–6.2、7.1 节与第 10 节为通达信增补，代码事实取自 `6f691ad0`）；其余内容 2026-09-20。所有“实测”均指从 owner peer（47.110.79.189）出口、收盘后的只读探测与不落库干跑；当前工作树新增的策略能力 facade 已完成本地回归，远端重启回读待 peer 运维会话。**例外：第 3.1、7.1、10 节的通达信解码证据来自 Mac 出口（家宽），不是 owner 出口**，每条都指向 `scripts/data/` 或 `docs/archive/` 下的证据文件。

## 1. 目标与边界

数据源层把**每一个上游**（持牌网关、官方免费 API、公开网页、非官方协议、本地文件、自算）收在一个独立包里，
对外只暴露一套**能力（capability）词汇**。策略声明“我要 `limits.limit_up_pool`、`sector.membership`”，
由目录决定谁来供、按什么顺序回退、存在哪里——策略里不再出现供应商名。

三条硬规则（`tests/test_datasource_boundaries.py` 强制）：

1. `app/datasources/**` 只能引用传输/持久化/租约基础设施（`http_clients`、`public_market_repository`、
   `provider_health`、`runtime_leases`、`database`、`fuyao_provider` 等），**不能**引用策略、规则、路由或 `main.py`。
2. 策略代码不能 import `app.datasources.sources.*` / `collectors.*`，只能用能力（目录、解析器、存储位置）。
3. `contracts.py` / `catalog.py` 是纯声明，无 I/O，任何人（API、agent、部署脚本、测试）都能直接读。

## 2. 包结构

```text
app/datasources/
  contracts.py    Capability / DataSource / Binding / CapabilityRequest / QualityReceipt 类型
  catalog.py      所有数据源 × 能力 × 绑定（优先级、实测状态、历史深度、限额、落库位置）
  resolver.py     能力 → 数据：按目录优先级 + 健康门控 + 失败/空结果回退，结果带来源证明
  bindings.py     把本包适配器绑到能力上（统一每个能力的参数约定）
  http.py         有界重试的公开传输 + A 股代码规范化（拒绝指数/基金/板块代码）
  sources/        一个上游一个模块，只做 fetch + normalize
  derived/        自算指标（纯函数）：market_sentiment、tick_flow
  collectors/     采集编排：intraday（常驻）、post_close（盘后归档）
  storage.py      采集器需要的几个只读查询
  runtime.py      依赖装配 + 租约循环（进程内与独立部署共用）
  __main__.py     python -m app.datasources catalog | validate | collect
app/platform/strategy_data_needs.py   每个策略需要哪些能力与板块口径（不写供应商）
```

## 3. 能力目录（节选，全量用 `python -m app.datasources catalog`）

状态：`live_verified`（owner 运行时已有健康记录/落库证据）> `declared`（已探测、未经盘中验证）>
`dormant`（已实现但无定时调用）；`unsupported`（上游拒绝，保留以便复测）与 `retired`（主动下线）不参与解析。

| 能力 | 解析顺序（状态） | 说明 |
|---|---|---|
| `quote.all_a_snapshot` | fuyao_ths(LV) → akshare(Tencent spot, declared fallback) | Fuyao 是主源；AKShare 腾讯全 A 仅在 Fuyao 请求失败/熔断时补充研究覆盖，日期为会话推断且不提升决策资格；东财 clist 在 owner 出口被断连（unsupported） |
| `quote.watch_snapshot` | longhuvip(LV) → tencent_free(LV) → sina_free(LV) | 仅 longhu/腾讯带交易所时间戳可进决策 |
| `quote.order_book` | longhuvip 十档(LV) → tencent 五档(LV) | |
| `quote.valuation` | fuyao 估值(D) → tushare daily_basic(LV) | 盘后全 A 5553 只/15.7 秒 |
| `bars.daily` | tushare 超级GET/超级SDK/备用(LV)、longhu 合成(LV)、fuyao 10 年导出(D)、通达信本地 .day(D)、baostock/东财/akshare(dormant) | 兼容主源已下线；历史主源行只作 provenance，不再路由 |
| `bars.minute` | longhuvip(LV) → tushare rt_min(LV) → 通达信本地 .lc1/.lc5(D) → 腾讯(LV) | fuyao 无分钟 K |
| `ticks.session` | tdx_public(D) → tencent_free(D) | 通达信历史分笔与 pytdx 逐笔一致；方向与腾讯逐分钟 100% 对上 |
| `auction.history_0925` | tdx_public(D) | 历史每日 09:25 竞价成交 + 09:15-09:25 虚拟撮合曲线 |
| `auction.open/close_snapshot`、`auction.short_term_benchmark` | fuyao、longhu 早盘 | |
| `limits.limit_up_pool` / `broken_pool` / `limit_down_pool` / `ladder` | fuyao(LV/D) → eastmoney_ztb(D) | |
| `limits.seal_detail` | eastmoney_ztb(D) | 首/末封时间、封板资金、炸板次数、几天几板 |
| `limits.previous_limit_up` / `strong_pool` / `sub_new_pool` | eastmoney_ztb(D) | 东财只保留近期，每日归档 |
| `limits.anomaly_tape` | eastmoney_ztb(D) | 盘口异动，当日 7469 条/10 类 |
| `limits.stock_anomaly_reason` | fuyao(D) | AI 生成原因摘要，只作解释 |
| `sector.membership` | longhu 行业(LV)、**fuyao 概念/行业/地域(D)**；tushare ths_member 已停用（2026-10-08，只余历史） | fuyao 概念/行业/地域由 `ths_member_backfill` 每个交易日盘后分批刷新，只记成分变化 |
| `sector.index_quote` / `sector.anomaly` / `sector.flow_curve` | fuyao / eastmoney_ztb / eastmoney_free(LV) | |
| `flow.stock_daily` / `flow.watch_intraday` / `flow.tick_derived` | longhu 合成、tushare / 东财 / 自算分笔 | 自算为 L1 成交额阈值估算，非 L2 |
| `lhb.daily` / `lhb.seat_statistics` | fuyao(D) → akshare(dormant)；tushare 已停用（决策 0005） | fuyao 只有个股合计与游资合计，没有营业部席位明细和机构拆分，读侧如实报“不可得”；席位→游资映射无官方来源 |
| `attention.*` | 同花顺热榜/飙升榜/热榜历史（fuyao）、东财人气/飙升/个股一年历史 | 同花顺热榜无历史，只能从现在快照；东财可回填一年 |
| `news.flash` | 财联社(A/B/C 等级) → 金十(important) → 东财 7x24 → 同花顺 | 只挂供应商自带的关联个股，不从正文猜 |
| `events.investor_qa` | 巨潮互动易、上证e互动 | 以“回答公开时间”为事件时间 |
| `events.*`（解禁/增减持/股东户数/大宗/业绩预告/快报/回购/新股） | eastmoney_datacenter(D)，业绩类另有 tushare(LV) | 可得时间 = 东财入库时钟与采集时刻取早，绝不用仅日期的公告日 |
| `fundamentals.capital_changes` | tdx_public(D) → eastmoney_datacenter(D) | 历次股本变动，流通股本按日可读，无需逐只积累 |
| `fundamentals.margin` | eastmoney_datacenter(D) | 市场汇总默认开；个股明细默认关（约 4000 行/日） |
| `fund.nav` | fuyao(D) → 天天基金(D) | QDII 晚 1-2 天，按净值日生效、按采集时刻可得 |
| `derived.market_sentiment` | 自算（盘中 5 分钟 + 收盘） | 见第 5 节 |

### 3.1 通达信（TDX）绑定

通达信相关的来源有 5 个，`catalog.py` 里共 40 个绑定（基线 `6f691ad0`；现值见本节末的三种看法）。

| 来源 | 上游 | 说明 |
|---|---|---|
| `tdx_public` | 公开行情主站 :7709，legacy 命令；`LOGIN_ONE` 握手，按主机保留 `legacy_3` 回退 | 行情、K 线、证券列表、服务器文件（`zhb.zip` 等）、F10、分笔 |
| `tdx_mac` | MAC 0x12xx 主机 :7709 | 批量行情、涨跌停价、IOPV、板块目录与成分、K 线 |
| `tdx_ext` | 扩展行情 :7727 | 期货、港股、美股等；A 股决策路径不得使用 |
| `tdx_local` | owner 工作站的通达信客户端（vipdoc），CLI 导出为离线文件 | 日线、分钟线；依赖手工或定时的盘后下载 |
| `derived_tdx_limits` | 自算：`tdx_public` 的 `quote.all_a_snapshot` + `tdx_mac` 的 `limits.prices` | 涨停、炸板、跌停池，只有快照时刻的成员关系 |

**状态怎么读。** 5 个旧绑定是 `declared`：`tdx_public` 的 `ticks.session`、`auction.history_0925`、`fundamentals.capital_changes`，`tdx_local` 的 `bars.daily`、`bars.minute`。通达信集成新增的其余 35 个绑定**一律 `unsupported`**；40 个绑定的 `decision_eligible` 都是 `False`。新绑定的 `unsupported` 是证据门（还没有 owner 出口的探测与对账记录），不是上游拒绝；解析器不走这个状态，研究试读（第 6.2 节）可以读。

状态只经 `scripts/tdx-promote.py` 在已记录的证据上移动：`check` 把探测写成 `scripts/data/tdx_promote_*.json`，`apply` 把 `catalog.py` 里该绑定的状态记号改一格（`UNSUPPORTED` → `DECLARED` → `LIVE_VERIFIED`）。升级就是 `catalog.py` 里一个记号的改动，单独提交；`decision_eligible` 不在同一提交里动。门条件见第 7.1 节。

下表按能力排序，优先级数字小者先试（只对可解析状态有意义）；“返回”和“主要限制”取自 catalog 的 `notes` 与 BindingSpec，价格、单位的证据见第 10 节。

| 能力 | 来源 | 优先级 | 状态 | 返回 | 主要限制 |
|---|---|---|---|---|---|
| `auction.history_0925` | `tdx_public` | 20 | `declared` | 历史每日 09:25 竞价成交与 09:15–09:25 虚拟撮合曲线 | 旧绑定；近期任意交易日 |
| `bars.daily` | `tdx_public` | 65 | `unsupported` | legacy 0x052d category 9 日线；volume 为手，amount 为元；每页 800 根 | `LOGIN_ONE`；价格 /1000；可得时间 = 响应接收时间 |
| `bars.daily` | `tdx_mac` | 70 | `unsupported` | MAC 0x122e 日线（period 4）；`unit_factors` volume ×0.01 | 日线 volume 是 float32 的股，约 7 位有效数字 |
| `bars.daily` | `tdx_local` | 35 | `declared` | 客户端 `.day` 日线，下载过的全部历史 | owner 工作站 CLI 导出的离线文件，不直接写 canonical |
| `bars.index_daily` | `tdx_public` | 65 | `unsupported` | 指数、板块日线（0x052d category 9 的指数布局）；canonical 只映射 open、close、amount，high、low、volume_raw、up_count、down_count 留作源字段 | count 为 1..800；`880005` 等非普通指数不进；volume_raw 单位未知 |
| `bars.minute` | `tdx_public` | 65 | `unsupported` | legacy 0x052d category 8 一分钟线；volume 为股，amount 为元；每页 800 根 | 价格 /1000；可得时间 = 响应接收时间 |
| `bars.minute` | `tdx_mac` | 70 | `unsupported` | MAC 0x122e 一分钟线（period 8） | 只给日期加当日秒数，适配器组成 Asia/Shanghai 的 bar_time；可得时间 = 响应接收时间；声明了对腾讯分钟线 close、volume 的容差 |
| `bars.minute` | `tdx_local` | 35 | `declared` | 客户端 `.lc1/.lc5` 分钟线，离线导入 `market_bars_minute` | 客户端保留多久就有多久 |
| `breadth.index_daily` | `tdx_public` | 65 | `unsupported` | 指数成分股的上涨、下跌家数（按日） | 与 `bars.index_daily` 共用一次请求；成分股口径，不得与全 A 快照宽度混用 |
| `context.bars_daily` | `tdx_ext` | 90 | `unsupported` | 扩展市场日线，含 open_interest、settlement | A 股决策路径不得使用；volume_raw 的单位随市场而异 |
| `context.instruments` | `tdx_ext` | 90 | `unsupported` | 扩展市场品种列表（market_id、code、name、category） | 同上 |
| `context.quote` | `tdx_ext` | 90 | `unsupported` | 扩展市场快照（price、pre_close、open、high、low、volume、amount、open_interest） | 同上；报价不带服务器时间，生效时间 = 采集时刻 |
| `events.ipo_calendar` | `tdx_public` | 80 | `unsupported` | `xgsg.cfg`、`othersg.cfg` 的申购行（symbol、apply_date、issue_price） | 只有申购日，不推断上市日；`zhb.zip` 成员 |
| `fund.iopv` | `tdx_mac` | 70 | `unsupported` | MAC 0x122b 位 0x27 的 IOPV（float32，元）和同一行的行情更新时间；批 80 | 不是 `fund.nav` 的披露净值；只收 ETF、LOF、基金类代码；位 0x24 不读 |
| `fundamentals.capital_changes` | `tdx_public` | 20 | `declared` | 除权除息与股本变迁 | 旧绑定；上市以来全部；与 pytdx 一致 |
| `fundamentals.company_profile` | `tdx_public` | 80 | `unsupported` | F10 公司资料的 GBK 文本（category、filename、content） | 可得时间 = 采集时刻 |
| `fundamentals.daily_basic` | `tdx_public` | 80 | `unsupported` | `tdxstat.cfg` 与 `tdxstat2.cfg` 按 (market, code, date) 连接的已确认列 | 20261009 快照；行日期不是采集日期；没有 canonical 映射的列只留在解析器的 raw 字段 |
| `fundamentals.financial_statements` | `tdx_public` | 80 | `unsupported` | F10 0x0010 财务摘要，仅最新一期 | 不带报告期（`report_period` 为 None），须与 `tipinfo` 的第 2、4 列关联才可用；绝不用 `updated_date` |
| `limits.limit_up_pool`、`limits.broken_pool`、`limits.limit_down_pool` | `derived_tdx_limits` | 90 | `unsupported` | 0x054b 快照与 MAC 0x122b 涨跌停价按 0.01 元整数分比较自算，三个池各一个绑定 | 仅当前会话快照；无首封、末封时间、原因、连板数、封单额；炸板池没有 open_times；无涨跌停价的证券不入池；永不替代供应商池作决策 |
| `limits.prices` | `tdx_mac` | 70 | `unsupported` | MAC 0x122b 位 0x20/0x21 的涨跌停价（元），交易日取位 0x13；批 80 | 无价格限制的证券两个限价都回 0.0；主板 ST 自 2026-07-06 起为 10 %；声明了对腾讯的容差 |
| `microstructure.auction_curve` | `tdx_public` | 90 | `unsupported` | 0x056a 集合竞价曲线 | 到 09:24:57 截止，没有 09:25 行；matched_raw、unmatched_raw 的单位未确认 |
| `microstructure.minute_series` | `tdx_public` | 90 | `unsupported` | 0x0fb4 指定日期的历史分时，每交易分钟一行；价格 /100，量为手 | 只收个股；第二个变长字段含义未知；覆盖率核对前不进决策 |
| `microstructure.top_board` | `tdx_public` | 90 | `unsupported` | 0x053f 排名榜 | 永不替代涨停池；覆盖率核对前不进决策 |
| `microstructure.unusual` | `tdx_public` | 90 | `unsupported` | 0x0563 异动事件 | 覆盖率核对前不进决策；`limits.anomaly_tape` 可在核对后作第二来源 |
| `microstructure.volume_profile` | `tdx_public` | 90 | `unsupported` | 0x051a 分价成交量（价格 /100，量为手，含 buy_lots、sell_lots） | 只收个股；覆盖率核对前不进决策 |
| `quote.all_a_snapshot` | `tdx_public` | 80 | `unsupported` | 0x054b 全 A L1 排序列表：price、pct_change、volume、turnover；每页 80 只 | volume 是手，canonical 股 ×100；price≤0 的行已滤掉；coverage 为 None（证券列表接入前分母未知）；ST 要用证券列表，不能用 0x054b 的名称 |
| `quote.index_overview` | `tdx_public` | 80 | `unsupported` | 0x051d 指数概况：OHLC（点数）、amount（元）、volume_raw、up_count、down_count | 涨跌家数是指数成分口径，与 0x054b 排序的宽度口径不同；volume_raw 单位未测 |
| `quote.order_book` | `tdx_public` | 80 | `unsupported` | 0x053e 五档：bid1..5、ask1..5（元），bid_vol、ask_vol（手）；批 80 | 只收主板、创业板、科创板、北交所个股，价格固定 /100；指数、板块、ETF、基金、可转债在联网前以 ValueError 拒绝；旧北交所代码按 920xxx 请求，行带 `source_symbol`；盘中延迟与新鲜度未测 |
| `quote.valuation` | `tdx_public` | 80 | `unsupported` | `tdxstat.cfg` 的已确认列；canonical 只映射 `pe_ttm`（第 9 列） | 20261009 快照；行日期不是采集日期 |
| `quote.watch_snapshot` | `tdx_mac` | 70 | `unsupported` | MAC 0x122b 批量行情：price、volume、amount、volume_ratio、turnover_rate、exchange_time；批 80 | 位 0x05 是手，canonical 股 ×100；回包按位置核对，代码不符的行丢弃并记 `code_mismatch`；声明了对腾讯 price、volume、amount 的容差 |
| `reference.instruments` | `tdx_public` | 19 | `unsupported` | 从 `reference.security_list` 派生的股票类基础信息（symbol、name、list_date、is_st） | TDX 不给上市日期，`list_date` 为 None |
| `reference.security_list` | `tdx_public` | 20 | `unsupported` | TDX 证券列表（symbol、market、code、name、instrument_type、decimal_point、pre_close、is_st、list_source、source_host） | `LOGIN_ONE`，一台确定的主机；深沪分页，北交所只取计数并用 `zhb.zip`；decimal_point 要逐只取 |
| `reference.trade_calendar` | `tdx_public` | 80 | `unsupported` | `needini.dat` 节假日加 `hqrule.dat` 规则 | 只有休市日，不编造开市日 |
| `sector.board_catalog` | `tdx_mac` | 70 | `unsupported` | MAC 板块目录（board_code、name、board_type），类型 0、1、3、4、5 | 类型 2 无板块、类型 6 与其余重复，不读 |
| `sector.index_quote` | `tdx_public` | 80 | `unsupported` | 0x053e 板块码（880xxx、881xxx）的 last_price、pre_close；pct_change 由适配器算出 | 价格固定 /100；volume_raw、amount_raw 单位无证据，保持 raw；买卖盘字段不是盘口，不读；只收板块码 |
| `sector.membership` | `tdx_mac` | 70 | `unsupported` | MAC 板块成分，`known_at` = 采集时刻（UTC） | board_type 取自 `sector.board_catalog` 的同一行；未知类型在联网前报错 |
| `sector.membership` | `tdx_public` | 80 | `unsupported` | `block_gn.dat` → `tdxzs3` 类型 4（概念）、`block_fg.dat` → 类型 5（风格、事件）的成分 | 不含 `block_zs`（指数成分表）和 `spblock`；只留 A 股权益并计数被拒的成员；不声称行业或地域；快照，`known_at` = 采集时刻 |
| `ticks.session` | `tdx_public` | 20 | `declared` | 历史分笔，带主动买卖方向 | 旧绑定；2000 笔/请求；方向与腾讯逐分钟 100 % 一致 |

看现值（上表是基线快照，状态、优先级、说明以现值为准）：

- `GET /api/v1/datasources/catalog`（可按 `source`、`category`、`status` 过滤；每个绑定带 `research_readable` 和 BindingSpec 全字段）与 `GET /api/v1/datasources/capabilities/{capability}`；
- 控制台页签“数据源 Doctor” → “数据源能力目录”卡片（来源筛选里有“TDX（全部通达信来源）”；`research_readable` 的绑定带“研究试读”按钮）；
- `python -m app.datasources catalog [--capability <键> | --source <来源>]`：不带参数输出整份目录；带参数列出该能力或该来源的绑定，含 `unsupported`。

## 4. 策略怎么用

```python
from app.datasources import CapabilityResolver, evidence_locations
from app.datasources.bindings import register_package_sources

resolver = register_package_sources(CapabilityResolver(), fuyao_fetch=fuyao_fetch)
result = await resolver.fetch("limits.limit_up_pool", trade_date=today)
result.rows, result.source, result.is_fallback, result.provenance()

# 读已落库证据时，用目录给出的位置而不是写死 source='fuyao_ths'：
evidence_locations("limits.limit_up_pool")
# [{'source': 'fuyao_ths', 'table': 'market_events', 'filter': {'event_type': 'limit_up_pool'}}, ...]
```

### 4.1 请求级质量门（2026-09-18）

目录状态只回答“这个源宣称能提供什么”，不能替代某一次读取的点时质量。需要把结果交给
复盘、影子策略或任何后续评分时，调用方可以附带 `CapabilityRequest`：

```python
from app.datasources import CapabilityRequest

request = CapabilityRequest(
    "quote.watch_snapshot", purpose="replay", as_of=observed_at,
    required_fields=("symbol", "price", "effective_at"),
    min_rows=1, max_age_seconds=15,
    require_live_verified=True, require_decision_eligible=True,
)
result = await resolver.fetch("quote.watch_snapshot", request=request, symbols=symbols)
```

适配器可以继续返回旧的 `list[dict]`，也可以返回 `CapabilityEvidence` 以提供覆盖率和
`effective_at`/`available_at` 时钟。解析器会对每次尝试生成 `QualityReceipt`（`complete`、
`partial`、`empty`、`stale`、`invalid` 或 `conflicted`）与确定性响应哈希；不满足必填字段、
最小行数、覆盖率或新鲜度时自动尝试下一个目录源，并把失败原因保留在 `attempts`。这使
“研究结果可展示”和“复盘输入可用”成为显式策略，而不是把非空响应误当成完整数据。

生产采集器已将盘口异动这条链路接入同一解析器；没有解析器的单元测试和旧调用仍保留直接
适配器回退。`purpose`、源状态和 `decision_eligible` 只进入证据 provenance，不会改变任何
live 阈值或订单路径。

新策略先在 `app/platform/strategy_data_needs.py` 登记所需能力（板块类需求同时登记口径）；测试会校验每个必需能力
至少有一个可解析的来源。需要换源/加源时只改 `catalog.py` 与 `bindings.py`，策略不动。

策略运行时通过 `app/platform/strategy_data_context.py` 把这份登记编译成不可变的
`StrategyDataPlan`，再注入 `CapabilityResolver`、持久化证据仓库或 replay fixture
进行解析。必需能力失败会抛出 `StrategyDataUnavailable` 并阻断本次研究输入；可选能力
只记录 `optional_missing`。返回值同时保留 `QualityReceipt` 与 provenance，因此策略规则
不需要知道供应商、传输方式或数据库实现。这个 facade 只做能力编排，不改变任何 live
阈值、推荐权重或订单路径。

读库时同样不写供应商：`store_values(capability, table, column)` / `primary_store_value(...)` 给出该能力在某表上的
来源过滤值（按优先级），`strategy_taxonomies(strategy)` 给出口径，`SOURCE_LABELS` 给出标签属性。

### 策略侧解耦（2026-09-18 完成）

审计出的 7 处供应商耦合全部迁完，`tests/test_datasource_boundaries.py::test_strategy_code_names_no_vendor`
禁止策略模块里再出现任何源键或来源标签字符串。

| 位置 | 原来 | 现在 | 行为 |
|---|---|---|---|
| `intraday_signal_rules` 开盘跳空观察 | `price_source == 'tencent_batched_watch_quote'` | 目录标签属性 `exchange_timestamped`（腾讯、Longhu） | **变**：Longhu 报价也可触发，模型 v6→v7 |
| 同上 量能标签 | `!= 'fuyao_ths_derived'` | 目录标签属性 `rule_usable_flow` | 不变（Longhu 标签暂不可用，见下） |
| 同上 入场键名 | `fuyao_minute_breadth_v1` 等 | `minute_breadth_v1`、`no_public_main_flow` | 仅改名（随 v7） |
| `intraday_scan_preparation` | 记 `fuyao_ths` 健康 | 记目录中 `quote.all_a_snapshot` 的主来源 | 不变 |
| 扫描链路 | `tushare_minutes` 依赖 | `realtime_minutes`（实现仍由组合根注入） | 不变；信号证据键 `tushare_rt_min`→`realtime_minute` |
| `post_close_strategy_service` | 直读 `tushare_raw_records` daily_basic、`source='longhuvip_main_net'` | 读 canonical `daily_fundamentals`（按目录优先级）；资金流来源由目录给出 | 不变（owner 库 9-16/9-17 逐行比对一致） |
| `watchlist_countertrend_rebound` | 复权因子优先两个 Tushare 源；`taxonomy_key='ths_industry'` | 目录来源顺序（当前 `longhu_qfq_derived` 优先，Tushare 仅作已落库 checkpoint）；策略登记的口径 | 研究价 fail-closed；先按 provider 优先级选 Longhu，再在同源内按 `available_at` 选最新 |
| `strategy_pattern_mining_service` | 分钟回放写死腾讯 | 组合根注入 `minute_source` | 不变 |
| `xiaojie_reference_repository` | `call_tushare_api('stk_limit',…)`；口径优先级写死 | `limits.prices` 适配器（完整性契约在 `sources/tencent_limits.py`：交易所公布值，取不全即报错重试）；口径来自策略登记 | 不变 |
| 盘中扫描同业集合（两处仓库查询） | `('ths_concept_flow','ths_index_n','ths_industry')` | 策略登记的口径 | 不变 |

口径是**策略参数**：`CapabilityRequirement.taxonomies` 记录该策略基于哪几个板块口径校准，测试要求它们在口径表里，
且每项需求至少有一个 `live_verified` 口径。Tushare 停用后 `ths_concept_flow` 不再刷新；按运营者决定（2026-10-09），
凡列出它的策略都在它**前面**加了 `declared` 的 `fuyao_ths_concept`：按顺序取口径的读者（小杰）在 Fuyao 成分入库后
先用它，入库前的历史场次仍读 `ths_concept_flow`；按并集取的读者（盘中同业集合）两者都读，同一概念代码的同一组成员只计一次。
其余 fuyao 口径不会被策略自动选中。

### 4.2 P0 消费者盘点（按当前代码事实）

- 经 resolver 读取：独立运行时在 `quant-service/app/datasources/runtime.py:82` 组装
  `CapabilityResolver`；盘中异动采集在 `quant-service/app/datasources/collectors/intraday.py:195`
  通过 `limits.anomaly_tape` 读取；策略能力 facade 在
  `quant-service/app/platform/strategy_data_context.py:211` 统一调用
  `resolver.fetch`。
- 组合根直连：盘中采集在 resolver 未注入时于
  `quant-service/app/datasources/collectors/intraday.py:192-194` 直接调用
  `eastmoney_ztb.fetch_stock_changes`；其余 Fuyao 盘后/板块任务通过注入的
  `fuyao_fetch` 读取（装配入口为 `quant-service/app/datasources/runtime.py:82`）。
  `quant-service/app/datasources/bindings.py:76-155` 是 resolver 的组合根绑定表，
  不属于消费者。

v7 回放对比（owner 库冻结输入，v6 规则 vs v7 规则）：随机 1,279 条 0 差异；开盘窗口 10 条候选 0 差异——
因为这些 Longhu 报价在开盘窗口的新鲜度全是 `invalid_timestamp`（见下），v7 的放开要配合时钟修复才会生效。

### 验证中发现并修复的两个数据问题

1. **Longhu 上午报价时间戳全部无效**：供应商时钟是不补零整数（09:30:14.237 → `93014237`），归一化截前 6 位得到
   `930142`（93 点），导致每天 09:30–10:00 的 Longhu 报价都被判 `invalid_timestamp`、退回备用源。
   修复两处：源头 `longhu_vendor_source.py`（owner 网关侧，需 owner 更新后生效）；消费侧
   `intraday_quote_normalization.exchange_time_status` 对“小时>23”的 6 位时间做无歧义还原（peer 部署即生效）。
2. **规则冻结输入缺 `flow_metric_sources`**：线上规则按标签把某些量能字段置 0，冻结给回放的输入却丢了标签，
   回放会用上线上没用的值。已加入冻结字段（旧快照无该键，回放行为不变）。

待查（未改）：9-16/9-17 有一批行标为 `longhuvip`、原始数据却是腾讯盘口、量比约 0.03；报价合并路径查清前，
Longhu 量能标签在目录中保持 `rule_usable_flow=False`。

### 非板块名单（目录 `NON_SECTOR_GROUPS`，catalog v2）

同花顺概念表（`ths_index_n`、`ths_concept_flow`）里混着一些**资格名单**，它们的成员没有共同的涨跌驱动：
- 交易通道：融资融券 3,915、深股通 1,908、沪股通 1,667；
- 指数成份：沪深300、上证50/180/380、中证500；
- 同花顺自编指数：漂亮100、中特估100、出海50、新质50、果指数；
- 高股息精选；
- 专精特新（认定名单，横跨所有行业）；
- 证金持股；
- 按报告期发布的名单：2026一季报预增、2026中报预增。

同伴规则把每个分组都当作板块。9-18 起观察池的 5 只票被“融资融券”连成一组，每只票的“同板块确认”对象都是另外 4 只。
按代码剔除之外，另有一条标签正则，用来接住同花顺以后每期新发的名单。这条正则在 395 个现有标签上只命中已列出的 15 个。

保留的分组（成员确实一起交易）：ST板块、次新、摘帽、国企改革、中字头、参股券商、国家大基金持股、并购重组。

在三处查询中剔除（`sector_membership_repository.sector_group_predicate`）：盘中同伴、候选票所属板块、模拟盘板块敞口。

受影响的两个策略都升了版本：watchlist-confirmation v7→v8，ten-day 影子 v1→v2。回放差异为 0，原因是历史输入里还没有这些分组：
- 60 天内 199 条盯盘信号，同伴都为空；
- 十日影子共 2,580 条观察，有映射的 118 条全部来自“智能电网”。

9-21 是第一个会读到这些分组的交易日。本次修复在此之前上线。

## 5. 自算层（derived.market_sentiment）

输入：fuyao 涨停/炸板/跌停池（翻页取全）、fuyao 全 A 快照、东财“昨日涨停”池（自带昨日连板数）、
上一交易日收盘读数（量能基准）、可选的同花顺概念行情与成分。输出：

- 涨停/跌停/炸板家数、封板率、炸板率；连板天梯分布与断层；
- **分层晋级率**：1→2、2→3、3→4、4+，以及总体；
- **昨涨停今日表现**：溢价（均值）、红盘率、中位数，分全体/首板/连板；
- 涨跌分布（>7、5-7、3-5、0-3、平、-3-0 …、按板块涨跌幅限制判定的价格涨跌停数）；
- 全 A 成交额与较前一日变化；
- 概念强度分 = 0.4·z(指数涨幅) + 0.4·z(成分内涨停数) + 0.2·z(成分红盘率)，权重是声明值、未拟合。

口径公开、可复算；**与开盘啦私有情绪分数值不会一致**，也不是交易信号。2026-09-18 收盘读数：
涨停 77、炸板 25、封板率 75.5%、天梯 1 板 65 / 2 板 8 / 3 板 2 / 4 板 2、1→2 晋级 8/38。

## 6. 调度与部署

| 任务（租约键 `background_loop:<label>`） | 归属 profile | 内容 |
|---|---|---|
| `market_event_capture`（已有，扩展） | intraday_edge | fuyao 池子每分钟（翻页）、跌停池、热榜/飙升榜 10 分钟、异动 5 分钟、09:26 竞价基准 |
| `public_evidence_capture`（新） | intraday_edge | 快讯 90 秒（夜间 10 分钟）、互动问答 10 分钟、盘口异动 3 分钟、东财人气/飙升 10 分钟、板块异动 15 分钟、情绪 5 分钟 |
| `post_close_public_archive`（新） | research | 15:10 东财六池/异动汇总/收盘情绪 → 15:20 热榜历史 → 17:30 龙虎榜 → 18:00 datacenter → 18:10 两融 → 18:30 估值与 848 指数 → 19:00 观察池分笔 → 19:30 股本变迁；失败每 10 分钟重试到 23:30 |

开关：`PUBLIC_EVIDENCE_CAPTURE_ENABLED`、`POST_CLOSE_PUBLIC_ARCHIVE_ENABLED`（整体），
`PUBLIC_EVIDENCE_<源>_ENABLED`、`PUBLIC_ARCHIVE_<任务>_ENABLED`（单项），
`PUBLIC_ARCHIVE_MARGIN_DETAIL_ENABLED`（两融明细，默认关），`PUBLIC_ARCHIVE_MAX_TICK_SYMBOLS`（默认 60），
`TDX_HQ_HOSTS`（通达信主站列表）。

两种部署形态：

1. **进程内（默认）**：随 quant 服务启动，按 runtime profile 分配到 47 的 intraday_edge 与 research 两个容器。
2. **独立容器**：同一镜像运行 `python -m app.datasources collect`，只需 `PG*` 环境变量与（可选）fuyao key。
   与服务使用**同一租约键**，谁持有租约谁写，另一方等待，不会双写；需要彻底切走时在服务上关掉两个总开关。

不引入新依赖：通达信协议为标准库实现（pytdx 只有 sdist 且依赖 cryptography，进不了 peer 的离线 wheelhouse）。

### 6.1 通达信盘后归档任务（十个，默认关闭）

十个任务挂在 `post_close_public_archive` 循环里（`quant-service/app/datasources/collectors/post_close.py`），与其余盘后任务不同，**默认全部关闭**：单项开关是 `PUBLIC_ARCHIVE_<键大写>_ENABLED`（例如 `PUBLIC_ARCHIVE_TDX_GPCW_ENABLED`），取值 `1`、`true`、`yes`、`on` 为开（`runtime.py` 的 `opt_in_keys`）；总开关 `POST_CLOSE_PUBLIC_ARCHIVE_ENABLED` 仍要开着。窗口按 Asia/Shanghai 本地时间；只在交易日运行（日历取不到就跳过，不猜）；每个交易日成功一次，窗口内失败每 10 分钟重试，到 23:30 为止。

写入走 `persist_timed_observations`（`public_market_repository.py`）：唯一键是 (来源, 能力, 证券, 生效时间, 载荷哈希)，`effective_at`、`available_at`、`availability_basis`、`observation_symbol` 不入哈希，同一事实再采到会被忽略。所以重启只重复请求，不重复证据，也不会把 `available_at` 往后移。板块成分类任务改走成分变化写入：只记开、平仓，空回答不改任何东西。

| 键（开关 `PUBLIC_ARCHIVE_<键大写>_ENABLED`） | 窗口 | 内容 | 正常一天的行数（函数 docstring） | 幂等与备注 |
|---|---|---|---|---|
| `tdx_security_list` | 19:40–23:30 | 证券列表（`reference.security_list`），能力 `tdx_security_list` | 首次约 52,000 行，之后只存有变化的证券 | 只存参考字段（代码、市场、名称、类型、小数位、ST、来源；不存每天都变的昨收和应答主机），与库里每只证券的最近一行比较，只写参考字段变了的行 |
| `tdx_tipinfo` | 19:50–23:30 | `tipinfo.dat` 的全部披露行，能力 `tdx_tipinfo` | 约 5,600 行，一次 | `effective_at` = 首次披露日 23:59:59+08:00，`available_at` = 采集时刻 |
| `tdx_gpcw` | 20:00–23:30 | 历史财务报表：清单 `tdxfin/gpcw.txt` 入 `tdx_gpcw_manifest`，变化的期入 `tdx_gpcw` | 首次开启存最新 2 期，之后每次再补 2 期积压 | 预算与日期见下 |
| `tdx_index_bars` | 20:10–23:30 | 6 个指数的日线与涨跌家数：`999999.SH`、`399001.SZ`、`399006.SZ`、`399300.SZ`、`000688.SH`、`899050.BJ`；能力 `tdx_index_daily_bars`、`tdx_index_breadth` | 每个指数首次 800 行，之后每个交易日 5 行 | 库里已有该指数就只取最近 5 根；`effective_at` = 交易日 15:00 |
| `tdx_mac_boards` | 20:20–23:30 | MAC 板块目录（`tdx_mac_board_catalog`）与成分变化（taxonomy `tdx_mac_type_<类型>`） | 一轮 5 次目录请求，加每个板块 1 次成分请求 | 成分只记开、平仓 |
| `tdx_limit_pools` | 20:30–23:30 | 三个派生涨跌停池，能力 `tdx_limit_up_pool`、`tdx_broken_pool`、`tdx_limit_down_pool` | 每个池一行一个成员 | 三个池共用一次读取；只能取当日会话，涨跌停价行的日期不是当日就报错，等重试 |
| `tdx_host_probe` | 20:40–23:30 | 主机周探针，见下 | 每台主机一条健康记录 | 每个 ISO 周一次 |
| `tdx_stat_snapshot` | 20:50–23:30 | `tdxstat.cfg`、`tdxstat2.cfg` 快照，能力 `tdx_stat_valuation`、`tdx_stat_daily_basic` | 完整快照日各约 8,000 行 | `effective_at` = 行自带日期当天 15:00，行日期不是采集日期 |
| `tdx_files_membership` | 21:00–23:30 | 服务器文件的板块成分（`block_gn`、`block_fg`，taxonomy `tdx_files_*`） | 每个返回的成分一行观察；没变的成分不存新区间 | 成分只记开、平仓 |
| `tdx_calendar_ipo` | 21:10–23:30 | 节假日与新股申购日，能力 `tdx_trade_calendar`、`tdx_ipo_calendar` | docstring 写几十行日历加一小批新股；用整文件夹具 `quant-service/tests/fixtures/tdx_zhb_20261009/needini.dat` 解析是 680 个节假日（1991-01-01 至 2030-10-07，40 个声明年），申购行 15 + 12（`tdx_server_files`） | `effective_at` = 日期当天 15:00 |

**`tdx_gpcw` 的预算与日期**

- 每次运行最多取 `PUBLIC_ARCHIVE_TDX_GPCW_MAX_PERIODS` 期（默认 2，下限 1）：“新增或 md5、大小变了”的期按文件名（即报告期）从新到旧取。
- 清单条目只在该期的数据落库之后才落库（`bd5b26ae`）：预算之外的期和下载失败的期，下一次运行仍算变化，会再被取到。所以首次开启只存最新的 N 期，其余积压每次运行补 N 期，直到完整历史（148 期）补齐；以后只取新出的或被重述的期。
- 每行财报的 `availability_basis` 有两种：
  - `tipinfo_first_disclosure`（有日期）：库里已落的 `tdx_tipinfo` 行里有同一个 (代码, 报告期)，`available_at` = 首次披露日 23:59:59+08:00，下一个交易日起可用；
  - `collection_time_undated`（无日期）：`tipinfo.dat` 每只证券只留最新一期，更早的期和它没列的证券拿不到日期，`available_at` = 采集时刻。任务返回里的 `undated` 是这类行的个数。
- 日期来自库里已落的 `tdx_tipinfo` 行，所以要先开 `tdx_tipinfo`（窗口也排在它之后）；没开时所有行都是 `collection_time_undated`。代码不是有效 A 股代码的行被拒，计入返回里的 `rejected`。

**主机周探针（`tdx_host_probe`）**：对 `tdx_protocol.configured_hosts()` 的每台主机用 `LOGIN_ONE` 握手各取一次证券计数（`security_count`，市场 0），写成 `tdx_public` 的 provider 健康记录：成功记在 `tdx_host:<主机:端口/握手>` 下（计数与耗时），失败记在 `tdx_host:<主机:端口>` 下（异常类名）；一台失败不影响别的主机。周状态只在进程内存里。它**不**重写主机池，主机池由 `scripts/refresh-tdx-hosts.sh` 手动刷新（第 7.1 节）。主机列表默认取 `tdx_hosts.py`，可用 `TDX_HQ_HOSTS=host:port,host:port` 覆盖；MAC 主机可用 `TDX_MAC_HOSTS` 覆盖。


### 6.2 研究试读：开关与边界

`GET /api/v1/datasources/read/{source}/{capability}` 用目录里该绑定的适配器现场读一次上游并返回行，只用于研究看数，**永远 `decision_eligible=false`**（`decision_eligible_reason`：research reads are evidence-only and never decision eligible）。开关是 `DATASOURCE_RESEARCH_READ_ENABLED`（默认 `true`；`0`、`false`、`no`、`off` 为关，关了返回 503）。边界在 `quant-service/app/datasources/adapter_calls.py`：

- 只服务 TDX 包内的适配器：适配器在 `app/datasources/sources/tdx_*.py`，或是 `app/datasources/derived/limit_pools.py`，并且参数全是关键字参数；其他绑定（持牌、供应商）返回 404。基线上 40 个 TDX 绑定里 34 个可试读，不可试读的 6 个是 `tdx_public` 的 `ticks.session`、`auction.history_0925`、`fundamentals.capital_changes`、`quote.index_overview` 和 `tdx_local` 的 `bars.daily`、`bars.minute`；目录里每个绑定的 `research_readable` 说明能否试读。
- 同一 (来源, 能力) 每秒最多一次，超了返回 429；全进程最多 2 个读并发。
- 查询参数就是适配器的关键字参数：未知参数、缺必填参数、标量参数重复都是 422；列表参数用逗号分隔，`symbols` 最多 80 个、`count` 最多 800，超了 422；日期用 ISO 格式。
- 最多返回 2000 行，超出时 `truncated=true`。
- 回包带 `coverage`、`effective_at_min`/`effective_at_max`、`available_at_min`/`available_at_max`、`warnings`（含应答主机 `tdx_host=…`）和起止时间。

控制台的“研究试读”按钮调用的就是这条路由（经 relay 的 `/api/research/datasources/read/{source}/{capability}`）。

## 7. 运维命令

```bash
python -m app.datasources validate                 # 目录一致性
python -m app.datasources catalog --capability limits.limit_up_pool
python ../scripts/probe-public-sources.py          # 全部公开源只读探测（在要测的出口上跑）
# fuyao 同花顺概念/行业/地域成分：盘后循环 ths_member_backfill 自动刷新；手动推进一批用
#   POST /api/v1/market/sectors/concepts/members/backfill/run  {"batch_size": 25}
python ../scripts/backfill-eastmoney-hot-rank-history.py [--symbols ...]
python ../scripts/tdx-local-export.py --vipdoc <通达信>/vipdoc --out <offline 目录> --kinds 1m
PYTHONPATH=<pytdx 解包> python ../scripts/verify-tdx-protocol.py   # 与 pytdx 逐行比对
```

迁移 `20260918_ds0001`（owner 库已记录一条本仓库没有的 0095–0105 迁移线，故用不占序号的 ID）登记新 provider/能力/路由。
2026-09-18 已在 owner 库直接执行其幂等插入（13 个 provider / 18 条能力 / 33 条路由），未改 alembic_version；两条迁移线合并时再补合并迁移。

### 7.1 通达信：探测、升级与周一验收

下面的命令在仓库根目录执行（`python -m app.datasources` 要在 `quant-service/` 下执行）。脚本只读上游；写文件的只有 `--output` 指定的结果、`tdx-promote.py check` 的证据文件，以及 `refresh-tdx-hosts.sh` 对 `tdx_hosts.py` 的改写。

**探测一个绑定**（`python -m app.datasources probe`）

```bash
# 不管状态，调一次该绑定的适配器，打印一个 JSON，不写任何东西；适配器抛错时退出码为 1
(cd quant-service && python -m app.datasources probe tdx_public bars.index_daily --params '{"symbol": "999999.SH", "count": 5}')
(cd quant-service && python -m app.datasources probe tdx_mac limits.prices --params '{"symbols": ["000001.SZ"]}' --all-rows)
# --adapter 改调指定函数（app/<模块路径>.py:<函数>），给目录里只有模块名的参考源用
(cd quant-service && python -m app.datasources probe tencent_free bars.minute --adapter app/free_market_providers.py:tencent_intraday_minutes --params '{"symbol": "600519.SH"}')
```

输出的 JSON 有：来源、能力、适配器、参数，起止时间（UTC），行数，`coverage`，生效与可得时间的最小、最大值，`warnings`（带应答主机 `tdx_host=…`），前 5 行（`sample`；`--all-rows` 给全部行），适配器抛错时的 `error`。`--params` 是 JSON 对象，`date` 型参数写 ISO 字符串，`probe` 会转成日期。

**升级一个绑定**（`scripts/tdx-promote.py`）

```bash
python scripts/tdx-promote.py check tdx_mac quote.watch_snapshot --params '{"symbols": ["600519.SH", "000001.SZ"]}' --egress owner
python scripts/tdx-promote.py apply scripts/data/tdx_promote_<能力>_<来源>_<日期>_<出口>_<参数哈希>.json [...]
```

- `check` 在本机（`--egress mac`）或 owner 的 quant-research 容器里（`--egress owner`）跑 `python -m app.datasources probe`。owner 出口经 ssh 连到 owner，再只读地 `docker exec trading-hareness-peer-quant-research-1 …`，需要先设置 `LONGHU_SSH_HOST`、`LONGHU_SSH_PORT`、`LONGHU_SSH_USER`、`LONGHU_SSH_KEY_PATH`（这里只写变量名；值不进文档、日志和证据文件）。exec 跑的是镜像里的代码：`QUANT_HOTFIX_ENABLED` 的代码覆盖层只在服务进程的 `PYTHONPATH` 上，不在 exec 里，所以镜像本身要带 `probe` 命令和被探的适配器。
- 证据写到 `scripts/data/tdx_promote_<能力>_<来源>_<日期>_<出口>_<参数哈希>.json`（日期取 Asia/Shanghai；参数哈希是 params JSON 的 sha256 前 8 位；schema `tdx-promote-v2`），记录所有输入、每次探测（前 5 行和全部行的哈希）、对账结果（行数、最大偏差、例子）和三道门的判定。`check` 不管判定如何都退出 0。
- 三道门：
  - `owner_egress`：探测在 owner 容器里跑，并且有行；
  - `agreement`：`BindingSpec.agreement` 声明的每个容差都成立。两侧的行按声明的 key 连接，逐个公共行比较，公共行占本绑定行数的比例不得低于 `min_coverage`（默认 0.95）。**没有声明参照的绑定，这道门不通过**（证据里写 not applicable，并注明 promotion is blocked），所以它不能从 UNSUPPORTED 升到 DECLARED（`c06fbad8`）。声明了参照的绑定：`tdx_mac` 的 `bars.minute`（close、volume）、`limits.prices`（up_limit、down_limit）、`quote.watch_snapshot`（price、volume、amount），参照都是腾讯；`tdx_public` 的 `bars.minute`（腾讯分钟线）、`bars.daily`（参照 `tdx_mac`，只比 close 与 amount；这是跨协议而不跨厂商，旧协议日线成交量是整手）、`bars.index_daily`（腾讯指数收盘）；容差是留给第一次盘中检查去调的起始值（`catalog.py` 的注释）；
  - `intraday`：每次探测的开始和结束都在上交所（XSHG）交易时段内，含午休和节假日，用运行脚本的 Mac 上的 `exchange_calendars` 判定，证据里写日历名和版本。
- `apply` 取同一个绑定的证据文件，**每个文件**都过了这一步要的门才改：`UNSUPPORTED` → `DECLARED` 要 `owner_egress` 和 `agreement`；`DECLARED` → `LIVE_VERIFIED` 另要 `intraday`。它只把 `catalog.py` 里该绑定的状态记号改一格，从不碰 `decision_eligible`；不提交，只打印 `git commit -F - -- quant-service/app/datasources/catalog.py` 命令，提交信息引用各证据文件和它们的 sha256。顺序是先提交证据，再单独提交状态改动。
- `apply` 要求 `catalog.py` 里该绑定恰有一处字面的 `_bind("<来源>", "<能力>", …)` 调用，否则退出。`derived_tdx_limits` 的三个池已改成三处字面的 `_bind(...)`（`e41e4dba`），`apply` 能改它们的状态；它们还没有声明参照，比对门不会通过。

**owner 出口的路由探测与主机池**

```bash
# scripts/probe-tdx-routes.py 连同候选主机文件经 ssh 送进 owner 的 quant-research 容器里跑（docker exec -i … python -B -），owner 上不写任何东西
bash scripts/tdx-owner-probe.sh --output <矩阵.json> [--profile login_one|legacy_3] [--require <命令,…>] [--min-usable-hosts N] [--samples N] [--interval 秒] [--hist-date YYYY-MM-DD]
# 重跑 owner 探测，重新生成 tdx_hosts.py，打印它的 git diff；不提交
bash scripts/refresh-tdx-hosts.sh
```

- `tdx-owner-probe.sh` 同样需要上面四个 `LONGHU_SSH_*` 变量，`--output` 必填；低于阈值的扫描退出码为 2，但已打印的矩阵仍会保留下来（那就是证据）。
- `refresh-tdx-hosts.sh` 把矩阵交给 `scripts/generate-tdx-hosts.py`：只取每个样本里都可用的主机，按连接延迟中位数排序，同一 /16 最多 3 台，取前 20 台，写入 `quant-service/app/datasources/sources/tdx_hosts.py`。盘后的 `tdx_host_probe` 只记健康，不重写主机池（第 6.1 节）。

**周一盘中验收**（计划第 4 节；输出文件名沿用 `scripts/data/<主题>_<日期>_<出口>.json` 的写法）

```bash
python scripts/probe-tdx-cadence.py --rates 1,2,5 --seconds 10 --output scripts/data/tdx_cadence_<日期>_mac.json                  # 4.1 节奏与速率
python scripts/probe-tdx-ext-delay.py --seconds 1800 --output scripts/data/tdx_ext_delay_<日期>_mac.json                          # 4.2 扩展行情延迟，09:30 开始
python scripts/tdx-promote.py check <来源> <能力> --params '<JSON>' --egress owner                                               # 4.3 owner 出口逐绑定探测与对账，随后 apply
python scripts/probe-tdx-q-flow.py --samples <N> --interval 60 --output scripts/data/tdx_q_flow_<日期>_mac.json                   # 4.4 0x90–0x96 与资金流位的盘中取样
python scripts/probe-tdx-disclosure-timing.py --polls <N> --interval 10 --output scripts/data/tdx_disclosure_timing_<日期>_mac.json  # 4.5 披露日当天
```

- **4.1 `probe-tdx-cadence.py`**：三类请求（legacy 0x054b 全 A 一页 80 只，MAC 0x122b 批量 80 只，legacy 0x053e 报价 80 只），每台主机、每个速率各跑 `--seconds` 秒，先在一条保持打开的连接上跑，再每个请求新开连接跑一遍。请求按序发出，晚了不补发。输出每次运行的失败率、应答请求的 p50、p95 延迟，以及行摘要变化的次数（缓存陈旧的主机永不变化）。默认 `--rates 1,2,5`、`--seconds 10`、`--host-count 3`（取 `tdx_hosts.py` 的前 3 台）、`--timeout 5`；MAC 那一类固定跑 MAC 主机；高于 10 次/秒的速率被拒绝。`--egress mac|owner` 只是写进结果的标签，不改变出口。采集器的节奏由这份结果定。
- **4.2 `probe-tdx-ext-delay.py`**：每秒向每台扩展行情主机读 `(27, HZ5017)` 和 `(47, IF2610)`，同时读腾讯的 `hkHSTECH`，每个读数带本机到达时间。延迟是使价格变化最多重合的整秒平移（正数表示该序列更晚显示价格），同时给出所依据的变化次数。`HZ5017` 对腾讯比；`IF2610` 没有公共参照（应用既不读新浪也不读腾讯的期货报价，新浪的 `nf_IF2610` 只在 `docs/archive/tdx-q-extcodes.md` 里手工核对过一次），所以后面的主机对第一台比。默认主机是 `113.45.175.47:7727`（登录标记 `TDX_DS`，实时候选）和 `139.9.191.175:7727`（标记 `TDX延时全`，延时候选），默认 `--seconds 1800`、`--max-lag 1200`；超过 `--max-lag` 或超过运行时长的延迟测不出来。读失败写成带异常类名和消息的条目，连续 `--max-consecutive-errors`（默认 5）秒失败则停，结果里记 `stopped`，退出码为 1。要定的是 `TDX延时全` 主机是否排除。
- **4.3 `tdx-promote.py`**：见上。新绑定都是 `unsupported`，所以第一步是 `UNSUPPORTED` → `DECLARED`（`owner_egress` + `agreement`），在交易时段内跑出的证据才能再走 `DECLARED` → `LIVE_VERIFIED`（另要 `intraday`）。
- **4.4 `probe-tdx-q-flow.py`**：不带参数是 2026-10-09 会话的一次性对比。`--samples N --interval 秒`（默认 60）取 N 个盘中样本，每个样本新开一个 MAC 连接，读 MAC 0x122b 的资金流位与 0x90–0x96、`0x1218` 的资金流 JSON，以及东财 `push2 ulist.np/get` 的个股主力资金（`f62` 净流入额，`f184` 占比，占比的单位未确认），三块各带接收时间；数字原样记录，不做解读。每个样本之后重写 `--output`；失败的样本写成条目，连续 `--max-consecutive-errors` 个失败则停。MAC 主机和 8 只证券是脚本里的常量；东财参照经 `curl` 读取，脚本注释写明要从家宽出口读。
- **4.5 `probe-tdx-disclosure-timing.py`**：`--polls N`（必填）、`--interval` 分钟（默认 10）。每隔 `--interval` 分钟下载一次 `zhb.zip`，记录上一次成功轮询没有的 (证券, 报告期, 第 4 列日期) 行和轮询时间（第一次轮询只作基线）。最后一次轮询之后，对每条季末报告期的新行，用应用的 cninfo 读取器查该证券从第 4 列日期前一天到后一天的公告，记下每条公告的标题是否含“<年>年<报告名>”，以及 Asia/Shanghai 时间（cninfo 列表只给日期时，时间就只有日期）；逐条查询，按读取器的限速。失败的轮询写成条目，连续 `--max-consecutive-errors` 次失败则停。

**三条 API 路由**（`quant-service/app/routers/datasource_catalog.py`、`datasource_reads.py`；控制台经 relay 用 `/api/research/datasources/…`）

| 路由 | 作用 |
|---|---|
| `GET /api/v1/datasources/catalog?source=&category=&status=` | 整份目录：来源、能力、绑定（含 BindingSpec 全字段、`research_readable`）、口径、退役项 |
| `GET /api/v1/datasources/capabilities/{capability}` | 一个能力和它的全部绑定，另带 `evidence_locations`；未知能力返回 404 |
| `GET /api/v1/datasources/read/{source}/{capability}?<适配器参数>` | 研究试读（开关和边界见第 6.2 节） |

## 8. 本次顺带修复的现存问题（均有测试）

| 问题 | 影响 | 修复 |
|---|---|---|
| fuyao 涨停/炸板池默认分页 50，只取第一页 | 9-18 实有 77-87 只涨停，库里每分钟封顶 50 只 | 按 size=200 翻页取全 |
| fuyao `thscodes` 上限 100，采集按 500 分批；一个指数/退市代码使整批失败 | 收盘竞价 9-18 只落 70 只（仅尾批成功） | 100 一批、先过滤成 A 股、按报错剔除坏代码重试；竞价 universe 改用 fuyao 自身在市代码表 |
| 复盘按**行数**数涨停/跌停（fuyao 每分钟一行） | 涨停家数被放大到上千，`risk_off` 判定失真 | 按 symbol 去重取最新；天梯读 `continue_day_cnt`；方法版本 v2→v3 |
| `persist_market_events`/`persist_public_observations` 逐行写（52ms 往返） | 每分钟池子写入约 10 秒 | 改 `executemany` 批量，语义不变 |
| 东财盘口异动接口只认类型列表第一个 | 若按列表请求只拿到“火箭发射” | 每类型单独请求 |

## 9. 与原表的对照

| 原表来源 | 结论 |
|---|---|
| 开盘啦 longhuvip | 保留为授权主源（盘口/分钟/行业/竞价）；个股 K 线（id=7）下线 |
| 乘风（含 kpl_archive 榜单） | 不在本平台；其日 K、集合竞价、指数、同花顺热榜、龙虎榜、概念成分由 fuyao 对应能力整体替代 |
| 通达信客户端 .day/.lc1/.lc5 | `tdx_local`：owner 工作站 CLI 导出到离线导入契约；GPJY 财务包未解析（后续项） |
| TDX public protocol | `tdx_public`：使用 LOGIN_ONE 握手，按主机保留 legacy-3 回退；主机池由探测矩阵生成，进程内传输失败冷却，结果回执带 `host:port/profile`。LOGIN_ONE 下行情和 K 线命令可用，绑定待 P2；当前接入的是历史分笔和除权除息，失败时 fail closed。（2026-10-11 补：之后新增的通达信绑定见第 3.1 节，除上述旧绑定外均为 `unsupported`，不参与解析；解码事实见第 10 节。） |
| 腾讯 qt / fqkline / 分笔 | 观察池报价、五档、分钟、当日分笔 |
| 东财 push2 / datacenter / 天天基金 | 板块资金流（已有）+ 涨停板专题、盘口异动、人气榜、datacenter 事件、两融、基金净值（新增）；clist 全市场在 owner 出口被断连 |
| 同花顺事件 + 问财 | 未接：问财需登录态且有反爬；由 fuyao 热榜/飙升榜/异动原因覆盖“抢手名单”类需求 |
| 金十快讯 | 已接，不含 VIP |
| 同花顺官方 API（fuyao） | 59 条路由白名单，已采：全 A 快照、池子、天梯、竞价、热榜、飙升榜、异动、龙虎榜、估值、848 指数行情与成分；按“现在免费、以后未必”对待，关键能力均有第二来源 |
| 东财人气榜、巨潮/上证 e 互动、财联社 | 均已接入 |

## 10. TDX 解码字段字典（单位、符号、窗口）

本节只收有证据的解码事实，每条给出证据。证据没有确认的单位或含义保持 `*_raw`、`field_N`、`bit_0xNN`，或标 UNKNOWN，不在代码里起名。证据分两类：

- `scripts/data/<名称>_2026-10-10_mac.json`：实测记录，下表只写 `<名称>`（如 `tdx_bars_legacy_vs_mac`）；
- `docs/archive/tdx-*.md`：归档说明，写全路径。

除另注外，证据于周末从 Mac 出口（家宽）读取（证据文件名里的 2026-10-10 是 UTC 日期，个别文件的采集时刻已过北京时间 10-11 零点），行情是 2026-10-09 收盘后的状态；盘中的延迟与新鲜度没有测过（周一验收，见第 7.1 节）。

### 10.1 K 线与分笔

| 项 | 事实 | 证据 |
|---|---|---|
| legacy K 线价格 | 整数 /1000，K 线不需要按证券换算。5 个样本——600519.SH（沪主板）、300750.SZ（创业板）、688981.SH（科创板）、510300.SH（ETF）、920000.BJ（北交所）——都是 /1000（510300 为 4.369，920000 为 13.90）。需要按证券换算的只有 0x053e 报价（10.3） | `tdx_bars_legacy_vs_mac` |
| 分钟 volume、amount | 分钟类 K 线（category 0、1、2、3、8）的 volume、amount 是两个 IEEE float32：volume 的单位是股，amount 是元；绝对值小于 1e-20 的非正规数按 0 处理（`tdx_protocol._float32`）。日线及更长周期的 K 线保持 TDX 的压缩格式（`decode_volume`）。旧解析器把分钟线的这两个值也当 pytdx 的压缩数去解码，得到无意义的数 | `docs/archive/tdx-q-units.md`；`tdx_f7_1m_volume`（000001.SZ 14:59 读到 5.877e-39） |
| 分钟线对 MAC | 同上 5 个样本、2026-10-09 的全部 240 根（09:31 … 15:00）：legacy 与 MAC 的 volume、amount 逐位相同，OHLC 只差 float32 舍入（最大绝对差 5.9e-05）。MAC 0x122e 的请求多要一根，回包第一行是昨收哨兵（240 根分钟线回 241 行），解析时丢弃；上市不足所要根数的证券没有哨兵，解析时最老的一根也被丢掉。它的 `seconds` 是当日 0 点起的秒数，是 bar 标签，不是成交时刻 | `tdx_bars_legacy_vs_mac`；`docs/archive/tdx-q-units.md`；`quant-service/app/datasources/sources/tdx_mac.py` 的 `parse_bars` |
| 分钟 volume 对腾讯 | TDX 分钟 volume 是腾讯分钟手数的 100 倍（比值中位数 100.0）。000001.SZ、600519.SH 的 240 根里分别有 238、237 根换算后相等，其余是首根 09:31（含 09:25 开盘集合竞价成交）和 14:58–14:59 收盘集合竞价里本应为 0 的 float32 非正规数 | `tdx_f7_1m_volume`；`docs/archive/tdx-q-units.md` |
| 日线 volume | legacy：沿用压缩格式，给出整手，奇数股被截断（600519 在 2026-10-09 为 35,110 手，MAC 为 3,511,051 股）；MAC：float32 的股，约 7 位有效数字（510300 为 1,276,733,184）；amount 两边相等。`tdx_mac` 的 `bars.daily` 用 `unit_factors` volume ×0.01 把股换成手 | `tdx_bars_legacy_vs_mac`；`catalog.py` |
| 全日成交量的口径 | 交易所口径的日成交量是日线和 MAC 批量行情给的值，不是全部逐笔之和，逐笔里有盘后成交：000001.SZ 逐笔共 1,078,143 手，日线是 1,078,105 手 | `docs/archive/tdx-q-units.md` |
| 分笔 | `ticks.session`：volume 的单位是手；方向码 0 买、1 卖、2 中性、5 盘后固定价（P）、8 集合竞价指示（A）。价格高于上一笔为 B，低于为 S，相等为中性，除非供应商给 5 或 8；2026-10-09 的样本里 8 只出现在零成交的竞价指示上，5 只出现在盘后成交上 | `docs/archive/tdx-q-units.md` |
| 时间约定 | 全部是 Asia/Shanghai 的交易所本地时间；legacy 分钟线的标签从 09:31 到 15:00；持久化的时间戳保持带时区的 UTC | `docs/archive/tdx-q-units.md` |

### 10.2 指数 K 线与涨跌家数

| 项 | 事实 | 证据 |
|---|---|---|
| 价格与成交额 | 指数日线（0x052d category 9 的指数布局）：OHLC 是点数，amount 是元。999999 在 2026-10-09 的 amount 888,561,205,248 等于同日 0x053e 指数报价的 amount | `tdx_bars_legacy_vs_mac` |
| `volume_raw` | 单位未知（999999 在 2026-10-09 为 5,351,499），所以 `bars.index_daily` 不映射它 | `tdx_bars_legacy_vs_mac`；`catalog.py` |
| 涨跌家数 | 每个交易日都带 `up_count`、`down_count`，是**该指数成分股**的上涨、下跌家数，不是全 A 宽度。2026-10-09：999999 为 1,341/956，399300 为 179/113，399001 为 1,698/1,166，399006 为 708/677，000688 为 270/341，899050 为 308/37 | `tdx_bars_legacy_vs_mac` |
| 与 0x054b 的宽度不等 | 同会话 0x054b 的 SH-A 是 2,320 行 1,316 涨/947 跌/57 平，与 999999 的 1,341/956 不相等，已测的 0x054b 分类没有一个能复现指数的数；0x051d 的涨跌家数同样是指数成分口径 | `docs/archive/tdx-q-breadth.md` |

### 10.3 0x053e 报价与五档

| 项 | 事实 | 证据 |
|---|---|---|
| 价格 | 整数，小数点位置属于每只证券，解析器固定 /100。证券列表（52,331 行）里个股（主板 3,198、创业板 1,412、科创板 618 行）、板块码（1,119 行）和指数（557 行）的 `decimal_point` 全是 2，所以对它们 /100 正确；北交所个股在列表里没有 `decimal_point`（352 行为空），报价按两位小数（920000.BJ 14.19 = 腾讯） | `tdx_quote_scale_and_bj`；`tdx_quote_order_book` |
| ETF、可转债 | ETF 的 `decimal_point` 是 3（1,427 行），可转债是 4（571 行）：固定 /100 对 ETF 高 10 倍（510300 为 43.85，应为 4.385；159915 为 30.64，应为 3.064），对可转债高 100 倍（127045 为 11,697.1，应为 116.971；113709 为 18,185.9，应为 181.859）。所以 legacy 报价适配器只收小数位为 2 的类型，ETF、可转债、基金的价格走 MAC 批量行情的 float 价格 | `tdx_quote_scale_and_bj`；`quant-service/app/datasources/sources/tdx_quotes.py` |
| 小数位不是类型的函数 | `b_share` 有 2 和 3，`fund`、`lof` 有 3 和 4，`other` 有 2、3、4，要逐只取证券列表里的值 | `tdx_quote_scale_and_bj` |
| 五档 | bid1..5、ask1..5 是元，bid_vol、ask_vol 是手。沪（600519）、深（000001）、北（920000）个股与腾讯收盘盘口逐档一致，价格和手数相同 | `tdx_quote_order_book` |
| 指数、板块码没有盘口 | 它们的买卖盘字段装的是别的数：999999 的 bid1 为 376,755.31，不是价格；880005 的 bid_vol1/ask_vol1（2494/1658）等于它的指数日线 up/down 家数；880491 的 ask_vol4 为 999999。永远不对指数或板块码暴露盘口字段 | `tdx_quote_scale_and_bj`；`tdx_quote_order_book` |
| 0x054b 全 A 排序列表 | 价格是元，volume 是手（canonical 股 ×100），成交额是元；每页 80 只，price≤0 的行滤掉 | `catalog.py`（`quote.all_a_snapshot` 的说明） |

### 10.4 880xxx：板块与市场统计

| 项 | 事实 | 证据 |
|---|---|---|
| 构成 | 证券列表（市场 1）里的 880xxx 共 652 个：604 个是 `tdxzs3.cfg` 的板块（最小 880081），48 个不是 | `tdx_market_stat_codes` |
| 那 48 个 | 880001–880079 里的市场统计（总市值、流通市值、活筹市值、平均股价、成交均价、涨跌家数 880005、停板家数、全 A 等权与中位，以及主板、创业、科创、北证各自的同类项）和 880096–880099（ETF 等权、REITs、可转债、通用回购）。代码里归为 `market_stat`，不是 `board` | `tdx_market_stat_codes`；`tdx_instruments.py` 的 `instrument_type` |
| 880005 | 2026-10-09 的收盘价 3297.0 等于同会话 0x054b 全 A 的上涨家数（那次完整排序的全 A 是 5,578 行，3,297 涨/2,155 跌/126 平；`tdx_mac_limits_all_a` 里滤掉 16 个无成交行后的快照是 5,562 行）。它的 bar 字段含义没有依据（UNKNOWN），不绑定；`bars.index_daily` 只收 `index`、`board` | `tdx_bars_legacy_vs_mac`；`tdx_market_stat_codes`；`docs/archive/tdx-q-breadth.md` |

### 10.5 北交所代码迁移

| 项 | 事实 | 证据 |
|---|---|---|
| 映射 | `zhb.zip` 的 `addedcode_bj.cfg` 把 351 个旧代码（43xxxx、83xxxx、87xxxx）映射到 920xxx；证券列表里 920xxx 行有 352 个，来自 zhb 的 `tdxbjmore`，没有 `decimal_point` | `tdx_quote_scale_and_bj` |
| 历史在新代码下 | 服务器在新代码下给迁移证券的全部历史，旧代码下什么也没有：920017 有 800 根日线，最早 2023-06-19（代码切换之前），旧代码 430017 为 0 根；920047 同；920288（旧 874709）有 25 根，自 2026-08-28，是新近上市 | `tdx_bj_history` |
| 实现 | 在 TDX 边界把旧码译成新码，不丢历史：`tdx_protocol.market_code` 按 `tdx_bj_codes.OLD_TO_NEW` 把旧 BJ 代码译为 920xxx，批量适配器返回的行带 `source_symbol`（请求时的写法）。映射表由 `scripts/generate-tdx-bj-codes.py` 从 `addedcode_bj.cfg` 生成 | `tdx_bj_history`；代码 |

### 10.6 MAC 0x122b 动态字段

注册表是 `quant-service/app/datasources/sources/tdx_mac_fields.py`（160 个位，`0x00`–`0x9f`）：每置一位回 4 个字节，按位序排列；没有条目的位叫 `bit_0xNN`，没有单位和 canonical 键。请求要拆成小位图，有些 MAC 主机会静默截断一次回包里的动态值个数（`docs/archive/tdx-route-mac-fields.md`）。下表只列有结论的位；状态里 MATCH、NO_REFERENCE 是注册表的记法，CONFIRMED 是归档说明的判定，两者不同时两个都写。

| 位 | 名称 | 单位、符号、窗口 | 状态与说明 | 证据 |
|---|---|---|---|---|
| `0x00`–`0x04` | pre_close、open、high、low、close | float32，元 | MATCH（对日线） | `docs/archive/tdx-route-mac-fields.md` |
| `0x05` | vol | uint32，**手**。MAC K 线 0x122e 的 volume 是股，批量行情的 0x05 是手，canonical 股 = ×100。例：600519.SH 在 2026-10-09 位 0x05 = 35110 手，同日 MAC 日线 3,511,051 股 | MATCH（对日线） | `tdx_mac_adapters_live`；`tdx_bars_legacy_vs_mac`；`docs/archive/tdx-q-units.md` |
| `0x13`、`0x14` | server_update_date、server_update_time | uint32 | 行情更新的日期、时间（Asia/Shanghai）；适配器用来组成 `exchange_time`，`limits.prices` 的 `trade_date` 取位 0x13（2026-10-09 的 5,562 行全是该日）。注册表对这两位仍记 NO_REFERENCE | `tdx_mac_limits_all_a`；`catalog.py` |
| `0x1b` | turnover | float32，% | MATCH（流通股本可信时）：= 位 0x05 的手数 / 位 0x0b 的万股 | `docs/archive/tdx-q-limitfields.md`；`catalog.py` |
| `0x20`、`0x21` | buy_price_limit、sell_price_limit，即涨停价、跌停价 | float32，元。按板块比例，取整到 0.01 元：主板 10 %，创业板、科创板 20 %（含 ST），北交所 30 %；沪深主板 ST 自 2026-07-06 起为 10 %，此前 5 %（`app/market_rules.py`） | MATCH。2026-10-09 全 A 快照的 5,562 行都有限价行（覆盖率 1.0），日期都是 2026-10-09。**无价格限制的证券两个限价都回 0.0**（001246.SZ、301716.SZ 两只新股，和上市首日的 920157.BJ），0.0 是“无限价”，不是价格。限价/昨收 − 1 与板块比例的偏差多在 0.001–0.003，证据文件归因于限价取整到 0.01，只有科创板 ST 的两行偏差更大（0.204、0.207），文件没有解释；float32 的 12.96 读成 12.960000038146973，比较要按 0.01 元的整数分 | `tdx_mac_limits_all_a`；`quant-service/app/market_rules.py`；`quant-service/app/datasources/derived/limit_pools.py` |
| `0x24` | pre_iopv | float32，元 | NO_REFERENCE：收盘后 510300 读到 0.0、159915 读到 305.58，都不是 3–4 元 ETF 的前一日 IOPV；非基金代码上这一位是别的数（600519 读到 1250081664.0，000001 读到 5.61，300750 读到 18.82）。`fund.iopv` 不读它 | `tdx_mac_iopv`；`tdx_mac_adapters_live` |
| `0x27` | iopv | float32，元 | 紧邻 ETF 的收盘价：510300 为 4.3898（收盘 4.385），159915 为 3.0639（收盘 3.064）。只有 510300 对账为 MATCH。`exchange_time` 取同一行位 0x13/0x14 的行情更新时间，IOPV 自己的时刻不在行内。非基金代码上也是别的数，所以适配器只收 ETF、LOF、基金类代码 | `tdx_mac_iopv`；`docs/archive/tdx-route-mac-fields.md`；`catalog.py` |
| `0x38`、`0x6b` | main_net_amount 及其副本 | float32，元，正为主力净流入 | CONFIRMED（归档判定；注册表仍记 NO_REFERENCE）：= `0x1218` 第 0 行的 [主力流入 − 主力流出]，8 只样本逐个相等（舍入残差最大 512 元）。是供应商口径，不是由逐笔重建的主动买卖净额（已测的方向码、涨跌、金额和手数阈值都对不上）；920000.BJ 为 0（北交所覆盖不全）。目前没有 TDX 的资金流绑定 | `docs/archive/tdx-q-flow.md` |
| `0x3b` | 20 日涨跌幅（复权价） | float32，% | 注册表登记为 `change_20d_pct`（`3de7cfff`；之前没有条目、按 uint32 读成 3199079547）。12 只中 9 只与日线收盘算出的 20 日涨幅相差不超过 0.01，另外 3 只窗口内有除权除息（类别 1），所以是复权价上的 20 日涨幅；也等于 tdxstat 第 18 列 | `tdx_mac_bit_0x3b_2026-10-11_mac`；`tdx_stat_columns_vs_mac` |
| `0x58` | annual_limit_up_days | int32，天。窗口是日历年（2026 年），不是滚动 250 根 | MATCH：36 只样本里 30 只与日线复算相等，其余 6 只由年界解释；新股无涨跌停的交易日要排除 | `docs/archive/tdx-q-limitfields.md` |
| `0x5c` | close_streak | int32，天，带符号：连涨为 +n，连跌为 −n | MATCH（归档判定 CONFIRMED）：36/36 与日线相符 | `docs/archive/tdx-q-limitfields.md` |
| `0x88`、`0x8b` | up_count（0x88）、down_count（0x8b） | uint32，家 | MATCH（归档判定 CONFIRMED）：板块成分股的上涨、下跌家数；880761、880842、881376、880231 四个板块逐个与成分报价的涨跌家数相符。`0x89`、`0x8a` 是 ask3_volume、ask4_volume，不是家数 | `docs/archive/tdx-q-limitfields.md`；`tdx_mac_fields.py` |
| `0x90`–`0x96` | change_at_1000、change_at_1030、change_at_1100、change_at_1130、change_at_1330、change_at_1400、change_at_1430 | float32，% | 日内采样快照：等于当日一分钟线在该时刻或相邻一分钟的涨跌，不要求恰好同一格；盘中含义等周一取样（计划第 4 节 4.4） | `docs/archive/tdx-q-limitfields.md`；`docs/archive/tdx-route-mac-fields.md` |
| 批量回包 | — | — | 服务器按请求顺序答，不列的代码就不回：请求 80 个沪市代码（600000–600079）回 57 行，缺的 23 个都是已退市代码。不是“请求里还没轮到的代码”（别的代码、重复、乱序）的行被丢弃并记 `code_mismatch`；一行都对不上则整包报错并换下一台主机；没回行的证券由适配器在 warnings 里报告 | `tdx_mac_batch_omission`；`quant-service/app/datasources/sources/tdx_mac.py` |

### 10.7 tdxstat.cfg 与 tdxstat2.cfg

两个文件是 `zhb.zip` 的成员：`tdxstat.cfg` 35 列、`tdxstat2.cfg` 21 列，各 8,080 行，UTF-8。市场列：0 SZ 4,132 行、1 SH 3,596 行、2 BJ 352 行。**每行有自己的日期**，文件没有单一的生效日：8,032 行是 20261009，10 行是 20261008，8 行是 20260930（没成交的证券保留最后日期）。`-1` 只出现在 tdxstat 第 5 列，是有效的“连跌 1 天”，不是哨兵；空字段不当 0 用。

证据：`docs/archive/tdx-q-stats.md`（420 只样本对 MAC 日线、MAC 批量行情的位和腾讯，判定 CONFIRMED 要命中 ≥ 95 %；标 probe 的列可由 `scripts/probe-tdx-q-stats.py` 复算）；`tdx_stat_columns_vs_mac`（再取 80 只沪深股票，每第 60 行一只，对 MAC 位 45、48、49、53、54、59、60、65、68、69、87、88，除另注外 80/80 相符）。

tdxstat.cfg 的已确认列（解析器 `tdx_files.parse_tdxstat`；命中率取自归档，括号内是样本数）：

| 列 | 含义 | 单位、符号 | 解析器字段 | 命中率与参照 |
|---|---|---|---|---|
| 3 | 静态市盈率 | 倍 | `pe_static` | 99.7 %（389/390），MAC 位 49 |
| 4 | 最近交易日 | YYYYMMDD | `date` | 100 %（420/420） |
| 5 | 连续涨跌天数 | 天，正为连涨、负为连跌 | `streak` | 95.2 %（400/420），刚过线 |
| 6 | 当日涨跌幅 | % | `change_pct` | 100 %（420/420） |
| 7 | 前一日涨跌幅 | % | `change_prev_day_pct` | 99.3 %（417/420），MAC 位 66 |
| 8 | 前二日涨跌幅 | % | `change_prev2_day_pct` | 99.3 %（417/420），MAC 位 71 |
| 9 | 市盈率 TTM | 倍 | `pe_ttm` | 100 %（390/390），MAC 位 48 |
| 10 | 股息率 | % | `dividend_yield_pct` | 100 %（390/390），MAC 位 91 |
| 11 | 与 MAC 位 45 `circulating_capital_z` 完全相同 | 单位未对上，含义 UNKNOWN（见下） | `circulating_capital_z_raw` | 100 %（390/390），只是恒等 |
| 18 | 20 日涨跌幅 | % | `change_20d_pct` | 99.8 %（419/420），MAC 位 0x3b（59）按 float32 读 |
| 20 | 60 日涨跌幅 | % | `change_60d_pct` | 99.8 %（419/420），MAC 位 68 |
| 21 | 年初至今涨跌幅 | % | `change_ytd_pct` | 100 %（420/420），MAC 位 60 |
| 23 | 与 MAC 位 125 完全相同 | 含义 UNKNOWN | 不解析 | 100 %（420/420），只是恒等 |
| 26 | 年内涨停天数 | 天 | `annual_limit_up_days` | 100 %（390/390），MAC 位 88 |
| 27 | 4 日涨跌幅 | % | `change_4d_pct` | 98.1 %（412/420），按日线收盘 |
| 28 | 5 日涨跌幅 | % | `change_5d_pct` | 100 %（420/420），MAC 位 69 |
| 30 | 10 日涨跌幅 | % | `change_10d_pct` | 100 %（420/420），MAC 位 70 |

另有第 0 列（市场）和第 1 列（代码）已确认。

tdxstat2.cfg 的已确认列（解析器 `tdx_files.parse_tdxstat2`）：

| 列 | 含义 | 单位、符号 | 解析器字段 | 命中率与参照 |
|---|---|---|---|---|
| 2 | 最近交易日 | YYYYMMDD | `date` | 100 %（420/420） |
| 3 | 当日成交额 | **万元**（元 / 1e4） | `amount_10k_yuan` | 100 %（420/420），日线 amount / 1e4 |
| 5 | 前一日成交额 | 万元 | `amount_prev_10k_yuan` | 100 %（420/420） |
| 7 | 前二日成交额 | 万元 | `amount_prev2_10k_yuan` | 100 %（420/420） |
| 11 | 当月涨跌幅 | % | `change_mtd_pct` | 99.3 %（417/420），对上月最后一个收盘 |
| 12 | 1 年涨跌幅 | % | `change_1y_pct` | 100 %（420/420），MAC 位 65 |
| 14 | 集合竞价金额 | 万元 | `auction_amount_10k_yuan` | 99.5 %（403/405），MAC 位 87 / 1e4 |
| 17 | 52 周最高 | 元 | `high_52w_yuan` | 100 %（420/420），MAC 位 53 |
| 18 | 52 周最低 | 元 | `low_52w_yuan` | 100 %（420/420），MAC 位 54 |

另有第 0 列（市场）和第 1 列（代码）已确认。

- **PE 的两列别弄反**：第 3 列是静态市盈率（`pe_static`），第 9 列是 TTM 市盈率（`pe_ttm`）。`origin/main` 上的 `parse_tdxstat` 曾把两者命名反了，基线上已改正（`tdx_stat_columns_vs_mac`）。
- **第 11 列**：与 MAC 位 45 的值完全相同（000001 为 816,056.56，600519 为 54,094.90，300750 为 282,052.32，688981 为 180,066.49），注册表里该位的单位是万股（置信度低）。600519 的 54,094.90 约为其总股本 125,619.78（万股）的 43 %，000001 约 42 %，像自由流通股本，但没有参照比对：除了恒等，含义 UNKNOWN，解析器只留 `circulating_capital_z_raw`。
- **第 27 列不是一年涨跌幅**（那是 tdxstat2 第 12 列），是 4 日涨跌幅。
- **窗口基准日未定**：PLAUSIBLE 的窗口列——tdxstat 第 17 列（约 20 日，91.4 %）、第 19 列（约 60 日，80.7 %）、第 29 列（9 日，94.8 %），tdxstat2 第 19 列（约 30 日，90.0 %）、第 20 列（30 日，89.2 %）——与 MAC 的涨跌幅位不完全相等：MAC 位 0x3b 对第 18 列是 99.8 %，而按“19 根前的收盘”算只有 91.4 %，基准日的约定没有定下来。这五列不解析。
- 两个文件共 56 列：CONFIRMED 30、PLAUSIBLE 5、UNKNOWN 21（tdxstat 13 个、tdxstat2 8 个）；UNKNOWN 的列保持 raw，不起名。第 14–16、24–25 列（tdxstat）和第 9–10 列（tdxstat2）与 MAC 主力净流入的 Spearman 相关在 −0.18 到 0.00 之间、符号一致率 36–50 %，不是主力净流入，只能说量级像元或万元的金额。

### 10.8 tipinfo.dat

- **形状**：22 列，每只证券一行，**只含最新一期报告**。2026-10-10 下载的 5,652 行里，5,649 行的报告期是 20260630，另有 20260930、20250930、20260331 各 1 行；EPS 全是数，第 4 列全是 8 位日期，取值范围 20251028–20261010（`tdx_tipinfo`）。所以它只给每只证券的最新一期定日期，更早的期没有日期。
- **列 0–3**：市场（0 SZ、1 SH；北交所行用供应商的市场码）、代码、报告期、EPS（元/股）。
- **第 4 列**是该期报告的**首次披露日**，只有日期，没有时刻（解析器字段 `first_disclosure_date`）。与东财 `RPT_PUBLIC_BS_APPOIN` 的实际披露日相比，5,551 只里有 5,549 只相等（99.96 %）；两个“例外”（`002107`、`002731`）其实是另一报告期的行，不是第 4 列出错，见 `docs/archive/tdx-q-disclosure.md` 末尾 2026-10-11 的更正。它是披露日，不是通用的 PIT 可得时钟：没有时刻，且只覆盖最新一期。第 5–7 列都不是披露日（对实际披露日的精确命中为 56/5,296、43/5,513、0/494）。
- **解析与用法**：`tdx_zhb_extras.parse_tipinfo` 要求恰好 22 列、EPS 是数、第 4 列是日期，否则抛 `TdxFileError`。`tdx_fin_history.date_gpcw_rows` 按 (代码, 报告期) 给 GPCW 行定 `available_at` = 首次披露日当天 23:59:59（Asia/Shanghai），下一个交易日起可用；tipinfo 里没有的期保持无日期（`availability_basis` 见第 6.1 节）。
- **第 5–21 列**保持 `field_N`，见 10.10。

### 10.9 zhb.zip 的成员与可单独下载的文件

- `zhb.zip`（0x06b9 报告文件下载）1,332,578 字节，47 个非空成员，md5 `fa3f6929fb22eeec5240b01b9d40922f`。`tdxstat.cfg`、`tdxstat2.cfg`、`tdxzs.cfg`、`tdxzs3.cfg`、`spblock.dat` **是它的成员**：按名字单独向服务器要，这五个都答 0 字节（`tdx_server_files`）。`tipinfo.dat`、`xgsg.cfg`、`needini.dat` 等其余成员也在 zip 里；计划第 7 节把它们也算作“按名字要得 0 字节”，证据文件只记了上面五个。
- 可单独下载的是 `block_gn.dat`（757,083 字节，269 个板块）、`block_fg.dat`（453,279 字节，161 个）、`block_zs.dat`（329,507 字节，117 个）。
- `tdx_files.download` 对 0 字节的答复现在抛 `TdxFileError`（“TDX file is empty or not served”），此前静默返回 `b''`（一个静默的空文件）。
- 板块定义：`tdxzs.cfg` 604 行（类型 2 为 145、3 为 32、4 为 269、5 为 158），`tdxzs3.cfg` 1,071 行（多出类型 12 的 467 行）；`spblock.dat` 35 个特殊名单。`block_gn.dat` 对 `tdxzs3` 类型 4（概念），`block_fg.dat` 对类型 5（风格、事件）；`block_zs.dat`（指数成分表）和 `spblock.dat` 不是板块，不进 `sector.membership`。

### 10.10 保持 UNKNOWN 的事项

这些事项没有证据，代码和文档里都保持原始名字（`*_raw`、`field_N`、`bit_0xNN`），不绑定、不起名。前七项是计划第 5 节列出的，最后一项是归档里另有记载的。

| 事项 | 现状 | 来源 |
|---|---|---|
| MAC 位 `0x5d`、`0x5e`、`0x16`、`0x1d`、`0x7a` | 没有稳定的复算，也没有独立参照。`0x5d`、`0x5e` 的取值从个位数到 38,508、22,188，不等于涨跌停事件数、累计涨跌逐笔数、上市天数、成交笔数或涨跌成交量；`0x16` 是带符号整数（含 0），没有独立的强弱参照；`0x1d` 是随行业重复的合理百分数，没有独立的行业汇总；`0x7a` 是合理的比值，但竞价曲线没有给出独立的 09:25 分母 | `docs/archive/tdx-q-limitfields.md`；`docs/archive/tdx-route-mac-fields.md` |
| tipinfo 第 5–21 列 | 归档的最终字典把这些列全记 UNKNOWN（命中率低于阈值；第 8、13、14 列分别是 0.24 %、0.02 %、0 %）；归档的结构表里第 8、13、14 列是 PLAUSIBLE，依据只是成对出现的日期、数量形状，没有做公开数据连接。计划第 5 节写的是第 8 列、第 13 列（解禁日期，78 %）、第 14 列（解禁股数，86 %）为 PLAUSIBLE；这两个比率在本树里没有记录文件可引 | `docs/archive/tdx-q-disclosure.md`；计划第 5 节 |
| `importzs.cfg` 的值列（第 3–7 列） | 五个指数统计值，单位不明，没有成分重算可对；第 0–2 列（代码、快照日期、成分数）已确认 | `docs/archive/tdx-q-disclosure.md` |
| legacy 0x056a 的数量单位 | matched、unmatched 是原始服务器数量，单位不明：在 5 个股票、ETF 样本上没有一个统一的倍数能对上 09:25 的逐笔成交量；MAC 0x123d 的竞价数量同样。保持 `matched_raw`、`unmatched_raw`，不标手或股。竞价流到 09:24:57 止，没有字面 09:25 行 | `docs/archive/tdx-q-units.md` |
| 扩展行情 K 线与指数的成交量单位 | 扩展行情日线的 `volume_raw` 单位随市场而异：00700 的报价量 20,422,500 对日线 2,042，AAPL 的 37,881,199 对 378,811，IF2610 的 36,158 对 36,158；期货日线的 amount 槽放的是持仓量，最后一个字是结算价。指数（legacy 0x052d 的指数布局、0x051d）的 `volume_raw` 同样单位未知 | `docs/archive/tdx-route-ext-market.md`；`docs/archive/tdx-q-extcodes.md`；`tdx_bars_legacy_vs_mac` |
| `brkseat.dat` 与不透明的 zhb 成员 | `brkseat.dat` 的前两列（券商 id、席位或类型码）只是 PLAUSIBLE，没有席位名。`profile.dat`、`relation.dat` 和六个 `*comte*.dat`（`nacomte`、`nbcomte`、`nscomte`、`nscomte_std`、`nvcomte`、`nzcomte`）没有解出编码、校验、记录长度或语义；归档逐个列出 8 个，计划第 5 节写作 7 个 | `docs/archive/tdx-route-zhb-extras.md`；计划第 5 节 |
| `gbbq` 与其他本地文件 | `tdx_local_extra.py` 只有合成字节的单测，没有拿真实客户端的文件对过；公开代码（rainx/pytdx、mootdx、tdxpy）只是读法参考，不算 owner 数据证据 | `docs/archive/tdx-route-local-files.md` |
| 归档里另有记载的 | MAC 位 `0x59`（整数分值，20 只样本在 420–4,800，对不上成交量、成交额、换手、笔数）；MAC 资金流位 `0x39`、`0x6c`–`0x72`、`0x74`–`0x76`（UNKNOWN；`0x6f`–`0x71` 只有名字 PLAUSIBLE，数值仍 UNKNOWN）和 `0x73`（DDX，PLAUSIBLE）；tdxstat 的 13 列和 tdxstat2 的 8 列（10.7）；`tdxpkmore.cfg` 的第 7 列（24 行有 10.00、5.00、2.00、1.00、0.10 的覆盖值，没有 60 % 的规则）和全部标志列；0x054b 的排序类型 18–30（有数据，但没有独立的字段映射） | `docs/archive/tdx-q-limitfields.md`；`docs/archive/tdx-q-flow.md`；`docs/archive/tdx-q-disclosure.md` |
