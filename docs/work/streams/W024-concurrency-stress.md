# W024 并发上限与压力测试

- state: building
- holder: Codex 主会话，2026-10-16
- goal: 在 owner 4 核、约 7.3 GiB 内存和名义 200 Mbps 的资源约束下，用隔离压力测试区分当前配置、软件上限和可安全提高的并发值
- next: 由用户决定是否在 owner 非交易时段做 realtime vendor 4→8 的小批量观察；若执行，再绑定 release 和线上资源门槛
- updated: 2026-10-16
- plan: docs/work/plans/concurrency-stress.md
- branch: main
- head: 13bde20b
- release: none

## Requirements

- 2026-10-15 用户要求提高并发上限前先做压力测试，选取当前配置和软件可用最高配置。
- 2026-10-15 用户要求充分利用内存、CPU core、网络带宽，同时保证正确性和可用性。
- 2026-10-15 用户补充实时量化服务应审查抓取、存储、策略运行之间的 overlap 和异步加速。

## Plan

方案见 `docs/work/plans/concurrency-stress.md`；用户已明确授权压测与配置选择（2026-10-15）。本线不向生产 provider、licensed gateway 或 owner 数据库施压，也不直接发布配置。

## Steps

- [x] 1 固化当前 compose/default 与代码上限 — `deploy/shared-peer/compose.intraday-owner.yaml`、`runtime_executors.py`、`database.py`、clamp probe
- [x] 2 编写隔离压力探针 — `docs/work/probes/2026-10-15-concurrency-stress.py`，使用真实 bounded executor 和本地合成 I/O/CPU
- [x] 3 运行当前值、阶梯值和软件最高值 — 原始输出已保存，记录吞吐、p95/p99、拒绝、CPU/RSS、occupied
- [x] 4 综合资源与既有 licensed 上限 — 形成当前生产建议、provider-safe ceiling 和软件峰值
- [x] 5 运行回归与工作记录检查 — Docker probe exit 0、JSON outputs valid、`py_compile`、`git diff --check`、notes check 0 errors

## Log

- 2026-10-15 23:53 Asia/Shanghai：登记 W024。当前配置从 owner compose/default 读取，主服务 DB worker 8/queue 16、scheduler DB worker 4/queue 16、async read pool 16；代码上限为 public 32、DB 8、realtime vendor 16、async read 16。既有 F027 仍约束 licensed 路径最多 8 in-flight。
- 2026-10-15 23:58 Asia/Shanghai：主服务当前档合成 I/O 压测完成：public 8/16、realtime 4/8、DB 8/16；满 queue 的 150 ms 任务 p95 约 453–457 ms，超过容量的任务按设计拒绝，无超时，峰值 RSS 约 31.7 MiB。
- 2026-10-15 23:59 Asia/Shanghai：scheduler 当前档 DB 4/16 的 150 ms queue p95 约 757 ms、吞吐约 26.4/s；把 DB worker 提到 8 的主服务档 p95 约 453 ms，说明 scheduler 的 4 worker 是隔离预算，不应与主服务混用。
- 2026-10-16 00:00 Asia/Shanghai：软件最高档（public 32/64、realtime 16/32、DB 8/64、DB pool 32、public batch 8、minute batch 300）合成 I/O 吞吐随线程数上升，但 DB queue p95 达约 1,360 ms；合成 CPU 工作负载下 public/realtime/DB queue p95 约 1,789/1,079/2,079 ms，证明大 queue 只是隐藏背压。
- 2026-10-16 00:01 Asia/Shanghai：候选扩大档 public 12/16、realtime 8/8、DB 8/16 的合成 I/O p95 约 455/307/455 ms；public 超过 8 没有线上依据，且 F027 已测 licensed gateway 8 in-flight 约 7.4 calls/s 封顶。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/work/plans/concurrency-stress.md`
- `docs/work/FACTS.md` F027、F053
- `deploy/shared-peer/compose.intraday-owner.yaml`
- `quant-service/app/runtime_executors.py`
- `quant-service/app/database.py`
- `docs/work/probes/2026-10-15-concurrency-stress.py`
- `docs/work/probes/2026-10-15-concurrency-stress-owner-main.out.txt`
- `docs/work/probes/2026-10-15-concurrency-stress-owner-scheduler.out.txt`
- `docs/work/probes/2026-10-15-concurrency-stress-candidate-expanded.out.txt`
- `docs/work/probes/2026-10-15-concurrency-stress-software-max.out.txt`
- `docs/work/probes/2026-10-15-concurrency-stress-owner-main-cpu.out.txt`
- `docs/work/probes/2026-10-15-concurrency-stress-software-max-cpu.out.txt`
- `docs/work/probes/2026-10-15-concurrency-setting-clamps.out.txt`

## Findings

- **当前主服务建议保持**：public 8 worker/16 queue、realtime vendor 4/8、database 8/16、async read pool 16、sync DB pool 12、public snapshot concurrency 2、Longhu minute batch 4。此档在合成慢 I/O 下没有超时，且 queue p95 仍小于软件最高档。
- **scheduler 保持独立预算**：database 4/16。它的 queue p95 比主服务高，正是盘后任务应被隔离和批量化的信号；不能把 scheduler 直接调到主服务值。
- **可试的 provider-safe ceiling**：realtime vendor 最多 8/8，Longhu licensed single-symbol fan-out 最多 8；只有在 owner 同一交易日观察 provider error/拒绝率、池等待和隧道速率后才允许把 4 提到 8。该数值受源码 `LICENSED_READ_MAX_IN_FLIGHT=8` 和 F027 约束。
- **public source 不因带宽提高**：软件上限是 32，但 F027 证明 licensed gateway 在 8 in-flight、约 7.4 calls/s 已封顶，16 会被拒绝；public worker 先保持 8。`MARKET_SNAPSHOT_PUBLIC_CONCURRENCY` 先保持 2，最多只做带 provider error gate 的 4 候选实验。
- **数据库不提高 worker/pool**：DB blocking 已到代码上限 8，sync pool 12、async read pool 16 已有界；把 queue 从 16 提到 64 只把慢任务的 p95 推到 1.36–2.08 秒。软件 pool 上限 32 是故障演练边界，不是 owner 推荐值。
- **内存和带宽不是合成测试的瓶颈**：合成 I/O/CPU 探针进程 RSS 约 31.5–31.9 MiB，但它没有真实 provider payload、PostgreSQL 行和隧道缓冲；因此不能据此宣称 200 Mbps 已被安全利用。线上仍需记录 10 秒级网卡/隧道速率和 provider 拒绝率。

## Open questions

- owner PostgreSQL 的实时连接/锁等待和 200 Mbps 瞬时峰值尚未通过本线合成压测证明；上线前需在同一交易日只读采样。
- scheduler 与同机 relay 的真实 CPU/RSS 余量需要绑定当时 release 和 runtime source，不能由本机 synthetic ceiling 代替。
- 是否把 realtime vendor 从 4 提到 8，需要 owner 非交易时段的小批量读压测或同一交易日观察窗口；本线不替用户打生产 provider。

## Resume here

压力测试和记录已经完成。当前生产建议保持主服务 public 8/16、realtime 4/8、DB 8/16、async 16、sync pool 12；scheduler DB 4/16。软件峰值只作为故障演练，provider-safe 试验上限是 realtime 8/8，仍需用户安排非交易时段的小批量线上观察；不要把 synthetic ceiling 当作已发布配置。
