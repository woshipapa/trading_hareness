# Longhu 接口能力审计（2026-09-05）

## 结论

owner 的 Longhu shared gateway 当前不是只有报价和分钟线，而是一个受 owner 凭据保护的多目标 stock-data proxy：8 个上游目标、50 个 documented operations、89 个带参数示例，单次物理分页上限为 300。当前探针没有改变数据库、策略阈值或实时信号。

网关入口：

- `GET /licensed/stock-api/catalog`
- `POST /licensed/stock-api/call`
- 认证：`X-Quant-Read-Key`
- peer 只拿规范化网关响应；Longhu token、UserID、DeviceID 留在 owner 侧

## 目标与能力面

| 目标 | 上游域名 | 已注册能力 | 研究用途 |
|---|---|---|---|
| `longhu_history` | `apphis.longhuvip.com` | 日线 K 线、历史涨跌广度/统计、历史涨停表现、历史竞价、筹码、盘后直播、主力持仓/布局 | 日线回放、事件研究、次日先验、筹码与涨停行为 |
| `longhu_quote` | `apphwhq.longhuvip.com` | `GetStockPanKou`、逐分钟趋势、竞价、委买委卖、涨停表现、筹码、逐笔大单、涨停基因、板块竞价 | 观察池实时状态、盘口研究、盘中 episode、封板/大单特征 |
| `longhu_market` | `apphq.longhuvip.com` | 主力监控、十档委托汇总、实时/历史排名、涨跌统计、雷达、热点榜、全球指数 | 市场宽度、热点扩散、主力/盘口代理、跨市场背景 |
| `longhu_market_wide` | `apphwshhq.longhuvip.com` | 涨停复盘、涨跌分析、情绪计数、板块全局排名、板块成员、异动/调研、偏离值、新高新低 | 市场温度、板块轮动、涨停梯队、研究事件 |
| `longhu_lhb` | `applhb.longhuvip.com` | 股东人数、机构跟踪、机构名称/股票列表、主题列表/详情 | 盘后机构与主题背景；不能倒灌同日盘中信号 |
| `longhu_article` | `apparticle.longhuvip.com` | 新闻快讯 TopList、文章/快讯列表 | 新闻事件时间线和次日研究上下文 |
| `xuangubao` | `flash-api.xuangubao.com.cn` | 涨停/炸板/跌停池、市场指标、强势股/板块接口 | 独立公开源对照；不等同 Longhu licensed evidence |
| `fupanwang` | `api.fupanwang.com` | 复盘直播接口 | 只做外部背景源，需单独记录可得性和时间 |

代码入口：`quant-service/app/licensed_stock_api.py`、`quant-service/app/longhu_vendor_source.py`、`quant-service/app/routers/licensed_stock_api.py`、`quant-service/app/licensed_stock_api_examples.json`。

## 已完成的低风险探针

探针通过 owner gateway 执行，只输出 errcode、分页数、字段名和列表长度，不输出任何凭据或大 payload。

- `GetKLineDay_W14`：`errcode=0`，可返回 300 个历史日线数组；`x` 为交易日，`y`、`vol`、`bal`、`turnover`、`CQ`、状态数组需要建立明确字段字典后才能进入 canonical。
- `GetStockTrendIncremental`：`errcode=0`，样例返回 241 个分钟点；当前解析器已强制校验交易日，不能把 HHMM-only 的旧会话重新标成今天。
- `GetStockPanKou`：`errcode=0`，返回 `real`、`weituo`、交易日/时间、涨停原因等；现有解析器已支持十档买卖盘和单边封板识别。
- `GetStockBid`：`errcode=0`，返回 `bid` 列表，可作为独立竞价/委托证据，不应与 `weituo` 混成同一来源。
- `GetWeiTuo_W14`：`errcode=0`，网关正确拆成多个 300 行物理页，证明分页上限和 owner proxy 的 batch 行为有效。
- `RealRankingInfo`、`GetHotPHB`、`Radar`、`GlobalCommon`：均返回有效排名/雷达/全球指数对象。
- `RiseFallAnalysis`、`ChangeStatistics`、`MoodNumCount`：均返回有效市场宽度或情绪对象，但字段尚未形成统一字典。
- `GetPlateInfo_w38`、`GetPlate_Info_QJ`、`ZhiShuStockList_W8`：返回板块/成员/涨停复盘数组，可支持板块强弱与梯队研究。
- `GetStockChouMa_New`、`StockChouMaByTimeNew_W5`、`GetZhangTingGene`：返回筹码和涨停基因对象，适合作为研究特征候选。
- `GuDongRenShu`、`JGStockListox`、`GetJGNameID`、`InfoList`、`InfoZS`、`InfoGet`：机构/股东/主题接口均返回 `errcode=0` 的结构化对象。
- `GetTopList`、`GetList`：新闻快讯接口可正常返回列表。
- `xuangubao` 和 `fupanwang` 也能收到响应，但它们是独立公开目标，必须保持单独的 source label 和可得时间。

