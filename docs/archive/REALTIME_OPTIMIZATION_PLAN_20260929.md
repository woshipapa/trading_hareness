# 实时数据与策略优化方案

日期：2026-09-29（Asia/Shanghai）
基线：`main@216e5c4`（本文所有代码位置均按此提交核验）
状态：设计与执行计划；`research_only`；不自动下单。本文件本身不改变任何阈值、晋级状态或
`live_effect`。

适用范围：远端 owner/peer 的实时数据面与策略扫描面（见
[`DEPLOYMENT_BOUNDARIES.md`](../DEPLOYMENT_BOUNDARIES.md)）。本地工作站只做分析、回放与开发验证。

相关文档：

- [`REALTIME_STRATEGY_STRENGTHENING_PLAN.md`](../REALTIME_STRATEGY_STRENGTHENING_PLAN.md)：状态机、因子分层、概率契约与晋级门槛（2026-08-16）。本文不重复其研究结论，只补充执行层问题与工程方案。
- [`OWNER_REALTIME_PROVIDER_PRIORITY.md`](../OWNER_REALTIME_PROVIDER_PRIORITY.md)：按能力选主源与"时间门禁先于优先级"规则。
- [`DATA_SOURCE_LAYER.md`](../DATA_SOURCE_LAYER.md)：`app/datasources` 能力目录。
- [`OPENING_REALTIME_RUNBOOK.md`](../OPENING_REALTIME_RUNBOOK.md)：开盘运行手册（部分内容已过期，见 D6）。

除特别说明，路径相对 `quant-service/`。

---

## 0. 摘要

1. **实时热路径的健康数据不可信。** 飞瑶全 A 快照和东财资金流的新鲜度被写死为 `fresh / 0 秒`。盘口与扫描失败一律记到 `tencent_free`，Longhu 盘中报价的健康从不写入。结果是熔断打不开、健康面板误报，`decision_eligible` 的判断也建立在错误的新鲜度上。
2. **`app/datasources` 的能力目录没有进入实时热路径。** 解析器在生产中只构造了一次，也没有接健康门禁。观察池报价、分钟、盘口、Tushare 仍是各自手写的降级链，目录里的优先级只起文档作用。
3. **三类写入会覆盖"当时可见"的内容**：市场事件、分钟线、原始观测。回放在 t 时刻可能看到 t 之后才出现的内容。
4. **策略生命周期是空转的。** 晋级登记表和 `strategy_trials` 没有写入方，校准状态永远不会变成 `validated`，所以盘中概率从不展示。健康度只看触发次数漂移，不看收益衰减。
5. **仓库没有任何前视扰动测试，也没有缺失 K 线测试。** 这是成本最低、收益最高的补强。
6. 外部参考 HKUDS/Vibe-Trading（commit `9a27a6e`）：借它的工程纪律部件，包括健康探测、前视检测、衰减状态机、证据门槛、运行卡、数字核对。不借它的加载器、回测引擎、统计验证模块和多 agent 辩论（理由见 §4）。

执行顺序：先做不改 schema 的低风险止血（批次 1），再做健康与限流（批次 2），然后是需要迁移的时点修正和生命周期（批次 3），最后是取数统一入口、规则重组和因子库（批次 4）。详见 §6。

---

## 1. 不变约束

以下约束对本方案所有工作流都成立。任一改动违反其中一条即视为不合格。

- 研究平台，不连接券商、不下单。飞书提醒是带来源和时间戳的研究线索。
- 远端 owner 是唯一的实时 writer；本地不启动 provider 轮询，也不创建实时租约。
- 数据缺失、陈旧、覆盖不足、样本不足时 fail closed，不编造价格、Top 列表或系数。
- `strategy_available_at` 是唯一的策略可用时钟；`stated_at` 只用于 `replay_only`。
- 08-16 计划中暂停的事项继续暂停，除非用户单独授权：
  - 历史大规模回填
  - 自动调参
  - 自动降级
  - 在线更新 champion

  因此本方案中的衰减与晋级只产出"建议/提案"，状态变更必须由人确认。
- 生产 schema 只走 Alembic。owner DDL 按 [`OWNER_DATABASE_STORAGE.md`](../OWNER_DATABASE_STORAGE.md) 与 [`RELEASE_SYNC_47.md`](../RELEASE_SYNC_47.md) 在 owner 执行；peer 不执行 owner DDL。
- 大模型不进入盘中决策链。

---

## 2. 现状：保持的部分

下列设计经核验是正确的，重构时必须保留其语义：

