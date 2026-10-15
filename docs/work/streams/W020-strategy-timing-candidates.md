# W020 策略分时候选板与盘后样本回流

- state: released
- holder: Codex 主会话，2026-10-15
- goal: 让每套龙头/竞价/盘中/尾盘研究策略在对应时段展示候选票、参考买点、选股理由和缺失证据，并把当天候选保留给盘后结算与 AI 复盘
- next: 下一交易日核对竞价、盘中和尾盘实际样本；收集样本后再做 AI 总结，不自动调整阈值
- updated: 2026-10-15
- plan: none
- branch: main
- head: 9828a9e1
- release: owner-code-20261015-live-freshness-3；edge-2026.10.15-timing；console-20261015T171516Z-9828a9e19051；hotfix-20261015T173007Z-861ecbfaa96e

## Requirements

- 2026-10-15 用户要求：“当前就是希望每套策略可以放心大胆的推出他选的票和买点，以及选他的原因。这里不需要因为过多的闸门担心而严格，这里需要的就是每个票选出来之后当天结束看看每个策略就有他的样本了再去让ai去总结修正就好了。”
- 2026-10-15 用户要求：“每个策略比如竞价的这几个龙头策略，可以在开盘前竞价的时候结合条件给出他要买的票。有的盘中扫描的可能是盘中买或者尾盘买。”
- 2026-10-16 用户要求：“在开盘前，把前端这里设计好哦，也方便后续修改拓展，这里就是前后端利用好我们的高性能owner的配置，把owner的200mbps网络带宽尽量利用高。”

## Plan

1. 在现有策略卡片上增加统一的分时计划：竞价、盘中、尾盘、盘后分别说明参考买点和理由。
2. 不新增订单路径或硬闸门；已有候选始终输出为 `research_candidate`，缺失数据单列为 `missing_evidence`。
3. 将 15 个注册契约策略全部列入 timing board；没有当天候选的策略仍显示计划和等待状态。
4. 盘后继续复用 `strategy_daily_candidates` / `strategy_daily_candidate_outcomes`，把买点、理由、阶段和缺失证据作为 AI 复盘对比字段。

## Steps

- [x] 1 新增纯研究分时板模块 `quant-service/app/strategy_timing_board.py`
- [x] 2 将竞价快照价格和成交量带入策略卡片，并输出每卡的 timing plan/candidates
- [x] 3 在 `/api/v1/strategies/cards` 响应加入覆盖全部契约策略的 `timing_board`
- [x] 4 为候选保留理由、参考价、缺失证据和盘后复盘字段；不改变 `live_effect=none`
- [x] 5 在 Session Radar 页面展示分时策略候选、参考买点和选股理由；frontend typecheck/build 通过
- [x] 6 focused release-path suite（14 tests）、边界检查、API contract 和 diff check 通过；全量 release-path suite 的既有 `test_settings` 因其他会话新增 local-spool 环境变量未声明而失败
- [x] 7 owner code-only、edge adapter 和量化控制台均已发布；owner schema gate、健康检查、运行时摘要/PID 1 和 timing board 路由已核对
- [x] 8 将当日 `auction_open_desk_daily` 和 `intraday_signal_events` 投影到对应研究线；4 板竞价样本与老师 entry 信号经 owner 路由实读，排序和来源理由已核对
- [x] 9 将 Session Radar 改为可扩展的四阶段工作台；实时雷达、策略卡片和健康并行读取，取消旧请求，收盘复盘明细不再在开盘前轮询
- [ ] 10 下一交易日核对竞价时段与盘中/尾盘阶段实际候选样本

## Log

