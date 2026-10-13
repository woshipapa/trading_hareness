# W003 日线收盘补全

- state: released
- holder: Claude Code 会话 93ba6e1d，2026-10-13
- goal: 每晚全市场日线之后，按名册逐只补上缺的日线、涨跌停价和复权因子，补不了的给出分类原因，不再每天漏 70–150 只
- plan: docs/work/plans/daily-close-completion.md
- branch: none
- head: 932f6b5c
- release: owner-code-20261013-03
- next: 先看 owner 主机是否恢复（BACKLOG B031）；恢复后在 10 月 13 日 19:30 之后核对该阶段的第一次自动运行（见 Due），通过后改成 `verified`
- updated: 2026-10-13

## Requirements

- 2026-10-12 「请你把缺口的都按照顺序补上，使用我们现在的数据源。然后阈值修改」（先手工补了 9 月 21 日至 10 月 12 日的缺口）
- 2026-10-12 「请你把根源解除掉，必须全部采集。请你验收完全部更改当前可以发布」
- 2026-10-13 「继续推进根源修复，午间发布」

## Plan

`docs/work/plans/daily-close-completion.md`（第 2 版，审查一轮后裁决两处）。决策记录 `docs/decisions/0018-daily-close-completion.md`。实现经四轮评审。

## Steps

- [x] `daily_close_completion.py`、盘后阶段、对账入口、老师复盘用的修复入口、健康检查 `data.daily_bars_complete` — 合并 29f3ffdf — 发布路径测试 3036 项通过（合并时）
- [x] 发布 — `owner-code-20261013-03`，2026-10-13 11:57，用户执行包装脚本
- [ ] 首次自动运行的核对

## Log

- 2026-10-12 夜 手工修复：补日线 818 根、因子 814 个、涨跌停价 811 个；脚本和日志在本机 `state/research/owner_repairs_20261012/`。留下的尾巴见 BACKLOG B008。
- 2026-10-13 12:00 发布后核对：状态脚本 ALL CHECKS PASSED；主服务和调度两个容器实际导入的源码目录都是 `owner-code-20261013-03`；阶段表里 `daily_close_completion` 在 `full_market_daily` 之后、`limit_performance_lists` 之前；健康检查项出现，状态是未到产出时点。

- 2026-10-13 15:20 reality: owner 主机 15:07 起不响应（BACKLOG B031）。今晚盘后流程和本阶段的首次运行都取决于它恢复；恢复前这条线什么也核对不了。

## Waiting on the user

- [ ] 2026-10-13 请从云控制台看 owner 主机（47.110.79.189）的 CPU、内存和带宽监控，并决定是否重启服务或主机。为什么等：15:07 起它不响应，agent 连不上去就看不到原因，重启只能由用户做。之后：用户说明看到了什么，agent 据此排查并核对今晚的盘后流程（BACKLOG B031）。

## Running

## Due

- [ ] 2026-10-13 15:20 先确认 owner 主机是否恢复（BACKLOG B031 写了现象和检查命令）。没恢复就提醒用户看云控制台；恢复后先读负载和内存、弄清原因，再做下面 18:45 那条。
- [ ] 2026-10-13 19:30 核对该阶段第一次自动运行（盘后流程 18:45 才开始，早查只会看到“没有收据”）：读 `fetch_runs` 里能力 `daily_close_completion` 的回执（状态、各分类计数、剩下的股票和原因）；抽查补上的日线有没有涨跌停价和因子；健康检查 `data.daily_bars_complete`（`curl http://127.0.0.1:18300/api/research/indicators/health`）。阶段没跑或 `blocked`：查原因；当天缺口可用本机 `state/research/owner_repairs_20261012/` 里验证过的脚本手工补（写库由用户执行）。

## Evidence

- 发布后状态检查的输出：`docs/work/probes/2026-10-13-release-status.out.txt`。
- 发布记录：`docs/RELEASE_SYNC_47.md` “2026-10-13 午间” 一行。
- “两个容器导入的源码目录”来自会话里的只读命令，输出未存档；可按 Resume here 的进容器办法重跑：在容器里 `python -c "from app import daily_close_completion as c; print(c.__file__)"`，路径里应有 `owner-code-20261013-03`。

## Open questions

- 之后有除权、指定日期 K 线不能用的股票日，新流程会归为 `adjusted_later`：每天有几只、要不要另找未复权来源（B008）。

## Resume here

交接：用户 2026-10-13 15:20 说明之后会换新会话接着推进，但写这段时旧会话（93ba6e1d）还在工作。新会话先判断旧会话是否还在（`docs/work/README.md` “持有者”一节的办法）：还在就先问用户；不在了就把 `holder` 改成自己，挂在旧会话上的后台任务（Codex 派工、子 agent、后台脚本）也已随它结束，按下面的办法从文件状态接着做。

代码已在生产上运行，还没有经过一次真实的盘后运行。第一件事是 Due 那一条。盘后流程 18:45 之后才开始，本阶段排在全市场日线之后，18:45 整去查很可能还没有回执（健康检查此时会报“没有收据”，不代表失败）：19:30 之后再查，仍没有才算异常。

读回执的办法：owner 主服务容器名是 `trading-hareness-peer-quant-research-1`（调度容器同名加 `-scheduler`）；SSH 参数是 `config/secrets/.env.local` 里的 `LONGHU_SSH_HOST / PORT / USER / KEY_PATH` 四个变量，只在内存里读、不打印。调用方式照抄 `docs/work/probes/2026-10-13-owner-history.py` 文件头和 `docs/work/probes/2026-10-13-tdx-shadow.py`：`ssh … "docker exec -i <容器> sh -c 'd=/app/hotfix/current; cd $d && PYTHONPATH=$d python -'" < 脚本`。查询形状见 `quant-service/app/indicator_health.py:326-340`（`quant.fetch_runs`，`capability='daily_close_completion'`，当天）。

“通过”的标准：回执状态是 `completed` 或 `partial` 且每个没补上的股票都有分类原因；当天全市场日线行数不少于名册上应有的数减去停牌和已分类的；抽 5 根补上的日线，都有涨跌停价，复权因子有或被标为待定。所有 owner 检查只读；要写库必须用户同意并由用户执行。
