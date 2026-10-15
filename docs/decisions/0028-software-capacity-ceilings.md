# 0028 软件容量 ceiling 与硬限制分层

- 状态：生效
- 日期：2026-10-16

## 背景

owner 量化服务运行在 4 核、约 7.3 GiB 内存、名义 200 Mbps 的主机上。此前公共 I/O、实时 vendor、数据库桥接和异步读池的代码 ceiling 偏小，实时抓取、存储和策略计算的 overlap 仍有本地排队空间。2026-10-15/16 的隔离压力探针只使用本地合成 I/O/CPU，不触碰 owner 数据库、licensed gateway 或实时 provider。

## 决定

提高本地资源控制的 software ceiling，并把运行默认值与 ceiling 分开：公共源执行器为 12 worker、64 worker ceiling，实时 vendor 为 8 worker、32 ceiling，数据库阻塞桥为 8 worker、16 ceiling；队列 ceiling 分别为 128、64、128。同步数据库池 ceiling 提高到 64，异步读池 ceiling 提高到 32，等待槽 ceiling 提高到 128；Tushare-compatible GET 的本地代理池提高到 32 worker、128 queue。公开批量行情默认并发提高到 4，软件 ceiling 为 16。

观察池 admission ceiling 提高到 200，盘口到 160，分钟画像到 200；owner compose 的当前默认值为 160/80/80。盘口 fallback 对 160 只逻辑池按 Tencent 80 只物理批次并行拆分。Tencent 分钟上下文使用显式的 16 in-flight、32 ceiling；Feishu 告警默认仍为 4，ceiling 为 16。Longhu full-market gateway 的本地 worker ceiling 提高到 32。

盘后 Tencent 分钟样本重放默认 8 路、ceiling 32；Sina 补行情默认 4 路、ceiling 16。通用 TDX 研究读取的进程 semaphore 默认 8 路、ceiling 32，但同一 source/capability 的 1 秒 pacing 仍保留。收盘历史补齐和 Longhu 单票分钟 fan-out 使用 8 路，恰好对齐已测 licensed gateway ceiling；任意更高的调用参数仍会被压回 8 路。

以下限制仍是硬边界或正确性边界，不因软件 ceiling 扩展而放宽：Longhu 物理页 300 行、Tencent 批量协议 80 个标的、licensed 分时 gateway 最多 8 个 in-flight、provider rate limiter、数据库真实连接/锁预算、TDX shadow 与竞价采集的单写者顺序，以及研究 fail-closed 和人工观察池审批边界。

## 后果

新 ceiling 允许在不改代码的情况下利用更多网络 I/O 和内存，但大 queue 仍可能增加尾延迟。owner 的数据库 worker、异步池和批量 tunnel 默认值暂不盲目推到 ceiling；它们必须结合连接/锁等待和隧道速率再调整。compose 改动属于 owner 的完整 release 输入，代码改动可走 code-only overlay；本记录本身不表示已经发布到 owner。

## 怎样保证

隔离压测 `docs/work/probes/2026-10-16-concurrency-stress-expanded-after-limit.out.txt` 在扩展默认档下无 timeout，公共/实时/数据库 queue p95 约 454/305/456 ms；软件最大档的数据库 queue p95 约 1,368 ms，说明 ceiling 是故障演练边界而不是应长期堆满的 queue。clamp 探针 `docs/work/probes/2026-10-16-concurrency-setting-clamps-after-limit.out.txt` 证明环境值不能越过各 ceiling。overlay 回归必须通过，生产发布仍需记录 runtime source、provider 拒绝率、池等待、CPU/RSS 和 tunnel 速率。
