# Review: `codex/tdx-live-snapshot-fallback` (`c5da5fa0..fd0d30e2`, W008 B1 + B2)

Read-only review of five commits (`99f8d5de`, `ae3800aa`, `af6f1321`, `30c055ec`, `fd0d30e2`): 28 files, +2146/-135, of which about 1,440 lines are tests.
Paths below are relative to `/Users/papa/codebase/.codex-wt/n8n-tdx-fallback/quant-service/app/` unless they start with `tests/`.

Test run (release path, throwaway database): `tests.test_all_a_snapshot_sources` and `tests.test_tdx_shadow_snapshot_runtime`, 46 tests, OK. The full suite was not run.

Counts: 1 high, 4 medium, 8 low.

## Verdict on "a status with no `decision_eligible` key counts as eligible"

No real code path today produces a TDX-derived status without the key, so the branch is not fail-open through this default. It should still be tightened before merge, because the default is the wrong way round for a fail-closed gate and one refactor away from a leak.

What I traced:

- `all_a_snapshot_sources.read` always returns `tdx_snapshot_metadata(...)`, which sets `decision_eligible`, `provider_key` and `fallback_for` (`all_a_snapshot_sources.py:115-134`). `fuyao_provider.all_a_snapshot_rows` always sets `decision_eligible: True` (`fuyao_provider.py:230-231`).
- `intraday_all_a_snapshot` merges `{**supplier_status, **cache_status}` (`main.py:1794-1797`). `SnapshotStatus.as_dict()` has only `status`, `age_seconds` and `ttl_seconds` (`intraday_cross_section.py:28-33`), so the key survives. The merge does overwrite the TDX `status: "unknown"` with `fresh`/`cached` and `age_seconds: None` with the cache age; the implementer's docstrings say so and no gate infers eligibility from `status`.
- `materialize_evidence_status` overwrites `decision_eligible` with the contract's `False` (`platform/evidence_contracts.py:27-41`). That direction is fail-closed. `capture_watch_quotes` saves the snapshot's own value first and hands it to the flow annotation (`intraday_watch_quote_capture.py:281-314`).
- The only key-less statuses in production are `{"status": "unavailable", "error": ...}` (`intraday_watch_quote_capture.py:274,277`), which fail on `status`.
- Paper execution and the board drill pass the merged status straight to `snapshot_decision_eligible` (`paper_auto_execution_runtime.py:119-122`, `board_flow_drill_runtime.py:62-68`), so the key is present.

Why tighten anyway: `snapshot_decision_eligible(None)` and `snapshot_decision_eligible({})` return `True` (`all_a_snapshot_sources.py:146-148`), and `annotate_flow_snapshot_provenance` uses `.get("decision_eligible", True)` (`intraday_features.py:229`). Any later wrapper that rebuilds the status dict (a new cache layer, a projection that keeps `source` and `provider_key` but not the flag) turns a TDX minute eligible silently.

Suggested rule, which keeps every existing stub passing: a status without the key is eligible only when it also names no other source, i.e. `provider_key` is absent or `fuyao_ths` and `fallback_for` is absent. Apply the same rule at both sites through one helper.

## Findings

### 1. High: the Xiaojie leader-flow strategy records candidates and sends alerts from a TDX-filled scan, with no provenance check

- Where: `intraday_watchlist_scan_service.py:176,380` pass `quote_capture.all_a_rows` to `xiaojie_leader_flow.run` (`main.py:1901`); `xiaojie_leader_flow_runtime.py:153-266`.
- Input: Fuyao fails as a provider, the chain fills the minute, and the scan gets the TDX rows (directly, or from the 30 s cache on the next scan).
- What happens: `run` only checks `if not all_a_rows`. It then calls `evaluate_xiaojie_leader_pool` on the TDX rows, `record_xiaojie_candidates` (strategy candidate table), `persist_signal_event` and `deps.deliver_alert` (the research alert to the user), and `record_launch_radar_observations`. Nothing reads `snapshot_metadata.decision_eligible`, and the candidate evidence carries no source, so a TDX-derived candidate is indistinguishable from a Fuyao one afterwards.
- Before this branch a failed Fuyao read meant `all_a_rows == []` and the run was `skipped`. This is new behaviour created by the chain, and it is the one unchanged reader where a TDX minute reaches a strategy candidate output and a delivered alert. The alerts are research-only (zero live weight, no order), which is why this is not a paper-execution leak; it does break the governing rule that every consumer uses what the snapshot says.
- The plan's B2 site list does not name this consumer, so this is a gap in the plan as well as the code.
- Fix: pass the scan's snapshot status into `run` and return `skipped` (reason: snapshot source not decision eligible) when `snapshot_decision_eligible` is false; if the launch radar should still observe, stamp its rows with the snapshot source.

