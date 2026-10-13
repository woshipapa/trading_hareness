# 当前状态

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | verified | none | 10 月 13 日 19:30 之后核对当晚的涨停榜和风险分读数已自动生成（见 Due），通过后把本记录改成 `closed` | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | none | W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 先看 owner 主机是否恢复（BACKLOG B031）；恢复后在 10 月 13 日 19:30 之后核对该阶段的第一次自动运行（见 Due），通过后改成 `verified` | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | building | Claude Code 会话 93ba6e1d（主会话，负责验收与派工），2026-10-13 | 等 Codex 交回第 9 步的修正第 1 部分（Log 里 2026-10-13 那条预审的 A1–A7、B8–B10、C11–C14 和对应测试），在工作区复跑发布路径测试并逐条对照代码核实；通过后用同一线程派修正第 2 部分（竞价回放的样本、`decided_at`、`summarise` 取上一份总结的读法、删掉提前写的第 11–12 步的函数、计时重跑），两部分都通过才勾掉第 9 步、派第 10 步 | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | building | Claude Code 会话 93ba6e1d，2026-10-13 | 两件事。(1) Codex 的研究第 1 步在跑（线程 01a134cc-8eb1-76f0-9aec-6bb66827ce98，休眠切断后 20:20 续上，分支已有提交）：交回后读 `docs/archive/2026-10-13-v-reversal-study/01-notes.md` 并抽查等权线与日线面板的吻合、有没有用到事件时刻之后的数据、深跌日数量与 193 天的差异说明。(2) 取数停在 `510300` 的 2025-03-10（日志末行 PAUSED，4,105 次），续取要等 BACKLOG B041（本机切到新的 owner 服务器）完成，且先看新主机的内存再定节奏（对方说只有 1 GB）；命令不变：`/Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-fetch.py SH000001 SZ399001 SZ399006 SH000852 SH000300 510300 512100 510050 510500 159915 588000` | 2026-10-13 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | reviewing | Claude Code 会话 93ba6e1d（主会话，派工与验收），2026-10-13 | 等 Codex 实现者交回评审第 1 轮的修正（线程 01a1349e-01a9-7172-9d3f-d63b744c9bcb；四条见 Log），在工作区跑验收（发布路径测试、`pytest scripts -q`、`git diff --check 26170231 HEAD`）并读 diff，再送同一评审者线程 01a134c8-e142-7ba2-b4a0-7f41b86c2562 复审（最多三轮）；通过后 `--no-ff` 合并进 main、回收工作区，再开 `tdx-live-snapshot-fallback` 派工 6。注意：修正给当日汇总加了两个字段，改的是 owner 运行时文件，随下一次 owner 发布带上；发布目标已换服务器，见 BACKLOG B041 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | building | Claude Code 会话 eac0e1fc，2026-10-13 | 收齐五条功能线的看板一步（create、members、urgent、recall、membership；join 已返回 ae4f174f，评审者已派）。每条线：复跑验收（含看板）→ Codex 评审者（只读，新线程）→ 修 → edge 上跑 bridge 套件 → `--no-ff` 合进集成分支 → 集成分支全验收 → 合进 main → 热更新 → 线上核对 → 告诉用户可以试什么。顺序 join → create → members → membership → urgent → recall | 2026-10-13 |

## 等用户处理

- W004 2026-10-13 请看一下控制台“盘面雷达与战法”页签的新图，配色、数字口径、点的密度是否还要调。为什么等：这是按用户的参考图做的，是否“看得懂”只有用户能判断。之后：没有意见就关闭本记录。

## 到点要做

