# 当前状态

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | verified | none | “回看接口把未来交易日算进最近 N 天”的修复（7bb29e71，见 BACKLOG B011）随下一次 owner 发布上线后，关闭本记录 | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | none | W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 先看 owner 主机是否恢复（BACKLOG B031）；恢复后在 10 月 13 日 19:30 之后核对该阶段的第一次自动运行（见 Due），通过后改成 `verified` | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | building | Claude Code 会话 93ba6e1d（主会话，负责验收与派工），2026-10-13 | 等 Codex 交回“卖出规则改名＋第 6 步”（`tactic_trials.py` 上半：战法、组合逻辑、选股、格内当日结果），复跑发布路径测试，核对二十个组合的定义与方案的表一致，通过后派第 7 步（战绩、标签、两两组合、每日总结） | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | planned | Claude Code 会话 93ba6e1d，2026-10-13 | 等 owner 恢复、B031 查明原因、用户同意取数（见 Waiting on the user）后，在非交易时段跑 `/Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-fetch.py SH000001 SZ399001 SZ399006 SH000852 SH000300 510300 512100 510050 510500 159915 588000`（一条命令顺序取 11 个代码，带参数就不存雷达；可断点续取），另用 `curl` 把全天雷达存成 `state/research/vshape_20261013/radar/radar_2026-10-13.full.json`（13:27 那份 `radar_2026-10-13.json` 是早先输出的依据，留着）；`fetch.log` 出现 FINISHED 后核对 11 个文件各约 750 行，写研究登记，派 Codex | 2026-10-13 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | building | Claude Code 会话 93ba6e1d（主会话，派工与验收），2026-10-13 | 等 Codex 交回派工 1（方案的 P1：两条采集循环固定在每分钟的某一秒启动），在工作区跑方案 Acceptance 一节的命令并读 diff，通过后用同一线程派工 2（P2 和 P3） | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | approved | Claude Code 会话 eac0e1fc，2026-10-13 | 读 Codex 方案审查的结论并逐条裁决（改方案或写明不采纳的理由）；同时做线上探测 P0-1 至 P0-7，把结果写成 `feishu-relay/bridge/WEB_GATEWAY.md`；然后把审查后的改动给用户看，得到同意后建集成分支、派 base 线 | 2026-10-13 |

## 等用户处理

- W003 2026-10-13 请从云控制台看 owner 主机（47.110.79.189）的 CPU、内存和带宽监控，并决定是否重启服务或主机。为什么等：15:07 起它不响应，agent 连不上去就看不到原因，重启只能由用户做。之后：用户说明看到了什么，agent 据此排查并核对今晚的盘后流程（BACKLOG B031）。
- W004 2026-10-13 请看一下控制台“盘面雷达与战法”页签的新图，配色、数字口径、点的密度是否还要调。为什么等：这是按用户的参考图做的，是否“看得懂”只有用户能判断。之后：没有意见就关闭本记录。
- W007 2026-10-13 owner 恢复后，是否同意跑指数和宽基 ETF 的历史分钟线取数：11 个代码、2023-09-01 起每个交易日、共约 8,250 次只读请求，经授权数据网关，非交易时段跑。为什么等：超过 5,000 次的批量读取按 README 要用户明确同意；此前两次只是告知（“不同意请说”），用户没有反对，并在之后要求把假设放到历史上验证，但没有明确说同意。之后：按 `next` 取数。
- W008 2026-10-13 是否同意在发布分类器里把 `scripts/tdx-promote.py` 列为“只在工作站运行”的工具（改的是 `scripts/shared-peer/classify-owner-paths.sh` 的规则）？为什么等：这是发布安全规则的改动，方案要求用户明确同意。同意：派工 5 带上这条规则，两次发布都是纯代码发布。不同意：派工 4–5 的分支等派工 6–10 的分支一起合并，发布 B 走全量发布。派工 5 开始前需要答案；派工 1–4 不受影响。

## 到点要做

