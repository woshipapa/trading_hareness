# Plan: tdx-live-snapshot-fallback (revision 2)

Decision 0015 (`docs/decisions/0015-tdx-may-be-decision-evidence.md`, operator, 2026-10-12): TDX
joins the production collection chain and may be decision evidence under the catalog's own rule.
This plan wires the intraday all-A snapshot. Revision 2 replaces the first plan after its audit
found that the snapshot feeds a signal path and that provenance is hard-coded in several places.

## Facts

- One source today: `_fetch_intraday_all_a_snapshot_rows` (`quant-service/app/main.py:1708-1710`)
  → `fuyao_all_a_snapshot_rows()`, behind `SharedAsyncSnapshot` (30 s TTL, `main.py:1713-1717`,
  `quant-service/app/intraday_cross_section.py:36-71`). It feeds the level1 capture
  (`main.py:3143-3151`, `quant-service/app/level1_snapshot_runtime.py`) and the scans, rotation,
  paper execution, board drilling and the limit-up anchors (`main.py:2681-2720,3218-3222`,
  `paper_auto_execution_runtime.py:102-108`, `board_flow_drill_runtime.py:52-60`).
- Provenance is hard-coded as Fuyao: `quote_from_fuyao` sets `price_source`
  (`intraday_quote_normalization.py:191-209`); derived volume is labelled `fuyao_ths_derived`
  (`intraday_derived_flow_metrics.py:138-155`), the only cross-sectional flow label the signal
  rules accept (`intraday_signal_rules.py:81-108`); `annotate_flow_snapshot_provenance`
  (`intraday_features.py:220-236`) decides flow eligibility from freshness and cross-section status
  only, and `live_policy.py:64-83` uses that for entry decisions; `intraday_scan_source_status.py:53-71`
  and `intraday_scan_preparation.py:79-87` name Fuyao; level1 storage and the minute documents use a
  fixed provider key (`level1_snapshot_runtime.py:64-67,87-93`, `minute_cross_section.py:39-42,
  180-205,276-283`); provider health knows one all-A entry (`realtime_provider_health.py:12-22`).
- Every Fuyao failure is a `FuyaoProviderError` (refusal by our rate limiter, HTTP status,
  transport, JSON or value errors, business errors; `fuyao_provider.py:84-130`). An open Fuyao
  circuit is not raised by the client; `MarketSnapshotActions.build`
  (`market_snapshot_actions.py:284-347`) checks it and goes to its own AKShare fallback.
- TDX: `tdx_public quote.all_a_snapshot`, `declared`, adapter
  `app/datasources/sources/tdx_legacy_misc.py:fetch_all_a_snapshot`; the `BindingSpec`
  (`catalog.py:319-326`) holds the projection. Its blocking work runs in a thread
  (`tdx_protocol.py:518-519`); `FAILOVER_SECONDS` stops starting further hosts but never cuts a
  running operation (`:466-499`). `acquire_public_provider_slot` gives an unknown provider key a
  default of 120 a minute (`public_provider_rate_limits.py:56-64,113-130`).
- Agreement, settled values after the close: all nine fields equal or within a lot on 5,560
  symbols (`scripts/data/tdx_fuyao_all_a_snapshot_agreement_2026-10-12_owner.json`); real rows of 16
  symbols from both sources are in `quant-service/tests/fixtures/all_a_snapshot_fuyao_tdx_20261012.json`.
  Intraday timing differences are not measured.
- The catalog's only eligibility rule: a `decision_eligible` binding must be `live_verified`
  (`catalog.py:1123`). `tdx_public quote.all_a_snapshot` is `declared`, so it is not eligible today.

## Design

One rule carries the whole change: **a snapshot says where it came from and whether its source
is decision eligible, and every consumer uses what the snapshot says instead of assuming Fuyao.**

- The snapshot's metadata gains `provider_key` and `decision_eligible`. For Fuyao they are
  `fuyao_ths` and what the code grants today (unchanged behaviour). For the fallback they are
  `tdx_public` and the `decision_eligible` of the `tdx_public quote.all_a_snapshot` binding read
  from the catalog at call time. Today that is `False`: a fallback minute then feeds the minute
  document, the radar and the intraday temperature, and the signal path treats its flow as not
  eligible (fail closed). When the binding is promoted and marked eligible (decision 0015), the same
  code path yields eligible evidence with no further change.
- Rows carry their own `price_source` (`tdx_public_all_a_snapshot` for the fallback); nothing
  relabels a TDX row as Fuyao.

## Steps

### Step 1: the fallback snapshot in the snapshot's shape (pure) and the chain

