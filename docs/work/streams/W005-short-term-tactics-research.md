# W005 短线战法调研（五路调研、老师复盘稿对账、组合战法 v0）

- state: closed
- holder: none
- goal: 调研龙头、连板、竞价、量价、筹码、压力位这些短线说法在数据上成不成立，结合老师的复盘稿给出第一版组合战法
- plan: none
- branch: none
- head: 932f6b5c
- release: none
- next: none
- updated: 2026-10-13

## Requirements

- 2026-10-13 「这里你需要启动多个codex-flow去调研A股的短线，竞价玩法，就是i龙头战法，看连板，量价，筹码，需要突破的压力的量价关系这些等等，然后先给出一套组合的战法」
- 2026-10-13 「你可以同时结合这些看一下我们的几个擒龙teacher的复盘稿，它里面的一些看法就是龙头短线要看的东西，可以结合一下，帮我们确定战法。」
- 之后的要求（放宽门槛、不成立的也登记、连板角色、卖点）改变的是怎么用这些结果，记在 W006。

## Plan

没有方案文件。五个并行的 Codex 研究任务共用一份数据说明和协议（本机 `state/research/shortterm_tactics_20261013/README.md`）。

## Steps

- [x] 五路调研报告与脚本 — 收进主线 932f6b5c（`docs/archive/2026-10-13-st-*`）
- [x] 主会话独立复核 — `docs/archive/2026-10-13-st-review-notes.md`
- [x] 老师复盘稿摘要 — 本机，未入库
- [x] 组合战法 v0，含连板角色、卖点口径更正 — `docs/archive/2026-10-13-st-combined-tactics-v0.md`

## Log

- 2026-10-13 筹码调研第一版的复权公式写错，结果作废后重算；“缩量突破更好”查明是一字板效应；历史竞价榜里的“主力买卖”是收盘后数据。都记在复核记录里。
- 2026-10-13 correction: 各表的收益只用了次日收盘等单一卖法，见 W006 的 Log 和组合战法文档“三之三”。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/archive/2026-10-13-st-*.md` 及各自的 `-data/` 目录；复核脚本与输出 `docs/archive/2026-10-13-st-checks/`。
- 数据（276 MB 的日线面板、涨停榜、竞价榜）在本机 `state/research/shortterm_tactics_20261013/data/`，未入库。

## Open questions

- 都转成了 BACKLOG 的条目（B012–B016）或 W006 里的条件和战法。

## Resume here

已结束。要用结果就读组合战法文档和复核记录；要接着做的事在 W006 和 BACKLOG。五个调研分支已回收并打了归档标签 `archive/codex/st-*/20261013`。
