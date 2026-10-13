# Daily close completion: every listed stock gets its bar, or a stated reason

Status: revision 2, 2026-10-13 00:05, after plan-audit round 1 (six objections, adjudicated at the end).
Requested by the user on 2026-10-12 ("把根源解除掉，必须全部采集").

## Problem (measured 2026-10-12)

The full-market daily close (`longhu_market_service.sync`) is accepted when at least 95% of the vendor
cross-section has OHLC (`MINIMUM_COVERAGE = 0.95`) and it clears the all-A row gate; the reconciliation that
follows uses the same ratio (`daily_control_reconciliation.MINIMUM_BAR_COVERAGE_RATIO = 0.95`) and its source plan
names no fallback for bars (`"fallback": "none_retired"`). Whatever is missing is dropped silently and every
receipt says `completed`. Since 2026-09-21, when the Tushare repair that used to follow stopped, 70-150 traded
stocks a session have had no daily bar: 09-21 (136), 09-23 (105), 09-24 (89), 09-28 (153), 10-08 (122),
10-09 (141), 10-12 (71). The close runs on the owner with `QUANT_LONGHU_FULL_MARKET_ENABLED=true`; on this host
the flag is off (`settings.py:79`), so `sync_full_market_daily` answers `disabled` (`main.py:1439-1449`) and the
repair the teacher import asks for (`teacher_review_service._repair_missing_sessions`) cannot fill anything. On
2026-10-12 three of fifteen teacher plans failed for that reason.

A bar alone is not enough. The daily control plane counts a session's bar only together with its controls
(`daily_control_plane.py:20-78`: `controls_ready` needs an adjustment factor, a fundamentals row and both limits
for every counted bar), and every adjusted-price reader inner-joins the factor
(`teacher_review_repository.py:441-457`). Factors (`longhu_qfq_derived`) and limits are written by the owner right
after its close, only for stocks that have a bar then.

On 2026-10-12 the gaps were filled by hand with the method below: 818 bars and 814 factors, the bars validated
against 150 stored bars to exact equality; limits for those bars are prepared in
`state/research/owner_repairs_20261012/` (scripts, logs).

One pre-existing fact this plan does not change: the close's own off-plate rows carry no fundamentals by design
(`longhu_market_sync.py:107-115`), so `fundamental_rows == daily_rows` was already false on 2026-10-12 before any
repair (319 of 5,490 bars). The control plane is `blocked` on that every day. It is reported to the user as a
separate finding.

## What exists (verified in audit round 1)

- `longhu_settled_quotes.fetch(source, symbols, day)` requests one dated licensed K-line per stock
  (`GetKLineDay_W14`, target `longhu_history`) and returns only the quotes `normalized_quote` accepts plus
  aggregate errors; the payload's `CQ` array (aligned with the dates, non-empty at ex-rights events) is dropped.
  The series is forward-adjusted: after a later ex-rights event the requested day no longer shows its traded
  prices (000001.SZ on 2026-09-21: stored 11.73, refetched 11.48).
- `longhu_market_sync.merge_cross_section(trade_date, {}, quote_rows)` turns each quote into an off-plate daily row
  in the canonical units and emits no fundamentals or flow rows for it.
- `main.persist_tushare_rows` → `tushare_normalization.normalize_rows`: `"daily"` upserts the bar
  (`quality_status` fresh); `"adj_factor"` accepts `longhu_qfq_derived`, stores the factor row and sets the bar's
  `adj_factor`; `"stk_limit"` stores the limit row and sets the bar's `limit_up` / `limit_down`.
- Transport: with direct access off, `main.longhu_full_market_source_factory()` builds a
  `SharedLonghuFullMarketSource`, whose construction raises unless `QUANT_SHARED_READ_API_BASE_URL` and
  `QUANT_SHARED_READ_API_KEY` are set (`longhu_vendor_source.py:496-503`); its reader with `raw_call` is the
  private `_source`.
- Stage receipts accept `completed`, `partial`, `blocked`, `failed`; any other status is rewritten to `completed`
  (`post_close_refresh.py:74-80`, `:147-178`). Stages run in order after `full_market_daily`.
- `daily_control_reconciliation.reconcile(trade_date, read=, sync=)` reads coverage, calls one `sync` when
  coverage needs it, re-reads and reports `completed` or `blocked`.
- Control and feature readers take a bar or factor only when its `available_at` is before the next Shanghai
  midnight (`daily_control_reconciliation.py:63-87`, `feature_snapshot_repository.py:79-104`).

