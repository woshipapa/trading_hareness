# 老师复盘每日流程（规范）

目标：每个交易日把老师的复盘视频变成次日盘中可以判定的计划。做法是结合当天收盘后的计划结算和收盘数据，在次日 09:15 前更新观察池。全程只做研究：不下单、不改实盘阈值，也不对 owner 执行任何 DDL。

- 策略机制：见 [TEACHER_REVIEW_COHORT.md](TEACHER_REVIEW_COHORT.md)
- 生成策略包的细则：见 [teacher_review/PACK_BUILD_BRIEF.md](teacher_review/PACK_BUILD_BRIEF.md)

## 视频怎么进来（事件驱动，2026-09-25 起）

老师每个交易日晚 19:10~21:40 在爱投顾「猎场擒龙内参」发复盘视频。47 edge 上的
`itougu_neican_relay` 本来就在转发这条内参，并且**顺手把视频任务写成一行 JSON 追加到
`/var/lib/itougu-neican/video-tasks.jsonl`**（幂等 `task_key`、不含凭据、不下载媒体字节）。
那份队列就是事件源——不用改 edge 的服务、不用把本机暴露给 edge、也不用解析飞书消息。

```
老师发布
  ↓ itougu_neican_relay（非交易时段每 10 分钟轮一次）→ 追加 video-tasks.jsonl
  ↓ video.ingest（supervisor，每 10 分钟，仅 18:00–23:59）读队列 → POST 本机 harness 建任务
  ↓ harness：ASR → 抽取 → owner 证据 → 第三阶段 a 取数 → 逐节写稿
  ↓ teacher.cycle（每 20 分钟）：gate → 建包 → check → 自动入池 → 扫描 → 蒸馏 → 报告
```

总延迟 ≤ 20 分钟（relay 10 + ingest 10）。

`video_understanding_harness/ingest.py`：

- `SOURCES` 是**注册表**，加来源就是加一条 `Source(name, fetch, allowed_hosts, note)`；
  去重台账、URL 白名单、投递、报告全部共用。`--list-sources` 看当前注册了什么。
- 只接受 https 且主机名在白名单内（`voss.itougu.com`）；`voss.itougu.com.evil.com` 这种也拦。
- 一轮最多投 `VIDEO_INGEST_MAX_PER_RUN`（默认 1）个，且只投
  `VIDEO_INGEST_MAX_AGE_DAYS`（默认 3）天内发布的；补历史用 `--since YYYY-MM-DD`。
- 台账 `ingest_ledger.json` 只存 `task_key`、job_id、主机名 —— **不存完整 URL**。
- 时间窗 `VIDEO_INGEST_WINDOW`（默认 `18:00-23:59`）只在 `--scheduled` 时生效；
  窗口配置写坏时**全天开着**，宁可多看一眼也别漏掉当天的视频。
- **第一次启用前先 `--seed`**：把队列里已经有对应 harness 任务的标进台账，
  否则第一轮会给同一场复盘再建一个任务。

## 「换手」有两个口径，不要混

老师说「换手」时念的是**成交额**（"远端十五个亿，近端仅才五"），不是换手率。owner 的
level1 里那个 `turnover` 字段**同样是成交额（元）** —— 这个混用是行业通病。所以：

| 名字 | 是什么 | 哪来的 |
|---|---|---|
| `*_amount_yi`（`near_`/`far_`/`today_`/`leg_amount_excl_today_`） | **成交额，亿元** | `chip_distribution` 从 500 根日 K 算 |
| `*_chips_yi`（`overhead_`/`underhang_`） | 衰减后的筹码，折算成亿元成交额 | 同上 |
| `*_share_pct`（`trapped_`/`profit_`/`peak_`） | 占总筹码的**百分比** | 同上 |
| `换手率` | **真换手率（rate）** | 只在按需取回的授权通道涨停池行里 |
| `turnover_max_pct` | 真换手率阈值（一字板判定用 3%） | 剧本参数，见 PACK_BUILD_BRIEF |

