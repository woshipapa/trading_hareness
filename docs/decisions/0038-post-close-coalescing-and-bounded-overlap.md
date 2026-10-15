# 0038 盘后收盘数据去重与有界阶段并行

- 状态：生效
- 日期：2026-10-16

## 背景

Longhu 模式的盘后流程先执行 `all_a_universe`，再执行
`full_market_daily`。两个入口过去都会请求并落盘同一交易日的全市场收盘
证据。盘后阶段本身也把互不依赖的研究任务逐个等待，导致 provider 等待和
本地数据库读写无法重叠。

## 决定

1. 两个 Longhu 入口通过按交易日分片的 async memo 共享同一份成功结果，缓存
   仅保留 300 秒、最多四个日期；失败或控制面阻断结果不缓存，调用方仍可
   重试。应用停机时取消仍在进行的 fetch。memo 只消除重复请求，不提高
   Longhu、数据库或隧道并发上限。
2. 盘后 orchestrator 只对已审计的独立阶段启用并行组：早期清理/日历/分析师
   文本，两个独立分析师 outcome ledger，以及四个独立研究温度/ETF/市场时点
   读数。每组仍受现有 scheduler 数据库 executor 和 provider limiter 约束，
   其余阶段保持原有顺序；依赖回执先于依赖阶段执行。
3. 收盘策略 review 的指数上下文和市场截面是不同证据产品，收盘时一起启动；
   任一失败仍使该 checkpoint 失败。日线补齐把 roster 与当日 bars 合成一次
   left-join 读取，并复用已读的当日 bars，减少 owner 隧道往返。

## 后果

- 本地合成 orchestrator 探针 20 次中位耗时从 635.246 ms 降到 269.285 ms，
  峰值并发从 1 到 4；该数字只证明编排 overlap，不代表 owner/provider 吞吐。
- 同一交易日的两个 Longhu 路由在回归中只调用一次底层同步；失败回退、
  `all_a` universe 标记和 full-market 状态字段保持原合同。
- 研究写入仍由各自 durable stage receipt 保护，策略 promotion/live effect
  边界未改变。生产收益必须在 owner 发布后按同一交易日复核 statement count、
  锁等待、pool waiting、provider 拒绝率和隧道速率。

## 怎样保证

- `tests.test_async_keyed_memo` 覆盖同 key join、成功复用和失败重试；
  `tests.test_longhu_shared_full_market` 覆盖两入口只发一次请求。
- `tests.test_post_close_refresh` 覆盖并行组依赖闸门，
  `tests.test_strategy_review_scheduler` 覆盖指数与收盘截面同时启动；
  `tests.test_daily_close_completion` 覆盖单次 roster/bar 读取。
- overlay focused regression（146 项）与组合回归（146 项）通过；探针输出
  为 `docs/work/probes/2026-10-16-post-close-overlap.out.txt`。owner 尚未应用
  或发布这组源码，不能把本地结果写成线上结果。
