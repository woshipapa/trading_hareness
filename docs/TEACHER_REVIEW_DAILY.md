# 老师复盘每日流程（规范）

目标：每个交易日把老师的复盘视频变成次日盘中可以判定的计划。做法是结合当天收盘后的计划结算和收盘数据，在次日 09:15 前更新观察池。全程只做研究：不下单、不改实盘阈值，也不对 owner 执行任何 DDL。

- 策略机制：见 [TEACHER_REVIEW_COHORT.md](TEACHER_REVIEW_COHORT.md)
- 生成策略包的细则：见 [teacher_review/PACK_BUILD_BRIEF.md](teacher_review/PACK_BUILD_BRIEF.md)

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