New module `quant-service/app/all_a_snapshot_sources.py`; `main.py` only wires it.

- Projection: TDX adapter rows → the rows `normalize_snapshot_rows` produces, with
  `price_source` = `tdx_public_all_a_snapshot`, `price_observed_at` = collection time, units from the
  binding's `BindingSpec`, and `raw` holding the Fuyao key names the intraday consumers read
  (`open_price`, `high_price`, `low_price`, `prev_price`; `xiaojie_indicators.py:85-97`,
  `market_temperature_intraday.py:106-119`, `minute_cross_section_export.py:25-30`) plus `thscode`,
  `last_price`, `price_change`, `price_change_ratio_pct`, `turnover`, `volume` for the document's
  shape. A non-positive price is dropped, as Fuyao's normalizer does. Test against the fixture:
  for the symbols both sources have, the projected row equals the Fuyao row field by field (volume
  within 100 shares, turnover within 1,000 yuan); `600293.SH`, which Fuyao lacks, projects cleanly.
- Metadata as `fuyao_provider.py:212-229` builds it, with `source` = `tdx_public_all_a_snapshot`,
  `provider_key`, `decision_eligible` (from the catalog binding), `fallback_for` = `fuyao_ths`,
  `fallback_reason` (bounded text of the Fuyao error). `fuyao_all_a_snapshot_rows` gains the two new
  keys for its own source.
- Chain `async def read(fuyao, tdx)`: await Fuyao; on `FuyaoProviderError` read TDX once. Anything
  else propagates. If TDX fails too, raise the TDX error chained from the Fuyao one.
- Bounds:
  - a declared per-minute limit for `tdx_public` beside the others in
    `public_provider_rate_limits.py` (not the 120 default) and
    `acquire_public_provider_slot("tdx_public", max_wait_seconds=0)`; a refusal is a TDX failure;
  - the TDX read is one shared task: a second caller while it runs joins it, and a caller that
    stops waiting (`asyncio.wait_for` over `asyncio.shield`) does not cancel it or start another.
    Reuse `SharedAsyncSnapshot` for this if its semantics fit (single flight while active, a failed
    task is not cached), otherwise say why not. So at most one TDX thread exists at any time,
    whatever the callers' timeouts (the scan abandons the shared snapshot after two seconds,
    `intraday_watch_quote_capture.py:266-273`); state the worst-case duration of that one thread
    from `tdx_protocol`'s timeouts in the docstring;
  - the chain's caller waits for TDX no longer than a bound below the capture interval.
- `_fetch_intraday_all_a_snapshot_rows` calls the chain. `MarketSnapshotActions.build` keeps its
  circuit check and its AKShare fallback: that path is out of scope, and the docstring of the chain
  says the fallback covers reads that reach the Fuyao client and fail there.
- Tests: Fuyao answers → TDX not called; `FuyaoProviderError` → TDX rows and metadata; another
  exception propagates, TDX not called; both fail → chained error; refused slot; a TDX stub that
  never answers: the caller returns within its bound, a second call does not start a second TDX
  read, and the first one's eventual result is not lost to an exception warning.

### Step 2: consumers use what the snapshot says

For each site, replace the Fuyao assumption with the snapshot's own provenance; behaviour for a
Fuyao snapshot must be byte-for-byte what it is today (existing tests unchanged).

- `quote_from_fuyao` (`intraday_quote_normalization.py:191-209`) and its callers
  (`main.py:2681-2720,3143-3151`): the row's `price_source` is kept; `observation_source()`
  (`:134-147`) and the source catalog (`datasources/catalog.py:878-900`) know
  `tdx_public_all_a_snapshot`.
- Derived flow (`intraday_derived_flow_metrics.py:138-155`): the label names the provider the
  volume came from (`tdx_public_derived` for the fallback). `annotate_flow_snapshot_provenance`
  (`intraday_features.py:220-236`) also requires the snapshot's `decision_eligible`. The signal rules
  (`intraday_signal_rules.py:81-108`) accept a derived cross-sectional flow label of an eligible
  provider instead of the one literal; with the binding not eligible, a fallback flow is rejected
  there exactly as a stale Fuyao flow is. `live_policy.py:64-83` then needs no TDX-specific branch;
  confirm with a test on both sides of the binding's flag.
- `intraday_scan_source_status.py:53-71` and `intraday_scan_preparation.py:79-87`: the status and
  the recorded success name the provider that served the snapshot.
- `paper_auto_execution_runtime.py:102-108`, `board_flow_drill_runtime.py:52-60`,
  `limit_up_anchor.py:46-65` with `main.py:3218-3222`: read how each uses the rows. Where it stores
  or reports a source, it stores the row's; where it would act on a row (paper execution), it acts
  only on a snapshot whose `decision_eligible` is true. If any of the three cannot be made to follow
  the snapshot's provenance at one place, stop and report.
