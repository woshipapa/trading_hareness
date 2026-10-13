# Limit-up big-loss risk score (research-only daily indicator)

Status: revision 2, 2026-10-12, after the independent audit of the study (`state/research/divergence_study_20261012/audit/AUDIT.md`) and plan-audit round 1 (nine objections, adjudicated at the end). Approved in principle by the user on 2026-10-12 ("按照你说的这几个推进让codex去执行").

## Goal

After each close, give every stock that closed at its limit-up price a **big-loss risk score** (fixed
coefficients): high means a high chance of closing 5% or more lower the next day. Show it in the quant console
beside the plain facts it is built from (seal ratio, main net, volume ratio, first seal time, boards, float
value, whether the board was sealed from the open).

It is research evidence with `live_effect: none`. Nothing here feeds a threshold, a candidate list or an order
path. The page also shows how the score did on the days already scored, so a decay is visible.

The audit removed the second score of the first draft. The six-item "distribution score" separates next-day
returns mostly because boards sealed from the open continue; without them its spread is under one point. It
is not shipped as a score; `sealed_from_open` is shown as a fact instead.

Evidence and definitions: `state/research/divergence_study_20261012/grab_dump/REPORT_抢筹出货判断.md`, section 六,
and `PREREG_grab_dump.md`. Hold-out (2026-01-01..2026-09-18, 12,313 limit-up closes): big-loss rate of the lowest against the highest
risk fifth 8.5% against 18.9% (the audit recomputed 8.6% and 19.0%, t above 4 in each quarter, unchanged when
boards sealed from the open, 20%-limit boards or later boards are removed). The development figure was 5.2%
against 29.2%: expect the hold-out size, not the development size.

## What exists (verified 2026-10-12)

- `quant-service/app/market_history_backfill.py`: `longhu_limit_performance` fetches the Longhu
  `DailyLimitPerformance` lists (`PidType` 1-5) from target `longhu_history` and `walk_source` stores one row per
  (list, day) in `quant.raw_market_observations` as provider `longhuvip_history`, capability
  `limit_performance_history`, symbol `pid:N`. Stored for 739 sessions, 2023-09-01..2026-09-18. Nothing appends
  to it daily; it was an operator backfill.
- The history target returns an empty list for the current day (seen 2026-10-12 19:36); the live target
  (`longhu_quote`, controller `HomeDingPan`, same parameters without `Day`) returns the day's list in the same
  23-field row layout. `walk_source` stores an empty list and then treats the day as done.
- Row layout, checked against daily bars and minute series: [0] code, [1] name, [4] first seal time (epoch
  seconds), [5] sector, [6] closing seal money, [7] maximum seal money, [8] main net (= [9] buy + [10] sell),
  [11] turnover, [13] free-float market value, [14] turnover rate, [15] consecutive boards, [16] opened after
  sealing (0/1), [21] price, [22] percent change. [17] and [20] are not decoded and are not used.
- Derived daily readings: `quant-service/app/derived_daily_readings.py` (`store`, `newest`), used by
  `market_temperature_runtime.refresh` and wired as post-close stage `market_temperature` in
  `quant-service/app/post_close_refresh_service.py` (`POST_CLOSE_STAGE_ORDER`, the stage map near line 345).
- Read routes: `quant-service/app/routers/market_radar_reads.py` (`/api/v1/market/temperature/daily`); relay
  mapping `feishu-relay/adapter/research-routes.mjs`; page `frontend/src/views/research/SentimentTemperatureTab.vue`
  with helpers in `frontend/src/research/sentiment-temperature.ts`; tab registered in `frontend/src/App.vue`.
- Health: `quant-service/app/indicator_health.py` (`CHECKS`), `quant-service/app/indicator_registry.py`.

## Definitions (must reproduce the study exactly)

Lists of day T: the history lists (`longhuvip_history` / `limit_performance_history`) when a non-empty one is
stored for T, otherwise the live lists stored by step 1 (`longhuvip` / `limit_performance_live`). The reading
records which one it used as `list_source` (`history` or `live`); the vendor's history is its final version of
a day, so it supersedes the live capture once it exists.

Population of day T: list rows with at least 23 fields whose code does not start with 4 or 8, de-duplicated by
symbol, joined to the stock's canonical daily bar of T, and kept when all of these hold (the study's rules):

- limit-up price = the bar's own `limit_up` in `quant.canonical_bars_daily` when it is set, otherwise the newest
  `quant.daily_trade_limits` up-limit of T (the order `market_temperature_repository` uses and the study's export
  used), otherwise round(previous close x (1 + 0.20 for codes starting 30 or 68, else 0.10), 2);
- limit ratio = round(limit-up price / previous close - 1, 2) is at least 0.08 (5%-limit stocks are excluded);
- close >= limit-up price - 0.005;
- at least 10 valid bars exist among the stock's last 20 valid bars in the 90 calendar days before T (`vr` uses
  the mean turnover of those bars). A valid bar is not suspended and has open, close and previous close above 0;
  turnover passes the unit guard of `market_temperature_repository`.

