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
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | building | Claude Code 会话 93ba6e1d，2026-10-13 | 两件事并行。(1) 等 Codex 交回研究第 1 步（数据核对、等权线、K 线与指标、两张事件表、深跌日的描述；只用五个指数文件），读它的 `01-notes.md` 并抽查：等权线与日线面板的吻合、有没有用到事件时刻之后的数据、深跌日的数量与日线定义的 193 天的差异说明；通过后等基金文件取齐再派第 2 步。(2) 取数 18:35 自己暂停，盘后流程结束后（10 月 13 日 20:30 以后，或次日非交易时段）重跑同一条命令续取余下的 ETF 并补 `SZ399001` 缺的 7 天：`/Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-fetch.py SH000001 SZ399001 SZ399006 SH000852 SH000300 510300 512100 510050 510500 159915 588000`（要限时就加 `--until=HH:MM`） | 2026-10-13 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | reviewing | Claude Code 会话 93ba6e1d（主会话，派工与验收），2026-10-13 | 等 Codex 评审者（只读，新线程，交回时才知道线程号）对 `codex/tdx-promote-gate` 的结论；主会话自己的两条小发现见 Log。有高优先级发现就核实后回派实现者线程 01a1349e-01a9-7172-9d3f-d63b744c9bcb（最多三轮）；通过后 `--no-ff` 合并进 main、回收工作区，再从 main 建 `tdx-live-snapshot-fallback` 工作区派工 6。这个分支不需要单独发布：它只改目录里的比对声明、升级工具和发布分类规则，随下一次 owner 纯代码发布带上 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | building | Claude Code 会话 eac0e1fc，2026-10-13 | 收齐五条功能线的看板一步（create、members、urgent、recall、membership；join 已返回 ae4f174f，评审者已派）。每条线：复跑验收（含看板）→ Codex 评审者（只读，新线程）→ 修 → edge 上跑 bridge 套件 → `--no-ff` 合进集成分支 → 集成分支全验收 → 合进 main → 热更新 → 线上核对 → 告诉用户可以试什么。顺序 join → create → members → membership → urgent → recall | 2026-10-13 |

## 等用户处理

- W004 2026-10-13 请看一下控制台“盘面雷达与战法”页签的新图，配色、数字口径、点的密度是否还要调。为什么等：这是按用户的参考图做的，是否“看得懂”只有用户能判断。之后：没有意见就关闭本记录。

## 到点要做

- W001 2026-10-13 19:30 核对当晚盘后自动生成的涨停榜和风险分读数（明早 09:15 的竞价池从 10 月 13 日的风险分读数取昨日涨停股）：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/daily'` 返回的 `reading.trade_date` 应是 2026-10-13。没有的话看盘后流程是否运行，以及 owner 主机是否正常（BACKLOG B031）。
- W003 2026-10-13 19:30 核对该阶段第一次自动运行（盘后流程 18:45 才开始，早查只会看到“没有收据”）：读 `fetch_runs` 里能力 `daily_close_completion` 的回执（状态、各分类计数、剩下的股票和原因）；抽查补上的日线有没有涨跌停价和因子；健康检查 `data.daily_bars_complete`（`curl http://127.0.0.1:18300/api/research/indicators/health`）。阶段没跑或 `blocked`：查原因；当天缺口可用本机 `state/research/owner_repairs_20261012/` 里验证过的脚本手工补（写库由用户执行）。
- W006 2026-10-13 22:06 owner 发布窗口重开（今天 15:10–18:45 也开着，但 16 步做不完）。若 16 步、双重评审和合并都已完成：备好包装脚本交给用户执行，发布后先做历史回放的干跑。没完成就顺延到 10 月 14 日 11:30–13:00 或 15:10–18:45。本线要发三个单元：owner 纯代码（`quant-service/app/tactic_*.py` 等在纯代码白名单里）、edge 中继（`research-routes.mjs` 新增三条路由）、edge 控制台（新页签）；owner 发布要求提交已在 origin/main，所以包装脚本里先 `git push`。
- W008 2026-10-14 09:16 发布后第一个交易时段：核对两条采集循环按新节拍落库。只读查 owner（探针写法见 `docs/work/README.md`）：`quant.raw_market_observations` 里当天 `fuyao_ths` / `a_share_minute_cross_section` 和 `tdx_public` / `a_share_minute_cross_section_shadow` 的 `effective_at`，09:15 起每分钟各一份、秒数稳定（主源在第 50 秒之后几秒、通达信在第 52 秒之后），没有漏分钟。异常（没有文档、或漏得比原来多）立刻告诉用户：回滚命令是把 `owner-code-20261013-03` 重新发布，但 08:30–11:30 是不能发布的窗口，要用户决定是否破例。收盘后跑当日汇总看可判分钟数和是否达标，记为 5 个交易日观察的第 1 天。
- W006 2026-10-14 09:26 （10 月 13 日晚没发布的话，这条和下一条顺延到发布后的第一个交易日）发布后的第一个交易日：核对竞价看板读数已存、与 09:25 处置表同一批股票，页面可见。
- W006 2026-10-14 19:00 第一份每日总结：核对生成、各战法当日结果与手算的两三条一致。

## 正在运行

- W007 2026-10-13 17:30 取数脚本在跑（主会话的后台命令）：11 个代码顺序取，每次请求后停 0.3 秒，带 `--until=18:35`，到点自己停并在日志里写 PAUSED（避开 18:45 开始的盘后流程）。日志 `state/research/vshape_20261013/data/fetch.log`，数据 `<代码>.jsonl`。结束标志：日志末行 FINISHED 或 PAUSED；ABORT 是连续 20 次请求失败。挂在主会话 93ba6e1d 上；主会话结束它就停，重跑同一条命令即续取。
- W006 2026-10-13 18:18 Codex 实现者在做第 9 步的修正第 1 部分（同一线程 01a1339e-3f02-7072-ba13-2d122b927799，工作区 `/Users/papa/codebase/.codex-wt/n8n-tactic-trials`）。结束标志：分支在 2673fd39 之后有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上；主会话结束它就停，判断死活和续派的办法见 Resume here。
- W008 2026-10-13 18:24 Codex 评审者（只读）在评审 `codex/tdx-promote-gate`（`git diff 26170231...HEAD`，4 个提交、7 个文件），工作区 `/Users/papa/codebase/.codex-wt/n8n-tdx-promote-gate`。结束标志：它交回 APPROVED / WARNING / BLOCKED。之后见 next。挂在主会话 93ba6e1d 上；主会话不在了就重新发起一次只读评审（提示词要点在 Log）。
- W007 2026-10-13 18:28 Codex 在做研究第 1 步，新线程（交回时才知道线程号），harvest 工作区 `/Users/papa/codebase/.codex-wt/n8n-v-reversal-study`，分支 `codex/v-reversal-study`，基点 2703af84。产出：`docs/archive/2026-10-13-v-reversal-study/`（脚本、输出、`01-notes.md`、事件表 CSV），派生表在本机 `state/research/vshape_20261013/derived/`。结束标志：分支有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上；主会话结束它就停，判断死活的办法见 `docs/work/README.md`，续派用 `codex-reply` 接同一线程（线程号用 `codex-trace.py --list` 按工作区找）。
- W010 2026-10-13 20:05 六条功能线的看板一步并行在跑（各用自己的实现线程续派；工作区都已合入集成分支 0e20e6f0）：

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
