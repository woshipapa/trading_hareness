# W011 每晚该跑的阶段由我们这一侧自己触发

- state: planned
- holder: Claude Code 会话 93ba6e1d（主会话），2026-10-13
- goal: 每个交易日晚上不靠人手，自动存当天的涨停榜和大面风险分读数，随后是情绪温度、宽基 ETF 放量、金银指；别的工作线以后把自己的晚间阶段加到同一个地方
- plan: docs/work/plans/peer-evening-stages.md
- next: 等 Codex 对方案的只读审计（新线程，主会话的后台调用；结论是 SOUND 或逐条异议），按异议改方案，把最终方案的要点给用户看一眼后建工作区 `peer-evening-stages` 派工（两次）。目标是 10 月 14 日 15:10–18:45 的窗口发布，当晚第一次自动运行
- updated: 2026-10-13

## Requirements

- 2026-10-13 「发布，然后把这些变成自动跑」（原文“自动抛”，按上文理解为自动跑；“这些”指当晚查出来的没有任何东西定时触发的阶段，首先是涨停榜和风险分）

## Plan

`docs/work/plans/peer-evening-stages.md`。要点：把 `limit_performance_lists`、`limit_up_risk`、`market_temperature`、`broad_etf_flow`、`market_timing` 五个阶段放进 `peer_close_research` 的晚间循环（每 10 分钟一次，16:15 之后、当天日线到齐才开始；每个阶段有回执，完成了不重跑），排在现有五个阶段之前；榜单取不到或风险分没有榜单时回执不关闭，下一轮重试；早上的补跑走历史榜单。不放进去的：盘中温度和分钟面板导出（要读当天全部分钟文档，新主机内存没量过）、依赖日线控制对账的三个阶段（控制面每天 blocked，BACKLOG B043）。

## Steps

- [ ] 方案审计（Codex，只读，最多三轮）
- [ ] 1 `peer_close_research.py`：阶段顺序、适配、依赖、时限；测试
- [ ] 2 `main.py` 接线与组合测试；决策记录；`docs/ARCHITECTURE.md` 一行
- [ ] 双评审、合并
- [ ] 发布（owner 纯代码，用户执行）
- [ ] 第一个晚上的核对；连续两晚通过后撤掉手工脚本
- [ ] 发布后手工补 10 月 12、13 日的温度、ETF、金银指读数（写 owner，用户同意后做）

## Log

- 2026-10-13 22:20 开线。起因见 BACKLOG B042：完整的盘后流程只在对方自己的服务上定时运行，我们后加的阶段没有任何东西触发；当晚的榜单和风险分是手工补的。方案写成，送审计。

## Waiting on the user

## Running

- [ ] 2026-10-13 22:20 Codex 方案审计在跑（只读，新线程）。结束标志：它交回 SOUND 或编号的异议。之后见 next。挂在主会话 93ba6e1d 上。

## Due

- [ ] 2026-10-14 15:00 当晚的手工补跑仍然要做，直到本线发布并核对通过：15:00 之后、24:00 之前执行 `bash ~/codebase/n8n/state/lists13.sh`（写 owner 库；会话的安全检查可能拦，拦了就请用户执行）。本线若已在当天发布，就等 16:40 看自动运行的结果，没出来再手工跑。

## Evidence

- 缺口的证据：FACTS F023、F024；`docs/work/probes/2026-10-13-evening-flow.out.txt`、`-2.out.txt`。
- 当晚手工补的脚本：`docs/work/probes/2026-10-13-evening-lists-and-risk.py`（调用的就是那两个阶段的函数）。

## Open questions

- 三个决策 0013 的阶段从没在调度进程里跑过，内存和耗时发布后第一晚量。
- `trade_calendar` 要不要也由我们这一侧跑：没查对方那边的日历同步是否覆盖我们需要的前瞻日历。

## Resume here

方案在 `docs/work/plans/peer-evening-stages.md`，还没审计完、没开工。接手的人先看 BACKLOG B042 和上面的 next。不要为了“先跑起来”去调用 owner 上的完整盘后刷新接口：它会把十几个没跑过的阶段一起跑，其中分钟面板导出在 894 MB 的主机上很可能撑爆内存。