| 能力 | 位置 | 说明 |
|---|---|---|
| 时间门禁先于优先级 | `app/intraday_price_priority.py:11-80`、`app/intraday_quote_normalization.py:150-188` | 陈旧的主源不能覆盖新鲜的备用源；修复 Longhu 未补零的时钟 |
| 单写入租约 | `app/runtime_leases.py:31-43`、`app/runtime_tasks.py:203-286` | `INSERT … ON CONFLICT … WHERE expires_at<=now()`，每 lease/3 续约一次 |
| 冻结并哈希规则输入 | `app/intraday_rule_snapshot_repository.py`、`app/intraday_scan_signal_persistence.py:209-222` | 每个被扫描标的都写一行拒绝原因 |
| 告警 outbox | `app/intraday_alert_delivery_service.py:27-36`、`app/alert_transport.py:87-108` | 先写库并加租约，再发送；结果不明确时不继续尝试下一通道 |
| 统一 T+1 结算 | `app/t1_settlement.py:116-203` | 次日开盘入场，开盘涨停不可成交，离场遇跌停或停牌顺延 |
| 按日期区分涨跌幅 | `app/market_rules.py:52-69` | ST 按交易日选 5% 或 10%；优先使用 `stk_limit` 精确行 |
| 多重检验 | `app/strategy_validation.py:57-176`、`app/research_trials.py:30-93` | 带 embargo 的 walk-forward、PSR/DSR、族内 BH-FDR |
| 共享全 A 快照 | `app/intraday_cross_section.py:36-60`、`app/main.py:2091-2100` | 扫描路径 30 秒 TTL 的单飞缓存 |

---

## 3. 问题清单（已核验）

严重度：**高**＝影响熔断、决策资格或回放正确性；**中**＝影响可维护性或结论可信度；**低**＝卫生问题。

### 3.1 数据面

#### D1 能力目录未进入实时热路径（中）

- `CapabilityResolver` 在生产中只构造一次（`app/datasources/runtime.py:81`），而且没有传 `health_gate`。
- 策略数据需求门面 `StrategyDataPlan` / `resolve_strategy_data` 只有 `tests/test_strategy_data_context.py` 在调用。
- 实时路径各自手写降级链：
  - `app/intraday_watch_quote_capture.py`（Longhu → 腾讯 → 新浪 → 全 A 合并）
  - `app/intraday_minute_capture_actions.py`
  - `app/market_snapshot_actions.py:279-300`
  - `app/tushare_providers.py` 中的 `call_with_fallback`
- 影响：修改 `catalog.py` 的优先级不会改变实时行为；也没有统一的取数回执（`QualityReceipt` / provenance）。

#### D2 健康记录失真（高）

- 新鲜度写死：
  - 飞瑶全 A 快照总是返回 `"status": "fresh", "age_seconds": 0.0`（`app/fuyao_provider.py:175-176`），虽然响应里其实带有 `upstream_timestamp_ms`。
  - 东财观察池资金流同样写死（`app/intraday_watch_quote_capture.py:246-250`）。
- 失败归因到错误的源：
  - 盘口失败一律记为 `tencent_free/order_book_quote`（`app/intraday_order_book_service.py:121-123`），所以 Longhu 盘口熔断在这个循环里永远不会打开。
  - 混合批次的成功只记一个源（同文件 `:116`）。
  - 扫描级 provider 失败也一律记为 `tencent_free/realtime_quote`（`app/intraday_scan_repository.py:222-228`）。
- 缺失写入：
  - 健康面板读取 `longhuvip/stock_quote`（`app/realtime_provider_health.py:12`），但盘中没有任何路径写它。`longhuvip` 的健康行只由盘后同步写入（`app/longhu_market_repository.py:203`）。
  - Level-1 全 A 循环（`app/level1_snapshot_runtime.py`）和 `market_event_capture` 不写健康。
- 同一端点两套能力名：飞瑶全 A 在扫描中记为 `realtime_quote`（`app/intraday_scan_preparation.py:83`），在快照动作中记为 `a_share_prices_snapshot`（`app/market_snapshot_actions.py:151`）。熔断只检查后者（`:287-290`）。
- 说明：`provider_api_capabilities.availability='verified'` 的含义是"这条物理路由曾经验证过"，后续失败不会把它降级（`app/provider_health.py:47-76`）。这是有意设计，但也意味着它不能当作实时健康使用，实时健康必须另有来源。

#### D3 只有 Tushare 限流；全 A 快照重复拉取（中）

- 进程内滚动窗口加跨进程数据库时间槽，只覆盖 Tushare（`app/tushare_providers.py:243-320`、`app/provider_rate_limits.py:14-42`）。
- 飞瑶全 A（2×5000 行）除扫描共享缓存外，还被以下位置各自直接调用：
  - `app/main.py:3974`：竞价 universe
  - `:4094`：情绪
  - `:4203`：Level-1，每 60 秒
  - `:4275`：小杰涨停池
  - `:4805`

#### D4 Longhu 实时请求与 AKShare 共用执行器（中）

- Longhu 观察池报价和盘口经 `run_akshare_blocking` 运行（`app/main.py:3051`、`:3095`）。
- 它们与 AKShare 等公共源共用 `public_source` 有界池（`app/runtime_executors.py:135-158`，默认队列 16）。
- 影响：慢速的 AKShare 或盘后任务可能挤占盘中 Longhu 的执行槽位。

#### D5 时点写入覆盖（高）

