# Review 2: tactic trial ledger, `git diff 92d13594 HEAD` on `codex/tactic-trials` (HEAD c7c97702)

Worktree `/Users/papa/codebase/.codex-wt/n8n-tactic-trials`. Paths are relative to it. `runtime.py` is
`quant-service/app/tactic_trial_runtime.py`, `trials.py` is `quant-service/app/tactic_trials.py`,
`repo.py` is `quant-service/app/tactic_trial_repository.py`.

## How this was checked

- Read-only. Every finding is from reading the current files and the diff hunks; nothing was edited, no remote host
  was contacted.
- One test run, the command given in the brief (five tactic test modules on the throwaway database):
  `Ran 38 tests in 5.983s`, `OK`.
- No finding below was reproduced by executing code. Each is marked "verified by reading" or "unverified".
- The JSON fixture was not read beyond its first lines. Whether it was really produced by b04be9b9 is unverified.

Counts: 0 high, 3 medium, 8 low. Of the 22 old findings: 19 closed, 3 partly closed, 0 not closed, 0 made worse.

## New findings

### MEDIUM

**1. A pick day that leaves the 12-session window without a digest never gets one, and nothing reports it.**
`runtime.py:258` (window/older split), `runtime.py:283-284` (readings are read for the window only),
`runtime.py:295-297` (an unsettled day is stored only on the one evening it is `window[0]`), `runtime.py:261-269`
(older days are read as digests only; `stale` counts wrong-fingerprint digests, not missing ones).
- Input: a pick day with at least one row that is not `settled`/`no_trade` after 11 further sessions, and the
  evening on which that day is `window[0]` does not complete (stage blocked until 09:00 because the lists or risk
  reading are missing, service down, `summarise` failing all night). Rows stay open that long when an exit is
  rolled past blocked sessions (`t1_settlement.py:206-233`, up to `MAX_EXIT_ROLL_SESSIONS = 10` more sessions, so
  beyond the window), when bars are missing, and for every auction row whose `reference_close` is `None`
  (`held:*` is `missing_bar` for good: all rows of a blind auction reading, finding 2).
- What happens: the next evening the day is in `older`, has a reading and no digest, and is never looked at
  again. It is absent from records, switch table, sell points and sealing rates; `open_days` and
  `stale_digest_days` do not count it. Same for a replay that leaves unsettled window days "to the evening run"
  (`runtime.py:604`) when the first evening comes more than a few sessions later.
- Also: `_store_final` stores pairs first (`runtime.py:240-241`); a stop between the two stores on the `window[0]`
  evening leaves pairs without a digest in `older`, so the pair fold has the day and the record fold does not.
- It is recoverable by hand with `--digests`, but nothing says it is needed.
- Fix: in `summarise`, also read the readings and digests of a few sessions before `window[0]` (or compare the
  sets of reading dates and digest dates of the last N sessions with one cheap query) and close any reading older
  than the window that has no current digest; report the count in the summary.
- Verified by reading. Frequency not measured.