### 2. Medium: the sentiment reading stores a hard-coded Fuyao tape label on a TDX-filled minute, including the day-close record

- Where: `datasources/collectors/intraday.py:285,304`; `datasources/collectors/post_close.py:142` (reuses `build_sentiment` for `market_sentiment_close`). The collector's `fuyao_snapshot` is `intraday_all_a_snapshot` (`main.py:3142`).
- Input: the Fuyao pool routes answer but the all-A snapshot read fails and TDX fills (or a TDX fill is in the 30 s cache).
- What happens: `market_sentiment_snapshot(snapshot_rows=<TDX rows>)` is stored under `derived_market_sentiment` with `"sources": {"tape": "fuyao_ths_all_a_snapshot"}`. `_meta` is discarded. The same happens for the close record, which is a stored daily fact read by the sentiment cycle (`ten_day_leader_rotation_intraday_service.py:47-49`).
- Classification: mislabels provenance. The plan allows TDX to feed the intraday temperature, but not under a Fuyao label.
- Fix: set `tape` from `_meta.get("source")` (default the Fuyao label when absent) and add `fallback_for` when present.

### 3. Medium: a failed fill is never remembered, so a Fuyao outage with an unusable TDX read repeats the full TDX all-A read on every caller, as often as every 5 s

- Where: `main.py:1763-1767` (`_intraday_all_a_snapshots`, 30 s TTL, failures not cached by `SharedAsyncSnapshot`, `intraday_cross_section.py:52-71`), `all_a_snapshot_sources.py:179-195,208-268`.
- Input: Fuyao is down and the TDX snapshot is incomplete. The plan states this is always the case 09:15-09:25 (share 0.000), and it is also the case when the roster cannot be read.
- What happens: the chain raises `AllASnapshotFallbackError`, nothing is cached, and the next caller of `intraday_all_a_snapshot` (the watch scan runs every 5 s in the early session, plus level1, anchors, drill, paper execution, market events and collectors) starts Fuyao and TDX again. The shared TDX reader reuses a result for 5 s and the `tdx_public` slot spaces starts by 1 s (`public_provider_rate_limits.py:84-98`), so the ceiling is about 12 full reads a minute (roughly 70 pages each) against a public host, for a result known to be rejected. When TDX raises instead, there is no reuse at all and each chain opens a new connection.
- A successful fill is cached 30 s, so the healthy-TDX case is about two reads a minute plus the shadow's; that part is fine. Single flight holds throughout (at most one TDX read in flight; callers join), so there is no task pile-up and no memory growth, only repeated work and repeated load on the public host.
- Fix: remember the last failed fill in the chain for about 30-60 s and re-raise it without touching TDX (Fuyao is still tried each time), or skip TDX outright inside the call-auction window.

### 4. Medium: callers that await the snapshot without their own timeout now wait up to about 30 s longer when Fuyao is down and TDX is slow

- Where: `all_a_snapshot_sources.py:234-238` (20 s bounded wait) plus `main.py:3226-3229` (roster read, up to 10 s); direct awaiters at `main.py:3337` (limit-up anchors), `board_flow_drill_runtime.py:62`, `paper_auto_execution_runtime.py:119`, `market_event_runtime.py:110`, `level1_snapshot_runtime.py:66`.
- Input: Fuyao fails after its own timeout and the TDX host answers slowly or not at all.
- What happens: before, these callers failed as soon as Fuyao did. Now each waits Fuyao's failure time + up to 20 s + up to 10 s before getting `AllASnapshotFallbackError`. The watch scan is protected by its 2 s budget (`intraday_watch_quote_capture.py:271`); the others are not. I did not trace whether the drill and the anchor refresh run inside a loop with a tighter cadence than that (see Unverified).
- Fix: confirm the cadence of each direct awaiter tolerates +30 s, or wrap the chain's whole fallback leg (read + roster) in one deadline.

