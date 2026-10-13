# Plan: tdx-live-snapshot-fallback (revision 3)

Workstream W008 (`docs/work/streams/W008-tdx-snapshot-filler.md`). Decision 0015
(`docs/decisions/0015-tdx-may-be-decision-evidence.md`, operator, 2026-10-12): TDX joins the
production collection chain and may be decision evidence under the catalog's own rule. This plan
makes the TDX all-A snapshot the filler for a minute the main source (Fuyao/THS) fails, with its
source labelled. It does not make TDX the primary source, and it changes no binding's status or
eligibility.

## Revision note (revision 3, 2026-10-13)

Revision 2 was written before any in-session measurement. The first shadow session
(2026-10-13) was measured read-only on the owner; the probes and their outputs are
`docs/work/probes/2026-10-13-tdx-{cadence,completeness,price-gaps,tolerance}.py` and `.out.txt`.
What changed against revision 2, and why:

1. **Four prerequisite steps (P1-P4) now come before the three steps of revision 2 (now
   B1-B3).** They fix what the first session showed: both capture loops drift, the TDX snapshot is
   empty during the opening call auction, the agreement is measured between capture times instead
   of data times, and the day summary cannot run in session.
2. **A completeness gate is new.** Revision 2 let any TDX answer fill a minute. From 09:15 to
   09:25 the TDX snapshot has no priced row at all.
3. **The agreement tolerance is a share of symbols, not every symbol.** This contradicts
   revision 2's step 3, which relied on the promote tool's tolerances: the tool fails an agreement
   when a single common row is outside the tolerance (`scripts/tdx-promote.py:254,266-267`). On
   2026-10-13 no in-session minute had every symbol within 0.5%. B3 adds a `min_share` key.
4. **Part of revision 2's step 1 already exists** (built with the shadow phase):
   `quant-service/app/all_a_snapshot_sources.py` with the projection and `compare`, and the
   `tdx_public` rate slot (60 a minute, `public_provider_rate_limits.py:39-40`). B1 extends them.
5. **One shared TDX reader for the shadow loop and the fallback**, instead of revision 2's reader
   owned by the chain with `max_wait_seconds=0`. After P1 both captures start two seconds apart
   every minute, so a fallback read would meet the shadow read every time the main source fails;
   each `tdx_protocol.call` opens its own connection and the access rule is one connection a host.
6. **Two releases instead of one, and the tool change apart.** P1-P4 are released first so that
   sessions measured with data times start to accumulate; B1-B3 follow, B3 first. The change to
   `scripts/tdx-promote.py` is one dispatch of its own, because the release classifier treats that
   path as a full release (`scripts/shared-peer/classify-owner-paths.sh:88`).
7. **The five sessions are checked by the tool, not left to memory** (audit round 1): `apply`
   refuses the promotion of this binding without five saved day summaries that pass. B3 is built
   before B1 and B2 so that this gate is in `main` before the chain (audit round 2).
8. File and line references were re-read at `e8a7b953`.

Audit: three rounds by Codex (`gpt-5.6-sol`, read-only) on 2026-10-13: 6, 2 and 1 objections, all
nine accepted, none rejected; the audit did not end on `SOUND`. The change made for the last
objection (the gate's roster is the main source's stored upstream total, and the gate fails closed
without it) has not been audited. The per-round account is in the W008 record.

Everything else revision 2 decided stands: the provenance rule, the consumer list, what a filled
minute may feed, and the way to eligibility.

## Facts (code, at e8a7b953)

- One source today: `_fetch_intraday_all_a_snapshot_rows` (`quant-service/app/main.py:1746-1748`)
  → `fuyao_all_a_snapshot_rows()`, behind `SharedAsyncSnapshot` (30 s TTL, `main.py:1743,1751-1755`,
  `quant-service/app/intraday_cross_section.py:36-71`). `intraday_all_a_snapshot()`
  (`main.py:1774-1777`) feeds the level1 capture (`main.py:3200-3208`,
  `level1_snapshot_runtime.py`), the scans (`main.py:2726,2756,2836,2854`), paper execution
  (`main.py:3073`, `paper_auto_execution_runtime.py:103`), board drilling
  (`board_flow_drill_runtime.py:56`) and the limit-up anchors (`main.py:3284-3285`).
- Provenance is hard-coded as Fuyao: `quote_from_fuyao` sets `price_source`
  (`intraday_quote_normalization.py:191-209`); derived volume is labelled `fuyao_ths_derived`
  (`intraday_derived_flow_metrics.py:140`); the signal rules accept a flow label only when the
  catalog marks it rule-usable (`intraday_signal_rules.py:81-96`, `RULE_USABLE_FLOW_LABELS`,
  `datasources/catalog.py:880-899`); `annotate_flow_snapshot_provenance`
  (`intraday_features.py:199-236`) decides flow eligibility from freshness and cross-section status
  only, and `live_policy.py:64-83` uses that for entry decisions; `intraday_scan_source_status.py:53-71`
  and `intraday_scan_preparation.py:79-87` name Fuyao; level1 storage and the minute documents use
  a fixed provider key (`level1_snapshot_runtime.py:66,87-88,156-159`,
  `minute_cross_section.py:40-41,204-256,277-284`); provider health knows one all-A entry
  (`realtime_provider_health.py:13-22`).