2026-09-25 之前我们的字段叫 `*_turnover_yi`，容易被当成百分比，已改名；每份筹码输出
自带 `units` 段说明这件事。旧产物（9/21–9/24）仍是旧名，`with_current_names()` 读的
时候会补上。**我们不提供真换手率**：需要流通股本，owner 只读面里没有。

## 时间线（T = 老师复盘的那个交易日，北京时间）

| 时间 | 环节 | 谁来做 | 产出 |
|---|---|---|---|
| T 15:00 | 收盘 | — | — |
| T 约 16:05 | owner 收盘流水线（`post-close-refresh-v6`）写入全市场日线和控制数据 | owner（15681） | `canonical_bars_daily` 等 |
| T 16:15 起 | `peer_close_research`，在 peer 调度器上自动执行：<br>① 结算 T 日老师计划，按延续规则分成晋级延续、观察、退出<br>② **次日结果复盘**：分类 + 归因 + 学习（见下节）<br>③ 自选股复盘<br>④ 小杰结算<br>⑤ **跨策略日报**：把上面几件合成一条飞书和一页 | 自动 | T+1 的延续计划、各自存档、复盘存档、飞书摘要 |
| T 晚间 | 老师视频发布后，harness 建任务（类别 `financial_review`）：<br>下载 → 大/小两套 ASR → OCR → 个股实体 → 研究智能体抽取 | video_understanding_harness | 任务目录 `jobs/<job_id>/` |
| T 晚间 | 量化整理老师观点，并用 owner 读路径核对价位 | harness 侧智能体 | `teacher_strategy_<T+1>.md`、`owner_market_evidence_<T>.json` |
| T 晚间 | **第 0 步 看复盘**：读 T 日的结果复盘，知道哪些条件该调 | `teacher_review_daily.py outcome` | `outcome_<T>.json/.md` |
| T 晚间 | **第 1 步 上下文**：取 T 日结算、延续结果和 T+1 观察池 | `teacher_review_daily.py context` | `daily_context_<T>.json/.md` |
| T 晚间 | **第 2 步 生成策略包**：按细则，把老师观点和上下文合成 pack | 智能体（Claude 等） | `teacher_pack_<T>.json` |
| T 晚间 | **第 3 步 检查**：校验结构，核对 owner 名录，演练导入，列出会覆盖哪些计划 | `teacher_review_daily.py check` | `pack_check_<T>.json` |
| T 晚间至 T+1 08:30 | **第 4 步 导入**：通过服务 API 写入观察池，推送飞书"入池"通知 | `teacher_review_daily.py import` | `import_report_<T>.json`、`pool_<T>.md` |
| T+1 09:15 起 | 盘中逐只判定：新计划和晋级延续的票触发即推送；观察的票不推送买点 | 自动 | 飞书提醒 |

**截止时间**：T+1 08:30 前完成导入。晚于 T+1 09:15 的导入会自动顺延到 T+2 生效（服务按"可用时间之后的第一个 09:15"选定生效交易日），不会误用于当天盘中。

## 交接约定：harness 任务目录里的文件