### 5. Medium: the missing-key default is fail-open by construction

See the verdict above. `all_a_snapshot_sources.py:137-149`, `intraday_features.py:229`. Not reachable for a TDX snapshot today; tighten as described.

### 6. Low: an exception after the TDX read returns escapes as a non-Fuyao error

- Where: `all_a_snapshot_sources.py:248-268`.
- Input: a TDX row missing a key or carrying a non-numeric price (`project_tdx_snapshot_rows` does `row["price"]`, `row["volume_lots"] * factor`), or an `available_at_max` that is not a `datetime`.
- What happens: `KeyError`/`TypeError`/`AttributeError` propagates out of `read`. The scan handles only `(TimeoutError, FuyaoProviderError, ValueError)` (`main.py:2757`) and the anchor refresh a similar tuple (`main.py:3345`), so a malformed fill would fail the scan instead of leaving the Fuyao failure standing. The docstring promises "an untrusted fill is a missing minute".
- Fix: put the projection and metadata build inside the same `try` and raise `AllASnapshotFallbackError`.

### 7. Low: the scan status keeps the key `fuyao_ths_derived_watch_flow` for a TDX-derived status

- Where: `intraday_scan_source_status.py:144`.
- What happens: on a filled scan the value is materialized under the `tdx_public_derived_watch_flow` contract (correct `evidence_contract` and `provider_key` inside), but the dict key that holds it still says Fuyao. Mislabel in the stored scan status only.
- Fix: key it by the contract name, or add the provider beside it.

### 8. Low: limit-up anchors of a filled minute are stored under a second source, and `fuyao_derived` gets no health row that minute

- Where: `limit_up_anchor.py:28-51`, `main.py:3312-3341`.
- What happens: anchors go to `market_events` with `source='tdx_public_derived'` and health to `(tdx_public_derived, limit_pool)`. The label is right. Readers that select `event_type='limit_up_pool'` with no source filter pick them up: the linkage miner's anchors (`async_limit_linkage_relation_repository.py:34-38`), `sector_flow_repository.py:128-131` (counts `DISTINCT symbol`, so no double count), `teacher_review_repository.py:597`, and `_OTHER_POOLS` (`limit_event_repository.py:56-60`). So a TDX-derived anchor can seed research linkage candidates; the anchor row is labelled but the mined output is not. `(fuyao_derived, limit_pool)` records neither success nor failure on a filled minute, so its `last_success_at` ages.
- Fix: decide explicitly whether TDX-derived anchors may seed the linkage miner while the binding is `declared`; if not, filter on source there. Record the `fuyao_derived` failure on a filled minute as level1 does for `fuyao_ths`.

### 9. Low: `tdx_public` realtime health reads `partial` on any filled minute that has suspended stocks

- Where: `realtime_provider_health.py:77-85,98-100`; `all_a_snapshot_sources.py:130-131`.
- What happens: `total` is priced + no-trade rows and `matched_rows` is priced rows, so `received < requested` and `observation_state` returns `partial` even for a complete fill.
- Fix: report `total` as the priced count for the health projection, or treat no-trade rows as received.

### 10. Low: settlement price paths treat the two sources inconsistently

- Where: `intraday_outcome_settlement.py:27,40-48,62-67`.
- What happens: `PRICED_QUOTE_SOURCES` excludes `tdx_public`, so TDX-labelled rows of `intraday_quote_observations` are skipped (fail-closed, a gap in the path). The `watch_scan_tape` branch has no source filter, so a TDX price recorded by the scan tape does enter the same series. Research outcome evaluation only; no decision.
- Fix: pick one rule; if TDX prices may settle outcomes add the provider to the tuple, otherwise filter the tape by source.

### 11. Low: the stored document of a filled minute says `status: "fresh"`

- Where: `main.py:1796-1797`, `level1_snapshot_runtime.py:76,108`.
- What happens: the cache status overwrites TDX's `status: "unknown"`, so the per-row `snapshot_metadata.status` and the capture's `freshness_status` read `fresh` beside `freshness_reason: "no_upstream_timestamp"`. The same overwrite already applied to Fuyao's upstream freshness before this branch.
- Fix: keep the supplier's value under `upstream_status` before the merge.

