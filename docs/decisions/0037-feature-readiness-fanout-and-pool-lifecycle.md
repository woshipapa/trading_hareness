# 0037 feature-readiness 并行读与连接池生命周期

- 状态：生效
- 日期：2026-10-16

## 背景

`GET /api/v1/data-readiness/features` 是只读研究门禁投影。它需要读取八个
独立数据集的符号数、行数和最新日期，再读取全 A universe 大小。原实现把
八个聚合放在一个 UNION 中，数据库必须先完成最慢分支，异步请求也不能利用
可用连接和 CPU 重叠执行。与此同时，多个首屏请求可能同时触发懒加载连接池，
psycopg pool 的 `open()` 不是 single-flight，曾在启动突发中出现竞态。

## 决定

1. async `feature_readiness` 为每个独立聚合建立一个有界池读任务，并与
   universe 统计并发执行。任务仍只读，使用 PostgreSQL READ COMMITTED 的
   语句级快照语义，不引入跨请求 TTL、旧数据或 provider 调用。同步兼容路径
   保留原查询合同。
2. `20261016_ow0130` 为 canonical bars、fundamentals 和 trade limits 的
   cardinality 读重建带日期 payload 的索引，保证全量扫描可以尽量走
   index-only 计划；迁移仍由 owner 先过 schema gate，再切换源码覆盖层。
3. sync 与 async 数据库池的 `open`、`close` 生命周期分别由线程锁和事件循环
   锁保护；async `replace_pool` 持锁完成新池首次打开，避免关闭并发看到半切换的
   `_opened=false` 窗口。连接池软件上限仍为 async 32、等待队列 128，owner 默认
   16；硬件、数据库和隧道预算决定实际可用并发，不能仅因软件上限存在就继续加大。

## 后果

- 本地 compose overlay 的 feature-readiness 三次中位数从同进程 legacy UNION
  的 1,716 ms 降到并行读的 1,532 ms；返回 8 项、`decision_ready=true`，并发
  读任务数量为 9。
- 当前配置的 1/2/4/8 并发压力矩阵全部返回 HTTP 200；单请求 p50 约 1,672 ms，
  8 并发 p95 约 7,855 ms，并产生 5--8 秒慢事务告警。4 核本地数据库在重聚合
  并发时已接近饱和，因此暂不把 owner 默认池从 16 继续上调；发布后要用 owner
  的真实隧道和锁等待数据重新测量。
- 连接池首次打开不会被同一突发重复调用，减少启动错误；这不改变事务、研究
  gate 或策略 promotion 语义。

## 怎样保证

- `tests.test_async_provider_and_readiness_reads` 覆盖 8 个 feature 读和 universe
  读的并发与返回投影；`tests.test_provider_and_realtime_rules` 覆盖 sync/async
  pool open single-flight。
- `docs/work/probes/2026-10-16-feature-readiness-overlap.py` 保存同进程 legacy
  对照、语义字段和读任务数量；`...-stress.py` 保存 1/2/4/8 ASGI 压力结果。
- 本地验证使用源码 overlay。owner 尚未应用 `20261016_ow0130`，也未执行生产发布；
  owner 发布后复核 p50/p95、statement count、pool waiting、锁等待和隧道速率。