| 表 | 位置 | 行为 | 回放影响 |
|---|---|---|---|
| `market_events` | `app/public_market_repository.py:224-228` | 冲突时保留最早的 `available_at`，但覆盖 `title/body/url/content_sha256` | t 时刻会看到"早时间戳配晚内容" |
| `intraday_minute_sessions` | `app/intraday_minute_capture_actions.py:153-160` | 冲突时覆盖 OHLCV 和 `available_at` | 形成中的分钟 K 无法还原到"当时所见" |
| `raw_market_observations` | `app/public_market_repository.py:95` | 相同 payload 冲突时把 `available_at` 改为更晚的时间 | 保守但丢失首次可见时间 |

#### D6 声明与实现漂移（中）

- 扫描节奏有三种不同说法：
  - 代码：早盘 09:15–11:30 每 5 秒（`app/intraday_schedule.py:45-62`，2026-09-22 决定）。
  - 任务注册表：仍写 `"30s during session"`（`app/platform/runtime_task_registry.py:27`）。
  - 运行手册：仍写 10 秒/30 秒、观察池上限 40 只；而当前上限是 100（见 `OWNER_REALTIME_PROVIDER_PRIORITY.md`）。
- 声明了但不存在的表：`intraday_fast_quotes` 和 `intraday_order_book_observations` 出现在以下位置，但没有任何迁移创建它们：
  - `app/datasources/catalog.py:243`
  - `app/platform/data_product_registry.py:92-93`
  - `app/platform/runtime_task_registry.py:32,40`

  实际写入的是 `intraday_quote_observations`（例如 `app/intraday_order_book_service.py:109`）。
- 找不到写入方的绑定：`catalog.py:261` 把 `tencent_free → bars.daily_adjusted → research_adjusted_bars_daily` 标为 `LIVE_VERIFIED`，`app/decision_research_repository.py:143` 也在读这张表，但仓库里没有任何写入它的代码。

#### D7 交易时段常量分散（中）

仓库中有 27 个模块直接写入时段边界字面量（`time(9,15)`、`time(11,30)`、`time(13,0)`、`time(15,0)` 等），例如 `intraday_schedule.py`、`intraday_clock.py`、`realtime_provider_health.py`、`market_event_runtime.py`、`auction_pulse.py`、`datasources/collectors/intraday.py`。时段规则一旦调整，很难保证全部同步。

#### D8 租约无 fencing token（低）

写入不携带 `holder_id`。失去租约后，已进入线程池的写入仍可能完成（`app/runtime_tasks.py:258-265`）。目前依赖数据库幂等键兜底。

#### D9 前复权口径（低，需核查 owner）

外部实测显示，腾讯 `qfqday`、东财 `fqt=1`、akshare `stock_zh_a_hist(adjust="qfq")` 的前复权是**加法**：分红被当作常数减去，而不是按比例调整。因此用旧价格计算的收益率有偏。

我们的现状：

- 腾讯前复权数据已被挡在标准日线之外（`app/daily_bar_repository.py:163-166`）。
- akshare 使用 `adjust=""`（`app/akshare_provider.py:209,218`）。
- 唯一的风险点是 D6 中 `research_adjusted_bars_daily` 的来源不明。

### 3.2 策略面

#### S1 晋级链路空转（高）

- `quant.strategy_promotion_registry` 只被读取（`app/strategy_promotion.py:25`），应用代码中没有写入方。
- `quant.strategy_trials` 同样只被读取（`app/paper_read_model.py:41`、`app/async_paper_read_repository.py:76`）。
- `intraday_decision_context.py:116` 要求 `calibration_status == "validated"` 才展示概率，但没有任何代码设置该值，所以概率永远不展示。

#### S2 没有收益衰减监控（高）

`app/strategy_health_read_model.py:73-78` 的漂移只比较"近 7 天与前 7 天的触发次数比"（>3 或 <0.33 即告警），外加全局 30 分钟命中率。没有按策略族的滚动净收益、命中率或数据覆盖率跟踪。

#### S3 试验记账覆盖窄（中）

`record_family` 只有两处调用：

- `app/factor_sql_lab.py:565`
- `app/research_trial_repository.py:129`，只覆盖 `candidate_ledger` 和 `xiaojie_leader_flow`

真正发飞书的观察池确认规则和老师 playbook 没有试验记账。参数扫描（`strategy_parameter_sweep.py`）和择时挑战者（`strategy_timing_challengers.py`）也不计入族内试验数。

#### S4 标签不可兑现（中）

- 概率画像使用 30 分钟毛收益（`intraday_decision_context.py:190-198`，`horizon_key='30m'`、`raw_return`）。在 T+1 下，买入当天无法兑现这笔收益。
- `LabelSpec.cost_bps=18.0`（`app/strategy_contracts.py:99`）已声明，但任何地方都没有使用。

#### S5 首个命中即停的规则链（中）

`app/intraday_signal_rules.py` 有 12 个 `and not signals` 分支，规则顺序决定哪个 setup 触发。被遮蔽的规则不留记录。新增生成器需要修改 `app/main.py:3312-3324` 的装配。

#### S6 注册表语义不足（中）

以下三个策略都登记为 `shadow / none`，但会发送飞书提醒：