## Design

A step `daily close completion` for a session D, run after the close has landed and repeatable for any past
session.

1. Transport check. When neither licensed transport can be built, do nothing and return `blocked` with the reason.
2. Roster = `quant.universe_members` (`all_a`, enabled). Missing = roster stocks with no canonical bar on D.
3. One dated K-line request per missing stock, classified with explicit `CQ` parsing:
   - `filled`: a bar for D that passes `normalized_quote`, with `CQ` present, aligned with the dates and empty
     after D;
   - `not_traded`: the series has no bar for D;
   - `adjusted_later`: a `CQ` marker after D (only possible when D is in the past); not written;
   - `unverified`: `CQ` absent or not aligned with the dates, so "no later event" cannot be shown; not written;
   - `invalid`: the bar fails `normalized_quote`; not written;
   - `failed`: the request failed.
4. For the `filled` stocks, in one transaction per session:
   - the bar, through `merge_cross_section(D, {}, quotes)` and the `"daily"` persistence under `longhuvip_composite`;
   - the limits, through the `"stk_limit"` persistence: the stock's limit ratio is the one most of its last five
     earlier bars with limits show (limit_up / pre_close - 1, rounded to 0.01, required to be 0.05, 0.10, 0.20 or
     0.30); the limits are pre_close x (1 +- ratio), rounded toward the previous close for Beijing stocks (up-limit
     down, down-limit up) and half up to the cent for all others. Before writing, the same rule is evaluated on
     every roster bar of D that already has limits, and limits are written only when it reproduces at least 99.5%
     of them (the figure is stored in the receipt); otherwise the stocks are left as `limits_pending`.
     Measured on the owner on 2026-10-13 (read-only, 5,400+ bars a session): this rule reproduces 100% of the stored
     limits on 09-21, 09-23, 09-24, 09-28 and 99.98% on 10-08, 10-09, 10-12 (the one difference is a stock that
     became ST that day). The repository's `market_rules.a_share_limit_ratio` reproduces 94%: it gives main-board
     ST stocks 10% since 2026-07-06 while the exchange-published limits still show 5% (141 stocks on 10-12), it
     rounds Beijing limits half up (176 stocks), and it treats code prefix 302 as a 20% board (1 stock). The stored
     limits of 2026-09-30 were derived with it and are wrong for about 143 ST stocks. Those are defects of that
     rule and of that day's data, to be fixed separately; this step must not reuse the rule;
   - the factor, through the `"adj_factor"` persistence under `longhu_qfq_derived` with `step: 1.0`,
     `method: "gap_completion_carry_v1"` and the anchor, only when certain: a stored factor exists before D (the
     readers' semantic predicate), `CQ` is empty on D itself, and a stored factor after D, when there is one,
     equals the one before. Otherwise `factor_pending` with the reason.
   No fundamentals row is written, as for the close's own off-plate rows.
5. One `fetch_runs` row per run (capability `daily_close_completion`) holding the counts and the lists. Stage
   status: `completed` when nothing is left other than `not_traded`; `partial` when anything is left in
   `adjusted_later`, `unverified`, `invalid`, `failed`, `limits_pending` or `factor_pending`; `blocked` without a
   transport. The step never blocks later stages.

Late repairs are honest about time: a completion of a past session writes rows whose `available_at` is the
moment of the repair, so point-in-time readers (controls, feature snapshots) keep ignoring them, as they must,
while readers that take what is known now (the teacher plan builder) see them.

## Steps

1. `longhu_settled_quotes.py`: `fetch_classified(source, symbols, day, *, workers)` with the outcomes above and
   `ex_rights_on_day` per filled stock; `fetch` and `normalized_quote` keep their behaviour. A public accessor on
   `SharedLonghuFullMarketSource` for its gateway reader. Tests from recorded payload shapes: clean day, marker
   after D, marker on D, no bar on D, `CQ` absent, `CQ` shorter than the dates, invalid bar, failed request.
2. New `quant-service/app/daily_close_completion.py`: the pure rules (`limit_rows`, `limit_rule_agreement`,
   `factor_rows`, the status), the bounded repository reads (roster; bars of D with limits and the latest earlier
   bar with limits per stock; the stored factor before and after D per stock), and
   `async complete(trade_date, *, db, run_public_blocking, run_database_blocking, persist_rows, read_source, now)`.
   The vendor requests run through the runner the close uses for the same call (`run_public_blocking`), in one
   blocking call with at most six worker threads, as `longhu_settled_quotes.fetch` already does. Tests: every pure
   rule with each skip reason; a completion with fakes (filled, not traded, adjusted later, unverified, failed);
   the 99.5% refusal; idempotence; `partial` and `blocked`.
3. Wiring, one entry point:
   - post-close stage `daily_close_completion` immediately after `full_market_daily` and before
     `core_daily_controls`, 600 s, injected callable wired in `main._post_close_refresh_dependencies()`, returning
     one of the receipt's accepted statuses;
   - `daily_control_reconciliation.DEFAULT_SOURCE_PLAN["daily_bars"]["fallback"]` becomes
     `longhu_dated_kline_per_symbol`, and the `sync` the reconciliation is given in `main.py` runs the completion
     for the date before its existing control synchronisation, so there is one repair path for a session's bars;
     the reconciliation's 95% readiness rule is unchanged;
   - the teacher import's `repair_daily` (wired in `main.py`) runs the same completion for the requested date as
     its bars part instead of `sync_full_market_daily`;
   - `indicator_registry.py` / `indicator_health.py`: `data.daily_bars_complete`: for the latest open session,
     after 18:45 Asia/Shanghai, fail when no completion receipt exists or its status is not `completed`, showing
     the counts and the first stocks of each leftover reason.
   Tests: stage order and receipt statuses, the reconciliation calling the completion once, the teacher repair
   path, the health check's cases.
4. Decision record `docs/decisions/0018-daily-close-completion.md`: a session is complete only when every roster
   stock has a bar or a stated reason; what was measured; the forward-adjustment trap; the limit and factor
   rules and why uncertain cases are left pending; the manual repair of 2026-10-12; the fundamentals gap of
   off-plate rows as an open, separate question.

## Acceptance

```bash
cd quant-service && /Users/papa/.venvs/svc/bin/python -m pytest tests -q -k "daily_close_completion or settled_quotes or post_close or daily_control or indicator_health or teacher_review"
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
python3 -m pytest scripts -q
git diff --check
```

After release: run the completion by hand for 2026-10-12 (expected: nothing missing except stocks not traded and
the listed leftovers) and read the health entry. After the next session's post-close pipeline: the stage's
receipt, the bar count against the roster, and that filled stocks have limits and factors.

## Risks

- About 100-200 extra Longhu history requests a session after the close, through the shared gateway, six workers,
  no retries inside a run; the next run retries what is left.
- The owner writes factors and limits for the stocks that have a bar when its own tasks run (factor rows of the
  repaired sessions carry `available_at` shortly after 16:00; the repository documents a 04:30 factor task as well).
  This step writes them only for stocks it filled itself. If the owner later writes the same stock and day, the conflict keys (symbol,
  day, provider) make its row replace ours.
- A stock with an ex-rights event on the session it was missed gets a bar and limits but no factor
  (`factor_pending`); the health check keeps it visible.
- The limit rule carries a stock's ratio from its recent bars: on the day a stock enters or leaves ST its limits
  are wrong by construction (one stock in three sessions measured). The per-session agreement figure measures it.

## Out of scope

- The owner's close and its 95% gate (another host).
- Restoring traded prices for past sessions after a later ex-rights event (needs an unadjusted source).
- Deriving factors on ex-rights days, and fundamentals for off-plate rows.
- Raising the reconciliation's 95% readiness rule.

## Plan-audit round 1 (Codex, 2026-10-13): adjudication

1. Blocker, `CQ` is not available from the existing fetch. Accepted: `fetch_classified` parses it explicitly; an
   absent or misaligned `CQ` is `unverified` and nothing is written.
2. Control-plane completeness. Accepted for limits (written by a validated rule) and factors; fundamentals are not
   written, as for the close's own off-plate rows, and the existing gap is stated.
3. Late persistence. Accepted as a stated policy: true `available_at`, invisible to point-in-time readers.
4. Transport availability. Accepted: `blocked` with the reason.
5. Stage status contract. Accepted: `completed` / `partial` / `blocked`; never blocks later stages.
6. Redundant repair path. Accepted: the reconciliation's `sync` and the teacher repair both call the completion.

## Plan-audit round 2 (Codex, 2026-10-13): adjudication

1. "Reuse `market_rules.a_share_limit_ratio` instead of a carried ratio." Rejected with measurement: that rule
   reproduces 94% of the exchange-published limits stored for 2026-10-12 and the carried rule 99.98% (figures and
   the three defects are in the Design section). The carried rule is specified precisely instead.
2. Owner timing claim. Accepted: the Risks sentence states what the rows show.
