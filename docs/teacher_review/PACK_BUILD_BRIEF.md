# 策略包生成细则（teacher-review-pack/v1）

任何智能体（Claude、Codex、harness 研究智能体）按这份细则，把一天的老师复盘做成可导入的策略包。流程位置见 [../TEACHER_REVIEW_DAILY.md](../TEACHER_REVIEW_DAILY.md) 第 2 步。

以下内容是唯一准绳：
- `quant-service/app/teacher_review_playbooks.py`：`CATALOG` 必填参数、`STANCES`、`FORECAST_KINDS`、`validate_pack`；
- `teacher_review_rules.evaluate`：盘中怎么用参数；
- `teacher_review_plan.plan_stock`：冻结哪些价位；
- `teacher_review_service.check_forecast`：预判怎么结算。

**只写一个 JSON 文件，不导入、不调写接口、不改数据库。**

## 输入（harness 任务目录）

- `teacher_strategy_<T+1>.md`：量化整理稿，其中的价位已用 owner 数据核对，可以作为 D 类来源；
- `transcript_large.srt` 为主，`transcript_small.srt` 交叉核对。ASR 经常听错股票名，要结合上下文和整理稿判断；
- `ocr_evidence.json`：画面上的数字、股票名、执业证号水印；
- `provenance.json`、`state.json`：来源哈希、时长、标题、创建时间；
- `owner_market_evidence_<T>.json`：owner 行情证据；
- `daily_context_<T>.md`：当天结算、延续结果、次日观察池，以及**昨日复盘里我们自己条件的表现**。
  **必读**，既用来和老师的新观点对账，也用来修正参数：
  - 「没触发却大涨」列出的剧本，同类票这次要重新考虑参数松紧，并在 `*_src` 里写清为什么改；
  - 「封板价才触发」说明该剧本的买点设计本身太晚，不是阈值问题，别靠放宽阈值补；
  - 「重放满足条件却没推送」是系统缺陷，**不能**作为放宽阈值的理由；
  - 「老师否定却大涨」用来判断老师这条否定逻辑的边界，不改参数，写进报告；
- 格式可参照上一份已导入的包：`analyst` 字段原样复用，同一位老师必须一致。

## 输出：`teacher_pack_<T>.json`

**顶层字段**

- `schema`：`"teacher-review-pack/v1"`
- `analyst`：复用同一位老师的对象。OCR 水印里的执业证号不一致时停下来报告，不要猜。
- `review_date`：T；`target_session`：T 的下一个交易日。
- `source`：
  - `kind="video"`；
  - `job_id`、`source_ref`、`media_sha256`、`audio_sha256`、`duration_seconds`、`title`；
  - `stated_at=T`，`stated_precision="day"`；
  - `received_at`：provenance 的 `created_at`，转成 +08:00；
  - `strategy_available_at`：生成包的当前时间（+08:00），不得早于 `received_at`。
- `supersedes`：一般不写。只有在纠正同一天已经导入的包时，才写 `[旧 pack_id]`。
- `market`、`lessons`：简短的结构化摘要。
- `forecasts`：只收可以核对的预判。
  - 每条包含 `{id, text, time, test, check}`；
  - `check.kind` 必须属于 `FORECAST_KINDS`；
  - 老师给了时间窗口的，写 `within_sessions`；
  - 否定式（"估计冲不过去"）或条件式（"不过就必死"）的判断，没有对应的 check，不要写成预判。
- `stocks`：见下节。
- `calibration.owner_data`：
  - 目标地址、隧道状态、交易日、观测时间；
  - 数据源、覆盖率、新鲜度、`decision_eligible`；
  - 缺口，例如龙虎榜、概念板块为 0 行。
- `pack_id`：去掉 `pack_id` 后，把包序列化为规范 JSON（`json.dumps(..., ensure_ascii=False, sort_keys=True)`），取 sha256 的前 16 个十六进制字符。检查脚本会核对。

## 选哪些股票