- `intraday_watchlist_confirmation`（`strategy_registry.py:29-32`）
- `xiaojie_leader_flow`（`:91-95`）
- `teacher_review_playbooks`（`:108-112`）

注册表没有字段区分"会发提醒的研究策略"和"静默影子策略"。另外，`multi_factor_rank_v1`、`board_flow_drill → paper`、`dragon_leader_research` 都没有注册。

#### S7 变更评估只看符号（低）

`app/strategy_change_log.py:143-156` 只比较前后均值差的正负，不做显著性检验，也没有 `inconclusive` 这一档。

#### S8 成本偏乐观（低）

研究用的往返成本不含佣金最低 5 元（`app/ashare_reality.py:136-150`，已在文档中注明），滑点固定 5 bp。没有按"想下单却成交不了"的原因做汇总。

#### S9 没有前视或缺失 K 线测试（高）

`quant-service/tests/` 共 331 个文件，没有任何基于"扰动未来输入"或"删除一根 K 线"的因果性测试。

#### S10 因子库小（低）

可按 SQL 评估的因子只有 7 个（`app/factor_sql_lab.py:22-30`）。IC 只在请求时计算，没有定时的滚动 IC 和存活分类。

---

## 4. 外部参考取舍：HKUDS/Vibe-Trading（commit `9a27a6e`）

VT 是"大模型写策略代码 + 多市场历史回测"的研究工作台，A 股实时部分基本空白（无 tick、无盘口、无轮询）。它的 A 股回测规则也比我们粗：

- 无 ST 5% 涨跌幅；
- `301xxx` 被当作 10%；
- 加仓后沿用原来的 `entry_time`，导致当天买入的份额当天可卖。

下表路径相对 VT 仓库的 `agent/`。

| 借鉴项 | VT 位置 | 用在本方案 | 注意 |
|---|---|---|---|
| 数据源金丝雀探测 | `backtest/loader_health.py` | WS-A4 | 实时场景需要改成按交易时段探测 |
| 区分"拒绝"与"空" | `backtest/loaders/eastmoney_client.py:76-104` | WS-A4 | — |
| 按主机预留时间槽限流 | `backtest/loaders/_http.py:46-106` | WS-B2 | — |
| 前视扰动测试 | `tests/factors/test_lookahead.py` | WS-F1 | — |
| 缺失 K 线测试 | `tests/factors/test_missing_bar_fabrication.py` | WS-F2 | — |
| 复权口径标记 | `backtest/loaders/registry.py:260-434` | WS-E4 | — |
| 衰减状态机（迟滞） | `src/strategy_store/decay.py:161-239` | WS-G2 | VT 用"前 5 次 vs 后 5 次运行"比较，窗口会重叠，需改为按交易日滚动 |
| 证据硬门槛与机器可读警告 | `src/strategy_discovery/evidence_harness.py:91-193`、`models.py:75-79` | WS-G4 | — |
| 未成交计划按原因计数 | `backtest/engines/base.py:755-830` | WS-H4 | — |
| 运行卡：产物哈希，指标精确引用 | `backtest/run_card.py:47-130, 333-382` | WS-G3 | 需补 git SHA 与数据快照 ID |
| 算子库与 alpha 定义（MIT） | `src/factors/base.py`、`src/factors/zoo/` | WS-J | 它的 bench 用收盘到次日收盘的收益，不过滤涨停和停牌，不可照搬 |
| 数字核对闸门 | `src/agent/grounding/` | WS-K1 | 只移植窄版 |
| 工具循环停滞保护 | `src/agent/tool_progress.py` | WS-K2 | — |
| 简报结论尾部与"数据缺口"小节 | `src/scheduled_research/verdict.py` | WS-K3 | — |

明确不借：

- **`backtest/validation.py`**：
  - 其蒙特卡洛只是打乱交易顺序，而 Sharpe 对顺序几乎不敏感；
  - Bootstrap 是独立同分布重采样；
  - "walk-forward" 只是切段看一致性，不重新拟合。
- **数据加载器与 `china_a` 引擎**：我们的实现更完整，规则更准确。
- **多 agent 辩论 preset**：worker 不经过数字核对；基金经理角色直接给出交易决定，与 `live_effect=none` 冲突。
- **影子账户**：只从盈利交易中学规则，结论带选择偏差。
- **技能库、agent 自写技能、跨会话记忆**：质量不齐；中文检索按单字拆分。

---

## 5. 工作流设计

每个工作流都给出：目标、设计、改动位置、验收标准。编号与 §6 的排期对应。

### WS-A 健康数据真实化（解决 D2）

**A1 新鲜度按上游时间计算**

- 飞瑶：`age = now - upstream_timestamp_ms`。时间戳缺失、晚于当前时间或跨交易日时，状态为 `unknown` 或 `invalid`，不再写 `fresh`。
- 东财资金流：使用行内的时间字段；没有时间字段时标记为 `unknown` 且 `research_confirmation_only`。
- `unknown` 不是 `fresh`。凡是依赖新鲜度的 `decision_eligible` 判断，一律按不合格处理。
- 改动位置：`app/fuyao_provider.py`、`app/intraday_watch_quote_capture.py`、`app/platform/evidence_contracts.py`。

