# Longhu 能力对盘中与盘后研究的增强评估（2026-09-06）

## 结论

Longhu 对当前系统有两类确定增强：

1. 盘中可提高观察质量：`GetStockPanKou`、`GetStockBid`、`GetStockTrendIncremental`、`GetWeiTuo_W14` 能补充五档/十档快照、竞价和分钟路径，用于盘口压力、封板状态、开板与回封的研究证据。它们是聚合快照，不能被命名为完整逐笔 OFI、撤单率或队列优先级。
2. 盘后可提高市场状态和事件标签：`DailyLimitPerformance(1/2)`、`RiseFallAnalysis`、`MoodNumCount`、`GetPlateInfo_w38`、`GetPlate_Info_QJ`、`ZhiShuStockList_W8` 能补涨跌停梯队、炸板/封板、市场宽度和板块扩散；`longhu_lhb` 与 `longhu_article` 能补次日背景和注意力事件。

现有系统已经把 Longhu 盘口接入 bounded watchlist 的采集和回放链路。本次修正了盘口特征的来源归因：Longhu 和 Tencent 在特征、事件 evidence 和 signal contract 中保留独立 source label，避免跨源 delta 混算。其余新增接口仍先进入 raw/replay 层，等字段字典、覆盖率和 PIT 检验完成后再评估策略影响。

## 能力到当前链路的映射

| Longhu 能力 | 盘中用途 | 盘后用途 | 当前接入状态 | 下一步验证 |
|---|---|---|---|---|
| `GetStockPanKou` | OBI、深度、价差、封单存在性、封板/开板观察 | 封板质量和次日标签 | 已接入 Longhu-first order-book capture | 同源 freshness、单位、盘口源切换 |
| `GetStockBid` / `GetWeiTuo_W14` | 委买委卖压力和异常突变 | 盘口压力分层 | generic proxy 已可调用 | 建立字段 fixture；不宣称撤单事件 |
| `GetStockTrendIncremental` | 1/5/15 分钟价格路径、短周期标签 | 分钟路径回放 | 单股分钟接口已接入 | 明确交易日字段和 session completeness |
| `MorningBiddingList` | 集合竞价强弱、竞价到开盘的延续 | 次日开盘标签 | generic proxy | 接入 auction evidence，按上海时钟冻结 |
| `DailyLimitPerformance(1/2)`、`ZhangTingGene` | 盘中只用当时已公布的状态 | 封板率、炸板率、连板梯队 | generic proxy | 持久化 raw event，构造 T+1/T+3/T+5 标签 |
| `RiseFallAnalysis`、`MoodNumCount` | 市场 regime 门控候选 | 涨跌家数、涨跌停比、情绪状态 | generic proxy | 与现有 market snapshot 做 source-aligned 对照 |
| `GetPlateInfo_w38`、`GetPlate_Info_QJ`、`ZhiShuStockList_W8` | 板块 breadth/leader diffusion | 板块扩散、热点集中度 | industry close path 已有部分能力 | 保留 Longhu taxonomy 和 PIT 成分版本 |
| `GetStockChouMa*`、`GetBKJJ*`、`GetMainMonitor_w30`、`Radar`、`GetHotPHB` | 研究 shadow context | 筹码、热点和主力代理描述 | generic proxy | 字段/单位 schema 通过后再入特征 registry |
| `longhu_lhb` | 不进入同日盘中 | 次日注意力、席位集中度、反转标签 | generic proxy；当前 LHB 主路径为 Tushare | 按 publication time 冻结，席位不映射为真实机构 |
| `longhu_article` | 不进入同日盘中 | 盘后叙事时间线、注意力验证 | generic proxy | 文章 `available_at` 与发布时钟审计 |

## 可检验的盘中增强

对每个候选和每个快照保存：

- `obi1`、五档加权 `obi5`、相对 spread、五档 depth、单边盘口和封单量；跨股票使用横截面分位数。
- `GetStockTrendIncremental` 的 1/5/15 分钟 forward return 标签；特征只使用 `available_at <= observed_at` 的数据。
- 封板生命周期：首次触及、锁定、开板、回封、最后状态；封死涨停不假设可成交。
- Longhu 与 Tencent 的同刻价格/盘口差异、延迟和源切换，作为质量和风险变量，不把两源拼成一个 OFI。