- Every Fuyao failure is a `FuyaoProviderError` (`fuyao_provider.py:31,82-135`). An open Fuyao
  circuit is not raised by the client; `MarketSnapshotActions.build`
  (`market_snapshot_actions.py:281-291`) checks it and goes to its own AKShare fallback.
- TDX: `tdx_public quote.all_a_snapshot`, `declared`, priority 80, adapter
  `app/datasources/sources/tdx_legacy_misc.py:fetch_all_a_snapshot` (`:249-252`); the `BindingSpec`
  (`catalog.py:319-325`) holds the projection and declares no agreement. The adapter pages 80 rows
  at a time and stops at the first short page (`:229-246`); it drops rows whose price is not
  positive and reports their count as the warning `no_trade_rows=<n>` (`:147-151,252`; omitted when
  the count is 0). Each call opens its own connection on a worker thread
  (`tdx_protocol.py:466-499,518-519`); `FAILOVER_SECONDS` stops starting further hosts but never cuts
  a running operation (`:83,470-475`).
- The two capture loops sleep a fixed 60 s after each capture
  (`level1_snapshot_runtime.py:112-119`, `tdx_shadow_snapshot_runtime.py:181-186`).
- The level1 capture stamps its document with the capture time and stores whatever the shared
  snapshot returns, which may be up to 30 s old; the snapshot's `age_seconds` and
  `upstream_observed_at` are in the document metadata and in every remembered row's
  `snapshot_metadata` (`level1_snapshot_runtime.py:40,49-61,82`, `main.py:1776-1777`).
- The shadow capture compares its rows with `minute_cross_section.recent` (the last main document
  in memory, at most 90 s older by capture time) and records the distance between the two capture
  times as `seconds_apart` (`tdx_shadow_snapshot_runtime.py:34,49-67,157-160`).
- `day_summary` selects the `normalized` body of every shadow document of the day and uses only
  their count (`tdx_shadow_snapshot_runtime.py:207-212,246-249`). `persist_document` also writes a
  small `payload` column holding `rows`, `sha256` and the metadata (`minute_cross_section.py:184`).
- The catalog's only eligibility rule: a `decision_eligible` binding must be `live_verified`
  (`catalog.py:1125-1126`). The promote tool never touches `decision_eligible`
  (`scripts/tdx-promote.py:27,408`) and compares every common row against `rel_tol`/`abs_tol`
  (`:212-216,240-267`); the keys an agreement entry may carry are `AGREEMENT_KEYS`
  (`catalog.py:1037-1038`).

## Measured on 2026-10-13 (first shadow session, read-only on the owner)

- **Cadence** (`2026-10-13-tdx-cadence.out.txt`, to 13:59). Seconds between consecutive captures,
  median: main 61.78, TDX 63.48. Of 195 expected minutes the main source lacks 9 and TDX 14; 7 and
  12 of them are minutes the drifting capture time stepped over, the rest are session edges (09:15
  for the main source, 11:29/11:30). Neither loop had an interval of 90 s or more: no capture
  failed that day, so every in-session gap of the main source was drift.
- **Completeness** (`2026-10-13-tdx-completeness.out.txt`). In the opening call auction
  (09:15-09:25) all ten TDX documents hold 0 rows, with `no_trade_rows=5579`; the main source holds
  4,940-5,444 rows in the same minutes. These are the nine comparison rows with 0 common symbols.
  From 09:25 TDX holds 5,480 rows (main 5,504); in continuous trading at least 5,535, median 5,560
  (main at least 5,548). TDX served 5,579 rows in every read; the main source's upstream total was
  5,579-5,580.
- **Single-symbol price gaps** (`2026-10-13-tdx-price-gaps.out.txt`). 000609.SZ at 09:30 (10.4%),
  301660.SZ at 09:33 (9.5%), 002549.SZ at 09:54 (10.8%): in each the two sources saw different
  instants of a fast-moving stock and neither held a wrong price. The main document was the older
  one each time (smaller cumulative volume): data times 24 s, 36 s and 81 s apart, recorded as
  18.1 s, 21.5 s and 56.4 s. The 09:30 pair straddles the open. Where both saw the same instant
  the prices are equal to the cent.
- **Why the recorded gap understates** (`2026-10-13-tdx-completeness.out.txt`,
  `2026-10-13-tdx-tolerance.out.txt`). 149 of 187 main documents were a cached snapshot (age median
  12 s, maximum 30 s); a TDX read returns 2.55 s (median) after its capture starts.
- **Agreement by data gap** (`2026-10-13-tdx-tolerance.out.txt`; complete snapshots, both data
  times in the same continuous half-day): see the table under "The in-session agreement rule". The
  saved run is from 14:17 and covers the session to that minute; the re-run after the close did not
  reach the owner (ssh timed out from 15:07) and is still to be done.