**A2 按实际源归因**

- 盘口：按行的 `source` 分组，分别记录成功和失败；整批失败时，按本次尝试过的源分别记失败。
- 扫描：`intraday_scan_repository` 的 `provider_failure` 改为结构化的 `{provider, capability, error}` 列表，不再固定为 `tencent_free`。
- 盘中写入 `longhuvip/stock_quote` 的健康记录。
- Level-1 循环和 `market_event_capture` 写入健康记录。

**A3 能力名统一**

- 在 `app/datasources/catalog.py` 增加一个映射：目录能力键 ↔ 旧健康能力名。
- `realtime_provider_health`、熔断检查和解析器门禁都通过这个映射查询。
- 飞瑶全 A 只保留一个能力键；旧键在一个发布周期内双写，之后停写。

**A4 数据源金丝雀探测**（借鉴 VT）

- 由远端 `research` profile 的租约任务执行：09:05 一次，交易时段内每 30 分钟一次。每个源一个固定标的；不走实时租约，也不占实时执行器。
- 检查项：
  - 字段结构；
  - 价格关系（high ≥ max(open, close)，low ≤ min(open, close)，价格为正）；
  - 时间单调且无重复；
  - 没有未来时间戳；
  - 交易所时间戳新鲜度。
- 状态：`healthy / stale / invalid / rejected / empty / unreachable / timeout`。其中 `rejected`（上游业务拒绝）必须与 `empty`（确实没有数据）区分。
- 结果写入 `provider_health`，采用同一套能力键。它与 `provider_api_capabilities` 分开，后者仍表示"曾经验证过"。

**验收**

- 单元测试：
  - 注入陈旧或缺失的上游时间戳，确认状态为 `unknown`，且 `decision_eligible=false`；
  - 注入 Longhu 盘口连续失败 3 次，确认 `longhuvip/order_book_quote` 熔断打开，而 `tencent_free` 不受影响。
- 一个交易日的 owner 证据：`/api/v1/intraday/services/status` 与 provider 状态中，每个源的健康都有独立更新时间。
- 证据记录按 AGENTS.md 的 owner 字段要求填写。

### WS-B 请求合并与限流（解决 D3、D4）

**B1 全 A 快照单飞**

- 把 `_intraday_all_a_snapshots` 从 `main.py` 抽到独立模块。所有全 A 消费者都经过它：竞价 universe、情绪、Level-1、小杰涨停池，以及 `:4805` 的调用。
- 每个消费者记录自己拿到的数据年龄，而不是缓存的年龄。
- 对 TTL 有更严格要求的消费者，显式传入 `max_age_seconds`；超过时单飞刷新。

**B2 按主机限流**

- 为飞瑶、东财、新浪、腾讯增加进程内令牌桶，采用"预留抖动时间槽"的写法：并发突发会被均匀错开。owner 是单 writer，所以进程内限流即可。
- 限额读取已有的 `rate_limit_per_minute` 配置（目前只用于展示），而不是另建一张表。
- 超限时拒绝本次请求并记为 `rate_limited`，不排队。

**B3 执行器隔离**

新增 `realtime_vendor` 有界执行器，供 Longhu 实时报价、盘口和分钟使用。它与 `public_source`（AKShare/盘后任务）分离，并暴露独立的占用指标。

**验收**

- 飞瑶每分钟调用次数指标下降（上线前后各记录一个交易日）。
- 盘后任务运行期间，Longhu 观察池报价的 p95 延迟不上升。
- `runtime_executor_status` 显示两个执行器各自的占用。

### WS-C 取数统一入口（解决 D1）

**C1 类型化的实时源协议**

- 参照 `app/longhu_vendor_source.py:468-485` 的 `LonghuIntradaySource` Protocol，定义实时源协议：`capability`、`fetch(symbols)`、`freshness(row)`。
- 适配器只负责传输和归一化，不负责选源。

**C2 解析器接入健康门禁**

- 把 WS-A3 的映射作为 `HealthGate` 注入 `CapabilityResolver`。
- 保留"时间门禁先于优先级"：解析器按目录优先级取数，合并时仍由 `intraday_price_priority` 按新鲜度逐股裁决。

**C3 逐条路径迁移并影子比对**

- 顺序：观察池报价 → 分钟 → 盘口 → 全 A。
- 每条路径先以影子模式并行运行 N≥3 个交易日，记录新旧选源的差异，确认无差异后再切换。

**验收**

- 修改 `catalog.py` 中某个绑定的优先级后，对应路径的选源随之改变（测试覆盖）。
- 扫描行的 `source_status` 带有解析器回执：尝试序列、`is_fallback`、质量状态。

### WS-D 时点写入修正（解决 D5）

**D-1 市场事件版本化**

- 内容变化时写新版本行（主键 `event_identity_key + content_sha256`），每个版本保留自己的首次 `available_at`。
- 读取时按"`available_at ≤ t` 的最新版本"取值。

**D-2 分钟线修订历史**

