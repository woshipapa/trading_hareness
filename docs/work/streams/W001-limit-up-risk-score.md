# W001 涨停股次日大面风险分

- state: released
- holder: none
- goal: 每天收盘后给当日涨停股算一个“次日收盘跌 5% 以上”的风险分并提供页面和回看，只作研究证据
- plan: docs/work/plans/limit-up-distribution-scores.md
- branch: none
- head: 932f6b5c
- release: owner-code-20261013-04
- next: 用户执行 `! bash ~/codebase/n8n/state/lists13.sh`（今晚 24:00 前）之后，agent 只读核对：`curl -s http://127.0.0.1:15682/api/v1/market/limit-up-risk/daily` 的 `reading.trade_date` 是 2026-10-13、行数与当天榜单相符（试跑时 5 张榜共 71 行）。此后每个交易日晚上都要这样手工跑一次，直到 BACKLOG B042 的代码修好
- updated: 2026-10-13

## Requirements

- 2026-10-12 「可以可以，按照你说的这几个推进让codex去执行」（批准实现风险分和竞价采集两项）
- 2026-10-12 「已合并待发布：大面风险分页面及其后端修正。 这个也可以发布，我同意了」

## Plan

`docs/work/plans/limit-up-distribution-scores.md`（第 2 版，研究先经独立审计）。决策记录 `docs/decisions/0016-limit-up-risk-score.md`。

## Steps

- [x] 后端：读数 `limit_up_risk_daily`、九个系数、与研究逐只一致的夹具 — 合并 dff8acee
- [x] 页面、中继路由、回看提速与“近期是否有效” — 合并 857e4832
- [x] 修复 `daily()` 返回窗口里最早读数的错误 — 5e2d159a
- [x] 回看接口日历上界修复 — 7bb29e71 — 随 `owner-code-20261013-04` 发布（2026-10-13 17:41）；`track?days=60` 返回 59 天（修复前 1 天），`days=250` 返回 249 天

## Log

- 2026-10-12 发布 `owner-full-20261012-20`（dff8acee）、`owner-code-20261013-01`（857e4832）。
- 2026-10-13 上线后在实际运行的源码上读值，发现 `daily()` 不带日期时返回 800 天窗口里最早的一条（竞价池会取到 2024 年的股票）；修复 5e2d159a，09:14 前以 `owner-code-20261013-02` 发布。
- 2026-10-13 午间核对别的发布时发现两个回看接口取“最近 N 个交易日”没有以当天为上界；修复 7bb29e71，补了测试，未发布。

- 2026-10-13 21:10 correction: 本记录此前写“阶段已接在盘后流程里，每晚自动生成”，并据此标成了 verified。这是错的：完整的盘后流程只在对方自己的服务上定时运行，我们这一侧新加的阶段没有任何东西触发（BACKLOG B042，FACTS F023）；10 月 12 日晚的榜单和读数是发布验证时手工跑出来的。10 月 13 日 20:49 库里没有当天的榜单和读数（FACTS F024）。状态因此退回 released：代码和接口是对的，“每晚自动产出”这一条没有成立。今晚先手工补（见 Waiting on the user），长期的修法在 B042。

## Waiting on the user

- [ ] 2026-10-13 21:10 今晚 24:00 前执行 `! bash ~/codebase/n8n/state/lists13.sh`：把 10 月 13 日的 5 张实时涨停榜和当天的大面风险分读数存进 owner 库。为什么等：这是对 owner 库的写入，只能用户执行。为什么今晚：实时榜单接口只给当天的数据；明早 09:15 的竞价池（W002）要读这份读数，没有它 10 月 14 日就没有竞价处置表。脚本已不写库试跑过：5 张榜 71 行（57、5、5、2、2）。之后：agent 按 next 核对。

## Running

## Due

- [x] 2026-10-13 19:30 核对当晚盘后自动生成的涨停榜和风险分读数（明早 09:15 的竞价池从 10 月 13 日的风险分读数取昨日涨停股）：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/daily'` 返回的 `reading.trade_date` 应是 2026-10-13。没有的话看盘后流程是否运行，以及 owner 主机是否正常（BACKLOG B031）。 2026-10-13 20:30 没有做成：本机的 owner 读路径不通（对方迁了服务器，见 BACKLOG B041）；经 edge 的那条 `curl` 也要等 edge 到新主机的连接确认。B041 连通后做；明早 09:15 的竞价池依赖这份读数。 → 21:10 核对了，结果是没有生成，而且不会自己生成（2026-10-13）：见 Log 的更正和 BACKLOG B042

## Evidence

- 发布记录：`docs/RELEASE_SYNC_47.md` 末尾 “2026-10-12 夜至 10-13 凌晨” 和 “2026-10-13 午间” 两行。
- 线上与研究一致：当日风险最高名单逐只相同；回看留出段 8.5% 对 19.0%（发布记录同一行）。
- 研究报告：本机 `state/research/divergence_study_20261012/grab_dump/REPORT_抢筹出货判断.md`（未入库）。

## Open questions

- 回看接口 250 天约 1.8 秒，500 天约 7 秒：够用，未再优化。

## Resume here

功能在线且核对过。剩一件事：B011 的修复随下次 owner 发布上线，然后把本记录改成 `closed`。发布后核对：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/track?days=60'` 返回的天数应接近 60 而不是 1。
