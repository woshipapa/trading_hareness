# W011 每晚该跑的阶段由我们这一侧自己触发

- state: released
- holder: Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14
- goal: 每个交易日晚上不靠人手，自动存当天的涨停榜和大面风险分读数，随后是情绪温度、宽基 ETF 放量、金银指；别的工作线以后把自己的晚间阶段加到同一个地方
- plan: docs/work/plans/peer-evening-stages.md
- branch: main
- head: 0a57d7fb
- release: owner-code-20261013-06（owner 纯代码，2026-10-14 00:43）
- next: 10 月 14 日 16:50 核对第一次真正的当晚自动运行（见 Due）：用 `docs/work/probes/2026-10-14-peer-evening-receipts.py` 的做法查 10 月 14 日五个 `peer_` 回执和读数（把脚本里的日期改成 10 月 13、14 日另存一份），再看调度容器日志有没有“left open”。通过后把本记录改成 `verified`；连续两晚通过后删掉手工脚本 `state/lists13.sh` 那条 Due。
- updated: 2026-10-14

## Requirements

- 2026-10-13 「发布，然后把这些变成自动跑」（原文“自动抛”，按上文理解为自动跑；“这些”指当晚查出来的没有任何东西定时触发的阶段，首先是涨停榜和风险分）

## Plan

`docs/work/plans/peer-evening-stages.md`。要点：把 `limit_performance_lists`、`limit_up_risk`、`market_temperature`、`broad_etf_flow`、`market_timing` 五个阶段放进 `peer_close_research` 的晚间循环（每 10 分钟一次，16:15 之后、当天日线到齐才开始；每个阶段有回执，完成了不重跑），排在现有五个阶段之前；榜单取不到或风险分没有榜单时回执不关闭，下一轮重试；早上的补跑走历史榜单。不放进去的：盘中温度和分钟面板导出（要读当天全部分钟文档，新主机内存没量过）、依赖日线控制对账的三个阶段（控制面每天 blocked，BACKLOG B043）。

## Steps

- [x] 方案审计 — 两轮，共 15 条，都进了设计（2026-10-13）
- [x] 1 `peer_close_research.py`：阶段顺序、适配、依赖、时限；测试 — 632bce9b；`Ran 3058 tests ... OK (skipped=2)`；`pytest scripts -q` → `1 failed, 360 passed`（失败的是 B035）；主会话自己写的（用户 2026-10-13 让主会话接手实现）
- [x] 2 `main.py` 接线与组合测试；决策记录 0021；`docs/ARCHITECTURE.md` 一段 — 632bce9b、6743d4a1
- [x] 合并 — 0a57d7fb（`--no-ff`）。评审只有主会话自己逐条过了一遍，没有第二个评审者：实现者和评审者是同一个，这是本线的一处欠缺，发布后的核对要做足
- [x] 发布（owner 纯代码，用户执行）— `owner-code-20261013-06`，2026-10-14 00:43，发布日志见 `docs/RELEASE_SYNC_47.md` 当天那一行
- [ ] 第一个晚上的核对；连续两晚通过后撤掉手工脚本
- [x] 10 月 12、13 日的温度、ETF、金银指读数 — 不用手工补：发布后循环补跑 10 月 13 日时这三个阶段把两天的读数都存了（探针输出里两天各 1 份，时间 00:44）

## Log

- 2026-10-13 22:20 开线。起因见 BACKLOG B042：完整的盘后流程只在对方自己的服务上定时运行，我们后加的阶段没有任何东西触发；当晚的榜单和风险分是手工补的。方案写成，送审计。

- 2026-10-13 23:13 审计第 1 轮的十条和改法（方案文件末尾有同样的清单）：新阶段改排在现有五个之后，不会拖慢老师复盘的阶段；回执用我们自己的名字（`peer_` 前缀），不会被阶段表里“跳过也算完成”的旧回执压住；榜单阶段只有当天真的有榜单才算完成，取不到是 partial、下一轮重试；风险分阶段依赖榜单阶段；历史榜单只用于补没有实时榜单的日子，并且每一轮都先补前一个交易日；时限改成 120、120、180、180、120 秒；两个测试文件要跟着改；每轮打印一行没完成的阶段。当晚 22:49 还量了一件事：历史榜单接口当天晚上还没有当天的数据（10 月 13 日返回无数据，10 月 12 日有），所以当晚必须靠实时榜单。

