# 量化与分析师联合系统计划完成矩阵

更新时间：2026-09-20。本文是四份主计划的当前状态索引，不把历史数据、回放样本或统计晋级缺口伪装成完成。

## 状态定义

- **已完成**：代码、测试和运行态均有直接证据。
- **研究中**：在线证据已落库，但统计门禁尚未满足；不影响实时规则、不自动调参。
- **暂停**：需要用户明确授权或外部资源，当前不执行。
- **工程余项**：不改变策略结论的工程加固，允许继续渐进推进。

## 2026-09-20 owner clarification and peer handoff

The older evidence entries below describe the peer's pre-upgrade read-back and
are historical, not the current owner claim. Owner production remains on
`20260919_0106` with the real runtime contract exposed by
`GET /api/v1/peer/contract`; owner release announcements are published through
`quant.owner_deploy_events`. The peer source now validates only the two actual
bar/factor projections, reads factor semantics from `daily_adjustment_factors.raw`,
uses report-only contract mode for the first restart, and exposes the owner's
deployment notice in `/health.owner_deploy`. It no longer blocks on the
nonexistent semantic/cold/index objects described by the superseded prose
handoff. Owner, not this workstation, performs the remote peer release and
runtime acceptance; no owner migration is run from peer.
Recommendation generation now carries the owner `multi-source-feature-v4`
contract through a same-session close gate: a prior-date bar remains auditable
but cannot become a `research_candidate` for the requested date.

## P0 数据与因果正确性

| 项目 | 状态 | 当前证据 |
| --- | --- | --- |
| 复权研究价与原始执行价分离 | 已完成止血 | `app/research_prices.py`；生产特征、盘后结构、factor lab、训练和 watchlist 策略统一要求持久化 `adjustment_state=complete` + cumulative-factor semantics；owner 的 `longhu_qfq_derived` 与 Tushare checkpoint 均须显式 allow-list，缺因子或语义不完整显式阻断 |
| ST、停牌、涨跌停和时区门禁 | 已完成 | `P0_DATA_CORRECTNESS_STATUS.md`；四种涨跌停规则、上海日期和 `upsert_bar` SQL 回归。实时 `live_policy` 与纸面成交共用 `ashare_reality.price_limit_state`：优先精确 `stk_limit`，缺位才按主板/创业科创/北交/ST 的正确价格带兜底 |
| 实时市场/数据/纸面风险 gate | 已完成 | `app/live_policy.py`、`app/paper_portfolio.py`；risk-off、质量、T+1、日亏、回撤、单票和板块集中度均可解释阻断 |
| 盘后同日完成语义 | 已完成 | latest-attempt/latest-completed 分离及回归测试 |
| 分析师唯一 promotion registry | 已完成（默认零权重） | `app/analyst_promotion.py`；未人工批准永远 `weight=0`；scorecard 只使用版本化远端 `analyst_claims`，不再混入已退役的本地 `analyst_signals` |
| 分析师交易日时钟统一 | 已完成 | claim outcome 入场日、研究容量与异步读模型均以 `available_at AT TIME ZONE 'Asia/Shanghai'` 取交易日；`received_at` 保持唯一 live 时钟，安强 `stated_at` 仍仅进入独立作者时点复盘 |
| 分析师报告差量同步公平性 | 已完成止血 | 每个分析师每轮均检查一页最多 100 条的**文字元数据**；`max_items` 仅限制已变化报告正文的导入数。超出预算的变化不推进游标，后续轮次必重试；不请求图片、视频或媒体 URL |
| 盘中固定期限结算 | 已完成修复 | 5/15/30m 只读同一连续竞价段、目标后 90 秒内的本地腾讯报价；盘后重算仍读取原始有界窗口，午休/隔夜明确 unavailable |
| 安强作者时点动作复盘 | 已完成（仅 replay） | `author-stated-local-quote-session-bounded-replay-v1` 独立账本；不进入 live 因子、权重或飞书决策 |

## P1 运行工程