| 文件 | 来源 | 必需 |
|---|---|---|
| `transcript_large.srt`、`transcript_small.srt` | harness ASR | large 必需 |
| `provenance.json`、`state.json` | harness | 必需 |
| `ocr_evidence.json` | harness OCR | 建议有 |
| `teacher_strategy_<T+1>.md` | 量化整理（价格已用 owner 数据核对） | 必需 |
| `owner_market_evidence_<T>.json` | owner 读路径的证据 | 建议有 |
| `daily_context_<T>.json/.md` | 第 1 步 | 由流程生成 |
| `teacher_pack_<T>.json` | 第 2 步 | 由流程生成 |
| `pack_check_<T>.json`、`import_report_<T>.json`、`pool_<T>.md` | 第 3、4 步 | 由流程生成 |
| `outcome_<T>.json/.md` | 第 0 步 | 由流程生成 |
| `owner_capability_surface.json` | 第三阶段 a 拉的 owner 活目录 | 由流程生成 |
| `agent_prompt_strategy_<节>.md`、`agent_strategy_raw_<节>.md` | 写稿逐节的提示词与原始返回 | 由流程生成 |
| `writer_short_passes.json` | 重试后仍然写不全的节及原因 | 有才写 |
| `agent_prompt_data_requests.md`、`agent_data_<T>.json` | Agent 的取数需求与取回结果 | 由流程生成 |
| `chip_evidence_<T>.json` | 日 K 筹码代理（套牢区/筹码峰/远近端换手） | 由流程生成 |

### 第三阶段 a：Agent 自己向 owner 要数据

写稿之前多一趟：把 owner **此刻真正在服务的只读能力**拉下来（`/openapi.json` 的
GET 路径 + 授权通道 `/api/v1/research/longhu/catalog` 的已登记操作），作为目录交给
Agent；它读老师的 `method_notes`，在他做量价推演的地方（远端/近端换手、封单、套牢、
上方压力、30 分钟 60 均线、几板扛不住）声明要哪些数据，harness 代它取回，再进写稿。

- 目录是**运行时拉的**，不是写在代码里的清单：拉不到就这一轮不给取数能力，也不拿
  旧清单冒充（`owner_capability_surface.json` 的 `errors` 会记下原因）。
- 三种问法：`metric`（本地算的派生量，如 `chip_profile`）、`path`（只读 GET）、
  `target`+`action`+`controller`（授权通道操作）。目录之外、写方法的接口、不在
  `bound_codes` 里的代码，一律丢弃并记下理由（`agent_data_<T>.json.dropped_requests`）。
- Agent 全程没有网络也没有凭证，只声明需求；`X-Quant-Read-Key` 只在进程内存里，
  不进目录文件、不进提示词、不落盘。
- 上限 `VIDEO_AGENT_DATA_MAX_REQUESTS`（默认 24）、`VIDEO_AGENT_DATA_MAX_PER_CODE`
  （默认 6），避免一个宽泛的请求吃掉收盘后的时间预算。

### 写稿返回空稿时

写稿模型偶尔只返回一句“我将要怎么做”的开场白。这种稿子会缺章节、附录 B 检查到 0
个数字，**交接清单必须自己拦下来**：`narrative_defects()` 会把它记成 `status=
strategy_unusable`、`ready=false`，`teacher_cycle` 的 gate 也会拒（`checked < 50`）。
**真正的原因是提示词形状**：写稿提示词最后一样东西是一大团 JSON（`输入：{payload}`），
没有收尾的命令句，模型就把任务读成"请说明你会怎么做"，回一句"我会按……组织第 1–4 节"。
修法：结尾点名正文第一行长什么样并明确禁止前言（`teacher_strategy._closing()`；
第二阶段在 `agent_packet.prompt_for` 末尾）。**拆两趟和列式编码都没能修掉它**——
A 半 87k 字符照样只回开场白，所以不是规模也不是截断。

顺带修掉的四个真问题（都有实证，但都不是这个症状的成因）：

1. provider 只保留最后一段 `output_text`（模型先发开场白再发正文时，正文被丢掉）；
2. `response.incomplete` 被当成正常返回；
3. 截断被当成可重试错误 —— 同一个提示词、同样的 effort 重试只会再截断一次，
   而链上是 3 模型 × 2 key × 3 次。现在 `IncompleteResponse` 直接换模型，不重试同一个；