- Storage: the level1 capture persists under the snapshot's `provider_key`
  (`level1_snapshot_runtime.py:64-67,87-93`); minute documents are written under it and read by
  capability across providers (`minute_cross_section.py:39-42,180-205,276-283`: `latest`,
  `latest_async`, `first_between`, `day_documents`, `latest_level1_items`), and the indicator-health
  query that names the provider (`indicator_health.py:178`) likewise. The document already stores
  the snapshot metadata. Existing rows are untouched; no migration.
- Health: `level1_capture.persist_health` (`level1_snapshot_runtime.py:147-161`) records a Fuyao
  failure with its error and a `tdx_public` success when the fallback served the minute; the TDX
  all-A capability gets its alias in the catalog (`datasources/catalog.py:28-37`) and its entry in
  `realtime_provider_health.SOURCES` (`:12-22`). `market.minute_documents` in indicator health gains
  the count of today's fallback minutes (`ok` at zero, `warn` above zero with the count).
- Tests: one per site, each with a Fuyao snapshot (unchanged output) and a fallback snapshot, the
  latter with the binding's flag false and true where eligibility is involved.

### Step 3: the way to eligibility

- The catalog declares an agreement for `tdx_public quote.all_a_snapshot` against the Fuyao
  snapshot (reference adapter `app/fuyao_provider.py:all_a_snapshot_rows`, the fields and tolerances
  of the agreement file, with a tolerance for price and volume that allows the seconds between the
  two reads inside a session; say how the tolerance was chosen and that the first in-session check
  will show whether it holds).
- `scripts/tdx-promote.py apply` takes `--decision-eligible <decision record>` on the
  `declared → live_verified` step only: it then also sets `decision_eligible=True` on that binding
  and records the decision reference in the commit text it prints; without the flag it behaves as
  today. The catalog's rule (`catalog.py:1123`) stays the guard. Tests in
  `scripts/test_tdx_promote.py`; the tests that assert every TDX binding is never eligible
  (`tests/test_datasource_catalog.py:192,203,209`, `tests/test_datasource_tdx_mac.py:628-633,750`)
  now assert the catalog rule instead (eligible implies live verified) and today's values.
- `docs/DATA_SOURCE_LAYER.md` (§3 priority table, §3.1, §6 scheduling, §7.1) and
  `docs/ARCHITECTURE.md` describe the chain, the provenance rule and the promotion flag.

No binding's status or eligibility changes in this plan. The planner runs the in-session check on
the next trading day and applies the promotion.

## Acceptance (from the worktree root)

```
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path QUANT_TESTS="tests.test_all_a_snapshot_sources tests.test_level1_snapshot_runtime tests.test_minute_cross_section tests.test_background_loops tests.test_datasource_catalog tests.test_datasource_tdx_mac"
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q
cd quant-service && /Users/papa/.venvs/svc/bin/python -m app.datasources validate
/Users/papa/.venvs/svc/bin/python scripts/verify_component_boundaries.py --check
git diff --check && git diff --check main...HEAD
```

Add to the first command every test module Step 2 touches and name them in the report. Do not
read, print or copy the env file. No network request and no live read.

## Commits

One per step (Step 2 may be several, one per consumer group) on `codex/tdx-live-snapshot-fallback`.
No push. Do not stage `.env.example`, `.gitignore`, `config/secrets/*`, `feishu-relay/**`,
`scripts/svc_supervisor.py`, `scripts/test_svc_supervisor_tasks.py`, `scripts/data/*`.

## Verification after release (planner)

A Fuyao failure cannot be forced in production. On the next session: `tdx_public` snapshot
successes in provider health only at minutes where Fuyao failed; the fallback-minute count; the
largest gap between minute documents; one fallback minute's document against the neighbouring
Fuyao minute; that scans on a fallback minute report a not-eligible flow while the binding is
`declared`. Roll back by releasing the previous version.

## Risks

- This changes code on the signal path (source labels, eligibility). The control is that a Fuyao
  snapshot's outputs are unchanged, pinned by the existing tests plus one unchanged-output test per
  site.
- Reading minute documents across providers: two documents can exist for one minute only if both
  sources succeeded, which the chain does not do; the readers keep "newest per minute".
- Timing inside a session is unmeasured until the first in-session agreement check.

## Out of scope

The limit pools and the minute bars; `MarketSnapshotActions`'s AKShare fallback and the post-close
path; changing any binding's status or eligibility; the console.
