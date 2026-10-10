# 数据源层（app/datasources）

更新：2026-09-20。所有“实测”均指从 owner peer（47.110.79.189）出口、收盘后的只读探测与不落库干跑；当前工作树新增的策略能力 facade 已完成本地回归，远端重启回读待 peer 运维会话。

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
| TDX public protocol | `tdx_public`：使用 LOGIN_ONE 握手，按主机保留 legacy-3 回退；主机池由探测矩阵生成，进程内传输失败冷却，结果回执带 `host:port/profile`。行情、K 线、历史分笔和除权除息均通过同一研究证据层读取，失败时 fail closed。 |
| 腾讯 qt / fqkline / 分笔 | 观察池报价、五档、分钟、当日分笔 |
| 东财 push2 / datacenter / 天天基金 | 板块资金流（已有）+ 涨停板专题、盘口异动、人气榜、datacenter 事件、两融、基金净值（新增）；clist 全市场在 owner 出口被断连 |
| 同花顺事件 + 问财 | 未接：问财需登录态且有反爬；由 fuyao 热榜/飙升榜/异动原因覆盖“抢手名单”类需求 |
| 金十快讯 | 已接，不含 VIP |
| 同花顺官方 API（fuyao） | 59 条路由白名单，已采：全 A 快照、池子、天梯、竞价、热榜、飙升榜、异动、龙虎榜、估值、848 指数行情与成分；按“现在免费、以后未必”对待，关键能力均有第二来源 |
| 东财人气榜、巨潮/上证 e 互动、财联社 | 均已接入 |