Every dropped row is counted by reason in `coverage`.

Per stock:

| name | definition | known at |
|---|---|---|
| `vr` | turnover of T / mean turnover of the stock's last 20 stored bars before T (at least 10) | close |
| `gap` | open / previous close - 1 | open |
| `intraday` | close / open - 1 | close |
| `flow` | list main net [8] / list turnover [11] | close |
| `seal_ratio` | closing seal money [6] / list turnover [11] | close |
| `seal_minutes` | first seal time [4] in minutes after 09:30; at or before 09:30 is 0; afternoon minus 90 | at sealing |
| `opened` | [16] | close |
| `boards` | [15] | close |
| `float_cap` | [13] | close |

`rank(x)` is the within-day percentile rank (average for ties) minus 0.5.

- Risk score = sum of coefficient x feature over rank(vr), rank(flow), rank(intraday), rank(gap), rank(seal_ratio),
  rank(seal_minutes), opened minus the day's mean of opened, rank(boards), rank(float_cap), with the `loss5`
  coefficients of `state/research/divergence_study_20261012/seal/fitted_coefs.json` copied into the module as
  constants (nine numbers, with the file's SHA-256 in a comment).
- Each stock gets `risk_rank`: the 0-100 percentile of its risk score within the day, and
  `sealed_from_open` (first seal at or before 09:30).
- On 10%-limit boards `gap` and `intraday` are the same information with opposite sign (their within-day ranks
  correlate -1.00). The two coefficients are kept as fitted, because that is the model that was validated; the
  page shows no per-item contribution for these two.
- A day with fewer than 20 scored stocks stores the rows but marks `ranks_reliable: false`.
- `VERSION = "limit-up-risk-v1"`. Any change to an item, a sign or a coefficient is a new version.

Outcomes for the track record, computed when read, never stored: next-day return (close to close) and
`big_loss` (next-day return <= -5%) from canonical bars. Per scored day with at least 20 stocks: big-loss rate and mean next-day return of the lowest and highest risk
fifth. "Next day" is the next open session of the SSE calendar, never the next stored row: the owner's daily
bars hold 13 rows dated on a weekend (2026-08-30, 2026-09-12), which silently broke row-adjacent alignment in
the study.

## Steps

Each step is one Codex dispatch of under 15 minutes, committed on `codex/limit-up-distribution-scores`.

### 1. Daily append of the limit-performance lists (owner-quant)

- `market_history_backfill.py`: add `longhu_limit_performance_live(call)`, the same five calls against target
  `longhu_quote`, controller `HomeDingPan`, without `Day` (allowed: `licensed_stock_api.py:99`). Add
  `append_limit_performance(day, ...)` that stores the five lists of one day through the existing `store` under
  provider `longhuvip`, capability `limit_performance_live` (its own provenance: these rows did not come from
  the history interface), with `meta.target = "longhu_quote"`, and refuses to store a day whose five lists are
  all empty (returns `{"status": "empty"}`), so an empty answer never marks a session as done.
- Fix `walk_source` the same way for source `longhu_limit_performance`: an all-empty trading day is reported in
  `empty` and not stored.
- Post-close wiring, all four places: a new injected callable on the post-close dependencies
  (`post_close_refresh_service.py:223-235`), the stage in `POST_CLOSE_STAGE_ORDER` after `full_market_daily`
  with a 120 s entry in `POST_CLOSE_TIMEOUT_OVERRIDES`, its action in the stage map (`:370-376`), and the
  production wiring in `main.py` `_post_close_refresh_dependencies()` (`main.py:3819-3860`) using the
  licensed-API caller the other Longhu stages use. `main.py` gains wiring only.
- `storage_tiering_policy.py`: explicit rules for `limit_performance_live` and for `limit_performance_history`
  (the history capability has none today, `storage_tiering_policy.py:47-55`), in the tier the other daily
  list evidence uses.
- Tests: an all-empty day is not stored and is fetched again; a non-empty day is stored once (second call writes
  nothing); the live fetcher sends no `Day` and the `HomeDingPan` controller; the stage order and its receipt.

### 2. Scores, reading and track record (owner-quant)

- New `quant-service/app/limit_up_risk.py` (pure): parse list rows, compute features, ranks, the risk score
  and the per-day summary; `track(days)` computes the fifth comparisons from scored days and next-day returns.
- New `quant-service/app/limit_up_risk_repository.py`: read the lists of a day by the rule above, the bars of T,
  each listed stock's last 20 stored bars before T, the day's up-limits, and the next open session's bars for the
  track record (next session from the SSE calendar).
- New `quant-service/app/limit_up_risk_runtime.py`: `refresh(database, trade_date)` stores one reading per
  day as capability `limit_up_risk_daily` through `derived_daily_readings.store`; `daily(database, day)`
  and `track(database, days)` for the routes; CLI `python -m app.limit_up_risk_runtime --start D --end D
  [--apply]` for the history.
- Post-close stage `limit_up_risk` after `limit_performance_lists`, budget 120 s, wired like `market_temperature`
  (`dependencies.run_database(refresh, dependencies.database, trade_date, ...)`), with `limit_performance_lists`
  in `POST_CLOSE_STAGE_DEPENDENCIES`; stage-order and receipt tests.
- `routers/market_radar_reads.py`: `GET /api/v1/market/limit-up-risk/daily?date=` and
  `GET /api/v1/market/limit-up-risk/track?days=250`. Both carry `research_only: true`,
  `live_effect: "none"`, `version`, and `stale_versions` like the temperature route.
- `indicator_registry.py` and `indicator_health.py`: entry `market.limit_up_risk`; the check fails when
  the latest open session has no reading, or its version differs from `VERSION`.
- Tests: the parity fixture `quant-service/tests/fixtures/limit_up_risk_20260918.json` (already in the worktree: the
  five raw lists of 2026-09-18, each listed stock's bar and prior turnovers, the nine coefficients, and the
  study's features, risk and risk rank for the 76 scored stocks) must be reproduced to 1e-9, including the one
  listed stock that is not scored and the two malformed list rows; ties; a day with
  fewer than 20 stocks; a missing bar is counted in `coverage`; the track record on a three-day synthetic series;
  HTTP tests for both routes.

### 3. Console (edge-relay and edge-quant-console)

- `feishu-relay/adapter/research-routes.mjs`: GET mappings for the two routes. Another session is editing
  `feishu-relay/adapter/index.mjs`; this step touches only `research-routes.mjs` and its test.
- `frontend`: `npm run api:generate`; new `frontend/src/research/limit-up-risk.ts` (pure helpers, tested)
  and `frontend/src/views/research/LimitUpRiskTab.vue`: the day's table (name, boards, first seal time,
  sealed from the open, seal ratio, flow, volume ratio, float value, risk rank), sorted by risk rank; the track
  record as one chart; a
  visible "研究用途，不构成操作建议" label and the version. Registered as a lazy tab in `frontend/src/App.vue`
  without adding logic to the shell.

### 4. Decision record

- `docs/decisions/0016-limit-up-risk-score.md`: what the score is, the evidence with its limits (the
  decay from development to hold-out, limit-up boards cannot be bought at the close, the return is overnight),
  why they are research-only, and what would justify a promotion review.

## Acceptance

```bash
make -C quant-service test-release-path
python3 -m pytest scripts -q
node scripts/verify-api-contract.mjs
cd frontend && npm run api:check && npm run typecheck && npm run build
cd feishu-relay && npm test
git diff --check
```

After release: fill 2026-09-19..2026-10-12 with `python -m app.market_history_backfill 2026-09-19 2026-10-12
--sources longhu_limit_performance` (outside 09:00-15:45), run `python -m app.limit_up_risk_runtime --start 2023-09-11 --end <today> --apply`, then check that
`/track?days=250` shows a big-loss rate of the lowest and highest risk fifth close to the study's hold-out
figures (8.6% and 19.0% over 2026-01-01..2026-09-18) and that the health check passes.

## Risks

- The live list is read once after the close. If the vendor revises it later, the stored list of that day
  differs from what the history interface would return. The stage stores what it saw; the reading names the
  list's `available_at`.
- Closing seal money is an end-of-day figure; a late-session reading before 15:00 would not be the same number.
- The score ranks stocks within one day. It says nothing about whether the day itself is good.
- The separation halved from development to hold-out. The track record on the page is the real out-of-sample
  test; the decision record states the size that would count as "no longer working".
- Release units: steps 1-2 are an owner code release; step 3 is an edge relay hotfix and a console deploy.

## Out of scope

- Any use of the scores in candidates, thresholds or alerts.
- Market-level grab / distribution gauges (the radar already bands the whole market by +-2%; a percentile
  context for them is a separate, smaller plan).
- The falling-day "main outflow" list: its evidence with the vendor available in production covers 27 sessions.
- An intraday (at sealing) version: the study of it is not finished.
- The auction desk at 09:25: `.claude/plan/auction-open-desk.md`, which reads this plan's daily reading.
- Daily archive of Level-2 entrust lists: a separate plan.

## Plan-audit round 1 (Codex, 2026-10-12): adjudication

1. Post-close wiring names only one file. Accepted: step 1 lists the injected callable, order, timeout, stage map
   and `main.py` composition; step 2 follows the `market_temperature` pattern.
2. Provenance of the live list. Accepted: the live capture has its own provider/capability
   (`longhuvip` / `limit_performance_live`); the reading rule and `list_source` are in "Definitions".

Round 2: one minor objection (no tiering rule exists for the history capability). Accepted: step 1 adds both rules.

Review round 1 (2026-10-12): the plan's up-limit sentence was imprecise and is corrected above; the reviewer's finding against the code on this point was rejected.
Review round 2: the live append stores only non-empty lists and treats a vendor error payload as a failure; bar queries require close > 0; the 90-day bound on prior bars is now part of the definition.
