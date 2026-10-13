# W001 涨停股次日大面风险分

- state: verified
- holder: none
- goal: 每天收盘后给当日涨停股算一个“次日收盘跌 5% 以上”的风险分并提供页面和回看，只作研究证据
- plan: docs/work/plans/limit-up-distribution-scores.md
- branch: none
- head: 932f6b5c
- release: owner-code-20261013-02
- next: “回看接口把未来交易日算进最近 N 天”的修复（7bb29e71，见 BACKLOG B011）随下一次 owner 发布上线后，关闭本记录
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
- [ ] 回看接口日历上界修复 — 7bb29e71，已在主线，未发布

## Log

- 2026-10-12 发布 `owner-full-20261012-20`（dff8acee）、`owner-code-20261013-01`（857e4832）。
- 2026-10-13 上线后在实际运行的源码上读值，发现 `daily()` 不带日期时返回 800 天窗口里最早的一条（竞价池会取到 2024 年的股票）；修复 5e2d159a，09:14 前以 `owner-code-20261013-02` 发布。
- 2026-10-13 午间核对别的发布时发现两个回看接口取“最近 N 个交易日”没有以当天为上界；修复 7bb29e71，补了测试，未发布。

## Waiting on the user

## Running

## Due

## Evidence

- 发布记录：`docs/RELEASE_SYNC_47.md` 末尾 “2026-10-12 夜至 10-13 凌晨” 和 “2026-10-13 午间” 两行。
- 线上与研究一致：当日风险最高名单逐只相同；回看留出段 8.5% 对 19.0%（发布记录同一行）。
- 研究报告：本机 `state/research/divergence_study_20261012/grab_dump/REPORT_抢筹出货判断.md`（未入库）。

## Open questions

- 回看接口 250 天约 1.8 秒，500 天约 7 秒：够用，未再优化。

## Resume here

功能在线且核对过。剩一件事：B011 的修复随下次 owner 发布上线，然后把本记录改成 `closed`。发布后核对：`curl 'http://127.0.0.1:18300/api/research/market/limit-up-risk/track?days=60'` 返回的天数应接近 60 而不是 1。
