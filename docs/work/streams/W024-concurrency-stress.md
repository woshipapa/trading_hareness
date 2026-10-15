# W024 并发上限与压力测试

- state: building
- holder: Codex 主会话，2026-10-16
- goal: 在 owner 4 核、约 7.3 GiB 内存和名义 200 Mbps 的资源约束下，用隔离压力测试区分当前配置、软件上限和可安全提高的并发值
- next: 按 `docs/RELEASE_SYNC_47.md` 生成 owner code/config 发布计划；发布前补齐同交易日只读连接、锁等待、隧道速率和 provider 拒绝率证据
- updated: 2026-10-16
- plan: docs/work/plans/concurrency-stress.md
- branch: main
- head: a0709f24
- release: none

## Requirements

- 2026-10-15 用户要求提高并发上限前先做压力测试，选取当前配置和软件可用最高配置。
- 2026-10-15 用户要求充分利用内存、CPU core、网络带宽，同时保证正确性和可用性。
- 2026-10-15 用户补充实时量化服务应审查抓取、存储、策略运行之间的 overlap 和异步加速。
- 2026-10-16 用户明确授权：凡属于当前软件限制的并发、队列和连接池上限均可提高；只有硬件硬限制、上游 licensed 网关限制以及数据库实际连接/锁预算等硬限制保留。要求在保证正确性与可用性的前提下全部优化。

## Plan

方案见 `docs/work/plans/concurrency-stress.md`；用户已明确授权压测与软件上限扩展（2026-10-15、2026-10-16）。本线继续不向生产 provider、licensed gateway 或 owner 数据库施压，也不直接发布配置；生产默认值只在有证据证明不会突破硬限制时提高。

## Steps

- [x] 1 固化当前 compose/default 与代码上限 — `deploy/shared-peer/compose.intraday-owner.yaml`、`runtime_executors.py`、`database.py`、clamp probe
- [x] 2 编写隔离压力探针 — `docs/work/probes/2026-10-15-concurrency-stress.py`，使用真实 bounded executor 和本地合成 I/O/CPU
- [x] 3 运行当前值、阶梯值和软件最高值 — 原始输出已保存，记录吞吐、p95/p99、拒绝、CPU/RSS、occupied
- [x] 4 综合资源与既有 licensed 上限 — 形成当前生产建议、provider-safe ceiling 和软件峰值
- [x] 5 运行回归与工作记录检查 — Docker probe exit 0、JSON outputs valid、`py_compile`、`git diff --check`、notes check 0 errors
- [x] 6 盘点所有资源型软件 ceiling，区分硬限制、可扩展 ceiling 与业务正确性 cap — ADR 0028 固化分层
- [x] 7 为可扩展 ceiling 增加配置/纯函数测试并完成隔离压力验证 — 120 项 overlay 回归、扩展档和软件最大档通过
- [x] 8 运行 overlay 全量回归、组件边界检查和工作记录检查，记录变更后的软件上限与仍保留的硬限制 — overlay 3291 项仅保留既有 local-spool 未声明环境变量失败；容器边界检查通过，notes check 0 errors

## Log

