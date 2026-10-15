# W019 多策略与工作区分支盘点

- state: verified
- holder: Codex 主会话，2026-10-15
- goal: 盘点代码、运行时、数据库目录和 worktree/分支里的龙头及相关策略，标出可运行、研究影子、历史残留和数据源短板
- next: 用户若要实施增强，先从 Open questions 选择一个研究变更并建立独立方案；本盘点本身已完成
- updated: 2026-10-15
- plan: none
- branch: main
- head: c0d38ce4
- release: none

## Requirements

- 2026-10-15 用户要求检查当前不止 `xiaojie` 的多种策略，并确认龙头策略是否在代码或其他 worktree/分支中。

## Plan

只读盘点四层证据：代码契约和模块、运行时绑定与调度、owner 远端策略看板/目录/晋级闸门、Git worktree/分支。盘点结果保持 research-only，不修改阈值、不修改 live_effect、不写 owner 库。

## Steps

- [x] 1 生成代码契约和模块清单 — `docs/work/probes/2026-10-15-strategy-inventory.py` → `contract_count=15`，模块均存在
- [x] 2 保存 owner 当前策略看板、数据库目录和 promotion 闸门读数 — 远端只读 API 输出已保存；看板 `ready=10/degraded=5`，晋级 `nonzero=[]`
- [x] 3 对照 worktree/分支历史，确认龙头实现是否已在 main — `7d9a7111`、`9c76fb22`、`0ce39a44` 均为 main ancestor；相关分支差异已保存
- [x] 4 汇总缺口、版本漂移和可实施的增强方向 — 本记录 Log、Evidence 和 Open questions

## Log

- 2026-10-15 Codex：主线 `strategy_registry.py` 有 15 个显式契约，`main.py` 有对应的运行时版本绑定；全部 `live_effect=none`。
- 2026-10-15 Codex：owner `2026-10-15T16:46:11+08:00` 看板为 ready 10、degraded 5；`dragon_leader_research` ready，`xiaojie_leader_flow`、`teacher_review_playbooks`、`launch_radar`、`longhu_multifactor_shadow` 等均在看板中。
- 2026-10-15 Codex：数据库 `/api/v1/strategies` 仍保留旧的 event-research、EAC、SQL factor-lab 和 watchlist 变体；其中 `multi_factor_rank_v1` 的数据库版本是 `strategy-v2`，代码契约是 `strategy-v1`，存在目录漂移。
- 2026-10-15 Codex：dragon leader 的历史提交 `0ce39a44`、`9c76fb22`、`7d9a7111` 都已在 `main`；`integrate/pr1-safe` 只相对当前 main 保留一处旧的 nested continuation 字段兼容改动，不是另一个未合入的龙头策略实现。
- 2026-10-15 Codex：静态调用关系显示 `xiaojie_leader_flow` 直接挂在盘中 watchlist scan，并内嵌 `launch_radar`；teacher review 与 ten-day rotation 由同一盘中扫描/独立盘后调度承载；dragon leader 主要经 `strategy_pattern_read_model` 和 `post_close_pattern_candidates` 提供盘后读模型，没有独立盘中后台 loop。
- 2026-10-15 Codex：策略卡片接口当前 `lines_count=0`；这说明“契约 ready”不等于当天已有候选卡片或有可执行信号。
- 2026-10-15 Codex：契约之外还发现战法试验台的 20 组 close/auction 组合，含 `leader_hold`、`relay_2_3`、`leader_moderate_gap`、`teacher_auction_entry`、`amount_top5_big_theme` 等；由 `tactic_auction` 在 09:25 后生成研究读数，属于实验台/账本，不是可晋级的策略契约。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/work/probes/2026-10-15-strategy-inventory.py`
- `docs/work/probes/2026-10-15-strategy-inventory.out.txt`
- `docs/work/probes/2026-10-15-strategy-board.out.txt`
- `docs/work/probes/2026-10-15-strategy-catalog.out.txt`
- `docs/work/probes/2026-10-15-strategy-promotion.out.txt`
- `docs/work/probes/2026-10-15-strategy-branches.out.txt`
- `quant-service/app/platform/strategy_registry.py`
- `quant-service/app/strategy_promotion.py`
- `quant-service/app/dragon_leader_research.py`
- `quant-service/app/xiaojie_leader_flow.py`
- `quant-service/app/teacher_review_rules.py`
- `quant-service/app/longhu_multifactor_shadow.py`
- `quant-service/app/tactic_trials.py`
- `quant-service/app/tactic_trial_runtime.py`

## Open questions

- 数据库旧目录是否应该迁移为“历史研究目录”并与代码契约分层，需要单独设计迁移和保留期；本线先不删除旧项。
- `dragon_leader_research` 当前看板 ready 但没有 ledger 行，需确认是否要把它接入盘后调度/观察账本；接入前仍须保持研究观察，不得变成交易路径。
- `multi_factor_rank_v1` 的数据库 `strategy-v2` 与代码 `strategy-v1` 是否是同一模型谱系，需以 owner 的历史实验记录和提交 lineage 对账后再决定统一版本。

## Resume here

先阅读本记录的 Evidence，再用 `python3 docs/work/probes/2026-10-15-strategy-inventory.py` 复跑静态盘点。当前已确认：龙头相关实现不止 Xiaojie，主线已有 15 个契约且全是 evidence-only；远端 promotion 15 项全部权重 0。不要把数据库旧 registry 行当作当前运行时策略，也不要在没有样本和 promotion 记录前放宽竞价条件。
