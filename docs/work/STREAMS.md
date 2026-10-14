# 工作线索引

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | released | none | 10 月 14 日 09:26 核对当天的竞价处置表已生成、股票与 10 月 13 日读数里的 67 只对得上（W002）。此后每个交易日 15:00 之后、24:00 之前要手工跑一次当天的榜单和读数（`bash ~/codebase/n8n/state/lists13.sh`，脚本按当天的交易日期取，不用改），直到 BACKLOG B042 的代码修好并发布 | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | Claude Code 会话 4a420907，2026-10-14 | B059 的代码已随 `owner-code-20261014-07` 发布；它的验证要等下一次采集循环在 09:25 之后才启动（或在 09:25–11:30 之间重启一次服务）时看日志行里的 `late_table=`。此外：W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录。 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 把剩下的两件事定下来再改状态：(1) 28 只一直“缺日线”的股票（6 只北交所，其余多半停牌）应归到“未交易”，否则这个阶段永远是 partial；(2) 控制面现在卡在每日指标 5,138/5,560 上，对账循环每 5 分钟调一次补全，后 21 次都是空跑（BACKLOG B043）。先只读查清每日指标的缺口是不是常态，再写修正方案 | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | released | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | 三个单元都已发布。等全量回放跑完（Running），然后只读核对：`tactic_trials_close_daily`、`tactic_trials_auction_daily` 和两类摘要在库里的天数、`/api/v1/market/tactic-trials/board` 有前一交易日的收盘选股。之后按 Due：16:50 核对 `peer_tactic_trials` 的回执是 completed、当天总结已存、今天的竞价读数（来自补录的处置表）已建；10 月 15 日 09:26 核对竞价读数。在浏览器里看一次新页签（还没看）。全部通过后状态改 `verified`，回收工作区 `/Users/papa/codebase/.codex-wt/n8n-tactic-trials` 和 `n8n-release-07`。 | 2026-10-14 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | released | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | 等独立评审返回（Running）。逐条对代码核实，修掉高和中的（尤其是“缺键按可用”那一条），复跑 `COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path` 和 `python -m pytest scripts -q`，把 main 合进分支再 `--no-ff` 合回 main，回收工作区 `/Users/papa/codebase/.codex-wt/n8n-tdx-fallback`（先删 `frontend/node_modules` 符号链接）。发布 B 不随合并立刻做：它要等发布 A 之后五个完整交易日的当日汇总（从 10 月 15 日数起，每天收盘后跑一次并把输出存进 `docs/work/probes/`）。 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在编辑框里改了备注名并保存后，核对飞书里的备注名和会员表（这是经线上 bridge 接口的第一次写入）；其余见 Open questions 和待办 B056 的 (2)、(4)。之后回收剩下的两个工作区和集成分支，做探测 P0-8，状态改 `verified` | 2026-10-13 |
| [W011](streams/W011-peer-evening-stages.md) | 每晚该跑的阶段由我们这一侧自己触发 | released | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | 10 月 14 日 16:50 核对第一次真正的当晚自动运行（见 Due）：用 `docs/work/probes/2026-10-14-peer-evening-receipts.py` 的做法查 10 月 14 日五个 `peer_` 回执和读数（把脚本里的日期改成 10 月 13、14 日另存一份），再看调度容器日志有没有“left open”。通过后把本记录改成 `verified`；连续两晚通过后删掉手工脚本 `state/lists13.sh` 那条 Due。 | 2026-10-14 |
| [W012](streams/W012-owner-hot-releases.md) | owner 只用一套镜像，其余改动全部热部署 | building | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | A 段已合进本地 main（86831925），随 W006 的发布一起到 owner（见 W006 的 Waiting on the user）。接着：(1) owner 主机恢复后（BACKLOG B058），发布之前只读核对主机上 `scripts/backfill-full-market-daily.sh` 和 `scripts/fill-longhu-sector-membership.py` 与目标提交的是否相同（`sha256sum ~/trading_hareness/scripts/<名字>` 对 `git show <sha>:scripts/<名字> \| shasum -a 256`）：这两个脚本的目录登记在本线改成了 `47owner`，分类会把它们报成 `full` 而拒绝纯代码发布。相同就照 `state/rel07.sh` 里已写好的 `RELEASE_RELABELLED_CURRENT` 放行；不同就告诉用户（要么手工把这两个文件拷上主机，要么做一次全量发布，后者要在 894 MB 的主机上构建镜像），并把 `rel07.sh` 里那一行去掉。(2) 第一次用新规则的发布之后，把检查模式和发布的输出存进 `docs/work/probes/`，状态才改 `released`。(3) 回收工作区 `/Users/papa/codebase/.codex-wt/n8n-owner-hot-a`。(4) 之后才是 B 段（Steps 里的 1–4），先为它建分支。 | 2026-10-14 |

## 已结束、暂停或放弃

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W005](streams/W005-short-term-tactics-research.md) | 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0） | closed | none | none | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | closed | none | 没有了：研究做完，报告在 `docs/archive/2026-10-13-v-reversal-study.md`。留下的三件事在 BACKLOG：B053（把两个时期方向一致的八个条件登记进战法试验台做盘中条件）、B054（“多数股票守住而权重创新低”换一种事件定义再检验）、B055（10 月 13 日当天的分钟线次日补取） | 2026-10-13 |
| [W013](streams/W013-licensed-batch-fetch.md) | 授权接口的批量取数：一个工具、一条并发规矩 | closed | Claude Code 会话 93ba6e1d（主会话），2026-10-14 | 无。以后批量读授权接口都用 `scripts/licensed_minute_history_fetch.py`；规矩在 `AGENTS.md` “Time and data rules” 和 `docs/work/README.md`。 | 2026-10-14 |