- 2026-10-15 23:53 Asia/Shanghai：登记 W024。当前配置从 owner compose/default 读取，主服务 DB worker 8/queue 16、scheduler DB worker 4/queue 16、async read pool 16；代码上限为 public 32、DB 8、realtime vendor 16、async read 16。既有 F027 仍约束 licensed 路径最多 8 in-flight。
- 2026-10-15 23:58 Asia/Shanghai：主服务当前档合成 I/O 压测完成：public 8/16、realtime 4/8、DB 8/16；满 queue 的 150 ms 任务 p95 约 453–457 ms，超过容量的任务按设计拒绝，无超时，峰值 RSS 约 31.7 MiB。
- 2026-10-15 23:59 Asia/Shanghai：scheduler 当前档 DB 4/16 的 150 ms queue p95 约 757 ms、吞吐约 26.4/s；把 DB worker 提到 8 的主服务档 p95 约 453 ms，说明 scheduler 的 4 worker 是隔离预算，不应与主服务混用。
- 2026-10-16 00:00 Asia/Shanghai：软件最高档（public 32/64、realtime 16/32、DB 8/64、DB pool 32、public batch 8、minute batch 300）合成 I/O 吞吐随线程数上升，但 DB queue p95 达约 1,360 ms；合成 CPU 工作负载下 public/realtime/DB queue p95 约 1,789/1,079/2,079 ms，证明大 queue 只是隐藏背压。
- 2026-10-16 00:01 Asia/Shanghai：候选扩大档 public 12/16、realtime 8/8、DB 8/16 的合成 I/O p95 约 455/307/455 ms；public 超过 8 没有线上依据，且 F027 已测 licensed gateway 8 in-flight 约 7.4 calls/s 封顶。
- 2026-10-16 00:39 Asia/Shanghai：用户授权扩大软件 ceiling 后，代码与 owner compose 已提高：public 12 默认/64 ceiling、realtime 8/32、DB 8/16、sync pool 12/64、async read 16/32、public snapshot 4/16、Tencent surge 16/32、告警 4/16；观察池/盘口/分钟画像 owner 默认 160/80/80，代码 ceiling 200/160/200。licensed Longhu fallback 显式保持 8 in-flight。
- 2026-10-16 00:42 Asia/Shanghai：扩展默认档 overlay 合成 I/O 压测通过：public/realtime/DB queue p95 约 453.883/305.303/456.459 ms，均无 timeout；RSS 约 31.46 MiB（合成进程）。
- 2026-10-16 00:44 Asia/Shanghai：新软件最大档 public 64/128、realtime 32/64、DB 16/128 的合成 I/O 压测通过且无 timeout；queue p95 约 462.554/458.504/1367.960 ms，RSS 约 33.80 MiB，确认最大档只适合作为故障演练 ceiling。
- 2026-10-16 00:45 Asia/Shanghai：clamp 探针证明非法高值被压到 runtime public 64/128、realtime 32/64、DB 16/128、super_get 32/128、sync pool 16/64、async pool 8/32、public batch 16、watchlist/orderbook/minute 200/160/200、surge/alert 32/16；Longhu gateway software ceiling 32，licensed read ceiling 8 未改。
- 2026-10-16 00:58 Asia/Shanghai：复核剩余 I/O fan-out 后，收盘历史补齐、Longhu 单票分钟 fan-out 与盘后 Tencent 重放均提高到默认 8 路（仍分别受 licensed 8 路或 provider limiter 约束）；Sina 补行情默认 4 路；TDX research-read semaphore 默认 8、ceiling 32，单 capability 的 1 秒 pacing 保持不变。
- 2026-10-16 01:02 Asia/Shanghai：盘口观察池扩到 160 后补上 provider-aware batching：Tencent fallback 以最多 80 只/请求并行拆分，避免逻辑池扩容被单请求校验再次截断；定向 overlay 23 项通过。
- 2026-10-16 01:09 Asia/Shanghai：完整 overlay 回归运行 3291 项，除工作区既有 W016 local-spool 环境注册失败外无新增失败；容器内 `verify_component_boundaries.py --check` 通过，notes check 为 0 errors。主机缺 pytest/PyYAML/httpx，脚本 unittest/边界主机命令的依赖失败已保留为环境限制。
- 2026-10-16 01:28 Asia/Shanghai：又对照运行时默认值收紧不一致：观察池默认 160（小部署仍可降到 40）、盘口默认 80、分钟画像默认 80、收盘分类补齐默认 8；Tencent 单次 80 与 Longhu licensed 8 仍为上游硬边界。扩容相关定向 overlay 244 项仅保留同一条既有 W016 local-spool 环境注册失败。
- 2026-10-16 01:33 Asia/Shanghai：提交 `c926ef67` `perf: expand software capacity ceilings`，包含代码 ceiling、owner overlay 参数、ADR 0028、压测证据与定向回归；当前仍未向 owner 发布。
- 2026-10-16 01:34 Asia/Shanghai：`scripts/release plan c926ef67` 只读结果显示 owner 必须走 FULL release（compose/env 示例改变），当前窗口允许，owner schema gate 目标为 `20261015_ow0122`；未执行 `--apply`。
- 2026-10-16 02:33 Asia/Shanghai：继续盘点研究读取门槛：盘口逻辑池软件 ceiling 从 160 提到 200，分钟横截面研究读取上限从 50 提到 200；Tencent 单次最多 80 只仍拆为 80/80/40，licensed Longhu 仍最多 8 路。200 只盘口/分钟池定向 overlay 回归 21 项通过，未改变 provider 或交易决策边界。
- 2026-10-16 02:41 Asia/Shanghai：实时扫描的 Tencent 盘口批次改为复用已验证的 80 只上游合同；200 只观察池请求从 5 批降为 80/80/40 三批，Eastmoney 40 只批次和 licensed Longhu 8 路保持不变。新增 200 只 capture 回归与 scan overlap 定向 overlay 共 22 项通过。

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
- `docs/work/probes/2026-10-16-concurrency-stress-expanded-after-limit.out.txt`
- `docs/work/probes/2026-10-16-concurrency-stress-software-max-after-limit.out.txt`
- `docs/work/probes/2026-10-16-concurrency-setting-clamps-after-limit.out.txt`
- `quant-service/app/intraday_price_priority.py` 及 `tests.test_intraday_price_priority`：扩展盘口池按 Tencent 80 只物理请求批次拆分