4. **一趟写多节时输出不稳**。同一个提示词、同样的输入，一次写全第 1–4 节（22.6k 字节），
   一次只写到第 1 节就停（2.4k）—— 运行间波动。所以：
   * **一趟只写一节**（`## 1./2./3./4./5./5b./6./7.`），第 5b 节再按推演条目分批
     （`VIDEO_WRITER_NOTES_PER_CALL`，默认 8 条）；
   * **每一节只带它写得着的输入**（`PART_NEEDS`）。第 1 节只要指数和大盘，33k 字符；
     逐股表要 stocks + 筹码，82k；取回数据只给第 5b 和第 6 节（逐股表里塞进来只是
     29k 负担），按票分批时取回数据也按 code 过滤；
   * **写完当场校验**（`part_ok`）：该节标题在不在、标题之后有没有正文。不行就重试
     这一趟（`VIDEO_WRITER_PART_RETRIES`，默认 2），只重试它。仍然不行就留最长的
     那一次，并把原因写进 `writer_short_passes.json`，由交接清单的闸门去拦；
   * 拼接时按章节号去重（`join_narrative`）—— 分批的第 5b 节每批都会自己带标题。

   产物：`agent_prompt_strategy_<节>.md`、`agent_strategy_raw_<节>[.retryN].md`。
   `VIDEO_WRITER_PARTS=whole` 可回到单趟对照。

载荷同时改成列式编码（字段名只写一次，113k → 89k 字符，无损）。

### 自动入池（2026-09-25 起默认开启）

`teacher_cycle` 每 20 分钟一轮，`check` 全绿就直接导入。导入是这条链里唯一的对外
写操作，所以过三道闸，任一不过就写 `hold` 等人看：

1. **gate**：owner 读路径通、交接清单 `status=completed`、正文没有阻断性代码问题、
   正文核对的数字 ≥ 50（防止拿空证据跑出来的稿子建包）；
2. **check**：`problems` 为空；
3. **时间窗** `import_window`：目标就是今天而且**已开盘**或**已收盘**时，一律不自动
   入池 —— 一个过了半场的交易日不该再自动加计划。目标是未来交易日的照常导。
   休市日没有"盘中"，所以不会被误拦。

同一个包绝不导入两次（自己记账 + `check` 会报 already imported）。
要关掉：`TEACHER_CYCLE_AUTO_IMPORT=0`。

## 命令

在工作站上执行，使用共享 venv（`/Users/papa/.venvs/svc/bin/python`）：

```bash
J=/Users/papa/codebase/video_understanding_artifacts/jobs/<job_id>
python scripts/teacher_review_daily.py context $J --date <T>    # 第 1 步
# 第 2 步：按 PACK_BUILD_BRIEF 生成 $J/teacher_pack_<T>.json
python scripts/teacher_review_daily.py check  $J                # 第 3 步；有 problems 时退出码为 1
python scripts/teacher_review_daily.py import $J                # 第 4 步：先重跑一遍检查，通过才导入
python scripts/teacher_review_daily.py outcome $J --date <T>    # 第 0 步；--rerun 重算
# 跨策略日报与反事实扫描（收盘后，在 peer 容器内）：
#   python -m app.teacher_review_ops digest --date <T>
#   python -m app.teacher_review_ops sweep  --date <T>
python scripts/teacher_review_daily.py status $J                # 随时查看观察池
```

脚本通过 SSH（`stockpeer@47.110.79.189:3535`）在 peer API 容器里执行 `python -m app.teacher_review_ops`。策略包经 stdin 传入，不落任何临时文件。导入调用服务自己的 `POST /api/v1/teacher-review/packs`，写权限 key 由容器从自身环境变量读取，全程不打印。

## 次日结果复盘（每天自动跑，也可以手动重算）

结算回答"计划走成什么样"，这一层回答"我们的条件对不对"。T 日收盘后自动执行，
存档 capability 为 `teacher_review_outcome`，读取接口 `GET /api/v1/teacher-review/outcomes`。

**每只票落到一个结果里**

