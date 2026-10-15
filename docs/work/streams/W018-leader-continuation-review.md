# W018 龙头连板与极端低吸研究通道

- state: verified
- holder: Codex 主会话，2026-10-15
- opened: 2026-10-15 (Asia/Shanghai)
- goal: 把复盘中前序连板、深水承接和竞价证据沉淀为可结算的研究观察，解释漏抓原因并保持 live_effect none
- next: 下一交易日首扫后读取 continuation_watch、pullback_watch 的真实计数和样本，继续累计至少 20 个交易日的结算
- updated: 2026-10-15
- plan: none
- branch: main
- head: 547ef89d
- release: owner-code-20261015-leader-v1
- scope: 小杰龙头资金流的研究观察；不改变 live_effect，不进入自动下单

## Requirements

用户提供了 2026-10-15「只做核心」的带时间戳复盘。可核对的行为样本包括：09:17 等九阳股份/时代万恒深水低吸，09:31 时代未成交，09:37 继续强调“够低才吸”；09:40–10:27 观察传媒和澳弘电子承接/封板；10:14 总结九阳、时代、澳弘、近岸、善水、新华文轩与高度板为抱团；13:32 以后强调指数弱但情绪强、尾盘不要抢。

## Plan

只读探针 `docs/work/probes/2026-10-15-teacher-review-leader.py` 及其输出核对了名录和 2026-10-15 盘中证据。名录映射：九阳股份 002242.SZ、时代万恒 600241.SH、联亚药业 301569.SZ、澳弘电子 605058.SH、果麦文化 301052.SZ、百洋股份 002696.SZ、满坤科技 301132.SZ、近岸蛋白 688137.SH、善水科技 301190.SZ、新华文轩 601811.SH、领湃科技 300530.SZ、慈文传媒 002343.SZ、中文传媒 600373.SH、新华传媒 600825.SH。

同日 `fuyao_ths` limit-up pool 记录：慈文传媒与百洋股份为 5 连板、09:30 封板；新华文轩为 2 连板、14:49 封板；澳弘电子为首板、10:08 封板。limit-open pool 记录了百洋股份 09:58 开板一次、累计换手率 19.5849%，新华文轩开板一次、换手率 8.0178%，澳弘电子开板 16 次、换手率 14.8073%。这说明“板数、首封/回封、开板次数、换手”必须分开记录；封板时间不是可买时间。

## Steps

- `candidate_references` 计算前序连续涨停长度，并把同日 `auction_open_desk_daily` 的 09:25 竞价证据并入会话引用。
- `evaluate_pool` 保留 `continuation_watch`：前序至少两板且当日触板，即使市场闸门为风险环境，也记录竞价、换手、VWAP 承接和盘口证据缺口，`decision=research_watch`、仓位为 0。
- `evaluate_pool` 增加有界的 `extreme_pullback_watch`：跌幅至少 5% 且低于 MA5 的股票进入低吸研究池，记录是否回收日内低点、是否回到 VWAP、超跌确认是否缺失；不产生候选或告警。
- 研究观察写入既有观察表，但日候选账本只物化 `decision=research_candidate`，避免把观察样本当成策略候选。

- [x] 只读复盘探针和 owner 证据 — `547ef89d` — `docs/work/probes/2026-10-15-teacher-review-leader.out.txt` 已保存
- [x] continuation/pullback 研究观察与候选隔离 — `547ef89d` — 定向 release-path suite → Ran 105 tests, OK
- [x] owner code-only 发布 — `547ef89d` — `owner-code-20261015-leader-v1`，schema gate `at_head`，容器源码摘要通过

## Log

- 2026-10-15 Codex：远端盘中证据显示原策略在风险闸门下 `evaluated=87/88`、`candidates=0`；旧路径没有消费涨停池的前序连板、竞价和盘口证据，因此连板被门控后直接消失。
- 2026-10-15 Codex：新增前序连板长度、09:25 竞价引用、有界盘口特征、continuation_watch 和极端回撤 watch；研究观察仓位固定 0，不刷新 confluence、不告警，日候选账本只接收 `research_candidate`。
- 2026-10-15 Codex：发布后 `/health` 为 `ok`，build.git_sha/running_git_sha 均为 `547ef89d`；收盘后不强行重跑行情，下一交易日首扫复核新字段。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/work/probes/2026-10-15-teacher-review-leader.py`
- `docs/work/probes/2026-10-15-teacher-review-leader.out.txt`
- `docs/RELEASE_SYNC_47.md` 的 2026-10-15 发布记录
- `quant-service/app/xiaojie_indicators.py`
- `quant-service/app/xiaojie_reference_repository.py`
- `quant-service/app/xiaojie_observation_repository.py`
- `quant-service/app/xiaojie_leader_flow_runtime.py`
- `quant-service/app/strategy_daily_candidate_ledger.py`

## Open questions

- 需要用至少 20 个交易日的复盘结算区分“低吸池抓到机会”和“只是接住下跌”；不能根据一份老师复盘直接放宽低吸阈值。
- 盘口 `qi1/qi5`、OFI 和封单变化只作证据，当前没有足够样本证明“大单流入”可独立作为条件。

## Resume here

owner overlay `owner-code-20261015-leader-v1` 已上线，数据库 schema ready，live_effect 仍为 none。下一交易日先读 `/api/v1/intraday/scans/latest` 的 continuation/pullback 计数和观察样本；先确认 all-A snapshot 的 `decision_eligible`，再解释空结果。不要把 `research_watch` 当候选或告警，也不要在不足 20 个交易日结算前放宽阈值。主工作区有其他会话未提交文件，提交时只触碰 W018 自己的记录。
- 发布记录：2026-10-15（Asia/Shanghai），owner code-only overlay `owner-code-20261015-leader-v1`，目标提交 `547ef89dde3c401b8aee9624ee745ac74d6a15b1`，上一版 `7e9d61f3ea73ce9ac37ba7ca640439efce330442`，alembic `20261010_ow0121`；迁移闸门 `at_head`，发布脚本验证容器源码摘要与 `/app/hotfix/current` 一致。
- 发布后验证：owner `/health` 为 `ok`，build.git_sha 与 running_git_sha 均为目标提交，主服务与 scheduler 均 healthy，数据库 lineage 为 ready。收盘后最新扫描仍是发布前的 `14:59:57` 记录，`decision_eligible=false`、`candidates=0`；未在收盘后强行触发行情扫描，下一交易日首扫复核新增观察字段。
- 当前仍需用至少 20 个交易日的复盘结算区分“低吸池抓到机会”和“只是接住下跌”；不能根据一份老师复盘直接放宽低吸阈值。
- 盘口 `qi1/qi5`、OFI 和封单变化只作证据，当前没有足够样本证明“大单流入”可独立作为条件。
