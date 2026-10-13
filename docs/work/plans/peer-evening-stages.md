# Peer evening stages: what the peer needs every evening, the peer starts itself

Workstream W011. Written 2026-10-13 (Asia/Shanghai). Revision 2, same day, after the plan audit (ten objections, all
accepted; the list is at the end). Research-only stages; nothing here changes a live threshold.

## Goal

Every trading evening, without anyone's hand, the peer stores the day's limit-up lists and its limit-up risk
reading (tomorrow's 09:15 auction pool reads it), then the sentiment temperature, the broad index-fund flow and
the market-timing reading. Other lines add their evening stage to the same place (the tactic trial ledger, W006).

The user's words, 2026-10-13: "发布，然后把这些变成自动跑".

## What exists

- The full post-close refresh (`quant-service/app/post_close_refresh_service.py`, `POST_CLOSE_STAGE_ORDER`, 40
  stages) is scheduled only on the owner's own service, whose code is of 2026-09-20. On the peer it is reachable
  by hand only (`quant-service/app/peer_close_research.py:1-14`; in the released code `run_post_close_refresh`
  is reached from the two HTTP endpoints only, `main.py:5005` and `:5009`). On 2026-10-13 thirteen stages of our stage order had no receipt at all,
  among them `limit_performance_lists`, `limit_up_risk`, `market_temperature`, `broad_etf_flow`, `market_timing`
  (docs/work/FACTS.md F023). The lists and the reading of 10-12 and of 10-13 were produced by hand; the
  temperature series ends on 2026-10-09.
- The peer's own evening loop, `run_peer_close_loop` (`peer_close_research.py:120-136`): every 600 seconds; from
  16:15 it works on today's session once the day has at least 4,000 daily bars; before 09:00 it catches up the
  previous session; between 09:00 and 16:15 it does nothing. It runs `STAGES` in order (five today), each behind
  `record_stage_with_receipt`: a stage whose receipt is `completed` is never run again; one stage failing does not
  stop the next; each stage has a time budget (`STAGE_TIMEOUT_SECONDS`).
- Receipt statuses (`quant-service/app/post_close_refresh.py:74-76`): a result whose status is not one of
  `completed`, `partial`, `blocked`, `failed` is stored as `completed`. So `skipped`, `empty`, `no_lists` and
  `unchanged` all close the receipt for good.
- The two stage functions, as the stage table calls them (`post_close_refresh_service.py:220-223, 256-259`,
  wired in `main.py:3910-3912`):
  - `market_history_backfill.append_limit_performance(day, call, database=, run_database=, now=)`: the live lists,
    for the current exchange date only and after 15:00; returns `completed` (stored), `unchanged`, `empty`, or
    `skipped` with a reason.
  - `limit_up_risk_runtime.refresh(database, day)`: returns `completed`, or `no_lists` when the day has no list.
- Measured 2026-10-13 22:49 (`docs/work/probes/2026-10-13-history-lists-availability.out.txt`): the history source
  does not serve a session's lists on the same evening (error code 1020 for 10-13, all five lists for 10-12). When
  during the next day they appear is not known.
- `start_or_resume_run` (`automation_run_repository.py:36-58`) reopens every receipt that is not `completed`,
  a `running` one included: the receipt is a record, not a lock.
- The reading's inputs at 16:15: `limit_up_risk_runtime.refresh` reads the day's bars, the limit prices where
  stored and 20 earlier daily amounts (`limit_up_risk_repository.py:55-90`); a missing limit price falls back to
  the board-percentage estimate (`limit_up_risk.py:55`). The loop's gate only counts bars. On 2026-10-13 the
  completion job filled 324 rows of limits and factors at 18:55.
- The history source of the same lists: `market_history_backfill.walk_source("longhu_limit_performance", [day],
  longhu_limit_performance(call), database=, run_database=, allowed=, pace_seconds=)`; `backfill_allowed` refuses
  09:00-15:45 on a trading day. Readers prefer the history lists of a day when both exist.
- Decision 0013's three stages, as the stage table calls them (`post_close_refresh_service.py`, the `actions`
  map): `refresh_market_temperature` through `run_database` with 180 seconds; `refresh_broad_etf_flow(trade_date)`;
  `refresh_market_timing(trade_date)`.