- W001 2026-10-13 19:30 核对当晚盘后自动生成的涨停榜和风险分读数（明早 09:15 的竞价池从 10 月 13 日的风险分读数取昨日涨停股）：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/daily'` 返回的 `reading.trade_date` 应是 2026-10-13。没有的话看盘后流程是否运行，以及 owner 主机是否正常（BACKLOG B031）。 2026-10-13 20:30 没有做成：本机的 owner 读路径不通（对方迁了服务器，见 BACKLOG B041）；经 edge 的那条 `curl` 也要等 edge 到新主机的连接确认。B041 连通后做；明早 09:15 的竞价池依赖这份读数。
- W003 2026-10-13 19:30 核对该阶段第一次自动运行（盘后流程 18:45 才开始，早查只会看到“没有收据”）：读 `fetch_runs` 里能力 `daily_close_completion` 的回执（状态、各分类计数、剩下的股票和原因）；抽查补上的日线有没有涨跌停价和因子；健康检查 `data.daily_bars_complete`（`curl http://127.0.0.1:18300/api/research/indicators/health`）。阶段没跑或 `blocked`：查原因；当天缺口可用本机 `state/research/owner_repairs_20261012/` 里验证过的脚本手工补（写库由用户执行）。 2026-10-13 18:30 补充：主会话排了一条后台命令，19:30 用 `docs/work/probes/2026-10-13-post-close-stages.py` 读当晚各阶段的回执，存到 `docs/work/probes/2026-10-13-post-close-stages.1930.out.txt`（这个探针直接读 owner 读路径的 `automation/runs`，可以随时手工重跑）。18:15 那次已看到：当天 16:02 跑过一轮，其中 `adjustment_factors` 失败、没有 `daily_close_completion` 的回执；要看 18:45 这一轮。 2026-10-13 20:30 没有做成：本机 18:34–20:19 合盖休眠，挂在主会话上的 Codex 调用和后台命令都被切断；醒来后发现对方当天把量化服务迁到了新服务器，本机的 owner 读路径还指着旧主机，不通（BACKLOG B041）。旧主机的 Docker 18:37:15 就停了，早于 18:45 的盘后流程，所以当晚这一轮只可能是在新主机上跑的，跑没跑、结果如何都还不知道。B041 连通后先做这条。
- W006 2026-10-13 22:06 owner 发布窗口重开（今天 15:10–18:45 也开着，但 16 步做不完）。若 16 步、双重评审和合并都已完成：备好包装脚本交给用户执行，发布后先做历史回放的干跑。没完成就顺延到 10 月 14 日 11:30–13:00 或 15:10–18:45。本线要发三个单元：owner 纯代码（`quant-service/app/tactic_*.py` 等在纯代码白名单里）、edge 中继（`research-routes.mjs` 新增三条路由）、edge 控制台（新页签）；owner 发布要求提交已在 origin/main，所以包装脚本里先 `git push`。
- W008 2026-10-14 09:16 发布后第一个交易时段：核对两条采集循环按新节拍落库。只读查 owner（探针写法见 `docs/work/README.md`）：`quant.raw_market_observations` 里当天 `fuyao_ths` / `a_share_minute_cross_section` 和 `tdx_public` / `a_share_minute_cross_section_shadow` 的 `effective_at`，09:15 起每分钟各一份、秒数稳定（主源在第 50 秒之后几秒、通达信在第 52 秒之后），没有漏分钟。异常（没有文档、或漏得比原来多）立刻告诉用户：回滚命令是把 `owner-code-20261013-03` 重新发布，但 08:30–11:30 是不能发布的窗口，要用户决定是否破例。收盘后跑当日汇总看可判分钟数和是否达标，记为 5 个交易日观察的第 1 天。 2026-10-13 20:30 补充：对方当天把量化服务迁到了新服务器（BACKLOG B041）。这条核对要走新的 owner 读路径；探针的调用行用的是 `.env.local` 里的 `LONGHU_*`，B041 完成后自然指向新主机。新主机只有 1 GB 内存：在容器里跑的探针只查计数和时间，不要读文档正文。
- W006 2026-10-14 09:26 （10 月 13 日晚没发布的话，这条和下一条顺延到发布后的第一个交易日）发布后的第一个交易日：核对竞价看板读数已存、与 09:25 处置表同一批股票，页面可见。
- W006 2026-10-14 19:00 第一份每日总结：核对生成、各战法当日结果与手算的两三条一致。

## 正在运行

- W006 2026-10-13 18:18 Codex 实现者在做第 9 步的修正第 1 部分（同一线程 01a1339e-3f02-7072-ba13-2d122b927799，工作区 `/Users/papa/codebase/.codex-wt/n8n-tactic-trials`）。结束标志：分支在 2673fd39 之后有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上；主会话结束它就停，判断死活和续派的办法见 Resume here。 20:30 补充：18:34 本机休眠把调用切断，当时已有两个提交（88dc8851 仓储的窗口和涨跌停价、7bc74226 收盘回放和结算路径）；20:20 用 `codex-reply` 续上，让它先逐条核对哪些已做、再补完其余和缺的测试。续派的调用立刻返回空内容但线程在跑，用 `docs/work/probes/2026-10-13-wait-codex.py 01a1339e` 等它结束（主会话的后台命令）。
- W007 2026-10-13 18:28 Codex 在做研究第 1 步，线程 01a134cc-8eb1-76f0-9aec-6bb66827ce98，harvest 工作区 `/Users/papa/codebase/.codex-wt/n8n-v-reversal-study`，分支 `codex/v-reversal-study`，基点 2703af84。产出：`docs/archive/2026-10-13-v-reversal-study/`（脚本、输出、`01-notes.md`、事件表 CSV），派生表在本机 `state/research/vshape_20261013/derived/`。结束标志：分支有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上；主会话结束它就停，判断死活的办法见 `docs/work/README.md`，续派用 `codex-reply` 接同一线程（线程号用 `codex-trace.py --list` 按工作区找）。 20:30 补充：18:34 本机休眠把调用切断（当时没有提交），20:20 用 `codex-reply` 续上；续派的调用立刻返回空内容但线程在跑，所以用 `docs/work/probes/2026-10-13-wait-codex.py 01a134cc` 等它结束（主会话的后台命令）。
- W010 2026-10-13 20:05 六条功能线的看板一步并行在跑（各用自己的实现线程续派；工作区都已合入集成分支 0e20e6f0）：
- W008 2026-10-13 20:30 Codex 实现者在修评审第 1 轮的四条（线程 01a1349e-01a9-7172-9d3f-d63b744c9bcb，工作区 `/Users/papa/codebase/.codex-wt/n8n-tdx-promote-gate`）。结束标志：分支在 61f09555 之后有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上。

## 待办清单

open 26, ready 9, waiting-user 1, done 4, dropped 1

在做的、可以开工的、等用户处理的:

- B002 (ready) 2023 年的复权因子补齐
- B003 (ready) 2023 年缺的涨跌停价补齐
- B006 (ready) 分四档的资金流在 2026-08-20 之后的缺口
- B009 (ready) 仓库涨跌停规则的三处缺陷和 9 月 30 日的错值
- B010 (ready) 日线表里周末日期的行
- B019 (ready) 老师复盘包导入时竞价金额的单位校验
- B033 (ready) 通达信并行快照和比对行没有列入冷热分层
- B023 (ready) 全市场分钟快照 09:16 才开始
- B024 (ready) 竞价处置表缺的那 1 只没查明
- B041 (waiting-user) owner 量化服务迁到新服务器：本机的连接、隧道和发布目标要切过去
