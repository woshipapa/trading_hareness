# W013 授权接口的批量取数：一个工具、一条并发规矩

- state: closed
- holder: Claude Code 会话 93ba6e1d（主会话），2026-10-14
- goal: 以后从本机批量读授权接口都走同一个工具，默认按 2026-10-13 量出来的并发跑，上游会拒绝的设置工具自己拒绝，交易时段不跑；规矩写在 agent 会读到的地方
- plan: docs/work/plans/licensed-batch-fetch.md
- branch: main
- head: 45e2fca5
- release: none（只在本机运行的工具，没有发布单元）
- next: 无。以后批量读授权接口都用 `scripts/licensed_minute_history_fetch.py`；规矩在 `AGENTS.md` “Time and data rules” 和 `docs/work/README.md`。
- updated: 2026-10-14

## Requirements

- 2026-10-13 「那个服务器更新的就是内存小了点，但他网络带宽更高，咱们这边可能可以加大并发」
- 2026-10-13 「这里公共执行器的约束可以修改，你可以试找到现在的最大可用数！」
- 2026-10-13 「好的，你把代码和规范改一下以后都跑这个批量的并发」（主会话把这句当作对方案的批准：工具默认 6 个并发、最多 8 个）

## Plan

`docs/work/plans/licensed-batch-fetch.md`。量出来的上限约 7.4 次/秒，在 8 个并发时达到，16 个会被 owner 网关拒绝（FACTS F027）。规矩：默认 6、最多 8；交易日 09:00–15:10 不跑；连续 20 次失败停；上游返回“无数据”以外的错误码算失败；可断点续取；超过 5,000 次仍要用户明确同意；服务内部的并发设置不动（盘中没量过）。工具是 `scripts/licensed_minute_history_fetch.py`，接替当晚临时用的探针脚本。

## Steps

- [x] 工具、测试、发布分类规则、脚本目录的规则 — Codex 实现 6ba9f13a、ee079d06；主会话改了两处 d0053ca0（最近三个交易日的“无数据”不落盘，否则当晚取不到的当天会被永久记成空；循环里三处重复的补位和两处重复的中止合成一处）；`pytest scripts -q` → `1 failed, 365 passed`（失败的是 B035），node 测试通过，脚本目录检查通过
- [x] 规矩写进 `docs/work/README.md`、本机记忆笔记和 `AGENTS.md`（“Time and data rules”一条，随工具合并）
- [x] 合并 — 45e2fca5。评审是主会话读代码并改了两处；没有第二个评审者

## Log

- 2026-10-13 23:09 开线并派工。测量过程和数字见 W007 的 Log 与 FACTS F027。发布分类规则要加一条（新脚本登记为工作站工具，否则它会把下一次 owner 发布判成全量）；W008 时这类改动先问过用户，这次用户的话是“把代码和规范改一下”，主会话当作同意，并在汇报里明说。
- 2026-10-14 00:38 第一次真实使用（BACKLOG B055 的取数部分）：取 2026-10-13 一天、11 个代码，目标 owner 读通道 `http://127.0.0.1:15682`（隧道在，`/health` 报告 `owner-code-20261013-05`），非交易时段。日志 `state/research/vshape_20261013/data/fetch.log` 最后一行 `00:38:41 FINISHED 11 calls (159915 510050 510300 510500 512100 588000 SH000001 SH000300 SH000852 SZ399001 SZ399006)`；11 个文件各多一行（上证指数 242 个点，300ETF 241 个点）。只有 11 次调用，没到每 200 次才打的速率行。没验证到的一条：“最近三个交易日的空结果不落盘”在真实接口上没有碰到（这次都有数据），仍只由测试保证。工作区 `licensed-batch-fetch` 已回收（`finishworktree.sh --apply`）。本线没有发布：工具只在本机运行。记录关闭。

## Waiting on the user

## Running

- [x] 2026-10-13 23:09 Codex 实现者在写工具和测试（新线程，工作区 `/Users/papa/codebase/.codex-wt/n8n-licensed-batch-fetch`）。结束标志：分支有提交且 `git status --short` 为空。之后见 next。挂在主会话 93ba6e1d 上。 → 交回 ee079d06（2026-10-13 23:41）

## Due

## Evidence

- 测量：FACTS F027；`docs/work/probes/2026-10-13-licensed-read-concurrency.py` 与 `.out.txt`；`state/research/vshape_20261013/data/fetch.log` 里带 `worker(s)` 的行。

## Open questions

- 上限卡在 owner 一侧的网关。要更快得对方放宽他那边的并发，供应商能承受多少不知道；用户还没说要不要去问。
- 盘中的容量没有量过，服务内部的并发设置这次不动。

## Resume here

方案很短，先读它。派工只有一次；工具不连任何主机就能测（假的调用函数）。合并后以后的批量取数都用这个工具，不要再复制当晚那个探针脚本。