## Findings

- **当前主服务建议保持**：public 12 worker/16 queue、realtime vendor 8/8、database 8/16、async read pool 16、sync DB pool 12、public snapshot concurrency 4；这些是扩展后的本地运行默认值，线上仍需 provider/DB 观测。
- **scheduler 保持独立预算**：database 4/16。它的 queue p95 比主服务高，正是盘后任务应被隔离和批量化的信号；不能把 scheduler 直接调到主服务值。
- **可试的 provider-safe ceiling**：realtime vendor 最多 8/8，Longhu licensed single-symbol fan-out 最多 8；只有在 owner 同一交易日观察 provider error/拒绝率、池等待和隧道速率后才允许把 4 提到 8。该数值受源码 `LICENSED_READ_MAX_IN_FLIGHT=8` 和 F027 约束。
- **public source 仍受 provider gate**：本地 public ceiling 已提高到 64 worker/128 queue，公开批量默认并发 4、ceiling 16；F027 的 licensed gateway 8 in-flight、约 7.4 calls/s 封顶仍优先，不能因为本地 worker 增加而突破。
- **数据库 ceiling 与运行预算分开**：DB blocking ceiling 提到 16、sync pool 64、async pool 32、waiting 128，但 owner 默认仍是 8/12/16，scheduler 仍 4；把 queue/池直接推满只会把慢任务 p95 推到约 1.37 秒，必须用线上连接、锁和隧道证据再调。
- **内存和带宽不是合成测试的瓶颈**：合成 I/O/CPU 探针进程 RSS 约 31.5–31.9 MiB，但它没有真实 provider payload、PostgreSQL 行和隧道缓冲；因此不能据此宣称 200 Mbps 已被安全利用。线上仍需记录 10 秒级网卡/隧道速率和 provider 拒绝率。

## Open questions

- owner PostgreSQL 的实时连接/锁等待和 200 Mbps 瞬时峰值尚未通过本线合成压测证明；上线前需在同一交易日只读采样。
- scheduler 与同机 relay 的真实 CPU/RSS 余量需要绑定当时 release 和 runtime source，不能由本机 synthetic ceiling 代替。
- 是否把 realtime vendor 从 4 提到 8，需要 owner 非交易时段的小批量读压测或同一交易日观察窗口；本线不替用户打生产 provider。

## Resume here

软件 ceiling 扩展、隔离验证、overlay 回归和边界检查已完成。下一步按发布流程生成 owner code/config 计划；代码/compose 尚未向 owner 执行发布，不要把本地或 synthetic ceiling 当作线上已生效配置。
