# 并发上限与压力测试方案

## 授权与边界

2026-10-15 用户明确要求“当前可以考虑提高并发上限，你可以去做一个压力测试，然后选取我们当前配置下以及软件可以的最高配置”。2026-10-16 用户进一步授权：软件限制全部可以提高，硬件硬限制保留。本方案只在本地隔离环境压测 quant-service 的有界执行器和异步编排，读取当前 compose/default 配置并与代码允许上限对比；不向 owner 生产数据库、实时供应商或 licensed 网关施压，不突破已知 6–8 in-flight 授权接口上限。

## 资源基线

owner 目前按 F053 记录为 4 核、约 7.3 GiB 内存、约 50 GiB 可用磁盘，名义带宽 200 Mbps。主实时服务数据库阻塞池为 8 个 worker、16 个排队槽；scheduler 为 4 个 worker、16 个排队槽；异步读池运行默认 16；公共源执行器默认 12、实时 vendor 执行器默认 8，公开批量快照默认并发 4。代码 ceiling 分别为公共源 64/128 queue、数据库 16/128 queue、实时 vendor 32/64 queue、异步读池 32、同步 DB pool 64；licensed 分时仍受 8 in-flight 硬限制。

## 测试阶梯

1. 使用真实 `BlockingExecutorBoundary`，以本地可控的 I/O 等待动作替代外部 provider/数据库，分别测 public、realtime vendor、database 的当前值、半阶值和软件上限。
2. 以低延迟和慢 I/O 两种持续时间发起超过容量的有界波次，记录吞吐、p50/p95/p99、饱和拒绝、峰值 occupied、进程 CPU 时间和 RSS。
3. 将实时主服务的三类边界叠加运行，观察把所有值推到软件上限时是否出现拒绝、长尾、内存增长或 CPU 饱和。
4. 将异步读池、同步连接池和 provider-specific semaphore 的静态 clamp 与压测结果合并，区分“软件可接受上限”和“当前 owner 安全配置”。

## 选择规则

- 安全值必须在目标 offered load 下无饱和拒绝，p95/p99 不发生阶跃，RSS 留出 scheduler 与同机 relay 的余量，且不把数据库/供应商瓶颈隐藏在队列中。
- 软件最高值只作为单项边界和故障演练值；不因为 200 Mbps 就提高 licensed 并发，既有 F027 的 8 in-flight/约 7.4 calls/s 上限优先。
- 若合成 I/O 压测无法证明数据库、隧道或上游安全性，报告只标为 synthetic ceiling，并保留线上同交易日复测门槛，不直接改生产 env。
- 用户已授权提高软件 ceiling；因此代码 clamp、默认配置和资源队列会逐项提高，但只有经过隔离压测且不等价于硬件/上游/数据库预算的项才进入运行默认值。业务语义 cap（样本完整性、单写者顺序、研究 fail-closed、provider physical page size）不因并发授权而放宽。

## 验证

保存探针脚本和原始输出到 `docs/work/probes/`；运行相关 quant overlay 测试、`git diff --check` 和工作记录检查。任何配置发布都另行走 owner code/config release，并绑定 runtime source、release、拒绝率、池等待、RSS、隧道速率和 provider 错误率。