| 项目 | 状态 | 当前证据 |
| --- | --- | --- |
| provider registry、共享限频、有限重试、熔断 | 已完成 | Tushare/公共源/远端分析师文本源均使用生命周期 HTTP client；远端每次触发只短暂附加 Bearer，不在连接池、日志或状态中保留凭据；Retry-After、跨副本预约和 health 均已接入。通用 fallback 与盘中 capability 熔断查询均在原生异步读池完成，避免在请求前占用同步执行器。公共日线的前复权腾讯响应仅作为原始证据，不会回退写入 canonical 不复权序列 |
| 成功/失败延迟与错误脱敏 | 已完成主要路径 | `provider_health.py`；Tushare、腾讯、Sina、东财、AKShare、巨潮公告、BaoStock、Super GET 主要路径耗时进入 health/Prometheus；兼容路径允许延迟缺省且不覆盖已有值 |
| 盘中调度、租约、outbox、飞书恢复 | 已完成 | 开盘预检、`runtime_leases`、投递回执和连续失败治理；个股 outbox 的 pending receipt/due retry 与板块轮动遗留行的 suppression 使用异步池，复杂个股投递状态机仍保持原有有界单事务。观察池上一同源报价、EAC 首次确认、最近 61 根复权日线因子及 `标的×分钟桶` 历史量能基线均按一轮批量读取，避免随标的数线性增加小查询。生产扫描的显式观察池容量和精确成员读取也使用异步池；无指定 symbol 固定上限+1 行以 fail-closed 发现超量，成员仍按点时精确关系过滤。兼容 SSE 日历状态继续经注入执行器，生产盘中/调度/板块曲线门禁使用异步池；周末、日历缺口、关闭日和本地池异常均 fail-closed，并共享明确原因码 |
| n8n 孤儿执行审计收口 | 已完成 | `scripts/reconcile-stale-n8n-executions.sh` 只标记超过 10 分钟仍为 `running` 的记录为 `crashed`，绝不删除执行证据；LaunchAgent 每 15 分钟运行一次。无候选时脚本不再创建备份目录，避免运行日志/审计目录无界累积 |
| 盘后一键刷新跨进程恢复回执 | 已完成 | `post-close-refresh:{stage}:{trade_date}` 通过唯一 `automation_runs` 回执恢复：完成阶段不重跑且返回 `resumed_from_receipt`，partial/blocked/failed 才重试同一行；真实 PostgreSQL JSONB、终态时间戳与 partial 重开均有契约测试。阶段装配已独立至 `post_close_refresh_service.py`，控制面缺失会阻断依赖策略但不阻断独立证据阶段 |
| 午盘/收盘复盘重启恢复 | 已完成 | `strategy_review_runs` 的同交易日/session 且 `report.status=completed` 作为 durable checkpoint receipt；重启后检查点不重复刷新行情、结算或评分，未完成记录仍在原两分钟窗口内重试 |
| 日终研究摘要重启恢复 | 已完成 | `strategy_day_summaries` 的 `sent`/`disabled`/`suppressed` 是终态回执，重启后不重建摘要；但 `suppressed + post_close=blocked` 会在 19:15–22:00 同日窗口继续重试，避免晚发布日线留下旧 blocked 摘要；不向飞书推送候选池 |
| 常驻循环锁与生命周期可观测性 | 已完成 | durable `runtime_leases` 继续作为跨进程持锁真源；背景循环的 acquire/renew/release 均在原生 async 池执行，保留仅过期可接管及 holder 限定的原子 SQL，避免其与同步仓储争用 4 槽执行器。`/health.runtime_loops` 增加本进程 worker 的 running/waiting/lease-lost/backoff/error 生命周期状态，解释未启动/交接/异常退出而不把它冒充为业务心跳，也不记录 provider payload 或凭据。应用生命周期以唯一标签注册命名 task，关闭时统一取消并等待，重复 loop label 在启动前 fail-closed |
| 存储/备份/恢复前校验 | 已完成 | 总研究空间**硬上限** 40 GiB、日频 P2 证据热库**硬上限** 36 GiB（为受限历史保留 4 GiB artifact 余量）；存储测量和 60 秒准入缓存已收敛至 `research_storage_admission.py`。80% 预警、90% 仅暂停非必要高频采集，观察池风险/提醒不受影响。每日 PostgreSQL/workflow 备份除 14 天保留和同日去重外，另有 8 GiB 容量上限；创建 staging 前会按最近一份完整日备份的实测体积预留空间，只会回收严格命名的旧完成日备份。开盘预检同时校验该容量、`pg_restore -l` manifest 和 workflow JSON |
| owner 数据库、双通道与运行时 schema | 代码已按 owner 2026-09-20 clarification 对齐，待本轮 peer 重启验收 | peer 只读验证 `trading_hareness`、lineage `20260919_0106`、`canonical_bars_daily` 的实际 bar 列，以及 `daily_adjustment_factors.raw`。`adjustment_state` 是计算值，不是列；因子语义从 raw JSON 读取；owner 冷层为证据表，peer 不依赖市场数据 `_cold` 或 owner storage-tiers API。5432 盘中、5433 批量窗口仍保持。 |
| owner 因子控制面不再回源 Tushare | 已完成代码合同；待 owner/peer 运行验收 | `app/owner_factor_repository.py` 从 owner 已落库的 Longhu/允许 checkpoint 因子读取同日控制截面；`full_market_daily_controls_sync.py` 和显式 core 控制路径不再调用 Tushare `adj_factor`，缺少 95% 点时覆盖即阻断；其余 `daily_basic`、`stk_limit`、`suspend_d` 控制仍独立走原有 provider。 |
| 纸面组合展示与风险阻断 | 已完成 | 前端展示净值、总/净暴露、回撤、可卖量、板块暴露和风险事件；成员按观察日点时映射；新 entry 受日亏/回撤/集中度限制 |
| 策略族级健康/漂移投影 | 已完成（研究监控） | `/api/v1/strategy/health` 按策略族聚合事件和去重 episode；仅显示门禁/运营建议，不调阈值、不变更分析师权重 |
| 版本化 FactorSpec 与 episode 契约 | 已完成（证据/shadow） | `strategy_contracts.py` / `intraday_factor_contracts.py`；每个已登记盘中因子携带版本、输入、时钟、质量门禁、训练/推理许可和弃用日期。当前 `training_permitted=false`，只能进证据和归因，不能接入实时评分 |
| 持久化 SignalSpec / Insight 证据契约 | 已完成（证据/shadow） | 扫描器在写入事件前把策略族、版本、30m 结算期限、5 分钟有效期、稳定失效原因码、证据引用与风险旗标写入 `conditions.signal_contract`；不改变评分、提醒阈值或订单边界 |
| 观察池实时覆盖上限 | 已完成 | 40 只为已核验腾讯批量盘口上限；第 41 只起扫描 fail-closed 并落 `watchlist_capacity`，不会静默截断 |
| 盘中规则输入快照与留存 | 已完成（为未来回放采证） | 每轮每只观察股在调用纯 `signal_rules` 前，将最小 watch/同刻报价/前帧/日因子/分钟特征/精确同伴上下文冻结为 `intraday_rule_input_snapshots`，无信号行也记录；不保存重复 provider raw。默认留存 90 天（下限 60、上限 120），同源观察池报价同步裁至该窗口；盘口/秒级交叉确认仍按其独立短窗口保留。重复的 `suppressed/confirming` 事件也在 90 天后才有界清理，且仅当无投递/无 outcome；`confirmed/alerted`、回执、纸面决策和结算证据永不由该清理删除。只积累未来实时证据，不补拉历史、不改变 live 阈值 |
| 实时报价来源与时间戳门禁 | 已完成 | 同轮腾讯观察池批量报价才可确认，重点窗口须在 20 秒、其他窗口 45 秒内；新浪/腾讯全 A 快照严格标为证据来源并按真实 provider 落库，不可直接推送。进入个股条件的腾讯全 A 资金流带快照年龄，超过 45 秒不得确认新的 entry；`/intraday/services/status` 与前端实时卡同时展示资金流快照状态、年龄和“可确认/仅展示”边界，过期时决策路径明确标为 degraded。Super GET 仅作可选交叉确认：新鲜且价格冲突会阻断；缺失/陈旧不会替代同轮腾讯价格的新鲜度门禁，也会随证据保存并在提醒中披露。普通时段的 Super GET `rt_min` 每轮至多验证 4 只，但游标按实际观察池长度闭环推进，36 只池在 9 轮内全部覆盖 |
| 全 A 横截面并发缓存 | 已完成 | `SharedAsyncSnapshot` 将腾讯全 A 资金流横截面限定为 30 秒 TTL：并发扫描只共用一条在途上游请求，失败不写成新鲜缓存；观察池仍独立走 40 只以内腾讯批量报价，避免慢横截面拖慢 10 秒窗口 |
| THS 概念精确成员恢复 | 已完成 | 成员同步仅接受 `ths_member` 的精确代码关系；“全部 provider 临时熔断”失败在线路冷却后可受限恢复，格式/截断等失败仍保留三次上限。2026-08-16 对 2026-08-14 的唯一遗留 `885338.TI` 恢复成功，概念覆盖为 **387/387**、成员为 70,998；未做中文名称猜测 |
| 原生 async repository | 部分完成 | 策略决策/复盘/盘后候选、策略健康、策略消融、纸面研究、事件/龙虎榜、涨停/连板模式、研究目录，以及市场快照/原始 Tushare/分钟导入/最新推荐/指标计数、研究总览、**Agent 自动化回执、市场资金流特征、全部板块/概念/精确成员证据、涨停联动候选与关系输入、Prompt Lab 状态、已落库分析师市场复盘、有界市场评测、分钟时间轴、分析师研究状态及归档游标/总览、提供者目录/能力/健康、PIT 文本因子、跨 n8n public schema 的分析师同步健康、盘中扫描前的板块缓存、Super GET 交叉确认、观察池/精确成员输入、盘中 outbox pending/due 输入、THS 概念成员批次的 flow/progress 本地状态、同步 universe 与盘后核心 basket 输入、背景循环 runtime lease、通用 fallback/盘中熔断状态、生产 SSE 日历/连续竞价门禁**等 GET 已使用 `AsyncDatabase`；AST 回归要求其余面板 GET 不能回退为同步 DB 路径。保留的 3 个同步 GET 是明确运维例外：静态 Agent/路线图、注入式盘中状态兼容分支。其余读写仓储仍经有界同步执行器，健康页显示异步池水位 |
| `main.py` 完全拆分 | 工程余项 | router/read-model/纯规则已拆出；盘中侧现进一步分离了观察池报价采集、扫描总编排、单事务信号持久化及其生产事务适配、一秒级 Super GET 观察/清理、收盘分钟画像、腾讯盘口及板块曲线 runtime 装配、板块/秒级交叉确认、观察池/精确成员、涨停联动 research runtime、熔断状态和生产交易日历的异步预读、观察池配置/受限历史补水、全板块成员批次与 THS 概念成员恢复。应用启动/关闭顺序、复盘/盘后 runner 及日终摘要的生产装配亦移至独立模块。容量/覆盖度/就绪度、单股窗口/claim 证据、单股多源研究、特征读取、分析师文本/成绩单门禁、概念涨停候选持久化、日线/板块同步、盘后阶段装配、盘口/分钟/板块曲线、远端文字同步、本地研究实验/快照、Tushare fetch ledger、研究维护写入、研究存储准入及盘后形态样本/分钟挖掘均有独立模块；旧 n8n 直写分析师信号入口已删除。主文件当前约 5,462 行；仍保留明确的 composition root、兼容入口和少量写服务，旧兼容快照待获授权的回放验证后再删除。 |