| 结果 | 含义 |
|---|---|
| 触发并守住 | 推送了买点，收盘仍在入场价之上 |
| 触发后回落 | 推送了，收盘跌回入场价下方 |
| **未触发但大涨** | 没推送，但收盘涨 ≥5%（或封板），或盘中最高涨 ≥7%（或触及涨停）——**要学的就是这一类** |
| 盘中失效 | 触发前就跌破失效价 |
| 未触发且未涨 | 条件正确地把它过滤掉了 |
| 老师否定但大涨 | 老师说不碰，结果涨了 —— 老师这条逻辑的边界 |
| 老师否定且确实未涨 | 避坑正确 |

**归因怎么来的**：盘中每次扫描的规则输入都存在 `quant.intraday_rule_input_snapshots`，
复盘按 2 分钟一档重放同一个纯函数 `teacher_review_rules.evaluate`，数出每条 gating
条件当天卡了多少次、最接近触发的那一刻差几条。三种原因分得很清楚，不要混着看：

1. **条件太严**：某条 gating 条件全天未满足 → 报告给出这条的名字、卡住比例、最后一次的值；
2. **输入缺失**：规则要的输入（分时均价、成交额、开盘价、封板盘口…）过半扫描取不到 →
   这是数据问题，不是阈值问题，报告单列；
3. **重放满足却没推送**：重放显示条件全满足但盘中没有事件 → 这是缺陷，要查采样间隔、
   输入缺失或事件确认，**不要**据此放宽阈值。

**收益口径**：和小杰结算完全一致 —— 三种持有期（入场→当日收盘、入场→次日收盘、
次日开盘→次日收盘）、每种扣一次往返成本、与当日全市场中位数比超额；**封板价触发的
单列为「买不到」，不计入任何胜率**，因为它反映的是标记太晚而不是选错。次日的复盘会
回填前一天的远期收益。

**学习**：按剧本滚动累计最近 20 份复盘的命中率、漏网率、净收益与超额；同一条条件在
漏掉的大涨里卡满 3 次，就进"条件复核建议"。建议只描述计数，**任何阈值都不会自动改**。

要改阈值，按这个顺序：

1. `teacher_review_ops sweep --date <T>` —— 在当天自己的扫描输入上重放其它阈值，
   同时给出多抓到什么、多放进来什么、净收益与超额如何变化；
2. 把改动连同**事先写下的预期**（哪个指标、往哪个方向、看多少个交易日）记进变更台账
   （`POST /api/v1/research/strategy-changes`），没有预期的改动会被拒绝；
3. 之后每天的日报自动对照预期报告改动前后的变化，与预期相反的会被顶到最前面。

## 自动化：teacher_cycle

2026-09-25 起这条链由 `svc_supervisor` 的 `teacher.cycle`（每 20 分钟）自己跑，
入口 `scripts/teacher_cycle.py`：

    gate → chips → context → pack → check → import → sweep → distill → report

| 步骤 | 做什么 | 调模型 |
|---|---|---|
| `gate` | owner `/health`、交接清单、`checked` 数字够不够（< 50 判为证据缺失，拦住） | 否 |
| `chips` | 筹码分布代理（`chip_distribution.py`），把"远端/近端/套牢区"算成数 | 否 |
| `context` | 当日结算 + 次日观察池 | 否 |
| **`pack`** | **`teacher_pack_agent.py` 按细则生成策略包的判断部分** | **是，唯一一次** |
| `check` | `validate_pack` + dry-run + owner 名录核对 | 否 |
| `import` | 写 owner 观察池；**默认不自动执行** | 否 |
| `sweep` | 反事实扫描：换阈值会多抓到什么、多放进来什么 | 否 |
| `distill` | 重建 `teacher_method_library.md`，贴上次日结果 | 否 |

建包的分工不可颠倒：schema、analyst 沿用、source 哈希与时间、`calibration.owner_data`
八项、`pack_id`、`validate_pack` 全由代码生成；模型只填
`market / lessons / method_notes / forecasts / stocks`。