- **Day summary.** 182 shadow documents by 13:59 are 47.7 MB as stored and about 0.58 MB each as
  JSON text, about 106 MB in one statement with a 10 s timeout. The same query without the body
  (`effective_at`, `payload->>'rows'`) took 0.05 s. The failing command was not re-run in session.

## Design

One rule carries the change (unchanged from revision 2): **a snapshot says where it came from and
whether its source is decision eligible, and every consumer uses what the snapshot says instead of
assuming Fuyao.**

- The snapshot's metadata gains `provider_key` and `decision_eligible`. For Fuyao they are
  `fuyao_ths` and what the code grants today (unchanged behaviour). For the fallback they are
  `tdx_public` and the `decision_eligible` of the `tdx_public quote.all_a_snapshot` binding read
  from the catalog at call time. Today that is `False`.
- Rows carry their own `price_source` (`tdx_public_all_a_snapshot` for the fallback); nothing
  relabels a TDX row as Fuyao.

### What a TDX-filled minute may and may not feed while the binding is `declared`

Unchanged from revision 2 and decision 0015 item 3.

- May feed: the minute document (stored under provider `tdx_public`, with `fallback_for` in its
  metadata), the market radar point, the intraday temperature, provider health and the count of
  fallback minutes in indicator health.
- May not feed: the signal path. Its derived flow is labelled `tdx_public_derived`, which is not
  rule-usable while the binding is not eligible; `annotate_flow_snapshot_provenance` marks the flow
  snapshot not eligible, so `live_policy` rejects an entry on it exactly as it rejects a stale Fuyao
  flow; paper execution does not act on the snapshot.
- What "eligible" covers, for either source: the cross-sectional flow derived from the snapshot
  (volume ratio, turnover) and the flow snapshot behind an entry. It never covers the snapshot's
  price as a decision quote: `live_policy` accepts a quote only from an exchange-timestamped label
  (`live_policy.py:64-69`, `catalog.py:896-897`), and neither `fuyao_ths_all_a_snapshot`
  (`catalog.py:888`) nor the new `tdx_public_all_a_snapshot` is one. This plan does not add any label to that set.
- When the binding is promoted and marked eligible (an operator act after B3), the flow of a
  fallback minute becomes eligible with no further code change, because both the snapshot's
  `decision_eligible` and the label's `rule_usable_flow` are read from the binding (B2).

### The completeness gate

A TDX snapshot may fill a minute, and a shadow snapshot is compared, only when it is complete:

    roster = max(priced rows + no_trade_rows, R, 5000)
    complete when R is known and priced rows / roster >= 0.95

- `priced rows` is what the adapter returned; `no_trade_rows` comes from its warning (0 when the
  warning is absent). Their sum is what the server served.
- `R` is the upstream `total` in the metadata of the newest stored main-source minute document
  (`fuyao_provider.py:222,232`; 5,579-5,580 on 2026-10-13). It is read through the `payload` column
  (no document body; that query shape took 0.05 s on 2026-10-13), once per trade date and process,
  and kept in memory. It is a roster the TDX read cannot influence, so a read that stopped at a
  short page (`tdx_legacy_misc.py:237-246`) is measured against the full list whether or not the
  main source answered recently (audit round 3: the earlier draft used the main document in
  memory, at most 150 s old, and let a cut-off read of 5,000 rows or more through when there was
  none). The document may be a day old during a long outage; the list changes by a few names a day.
- When `R` cannot be read (database busy, no stored document), the snapshot counts as incomplete:
  the gate fails closed.
- The floor of 5,000 only guards against a nonsensical `R`.
- Measured on 2026-10-13 with this formula (`R` 5,580): call auction 0.000 (all ten documents);
  09:25-09:30 0.982; continuous trading at least 0.992. The threshold of 0.95 has 3 points of room
  below the lowest complete value and rejects every empty one. It is a first value from one
  session. A share cannot see a read that lost fewer than 5% of the list (279 names).
- Consequence: TDX cannot fill a minute between 09:15 and 09:25. A main-source failure there stays
  a missing minute, as today. (Backlog B023 assumed the 09:15:20 TDX document could fill the
  missing 09:15 minute; it holds 0 rows.)

### The in-session agreement rule (first value, from 2026-10-13)

Numbers below are from `2026-10-13-tdx-tolerance.out.txt` (session to 14:17): the 183 comparison
rows of complete snapshots whose two data times lie in the same continuous half-day, grouped by
the gap between the data times (main: capture time minus cache age; TDX: when its read returned).

| data gap | minutes | share of symbols within 0.5%: median | 10th percentile | lowest |
|---|---|---|---|---|
| under 5 s | 1 | 0.997 | 0.997 | 0.997 |
| 5-10 s | 5 | 0.993 | 0.976 | 0.976 |
| 10-15 s | 9 | 0.992 | 0.975 | 0.975 |
| 15-20 s | 4 | 0.990 | 0.987 | 0.987 |
| 20-30 s | 23 | 0.983 | 0.898 | 0.855 |
| 30-45 s | 43 | 0.978 | 0.848 | 0.598 |
| 45-60 s | 50 | 0.972 | 0.845 | 0.569 |
| 60 s and more | 48 | 0.954 | 0.828 | 0.348 |