## P2 数据地基与 P3 验证

| 项目 | 状态 | 准入条件 |
| --- | --- | --- |
| 三年日线、复权、停牌、涨跌停与日频板块/龙虎榜证据 | 复权语义变更后覆盖投影待重建；P2 暂停；分钟来源时钟仍阻断整体 P2 | 旧投影曾报告 737 个完整横截面日，但它没有排除 `adjustment_state` 未完成的 bars，不能作为当前门禁证据。服务现在按新的 coverage definition 过滤旧投影，发现陈旧时快速返回 `coverage_stale`，等待 bounded refresh；重建后才重新评估 720 日/1,090 日门槛。物化表仍只是一日一行的可重建投影，以 canonical、daily_basic、trade_limits 和 PIT membership 为真源。47 peer 所连 owner 库只保留近期控制面，不能代替本地历史研究库。 |
| 因子研究的点时成分与内存边界 | 已完成逻辑地基 | SQL 因子面板按 `universe_membership_history`、上市/退市日期过滤；兼容 Python 引擎仅允许不超过 250 个成分的诊断，广义全 A 在读取前 fail-closed，必须走数据库内的有界 SQL 引擎。当前历史覆盖尚未回填，故不能将该地基误作完整历史验证 |
| 历史分钟回放 | 因果时间合同已完成；价格路径回放继续暂停 | 本地已有 858,514 根、1,533 只标的、66 个交易日的分钟线和 3,625 个完成导入，但 `source_available_at` 为 0；其中仅 8 日是同日抓取，其余为事后补拉。readiness 现要求至少 60 日的来源时钟位于 `bar_time..bar_time+10m`，并按 K 线交易日计数，不能用文件导入日凑门槛。`bar_time`、供应商时钟和本地 `available_at` 继续严格分离。新增分钟回填回执会逐批报告 `explicit_source_clock_bars`、`causally_clocked_bars` 和 `missing_source_clock_bars`；`stk_mins` 没有明确供应商时钟时不补写、不晋级。 |
| 未来盘中规则回放证据 | 已完成采证与一致性重放基础，验证未开始 | `intraday_rule_input_snapshots` 的 v2 合同冻结核心规则与同刻 policy/risk gate 输入；旧 v1 仅兼容 core-only。`/api/v1/strategies/intraday/replay-recorded-inputs` 无 provider、历史导入、阈值拟合或订单能力，并明确排除 event state、执行和收益结论。数据有 60–120 天有界留存，只从上线后的真实扫描累积，不改变既有事件、不可替代获授权的历史分钟数据或完整市场横截面 |
| 本地已录制信号事件生命周期 replay | 已完成（非价格回测） | `/api/v1/strategies/intraday/replay-recorded-events` 只读 `intraday_signal_events`；按 availability 时钟写入幂等 `input_hash`/`trace_hash`，不请求 provider、不拉历史、不拟合阈值、不生成订单 |
| T+1/涨跌停/停牌/费用/滑点回放撮合 | 基础契约已完成，验证暂停 | `ashare_reality.py` 是实时风险、纸面成交和未来回放共用的整手、T+1、停牌、不同板块/ST 涨跌停、佣金/印花税/滑点及 non-fill 纯模型；尚无获授权历史路径，故不运行历史撮合或把它当策略验证 |
| purged walk-forward、embargo、DSR/PBO | 暂停 | 日线/分钟日期交集已达 66 日，confirmed 信号 385、matured outcome 247；但全部分钟线缺 `source_available_at`，所以这些数量不能绕过 P2 来源时钟门禁。每 cohort 30 条仍需在可准入事件上复核。 |
| 盘中阈值重校准 | 禁止启动 | P3 样本门禁通过且样本外胜出规则基线 |

## 分析师与模型演化

