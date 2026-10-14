# 当前状态

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | released | none | 10 月 14 日 09:26 核对当天的竞价处置表已生成、股票与 10 月 13 日读数里的 67 只对得上（W002）。此后每个交易日 15:00 之后、24:00 之前要手工跑一次当天的榜单和读数（`bash ~/codebase/n8n/state/lists13.sh`，脚本按当天的交易日期取，不用改），直到 BACKLOG B042 的代码修好并发布 | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | Claude Code 会话 4a420907，2026-10-14 | B059 的代码已随 `owner-code-20261014-07` 发布；它的验证要等下一次采集循环在 09:25 之后才启动（或在 09:25–11:30 之间重启一次服务）时看日志行里的 `late_table=`。此外：W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录。 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 把剩下的两件事定下来再改状态：(1) 28 只一直“缺日线”的股票（6 只北交所，其余多半停牌）应归到“未交易”，否则这个阶段永远是 partial；(2) 控制面现在卡在每日指标 5,138/5,560 上，对账循环每 5 分钟调一次补全，后 21 次都是空跑（BACKLOG B043）。先只读查清每日指标的缺口是不是常态，再写修正方案 | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | released | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | 回放已写完（Log 12:35）。下一件是 Due 里 16:50 那条：核对 `peer_tactic_trials` 的回执是 completed、10 月 14 日的总结已存（`/api/v1/market/tactic-trials/summary` 不再是 `reading: null`，`open_days` 约 11）、今天的竞价读数已由晚间阶段建出（来自补录的处置表，`decided_at` 是 11:45 的存储时间）、看板的标签有了；调度容器的内存与现在（128 MiB）比较。然后 10 月 15 日 09:26 核对竞价读数；在浏览器里看一次新页签。全部通过后改 `verified`，回收工作区 `n8n-tactic-trials` 和 `n8n-release-07`。 | 2026-10-14 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | released | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | 发布 B 已生效（Log 12:46）。读 `docs/work/probes/2026-10-14-session-capture-after-08.out.txt`（13:05 的后台核对）：13:00 起主源的分钟文档每分钟一份。之后两件：(1) 从 10 月 15 日起每个交易日收盘后跑当日汇总，输出存 `docs/work/probes/`，数满五个通过的交易日后用 `scripts/tdx-promote.py` 评估是否升级（升级要用户决定）；(2) 第一次真实的主源失败时，核对补的那一分钟记在 `tdx_public` 名下、指标健康里 `fallback_minutes` 为正、信号和纸面执行没有用它。 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在编辑框里改了备注名并保存后，核对飞书里的备注名和会员表（这是经线上 bridge 接口的第一次写入）；其余见 Open questions 和待办 B056 的 (2)、(4)。之后回收剩下的两个工作区和集成分支，做探测 P0-8，状态改 `verified` | 2026-10-13 |
| [W011](streams/W011-peer-evening-stages.md) | 每晚该跑的阶段由我们这一侧自己触发 | released | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | 10 月 14 日 16:50 核对第一次真正的当晚自动运行（见 Due）：用 `docs/work/probes/2026-10-14-peer-evening-receipts.py` 的做法查 10 月 14 日五个 `peer_` 回执和读数（把脚本里的日期改成 10 月 13、14 日另存一份），再看调度容器日志有没有“left open”。通过后把本记录改成 `verified`；连续两晚通过后删掉手工脚本 `state/lists13.sh` 那条 Due。 | 2026-10-14 |
| [W012](streams/W012-owner-hot-releases.md) | owner 只用一套镜像，其余改动全部热部署 | building | Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14 | A 段已合进本地 main（86831925），随 W006 的发布一起到 owner（见 W006 的 Waiting on the user）。接着：(1) owner 主机恢复后（BACKLOG B058），发布之前只读核对主机上 `scripts/backfill-full-market-daily.sh` 和 `scripts/fill-longhu-sector-membership.py` 与目标提交的是否相同（`sha256sum ~/trading_hareness/scripts/<名字>` 对 `git show <sha>:scripts/<名字> \| shasum -a 256`）：这两个脚本的目录登记在本线改成了 `47owner`，分类会把它们报成 `full` 而拒绝纯代码发布。相同就照 `state/rel07.sh` 里已写好的 `RELEASE_RELABELLED_CURRENT` 放行；不同就告诉用户（要么手工把这两个文件拷上主机，要么做一次全量发布，后者要在 894 MB 的主机上构建镜像），并把 `rel07.sh` 里那一行去掉。(2) 第一次用新规则的发布之后，把检查模式和发布的输出存进 `docs/work/probes/`，状态才改 `released`。(3) 回收工作区 `/Users/papa/codebase/.codex-wt/n8n-owner-hot-a`。(4) 之后才是 B 段（Steps 里的 1–4），先为它建分支。 | 2026-10-14 |
| [W014](streams/W014-owner-host-switch.md) | owner 中转主机再换一台：做好切换的准备 | building | Claude Code 会话 4a420907，2026-10-14 | 等用户给出新主机的地址、SSH 端口和主机指纹，以及谁来装它的底子（Waiting on the user）。拿到之后按 `docs/OWNER_HOST_SWITCH.md` 走：先 `scripts/shared-peer/owner-host-switch.sh preflight stockpeer@<新主机>`（只读，会话可以跑；第一次连接要先把指纹加进 `~/.ssh/known_hosts`，这一步由用户做），把输出存进 `docs/work/probes/`；`copy --apply`、新主机上的两件事、切换、三处地址，都是用户执行、会话核对。`copy --apply` 还没有对真实的新主机跑过，第一次跑先看不带 `--apply` 的输出。 | 2026-10-14 |

