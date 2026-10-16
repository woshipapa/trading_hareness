# 0040 研究概览只合并活动读取

## 背景

研究台的多个面板可能在同一刷新波次请求
`GET /api/v1/research/overview`。该投影包含历史容量估算、覆盖率和
feature readiness，重复请求会同时占用 async read pool，并在 owner 隧道上
重复传输相同的只读结果。

## 决定

1. 每个路由实例使用固定 key 的 `AsyncKeyedMemo`，只让相同请求加入当前仍在执行的读取。
2. `ttl_seconds=0` 且不保存成功结果；一次读取完成后，下一次请求必须重新读取数据库。
3. 原生 async history estimate 与 overview snapshot 并行启动；没有原生 adapter
   时，旧同步 estimator 通过 bounded database executor 与 async snapshot 并行。
4. 共享 task 由 `asyncio.shield` 保持独立于单个 HTTP caller 的取消；任一阶段失败会清理兄弟 task，失败不进入缓存并可立即重试。

## 边界

这是进程内的请求合并，不是跨进程缓存，也不改变 snapshot、history estimate、
point-in-time 或 fail-closed 语义。多 worker 部署中每个 worker 仍可能各执行一次；
在没有 owner statement、锁等待和隧道证据前，不增加结果 TTL 或继续扩大数据库池。

## 验证

源码 overlay 的 overview 压测在 1/2/4/8 并发下全部返回 200，p95 约
1,527.8/1,059.2/887.5/1,029.6 ms，连接池最终 `available=8,waiting=0`；
测试覆盖 native history、sync fallback、并发合并和完成后重新读取。
证据：`docs/work/probes/2026-10-16-research-overview-singleflight.py`。
