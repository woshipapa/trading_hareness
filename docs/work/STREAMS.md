# 工作线索引

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | verified | none | 10 月 13 日 19:30 之后核对当晚的涨停榜和风险分读数已自动生成（见 Due），通过后把本记录改成 `closed` | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | none | W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 先看 owner 主机是否恢复（BACKLOG B031）；恢复后在 10 月 13 日 19:30 之后核对该阶段的第一次自动运行（见 Due），通过后改成 `verified` | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | building | Claude Code 会话 93ba6e1d（主会话，负责验收与派工），2026-10-13 | 等 Codex 交回第 9 步（`tactic_trial_runtime.py`：收盘和竞价的刷新、结算、每日总结、区间回放、命令行、给接口用的读取函数；分两个提交），复跑发布路径测试，看它报告的总结计时（760 天、每天 100 行），通过后派第 10 步（盘后阶段接线和边界测试） | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | planned | Claude Code 会话 93ba6e1d，2026-10-13 | 看 `state/research/vshape_20261013/data/fetch.log`：末行是 FINISHED 就核对 11 个文件各约 750 行（上市晚的 ETF 少），然后写研究登记、派 Codex；末行是 PAUSED 就在盘后流程结束后（10 月 13 日 20:30 以后，或次日非交易时段）重跑同一条命令续取：`/Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-fetch.py SH000001 SZ399001 SZ399006 SH000852 SH000300 510300 512100 510050 510500 159915 588000`（要限时就加 `--until=HH:MM`） | 2026-10-13 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | building | Claude Code 会话 93ba6e1d（主会话，派工与验收），2026-10-13 | 等 Codex 交回派工 5（升级工具认 `min_share`、`--decision-eligible`、五份当日汇总的门槛；发布分类规则里登记 `scripts/tdx-promote.py`），在工作区 `/Users/papa/codebase/.codex-wt/n8n-tdx-promote-gate` 跑验收（发布路径测试、`pytest scripts -q`、分类器的 node 测试）并读 diff，然后送 Codex 评审者（只读）做双评审；通过后 `--no-ff` 合并进 main、回收工作区，再开 `tdx-live-snapshot-fallback` 分支派工 6 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | building | Claude Code 会话 eac0e1fc，2026-10-13 | 收齐并行调用的结果。先处理 base：评审者的意见用线程 01a13455 修掉 → base 后端和 `relay-auto-base-ui` 都合进集成分支 → 集成分支上跑全部验收 → 合进 main、热更新 edge（“目标群”栏目上线）。再逐条功能线：在其工作区 `git merge integration/relay-auto`，复跑验收、edge 上跑 bridge 套件，用它自己的线程续派看板一步，双重评审，合进集成分支 → main → 热更新 | 2026-10-13 |

## 已结束、暂停或放弃

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W005](streams/W005-short-term-tactics-research.md) | 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0） | closed | none | none | 2026-10-13 |