- 主表继续保存最新值，供盘中使用。
- 新增 `intraday_minute_revisions`，只追加 `(symbol, trading_date, minute_bucket, source_name, content_sha256, first_seen_at)`，回放按 `first_seen_at ≤ t` 还原。

**D-3 原始观测保留首次可见时间**

- 冲突时 `available_at = LEAST(...)`。
- 新增 `last_seen_at` 列记录最后一次见到的时间。

**约束与验收**

- 迁移通过 Alembic 在 owner 迁移线上执行；热表和冷表孪生结构需同步，见 `docs/OWNER_DATABASE_STORAGE.md`。
- 回放测试：同一事件在 t1 和 t2 两次写入不同内容时，按 t1 回放只能看到第一版内容。

### WS-E 声明与实现对齐（解决 D6、D7、D9）

**E1 统一交易时段来源**

- 以 `app/intraday_clock.py` 作为唯一的时段定义，导出竞价、连续竞价、午休、收盘和各重点窗口。
- 27 个模块逐步迁移到这里。
- 在 `tests/test_platform_boundaries.py` 中加入检查：禁止在 `intraday_clock.py` 和 `market_rules.py` 之外新增时段字面量。存量文件用白名单，迁移一个移除一个。

**E2 修正过期声明**

- `runtime_task_registry.py:27` 的节奏描述。
- `OPENING_REALTIME_RUNBOOK.md` 的节奏和上限。
- 把 `intraday_fast_quotes` 和 `intraday_order_book_observations` 改为实际表 `intraday_quote_observations` 加 `source_name` 过滤，或者建只读视图。要注意 `data_product_registry` 与存储分层策略之间的一致性校验。

**E3 核查 `research_adjusted_bars_daily`**

- 在 owner 上只读检查行数、`provider` 和最新日期。
- 如果为空：把绑定降级为 `DORMANT`，并让 `decision_research_repository` 在缺数时显式 fail closed。
- 如果来自腾讯前复权：标注口径为 `additive_qfq`，禁止用它计算收益率。

**E4 复权口径字段**

- 为调整后的 K 线产品增加 `price_caliber`：`raw / multiplicative_qfq / additive_qfq / unknown`。
- 计算收益率和涨跌幅的研究代码只接受 `raw` 或 `multiplicative_qfq`。

### WS-F 因果性测试（解决 S9，优先级最高）

**F1 前视扰动测试**

- 新增 `tests/test_causality_oracles.py`，提供一个通用断言：
  1. 构造一段确定性的合成序列；
  2. 计算第 t 行的输出；
  3. 把第 t+k 行之后的所有输入改为 NaN 或极端值，重新计算；
  4. 断言第 t 行输出不变（容差 1e-9，NaN 视为相等）。
- 覆盖接收时间序列的纯函数模块：
  - `intraday_technical_indicators`
  - `watchlist_daily_factors`
  - `xiaojie_indicators`
  - `intraday_volume_profiles`
  - `intraday_derived_flow_metrics`
  - `post_close_structures`
  - `market_regimes`
  - `sentiment_cycle`
  - `technical_analysis`
- 单次快照型的规则函数（`intraday_signal_rules`、`intraday_breakout`、`teacher_review_rules`）不适用序列扰动。它们的因果性由"冻结输入 → 确定输出"保证：从已冻结的规则输入中抽取样例作为黄金用例，断言重算哈希一致（复用 `intraday_rule_input_replay_runner` 的逻辑）。

**F2 缺失 K 线测试**

删除或扰动一根 K 线后，依赖它的输出必须是 `None` 或 NaN，或者带缺失标记，不能被常数或前值悄悄填补。

**F3 SQL 因子**

- `factor_sql_lab` 的 SQL 因子用 PostgreSQL 测试夹具执行同样的扰动断言。
- 如果当前容器测试不便建库，先对其 Python 参考实现做 F1，SQL 版本放到批次 3。

**验收**

- 新测试随 `docker compose exec -T quant-research python -m unittest discover -s tests -q` 运行，并在文档中列出已覆盖模块。
- 发现的真实泄露单独修复并补回归测试。

### WS-G 策略生命周期（解决 S1、S2、S3、S6）

**G1 注册表语义**

- 在 `StrategyContract` 增加 `alert_effect` 字段（`none | research_alert`），与 `live_effect` 分开。
- 启动时检查：所有注入到扫描链的生成器都必须已注册，且会发提醒的生成器必须标为 `research_alert`。
- 为 `multi_factor_rank_v1`、`board_flow_drill`（纸面）、`dragon_leader_research` 补注册。

**G2 衰减评估**（借鉴 VT 的状态机，改用我们的数据）

- 频率：每个交易日收盘、T+1 结算成熟之后，由 `research` 调度执行一次。
- 指标（按策略族）：
  - 滚动 20 个交易日的 T+1 结算净均值；
  - 命中率；
  - 独立交易日数；
  - 触发次数比；
  - 行情陈旧拒绝率。