## 对当前日线缺口的验证

对前一轮 Tushare 未发布日线的 symbol 进行了 Longhu `GetKLineDay_W14` 历史探针。接口能返回这些证券的历史数据，但在请求的 2026-09-02/03 窗口没有新增可确认的对应日线；因此没有把 Longhu 的旧/邻近日期数据冒充为缺失交易日。该结果支持当前 fail-closed 处理。

## 重要语义边界

1. `main_net` 是 Longhu 按订单规模分类的字段 13，不是机构身份，也不是 Level-2 撤单证据。
2. Longhu 的成交量/金额单位与 Tushare/腾讯不自动相同；现有代码只在明确边界转换，原始字段必须保留。
3. `GetKLineDay_W14`、涨停表现、龙虎榜/机构、文章和盘后主题都必须以供应商实际可得时间入库；不能按交易日直接当作盘中已知信息。
4. 盘中策略只能使用 exchange timestamp 通过 freshness/PIT gate 的数据。新闻、龙虎榜、盘后涨停表现、机构/股东和主题详情只能进入下一交易日背景或 replay。
5. `longhu_market_wide` 的板块/市场数值不能和东财净资金字段直接拼接；必须保留 source semantics 和字段字典。
6. 300 是物理页上限，不代表全市场完整性。每个逻辑请求必须保存页数、实际返回量、覆盖率、重复/冲突和失败原因。

## 建议实施顺序

### P0：证据层

- 新增有界 `longhu_capability_probe` 任务：按 target/action/params/request_key 幂等保存 raw response、页级元数据、provider availability 和 observed_at。
- 为每个操作增加 schema fingerprint：顶层 keys、数组长度、样本字段类型、分页完整性、交易日/时间字段。
- 先纳入 `GetStockPanKou`、`GetStockTrendIncremental`、`GetStockBid`、`RiseFallAnalysis`、`MoodNumCount`、`ZhiShuStockList_W8`。

### P1：研究特征

- K 线：完成 `x/y/vol/bal/turnover/CQ/state` 的字段字典和单位测试后，再作为 Longhu historical evidence 进入日线对照，不覆盖 Tushare canonical。
- 市场状态：构建 breadth、market temperature、limit-up continuation、hot-rank diffusion 四类 replay-only 特征。
- 盘口/大单：独立保存 Longhu depth、bid、big-order 三种来源，计算封单稳定性、委托不平衡和撤单代理，但明确不是交易所 Level-2。
- 板块/成员：只使用接口返回的 `PlateID` 和精确成员，不按中文名称跨源猜映射。

### P2：论文/策略研究

- 形成 `source-aligned multi-view` 数据集：行情、盘口、市场宽度、板块、筹码、机构/新闻各自保留 source clock。
- 做 walk-forward 与 purged event study：Longhu 盘中视图、Tushare 收盘视图、东财资金流视图分别建模，再测试增量信息量。
- 所有新因子先进入 shadow/replay，达到样本量、覆盖率、稳定性和样本外门禁后才考虑任何策略 promotion；不直接改变 live threshold。

本审计没有修改数据库和策略逻辑；它只验证了 owner gateway 的真实能力面，为下一步解码和证据化接入提供清单。
