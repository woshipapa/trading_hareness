# 当前状态

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

## 等用户处理

- W004 2026-10-13 请看一下控制台“盘面雷达与战法”页签的新图，配色、数字口径、点的密度是否还要调。为什么等：这是按用户的参考图做的，是否“看得懂”只有用户能判断。之后：没有意见就关闭本记录。

## 到点要做

- W003 2026-10-13 15:20 先确认 owner 主机是否恢复（BACKLOG B031 写了现象和检查命令）。没恢复就提醒用户看云控制台；恢复后先读负载和内存、弄清原因，再做下面 18:45 那条。
- W001 2026-10-13 19:30 核对当晚盘后自动生成的涨停榜和风险分读数（明早 09:15 的竞价池从 10 月 13 日的风险分读数取昨日涨停股）：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/daily'` 返回的 `reading.trade_date` 应是 2026-10-13。没有的话看盘后流程是否运行，以及 owner 主机是否正常（BACKLOG B031）。
- W003 2026-10-13 19:30 核对该阶段第一次自动运行（盘后流程 18:45 才开始，早查只会看到“没有收据”）：读 `fetch_runs` 里能力 `daily_close_completion` 的回执（状态、各分类计数、剩下的股票和原因）；抽查补上的日线有没有涨跌停价和因子；健康检查 `data.daily_bars_complete`（`curl http://127.0.0.1:18300/api/research/indicators/health`）。阶段没跑或 `blocked`：查原因；当天缺口可用本机 `state/research/owner_repairs_20261012/` 里验证过的脚本手工补（写库由用户执行）。
- W006 2026-10-13 22:06 owner 发布窗口重开（今天 15:10–18:45 也开着，但 16 步做不完）。若 16 步、双重评审和合并都已完成：备好包装脚本交给用户执行，发布后先做历史回放的干跑。没完成就顺延到 10 月 14 日 11:30–13:00 或 15:10–18:45。本线要发三个单元：owner 纯代码（`quant-service/app/tactic_*.py` 等在纯代码白名单里）、edge 中继（`research-routes.mjs` 新增三条路由）、edge 控制台（新页签）；owner 发布要求提交已在 origin/main，所以包装脚本里先 `git push`。
- W008 2026-10-14 09:16 发布后第一个交易时段：核对两条采集循环按新节拍落库。只读查 owner（探针写法见 `docs/work/README.md`）：`quant.raw_market_observations` 里当天 `fuyao_ths` / `a_share_minute_cross_section` 和 `tdx_public` / `a_share_minute_cross_section_shadow` 的 `effective_at`，09:15 起每分钟各一份、秒数稳定（主源在第 50 秒之后几秒、通达信在第 52 秒之后），没有漏分钟。异常（没有文档、或漏得比原来多）立刻告诉用户：回滚命令是把 `owner-code-20261013-03` 重新发布，但 08:30–11:30 是不能发布的窗口，要用户决定是否破例。收盘后跑当日汇总看可判分钟数和是否达标，记为 5 个交易日观察的第 1 天。
- W006 2026-10-14 09:26 （10 月 13 日晚没发布的话，这条和下一条顺延到发布后的第一个交易日）发布后的第一个交易日：核对竞价看板读数已存、与 09:25 处置表同一批股票，页面可见。
- W006 2026-10-14 19:00 第一份每日总结：核对生成、各战法当日结果与手算的两三条一致。

## 正在运行

- W006 2026-10-13 17:26 Codex 实现者在做第 9 步（同一线程 01a1339e-3f02-7072-ba13-2d122b927799，工作区 `/Users/papa/codebase/.codex-wt/n8n-tactic-trials`）。结束标志：分支在 7f70c6c9 之后有两个提交且 `git status --short` 为空。之后：复跑验收；看总结计时；勾掉 Steps；派第 10 步。
- W007 2026-10-13 17:30 取数脚本在跑（主会话的后台命令）：11 个代码顺序取，每次请求后停 0.3 秒，带 `--until=18:35`，到点自己停并在日志里写 PAUSED（避开 18:45 开始的盘后流程）。日志 `state/research/vshape_20261013/data/fetch.log`，数据 `<代码>.jsonl`。结束标志：日志末行 FINISHED 或 PAUSED；ABORT 是连续 20 次请求失败。挂在主会话 93ba6e1d 上；主会话结束它就停，重跑同一条命令即续取。
- W008 2026-10-13 17:57 Codex 实现者在做派工 5（B3 的工具部分和分类规则），线程 01a1349e-01a9-7172-9d3f-d63b744c9bcb，工作区 `/Users/papa/codebase/.codex-wt/n8n-tdx-promote-gate`，分支 `codex/tdx-promote-gate`。结束标志：分支在 4889ff76 之后有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上，判断死活和续派的办法同 W006 的 Resume here。
- W010 2026-10-13 19:25 起并行的 Codex 调用（都挂在会话 eac0e1fc 上；工作区都在 `/Users/papa/codebase/.codex-wt/n8n-<slug>`，都从 base 分支的 bb228919 起步，

## 待办清单

open 21, ready 9, doing 1, done 3, dropped 1

在做的、可以开工的、等用户处理的:

- B002 (ready) 2023 年的复权因子补齐
- B003 (ready) 2023 年缺的涨跌停价补齐
- B006 (ready) 分四档的资金流在 2026-08-20 之后的缺口
- B009 (ready) 仓库涨跌停规则的三处缺陷和 9 月 30 日的错值
- B010 (ready) 日线表里周末日期的行
- B019 (ready) 老师复盘包导入时竞价金额的单位校验
- B031 (doing) owner 主机 2026-10-13 收盘后不响应
- B033 (ready) 通达信并行快照和比对行没有列入冷热分层
- B023 (ready) 全市场分钟快照 09:16 才开始
- B024 (ready) 竞价处置表缺的那 1 只没查明
