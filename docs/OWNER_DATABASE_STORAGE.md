# Owner database storage, tunnel and data semantics

This is the peer-side contract for the owner cutover. The 2026-09-20 owner
clarification below supersedes the earlier handoff assumptions about
materialized semantic columns and market-data cold twins. The peer is
read-only against the owner database: it never runs owner migrations, moves
rows, changes tablespaces or repairs owner factors.

## Owner clarification (2026-09-20, authoritative)

The peer is connected to the intended production database `trading_hareness`.
The owner reports one PostgreSQL 16 instance at
`F:\\StockPlatformDB\\postgresql16` and lineage
`quant.alembic_version=20260919_0106`. The peer startup contract is limited to
the columns it actually reads:

| Relation | Required runtime columns |
| --- | --- |
| `quant.canonical_bars_daily` | `symbol`, `trading_date`, `adj_factor`, `available_at`, `quality_status` |
| `quant.daily_adjustment_factors` | `symbol`, `trading_date`, `adj_factor`, `provider`, `available_at`, `raw` |

`adjustment_state` is a computed daily control-plane value, not a persisted
column. Factor semantics are read from `daily_adjustment_factors.raw`; the
Longhu method is `longhu_cq_preclose_qfq_v2`. `adj_factor` is intentionally
`NOT NULL`, and a missing factor date is represented by no row. The peer does
not require `factor_semantics`, `retired_at`, adjustment guard indexes, or an
owner `/api/v1/research/storage-tiers` endpoint.

The owner's cold layer is operational evidence only. Its cold twins are
`edge_evidence_changes_cold`, `intraday_quote_observations_cold`,
`intraday_rule_input_snapshots_cold`, `raw_market_observations_cold`, and
`tushare_raw_records_cold`; peer research must not depend on those relations
or require access to them. `legacy_source_records` remains in the owner's
`stock_cold` tablespace. The peer's storage-tiers route is a local,
read-only compatibility diagnostic, not an owner API contract.

## Historical handoff (superseded, 2026-09-19)

The earlier handoff described five market-data cold twins and materialized
semantic columns. The owner has explicitly corrected that description; those
items are not part of the production schema and must not be used as peer gates.

The peer is revalidated after the owner-compatible startup gate and SQL
readers pass local tests. The peer still does not run owner migrations or data
repair.

## Storage layers

| Layer | Owner location | Peer contract |
| --- | --- | --- |
| Hot | `F:\StockPlatformDB\postgresql16` (NVMe) | `5432` is the low-latency/session path; current rows, indexes, WAL and temp files stay here. |
| Cold | owner-managed evidence tablespace | Evidence/audit cold twins are owner operations only; peer runtime has no market-data cold-table dependency. |
| Legacy | `legacy_source_records` on `stock_cold` | Whole-table cold evidence; the relation keeps its name and is never used by an intraday decision. |
| Backup | `G:\StockPlatform\backups` and offline upload | Logical dump plus raw-observation increment chain; a tier job must verify a read-back before pruning hot rows. |

Rows older than 365 days may be moved by the owner job
`trading-hareness-storage-tiers` (06:00 Asia/Shanghai). The job is bounded by
85% trigger / 75% target, seven days per table per run and a 30-day floor. It
must report `needs_repack` rather than issue an automatic `VACUUM FULL`.

The peer endpoint `/api/v1/research/storage-tiers` and `/health.owner_storage`
report `owner_compatible` only when the two runtime relation contracts and
database lineage are readable. Factor semantics are checked in query
predicates against `raw`; no owner guard index or cold-table scan is part of
the startup gate. Missing factor rows remain missing and block adjusted
research prices.

## Peer tiering policy (2026-09-22)

The peer publishes which of its evidence is needed by same-day live
computation and how many sessions each stream stays hot:
`GET /api/v1/research/storage-tiering` (`app/storage_tiering_policy.py`).
The all-A Level-1 snapshot (~1.5 GB a session, ~95% of raw growth) stays hot
for 2 sessions; intraday replay evidence for 5; sampled quote/rule-input
evidence for 10; daily bars, reviews and supplements stay hot. The intended
transfer is copy-after-close into the owner's cold twin, then delete from hot
only after the hot window and a verified cold copy (same key and payload
hash), outside 09:00-15:30 on trading days. The owner runs it; the peer never
moves rows. The peer's hot budget measures the whole `quant` schema today,
including relations already on `stock_cold`.

## Two database lanes

- `5432` (`db-tunnel`) is for intraday/session reads and bounded realtime work.
- `5433` (`db-tunnel`) is the owner batch forward for migrations, COPY/backfill,
  backups and full replay. It reaches owner `15433 -> 55432`; its scheduled
  task is `trading-hareness-shared-peer-batch-tunnel`. The legacy
  `db-batch-tunnel` sidecar remains optional compatibility infrastructure and
  is not the scheduler's default route.
- A reconnect on one lane must not reset the other. The peer scheduler uses
  `PEER_RESEARCH_DB_HOST=db-tunnel`, `PEER_RESEARCH_DB_PORT=5433` after the
  owner task is verified; intraday remains on `db-tunnel:5432`.
- The scheduler entrypoint infers `PEER_REQUIRE_OWNER_CUTOVER=true` when its
  database lane is `db-tunnel:5433`; it reads the minimal owner runtime schema
  before starting and exits unless the result is `owner_compatible`.