| 项目 | 状态 | 当前边界 |
| --- | --- | --- |
| 报告/消息差量同步、`received_at`、版本和文字证据 | 已完成 | 不下载远端图片、音视频或媒体 URL；报告与消息支路独立 |
| 分析师观点 outcome 与专家画像 | 研究中 | 当前成熟 outcome/eligible 样本不足，权重保持零 |
| Prompt Lab champion/challenger | 研究中（未晋级） | 11,313 个候选已物化；按上海可用日做时间外留出，但金标为 0，无法比较/晋级 |
| Qlib/LightGBM/LEAN 独立训练与模型注册 | 原生日频 CPU 基线已真实运行；跨框架验证未完成，现有就绪投影 fail-closed | `research_model_training_worker.py` 在 4 GiB/2 CPU 独立 profile 中完成点时导出、代码/数据/artifact SHA-256、3 个预登记 OOF trial 与 append-only trial 账本。本地真实运行 42,592 样本/703 日，最佳 ROC AUC 0.5164，但 log-loss 0.7094 劣于常数 0.7026，故模型登记为 `rejected`、`live_effect=none`。`research_framework_readiness.py` 和 `/api/v1/research-frameworks` 现在明确区分可选依赖、点时数据门槛、适配器状态与 benchmark 是否执行；完整 Alpha158/LightGBM/Qlib/LEAN benchmark 仍缺，不能晋级。 |
| RL / contextual bandit | 暂停 | 只能在 Phase 0–5 通过后离线 challenger，不得改 live champion |

## 当前验收证据

### 2026-09-19 owner handoff 后当前会话重检（历史，非当前远端状态）

> 本节中的远端 peer 回执是上一轮会话的历史快照。2026-09-20 owner
> clarification 已明确取消 materialized semantic columns、市场 `_cold` 孪生表和
> guard-index 依赖；本工作树已将启动门与读 SQL 改为实际 runtime contract，但新的
> peer 重启/远端回读必须重新执行后才能把“运行态”标为完成。

- 当前工作树与 `origin/main` 同为 `590e0167b137a62efdb41d5ec8852c518a7d05b1`；owner 提供的生产版本为 `20260919T160546-6f7da042db5d-clean`，不是仓库的 peer 分支，故以交接文档作为 owner 侧权威版本，不把本地 HEAD 冒充生产发布。
- 上一轮记录曾显示远端切换到 `20260919T000300Z-peer-owner-handoff`；owner 2026-09-20 已将其回滚为最后一个跑通的 `20260919T044218Z-owner-cutover-preflight`，并报告四个容器 healthy。上一轮的归档/wheel 校验与双 lane 回读不能替代这次重新发布后的验收。
- owner 报告的当前远端状态是 `db-tunnel` 与 `db-batch-tunnel` healthy、5432/5433 均可用；本工作区没有对远端容器做重启，故不把该报告冒充为本会话实测。
- 旧 release 曾因把交接文档中的控制面板值误当成 owner 列而 fail-closed；该启动门已改为只检查实际 bar/factor 列、raw JSON 语义和数据库 lineage。peer 不执行 owner DDL。
- 新 release 仍需要由 owner 重新启动两个应用容器，再做 `/health`、owner contract、权限、lineage、发布公告和真实 deadlock 验收；owner 不需要为此改表。
- owner 当前 `stock_peer` 角色的 15 分钟 statement timeout / 5 分钟 idle transaction timeout 以及历史 deadlock 计数仍需在 peer 新版本上线后重新读取；历史累计 deadlocks=4 不能提前宣称已经消失。
- 上一份旧 release verifier 回执曾显示真实 5433 lane 上的 `stock_peer` 仍有 `INHERIT`、`quant` schema CREATE、canonical 表 DML、父角色成员关系和可写 relation；这不是新 owner handoff 后的当前权限证明，必须在新 release 上重新执行。`bootstrap-local-peer.ps1` 已改为撤销全部父角色成员关系、显式设置 `NOINHERIT`、把 database/schema/quant 对象 ownership 转回 owner admin、撤销 database/schema/table/sequence/function/procedure 权限、再授予 USAGE+SELECT，并在执行后要求 membership count、全 schema 可写 relation/sequence 数量与可执行 SECURITY DEFINER 数量都归零。
- 复权门禁升级后，旧的 `replay_readiness_daily_coverage` 仍是上一版定义；同步和异步回放读取都会过滤它并在约 0.4 秒内返回 `readiness_query_status=coverage_stale`，不会从 dashboard GET 触发全历史扫描。必须先由 bounded refresh 重建当前定义，再重新发布 P2 计数。
- owner 的数据守卫是 owner 进程内 SQL（`identity_factor_leak_sql`、`factor_value_mismatch_sql`），不是 peer 必须存在的索引；peer 只在读取因子时使用 raw JSON 语义和正数/可用时间门禁。
- 上一次工作树重建后的 quant-service discovery 为 `1831/1831`，shared-peer/scripts 为 `100/100`（1 项环境跳过）；OpenAPI contract 为 `8 paths / 180 total`，前端 `api:check`、typecheck 和 production build 均通过。该证据只证明历史工作树，不替代远端 release 验收。
- 2026-09-20 owner-clarification 适配后的本地定向回归、owner contract gate、发布公告投影和 shared-peer verifier 均通过；远端应用容器尚未在本会话重启。
- （已被 2026-09-20 owner clarification 取代）旧版本地 registry 曾将市场表声明为 365 天 hot + `_cold`；这只保留给本地研究库实验，不是 owner 生产启动门。peer 生产读路径不 union owner 市场 `_cold`，只读 owner 实际授予的热表/因子表并在缺失时 fail-closed。
- 全市场事件研究同样绑定一次原子 hot+cold 布局快照；短期反转、量能事件、板块反转与盘后结构的跨 session 收益改用 `raw price × cumulative factor`（当前 owner 来源为 `longhu_qfq_derived`，licensed Tushare 仅作 checkpoint），涨跌停可成交性仍使用交易所 raw price。真实 PostgreSQL 增加了“原价减半、累计因子翻倍，研究收益必须为 0 而非 -50%”的公司行动回归。
- 继续重检策略读路径后，观察池日因子和全市场事件研究都改为从 `daily_adjustment_factors` 做点时 provider 优先选择；canonical bar 的 `adj_factor` 不再单独构成研究价格来源。缺少完整、正数、可用时间合规的 owner 因子时，研究因子与跨日收益均 fail-closed。
- 历史容量/覆盖投影也已改为读取同一原子 hot+cold 家族，避免分层作业开始搬迁后看板把最早日期、完整横截面天数和 fundamentals/limit 覆盖误报为骤降；它只改变证据读取范围，不放宽任何 readiness 阈值。