- A minute is **judged** when both snapshots are complete, both data times lie in the same
  continuous-trading segment, and the data times are at most **15 s** apart.
- A judged minute **agrees** when at least **95%** of the common symbols have prices within
  **0.5%** of each other (`price.within_0_5_pct >= 0.95`).
- A session **passes** when it has at least 30 judged minutes and at least 95% of them agree.
- Why these numbers. Up to 20 s every one of the 19 minutes has at least 97.46% of its symbols
  within 0.5%; from 20 s the 10th percentile falls to 89.8% and the lowest minute to 85.5%. The
  limit of 15 s leaves one group of margin before that break. 95% leaves 2.5 points under the
  lowest of the 15 judged minutes (97.46%); all 15 agree at 95% and at 97%, 12 of 15 at 98%. None
  of the 15 lies in 09:30-09:45 and one in 13:00-13:15, so the most volatile minutes are not in the
  sample. Without the limit on the data gap the rule would hold in only 139 of 183 minutes (76%).
- Volume and turnover are recorded and reported, and do not decide a judged minute. In the 15
  judged minutes the share of symbols with volume within 1% was at least 90.6% (median 98.8%);
  turnover the same to a tenth of a point. First value for the tool's entry in B3: `rel_tol` 0.01
  with `min_share` 0.85 for both.
- These are first values. They are to be confirmed over **at least five sessions measured after
  P1-P4 are live** (2026-10-13 is the calibration day and does not count: its minutes were paired
  by drift). No binding is promoted before that, and the thresholds are re-read against the five
  sessions' tables before any promotion. How many sessions, and what a failing session means for
  the count, is the operator's decision.

## Steps

Each step ends with its tests in the same commit. No step reads the network or the owner.

### P1: both capture loops start on fixed seconds of the wall clock

Why: the period is 60 s plus the capture's own duration, so the capture time moves 1.8 s (main)
and 3.5 s (TDX) later every minute and steps over a minute about every 34 and 17 minutes.

- `quant-service/app/intraday_schedule.py`: a pure function `seconds_until_slot(now_epoch,
  interval_seconds, offset_seconds)` returning the wait, in `(0, interval]`, until the next
  `k * interval + offset` on the wall clock.
- `run_level1_snapshot_loop` (`level1_snapshot_runtime.py:105-119`) and
  `run_tdx_shadow_snapshot_loop` (`tdx_shadow_snapshot_runtime.py:177-186`): wait for the slot, then
  capture; the clock and the sleep are parameters so the tests drive them. A capture that outlives
  its slot skips to the next one (one minute lost and logged, no catching up). The interval keeps
  its clamp of 10-300 s.
- Slots: main at second **50**, TDX at second **52**. Why 50: the stored snapshot may be 30 s old
  and its upstream stamp trails by 3 s (median; 6.5 s at most), so a capture at second 50 holds
  data from second 14 or later, which puts the 09:25 match into the first document of 09:25 (read
  by `strategy_cards_read_model.py:135-136`) and continuous prices into the document of 09:30. Why
  52: the main capture has stored and remembered its document within 0.6 s (median; 4.5 s at
  most), and the TDX read returns about 2.6 s after it starts, so the comparison finds the main
  document of the same minute.
- The session gate is unchanged (09:15:00-11:30:00, 13:00:00-15:00:00,
  `market_rules.py:89-103`): a session then has 255 slots (09:15-11:29, 13:00-14:59).
  `runtime_task_registry.py:87-96` states the new cadence in its two contracts.
- Tests (`tests/test_intraday_schedule.py`, `tests/test_background_loops.py`,
  `tests/test_tdx_shadow_snapshot_runtime.py`): the wait for times before, on and after a slot;
  a loop whose captures take 1.8 s and 3.5 s starts every capture on its slot for 40 rounds; a
  capture longer than the interval skips exactly one slot.

### P2: the completeness gate, and the shadow comparison uses it

- `all_a_snapshot_sources.py`: `tdx_snapshot_completeness(priced_rows, warnings, roster_total)`
  → `{priced_rows, no_trade_rows, roster_total, roster, share, complete}` by the formula above;
  the threshold and the floor are module constants. `roster_total=None` gives `complete: False`.
- `minute_cross_section.py`: `latest_main_total(connection)` selects
  `(payload->'metadata'->>'total')::int` of the newest `fuyao_ths` minute document (the shape of
  `_DOCUMENT_SQL`, `:204-206`, without `normalized`).
- `tdx_shadow_snapshot_runtime.py`: a dependency `roster_total` (async, returns the number or
  `None`), wired in `main.py` to `latest_main_total` through the shadow's own database runner and
  remembered per trade date; a refused or timed-out read is `None`, not an error, and a `None` is
  not remembered. The shadow
  document is stored as today, with the completeness in its metadata. Its comparison row has
  `status: "incomplete"`, the completeness and no comparison when the snapshot is not complete;
  `compared` rows carry the completeness too.