1. 老师给出了判断的每一只股票都要收。判断包括：看好、不追、回避、等买点、到头、观察条件等。只念了名字、没有给判断的不收。
2. **对账是必需的一步**：`daily_context_<T>.md` 最后一张表是次日观察池，列出晋级延续和观察中的票。
   - 老师这次对其中任何一只有新说法（例如"买点还没出来""半废""到头了""还能拿"），都必须写进新包。新包会覆盖旧计划；如果写成 `rejected`，旧计划会被撤掉。
   - 老师没提到的票不要写，延续规则会接着处理。
3. 每只股票至少要有一条带时间码的原话（`evidence: [{time: "MM:SS-MM:SS", quote}]`），时间码要与 srt 的分段边界对齐。找不到原话就不收，并在报告里写明。
4. ASR 无法确认的名字，不要编代码，写进报告。代码和名称必须在 owner 名录里（检查脚本会核对）。

## 剧本与参数

| 老师的说法 | 剧本 | 立场 |
|---|---|---|
| 不追、回避、到头、谁买谁死、只看不参与 | `rejected`（只记录、结算） | negative |
| 接力票现在不追，但给了重新介入的条件（例如"换到 10 亿优势还在"） | `relay_no_chase`（`reenable_amount`） | negative / watch |
| 看好的接力（例如三进四） | `relay_race`；有首封额和成交额模板的用 `relay_acceleration`；一字板用 `relay_one_word`；快速封板用 `relay_fast_seal`；只说"会顶一次"的用 `relay_expect_touch` | positive |
| 跟随龙头的套利 | `sympathy_follow` | watch |
| 赌消息、板块成形才做 | `relay_news_conditional` | watch |
| 突破前高 / 站上某价 | `prior_high_breakout`（`prior_high`） | positive / watch |
| 平台突破 | `platform_breakout` | watch |
| 回踩某区间、底背离、失效价 | `ma5_reclaim_or_divergence`（`prior_high`、`support_level`、`invalid_below`） | watch |
| 强势股不追，回踩均线再买 | `leader_benchmark_pullback`（`pullback_ma`、`trend_floor_ma`） | positive / watch |
| 沿均线趋势持有 | `trend_continuation` | positive |
| 回踩 10 日线后的第二波 | `ma10_second_wave` | watch |
| 双底 / 慢速平台 | `double_bottom_platform` | watch |
| 大票站上 60 日线 | `ma60_reclaim` | watch |
| 突破后杀跌、缩量再起 | `trend_pullback_restart` | watch |

- **参数来源**：每个数值参数都要写 `<名>_src`，以 T、D、I 开头，后面跟简短说明。
  - T：老师原话，引用处要有时间码；
  - D：用数据或画面核对过的值（整理稿里 owner 核对过的数字算 D）；
  - I：为了让定性说法可以检验而设的默认值。
- 均线类参数（`hold_ma`、`floor_ma`、`pullback_ma`、`trend_floor_ma`、`support_ma`、`invalid_ma`、`ma`）只能取 5、10、20、60。
- `valid_sessions`（同时写 `valid_sessions_src`）：
  - 接力票为 1；
  - 趋势票按老师给的窗口（例如"周四前反包"就是 2）；
  - 老师给的是次日一次性检验（例如"明天再不上就失败"），为 1；
  - 其他默认 2。
- 老师原话和数据对不上时，用哪一个、为什么，写在该股票的 `uncertainty` 字段里，并列进报告。

## 校验与报告

1. 在任务目录执行 `python scripts/teacher_review_daily.py check <job_dir>`，`problems` 必须为空；`warnings` 逐条处理或说明原因。
2. 报告（直接回复即可，不另建文件）需要包含：
   - pack_id；
   - 按剧本、按立场的数量；
   - 预判清单；
   - 没收的股票及原因；
   - 老师原话与数据的分歧；
   - 所有 I 类默认值中值得人工复核的判断；
   - `overrides` 与 `carried_unmentioned` 的摘要。