### 12. Low: `latest()` has no tie-break between providers

- Where: `minute_cross_section.py:213-215,248-257`.
- What happens: I found no way for a Fuyao and a TDX document to share an `effective_at`: the level1 loop is the only writer, it makes one fetch per capture, and one snapshot has one provider. The advisory-lock key and the existence check include the provider (`minute_cross_section.py:195-201`), so if a second writer ever appeared both would be stored and `ORDER BY effective_at DESC LIMIT 1` would pick either. `day_documents` would return both.
- Fix: add a deterministic secondary order (main source first).

### 13. Low: `market_event_runtime` and the valuation job use the TDX code list for Fuyao batch requests

- Where: `market_event_runtime.py:104-114`, `datasources/collectors/post_close.py:236-237`.
- What happens: only the symbol list is used, so no price or label leaks. The comment at `market_event_runtime.py:105-107` says Fuyao rejects a whole 100-code batch for one code it does not know, and the TDX list is not Fuyao's (it also omits no-trade names). Harmless for provenance; possible batch rejections or missing valuations on a filled minute.
- Fix: fall back to the local universe when the snapshot's `provider_key` is not the main source.

## Classification of the readers the implementer left unchanged

| Reader | Classification |
|---|---|
| `datasources/collectors/intraday.py` sentiment (`tape` label) | Mislabels provenance (finding 2) |
| `market_event_runtime.py:110` | Harmless: code list only (finding 13) |
| `collectors/post_close.py:236` valuation job | Harmless: code list only (finding 13) |
| `collectors/post_close.py:142` close sentiment | Mislabels provenance (finding 2) |
| `xiaojie_leader_flow` | Reaches a candidate output and a delivered research alert (finding 1) |
| Ten-day rotation shadow | Harmless: stores `quote_source='tdx_public'` through `observation_source`, `decision_eligible: False` always (`ten_day_leader_rotation_intraday_research.py:92-97`) |
| Linkage miner (anchors of any source) | Reaches research candidates; anchor labelled, output not (finding 8) |
| `intraday_outcome_settlement.PRICED_QUOTE_SOURCES` | Harmless, inconsistent (finding 10) |

## Looked at and found sound

- Chain semantics match B1: only `FuyaoProviderError` starts the fallback; a refused slot, a TDX error, a timeout and an incomplete snapshot all raise `AllASnapshotFallbackError` chained from the Fuyao error; an unreadable roster fails closed (`all_a_snapshot_sources.py:230-264`). `asyncio.wait` is used instead of `wait_for(shield(...))`, with the reason stated; behaviour is equivalent.
- At most one TDX all-A read in flight: both callers go through `_tdx_all_a_snapshots` (`main.py:1748-1755,3232-3233`); the chain itself is single-flight inside `_intraday_all_a_snapshots`. The slot is taken inside the shared fetch with `max_wait_seconds=0`.
- Late exceptions are retrieved: the chain's task has a done callback (`all_a_snapshot_sources.py:234-235`), the shadow's too (`tdx_shadow_snapshot_runtime.py:208-210`), and each keeps awaiting the shared fetch.
- Shutdown: `cancel_shared_snapshots` cancels both in-flight reads (`main.py:1770-1775`); the chain's outer task then ends cancelled and its callback handles that.
- Memory: one retained raw TDX result, one retained projected snapshot (30 s cache) and the level1 remembered minute. No growth per failure. `_roster_totals` gains one int per trade date.
- Derived flow: `quote_from_fuyao` keeps the row's `price_source` and sets `volume_source` for a non-main row; all three price merges copy the existing quote, so `volume_source` survives a Tencent or Sina price overlay and is replaced only by Longhu's own volume (`intraday_quote_normalization.py:28-48,74-81,103-110`). The label is then `tdx_public_derived`, whose `rule_usable_flow` is computed from the binding (`datasources/catalog.py:920-921`), and `intraday_signal_rules.py:92-96` zeroes the fields for rules.
- Decision quotes: `tdx_public_all_a_snapshot` is not exchange-timestamped, so `live_policy.py:64-69` rejects it exactly as it rejects the Fuyao snapshot label. No code compares against the Fuyao label to exclude it in a way a TDX label would slip past.
- Paper execution and the drill block on a non-eligible snapshot before any quote is used (`paper_auto_execution_runtime.py:118-123`, `board_flow_drill_runtime.py:66-68`). The drill's two early `paper_execution([], None)` calls go through the same check.
- Shadow reference: `main_source_reference` returns nothing unless the remembered rows say `fuyao_ths`, and a status without `provider_key` is also no reference (`all_a_snapshot_sources.py:162-176`). The roster query filters both the column and the payload metadata (`minute_cross_section.py:259-277`).
- Main-source behaviour: Fuyao metadata gains `provider_key` and `decision_eligible: True`; the stored scan status is unchanged because the contract overwrites both; level1 result keys, health capability (`a_share_prices_snapshot`) and stored provider are unchanged; `_serving_source` resolves Fuyao rows to the primary source. Visible differences for a Fuyao day: two more constant columns in each minute document's `snapshot_metadata`, and one more check (`fallback_minutes`, `ok`) in `market.minute_documents`. A quote whose `volume_source` is neither absent, Longhu nor TDX would now be labelled `unknown_volume_derived` instead of `fuyao_ths_derived`; no code sets such a value.
- No schema risk: `raw_market_observations.provider_key` and `provider_health` have no foreign key to `quant.providers` (`database.py:361-398`), and I found no CHECK on quote-source columns.
- Circuit breaker: a filled minute records a `fuyao_ths` failure as a failed minute did before, so the three-failure circuit opens on the same schedule.

