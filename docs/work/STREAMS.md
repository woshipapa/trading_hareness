# 工作线索引

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | verified | none | “回看接口把未来交易日算进最近 N 天”的修复（7bb29e71，见 BACKLOG B011）随下一次 owner 发布上线后，关闭本记录 | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | none | W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 10 月 13 日 18:45 之后核对该阶段的第一次自动运行（见 Due），通过后改成 `verified` | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | building | Claude Code 会话 93ba6e1d（主会话，负责验收与派工），2026-10-13 | 等 Codex 交回“卖出规则改名＋第 6 步”（`tactic_trials.py` 上半：战法、组合逻辑、选股、格内当日结果），复跑发布路径测试，核对二十个组合的定义与方案的表一致，通过后派第 7 步（战绩、标签、两两组合、每日总结） | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | planned | Claude Code 会话 93ba6e1d，2026-10-13 | 等 owner 恢复且 B030 查明原因后，重跑第一轮：`/Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-fetch.py SH000001 SZ399001 SZ399006 SH000852 510300 512100`（带代码参数就不存雷达），全天雷达另用 `curl` 存到本机 `state/research/vshape_20261013/radar/radar_2026-10-13.json`；第一轮日志出现 FINISHED 后第二轮自动开始（若排队命令已随主会话结束，按 Running 里的命令手工跑）；两轮都完成后核对 11 个代码的天数，写研究登记，派 Codex | 2026-10-13 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | planned | 主会话 93ba6e1d 派出的接手 agent（只做到“方案审完、待批准”），2026-10-13 | 用 10 月 13 日首个影子交易日的实测更新方案（四个前置修复、完整性门槛、盘中一致性容差的取值），交 Codex 审查，然后请用户批准 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |

## 已结束、暂停或放弃

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W005](streams/W005-short-term-tactics-research.md) | 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0） | closed | none | none | 2026-10-13 |