### 2026-09-20 owner 机器契约与 peer 静默失败修复（当前工作树）

- peer 启动门已改为调用 owner 只读 `/api/v1/peer/contract`；默认 `report_only` 写入本地 receipt，只有下一次启动且已有 receipt 后 `PEER_OWNER_CONTRACT_MODE=block` 才会真正阻断。不存在的 `adjustment_state`/`factor_semantics`/`retired_at` 列、行情 `_cold` 孪生表、guard index 与 `storage-tiers` 接口不再作为 owner 契约断言。
- 因子研究 SQL 接受 owner 实际 `raw->>'factor_semantics'='corporate_action_cumulative'`，保留历史标签兼容；身份因子仍退役，缺行仍 fail-closed。修复 `owner_storage._row_value` 对 tuple tablespace 的索引错误。
- raw-overflow allowlist 纳入 `daily_bar`，`next/ack/failure` 的 4xx、数据库异常现在带 stream/batch 上下文日志；HTTP 4xx/5xx 与同步/异步数据库语句均记录 ERROR/WARNING（SQL 与有界参数）。连接增加 `application_name`，compose 全部 peer 容器加入 `json-file` 50m×5 轮转。
- 发布归档新增完整性门：`compose.yaml`、`OPERATIONS.md`、`frontend/`、`workflows/`、`certs/`、systemd 依赖脚本缺一不可；新增 `package-peer-release.sh`，排除 state/backups/logs 等运行数据后一次性校验归档内容，避免再次出现 `203/EXEC`。
- 写入申报已补为 [`PEER_WRITE_DECLARATION.md`](PEER_WRITE_DECLARATION.md)，覆盖 runtime lease、provider/fetch ledger、原始/日线/因子/板块/intraday/research 证据表和批量窗口约束。
- 当前工作树的 owner contract、owner deployment notice、instrument lock retry、shared-peer verifier 定向回归通过；scripts verifier **15 项通过**；OpenAPI **8 paths/180 operations**；前端 typecheck/build/api:check 通过；shared-peer compose config 在注入必需密钥占位值后通过。尚未在本会话重启远端 peer 容器或声称 owner 线上 deadlock 已消失。

### 2026-09-19 owner 存储与隧道兼容（历史交接回执，已被 2026-09-20 口径取代）

- 交接文档记录远端 active source `20260919T044218Z-owner-cutover-preflight` 曾运行；本次已通过 direct SSH 和真实 5433 lane 重新读取，active symlink 仍指向该版本，batch listener/container 可用，但新 cutover verifier 仍为 blocked。历史的 1,788/1,788 与 provider catalog 0 问题只能视为旧 release 回执，不能替代当前工作树或新 release 验收。
- 旧回执中关于五张 `_cold`、四态列、`retired_at`、guard indexes 以及 `ds0004/ds0005` 的判断均已作废；owner 不会为 peer 执行这些 DDL。当前 peer 只检查实际 bar/factor 列、`raw` 语义和 owner lineage，仍保持 `QUANT_SKIP_MIGRATIONS=true`。
- `verify-owner-cutover.py --stage lane` 当前已通过 listener、batch container 和真实 SQL 回读，但因 owner `partial_cutover` 仍返回 blocked；`--stage complete` 还明确看到 scheduler 为 `PGHOST=db-tunnel/PGPORT=5432`。scheduler 继续使用旧通道且 `QUANT_SKIP_MIGRATIONS=true`，没有提前切换。
- 新增 API/session lane 与 5433 batch lane 的 `database_name + quant.alembic_version` lineage 对账；即使两边端口都 healthy、也必须证明指向同一个 owner 数据库后才可通过 lane gate。
- 下一版 immutable peer release 的激活脚本已增加双通道发布门：缺少 batch-capable SSH entrypoint、`compose.intraday-owner.yaml`、owner verifier 或 batch helper 的归档会在切换 symlink 前拒绝；当前运行中的旧 release 不做原地覆盖。
- session guard 已修复为批量通道未启用时不误报；当 scheduler 切到 5433、批量容器已存在或显式配置 required 后，才把它升级为必须自愈的依赖。远端 systemd heal 回执恢复为 success/0。

### 2026-09-18 数据地基与控制面复核

- 修复 readiness 将 `SET LOCAL` 与查询拼成一条 psycopg 多语句而被误报为 timeout 的问题；新增 `replay_readiness_daily_coverage` 一日一行物化投影，并在年度回填、历史成员重建和同日 controls 刷新后更新。旧投影曾有 749 日/737 个完整横截面日，但复权语义门禁升级后必须重建，不能继续沿用。
- 当前本地 gate：复权覆盖投影 `coverage_stale`（故 P2/P3 fail-closed），offline imports 3625/1，confirmed signal 385/200；分钟原始日期虽有 66 日，但 P2/P3 只承认 0/60 个因果时钟日。成熟 outcome 为 247，前向规则输入为 12 个交易日、211,327 行。
- 不能用 858,514 根分钟线的本地 `available_at` 补写供应商时钟：714,858 根来自事后历史回填，只有 143,656 根分布在 8 个同日抓取交易日。任何 purged walk-forward、DSR/PBO、阈值重校准和模型 promotion 继续 fail closed，`live_effect=none`。
- `research_model_registry` 在旧 owner schema 缺失时现在返回显式 `registry_status=schema_unavailable`，不再产生 HTTP 500；这不是用空结果伪装迁移完成，owner 迁移仍必须由 owner release lineage 执行。
- 本地独立 worker 已产生首个真实不可变训练快照和 3 条 trial 记录；选中候选未战胜常数 log-loss 基线，已 fail-closed 标记为 `rejected`。worker 默认 64 个哈希固定标的是经过 4 GiB 硬限制实测的审计样本，不将其宣传为完整全市场或跨框架训练。

### 2026-09-06 当前复核