## Tests: what would and would not catch a break

- Real behaviour: the chain tests (`tests/test_all_a_snapshot_sources.py`), the shared-reader and shadow tests including the never-answering stub and the one-read test, the roster test on the real database (`tests/test_tdx_shadow_snapshot_runtime.py:675`), and one real-schema read of a filled document (`tests/test_strategy_cards_postgres.py`).
- SQL text only: `MinuteDocumentReaderTests.test_documents_are_read_by_capability_across_providers` and `test_the_roster_reads_main_source_documents_only` (`tests/test_all_a_snapshot_provenance.py:754-793`) and `test_main_roster_query_passes_over_a_minute_another_source_filled` assert on the SQL string and parameters against a mock connection.
- Never executed: the indicator-health query. `MinuteDocumentHealthTests` (`tests/test_all_a_snapshot_provenance.py:815-843`) feeds a canned summary; the new `provider_key` column and the `fallback_documents` subquery are not run against Postgres by these tests.
- Mock-only gate: `WatchQuoteCaptureProvenanceTests` replaces `annotate_flow_provenance` with a recorder, so it proves the flag is handed over, not that a quote ends up ineligible end to end. Note that `annotate_flow_snapshot_provenance` only writes `flow_snapshot` on quotes with `main_net_inflow` (`intraday_features.py:241-243`), which the all-A snapshot never carries, so in production the effective gate on a filled minute is the derived-flow label, not this annotation.
- Missing: no test drives a filled scan through `intraday_watchlist_scan_service` to its downstream consumers, which is why finding 1 is not caught. No test covers repeated chain failures (finding 3) or a malformed TDX row (finding 6).

## Unverified

- Whether the board drill and the anchor refresh run inside a loop whose cadence cannot absorb the extra 30 s (finding 4); I did not trace their callers' timeouts.
- How long a Fuyao provider failure takes to surface (its request timeout), which sets the total latency of a filled minute and whether a fill ever lands inside the scan's 2 s budget rather than only through the 30 s cache.
- Consumers that read `quote["volume_ratio"]` / `quote["turnover_rate"]` or peer `pct_change` without checking `flow_metric_sources`. On a filled scan these values are TDX-derived where before the outage left Eastmoney values or nothing. I checked `intraday_signal_rules` only.
- Whether `market_snapshot_actions` exposes the new Fuyao metadata keys (`decision_eligible: True`) in the market-snapshot API response (`market_snapshot_actions.py:305`); it calls `fuyao_all_a_snapshot_rows` directly, not the chain.
- The thread that runs a TDX read after `cancel_inflight` at shutdown (the docstring's 35 s bound) and whether executor shutdown waits for it.
- Readers of the latest-level1 API outside this repository; rows of a filled minute are served there with `provider_key: tdx_public`.
- The docs changes (`docs/ARCHITECTURE.md`, `docs/DATA_SOURCE_LAYER.md`) were not reviewed.
