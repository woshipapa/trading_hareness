# 0031 盘末分钟 profile 的有界 fan-out 与批量持久化

- 状态：生效
- 日期：2026-10-16

## 背景

盘末分钟 profile 面向显式观察池，Longhu 的 licensed 单票趋势请求最多 8 路并发。旧路径为每个 symbol 创建一个任务，并在 owner 隧道中逐票注册 instrument、逐票执行分钟 `executemany`、逐票删除旧 retention 行；观察池扩大后，这些往返会把 provider executor 和数据库事务时间放大。

## 决定

本地抓取用 `LONGHU_MINUTE_BATCH_WORKERS` 的默认 8 与硬上限 8 做 semaphore。每个 symbol 仍独立记录 provider 失败和日期校验结果；成功结果在一个事务中完成 instrument `unnest` upsert、合并后的分钟 `executemany` 和 `ANY(text[])` retention 删除。Watch-review 的整段 session 写入复用同一批量参数路径。

## 后果

200 只观察池不会一次性向 realtime executor 提交 200 个请求，也不会让每个 symbol 产生独立的 owner 往返。内存只保留当前一次 capture 的已验证 rows，provider ceiling、跨日拒绝、source_name、幂等冲突键和 research-only 语义不变。

## 怎样保证

回归验证了 fan-out 的最大并发可注入并被压到 3/8、200 只 Tencent capture 的 80/80/40 物理批次、分钟行的跨日拒绝和批量 upsert；overlay focused 78 项通过。实际 owner 的事务时延、连接和隧道速率仍需发布后同交易日采样。
