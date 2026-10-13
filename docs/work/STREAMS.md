# 工作线索引

由笔记检查从各条记录生成，不要手改：改记录后用 --write 重新生成。

## 进行中的工作线

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W001](streams/W001-limit-up-risk-score.md) | 涨停股次日大面风险分 | released | none | 10 月 14 日 09:26 核对当天的竞价处置表已生成、股票与 10 月 13 日读数里的 67 只对得上（W002）。此后每个交易日 15:00 之后、24:00 之前要手工跑一次当天的榜单和读数（`bash ~/codebase/n8n/state/lists13.sh`，脚本按当天的交易日期取，不用改），直到 BACKLOG B042 的代码修好并发布 | 2026-10-13 |
| [W002](streams/W002-auction-capture-and-desk.md) | 竞价池采集与 09:25 处置表 | verified | none | W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录 | 2026-10-13 |
| [W003](streams/W003-daily-close-completion.md) | 日线收盘补全 | released | Claude Code 会话 93ba6e1d，2026-10-13 | 把剩下的两件事定下来再改状态：(1) 28 只一直“缺日线”的股票（6 只北交所，其余多半停牌）应归到“未交易”，否则这个阶段永远是 partial；(2) 控制面现在卡在每日指标 5,138/5,560 上，对账循环每 5 分钟调一次补全，后 21 次都是空跑（BACKLOG B043）。先只读查清每日指标的缺口是不是常态，再写修正方案 | 2026-10-13 |
| [W004](streams/W004-radar-chart-redesign.md) | 盘面雷达“主动买/卖·主力净额”图重画 | verified | none | 用户看过页面、没有要调的地方就关闭；有反馈就按反馈改 | 2026-10-13 |
| [W006](streams/W006-tactic-trial-ledger.md) | 战法试验台（条件登记、组合成战法、卖出规则、每日总结） | reviewing | Claude Code 会话 93ba6e1d（主会话；第 9 步之后自己实现，不再派给 Codex），2026-10-14 | 等独立评审（只读的 Claude 子代理，报告写到会话草稿目录的 `review_tactic.md`，结论抄进本记录的 Log）返回：逐条在代码里核实，属实的修掉并复跑发布路径测试；然后把 main 合进分支再合回 main（先确认 `docs/decisions/` 里 0023 仍空着）、推送，用 `scripts/release plan <sha>` 确认 owner 仍是纯代码发布，备好 `state/rel07.sh`（owner，起点是 owner 当前的 e8cb8dbf，一次带上 W011 和本线）交给用户执行；edge 的中继和控制台各自的发布命令同样交给用户。发布后的顺序见 Due。 | 2026-10-14 |
| [W008](streams/W008-tdx-snapshot-filler.md) | 通达信补主行情快照的缺口并作备用 | released | Claude Code 会话 93ba6e1d（主会话，派工与验收），2026-10-13 | 10 月 14 日 09:16 核对新服务器上两条采集循环按新节拍落库（见 Due）；然后从 main 建 `tdx-live-snapshot-fallback` 工作区派工 6（B1 的读取链）。当日汇总从 10 月 14 日起才有可判的分钟：收盘后跑一次并把输出存到 `docs/work/probes/`，记为五个交易日观察的第 1 天 | 2026-10-13 |
| [W009](streams/W009-feishu-relay-new-tag-and-target.md) | 飞书转发：全新标签和全新目标群也只在前端操作 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 用户第一次在看板里用新标签建源群、或新增一个真实的目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源、该路由 healthy、目标群里收到了转发；通过后把状态改成 `verified` | 2026-10-13 |
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | released | Claude Code 会话 eac0e1fc，2026-10-13 | 等用户运行改备注名的探测（Waiting on the user）；通过后做“续费时一键改备注名”：bridge 的写备注接口、adapter 的接口、编辑框里的备注名一栏。其余见 Open questions 和待办 B056。之后回收剩下的两个工作区和集成分支，做探测 P0-8，状态改 `verified` | 2026-10-13 |
| [W011](streams/W011-peer-evening-stages.md) | 每晚该跑的阶段由我们这一侧自己触发 | merged | Claude Code 会话 93ba6e1d（主会话），2026-10-13 | 等用户执行发布（见 Waiting on the user）。发布后立即只读核对：owner 库里 10 月 13 日的五个 `peer_` 回执都是 completed（榜单和风险分当晚手工补过，应直接完成）；当天的情绪温度、宽基 ETF、金银指读数存在；前一交易日（10 月 12 日）的两个回执也是 completed；调度容器日志里没有“left open”那一行；发布前后调度进程内存相差不超过约 50 MB。然后在 10 月 14 日 16:50 核对第一次真正的当晚自动运行，并把 16:20 左右的风险分读数与 19:00 之后的重算对比 | 2026-10-13 |
| [W012](streams/W012-owner-hot-releases.md) | owner 只用一套镜像，其余改动全部热部署 | planned | Claude Code 会话 93ba6e1d（主会话），2026-10-13 | 审计第 2 轮又提了 11 条（见 Log），其中几条说明“目录发布”这一步比预想的重（SSH 中途断开后的恢复、锁的续期、systemd 单元的安装与回滚）。按用户“压缩”的要求把本线切成两段：A 段先做、只动分类规则和发布工具的判断——不认识的路径改成“未分类”（拒绝并提示加规则，不再叫人去做全量发布）、登记一批工作站脚本为跳过、迁移文件改成“库已到目标版本就放行”、依赖和 Dockerfile 单列为“镜像”；compose 和主机脚本的改动在 A 段仍走现有的全量发布。B 段是“目录发布”，等 A 段用过一阵再做。下一步：把方案改成这两段（第 3 版），A 段由主会话直接实现 | 2026-10-13 |
| [W013](streams/W013-licensed-batch-fetch.md) | 授权接口的批量取数：一个工具、一条并发规矩 | merged | Claude Code 会话 93ba6e1d（主会话），2026-10-13 | 第一次真实使用：10 月 14 日非交易时段用它补取 10 月 13 日当天 11 个代码的分钟线（BACKLOG B055；11 次请求，同时验证“最近的空结果不落盘”这条在真实接口上成立），看日志里的速率行和输出文件各多一行，通过后把本记录改成 `closed` | 2026-10-13 |

## 已结束、暂停或放弃

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W005](streams/W005-short-term-tactics-research.md) | 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0） | closed | none | none | 2026-10-13 |
| [W007](streams/W007-intraday-v-reversal.md) | 盘中止跌放量（V 形反转）的识别 | closed | none | 没有了：研究做完，报告在 `docs/archive/2026-10-13-v-reversal-study.md`。留下的三件事在 BACKLOG：B053（把两个时期方向一致的八个条件登记进战法试验台做盘中条件）、B054（“多数股票守住而权重创新低”换一种事件定义再检验）、B055（10 月 13 日当天的分钟线次日补取） | 2026-10-13 |