- 2026-10-15 Codex：现有 `strategy_cards` 已能读取前一交易日候选台账、09:25 竞价、盘中 entry 信号和盘后 leaderboard，因此将分时解释层接在这里，避免新增孤立采集链。
- 2026-10-15 Codex：为全部 15 个 registry contract 添加至少一个时段计划；teacher、dragon、limit-up continuation、Longhu、ten-day rotation 明确提供竞价/盘中计划，base/pattern 明确提供尾盘/盘后计划。
- 2026-10-15 Codex：`scripts/release plan edce7d7a` 判定 owner 为 code-only overlay（2 个运行时代码文件），数据库已在所需 schema head；当前北京时间 20:04 落在 18:45-22:05 的 post-close-scheduler 禁重启窗口，未执行发布。
- 2026-10-15 Codex：Session Radar 新增“分时策略候选 · 买点假设与理由”表格，直接消费 `timing_board`；前端发布属于独立 edge-quant-console 单元。
- 2026-10-15 Codex：最终提交 `5d3daf17` 的只读发布计划仍显示 owner 在 18:45-22:05 禁重启窗口；owner overlay 和 edge console 均未执行发布。
- 2026-10-15 Codex：用户确认北京时间已闭盘后执行发布。owner `owner-code-20261015-timing`（源 `f9db189e`）通过 schema gate 并切换；edge adapter `edge-2026.10.15-timing` 和控制台 `console-20261015T130233Z-f9db189e9287` 已激活。adapter/console 直接健康和源码指纹核对通过；bridge 保持独立的 `861ecbfa` 旧覆盖层。
- 2026-10-15 Codex：发布后经 owner 路由读取 `timing_board`：`version=strategy-timing-board-v1`、16 行、`research_only=true`、`active_phase=post_close`；下一交易日只收集样本并交给 AI 复盘，不自动改阈值。
- 2026-10-15 Codex：owner 先发布慢事务优化 `82240ac3`（schema `20261015_ow0122`），随后发布 live-card overlay `14ac629d`，修正竞价候选排序后以 `279d1256` 重发；主服务和 scheduler 均报告 `owner-code-20261015-live-cards-fix`。
- 2026-10-15 Codex：`/api/v1/strategies/cards?trade_date=2026-10-15` 实读到百洋股份 `002696.SZ`、慈文传媒 `002343.SZ` 的老师 entry 候选，以及竞价龙头线的 4 板样本；每条候选含参考价、来源理由、`research_only=true`、`live_effect=none`。证据见 `docs/work/probes/2026-10-15-live-cards-release.out.txt`。
- 2026-10-16 Codex：策略卡片增加 `freshness`（读取时间、竞价源、盘中信号源、台账基线）；当前交易日只显示当天实时源，实时源缺失时返回空卡片和 `missing`，历史日期明确为 `historical_available`，旧台账只作 `replay_baseline`。
- 2026-10-16 Codex：Session Radar 的雷达、策略卡片和指标健康统一每分钟并行刷新；失败清空对应面板，读取序号阻止旧请求晚返回覆盖新结果。owner `bfd180e3` 与 edge console `13bde20b2b62` 已通过运行时/健康核验。
- 2026-10-16 Codex：提交 `9828a9e1` 增加数据驱动的竞价/盘中/尾盘/盘后阶段卡片、候选理由和参考价；实时读取使用 `AbortController` 取消过期请求，涨停复盘明细只在历史日或收盘后读取。前端 typecheck、26 项相关测试、build 和 diff check 通过。
- 2026-10-16 Codex：控制台发布 `console-20261015T171516Z-9828a9e19051`；为使独立控制台目录真正被同源适配器读取，按既定顺序重启既有 `861ecbfa` overlay，发布 `hotfix-20261015T173007Z-861ecbfaa96e-25717`。`scripts/release status --owner-source-sha bfd180e3 --edge-source-sha 861ecbfa` 全部检查通过；实读 `/` 的新 SessionRadar 资源和当日卡片接口确认当前竞价阶段无旧候选。

## Waiting on the user


## Evidence

- `quant-service/app/strategy_timing_board.py`
- `quant-service/app/strategy_cards_read_model.py`
- `quant-service/tests/test_strategy_timing_board.py`
- `quant-service/tests/test_strategy_cards_read_model.py`
- `frontend/src/research/session-radar.ts`
- `frontend/src/views/research/SessionRadarTab.vue`
- `docs/RELEASE_SYNC_47.md` 附录 C
- `docs/work/probes/2026-10-15-strategy-timing-release.out.txt`

## Running

## Due

- [ ] 2026-10-16 下一交易日首个竞价时段读取 timing board，核对各策略候选、参考价和缺失证据是否随阶段更新
- [ ] 2026-10-16 收盘后检查 `strategy_daily_candidate_outcomes` 是否能按策略沉淀当天样本，再交给 AI 做归因总结

## Open questions

- 下一交易日要观察各策略当天是否都有候选，以及一字封死、充分换手回封、深水承接三类样本在盘后是否能被分开结算；先收集样本，不在本线直接调整阈值。

## Resume here

focused suite 已通过；全量 release-path suite 只剩其他会话的 settings 失败。发布已完成，下一交易日从 `/api/v1/strategies/cards` 读取 `timing_board`，并继续完成上面的交易日核对；当日竞价池和 entry 信号已能进入相应研究线。