- Tests: the 2026-10-13 values against a roster of 5,580 (0 priced with 5,579 no-trade; 5,480 with
  99; 5,535 with 44); reads that stopped early (3,000 with 10; 5,000 with 0; 5,200 with 20) are
  incomplete; 5,301 with 0 is complete; no roster is incomplete; a capture of an empty snapshot
  stores the document and an `incomplete` row; the roster is read once for two captures of one
  trade date and its statement does not name `normalized`.

### P3: the agreement is measured between data times

Why: the largest gaps of 2026-10-13 came from pairs 24-81 s apart in data time that were recorded
as 18-56 s apart; neither source was wrong.

- `tdx_shadow_snapshot_runtime.py`: the comparison row gains `shadow_data_at` (the evidence's
  `available_at_max`; the capture time when the adapter returned bare rows), `reference_data_at`
  (the reference's capture time minus the `age_seconds` of its rows' `snapshot_metadata`),
  `data_seconds_apart`, and `same_continuous_segment` (both data times in one segment by
  `intraday_clock.continuous_auction_bounds`). `comparison.seconds_apart` keeps its meaning, so the
  rows of 2026-10-13 stay readable.
- Tests: a reference captured 18 s before the shadow, cached for 20 s, with a read that returned
  3 s after the shadow capture started, gives 41 s; a pair on both sides of 09:30:00 is not in the
  same segment; a pair before 09:30 is not in a continuous segment.

### P4: the day summary runs in session and reports the rule

- `day_summary` (`tdx_shadow_snapshot_runtime.py:202-265`): the document query selects
  `effective_at` and `(payload->>'rows')::int`, never `normalized`. The comparison query stays (182
  rows were 324 KB on 2026-10-13).
- The summary adds: expected minutes 255 (it said 257), minutes without a document, the lowest and
  median rows per document, counts by status (`compared`, `incomplete`, `no_reference`), judged
  minutes, judged minutes that agree, their share, whether the session passes, and the medians per
  data-gap group. Rows without `data_seconds_apart` (2026-10-13) are counted as not judged.
- The rule's numbers are one constant in `datasources/catalog.py`, keyed by binding:
  `SESSION_AGREEMENT_RULES[("tdx_public", "quote.all_a_snapshot")]` = data gap at most 15 s, share
  within 0.5% at least 0.95, at least 30 judged minutes, at least 0.95 of them agreeing, and
  `min_sessions` 5. The summary prints the rule it judged with and `session_passes`. B3's agreement
  entry and the tool's session gate read the same constant.
- Tests: the fake connection fails the test if a statement on the shadow document stream names
  `normalized`; a day of rows with known gaps gives the expected judged and agreeing counts; rows
  without data times are not judged.

### B1: the fallback chain (revision 2's step 1, minus what exists)

`quant-service/app/all_a_snapshot_sources.py`; `main.py` only wires it.

- The projection exists (`project_tdx_snapshot_rows`, tested against
  `tests/fixtures/all_a_snapshot_fuyao_tdx_20261012.json`). Add the metadata as
  `fuyao_provider.py:218-233` builds it, with `source` = `tdx_public_all_a_snapshot`, `provider_key`,
  `decision_eligible` (from the catalog binding at call time), `fallback_for` = `fuyao_ths`,
  `fallback_reason` (bounded text of the Fuyao error) and the completeness.
  `fuyao_all_a_snapshot_rows` gains `provider_key` and `decision_eligible` for its own source.