## 等用户处理

- W004 2026-10-13 请看一下控制台“盘面雷达与战法”页签的新图，配色、数字口径、点的密度是否还要调。为什么等：这是按用户的参考图做的，是否“看得懂”只有用户能判断。之后：没有意见就关闭本记录。
- W010 2026-10-13 在看板上试用七项功能并反馈；特别是会员期限：对真实目标群第一次“同步成员”后，老成员的开始日期是各自的入群日期、期限是默认的 1 个月，
- W010 2026-10-13 擒龙群现在可以发到期提醒了：是否打开“每天自动审查”由用户决定（预览现在是 0 人；10-17、10-19 各有一人到期）。为什么等：打开后机器人会在客户群里 @ 人。
- W010 2026-10-14 在看板上给一位续费的会员改日期并改写备注名、保存，然后看一眼飞书里这个人的备注名是否变了。为什么等：这是经线上接口的第一次真实写入，本会话不能代做。
- W014 2026-10-14 12:12 新主机的地址、SSH 端口、主机指纹；大概什么时候换；它的底子（用户 `stockpeer`、linger、rootless Docker、内存不少于 3 GB、三类公钥）由对方装还是我们装。为什么等：这些只有用户或对方知道；第一次连接新主机和往上面写都要用户执行。之后：会话跑只读预检并把缺的列出来。

## 到点要做

- W006 2026-10-14 11:30 评审修完并合并后（owner 发布窗口：08:30 前、11:30–13:00、15:10–18:45；10 月 14 日 16:15 前发布，当晚就自动跑）：三个单元各自发布，命令交给用户。owner 纯代码（`scripts/release plan <sha>` 先确认仍是纯代码；起点 e8cb8dbf，一次带上 W011）、edge 中继（`research-routes.mjs` 三条路由；W010 的会话也在发中继，发之前看它的记录）、edge 控制台（新页签）。提交要先在 origin/main。
- W006 2026-10-14 13:00 （时间随发布顺延）owner 发布后、回放前：在 owner 容器里干跑一个分块，读 `peak_rss_mb` 和年度报告（先看 `free -m`；只读）：`python -m app.tactic_trial_runtime --slot close --start 2026-09-01 --end 2026-09-28`。峰值超过约 300 MB 就不要跑全量，先把分块再调小。
- W006 2026-10-14 15:10 （时间随发布顺延）干跑通过后：全量回放（写 owner 库，命令交给用户）：先 `--slot close --start 2023-09-01 --end <上一个交易日> --apply`，再 `--slot auction` 同样的区间，不用再手工跑总结：回放之后晚间阶段的下一轮（每 10 分钟一次，到次日 09:00）自己建当天的读数并出总结；`--summarise --apply` 现在只接受最新交易日。不一致率超过 0.5% 回放会自己停下并报告是哪一年。
- W006 2026-10-14 16:50 （16:15 前没发布就顺延一个交易日）发布后的第一个交易日 16:50：核对 `peer_tactic_trials` 的回执是 `completed`、当天的总结已存，各战法当日结果与手算的两三条一致；调度进程的内存与发布前比较。
- W011 2026-10-14 16:50 第一次真正的当晚自动运行：核对 10 月 14 日五个 `peer_` 回执和读数；另在 19:00 后干跑一次风险分，与 16:20 左右那份比较。没出来再手工跑（下一条）。
- W011 2026-10-14 17:00 （只在自动运行没出结果时）手工补跑：15:00 之后、24:00 之前执行 `bash ~/codebase/n8n/state/lists13.sh`（写 owner 库；会话的安全检查可能拦，拦了就请用户执行）。本线若已在当天发布，就等 16:40 看自动运行的结果，没出来再手工跑。
- W006 2026-10-15 09:26 （10 月 14 日 08:30 前发布了就是当天）发布后的第一个交易日 09:26：核对竞价读数已存（`tactic_auction` 的日志行、接口 `/api/research/market/tactic-trials/board`）、与 09:25 处置表同一批股票，页面可见。

## 正在运行

无。

## 待办清单

open 32, ready 16, doing 3, waiting-user 1, done 9, dropped 1

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
- B042 (doing) 我们这一侧新加的盘后阶段没有任何东西定时触发
- B046 (ready) W010 的探测 P0-8：富文本和卡片转发读回的样子
- B048 (ready) 目标群功能里查群主失败时不带飞书的原因
- B049 (ready) W010 评审里没改的小项
- B056 (ready) 看板还调用不到的 LarkAgentX 能力（会员这条线上最常手做的几件）
- B052 (ready) 会员期限：指定到期日；读个人资料里的备注信息
- B050 (doing) owner 上只用一套镜像，以后的发布都是代码覆盖
- B055 (ready) 补取 2026-10-13 当天的指数和 ETF 分钟线
- B059 (doing) 采集循环在 09:25 之后才启动时：不补竞价定格，却把当时的盘口记成 09:30 的
- B060 (ready) edge 到 owner 的隧道在对端挂死时会永远停在握手里