**闸门**：`import` 只在 `check` 的 `problems` 为空**且** `TEACHER_CYCLE_AUTO_IMPORT=1`
时执行，否则写 `hold` 等人；owner 读路径不通就停，不得改用 5681；同一个包不导两次。
单次运行的记账在 `cycle_<T>.json`，可读的日报在 `cycle_report_<T>.md`。

**动 peer overlay 或重启 harness 之前，先确认 agent 不在跑**：2026-09-25 在第二阶段
读 owner 时切包，29 只个股全部 `RemoteProtocolError`，而写稿阶段照样出了一份没有
D 值的稿子。现在 `evidence_unusable()` 会重试一次再抛 `OwnerUnavailable`，`gate`
也会拦住这种稿子，但最省事的还是别在 agent 跑的时候动服务。

## 每一步的检查

1. **上下文**：`peer_close_stages` 三个阶段都应该是 `completed`。如果不是：
   - 16:15 之前属于正常，等一下；
   - 如果已经过了 16:30，看调度器日志里的 `peer close research failed`。任务每 10 分钟重试一次。
2. **生成**：细则里的每一条都要满足。老师对观察池里已有的票给出新判断时，必须写进新包。
3. **检查**：`problems` 必须为空。
   - `warnings` 要逐条看，例如名称和名录不一致、pack_id 不是内容摘要。
   - `overrides` 列出新包会替换或撤掉哪些已在池中的计划，要逐只确认是老师本人的意思。
   - `carried_unmentioned` 是老师这次没提、但会按延续规则继续留在池里的票。
4. **导入**：结果必须是 `status=imported`。
   - `plan_failures` 是因缺日线没能冻结计划的票，要记录下来。夜间数据面补数可能失败，这类票次日不会盯盘。
   - 导入可能耗时十几分钟（补数、30/60 分钟背离都要调行情接口）。HTTP 超时后脚本会轮询存档确认。

## 规则

- **最新复评为准**：新包提到的票一律按新包处理。新包否定的票（剧本 `rejected` 或 `relay_no_chase`），它的旧计划在导入时一并撤下。
- **延续**：新包没提到的票，按 `teacher_review_lifecycle` 处理：已满足的晋级延续（推送），未满足的只观察（不推送），失效或到期的退出。详见 TEACHER_REVIEW_COHORT.md 的"计划的延续"。
- **纠正当天的包**：包一旦导入就不能改。要纠正同一天复盘时，生成一个新包，写上 `supersedes: [<旧 pack_id>]`。旧包会被整体作废，被它撤下的计划不会复活。
- **部署窗口**：09:00–15:00 不部署。导入不属于部署，但盘中导入只对下一个交易日生效。
- **owner 数据声明**：策略包的 `calibration.owner_data` 要写明以下各项。隧道不可用时，owner 数据按"未核实"处理，不能用本地 5681 冒充。
  - 目标地址与隧道状态；
  - 交易日与观测时间；
  - 数据源；
  - 覆盖率与新鲜度；
  - `decision_eligible`。

## 异常处理

| 现象 | 处理 |
|---|---|
| 上下文里 `settled=false` | 当日结算还没跑，等 `peer_close_research`；也可以先生成策略包，导入前再取一次上下文 |
| 检查报 `already imported` | 同一个包已经导入过；如需纠正，按上面"纠正当天的包"处理 |
| 检查报 `not in quant.instruments` | 代码错误或者是新股；核对 ASR 与 OCR，不要编造代码 |
| 导入结果为 `unconfirmed` | 查看 `status`，或者在容器里查 `teacher_review_repository.pack_record(<pack_id>)` 是否已存档；不要换一个 pack_id 重复导入 |
| `plan_failures` 为缺日线 | 写进当日记录；第二天的收盘任务会重新冻结价位 |