- Chain `async def read(fuyao, tdx, roster_total)`: await Fuyao; on `FuyaoProviderError` read
  TDX once. Anything else propagates. A TDX error, a refused slot or an incomplete snapshot (P2)
  raises an error chained from the Fuyao one. `roster_total` is the value P2 remembers per trade
  date (the shadow reads it at the day's first capture); when none is remembered yet the wiring
  reads it once through `run_database_blocking`.
- The shadow's reference is a main-source document or nothing (audit round 2). Once the chain is
  live the level1 capture remembers whatever it stored, fallback rows included
  (`level1_snapshot_runtime.py:81-82`, `minute_cross_section.py:261-274`). One helper returns the
  remembered document only when its rows' `snapshot_metadata.provider_key` is `fuyao_ths`; the
  shadow runtime's `recent_reference` (`main.py:3184`) goes through it. On a fallback minute the
  shadow row is therefore `no_reference` and is not judged. The gate's roster comes from stored
  `fuyao_ths` documents only (P2). TDX is never compared with, or measured against, itself.
- One shared TDX reader, wired in `main.py`: `SharedAsyncSnapshot` over a fetch that acquires the
  `tdx_public` slot (`max_wait_seconds=0`) and then calls `fetch_tdx_all_a_snapshot`, with a TTL of
  5 s. The shadow runtime and the chain both read through it, so at most one TDX all-A read runs
  at a time and two callers within 5 s share one result. The shadow capture no longer acquires the
  slot itself and reports `rate_limited` when the fetch raises `PublicProviderRateLimited`.
  `SharedAsyncSnapshot` is not changed: it gives the single flight and does not cache a failed
  fetch (`intraday_cross_section.py:52-71`), and it does not consume the exception of a fetch its
  caller has left. Each caller therefore keeps its own task around its `get()` and consumes that
  task's late exception, as the shadow runtime already does
  (`tdx_shadow_snapshot_runtime.py:102-109,135-139`) and the watch scan does for the Fuyao snapshot
  (`intraday_watch_quote_capture.py:223-224,266`, `main.py:1758-1771`): the task keeps awaiting the
  shared fetch, so the fetch's exception is always retrieved.
- The chain waits for its TDX task no longer than `TDX_READ_TIMEOUT_SECONDS` (20 s, the shadow's
  bound) with `asyncio.wait_for(asyncio.shield(task), ...)`; on a timeout it raises the chained
  error and the task finishes in the background with the done callback above. State the worst-case
  duration of the one read thread from `tdx_protocol`'s timeouts in the docstring.
- `_fetch_intraday_all_a_snapshot_rows` calls the chain. `MarketSnapshotActions.build` keeps its
  circuit check and its AKShare fallback: out of scope.
- Tests: Fuyao answers → TDX not called; `FuyaoProviderError` → TDX rows and metadata; another
  exception propagates, TDX not called; both fail → chained error; refused slot; incomplete
  snapshot → chained error, nothing returned; a TDX stub that never answers: the caller returns
  within its bound, a second call does not start a second read, and the late result raises no
  warning; the shadow and the chain calling together cause one read; after a fallback minute was
  remembered the helper returns nothing and the shadow row is `no_reference`; a fallback document
  does not change the roster.

### B2: consumers use what the snapshot says (revision 2's step 2)

For each site, replace the Fuyao assumption with the snapshot's own provenance; behaviour for a
Fuyao snapshot must be what it is today (existing tests unchanged).

- `quote_from_fuyao` (`intraday_quote_normalization.py:191-209`) and its callers
  (`main.py:1830-1832,2726,2756,3707`): the row's `price_source` is kept; `observation_source()`
  (`:134-147`) and the source labels (`datasources/catalog.py:880-899`) know
  `tdx_public_all_a_snapshot`.
- Derived flow (`intraday_derived_flow_metrics.py:140`): the label names the provider the volume
  came from (`tdx_public_derived` for the fallback). `rule_usable_flow` is static catalog data and
  the rules test the static set (`contracts.py:157-170`, `catalog.py:896-899`,
  `intraday_signal_rules.py:89-96`), so the new `SourceLabel` is declared with
  `rule_usable_flow` computed from the `decision_eligible` of the `tdx_public quote.all_a_snapshot`
  entry of `BINDINGS`, which the same module defines above the labels (`catalog.py:310,880`). It is
  `False` today and turns `True` with the catalog edit that marks the binding eligible; a catalog
  test pins "the label is rule-usable exactly when the binding is eligible".
  `annotate_flow_snapshot_provenance` (`intraday_features.py:199-236`) also requires the
  snapshot's `decision_eligible`.
  `live_policy.py:64-83` then needs no TDX-specific branch; confirm with a test on both sides of the
  binding's flag.
- `intraday_scan_source_status.py:53-71` and `intraday_scan_preparation.py:79-87`: the status and
  the recorded success name the provider that served the snapshot.
- `paper_auto_execution_runtime.py:103`, `board_flow_drill_runtime.py:56`,
  `limit_up_anchor.py:30-65` with `main.py:3284-3285`: read how each uses the rows. Where it stores
  or reports a source, it stores the row's; where it would act on a row (paper execution), it acts
  only on a snapshot whose `decision_eligible` is true. If any of the three cannot follow the
  snapshot's provenance at one place, stop and report.
- Storage: the level1 capture persists under the snapshot's `provider_key`
  (`level1_snapshot_runtime.py:66,87-88`); minute documents are written under it and read by
  capability across providers (`minute_cross_section.py:204-256,277-284`: `latest`, `latest_async`,
  `first_between`, `day_documents`, `latest_level1_items`), and the indicator-health query that
  names the provider (`indicator_health.py:184`) likewise. The shadow stream keeps its own
  capability (`a_share_minute_cross_section_shadow`), so these readers never see it. Existing rows
  are untouched; no migration.
- Health: `level1_capture.persist_health` (`level1_snapshot_runtime.py:147-161`) records a Fuyao
  failure with its error and a `tdx_public` success when the fallback served the minute; the TDX
  all-A capability gets its alias (`datasources/catalog.py:28-37`) and its entry in
  `realtime_provider_health.SOURCES` (`:13-22`). `market.minute_documents` in indicator health gains
  the count of today's fallback minutes (`ok` at zero, `warn` above zero with the count).
- Tests: one per site, each with a Fuyao snapshot (unchanged output) and a fallback snapshot, the
  latter with the binding's flag false and true where eligibility is involved.

### B3: the way to eligibility (revision 2's step 3, with the share rule)

- Agreement entries gain an optional `min_share` (default 1.0, today's behaviour): the share of
  common rows that must be within the tolerance. Catalog part: `AGREEMENT_KEYS` and the entry check
  (`catalog.py:1037-1060`) accept a number in (0, 1]. Tool part: `compare_field`
  (`scripts/tdx-promote.py:240-275`) fails the field when the matched share is below it and reports
  the share. Until the tool part lands, the tool ignores the key and keeps failing on any row
  outside the tolerance, so the order cannot produce a false pass.
- The catalog declares the agreement of `tdx_public quote.all_a_snapshot` against the Fuyao
  snapshot (reference adapter `app/fuyao_provider.py:all_a_snapshot_rows`, key `symbol`): `price`
  with `rel_tol` 0.005 and `min_share` 0.95, from the rule above; `volume` and `turnover` with the
  first values the rule section gives. The tool's two reads are direct adapter calls seconds apart,
  with no cache between them.
- `scripts/tdx-promote.py apply` takes `--decision-eligible <decision record>` on the
  `declared → live_verified` step only: it then also sets `decision_eligible=True` on that binding
  and records the decision reference in the commit text it prints; without the flag it behaves as
  today. The catalog's rule (`catalog.py:1125-1126`) stays the guard. Tests in
  `scripts/test_tdx_promote.py` (run by `pytest scripts`); the tests that assert every TDX binding
  is never eligible (`quant-service/tests/test_datasource_catalog.py:194,205,211`,
  `quant-service/tests/test_datasource_tdx_mac.py:628-633,750`) assert the catalog rule instead
  (eligible implies live verified) and today's values.
- The five sessions are a gate of the tool. On the `declared → live_verified` step of a binding
  that has a session rule in the catalog (P4's constant; only this binding has one), `apply`
  requires `--session-summary <file>` at least `min_sessions` times. Each file is the JSON that
  `python -m app.tdx_shadow_snapshot_runtime --trade-date <day>` printed for one session. `apply`
  exits non-zero unless the files have distinct trade dates, each says the session passes, and
  each was judged with the rule the catalog holds now; the commit text it prints lists them with
  their hashes. Tests: four summaries, five with one failing, five with one judged under another
  rule (all refused, catalog unchanged), five passing (status moves).
- `docs/DATA_SOURCE_LAYER.md` (§3 priority table, §3.1, §6 scheduling, §7.1) and
  `docs/ARCHITECTURE.md` describe the chain, the provenance rule, the gate, the agreement rule and
  the promotion flag. They are written last, with B2 (dispatch 10), when the chain they describe
  exists.

No binding's status or eligibility changes in this plan. Promotion is a later operator act: the
tool's in-session check, five passing day summaries (which `apply` now demands), and the
operator's consent. Its evidence file is written under `scripts/data/`, which the release
classifier treats as a full release; that is how every promotion so far was released.

## Acceptance

From the worktree root, after every dispatch (the modules the dispatch touched, then everything):

```
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path QUANT_TESTS="<the test modules of the dispatch>"
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q
(cd quant-service && /Users/papa/.venvs/svc/bin/python -m app.datasources validate)
/Users/papa/.venvs/svc/bin/python scripts/verify_component_boundaries.py --check
git diff --check && git diff --check main...HEAD
```

Test modules by step: P1 `tests.test_intraday_schedule tests.test_background_loops
tests.test_level1_snapshot_runtime tests.test_tdx_shadow_snapshot_runtime`; P2-P4
`tests.test_all_a_snapshot_sources tests.test_tdx_shadow_snapshot_runtime`; B1
`tests.test_all_a_snapshot_sources tests.test_intraday_cross_section
tests.test_tdx_shadow_snapshot_runtime tests.test_public_provider_rate_limits`; B2 the module of
every site it touches, named in the report, plus `tests.test_level1_snapshot_runtime
tests.test_minute_cross_section`; B3 `tests.test_datasource_catalog tests.test_datasource_tdx_mac`,
and `scripts/test_tdx_promote.py` through the `pytest scripts` line.

At the merge gate the planner runs the same commands itself, and from the main checkout
`cd frontend && npm run typecheck && npm run build` (root `AGENTS.md`, Agent workflow 4; this plan
touches no file under `frontend/`). Do not read, print or copy the env file. No network request
and no live read.

## Dispatches

Each is one Codex call of about 15 minutes (`codex-flow`: implementer prompt,
`sandbox: danger-full-access`), one commit or more, acceptance run before it reports. A step that
runs long is continued with `codex-reply` from its last finished part.

| # | content | branch |
|---|---|---|
| 1 | P1 | `codex/tdx-shadow-measure` |
| 2 | P2 and P3 | same |
| 3 | P4 | same |
| | dual review, merge, **release A** (owner code-only), then the checks under "Verification" | |
| 4 | B3, catalog part: `min_share` in `AGREEMENT_KEYS` and the entry check, the agreement entry, the changed assertions | `codex/tdx-promote-gate` |
| 5 | B3, tool part: `min_share`, `--decision-eligible` and the session gate in `scripts/tdx-promote.py`, its tests, the classifier rule below | same |
| | dual review, merge; no owner release of its own (the catalog change rides the next one) | |
| 6 | B1: metadata, the main-source reference helper, chain, gate in the chain, their tests (no `main.py`) | `codex/tdx-live-snapshot-fallback` |
| 7 | B1: the shared TDX reader, the shadow capture's slot, wiring in `main.py` | same |
| 8 | B2: quote normalization and labels; derived flow, flow provenance, rule-usable label, `live_policy` test | same |
| 9 | B2: scan status and preparation; paper execution, board drilling, limit-up anchor | same |
| 10 | B2: storage under the provider key, readers across providers, health; the two documents of B3 | same |
| | dual review, merge, **release B** (owner code-only) | |

B3 is dispatched and merged before B1 and B2 (audit round 2), although it keeps its name: the
tool's session gate has to be in `main` before the chain exists. From that merge on, `apply` cannot
move this binding to `live_verified` without five passing summaries, and those cannot exist before
five sessions after release A. Until that merge the tool in `main` is today's: it can already move
the binding to `live_verified` on its three gates (a binding that declares no reference passes the
agreement gate, `scripts/tdx-promote.py:21-22`) and cannot set `decision_eligible` at all. This
plan asks nobody to run it.

The three worktrees have disposition `merge`, target `main`; each is created from `main` after
the one before it is merged. Commits: one per step or consumer group. No push. Do not stage
`.env.example`, `.gitignore`, `config/secrets/*`, `feishu-relay/**`, `scripts/svc_supervisor.py`,
`scripts/test_svc_supervisor_tasks.py`, `scripts/teacher_review_daily.py`, `scripts/data/*`,
`docs/work/**`.

Releases A and B are owner code-only releases: apart from dispatch 5 the dispatches change only
`quant-service/app/*` (runtime), `quant-service/tests/*` and `docs/*` (skipped). The operator runs
them (`docs/RELEASE_SYNC_47.md`), outside the no-restart windows.

Dispatch 5 and the release classifier: a change to `scripts/tdx-promote.py` falls through to
`full` (`scripts/shared-peer/classify-owner-paths.sh:88`), so once it is in `main` every later
owner release, of any workstream, is classified as a full release until one is done. The tool runs
on the workstation (`scripts/generate_scripts_catalog.py:132`) and reaches the owner only over
ssh, where it executes the image's `python -m app.datasources probe`; the owner runtime never runs
it. Dispatch 5 therefore adds `scripts/tdx-promote.py` to the classifier's workstation-tool rule
(`classify-owner-paths.sh:74-77`, the rule the teacher-cycle scripts have) with a case in
`scripts/shared-peer/deploy-code-only.test.mjs`. That is a change to a release safety rule and
needs the operator's explicit yes. Without it, the branch of dispatches 4-5 is reviewed and then
merged only together with the branch of dispatches 6-10, and release B is a full release.

## Verification after release (planner, read-only)

Release A, on the next session:

- `docs/work/probes/2026-10-13-tdx-cadence.py` with the day changed: 255 documents from each
  source or the missing ones explained by a failed cycle; consecutive captures 60 s apart; the main
  capture at second 50 and TDX at second 52.
- Inside the owner's main service container, in session:
  `d=/app/hotfix/current; cd $d && PYTHONPATH=$d python -m app.tdx_shadow_snapshot_runtime --trade-date <day>`
  finishes within its 10 s; it reports the 09:15-09:25 rows as `incomplete`, at least 30 judged
  minutes and whether the session passes.
- The summary of each of the next five sessions is saved as
  `docs/work/probes/<day>-tdx-shadow-summary.json` (a path the release classifier skips); these are
  the files `apply --session-summary` takes.

Release B: a Fuyao failure cannot be forced in production. On the next sessions: `tdx_public`
snapshot successes in provider health only at minutes where Fuyao failed; the fallback-minute
count; one fallback minute's document against the neighbouring Fuyao minute; that scans on a
fallback minute report a not-eligible flow while the binding is `declared`. Roll back by releasing
the previous version.

## Risks

- P1 changes when the main capture runs. Its documents move from a wandering second to second 50
  of each minute; no reader depends on the second (they read by minute window:
  `market_temperature_intraday.py:186-190`, `strategy_cards_read_model.py:135-136`).
- B1 puts the shadow read behind the shared reader; a mistake there costs shadow minutes, not main
  ones. The main read path changes only after a `FuyaoProviderError`.
- B2 changes code on the signal path (source labels, eligibility). The control is that a Fuyao
  snapshot's outputs are unchanged, pinned by the existing tests plus one unchanged-output test per
  site.
- On 2026-10-13 the main source had no failed capture, so the fallback would have filled nothing
  that day; its value is on a day the main source fails, and it cannot be exercised in production
  on demand.
- The gate and the agreement rule rest on one session. The five sessions exist to move them.
- The gate's roster is one database read per trade date; when it fails the fallback does not fill
  (fail closed), so a busy database at the first capture of the day costs that capture's fill.
- Reading minute documents across providers: two documents can exist for one minute only if both
  sources succeeded, which the chain does not do; the readers keep "newest per minute".

## Out of scope

The limit pools and the minute bars; `MarketSnapshotActions`'s AKShare fallback and the
post-close path; changing any binding's status or eligibility; the console; a capture after
15:00:00 (the closing match is not in any minute document today either); making the main capture
bypass the shared 30 s cache; why the main source stored nothing at 09:15 on 2026-10-13 (B023);
the 22 unmerged exploration branches (B029); a licence review of the TDX hosts (open question in
the W008 record).