- 工程回归：quant-service 容器内 Python discovery **1,419/1,419** 通过；新增离线模型基线和不可变特征导出合同的 4 项测试均通过。`git diff --check`、OpenAPI contract（7 paths/178 operations）、前端 `api:generate`、`api:check`、`typecheck` 和 production build 均通过。
- 运行态：数据库迁移 head 为 `20260906_0094`；`/health` 返回 `ok`，研究存储约占热库预算 52.1%，服务容器健康。`/api/v1/research/models` 当前为空，符合尚无合格真实 artifact 的预期；`/api/v1/research/longhu/replay-readiness` 为 `accumulating`，已观察/完整交易日均为 0，未进入训练或回放门禁。
- 新增研究边界：`research_feature_export.py` 只接受已物化的点时特征/标签表，强制合同版本、唯一 `symbol×exchange_date`、有限数值、可用时间先后关系，并返回不可变 SHA-256 数据快照；`research_model_training.py` 仅消费该类离线表执行 60 日训练/5 日 embargo/20 日 OOF 基线，任何结果继续 `research_only/replay_only/live_effect=none`。
- 仍未完成：跨框架 benchmark、Longhu 10 合同的 60 个完整交易日、分钟来源可用时钟与价格路径回放、P3 统计门禁和 Prompt Lab 金标晋级。缺失数据或外部资源时保持 fail-closed，不自动启动模型晋级或 RL。

### 2026-09-08 飞书群转发：卡片 2.0 接入

- 链路位置更正：飞书群历史转发（group-relay）现由远端 edge `47.114.113.152` 上的 `feishu-relay-edge-adapter` 持有写权（宿主 PostgreSQL `n8n_relay`），本地 adapter 的 `FEISHU_GROUP_RELAY_ENABLED=false`、本地游标停在 2026-08-31 是搬迁后的预期状态，不是故障；09-05 条目里"5 个 source 轮询停在 08-31"描述的是本地陈旧账本。edge 上 5 条路由当日均有新消息（xiaojie/xiaolan/liuzi/quanneng/liwei，08 日最新到 20:14）。
- `#anqiang` 路由于 09-08 由「马安强(1)」（OAuth 用户已被移出，`Bot/User can NOT be out of the chat`）切换到「安强训练营1（50）」，切换当日 5 条 `skipped_bootstrap` 为换群引导跳过的正常行为。
- 根因：该群 bot 改发**卡片 JSON 2.0**（`schema:"2.0"`，单个 `markdown` 元素）。默认 `im/v1/messages` 读取只能降级成 1.0 结构，2.0 无法降级，返回固定文案 `请升级至最新版本客户端，以查看内容` + 已删除的公共 image_key；当日 7 张卡片全部如此，列表/详情接口、tenant/user 令牌、5 种客户端版本头均无差别。官方文档：`card_msg_content_type=user_card_content` 返回发送时原始卡片 JSON。实测该参数在列表与详情接口均生效，7 张卡片解出与客户端一致的交易观点全文。
- 修复（adapter `3b462e0` + 本次）：两处 `messageList` 加 `card_msg_content_type=user_card_content`；文本提取器覆盖 2.0 的 `lark_md`/`div.text`/`header.title`；资源收集识别 `img_key` 并按现有图片路径上传后以 `post` 转发；升级横幅文案被过滤，全部不可得时明确提示"卡片内容无法通过接口获取"。`portable_summary_version` 保持不变，已投递消息不重发、不改写。Feishu adapter 回归 **85/85**。
- 验收：edge 部署 `edge-2026.09.08-card-image-relay` 及本次镜像后，以新到达的 2.0 卡片在汇总群出现 `#anqiang` 正文（而非升级横幅）为闭环回执。
- 部署回执：`edge-2026.09.08-card-v2`（adapter `07a7aad`）已于 09-08 21:2x 在 edge 生效（GHCR 某 blob 自边缘 TLS 超时，改为本机拉取→`docker save`→边缘 `docker load` 后完成）；容器内两处 `messageList` 均带 `card_msg_content_type`，健康校验 `07a7aad/edge-2026.09.08-card-v2`。
- 出口升级（同批）：所有出口（汇总群 `oc_523b…` 即尾盘掘金/擒龙内参目标群、`#liuzi`→`oc_606d…`、`#liwei`→`oc_903e…`、`#anqiang`→`oc_392b…`，以及微信中继 `#xiaolan`）改为发送**卡片 JSON 2.0**：`plain_text` div 首行为 `#tag`（保持汇总群入口按首行标签路由的契约），图片以 `img_key` 元素携带；含视频/文件的富文本保持 `post`，文件/媒体/云归档投递不变；源消息编辑通过卡片 `patch` 原位更新。`FEISHU_GROUP_RELAY_OUTBOUND_CARD=false` 回退到旧格式。汇总群入口 `extractMessagePayload` 新增 `interactive` 分支（共享 `card-content.mjs` 走读器），卡片内图片在消息资源接口不可用时回退到本 app 的 `images` 接口读取。
- 顺带修复：workbench 编辑同步把 `target_message_ids[0]`（`{targetChatId, messageId}` 对象）当作 message_id 传给 PUT，导致每次源消息编辑都报 `Invalid ids: [[object Object]]`（400）；撤回路径同样归一化。Feishu adapter 回归 **100/100**。
- 上线回执（a0e144b，`edge-2026.09.08-card-v2-outbound`）：GHCR 拉取再次在同一 blob 上超时，走本地 `docker save | ssh docker load` 后 apply，health `ok a0e144b`；顺手清掉 `ef8cb15` 旧镜像（edge 磁盘 12G 可用、内存可用 1.5G，adapter 常驻 58MB）。上线后日志暴露一个真实问题：源消息编辑时对**升级前**投递的文本/富文本气泡用卡片 `patch` → 飞书 400（`同步源消息编辑失败`×3）。修复：账本目标条目记录投递时的 `msgType`，编辑按目标原有形态重建载荷（无记录的旧条目走 text/post `update`，卡片条目走 `patch`）；回归 **104/104**，新增 Dockerfile 模块清单测试防再次漏 COPY。
- 第二个上线回执（d095dad → 再修）：剩下的一条 400 是 `#anqiang` 的专属出口群目标气泡已被撤回（`deleted:true`），编辑一个已撤回消息必然 400，而 `Promise.all` 让它拖垮了汇总群那条本已成功的同步，并让 workbench 兜底再 PUT 一次。改为逐目标 `allSettled`：任一目标成功即算已同步，失败目标单独记 warn 并带飞书错误码/msg（axios 只给 "status code 400"），全部失败才抛。回归 **105/105**。
- 部署链路提效：`deploy-feishu-relay-edge-release.sh` 保留 edge 拉取失败时的本地 `docker save | ssh | docker load` 自愈；`scripts/hotfix-feishu-relay-edge.sh` 改为“测试→版本化 adapter/前端/LarkAgentX bridge 源码覆盖层上传→远端 Node/Python/依赖清单校验→原子切换→`docker compose up --no-build --pull never`”，重复修 bug 不再构建或拉取 image。覆盖层保留最近 5 个版本，健康失败自动回滚；`package.json`、Node、supervisor venv 或基础镜像变化会拒绝快速路径并要求走对应的不可变运行时发布。正式 adapter release 会显式关闭容器覆盖层，避免旧源码遮蔽新镜像。

