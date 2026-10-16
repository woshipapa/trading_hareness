# W026 盘中 SSD 写入与盘后 Windows 回放

- state: building
- holder: Codex 主会话，2026-10-16
- goal: 在保证点时语义、幂等和研究只读边界的前提下，把高频盘中证据先安全落到服务器 SSD，并在收盘后批量回放到 Windows PostgreSQL
- next: 收盘后读取 owner SSD 水位并执行共享挂载/盘后 replay 的只读 preflight；通过后再生成 full release plan
- updated: 2026-10-16
- plan: docs/work/plans/intraday-ssd-writeback.md
- branch: main
- head: 34cccadf
- release: none

## Requirements

- 2026-10-16 用户授权：利用内存缓存保留更新和 SSD，晚上统一落到 Windows PostgreSQL，并开始写代码。
- 保留实时策略计算、信号去重、点时可用性、fail-closed 和 owner 单写边界。

## Plan

见 `docs/work/plans/intraday-ssd-writeback.md`。当前先实现默认关闭、可回滚的最小切片。

## Steps

- [x] 1 建立方案、边界和不变量 — working tree — 已记录本线设计
- [x] 2 实现原子 SSD spool、校验和、状态扫描与幂等回放 — working tree — focused overlay 11 项通过
- [x] 3 接入 `watch_scan_tape` 的显式开关 — working tree — 默认关闭，spool route 回归通过
- [x] 4 接入持续 paced replay loop 和研究调度器任务目录 — working tree — 每批受 `max_batch` 限制，失败退避
- [ ] 5 接入共享 SSD 挂载、运行时健康状态和盘后实际回放
- [x] 6 运行 focused、release-path、边界和故障演练测试 — 20 项 focused overlay、编译、边界、diff check 通过
- [ ] 7 生成 owner 发布计划并在用户执行后验证真实回放

## Log

- 2026-10-16：根据用户授权开始实现。审查确认 `local_spool_level1` 是故障备用路径，不能直接承担主 evidence 写入；`watch_scan_tape` 是首个低风险高频 raw 写入切入点。
- 2026-10-16：新增 `intraday_evidence_spool`，以 immutable envelope、SHA-256、fsync、原子 rename、pending/done/quarantine 目录实现 SSD durable staging；`watch_scan_tape` 通过显式开关接入，默认仍写 PostgreSQL。
- 2026-10-16：新增 `intraday_evidence_replay` research task。它通过 bounded database executor 使用 batch lane，按小批量循环回放；成功提交后才清理文件，数据库失败保留 SSD 并退避。
- 2026-10-16：增加 done 文件恢复窗口和定期清理；默认保留 7 天，每 20 个 replay pass 执行一次清理，避免 SSD 无限增长。
- 2026-10-16：将默认 replay batch 收紧为 100 个 envelope，并加入进程内 digest/path cache；重复重试在同一进程不重复写 SSD，磁盘不足时回退原 PostgreSQL 写入并记录 warning。
- 2026-10-16：compose 为采集容器和 research scheduler 增加独立共享 volume `/var/lib/quant/intraday-spool`；默认 writer/replay 开关仍关闭。该 host-tree 变更需要 FULL release，尚未部署。
- 2026-10-16：focused overlay `tests.test_intraday_evidence_spool tests.test_watch_scan_tape tests.test_background_task_catalog` 共 14 项通过；`py_compile`、组件边界和 `git diff --check` 通过。
- 2026-10-16：固定 L0 `CaptureBatch`、L1 `evidence segment`、L2 stream registry 的格式和目标表/幂等键；新增 capture contract、stream contract 和固定 replay adapter。默认 replay 仍只允许 `raw_market_observations`，quote 因 scan FK 保持 gated，其他 adapter 先不启用。
- 2026-10-16：将 L1 writer 对齐为 append-only JSONL segment（header/records/footer、内容 hash、fsync、原子封存），内存 recent envelope cache 同时受条数和 256 MiB 字节上限约束；回放按 record 上限拆分超大 segment，避免单次批量突破数据库预算。
- 2026-10-16：focused overlay 回归 19 项通过，包含多记录 segment、拆分回放、payload hash 校验和磁盘满回退。
- 2026-10-16：按真实 schema 修正分钟目标为 `quant.intraday_minute_sessions`；新增 raw/quote/board/minute 固定 replay adapters 和 stream gate，默认只启用 raw，新增 adapter focused tests 后总计 34 项通过。
- 2026-10-16：全量 quant release-path 3348 项运行，新增代码未引入本线失败；仍有工作树既有的 2 项失败：未登记的 `local_spool_*` 设置键，以及动态 strategy governance 期望值漂移。
- 2026-10-16：完整 overlay 运行 3342 项，失败 2 项：共享工作树既有的 `local_spool_*` 设置未登记，以及与本线无关的动态 strategy governance 断言；本线 focused suite 未失败。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/work/plans/intraday-ssd-writeback.md`
- `docs/work/plans/realtime-strategy-overlap.md`
- `docs/decisions/0032-batched-realtime-scan-evidence.md`
- `docs/decisions/0034-realtime-scan-two-phase-persistence.md`
- `quant-service/app/intraday_evidence_spool.py`
- `quant-service/app/intraday_evidence_adapters.py`
- `quant-service/tests/test_intraday_evidence_spool.py`
- `quant-service/app/intraday_capture_contracts.py`
- `quant-service/app/intraday_evidence_streams.py`
- `quant-service/tests/test_intraday_capture_contracts.py`
- `quant-service/tests/test_intraday_evidence_streams.py`
- `quant-service/tests/test_intraday_evidence_adapters.py`
- `deploy/shared-peer/compose.yaml`
- `deploy/shared-peer/compose.intraday-owner.yaml`

## Open questions

- owner 主机 SSD 的可用空间、高水位和保留天数需要在发布前读取真实运行时配置。
- 当前 replay loop 需要两个服务共享 spool 目录；compose 的 host-volume 变更属于 owner host-tree/full release，尚未执行。

## Resume here

SSD spool 和 paced replay 的代码切片已完成，默认开关仍关闭。下一步是给 intraday 与 research scheduler 挂载同一个 host SSD 目录，开启 shadow 模式并复测数据库 statement count、隧道速率、pending 水位和回放延迟；不要把本地测试当成线上回放证据。
