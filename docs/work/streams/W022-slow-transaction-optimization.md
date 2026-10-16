# W022 慢事务与慢流程优化

- state: building
- holder: Codex 主会话，2026-10-15
- goal: 在保持研究只读、幂等、点时边界和可用性的前提下，降低 owner quant scheduler 的慢事务、数据库往返和长事务风险，并为后续带宽与并发调优提供可复现实测基线
- next: 在发布窗口内先完成 owner 0123--0130 schema gate，再执行当前目标所需的 FULL release；上线后按同一交易日回填线上慢事务与资源证据
- updated: 2026-10-16
- plan: docs/work/plans/slow-transaction-optimization.md
- branch: main
- head: 204f53b6
- release: none

## Requirements

- 2026-10-15 用户授权：可以把这些慢事务，慢的流程在保证正确性和可用性的基础上全部优化！！！
- 保留研究平台的 fail-closed 规则、strategy_available_at/截止时间语义、幂等写入和 owner 单写边界。
- 不以提高 provider 并发、突破 owner 200 Mbps 或增加数据库连接数代替代码优化；先减少语句数、网络往返和事务持锁时间。

## Plan

见 `docs/work/plans/slow-transaction-optimization.md`。本线只修改本地源码、测试和工作记录；owner 发布和数据库写入仍按仓库规则由用户执行。

## Steps

- [x] 1 建立慢事务调用点和本地回归基线 — W021 owner 探针与 F053
- [x] 2 批量化 intraday outcome settlement 的行情读取和幂等写入 — PricePaths 预取、三类 outcome 批写
- [x] 3 批量化 outcome recomputation 的逐行写入并降低逐标的读取 — LATERAL forward window、两类 outcome 批写
- [x] 4 检查 daily close、market temperature 和健康读路径的长查询/长事务 — controls、Xiaojie limits、strategy health 与索引迁移
- [ ] 5 运行发布路径测试、边界测试、前端检查和 diff 检查（focused 已通过；全量 3327 项仍有共享 `local_spool_*` settings failure）
- [ ] 6 生成 owner 发布计划并按窗口执行；等待用户先应用 schema gate，再回填线上 statement count、耗时、RSS 和拒绝率

## Log

- 2026-10-15 Codex：从 W021 的线上探针确认 scheduler 的 `intraday_outcome_runtime:recompute` 曾在单事务中执行 117,615 条语句、约 7,307.7 秒；本线先做批量读写和事务边界改造。
- 2026-10-15 Codex：intraday settlement 已按交易日/标的预取 quote/tape 和 daily exits，barrier、minute horizon、close/next-close 改为批写；旧连接替身仍逐行回退。
- 2026-10-15 Codex：outcome recomputation 已用一次 LATERAL 请求窗口查询替代逐标的 forward-bar 查询，claim/recommendation upsert 改为批写；daily close 的 `stk_limit`/`adj_factor` 和 Xiaojie session limits 同样批量化。
- 2026-10-15 Codex：strategy health sync/async 路径合并为单快照查询；新增 `20261015_ow0122` 索引迁移，需 owner schema gate 应用后再发布依赖索引的代码。
- 2026-10-15 Codex：资源边界按 owner 4 核/约 7.3 GiB/名义 200 Mbps 固定：批量预取消耗短生命周期内存，纯结算消耗本地 CPU，跨隧道只发送集合查询和 executemany；主服务数据库阻塞池 8、scheduler 4、公共源 8，均保持有界。
- 2026-10-15 Codex：相关 overlay 回归 133 项通过；前端 `typecheck` 和 `build` 通过，`git diff --check` 通过。全量 quant overlay 为 3278 项、仅剩其他会话新增 `local_spool_*` 环境变量未登记这一项；宿主缺少 pytest/PyYAML/httpx，脚本套件的规定命令无法在当前 Python 环境启动，unittest 替代运行 388 项时只命中这些依赖缺口及既有 teacher harness 问题。
- 2026-10-16 09:08 Asia/Shanghai：开盘前冻结发布边界。已完成的批量 settlement、outcome recompute、只读快照、实时 overlap 和容量 ceiling 改动均已在本地 release-path/前端回归中验证，但尚未向 owner 生效；`scripts/release-sync-status.sh` 仍观测 owner 运行 SHA `bfd180e35a5b`、Alembic `20261015_ow0122`，不能把 health=ok 视为新代码已发布。
- 2026-10-16 09:08 Asia/Shanghai：新增 intraday attribution 的 `fetchmany`/`executemany` adapter 已通过 focused overlay 84 项，代码和 ADR 尚未纳入发布；目标版本需要 `20261016_ow0130`，而当前发布计划要求先应用 0123--0130，并因 host-tree 路径判为 FULL。当前北京时间 08:30--11:30 禁止 owner 重启，故本次只记录待发布项，不执行 `--apply`。
- 2026-10-16 09:10 Asia/Shanghai：本轮全量 release-path 运行 3327 项，唯一失败仍为共享工作树 `local_spool_fallback.py`/`local_spool_level1.py` 读取未在 `app/settings.py` 登记的环境变量；该失败与本线文件无关，故保留为发布前门禁待清理项。

## Waiting on the user

## Running

## Due

## Evidence

- `docs/archive/2026-10-15-platform-architecture-audit.md`
- `docs/work/probes/2026-10-15-platform-audit-owner.out.txt`
- `docs/work/FACTS.md` F053

## Open questions

- owner PostgreSQL 经 db-tunnel 的单次往返和锁等待，需要代码发布后按同一交易日复测；本地测试不能替代线上运行证据。
- 是否要把夜间大计算拆到独立进程，要在批量化后观察 scheduler RSS 和事务时长再决定。
- owner 应先应用 `20261015_ow0122` schema gate，再以独立 component SHA 发布；发布后必须复测同一交易日的 statement count、事务耗时、scheduler RSS、隧道速率和 provider 拒绝率。

## Resume here

先清理共享 `local_spool_*` settings 门禁并重跑全量 release-path；然后在允许窗口先应用 `20261016_ow0123`--`20261016_ow0130`，再执行 release plan 指定的 owner FULL release，最后回填线上慢事务与资源证据。