**2. The 09:25 loop still stores an auction reading built without the previous close reading, and that reading is now permanent.**
`runtime.py:169-184` (`refresh_auction` has no guard: `previous_reading` absent gives `previous_close=None`, the
reading is stored with `complete: True`), `runtime.py:160` (`reference_close = None` for every row),
`runtime.py:342-343` (the repair builds the session before; today's auction reading answers `already_stored`),
`runtime.py:512-514` and `:567-569` (the replay never replaces a reading without `replayed`).
- Input A: release day. `TACTIC_AUCTION_ENABLED` defaults to true (`settings.py:135`), so the loop runs on the
  first morning after the release, before the history replay. The evening stage is held back by
  `runtime.py:335-337`; the morning loop is not.
- Input B: any missed evening. On T+1 at 09:25 the close reading of T does not exist; the evening of T+1 repairs
  the close reading of T but not the auction reading of T+1.
- What is stored: every `prev.*` condition unknown (`trials.py:119-120`), so no auction tactic that uses one can
  pick; `held:*` outcomes `missing_bar`, `big_loss` `None`, `s1_high`/`s1_low`/`s3_high` measured from the open
  (`tactic_exits.py`, `evaluate_pick`). The day counts as a forward day. It never settles, so it depends on the
  `window[0]` closure of finding 1. Decision 0023 item 5c makes this reading final.
- Fix: in `refresh_auction`, return `not_ready` (a new status such as `waiting_for_close`) when the previous
  session's close reading is absent, and let the evening build it after the repair (`decided_at` stays the
  table's stored time). On the release, enable the loop only after the replay.
- Verified by reading. This is the unclosed half of old finding 10.

**3. Removing or renaming a condition crashes the evening summary and the board: the fingerprint does not cover conditions and pair documents carry none.**
`trials.py:386-393` (fingerprint: tactics and exit keys only), `trials.py:595-598` (`_condition_name` indexes
`names[key]`), called from `trials.py:627` (`PairHistory.result`, every stored pair key), `trials.py:769`
(`_cases`, every flag of every row of the latest pick day) and `trials.py:714` (`day_board`);
`runtime.py:270-272` (older pair documents are folded without any check), `runtime.py:281-282`.
- Input: a stock condition is deleted or its key renamed (the decision says conditions change often).
- What happens: `summarise` raises `KeyError` in `pairs.result()` as long as any pair document of the last 250
  sessions names the old key, and in `_cases(latest)` as long as the latest pick day's reading was built by the
  old code (one evening for close, two for auction). The stage fails every tick. `read_board` returns 500 for
  every board whose reading has the old flag, for good. The old digests are correctly reported stale; the pairs
  are what crash. `--digests` over 250 sessions of both slots repairs the summary, not the old boards.
- Fix: put the stock-condition keys into the fingerprint, write the fingerprint into the pair document and fold
  only current pair documents (count the rest as stale); in `day_board`/`_cases` skip or mark a flag that is no
  longer registered instead of raising.
- Verified by reading. This is the unclosed part of old finding 8.

### LOW

**4. `read_cases` opens one transaction per session and one more per pick day.** `runtime.py:399-407`.
With `days=10` (the tab's value) that is 1 + 10 + up to 10 statements, with `days=20` up to 41, each in its own
`database.transaction()`. At 52 ms a round trip, before BEGIN/COMMIT, that is 1.1 to 2.1 s per click (more if
the transaction wrapper costs extra round trips; not measured). Memory is fine (one day at a time). `tactic_day`
is also computed twice per pick day. Fix: one transaction for the request, or one range read of
`trade_date` plus the fields `tactic_day` needs. Verified by reading; latency is arithmetic, not measured.

**5. A blocked repair does not block the evening.** `runtime.py:339-344`. If `refresh_close(sessions[-2])` returns
`blocked` (yesterday's lists still missing), today's close reading is built anyway with `previous[0] = None`
and stored; from then on `known[-1]` is today, so yesterday is never tried again. Fix: return `blocked` with the
reason while the repair is blocked, or record in the reading that it was built without the session before.
Verified by reading.

**6. `--summarise <past date> --apply` stores a summary that looks ahead.** `runtime.py:279-291`, `:654-655`.
A final digest inside the window is used as stored; it was computed with bars after that past `as_of`
(`day_outcomes` with a later date, or `rebuild_digests`/`replay` with `today`, `runtime.py:602,634`).
`read_board` then presents that summary's labels as "what was known" on the following board
(`runtime.py:376`). The scheduled path is not affected (it summarises the target day only, and no summary is
written for a missed day). Fix: refuse `--apply` for a date other than the current target, or recompute window
digests when `as_of` is not the newest session. Verified by reading.

**7. The fingerprint covers exit keys, not exit rules.** `trials.py:393`. The docstring says "the sell rules";
changing a threshold inside an exit (for example `_target("0.05")`, `MAX_SELL_SESSIONS`) keeps every stored
digest "current". Fix: add a version constant for the exit rules to the fingerprint, or say in the docstring
that such a change needs a new `VERSION`. Verified by reading.

**8. `evening` reads twelve full close readings to learn whether one exists and its date.** `runtime.py:333-334`,
then `summarise` reads them again (`runtime.py:283`). Several megabytes parsed for two facts. Fix:
`read_newest(CLOSE_CAPABILITY, trade_date)` (one row) and compare its date. Verified by reading.

**9. A future date on the board route shows the newest close picks under that date.** `runtime.py:373-375`;
`market_radar_reads.py:132` has a lower bound and no upper one. `date=2027-01-04` returns
`trade_date: 2027-01-04` with `close` of the newest session (`read_open_sessions` clamps to today). No error.
Fix: bound the date to the session after the newest close reading, or return `close: null` when the session
before `day` is not the newest stored one's successor. Verified by reading.

**10. The decision names a test that checks less than it says.** `docs/decisions/0023-tactic-trial-ledger.md:47`
says `test_peer_close_research.py` checks that `peer_tactic_trials` is last; the test asserts only that it comes
after `peer_limit_up_risk` (`quant-service/tests/test_peer_close_research.py:163-164`). Moving the stage back
before the three products would pass. Fix: assert `STAGES[-1] == "peer_tactic_trials"`. Verified by reading.

**11. (Not introduced by this diff.) `s3_high` is taken from fewer than three sessions when a day is closed early.**
`tactic_exits.py`, `evaluate_pick`: `highs = bars[s1_index : min(s1_index + 3, len)]`; `_settled_complete`
(`runtime.py:187-192`) looks at entry statuses only. A small day whose rules have all left by T+2 is stored
final with a two-session `s3_high` in the sell-point room. The digest is now stored once, so this is permanent
where it used to be overwritten. Fix: require three sessions after the pick day before a day counts as settled.
Verified by reading; how often a day settles by T+2 is not measured.

## The 22 findings of the first review

| # | Old finding | Status | Evidence in the current code |
|---|---|---|---|
| 1 | `KeyError: 'scheduled_at'` on the stored 09:25 table | closed | `repo.py:111-116` returns `stored_at` from `available_at`; `runtime.py:174-181`; the integration test stores the table with `build_auction_table` (`test_tactic_trial_integration.py:96-107`) |
| 2 | Decimal values the store cannot write | closed | `runtime.py:50-52` (`_research` runs `json_safe`), `trials.py:256-266`; digests and pairs `trials.py:422,590`; exercised on the real store by the integration test |
| 3 | float prior closes multiplied by Decimal | closed | `runtime.py:195-196,210-213` (all prices enter as Decimal) |
| 4 | auction rows stored with `reference_close = None` | closed | `runtime.py:160`; asserted at `test_tactic_trial_integration.py:122-124`. Still `None` when the previous close reading is absent: new finding 2 |
| 5 | settled day closed without its digest | closed | no settled document; pairs first, digest last (`runtime.py:238-241`); a day is final only when its digest exists (`runtime.py:279-291`). Residue: new finding 1 |
| 6 | `settle` loads the exit bars of all pending days at once | closed | `runtime.py:219-230`, called per reading at `runtime.py:293` |
| 7 | day sections never carry an outcome | closed | `runtime.py:300-306` (newest pick day with `resolved`), `trials.py:643-652` (`pick_day`, `today_rows`); asserted at `test_tactic_trial_integration.py:139-141` |
| 8 | registry edit crashes the summary or drops history silently | partly closed | fingerprint `trials.py:386-393`, checked at `runtime.py:244-245,266-269,280`, reported as `stale_digest_days`. Not covered: conditions and pair documents (new finding 3), exit parameters (new finding 7) |
| 9 | the only digest rebuild replaces live readings | closed | `rebuild_digests` `runtime.py:622-640`; replay keeps live readings `runtime.py:512-514,539,567-569,614` |
| 10 | missed evening never built; next readings stored blind | partly closed | repair of the session before `runtime.py:339-342`; release guard `runtime.py:335-337`. Not closed: the morning auction reading is still stored blind (new finding 2); a blocked repair does not hold the evening (new finding 5) |
| 11 | cases route holds 20 sessions of documents | closed | one session at a time, bars only for pick days `runtime.py:401-407`; the tab asks 10 (`TacticTrialsTab.vue:54`). Statement count: new finding 4 |
| 12 | raw `prev.*` keys on the board | closed | `trials.py:595-598`, used at `:714` and `:769` |
| 13 | 刷新 in the morning empties the summary | closed | `runtime.py:353-356` (newest summary up to the day), `repo.py:105-108` |
| 14 | equivalence test compared the fold with itself | closed | `test_tactic_trials.py:197-223` against `tests/fixtures/tactic_fold_reference_b04be9b9.json`; the seven test-only functions are gone. That the fixture came from b04be9b9 is unverified |
| 15 | range reads to use one document | closed | `derived_daily_readings.py:50-57` (`LIMIT 1`), `repo.py:105-116`, `runtime.py:70-73`. One left over: new finding 8 |
| 16 | stage sat before three unrelated products | closed | `peer_close_research.py:44-46` (last in `STAGES`) |
| 17 | auction loop (a busy logged as no table, b PoolTimeout, c calendar asked every poll, d flag) | partly closed | a `runtime.py:443`; c `runtime.py:433-434`; d `main.py:4224`. b unchanged: `DATABASE_UNAVAILABLE` is still `(ExecutorSaturatedError, TimeoutError)` (`runtime.py:411`), no `PoolTimeout` anywhere under `app/`; the docstring (`runtime.py:427-428`) now says other errors end the task. Whether `run_database_blocking` turns a pool timeout into `TimeoutError` is unverified |
| 18 | order the store does not keep | closed | a `trials.py:532`; b `tactic-trials.ts:190-202`; c `trials.py:635` (ties broken by the condition keys) |
| 19 | dead, duplicate and defensive code | closed | `_connected` removed (`market_radar_reads.py`), `version` and `registry` are read (`runtime.py:244-245`), `day_rate` and `_day_exit_tables` gone, `Tactic.combination` (`trials.py:42-45`) replaces the four spellings, `BOARD_FIELDS` trimmed (`trials.py:701`), no `names.get` fallback |
| 20 | board date without a lower bound | closed | `market_radar_reads.py:31,132,137`; asserted at `test_tactic_trial_reads.py:86-87` |
| 21 | tab requests not sequenced | closed | `tactic-trials.ts:219-225`; `TacticTrialsTab.vue:21-22,31,38-45,49-60` |
| 22 | default view shows the previous session's close picks after the evening run | closed | `runtime.py:368-372`, `repo.py:152-158`; asserted at `test_tactic_trial_integration.py:153-156` |

## Tests: what would catch a real break, what is still only mocked

Would catch: the four crashes of the first round (the integration test runs replay, `refresh_auction` from a table
built by `build_auction_table`, `evening`, `summarise`, `read_board`, `read_cases`, the repair, `rebuild_digests`
and a second replay on a real database with the real `evaluate_pick`). Removing the `as_of` bound in
`day_outcomes` would fail `test_tactic_trial_integration.py:139` (the test's "today" is two sessions after the
summarised day).

Still only mocked or not asserted:
- `summarise` window logic: the unit test returns `[]` from every read (`test_tactic_trial_runtime.py:240-264`), so
  it checks read ranges only. Nothing asserts that an unsettled day is stored on its `window[0]` evening, that
  `open_days` is ever above 0, that a stale digest inside the window is recomputed and stored, or what happens
  with a digest without pairs. Finding 1 has no test.
- `EveningTests` replace `refresh_auction`, `refresh_close` and `summarise` (`:280-284`): order only.
- The auction loop tests replace `run_database`; no test builds a morning reading when the previous close reading
  is missing and looks at what is stored (finding 2). The unit test at `:72-86` builds exactly that reading and
  asserts it completes.
- Route tests replace the three read functions (`test_tactic_trial_reads.py:77-79`); no test goes route to database.
- `_settle_reading` and the auction replay unit tests replace `evaluate_pick` (`:107`, `:226`).
- Integration data: `adj_factor` is 1 on every bar, so the price-basis conversion in `_settle_reading`
  (`runtime.py:208-212`) is exercised for types, not for values; no suspended bar, no blocked sale, no day that
  stays open to the end of the window; the auction slot's day section and `rebuild_digests("auction")` are not
  asserted; no registry or condition change against stored pair documents (finding 3).
- Frontend: `latestOnly`, `recordRows`, `summaryCaption` are unit-tested; the component's use of them is not.

## Looked at and found sound

- `summarise` on the scheduled path: window and older are disjoint; older digests fold by slot in date order,
  window digests after them sorted by day; only current-fingerprint digests fold; a stale digest inside the window
  is recomputed and, if settled or `window[0]`, stored again; pairs are bounded to the last 250 sessions on both
  paths. With `apply=False` nothing is stored (neither digests nor the summary).
- `latest`/`newest_resolved`: on the evening of T the reading of T has no settled entry for either slot (close:
  first outcome bar is index 1; auction: `entry_index = 0`, exits start at index 1), so the day sections are T-1
  for both slots; a newest resolved day that came from a stored digest is recomputed once (`runtime.py:305-306`).
- Look-ahead: `day_outcomes` drops bars after `as_of`; `refresh_close` for the repaired session reads that
  session's bars, windows up to the pick day and turnover up to it; board labels come from the newest summary
  strictly before the board day; no summary is written for a missed day.
- Memory: one pick day's exit bars at a time (about 80 symbols, 60 calendar days); twelve readings of one slot at
  once; older digests 60 sessions a chunk; `History` keeps arrays of floats. Statement count of an evening: about
  14 chunks of up to 4 reads, 6 window reads, up to 24 exit-bar reads, a few stores. Well inside 600 s by
  arithmetic; not measured on the host.
- `replay` keeps live readings for both slots and stores only non-live ones; digests of days older than the
  window are stored as they stand, newer ones only when settled. `rebuild_digests` leaves readings alone and
  stores pairs before digest. Both cost roughly 6 to 8 round trips per day and slot (about 10 minutes for 760
  sessions and two slots by arithmetic): acceptable for a manual command.
- Decimal/float/JSON: every stored document and every route payload passes `json_safe`; `store` compares with
  sorted keys, so the `jsonb` key order does not create versions (the integration test asserts a second
  `summarise` is `unchanged`).
- `read_board` default day: session after the newest close reading, today when the calendar has no later
  session; weekend and after-midnight cases give the next session.
- `evening`: `blocked` for a missing close input or an empty window, otherwise `completed`; the stage is only
  called for a session (`target_session`); with one session in range the repair is skipped.
- Frontend types against the payloads, field by field: `TacticBoard` and `BoardRow` against `read_board`,
  `day_board`, `BOARD_FIELDS`, `tactic_card`; `TacticSummaryReading`, `SampleSize`, `SummaryRecord`,
  `TacticRecord`, `ConditionDay`, `MinedPair`, `PairTable` against `daily_summary`, `_day_item`,
  `_summary_records`, `_Series.record`, `_condition_day_items`, `PairHistory.result` and the keys `summarise`
  adds; `TacticCases`/`TacticCaseDay` against `read_cases` and `tactic_day`. All names and nullabilities match.
- Request ordering: a superseded response is dropped for both request kinds, `loading` is reset only by the
  current request, the date set from a default load does not trigger a second load. Sort order: combinations in
  card order, then single conditions by name, a consistent comparator.
- Decision 0023 items 5, 5a, 5b, 5c and 7 describe what the code does, except as listed in findings 1, 2, 3 and 10.

## Unverified

- Whether `run_database_blocking` converts `psycopg_pool.PoolTimeout` (old finding 17b).
- Whether a reading can hold the same symbol twice (it would duplicate bars in `read_exit_bars`' `unnest` join).
- Whether a stored close row can have `boards = None` together with others that have it (`runtime.py:116` would
  raise `TypeError` on `float(None)`; the line predates this diff).
- Provenance of the reference fixture; sizes and timings on the production host.