- The peer session guard treats the compatibility sidecar as optional. The
  normal db-tunnel health check and the verifier's in-container `5432`/`5433`
  SQL read-back are the authoritative lane checks.

Both lanes use `ExitOnForwardFailure`, keepalive, strict host-key checking and
compression. The batch lane carries no API forward and no provider credential.

## Instrument writes

`app/instrument_registry.py` is the only application owner of instrument
writes. It deduplicates symbols, sorts by symbol and executes one `unnest`
upsert. Daily normalization, sector membership, public events, remote analyst
claims, legacy imports, personal decision facts, universe maintenance,
watchlist registration and offline imports use it. Each statement is wrapped
by `instrument_lock_retry.py`: a 300 ms `lock_timeout`, savepoint rollback,
and SQLSTATE-limited (55P03/40P01) retry with a 60-second total budget. Sparse
`is_st` compatibility runs are contiguous slices of the sorted symbols, so
every statement still acquires row locks monotonically. Identical ordering
plus bounded retries addresses the owner's 9/18 deadlock chain without
retrying arbitrary data or network errors.

## Adjustment-factor semantics

The peer exposes a four-state computed evidence value for callers, but it is
not a column in the owner schema:

- `complete`: positive cumulative factor from `longhu_qfq_derived` or an
  explicitly allow-listed Tushare checkpoint provider, available before the
  strategy/session boundary;
- `pending`: a repair is expected but no factor is usable;
- `retired`: an audit row exists but is not eligible for research;
- `absent`: no row is present in `daily_adjustment_factors`.

Longhu/Tencent close data no longer writes `adj_factor=1`. A same-day identity
value is not corporate-action history. Research-price joins accept only the
explicit cumulative-factor contract and fail closed when the factor is missing.
The owner 04:30 task derives missing dates from Longhu 前复权 K-lines, records
`provider=longhu_qfq_derived`, then repairs and verifies canonical rows; five
evening coverage failures retire a date and record a data-quality issue instead
of inventing a factor.

The post-close control synchronizer now reads that persisted factor cross-section
through `app/owner_factor_repository.py`; it does not issue a Tushare
`adj_factor` request. Tushare remains an explicitly allowed checkpoint source
when already persisted, but it is never used by this control stage to overwrite
the owner's Longhu-derived series. `daily_basic`, `stk_limit` and `suspend_d`
remain separate same-date control inputs. On-demand stock study and watchlist
hydration use the same persisted factor projection; the only remaining direct
Tushare factor request is an explicitly injected legacy compatibility path, not
an owner runtime task.

The shared research-price helper applies the same rule to projected rows. SQL
reads require a positive factor, an allow-listed provider, and either
`raw->>'factor_semantics'` in the cumulative set or the owner's Longhu method
`longhu_cq_preclose_qfq_v2`; a missing factor row blocks cross-session returns.
The bounded factor loader and ten-day ranking resolve one complete cumulative
factor before calculating research prices.

The peer's `QUANT_SKIP_MIGRATIONS=true` remains mandatory. New peer releases
also set `PEER_REQUIRE_OWNER_SEMANTICS=true`: the entrypoint performs a
read-only runtime-schema check before starting any SQL worker. This prevents
late failures from selecting nonexistent owner columns while requiring no
owner DDL. The owner release pipeline remains independent and must not be
invoked by the peer.
The bootstrap contract has a read-only `-DryRun` audit for operator review,
including unexpected shared database/tablespace ownership,
then first reassigns any objects previously owned by the
peer role to the owner admin, because ACL revocation cannot remove owner
privileges. It rejects unexpected peer-owned databases/tablespaces first and
restores ownership of the deliberately separate peer n8n database after
`REASSIGN OWNED`, which otherwise also changes shared database objects. It then
revokes the peer role's database/schema CREATE, table DML,
sequence, function and procedure privileges before granting back only schema
USAGE and table SELECT. It revokes every parent-role membership and then reads
the membership count, ownership, write-capable relation count and writable
sequence count back. It also sets and verifies
`NOINHERIT` as a second layer; this is not treated as a replacement for removing
explicit `SET ROLE` paths. The cutover verifier requires zero memberships and
checks the same privileges over the 5433
lane; being merely non-superuser is not sufficient evidence of read-only
safety. It also requires `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`,
`NOREPLICATION`, `NOBYPASSRLS`, plus the 15-minute/5-minute timeout pair. A
single DML-capable relation or writable sequence anywhere in `quant`
blocks the lane; checking only the canonical bar table would not prove the
evidence store is read-only. The verifier also rejects executable `SECURITY DEFINER` routines in
the `quant` schema, since table ACLs alone do not prove that such a routine
cannot mutate owner data.

Peer releases do not assume an owner storage migration order. Owner cold
evidence maintenance and the peer's runtime read contract are independent;
the peer never unions owner market-data cold twins or checks guard indexes.

The owner database has its own Alembic lineage (the current remote readback is
`20260919_0106`), while this repository's peer-safe research lineage is local.
The peer must never try to upgrade the owner database or require owner-side
semantic DDL.
The batch verifier also reads `quant.alembic_version` over 5433; a bare
successful `canonical_bars_daily` query is not sufficient evidence that the
bulk lane points at the intended owner database. The storage endpoint now
returns the same non-secret database name and latest Alembic version over the
session/API lane; `lane_lineage` must match both values before either lane is
accepted as the same owner instance.