- The owner host has 894 MB of memory (FACTS F022); the scheduler process holds about 166 MB.

## Design

1. `peer_close_research.STAGES` keeps today's five stages first, unchanged in order and timing, and appends
   five: `peer_limit_performance_lists`, `peer_limit_up_risk`, `peer_market_temperature`,
   `peer_broad_etf_flow`, `peer_market_timing`. The stages run one after another (`run_due` is serial), so the new
   ones can never delay the teacher stages; the other way round the wait is bounded by the teacher budgets
   (17.5 minutes at the very worst, about a minute in practice).
2. The receipts are the peer's own: the names carry the `peer_` prefix, so a receipt the stage table closed as
   `completed` for a `skipped` or `empty` run (the status rule above) cannot suppress the peer's stage, and the
   peer and a refresh run by hand never write the same receipt. Both may run the same work at once; that is
   harmless because every product is stored by an idempotent upsert (`store` reports `unchanged`).
3. Stage adapters in `peer_stages`, thin, so that a receipt closes only when the day's product exists:
   - `peer_limit_performance_lists(day)`:
     - the day already has live lists stored (`limit_up_risk_repository.read_lists` says `live` with rows):
       `completed`, nothing is fetched;
     - `day` is the current exchange date: the live capture; `completed` or `unchanged` close the receipt; `empty`
       becomes `partial` with the reason; an upstream error code raises (the receipt is `failed`);
     - any other day (yesterday's repair, below): the history source for that one day. `completed` only when the
       walk reports no error, was not stopped by the session window and stored or already held at least one
       list; otherwise `partial` (or `blocked` when the window stopped it), with the report's counters as the
       reason. History is never requested for a day that has live lists, so a partial history can never take
       precedence over complete live lists in `read_lists`.
   - `peer_limit_up_risk(day)`: runs only when the lists stage of the same day ended `completed` in this tick
     (a dependency `run_due` checks, in the way the stage table's `POST_CLOSE_STAGE_DEPENDENCIES` works: a stage
     whose dependency is not completed is recorded `blocked` and tried again next tick); the refresh's `no_lists`
     is `blocked` too.
   - `peer_market_temperature`: `run_database(refresh_market_temperature, database, day, timeout_seconds=180)`;
     `peer_broad_etf_flow` and `peer_market_timing`: the two callables `main` already builds for the stage table
     (`refresh_broad_etf_flow(db, day, run_database=...)`, `refresh_market_timing(db, day, run_database=...)`).
4. Yesterday's repair. On every tick that has a target session, the loop first runs the two list and risk stages
   for the previous open session, behind their receipts. Completed receipts make this two reads; an evening that
   failed is repaired from the history source the next day as soon as the source has the day, in the morning
   window or at the latest on the next evening's first tick.
5. `PeerCloseDependencies` gains optional callables for these (absent means the stage is not wired and is
   skipped, which keeps the existing constructions in tests valid): live lists, history lists of one day, the
   stored-lists read, the risk refresh, the three 0013 refreshes, the exchange date of now, the previous open
   session. `main.peer_close_research_loop` passes the functions the stage table already uses. Composition only.
6. Time budgets: 120, 120, 180, 180, 120 seconds (the stage table's: `post_close_refresh_service.py:368-382`).
   A licensed call that outlives its budget keeps its executor thread until it returns (`runtime_executors.py`);
   five list calls are far inside 120 seconds in practice (about a second each).
7. The loop says what it left open: after each tick it prints one line naming the stages that did not complete
   and their reason (today the result of `run_due` is dropped). The existing health indicator
   `market.limit_up_risk` (`indicator_registry.py:53-58`) already turns red when the day's reading is missing
   after its due time (its class is `POST_CLOSE`; the dispatch reports the clock time that means and changes
   nothing there). No new alert channel.
8. Retries are bounded by the loop: one attempt of an open stage every ten minutes until 09:00 of the next
   morning; the lists stage costs five licensed read calls per attempt.

Not in this change, with the reason: `market_temperature_intraday` and `minute_panel_export` read the day's minute
documents and have not been measured on the 894 MB host; `candidate_ledger`, `longhu_supplemental_evidence` and
`decision_research_closure` wait on `daily_control_reconciliation`, which is blocked every day (BACKLOG B043);
`trade_calendar` and `daily_close_completion` already run elsewhere.

## Steps (one worktree `peer-evening-stages`, disposition merge into main; two dispatches of about 15 minutes)

1. `peer_close_research.py`: the stage list, the dependency check in `run_due`, the adapters, yesterday's repair,
   the open-stages line, the dependencies, the budgets. Tests in `quant-service/tests/test_peer_close_research.py`
   (its `recorded == list(STAGES)` assertion at :51-54 changes with the list) and
   `quant-service/tests/test_background_loops.py` (:104, :120 pin the five calls and stay valid because the new
   dependencies are optional; one new case wires them): an evening tick stores lists, then the reading, and closes
   both receipts; `empty` live lists leave the receipt open and the next tick runs it again; the risk stage is
   `blocked` while the lists stage is open; a day with live lists stored never calls the history source; a
   history walk with an error is `partial`; yesterday's repair runs before today's stages and does nothing when
   its receipts are completed; a new stage that raises does not stop the others; the teacher stages run first.
2. `main.py` wiring and the composition test;
   `docs/decisions/00NN-peer-evening-stages.md` (the next free number): a stage the peer needs every day is added
   to the peer's loop under a `peer_` receipt; adding it to the stage table alone schedules nothing on the peer.
   One line in `docs/ARCHITECTURE.md` where the post-close refresh is described.

## Acceptance

```
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q     # test_runtime_hosts.py fails on main already; nothing else may
git diff --check
```

## Release and verification

Owner code-only (two runtime files: `peer_close_research.py`, `main.py`), in an allowed window; the user runs it.
On the first evening after the release, read-only on the owner, by 16:50: receipts of the five new stages for the
day are `completed`; five list documents and one risk reading with the day's date exist; the temperature, flow
and timing readings of the day exist; the scheduler's memory did not grow by more than about 50 MB during the run
(`docker stats --no-stream` before and after). The reading stored at about 16:20 is compared with a dry re-run after 19:00
(`refresh(..., apply=False)`): the same rows and, per row, the same limit price; a difference means the stage must
wait for the completion job and becomes a follow-up. The next morning at 08:30 the history source is asked once
for the previous session (the probe above), to learn when it has the day. Until two evenings in a row pass, the
hand-run stays ready (`state/lists13.sh`). After the release, by hand once: the temperature, flow and timing readings of 10-12 and
10-13 (their own command lines; a write on the owner).

## Risks

- A list fetched right after the close could be incomplete. The loop starts at 16:15, more than an hour after
  the close, and the readers prefer the history lists once they exist.
- The three 0013 stages have never run inside the scheduler on this host. Their budgets bound the time; the
  memory check above bounds the surprise. If one misbehaves it is removed from `STAGES` by a one-line release.
- A stage left open is retried all night. Bounded as in Design 5.

## Out of scope

The tactic trial ledger's stage (W006 adds it after this lands); the intraday temperature and the minute panel
export; the daily control plane's criterion (B043); one image for every release (BACKLOG B050).

## The audit (2026-10-13, Codex, read-only) and what changed

1. `run_post_close_refresh` has two HTTP callers, not one: wording.
2. New stages placed first would delay the teacher stages by their budgets: they now come last.
3. A receipt closed as `completed` for a `skipped` or `empty` run would suppress the adapter for good: the peer's
   receipts have their own names.
4. The peer loop and a hand-run refresh can run a stage at once: separate receipts; products are idempotent.
5. The history source's timing was assumed, and a failed date was abandoned after 09:00: measured (not served the
   same evening); yesterday's repair on every tick.
6. `walk_source` returns counters, not a status, and partial history would override live lists: the completion
   rule is spelled out; history only for a day without live lists; the risk stage depends on the lists stage.
7. The reading's inputs at 16:15 are not gated: compared on the first evening with a dry re-run after 19:00.
8. The flow budget is 180 seconds, not 120; exact adapters named.
9. Two test files pin the stage list and the dependency set: both are in the steps.
10. A stage left open made no sound: one line per tick; the existing indicator already covers the reading.
