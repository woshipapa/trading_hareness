# W002 竞价池采集与 09:25 处置表

- state: verified
- holder: none
- goal: 竞价期间对昨日涨停股定时采集快照、竞价曲线和委托明细，09:25 生成一张按跳空分档的处置表，只作研究证据
- plan: docs/work/plans/auction-open-desk.md
- branch: none
- head: 932f6b5c
- release: owner-code-20261012-21
- next: W006 的竞价看板上线后，决定处置表自己的页签和中继路由是否还要做（BACKLOG B017）；不做就关闭本记录
- updated: 2026-10-13

## Requirements

- 2026-10-12 「你可以考虑在我们竞价期间根据数据源的能力，看看都需要采集哪些数据以及什么样的频率，然后把我们现在的策略加上去来规避风险，增大收益」
- 2026-10-12 「可以可以，按照你说的这几个推进让codex去执行」

## Plan

`docs/work/plans/auction-open-desk.md`（第 2 版）。决策记录 `docs/decisions/0017-auction-open-desk.md`。方案第 4 步（控制台页签和中继映射）没有做。

## Steps

- [x] 竞价池、定时快照、竞价曲线、委托明细的采集与读数 `auction_open_desk_daily` — 合并 b0bea637
- [ ] 控制台页签和中继路由（方案第 4 步）— 未做，见 B017

## Log

- 2026-10-12 发布 `owner-code-20261012-21`（b0bea637）。
- 2026-10-13 09:34 首个交易日核对：竞价池 78 只；09:15:05–09:25:05 的 62 个快照时点全部落库；竞价曲线 78 条、委托明细 78 行；处置表 09:25:05 存入，覆盖：有风险分 46 只、接受 45 只、缺 1 只（未查明，B024）。

- 2026-10-13 21:10 reality: 竞价池每天 09:15 从前一交易日的大面风险分读数取“昨日涨停股”，而那份读数并不会每晚自动生成（BACKLOG B042）。10 月 13 日的处置表之所以有，是因为 10 月 12 日晚手工跑过。10 月 14 日有没有，取决于 W001 记录里今晚那条手工执行。

## Waiting on the user

## Running

## Due

## Evidence

- 发布记录：`docs/RELEASE_SYNC_47.md` “2026-10-12 夜至 10-13 凌晨” 一行。
- 首日核对的数字来自会话里对 owner 读路径的只读查询，输出未存档；库里的行数见 `docs/work/probes/2026-10-13-owner-history.out.txt` 的 capabilities 一段。

## Open questions

- 处置表缺的那 1 只是哪只、为什么（B024）。
- 全市场分钟快照 09:16 才开始（B023）。

## Resume here

采集和处置表每天自动运行。本记录只剩一个决定：页签还要不要（B017）。要核对某天是否正常：经中继 `http://127.0.0.1:18300` 目前没有处置表路由，直接用 owner 读路径 `GET /api/v1/market/auction-desk/daily`（要读密钥，按 `AGENTS.md` 的 owner 读路径规定）。
