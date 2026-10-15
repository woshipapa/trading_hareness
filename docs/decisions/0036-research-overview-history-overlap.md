# Decision 0036: overlap the research overview history estimate

## Context

`GET /api/v1/research/overview` is a read-only dashboard projection. Its
overview snapshot itself is bounded and fast, but the historical capacity
estimate previously ran through the synchronous database executor first. The
estimate then scanned millions of canonical bars for daily coverage and
counted the independent fundamentals, limits and minute symbol sets in the
same serial read path.

On the local compose database, the pre-change endpoint was about 7.8--8.9 s.
The canonical coverage query alone took about 7.7 s before the new index. A
partial `(trading_date, symbol)` index for valid canonical rows, narrow
`symbol` indexes for the two cardinality counts, and row counting under the
canonical `(symbol, trading_date)` primary key preserve the exact coverage
definition while removing the avoidable DISTINCT sort.

## Decision

1. `canonical_bars_daily_coverage_idx` is the owner schema index for the
   valid canonical coverage grouping. `daily_fundamentals_symbol_count_idx`
   and `daily_trade_limits_symbol_count_idx` serve the display-only distinct
   symbol cardinalities. Migrations `20261016_ow0128` and `20261016_ow0129`
   own these indexes.
2. The async historical estimate keeps its exact source fields and uses the
   native async pool. Raw row-size estimation is bounded to the newest 256
   rows per API, matching the synchronous estimator's documented sample
   policy. Its independent read aggregates run concurrently on separate
   pooled read connections; no provider call, write, or strategy threshold is
   involved.
3. The overview route starts that native async estimate at the same time as
   the async overview snapshot. The two transactions remain isolated, and the
   history estimate is attached only after both complete. Deployments with a
   smaller pool naturally serialize at the pool boundary.

## Evidence and limits

- Local direct SQL: canonical coverage was about 7.7 s before the coverage
  index and about 2.2--3.5 s after the index plus row-count plan; the endpoint
  measured about 2.4--3.0 s after the change. Exact values depend on cache and
  local database load.
- The response remained semantically equal to the synchronous estimator for
  coverage fields and dataset row counts, including 704 bar days, 696 full
  cross-section days, 5,669 fundamental symbols, 7,904 limit symbols and
  1,533 minute symbols in the local snapshot.
- The local result is not owner-tunnel evidence. After the owner applies the
  migrations and source overlay, remeasure endpoint p50/p95, statement
  counts, pool waiting and database I/O before changing the pool ceiling.

## Guarantees

- This is a research/readiness display projection. It does not promote a
  provider response, alter a strategy gate, place an order or write evidence.
- The canonical row count is equivalent to `count(DISTINCT symbol)` because
  `(symbol, trading_date)` is the table primary key and the predicate only
  filters rows; no duplicate symbol/day can be admitted.
- No cross-request TTL or stale dashboard cache is introduced. A database
  error still fails the read rather than returning an old estimate.
