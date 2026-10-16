# W026 盘中 SSD 写入与盘后 Windows 回放

- state: verified
- holder: Codex 主会话，2026-10-16
- goal: 在保证点时语义、幂等和研究只读边界的前提下，把高频盘中证据先安全落到服务器 SSD，并在收盘后批量回放到 Windows PostgreSQL
- next: 下一交易日收盘后核对 pending/done 水位、回放批次和 Windows 隧道耗时；若无 pending 记录，保持本线 verified，不做人工造数
- updated: 2026-10-16
- plan: docs/work/plans/intraday-ssd-writeback.md
- branch: main
- head: 2dd12842
- release: owner-full-20261016-ssd；运行开关配置随后由 env-split 生成并同步

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
- [x] 5 接入共享 SSD 挂载、运行时健康状态和盘后实际回放开关 — owner 运行时 — 两个 quant 容器共享 named volume，主服务 writer=true/replay=false，scheduler writer=true/replay=true；当前无 pending 数据可做真实回放
- [x] 6 运行 focused、release-path、边界和故障演练测试 — 20 项 focused overlay、编译、边界、diff check 通过
- [x] 7 生成 owner 发布计划并验证 owner 运行时 — owner-full-20261016-ssd；主服务和 scheduler SHA、schema、health、volume、开关均已核对

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
- 2026-10-16：FULL release `owner-full-20261016-ssd` 已在 owner 生效，主服务与 scheduler 均运行 `a38d1bae00c84a49ff6efee798b581379fc8e41f`、schema `20261016_ow0130`、health=ok；两个容器共享 `trading-hareness-peer_peer_intraday_spool` → `/var/lib/quant/intraday-spool`，根盘可用约 43 GB（35% used）。
- 2026-10-16：通过集中式 `config/secrets/sync-secrets.sh owner` 同步运行开关并重建两个容器（`--no-build --pull never`）：主服务 `INTRADAY_EVIDENCE_SPOOL_ENABLED=true`、`REPLAY_ENABLED=false`；scheduler 两者均 true。两个端口的 `/health` 均 ok，spool_bytes=0；scheduler 当前慢事务累计 2 次、主服务 0 次，未发现本次 spool 引入的错误。

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

- 下一交易日盘后需要有一批真实采集记录后，才能测量 pending→done 的实际回放耗时；当前 `spool_bytes=0` 只证明水位为空，不证明 Windows 回放吞吐。

## Resume here

SSD spool 和 paced replay 已发布并启用：盘中主服务先落 SSD，独立 scheduler 以 100 条上限的 paced batch 回放。下一步只需在下一交易日盘后读取 pending/done、回放延迟、statement count 和 Windows 隧道速率；不要把当前空 spool 当成真实回放吞吐证据。
