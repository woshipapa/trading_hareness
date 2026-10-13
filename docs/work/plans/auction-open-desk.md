# Auction desk: what to collect 09:15-09:25 and the 09:25 handling table (research-only)

Status: revision 2, 2026-10-12, after plan-audit round 1 (adjudicated at the end). Approved in principle by the
user on 2026-10-12. Depends on `.claude/plan/limit-up-distribution-scores.md` (the daily reading
`limit_up_risk_daily` with yesterday's limit-up stocks and their risk ranks).

## Goal

1. During the opening call auction, collect for a bounded pool the per-stock auction state (indicative price,
   matched and unmatched volume) on a fixed timetable, and after 09:25 the complete auction curve, so that the
   auction path becomes history we own. None of it exists as history today, so none of it can be backtested yet.
2. At 09:25, as soon as the final auction snapshot is in, publish a handling table for the stocks that closed
   at the limit yesterday: open gap, the gap's bucket with its measured statistics, yesterday's risk rank.
   Research evidence, `live_effect: none`; no order path, no threshold, no candidate list reads it.

Evidence for the table: `state/research/divergence_study_20261012/grab_dump/REPORT_抢筹出货判断.md`, section 七.
Hold-out (173 days, 12,313 stock-days after a limit-up close), open to close by open gap:
below -5%: +1.83%; -5..-2%: +0.97%; -2..0: +0.03%; 0..2%: +0.21%; 2..5%: -0.11%; 5..9%: -0.80%; 9% or more
without opening at the limit: -2.02%; opening at the limit: -1.78% to the close and +2.38% to the next open.
Buying at the open is not positive in any bucket that can be bought. The five hypotheses built on "auction
surprise against yesterday's scores" failed; the table uses the gap alone plus yesterday's risk rank as a fact.

## What exists (surveyed 2026-10-12, verify before editing)

- `auction_pulse.py` / `auction_pulse_runtime.py`: five Longhu calls per tick (auction list top 300, sector
  auction, plates, breadth, mood), interval `AUCTION_PULSE_INTERVAL_SECONDS` default 2 s, window 09:15-09:30,
  stored as provider `longhuvip` capability `opening_auction_pulse` (one small summary row per tick), Feishu
  alert; no read route. About 150 Longhu calls a minute; there is no Longhu entry in
  `public_provider_rate_limits.DEFAULT_RATE_LIMITS_PER_MINUTE`. On 2026-10-12 it stored 179 rows from 09:16:45.
- Whole-market Fuyao snapshot every 60 s from 09:15 (`level1_snapshot_runtime.py`), feeding the minute
  document and the radar; the radar only counts indicative-price thresholds before 09:25
  (`market_radar.py` `session_phase`: cancellable to 09:20, locked to 09:25). On 2026-10-12 nine documents
  between 09:16:37 and 09:29:31.
- Fuyao `a_share_auction_snapshot` with `thscodes` (at most 100 codes a request): `auction_price`,
  `auction_volume`, `auction_amount`, `auction_unmatched`, `auction_pct`, `open_price`, `pre_close_price`,
  `data_status` (`final` after the match). Used on demand after 09:25:05 by
  `teacher_intraday_context.refresh_auction` and `watch_review_auction_snapshot`; normalised by
  `market_event_capture.normalize_fuyao_auction`. Not captured on a timetable.
- TDX `datasources/sources/tdx_microstructure.fetch_auction_curve(symbol=...)`: up to 500 points of
  (time, price, matched, unmatched) for one stock, the stream ends at 09:24:57. Nothing schedules it.
- Longhu `GetStockBid` (`licensed_stock_api.py:94`): per-stock auction detail. It is a `RESEARCH_ACTIONS` member
  and `_integration()` classifies it `shadow_research` with `provider_available_at`
  (`longhu_capability_registry.py:27-44`, `:71-82`). Nothing schedules it. This plan stores it as research
  evidence and changes neither classification.
- There is no pool of "yesterday's limit-up stocks": `limit_event_repository.py:143-153` returns the prior
  session's close limit-up pool only. Broken boards are stored in `quant.market_events` as event type
  `limit_open_pool`, source `fuyao_ths` (`market_event_capture.py:73-76`). The intraday watchlist is capped at
  40-100 symbols.
- `fuyao_provider.fetch_envelope()` always takes the Fuyao slot with `FUYAO_SLOT_MAX_WAIT_SECONDS = 15.0`
  (`fuyao_provider.py:26-28`, `:75-86`); a caller cannot choose a shorter wait today.
- `persist_timed_observations` only writes; storage admission is a separate call
  (`research_storage_admission.py:70-78`), and a capability missing from `storage_tiering_policy.py` stays hot.

## Collection timetable

Pool, fixed at 09:14 for the previous open session of the SSE calendar: that session's scored stocks from the
reading `limit_up_risk_daily` (they carry the risk ranks the table shows), that session's broken boards
(`quant.market_events`, event type `limit_open_pool`, source `fuyao_ths`) and the intraday watchlist
(`enabled_watch_symbols(database, limit=100)`, `datasources/storage.py:17-23`), all read through the capture
boundary;
de-duplicated in that order and capped at `AUCTION_POOL_MAX_SYMBOLS` (default 200: two Fuyao requests a tick).
If the reading for the previous session is missing, the loop stores nothing and reports `no_pool`.

| what | source | when | calls |
|---|---|---|---|
| pool auction snapshot | Fuyao `a_share_auction_snapshot` | every 10 s from 09:15:05; extra ticks at 09:19:50, 09:20:05, 09:24:55; every 5 s from 09:25:05 until every row is `final`, at most until 09:26:00 | at most 2 a tick, about 12 a minute, about 150 a session, inside the shared Fuyao limiter (60 a minute); a request waits at most 2 s for its slot and a tick that cannot get one is recorded as `rate_limited` and never retried |
| complete auction curve | TDX `fetch_auction_curve` | once per pool stock after the final snapshot | at most 200, one a second by the loop's own pacing; not through the `tdx_public` slot, which is reserved for the shadow snapshot loop |
| Level-2 auction detail | Longhu `GetStockBid` | once per pool stock after 09:25:30 | at most 200, one a second, only after the pulse's window has closed at 09:30 |
| whole-market snapshot | existing loop | unchanged | none added |
| Longhu pulse | existing loop | unchanged at 2 s | none added |

Reasons: before 09:20 orders can be cancelled, so the three extra ticks bracket the cancel cut-off and the
match; the TDX curve gives the dense path in one call per stock, so the Fuyao ticks need not be faster than
10 s; the Longhu gateway already carries 150 calls a minute in this window and gets nothing more until 09:30.

Storage: each Fuyao tick is one document (provider `fuyao_ths`, capability `auction_pool_snapshot`, the rows as
received plus the tick's scheduled time); TDX curves one row per stock and day (`tdx_public`,
`auction_curve_daily`); Longhu detail one row per stock and day (`longhuvip`, `auction_bid_daily`). All through
`persist_timed_observations`, research-only. The three capabilities get explicit entries in
`storage_tiering_policy.py` (same tier and retention as the other optional intraday evidence), and the loop asks
`ResearchStorageAdmission.optional_high_frequency_allowed()` once before 09:15 and stores nothing for the session
when it is refused (`storage_paused`). Expected volume: about 75 documents of up to 200 rows, 200 curves and 200
detail rows a session.

## The 09:25 table

For each pool stock that closed at the limit yesterday, from the first tick whose row is `final` and whose
`pre_close_price` equals yesterday's close (the same check `refresh_auction` applies):

- `gap` = open / yesterday's close - 1; `opens_at_limit` when the open equals today's limit-up price;
- `bucket`: the eight buckets above; each bucket carries its hold-out statistics as constants with the study's
  version (`auction-desk-v1`), and a neutral label of what was measured ("opens at the limit: on average higher
  at the next open", "gap of 5% or more without sealing: on average lower at the close", "gap below -2%: on
  average higher at the close", ...). Labels state measurements, never instructions;
- yesterday's `risk_rank` and `sealed_from_open` from the daily reading.

Stored once as derived reading `auction_open_desk_daily`. Served by
`GET /api/v1/market/auction-desk/daily?date=`. Track record, computed when read: per bucket and day, the
realised open-to-close return, so the page shows whether the table still holds.

## Steps

One Codex dispatch each, under 15 minutes, on `codex/auction-open-desk`.

1. Pure rules: `quant-service/app/auction_open_desk.py`: the timetable (tick times for a session), the pool
   builder, gap and bucket, the constants. Tests for every boundary (09:19:50, 09:20:05, 09:24:55, the 5 s
   phase, the stop at 09:26:00; bucket edges; a row that is not `final`; a `pre_close_price` mismatch).
2. Capture, in two dispatches.
   2a. `fuyao_provider.py`: `fetch` / `fetch_envelope` gain a keyword `slot_wait_seconds` (default the existing
   15 s constant, so no caller changes); tests for the default and for a short budget raising the limiter's
   refusal. `runtime_executors.py`: a boundary `auction_capture` built exactly like `tdx_shadow`
   (`runtime_executors.py:149-150`, `:184-186`, status at `:194`, shutdown at `:203`, `__all__`): one worker, no
   queue, `run_auction_capture_blocking(action, *args, timeout_seconds)`. The loop's database work (session
   gate, pool read, writes) runs there, so it can neither wait behind the scans nor delay them.
   2b. `quant-service/app/auction_pool_runtime.py`: a leased loop `auction_pool_capture`, with every composition
   point named: the label in `background_task_catalog.build_specs()`, the entry in
   `platform/runtime_task_registry.py`, setting `AUCTION_POOL_CAPTURE_ENABLED` (default true) and
   `AUCTION_POOL_MAX_SYMBOLS` in `settings.py`, and in `main.py` the runtime construction, the enabled map and the
   loop factory (`main.py:4131-4169`; wiring only). The runtime calls the synchronous
   `market_observation_session` through its own runner, as `tdx_shadow_snapshot_runtime.py` does. It sleeps to the
   next timetable instant rather than at a fixed interval, calls Fuyao with `slot_wait_seconds=2`, and runs the
   TDX curves and the Longhu detail as two follow-up passes, each paced at one call a second by the loop itself. Tests with fake clocks and fake sources: tick order, a refused tick, a failed source, the stop time,
   `no_pool`, `storage_paused`, nothing outside the session, and a composition test that the service wires the
   loop to `run_auction_capture_blocking`.
3. Table and route: the derived reading at the first final tick, the route in
   `routers/market_radar_reads.py`, `indicator_registry.py` and `indicator_health.py` entries (the check fails
   when an open session has no table by 09:27). Tests: pure, repository, HTTP.
4. Console and relay: GET mapping in `feishu-relay/adapter/research-routes.mjs`; `npm run api:generate`; a
   lazy tab `frontend/src/views/research/AuctionDeskTab.vue` with pure helpers in
   `frontend/src/research/auction-desk.ts`: the table, the bucket statistics, the track record, a visible
   "研究用途，不构成操作建议" label.
5. Decision record `docs/decisions/0017-auction-open-desk.md`: what is collected and why at these times, the
   evidence and its limits, the failed hypotheses, research-only status.

## Acceptance

```bash
make -C quant-service test-release-path
python3 -m pytest scripts -q
node scripts/verify-api-contract.mjs
cd frontend && npm run api:check && npm run typecheck && npm run build
cd feishu-relay && npm test
git diff --check
```

After release, on the first session: list the Fuyao ticks stored against the timetable (scheduled, stored,
`rate_limited`); report the share of the pool with a final row by 09:26:00 (the table is built from the stocks
that have one and states its coverage); the TDX and Longhu passes report their counts; and the whole-market
snapshot, the pulse and the teacher auction refresh kept their own cadence (compare their row counts for
09:15-09:30 with the day before).

## Risks

- Fuyao may not serve indicative rows before 09:25 (only the final snapshot has been used so far). The first
  session's ticks answer that; if they are empty before 09:25, the path comes from the TDX curve alone and the
  pre-09:25 Fuyao ticks are dropped in a follow-up.
- A pool of 200 costs two Fuyao requests a tick; the cap is a setting. The loop never retries a refused tick.
- From 09:25:05 the teacher auction refresh calls the same Fuyao endpoint (at most once in 30 s) and the
  whole-market snapshot keeps its minute. All of them draw on the one Fuyao limiter, which is the shared
  schedule; the new loop yields after 2 s instead of the default 15 s, so it is the one that gives way.
- The table's statistics are averages over a year and a half. A bucket with 2% of the stocks has wide day-to-day
  variation; the page shows each bucket's sample size.
- On 2026-10-12 both existing auction collectors started about 100 seconds late (09:16:37, 09:16:45). The new
  loop will show whether that is a start-up problem of that day or a gate problem; fixing the existing loops is
  not part of this plan.

## Out of scope

- A Feishu card for the table (a follow-up once the table has run for a few sessions).
- Any buy signal: no bucket that can be bought had a positive open-to-next-open return.
- Rules for yesterday's broken boards: they are collected, not judged; there is no tested rule for them.
- Using auction volume, unmatched volume or the path in the table: they are collected first and tested later.
- Raising the frequency of the Longhu pulse or of the whole-market snapshot.

## Plan-audit round 1 (Codex, 2026-10-12): adjudication

3. Broken boards are `limit_open_pool`, and there is no prior-session pool helper for them. Accepted: pool builder corrected.
4. Blocker: the Fuyao fetch cannot take a slot without the 15 s wait. Accepted: step 2a adds `slot_wait_seconds`.
5. Runtime composition points were not named. Accepted: step 2b names catalog, registry, settings and `main.py`.
6. Storage admission and tiering. Accepted: explicit tiering entries and one admission check before the session.
7. "One dedicated boundary" was unspecified. Accepted: a boundary `auction_capture` built like `tdx_shadow`.
8. Contention with the teacher refresh and the whole-market snapshot. Partly accepted: the acceptance is now a
   coverage report and the loop waits 2 s at most. Rejected: a separate single-flight coordinator; the Fuyao
   limiter already is the shared schedule of these callers, and a second one would duplicate it.
9. `GetStockBid` classification. Accepted: stated under "What exists"; nothing about it changes.

Round 2: one minor objection (the watchlist reader was not named). Accepted. The rejection in objection 8 was
confirmed: every Fuyao caller named there goes through the limiter in `fetch_envelope()`.

Review (2026-10-12): finality is per pool symbol; the curve pass does not take the `tdx_public` slot; a snapshot row with a NaN previous close is rejected (pinned by a test). Two reviewer findings were rejected with reasons (late worker thread of a single curve request; unrefunded slot wait).
