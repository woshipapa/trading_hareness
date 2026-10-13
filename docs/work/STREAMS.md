# 工作线索引

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
| [W010](streams/W010-relay-group-automation.md) | 飞书中继：目标群管理、撤回同步、加急告警、链接入群 | building | Claude Code 会话 eac0e1fc，2026-10-13 | 收 members 线的补丁（成员列表加 `is_bot`、`is_self`，线程 01a1348e）、urgent 线的四条修正（线程 01a13490）、membership 线的五条修正（线程 01a13494）。members 补丁合并后，给 membership 线续派：合入集成分支、成员同步跳过 `is_bot` 和 `is_self`。然后 urgent、membership 依次：复跑验收 → edge 上 bridge 套件 → 合进集成分支 → main → 热更新 → 线上核对。最后做探测 P0-8、回收八个工作区、汇总 token 开销 | 2026-10-13 |

## 已结束、暂停或放弃

| 编号 | 标题 | 状态 | 持有者 | 下一步 | 更新 |
|---|---|---|---|---|---|
| [W005](streams/W005-short-term-tactics-research.md) | 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0） | closed | none | none | 2026-10-13 |