- 基线：前 60 个交易日，与评估窗口不重叠。
- 等级：`healthy / warning / decayed / critical`，取各指标中最差的一个。窗口内独立交易日少于 20 时记为 `insufficient`，这一天不参与状态转移。
- 状态转移（初始参数，校准后可调）：
  - `active → monitoring`：连续 3 天不健康；
  - `monitoring → active`：1 天健康；
  - `monitoring → decayed`：连续 2 天 `decayed` 或 `critical`；
  - `decayed → disable_proposed`：连续 3 天 `critical`；
  - `decayed` 不会自动恢复，需要人工重新批准。
- 输出：追加写入 `strategy_lifecycle_assessments`（只追加）。状态变化生成提案，由人确认后才写入晋级登记表。`live_effect` 始终为 `none`。

**G3 晋级登记表的写入口与运行卡**

- 新增受写边界保护的 API（`app/security.py`），用于 approve / revoke / disable。每次写入附带：
  - 审批人；
  - 证据引用：运行卡哈希，包含配置、代码、输入数据快照、产物的 sha256，以及 git SHA。
- 登记表的历史只追加，不覆盖。
- 指标引用只接受与运行卡中已哈希结果文件完全一致的值（借鉴 VT 的 run card 规则）。
- 上线前需运行 `node scripts/verify-api-contract.mjs` 和 `npm run api:generate`。

**G4 证据硬门槛**

- 一次运行要算作证据，必须满足：运行成功、交易数大于 0、净值为有限值、覆盖率达到门槛。
- 数量门槛沿用 08-16 计划：每个动作/期限至少 60 个独立交易日、200 个独立 episode、每个 cohort 至少 30 条。
- 按市场状态分层统计，复用 `market_regimes.py` 或 `market_regime_daily.py`，每层单独判定 `sufficient / marginal / insufficient`。
- 计算盈亏平衡费率（bp），用于标记成本敏感的策略。
- 警告使用稳定的机器可读标记，例如 `insufficient-trades:`、`short-coverage:`、`cost-sensitive:`、`regime-thin:<regime>`。

**G5 扩大试验记账**

- 为观察池确认规则（按 setup）和老师 playbook（按 playbook × 版本）建立 `research_trials` 族。
- 参数扫描和择时挑战者的每个变体都计入对应族的试验数，确保 DSR 和 BH-FDR 的零假设包含全部尝试。

**G6 变更评估加显著性**

- 按交易日做块 bootstrap，或用 Welch t 检验。
- 增加 `inconclusive` 档；样本未达复核窗口时仍记为 `too_early`。

### WS-H 标签与成本（解决 S4、S8）

**H1 以可兑现标签为主**

- 入场类信号的概率画像、健康度和衰减评估，都改用 `t1_settlement` 的净收益作为主标签。
- 5/15/30 分钟收益保留为"信号时效"描述指标，并在界面上标注"不可兑现"。

**H2 启用或删除 `LabelSpec.cost_bps`**

三重屏障标签要么减去成本，要么删除该字段，避免声明了却不生效。

**H3 成本敏感性**

- 研究报告增加按名义金额（1 万 / 5 万 / 10 万）的往返成本和盈亏平衡费率，体现佣金最低 5 元的影响。
- 纸面交易继续使用按仓位计算的 `estimate_trade_cost`。

**H4 未成交原因汇总**

在 T+1 结算和纸面交易的输出中，按原因统计"想入场却无法成交"的次数：

- 开盘涨停封死；
- 停牌；
- 整手取整后为 0；
- 资金不足；
- 行情陈旧被拒。

这些计数进入 G4 的证据门槛。

### WS-I 规则组织（解决 S5）

**I1 评估全部规则，再裁决**

- `intraday_signal_rules` 改为先评估所有规则，收集全部命中的 setup 及原因码，再按确定的优先级选出主信号。
- 被遮蔽的命中也写入证据。
- 迁移期以影子模式运行至少 5 个交易日，确认主信号与旧实现逐条一致。

**I2 生成器插件化**

生成器在 `platform/strategy_registry.py` 中注册并声明依赖，`main.py` 只负责按注册表装配，符合 ARCHITECTURE.md 中组合根的约束。

### WS-J 因子库扩展（解决 S10）

**J1 移植算子与 alpha 定义**

- 移植 VT 的算子约束：差分必须至少滞后一期；除零返回 NaN；窗口内有缺失输入则输出为缺失。
- 移植部分 Alpha101 和国泰君安 191 公式（MIT 许可，在 NOTICE 中保留来源）。
- 评估一律使用 `factor_sql_lab` 的管线：次日开盘入场、T+1、过滤涨停和停牌、行业与市值中性化、BH-FDR、DSR。
- 每个 alpha 都记为 `research_trials` 中的一次试验。

**J2 定时滚动 IC**

- 每个交易日盘后计算滚动 IC，分类为 `alive / reversed / dead`；样本外符号翻转判为 `reversed`。
- 结果作为 G2 的输入之一。
- 在正式的 3 年时点门槛（`factor_sql_lab.py:43-55`，行业历史从 2026-08 才开始积累）满足之前，所有结果只作描述。

### WS-K 大模型产出核对（外部 harness，跨仓库）

作用对象：`video_understanding_harness` 的老师参数包，以及 `scripts/teacher_cycle.py` 流程中的 check 步骤。