- W003 2026-10-13 15:20 先确认 owner 主机是否恢复（BACKLOG B031 写了现象和检查命令）。没恢复就提醒用户看云控制台；恢复后先读负载和内存、弄清原因，再做下面 18:45 那条。
- W008 2026-10-13 16:00 重跑 `docs/work/probes/2026-10-13-tdx-tolerance.py` 取 10 月 13 日全天的表，输出覆盖写到 `docs/work/probes/2026-10-13-tdx-tolerance.out.txt`（现在那份只到 14:17），把方案“The in-session agreement rule”里的表、15 个合格分钟的计数和 FACTS F016 对到新输出上；规则的取值如果因此站不住（15 秒以内出现不到 95% 的分钟），在方案和本记录里写明并告诉用户。前提：BACKLOG B031 已关闭（owner 恢复应答），并且接手 W003 和 B031 的会话或用户说了可以再查 owner；否则不要跑。调用方式见 Evidence。脚本里的日期是写死的 10 月 13 日，数据留在库里，哪天跑都行。
- W001 2026-10-13 19:30 核对当晚盘后自动生成的涨停榜和风险分读数（明早 09:15 的竞价池从 10 月 13 日的风险分读数取昨日涨停股）：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/daily'` 返回的 `reading.trade_date` 应是 2026-10-13。没有的话看盘后流程是否运行，以及 owner 主机是否正常（BACKLOG B031）。
- W003 2026-10-13 19:30 核对该阶段第一次自动运行（盘后流程 18:45 才开始，早查只会看到“没有收据”）：读 `fetch_runs` 里能力 `daily_close_completion` 的回执（状态、各分类计数、剩下的股票和原因）；抽查补上的日线有没有涨跌停价和因子；健康检查 `data.daily_bars_complete`（`curl http://127.0.0.1:18300/api/research/indicators/health`）。阶段没跑或 `blocked`：查原因；当天缺口可用本机 `state/research/owner_repairs_20261012/` 里验证过的脚本手工补（写库由用户执行）。
- W006 2026-10-13 22:06 owner 发布窗口重开（今天 15:10–18:45 也开着，但 16 步做不完）。若 16 步、双重评审和合并都已完成：备好包装脚本交给用户执行，发布后先做历史回放的干跑。没完成就顺延到 10 月 14 日 11:30–13:00 或 15:10–18:45。本线要发三个单元：owner 纯代码（`quant-service/app/tactic_*.py` 等在纯代码白名单里）、edge 中继（`research-routes.mjs` 新增三条路由）、edge 控制台（新页签）；owner 发布要求提交已在 origin/main，所以包装脚本里先 `git push`。
- W006 2026-10-14 09:26 （10 月 13 日晚没发布的话，这条和下一条顺延到发布后的第一个交易日）发布后的第一个交易日：核对竞价看板读数已存、与 09:25 处置表同一批股票，页面可见。
- W006 2026-10-14 19:00 第一份每日总结：核对生成、各战法当日结果与手算的两三条一致。

## 正在运行

- W006 2026-10-13 14:58 Codex 实现者在做“卖出规则改名＋第 6 步”（同一线程 01a1339e-3f02-7072-ba13-2d122b927799，工作区 `/Users/papa/codebase/.codex-wt/n8n-tactic-trials`）。结束标志：分支在 0ddc86b3 之后有两个提交且 `git status --short` 为空。之后：复跑验收；核对 `TACTICS` 二十个组合的定义；勾掉 Steps；派第 7 步。该任务挂在主会话上，主会话结束它就停：接手者先看分支和工作区，再用新的 Codex 调用从最后一个完成的步骤接着派。
- W008 2026-10-13 15:27 Codex 实现者在做派工 1（方案的 P1），工作区 `/Users/papa/codebase/.codex-wt/n8n-tdx-shadow-measure`，分支 `codex/tdx-shadow-measure`，基点 9d75b5d9；这是一个新的 Codex 线程（线程号在它交回时才知道，交回后记进 Log）。结束标志：分支在 9d75b5d9 之后有提交且 `git status --short` 为空。之后：在工作区跑方案 Acceptance 一节的命令、读 diff、在 Steps 里勾掉 P1，再派工 2（P2 和 P3）。它挂在主会话 93ba6e1d 上，主会话结束它就停：新会话先看分支和工作区；工作区不干净且文件十分钟内没再变，就是中断了，用新的 Codex 调用“按方案从派工 1 没做完的地方继续”，不要自己写。

## 待办清单

open 19, ready 9, doing 1, done 2, dropped 1

在做的、可以开工的、等用户处理的:

- B002 (ready) 2023 年的复权因子补齐
- B003 (ready) 2023 年缺的涨跌停价补齐
- B006 (ready) 分四档的资金流在 2026-08-20 之后的缺口
- B009 (ready) 仓库涨跌停规则的三处缺陷和 9 月 30 日的错值
- B010 (ready) 日线表里周末日期的行
- B011 (ready) 回看接口日历上界的修复待发布
- B019 (ready) 老师复盘包导入时竞价金额的单位校验
- B031 (doing) owner 主机 2026-10-13 收盘后不响应
- B023 (ready) 全市场分钟快照 09:16 才开始
- B024 (ready) 竞价处置表缺的那 1 只没查明