### 2026-09-05 当前复核

- 工程回归：挂载当前源码执行 quant-service **1,373/1,373** 项 Python discovery 通过；Feishu adapter **72/72** 通过；前端 `api:check`、`typecheck`、production build、OpenAPI contract 和 architecture check 均通过。复核时提交与 `origin/main` 同步（代码提交 `85f0f9e`，当前文档提交随后更新）。
- 运行态：quant-research、PostgreSQL、Feishu adapter、gateway 和 n8n 容器均健康；`/health` 返回 `ok`，build metadata 已为 `85f0f9e/local-20260905-r2`，异步池可用，5 个后台租约均在续租，存储约占总预算 46.3%、热库约占 51.0%。数据库迁移 head 为 `20260905_0093`。
- 当前研究门禁：最新交易日（2026-09-04）日线控制面仅 13/5,556 个点时全 A 标的，覆盖率 0.23%，状态为 `blocked`；涨跌停控制行仍为 0。`ths_industry` 点时成员当前无可用历史行，离线分钟 `source_available_at` 仍为 0，未来盘中 v2 快照尚未积累满 60 个交易日，因此 P2/P3 研究验证不能启动。
- WeChat→Feishu：adapter `/health` 虽为 `ok`，但 `/api/group-relay/status` 当前为 `disabled`；5 个 source 最近轮询均停在 2026-08-31，delivery outbox 有 256 条积压（2 failed、7 paused）。安强文字导入最近为 completed，其余媒体导入仍有 `upload_part`/远端 HTTP 500 失败；必须恢复 worker、清理积压并取得一条新的本地接收、远端归档、下游同步闭环回执，才能标记全链路完成。
- 性能余项：`/api/v1/data-readiness/replay` 已加入 8 秒数据库 statement timeout，当前线上在 12 秒内返回 HTTP 200 的明确 `blocked`，并带 `readiness_query_status=timeout`；这保证控制面不会无限占用连接，但完整覆盖统计仍需后续增量物化优化。执行计划显示其仍会扫描大体量 canonical bars/fundamentals。
- 结论：P0 数据语义、P1 运行工程和研究证据账本已基本完成；P2 历史数据地基、分钟价格路径回放、P3 样本外统计门禁、Prompt Lab 金标晋级和 RL/challenger 仍未完成。所有研究输出继续保持 `live_effect=none`。WeChat 监听脚本当前存在工作树未提交改动，未纳入本次量化链路验收。

- quant-service：历史 734 项回归记录对应 2026-08-23 快照；当前数量和本次复核结果见上方 2026-09-05 条目。除历史的 v2 规则输入、policy/risk gate 与作者时点回放边界外，新增覆盖观察池报价的直连/兜底边界、扫描总编排的闭市零外呼和确认投递、单事务信号证据顺序与生产事务边界、服务启动失败回滚及逐项关闭隔离、复盘/盘后同日回执 runner、日终 suppressed 摘要、一秒级 Super GET、收盘分钟画像、腾讯盘口及板块曲线 runtime 装配、扫描前的板块/秒级交叉确认、观察池/精确成员、个股 outbox pending/due 与轮动 suppression、THS 概念成员补全 flow/progress、日线同步与盘后核心 basket 输入、背景循环 runtime lease、涨停联动 research runtime/精确关系、熔断状态与生产日历 async 预读、THS/东财精确成员批次失败隔离、概念目录不可用时成员恢复 fail-closed、概念/涨停池精确代码 join，以及原生异步自动化回执、市场资金流、板块/概念/精确成员、涨停联动、Prompt Lab、已落库分析师市场复盘、有界市场评测、分钟时间轴、分析师研究状态、归档总览/游标、提供者目录/能力/健康、PIT 文本因子、路由 async 边界、真实 `upsert_bar` SQL 集成、单股窗口/claim 就绪度、决策卡、板块曲线/复盘/轮动/挖掘、分析师操作回放/outcome、技能/研究/归档证据读取，以及本地研究窗口/因子拒绝、快照控制面阻断、Tushare ledger 缓存/取消、研究维护、远端 claim 唯一成绩单来源、原生 async 同步健康、观察池维护、研究存储准入、生命周期 task、统一交易日日历状态、盘后形态样本仓储、分钟挖掘与盘后一键刷新装配边界，以及单股研究公共源探针的熔断先行和有界证据写入、显式核心池同日控制面同步。
- frontend：2026-08-22 已重新执行 `npm run api:check`、`vue-tsc --noEmit` 与 Vite production build，均通过；概念成员卡现在区分有效精确映射覆盖和同日同步回执，避免显示层掩盖证据差异。生成 API 类型仍与 OpenAPI 一致。构建仅保留 charts/element-plus 大于 500 kB 的优化警告，不影响功能或接口契约。
- 开盘预检：compose、数据库迁移（当前 head 为 `20260905_0092`）、必需后台租约、共享 provider pacing、30s/10s/1s/60s 节奏、飞书、当前发布版分析师文字同步以及可恢复备份均通过；预检是只读的，不请求市场 provider 或发送提醒。
- 最近提交：见当前仓库最新提交；本轮未改变策略阈值、live 权重或历史数据范围。所有新增拆分均保持既有 provider、数据库事务和告警边界。
- 策略实现提交均已推送到 `origin/main`；工作树中可能并行存在未纳入本轮策略提交的前端/飞书适配改动，提交时必须按文件路径精确暂存，避免将凭据或未验收改动混入。

## 2026-08-14 运行与前端收口记录