实验应按市值、换手、ST、流动性、市场情绪分层，报告 AUC/IC、校准、滑点和成交可行性。中国高频研究发现订单不平衡对 5–30 分钟收益可能有正向预测、对 60–120 分钟可能反转，因此短周期确认和较长周期均值回归不能共用一个阈值；这只作为待复算假设，不能直接变成 live 规则。

## 可检验的盘后增强

每日收盘后以 `longhu_supplemental_evidence` 数据产品名义写入现有 `raw_market_observations`，保存：

- 市场宽度：涨跌家数、涨停/跌停、炸板、封板率、连板高度和情绪分位数。
- 板块扩散：成员覆盖、上涨占比、涨停扩散、top-k 集中度、leader-lag。
- 个股事件：涨停命中类型、开板次数/持续时间（只有接口确实提供时才写入），筹码和涨停基因作为 descriptive features。
- 龙虎榜：公告/可观测时间、席位集中度、净买卖描述和 T+1/T+3/T+5 outcome；`main_net` 仍保持“订单规模分类资金代理”，不能改名为机构净买或 Level-2。
- 文章：可观测时间、主题/情绪标签和后续市场状态，用于事件研究，不回灌同日盘中标签。

这些变量可增强当前 `post_close_candidate_screen`、`short_term_review`、`strategy_pattern_mining` 和 `daily_strategy_summary` 的解释能力。建议使用 purged walk-forward、embargo 和同源交易成本，涨停不可成交、停牌和 T+1 可卖约束必须进入标签与回测。

## 外部研究依据

- Cont、Kukanov、Stoikov 的 OFI 研究指出，短时间价格变化与 top-of-book order-flow imbalance 相关，并受市场深度影响：[The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402)。Longhu 聚合快照只能借鉴特征定义，不能声称拥有完整事件流。
- 中国订单不平衡研究：[Do order imbalances predict Chinese stock returns?](https://www.sciencedirect.com/science/article/pii/S0927538X15300056)；较新的中国高频结果报告 5–30 分钟与 60–120 分钟方向差异，见 [Zhang, Xie, Wang](https://doi.org/10.1080/16081625.2025.2604824)。
- DeepLOB 和不确定性模型可作为研究基线：[DeepLOB](https://arxiv.org/abs/1808.03668)、[BDLOB](https://arxiv.org/abs/1811.10041)。Longhu 粒度和样本量不足以直接照搬模型结论。
- 中国涨停与注意力：[Investor attention, aggregate limit-hits, and stock returns](https://doi.org/10.1016/j.irfa.2022.102265)、[Statistical Properties and Pre-hit Dynamics](https://pmc.ncbi.nlm.nih.gov/articles/PMC4395215/)、[Forecasting Volatility with Price Limit Hits](https://doi.org/10.1080/1540496X.2018.1532888)。旧样本只用于标签设计和待复算基线。
- 数据研究工程：[Microsoft Qlib](https://github.com/microsoft/qlib) 及其 [DataHandler/Processor 文档](https://github.com/microsoft/qlib/blob/main/docs/component/data.rst) 可借鉴训练期拟合、PIT 特征处理和 T+1 标签边界。

## 接入顺序

1. P0：来源归因、单位、交易日、freshness 和 schema fixtures；把竞价和市场情绪 raw evidence 纳入盘后 stage receipt。
2. P1：构造盘口压力、封板生命周期、情绪 regime、板块扩散和 LHB 注意力标签，全部标为 `research_only/replay_only/live_effect=none`。
3. P2：固定 30/60/90 个交易日 replay，做 purged walk-forward 与成本检验；只有 promotion record、覆盖率、样本量、样本外稳定性均满足门禁时，才讨论策略层消费。

当前本地库的盘口历史仍以 Tencent 为主，Longhu rows 需要在 owner 端按交易日确认后再宣称覆盖；缺失时回退基线并保留故障原因，不补零、不借用邻日数据。