- 2026-10-13 23:30 用户说「codex当前推进太慢了，全部交给你来处理现在，全部推进」。主会话自己实现了本线：`peer_close_research.py` 加五个 `peer_` 阶段、依赖检查、补前一交易日、每轮打印未完成阶段；`limit_up_risk_repository.live_lists_stored`；`main.py` 接线（没有配置授权接口的环境不接这几个阶段）；决策 0021。发布路径测试 3058 项通过，组件边界检查通过。合并 0a57d7fb，主线已推送，`rel06.sh` 的检查模式通过（纯代码、3 个运行时文件）。
- 2026-10-14 00:43 用户执行了发布（`bash ~/codebase/n8n/state/rel06.sh --apply`）：`owner-code-20261013-06` 生效，脚本输出 `code-only release active: owner-code-20261013-06 (overlay source verified against the container)`。
- 2026-10-14 00:45 发布后的只读核对（目标：owner 主机 118.178.133.37 上的 scheduler 容器；隧道 `http://127.0.0.1:15682/health` 报告 `owner-code-20261013-06`、`0a57d7fb`；探针 `docs/work/probes/2026-10-14-peer-evening-receipts.py` 与 `.out.txt`，状态检查 `docs/work/probes/2026-10-14-release-status-06.out.txt`）：
  - 回执：10 月 13 日的 `peer_limit_performance_lists`、`peer_limit_up_risk`、`peer_market_temperature`、`peer_broad_etf_flow`、`peer_market_timing` 都是 completed（00:43:55 到 00:44:35，温度 25 秒、ETF 12 秒，其余 1 秒）；10 月 12 日的两个补做回执也是 completed（00:44:35–00:44:37）。循环在 scheduler 重启后约 30 秒内就开始补跑前一个交易日。
  - 读数：10 月 12、13 日的 `market_temperature_daily`、`broad_etf_flow_daily`、`market_timing_daily` 各 1 份（00:44 写入），`broad_etf_daily` 两天各 12 份；`limit_performance_live` 两天各 5 份（都是之前存的，阶段没有重取）；`limit_up_risk_daily` 10 月 13 日仍是 1 份（与当晚手工补的一致，没有新版本），10 月 12 日变成 2 份：补做阶段重算出来的读数与原来那份不同，所以存了新版本。差在哪里没有查。
  - 日志：scheduler 最近 20 分钟没有“left open”一行。
  - 内存：主机可用 336 MB、交换区已用 448 MB；scheduler 容器 26.77 MiB、主服务 173.3 MiB。发布前 scheduler 的占用没有量过，所以“前后相差不超过约 50 MB”这一条没法按原样核对，只有发布后的绝对值。
  - `release-sync-status.sh --owner-source-sha 0a57d7fb --edge-source-sha 83b5d49e`：ALL CHECKS PASSED。
  - 没核对到的：第一次真正的当晚运行（实时榜单要当天取，这次两天的榜单都已在库里）；16:20 左右算的风险分与 19:00 后重算是否一致。
  - 第一次探针里有一条按 `LIKE` 扫 `raw_market_observations` 的查询超时（25 秒上限），换成精确能力名后正常；探针文件是改后的版本。

## Waiting on the user

- [x] 2026-10-13 23:30 执行发布：`! bash ~/codebase/n8n/state/rel06.sh --apply`（owner 纯代码发布 `owner-code-20261013-06`，从 e8cb8dbf 到 0a57d7fb，3 个运行时文件；主线已推送，检查模式已通过）。为什么等：生产发布只能用户执行（会话的安全检查会拦）。可发的时段：现在到 10 月 14 日 08:30 之前，或 11:30–13:00、15:10–18:45。之后：agent 按 next 核对。

## Running

- [x] 2026-10-13 方案审计第 1 轮 → 10 条异议，主会话逐条对过代码，全部接受，方案改成第 2 版（2026-10-13 23:13）
- [x] 2026-10-13 23:13 Codex 方案审计第 2 轮在跑（同一线程 01a135a3-55a4-74f2-9f1d-86d8338852cb，只读）。结束标志：它交回 SOUND 或编号的异议。之后见 next。 → 5 条，采纳了其中三处并写进代码（补前一交易日放在当天阶段之后；“当天已有实时榜单”用直接查询判断；老师复盘的每日摘要不列这五个阶段），另两条（更早日子的自动补、并发执行的仲裁）按决策 0021 的说明不做（2026-10-13 23:30）

## Due

- [ ] 2026-10-14 16:50 第一次真正的当晚自动运行：核对 10 月 14 日五个 `peer_` 回执和读数；另在 19:00 后干跑一次风险分，与 16:20 左右那份比较。没出来再手工跑（下一条）。
- [ ] 2026-10-14 17:00 （只在自动运行没出结果时）手工补跑：15:00 之后、24:00 之前执行 `bash ~/codebase/n8n/state/lists13.sh`（写 owner 库；会话的安全检查可能拦，拦了就请用户执行）。本线若已在当天发布，就等 16:40 看自动运行的结果，没出来再手工跑。

## Evidence

- 缺口的证据：FACTS F023、F024；`docs/work/probes/2026-10-13-evening-flow.out.txt`、`-2.out.txt`。
- 当晚手工补的脚本：`docs/work/probes/2026-10-13-evening-lists-and-risk.py`（调用的就是那两个阶段的函数）。

## Open questions

- 三个决策 0013 的阶段从没在调度进程里跑过，内存和耗时发布后第一晚量。
- `trade_calendar` 要不要也由我们这一侧跑：没查对方那边的日历同步是否覆盖我们需要的前瞻日历。

## Resume here

方案在 `docs/work/plans/peer-evening-stages.md`，还没审计完、没开工。接手的人先看 BACKLOG B042 和上面的 next。不要为了“先跑起来”去调用 owner 上的完整盘后刷新接口：它会把十几个没跑过的阶段一起跑，其中分钟面板导出在 894 MB 的主机上很可能撑爆内存。