**K1 数字核对**

- 参数包中的每个数值必须声明来源：
  - T＝老师所述，视为转述；
  - D＝从行情数据核实，视为观测；
  - I＝默认值。
- D 类数值在 check 步骤中与 owner API 的证据比对，偏差大于 0.5% 时拒绝该字段。
- T 类数值（例如老师给的目标价）永远不能当作观测到的行情使用。

**K2 停滞保护**

harness 中的 agent 循环连续 8 轮没有新的观测结果时停止；同一调用、相同参数失败 2 次后不再重试。

**K3 简报格式**

- 日报末尾使用 `- 代码: 状态 - 理由` 格式的结论行，格式不符即判为契约违规，并与上一日结论做差异对比。
- 强制包含"数据缺口"小节。

---

## 6. 排期

| 批次 | 内容 | Schema | 风险 | 预计 |
|---|---|---|---|---|
| 1 止血 | F1、F2；A1、A2；E2；E3（只读核查）；B3 | 否 | 低 | 2–3 天 |
| 2 健康与限流 | A3、A4；B1、B2；G1；H1、H2、H4；G6 | 否（A4 可复用 `provider_health`） | 中 | 1 周 |
| 3 时点与生命周期 | D-1、D-2、D-3；G2、G3、G4、G5；F3；E4 | 是（Alembic，owner 迁移线） | 中 | 1–2 周 |
| 4 结构性改造 | C1、C2、C3；E1；I1、I2；J1、J2；K1–K3 | 视情况 | 中高 | 分阶段 |

依赖关系：

- A3 先于 C2；
- H1 先于 G2；
- G1 先于 G5；
- D 系列先于"历史分钟回放"恢复（后者仍需单独授权）。

---

## 7. 验收与发布

每个批次都遵循 `AGENTS.md` 的流程：

1. 先写纯函数测试，路由或持久化变更再补仓储/HTTP 测试。
2. 依次运行：
   - `docker compose exec -T quant-research python -m unittest discover -s tests -q`
   - `cd frontend && npm run typecheck && npm run build`
   - `git diff --check`
3. 涉及路由时运行 `node scripts/verify-api-contract.mjs`；接口有意变更时运行 `npm run api:generate`。
4. 调度或采集变更必须同时核验三项：数据库行、最新状态端点、一次真实的适配器请求。单元测试通过不等于发布路由可用。
5. 发布到 owner 严格按 [`RELEASE_SYNC_47.md`](../RELEASE_SYNC_47.md) 执行，以只读的 `scripts/release-sync-status.sh` 开始并结束。

**基线与对比指标**

每个批次上线前后各采集一个交易日。数据来自 owner（15682），并按 AGENTS.md 记录目标路径、隧道状态、交易日、观测时间、来源、行数、新鲜度和 `decision_eligible`。

| 指标 | 来源 | 期望 |
|---|---|---|
| 各源健康行的更新时间与熔断状态 | provider 状态端点 | 每个源独立更新，注入故障只影响对应源 |
| 飞瑶全 A 调用次数/分钟 | 请求计数指标 | B1 之后下降 |
| 扫描耗时 p50/p95 | `intraday_scan_runs` | 不上升 |
| `public_source` 和 `realtime_vendor` 执行器占用 | `runtime_executor_status` | 盘后任务不挤占实时池 |
| 每日提醒数与主信号 | `intraday_signal_events` | 重构类改动（C、I）前后逐条一致 |
| `unknown` 新鲜度占比 | 扫描 `source_status` | 可见、可解释，不再被 `fresh` 掩盖 |

---

## 8. 明确不做

- 不下单，不接券商，不做自动晋级、自动降级或自动调参。
- 不把大模型或多 agent 讨论放进盘中决策链。
- 不引入 VT 的加载器、回测引擎、`validation.py` 或影子账户。
- 不以本方案为由启动历史大规模回填或历史分钟回放；两者仍需单独授权。
- 不把五档快照称为真 OFI，也不把 VPIN 当作入场因子（沿用 08-16 计划 §5）。

---

## 附录 A：问题与工作流对照

| 问题 | 工作流 |
|---|---|
| D1 目录未进入热路径 | C1–C3 |
| D2 健康失真 | A1–A4 |
| D3 限流与重复拉取 | B1、B2 |
| D4 执行器共用 | B3 |
| D5 时点写入覆盖 | D-1 至 D-3 |
| D6 声明漂移 | E2、E3 |
| D7 时段常量分散 | E1 |
| D8 租约无 fencing | 暂不处理（依赖幂等键）；如出现重复写入再评估 |
| D9 复权口径 | E3、E4 |
| S1 晋级空转 | G3 |
| S2 无衰减监控 | G2（依赖 H1） |
| S3 试验记账窄 | G5 |
| S4 标签不可兑现 | H1、H2 |
| S5 首个命中即停的规则链 | I1、I2 |
| S6 注册表语义 | G1 |
| S7 变更评估 | G6 |
| S8 成本 | H3、H4 |
| S9 因果性测试 | F1–F3 |
| S10 因子库 | J1、J2 |
