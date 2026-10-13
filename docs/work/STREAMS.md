# 工作线索引

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | released | none | 用户执行 `! bash ~/codebase/n8n/state/lists13.sh`（今晚 24:00 前）之后，agent 只读核对：`curl -s http://127.0.0.1:15682/api/v1/market/limit-up-risk/daily` 的 `reading.trade_date` 是 2026-10-13、行数与当天榜单相符（试跑时 5 张榜共 71 行）。此后每个交易日晚上都要这样手工跑一次，直到 BACKLOG B042 的代码修好 | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | none | W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 把剩下的两件事定下来再改状态：(1) 28 只一直“缺日线”的股票（6 只北交所，其余多半停牌）应归到“未交易”，否则这个阶段永远是 partial；(2) 控制面现在卡在每日指标 5,138/5,560 上，对账循环每 5 分钟调一次补全，后 21 次都是空跑（BACKLOG B043）。先只读查清每日指标的缺口是不是常态，再写修正方案 | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | building | Claude Code 会话 93ba6e1d（主会话，负责验收与派工），2026-10-13 | 等 Codex 交回第 9 步修正的第 2 部分（线程 01a1339e-3f02-7072-ba13-2d122b927799；十条见 Log），重点看它量出来的两个内存峰值（`summarise` 和收盘回放，760 天 × 100 行）。然后先改方案再往下做：(a) 每晚的运行不能靠“加一个盘后阶段”，那条路在我们这一侧不会自己跑（BACKLOG B042），要并入 peer 自己的晚间循环；(b) 新 owner 主机只有 894 MB 内存，`summarise` 每晚把全部历史读进内存重算的设计要按量出来的数字改（例如每个选股日结算完成后存一份紧凑的当日结果，总结只读这些）。方案改完给用户过目，再派第 10 步 | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | building | Claude Code 会话 93ba6e1d，2026-10-13 | 两件事。(1) 等 Codex 交回第 1 步的修正（线程 01a134cc-8eb1-76f0-9aec-6bb66827ce98）：跨日结果的算法要改成复利（见 Log），并加一条能查出这个错的核对；交回后重读 `01-notes.md` 里 7 月 9 日那一行（到次日开盘应约 +2.2%）和新核对的输出，通过后等基金文件取齐再派第 2 步。(2) 取数停在 `510300` 的 2025-03-10（4,105 次）。本机的 owner 读路径已指向新服务器（BACKLOG B041），续取前看一眼新主机内存（`free -m` 可用不到 150 MB 就不取），节奏不变（每次请求后停 0.3 秒），避开交易时段和对方可能在做的事；命令：`/Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-fetch.py SH000001 SZ399001 SZ399006 SH000852 SH000300 510300 512100 510050 510500 159915 588000 --until=HH:MM` | 2026-10-13 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | merged | Claude Code 会话 93ba6e1d（主会话，派工与验收），2026-10-13 | 两件事。(1) 准备第二次 owner 纯代码发布（从 26170231 到主线当前提交；`scripts/release plan` 判为纯代码、2 个运行时文件：目录和通达信并行采集的当日汇总），窗口是 10 月 13 日 22:05 之后或 10 月 14 日 11:30–13:00、15:10–18:45；这是迁到新服务器后的第一次发布，脚本的默认目标已是新主机（提交 4bb8c7ff），包装脚本备好后交给用户执行。(2) 从 main 建 `tdx-live-snapshot-fallback` 工作区派工 6（B1 的读取链）。先做 (1) 还是先攒着和 B1、B2 一起发，问用户；明早 09:16 的核对（见 Due）不依赖这次发布 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | building | Claude Code 会话 eac0e1fc，2026-10-13 | 收 members 线的补丁（成员列表加 `is_bot`、`is_self`，线程 01a1348e）、urgent 线的四条修正（线程 01a13490）、membership 线的五条修正（线程 01a13494）。members 补丁合并后，给 membership 线续派：合入集成分支、成员同步跳过 `is_bot` 和 `is_self`。然后 urgent、membership 依次：复跑验收 → edge 上 bridge 套件 → 合进集成分支 → main → 热更新 → 线上核对。最后做探测 P0-8、回收八个工作区、汇总 token 开销 | 2026-10-13 |

## 已结束、暂停或放弃

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W005](streams/W005-short-term-tactics-research.md) | 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0） | closed | none | none | 2026-10-13 |
