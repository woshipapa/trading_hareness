# Peer evening stages: what the peer needs every evening, the peer starts itself

Workstream W011. Written 2026-10-13 (Asia/Shanghai). Research-only stages; nothing here changes a live threshold.

## Goal

Every trading evening, without anyone's hand, the peer stores the day's limit-up lists and its limit-up risk
reading (tomorrow's 09:15 auction pool reads it), then the sentiment temperature, the broad index-fund flow and
the market-timing reading. Other lines add their evening stage to the same place (the tactic trial ledger, W006).

The user's words, 2026-10-13: "发布，然后把这些变成自动跑".

## What exists

- The full post-close refresh (`quant-service/app/post_close_refresh_service.py`, `POST_CLOSE_STAGE_ORDER`, 40
  stages) is scheduled only on the owner's own service, whose code is of 2026-09-20. On the peer it is reachable
  by hand only (`quant-service/app/peer_close_research.py:1-14`; in the released code `run_post_close_refresh`
  has one caller, the HTTP endpoint). On 2026-10-13 thirteen stages of our stage order had no receipt at all,
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
- The history source of the same lists: `market_history_backfill.walk_source("longhu_limit_performance", [day],
  longhu_limit_performance(call), database=, run_database=, allowed=, pace_seconds=)`; `backfill_allowed` refuses
  09:00-15:45 on a trading day. Readers prefer the history lists of a day when both exist.
- Decision 0013's three stages, as the stage table calls them (`post_close_refresh_service.py`, the `actions`
  map): `refresh_market_temperature` through `run_database` with 180 seconds; `refresh_broad_etf_flow(trade_date)`;
  `refresh_market_timing(trade_date)`.
- The owner host has 894 MB of memory (FACTS F022); the scheduler process holds about 166 MB.

## Design

1. `peer_close_research.STAGES` becomes, in this order: `limit_performance_lists`, `limit_up_risk`,
   `market_temperature`, `broad_etf_flow`, `market_timing`, then today's five unchanged. The auction pool's
   inputs come first and are quick; the digest stays last and can read what the day stored. The names are the
   stage table's names, so the receipts are the same receipts: a refresh run by hand finds them completed, and
   the other way round.
2. Stage adapters in `peer_stages`, thin, so that a receipt closes only when the day's product exists:
   - `limit_performance_lists(day)`: when `day` is the current exchange date, the live capture; its `empty`
     becomes `partial` with the reason (the next tick tries again). Otherwise (the morning catch-up of the previous
     session, or after midnight) the history source for that one day: stored or already stored is `completed`,
     nothing served is `partial`, a refused window is `blocked` with the reason.
   - `limit_up_risk(day)`: the refresh; `no_lists` becomes `blocked` with the reason, so it runs again when the
     lists have landed.
   - `market_temperature`, `broad_etf_flow`, `market_timing`: the same calls the stage table makes, unchanged.
3. `PeerCloseDependencies` gains the callables these need (live lists, history lists of one day, risk refresh,
   the three 0013 refreshes, the exchange date of now); `main.peer_close_research_loop` passes the functions the
   stage table already uses. Composition only: no logic in `main.py`.
4. Time budgets: 120, 120, 180, 120, 120 seconds, the stage table's own.
5. Retries are bounded by the loop: one attempt of an open stage every ten minutes until 09:00 of the next
   morning; the lists stage costs five licensed read calls per attempt.

Not in this change, with the reason: `market_temperature_intraday` and `minute_panel_export` read the day's minute
documents and have not been measured on the 894 MB host; `candidate_ledger`, `longhu_supplemental_evidence` and
`decision_research_closure` wait on `daily_control_reconciliation`, which is blocked every day (BACKLOG B043);
`trade_calendar` and `daily_close_completion` already run elsewhere.

## Steps (one worktree `peer-evening-stages`, disposition merge into main; two dispatches of about 15 minutes)

1. `peer_close_research.py`: the stage order, the adapters, the dependencies, the budgets. Tests in the module's
   existing test file (`quant-service/tests/test_peer_close_research.py`): an evening tick stores lists then the
   reading and closes both receipts; `empty` live lists leave the receipt open and the next tick runs it again;
   `no_lists` leaves the risk receipt open; a morning catch-up takes the history source; a new stage that raises
   does not stop the teacher stages; the order is pinned. The existing tests pass untouched.
2. `main.py` wiring and the composition test that covers the loop's dependencies;
   `docs/decisions/00NN-peer-evening-stages.md` (the next free number): a stage the peer needs every day is added
   to the peer's loop; adding it to the stage table alone schedules nothing on the peer. One line in
   `docs/ARCHITECTURE.md` where the post-close refresh is described.

## Acceptance

```
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q     # test_runtime_hosts.py fails on main already; nothing else may
git diff --check
```

## Release and verification

Owner code-only (two runtime files: `peer_close_research.py`, `main.py`), in an allowed window; the user runs it.
On the first evening after the release, read-only on the owner, by 16:40: receipts of the five new stages for the
day are `completed`; five list documents and one risk reading with the day's date exist; the temperature, flow
and timing readings of the day exist; the scheduler's memory did not grow by more than about 50 MB during the run
(`docker stats --no-stream` before and after). Until two evenings in a row pass, the hand-run stays ready
(`state/lists13.sh`). After the release, by hand once: the temperature, flow and timing readings of 10-12 and
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