- 盘中实时监控已恢复为 quant-service 内置租约循环单点运行；旧 `quantIntradayAlerts123` n8n Cron 保持取消发布，避免与服务内扫描重复。该工作流保留带 `X-Quant-Write-Key` 的手动/故障恢复图。盘中扫描落纸面决策前已补齐 `symbol`、`observed_at` 契约；开盘预检通过，量化服务 322 项测试通过。
- n8n 本地默认使用内置 JavaScript runner（`N8N_RUNNERS_MODE` 可显式改回 `external`）；现有分析师同步图只有 HTTP 节点，Python runner 缺失警告不影响其节点。2026-08-22 的服务端 completed receipt 已提供当前发布工作流的正式运行证据；不会把单纯 HTTP 200、旧版或 CLI execution 行误记为完成。
- 分析师报告/消息工作流已经拆分、凭据域名和 JSON Body 已修复；`workflow_entity.versionId` 已与发布版本对齐。两条图均保留独立 Schedule Trigger，并新增不参与定时的 Manual Trigger，供运行中 n8n 的 UI 受控验收。服务健康只要求当前 `workflow_id` 的可审计 completed receipt；2026-08-22 两条流均已满足并返回 `verified_recent_execution`。n8n 历史 execution 表仍可能留有旧错误或 CLI 行，它们不会覆盖当前 receipt；零项消息轮询继续记录独立的 45 日 liveness receipt，而不伪造内容游标推进。
- 前端 `Unexpected token '<'` 已修复：adapter 补齐 `/api/research/remote-archive/messages`、`/api/research/analyst-skills`、`/api/research/analyst-research/status` 三个缺失代理，前端 JSON 解码器现在会检查非 JSON 响应并给出接口路径/状态提示，不再把 SPA HTML 当 JSON 解析。三个代理真实返回 `Content-Type: application/json`；`vue-tsc --noEmit` 与 Vite build 均通过。
- 盘后一键刷新修复验证：BaoStock 隔离同步此前因 `baostock_code` 关键字无法穿过公共源有界执行器而必然失败，现已在执行器内用 `partial` 安全转发关键字，并补充回归测试。2026-08-14 重试时 Super GET `daily_all` 成功写入 5,540 条日线，盘后策略同日完成（5,540 日线标的、5,521 个具备 15 日窗口，严格 30 日结构门槛下候选 0）；未拉取历史数据。
- 仍未完成且保持原边界：3–5 年扩展历史、分钟路径回放、60 日/200 signal episode 样本外验证、Prompt Lab champion/challenger 晋级、RL/contextual bandit、组合自动熔断和策略自动降级。一年日频 PIT 修复不构成分钟回放或阈值调参授权，也没有改变分析师 live 权重。

## 2026-08-16 收口记录

- 运行态复核：所有服务容器健康；实时服务在非连续竞价时正确 `standby`，并保留 36 只启用观察股的 30 秒/特别窗口 10 秒/盘口 3 秒/板块曲线 60 秒节奏。主 Tushare 明确标为无实时能力；Super SDK 与 Super GET 均按已验证协议分工，代理连接池和 Super GET 线程内 `requests.Session` 复用已启用。
- 存储治理复核（2026-08-16 历史快照）：量化 schema 13.39 GB，占当时 40 GiB 总研究预算 31.2%、28 GiB 热库预算 44.5%；当前热库上限已统一为 36 GiB，当前容量见 2026-09-05 运行态条目。7 天盘口与秒级交叉确认、60 天板块曲线/轮动、90 天分钟剖面及 60–120 天（默认 90）规则输入/观察池报价均有留存边界。未删除原始证据，也未启动历史回填。
- 订单簿数据只作为 `attribution_only`：`qi5`、窗口聚合 order-flow proxy、封单侵蚀等已入 SignalSpec 证据引用；不改变实时评分、阈值或提醒资格。
- 盘中规则输入回放已升级为 `intraday-rule-input-replay-v2`：新扫描冻结市场状态、Super/Tencent 交叉确认、纸面仓位与组合风险上下文，回放可调用同一纯函数 policy/risk gate；旧 v1 快照明确标记为 `core-only`，不会伪装成完整门禁回放。当前 2026-08-17 已存 3,996 条快照均来自 v2 部署前，故本次回放的 `policy_replayable_snapshots=0`；下一交易时段才会产生可验证的 v2 样本。
- 分析师链复核：报告与消息流各自拥有持久 cursor 与 45 天 liveness receipt；当前 active/published 版本一致，2026-08-22 的服务端文字-only completed receipt 已使 `/analyst-research/sync-health` 返回 `verified_recent_execution`。n8n 历史 execution 行仍可能保留旧错误，健康页刻意以当前 workflow ID 的可审计同步回执而非旧 CLI/执行行判定，不把二者混同。
- 上海日期回归：对晚 UTC 的分析师消息，结算入场必须发生在其对应的上海交易日之后；该语义已覆盖 local-only outcome recomputation，容量/readiness 投影与兼容实现，并有回归测试。

## 2026-08-16 P2 日频执行记录

- 先以真实 provider 结果修复 2025-06-13 至 2025-08-14 的 45 个开市日：45/45 完成、0 逻辑失败。`daily` 243,081 行、`adj_factor` 242,646 行、涨跌停 242,766 行；最小单日股票覆盖 5,384，复权平均覆盖 99.95%，涨跌停平均覆盖 100%。旧主源 DNS/TLS 失败没有被标成完成，City/Super SDK 的成功记录按日期写入并参与 canonical 控制回填。
- 随后对 2024-08-15 至 2025-08-14 完成 242/242 缺口审计，0 逻辑失败，并补齐旧批次少量缺失的日线控制截面。复权/涨跌停重联现按 `tushare_super_sdk → tushare_super_get → tushare_primary` 的已验证记录选择，且限制目标交易日，避免旧主源硬编码或年度检查扫描全库。
- 当前 `canonical_bars_daily` 覆盖 2023-08-15 至 2026-08-14、505 个全市场横截面日，已满足 1,090 个日历日跨度，但 P2 仍明确阻断：还需要 215 个完整日截面和带供应商 `source_available_at` 的离线分钟数据。2026-08-17 已按“盘中观察优先”暂停第三年日频任务；绝不把本地导入时间冒充分钟来源可用时钟，也不因此改变实时策略阈值。
- 日频回填遵守 40 GiB 总研究空间 / 36 GiB 热库硬上限。暂停时数据库约 23.6 GiB（热库约 65.5%）；80% 预警或 90% 暂停会优先保护实时服务和已有证据。

## 下一次恢复条件

第三年日频任务已暂停，并保留 `quant.fetch_runs` 的 `annual:*` 完成检查点；只能在用户明确恢复且不影响盘中观察时续跑。恢复后必须重查 720 日/1,090 日 P2 门禁和 80% 存储预警。仍需取得带来源可用时钟的分钟数据并通过 P2 数据就绪审计，才可能开启分钟回放或 P3 统计验证。任何未通过项继续保持 `research_only` / `descriptive_only`，不得写入 live 阈值或分析师权重。
