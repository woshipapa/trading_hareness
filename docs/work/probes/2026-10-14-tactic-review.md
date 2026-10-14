# Review: tactic trial ledger, branch `codex/tactic-trials` at 92d13594

Worktree `/Users/papa/codebase/.codex-wt/n8n-tactic-trials`. Paths below are relative to it; `runtime.py` means
`quant-service/app/tactic_trial_runtime.py`, `trials.py` means `quant-service/app/tactic_trials.py`.

## How this was checked

- Read-only. I ran no repository code and no test (the brief forbids importing from the repo), and touched no
  database or host. Every finding is from reading the two diffs, the current files and the pre-change files
  (`git show b04be9b9:...`).
- Standalone checks, none importing the repo:
  - psycopg 3.3.6: dumping `Json({'a': Decimal('1.5')})` raises `TypeError: Object of type Decimal is not JSON serializable`.
  - `18.0 * Decimal('1.25')` and `Decimal('11') / 10.5` raise `TypeError`.
  - `psycopg_pool.PoolTimeout` is not a subclass of `TimeoutError`.
  - Sizes with `tracemalloc` on synthetic documents: a settled document of 100 rows is 552 KB of JSON and
    1.47 MB parsed (60 rows 0.88 MB, 150 rows 2.21 MB); one exit-bar row as the repository returns it is about 1.5 KB.
  - Folding per-day floats instead of exact Decimals over 760 synthetic days changes a mean by at most 1e-19.
- Findings 1 to 4 are on lines of the base commit b04be9b9, outside the two diffs. They are listed because both
  entry points this change adds (`evening`, `run_tactic_auction_loop`) call them every session and neither gets
  past them. The work record (`docs/work/streams/W006-tactic-trial-ledger.md:94`) reports two of them as fixed in
  b04be9b9 on the implementer's word, "主会话还没复跑"; the code does not do what that entry says.

## Findings

### HIGH

**1. `refresh_auction` raises `KeyError: 'scheduled_at'` on every stored 09:25 table.**
`runtime.py:172` (called from `evening`, `runtime.py:290`, and `auction_reading`, `runtime.py:378`).
- Trigger: any session on which `auction_open_desk_daily` is stored. The table is built by
  `auction_open_desk.build_auction_table` (`quant-service/app/auction_open_desk.py:201-207`), which returns
  `trade_date, version, rows, bucket_statistics, coverage, research_only, live_effect`, and is stored by
  `auction_pool_runtime._persist_table` (`:78-82`) unchanged. `derived_daily_readings.newest` returns only
  `normalized` minus the storage keys, so no time field reaches the reader. `git log -S scheduled_at` on
  `auction_open_desk.py` is empty: the key never existed.
- What goes wrong: in the morning the loop fails at its first poll after the table appears, the supervisor
  restarts it with backoff until 09:45, and no auction reading is stored. In the evening `evening()` fails at its
  first statement on every tick from 16:15 to 09:00, so the day gets no close reading, no settlement and no summary.
- Why the tests pass: the fixture writes the key by hand (`quant-service/tests/test_tactic_trial_runtime.py:77`).
- Smallest fix: read the table row's `available_at` in `tactic_trial_repository` (one query returning
  `available_at, normalized` for the day, newest first, limit 1) and use it as `decided_at`; build the test
  fixture with `build_auction_table`.
- Verified by reading the producer, the store and the reader. Not checked against stored production rows.

**2. The close reading and every settled document hold `Decimal` values, which the store cannot write.**
`runtime.py:97` (`reference_close` is the bar's `close`, a `numeric` column, so a `Decimal`); `runtime.py:197-201`
(`evaluate_pick` returns `Decimal` for `raw_return`, `net_return`, `price`, `s1_high`, `s1_low`, `s3_high`,
`open_to_close`); written through `derived_daily_readings.store` to
`quant-service/app/public_market_repository.py:132` (`Json(payload)`, default `json.dumps`).
- Trigger: `refresh_close(apply=True)` on a day with at least one limit-up row; `_store_settled` for any pick day
  that has one resolved return (from the second evening on). Replay with `--apply` hits it too, also for auction
  readings (`open` and `gap` come from bars, `runtime.py:515-517`).
- What goes wrong: `TypeError` inside `persist_timed_observations`; the stage is recorded failed on every tick.
  `store`'s own comparison uses `default=str` (`derived_daily_readings.py:51-52`), so the failure only shows at the insert.
- Smallest fix: pass both documents through the converter the digest already uses (`trials._json`) before
  `store`, and add one test that calls `json.dumps(document)` without `default` on a reading and a settled
  document built by the real functions. The production risk reading avoids this by converting every bar value
  with `float()` (`quant-service/app/limit_up_risk.py:119-131`).
- Verified by reading (column types in `database.py:448-455`, no adapter or `set_json_dumps` anywhere in the
  tree) and by the psycopg check above. Not executed end to end.

**3. `_settle_reading` passes float prior closes into `evaluate_pick`, which multiplies them by a `Decimal`.**
`runtime.py:196-197`; `quant-service/app/tactic_exits.py:205` reached from `:251-253`.
- Trigger: a pick whose prior and path bars all carry `adj_factor` (the normal case) and that has at least one
  bar after its entry, so the exit context is built. That is every pending pick from the second evening on, and
  every day of a replay.
- What goes wrong: `float * Decimal` raises `TypeError` in `_basis_reference`; `settle` fails, so nothing is
  settled or digested.
- Why the tests pass: `evaluate_pick` is mocked wherever `_settle_reading` runs
  (`test_tactic_trial_runtime.py:94-96` even asserts the float list `[18.0]`), and `test_tactic_exits.py` passes Decimals.
- Smallest fix: keep the prior closes in `Decimal` (the type `evaluate_pick` declares); one test through the real
  `evaluate_pick` with `adj_factor` on every bar.
- Verified by reading and by the arithmetic check above.

**4. Every auction row is stored with `reference_close = None`.**
`runtime.py:149` reads `previous_row.get("close")`; a stored close row has `reference_close` and no `close`
(`quant-service/app/tactic_conditions.py:107-120`, `runtime.py:97`).
- Trigger: every auction reading, live and replayed.
- What goes wrong, stored: for the auction slot all twelve `held:*` returns are `missing_bar`
  (`tactic_exits.py:228,235-240`), `big_loss` is always `None` (`:272-273`), and `s1_high`, `s1_low`, `s3_high`
  are measured from the open instead of the previous close (`:263`). Because `missing_bar` is not a final
  status (`runtime.py:181`), no auction day ever becomes `complete`: each stays pending for its whole
  12-session window, is settled again every evening (see 6), and the cases drawer shows "未结算完" for good.
- Smallest fix: `previous_row.get("reference_close")`, converted to `Decimal` where it enters `evaluate_pick`
  (after storage it is a float, which would otherwise hit the error of finding 3).
- Verified by reading.

**5. A settled day can be closed without its digest, and is then missing from every later summary.**
`runtime.py:216-221` with `runtime.py:230-233`.
- Trigger: `_store_settled` writes the settled document, then the digest, then the pairs, in three separate
  `store` calls (six transactions). If the process stops between them when the settled document is the
  `complete` one (a release restart, a kill, a database error on the second insert), the next `settle` sees the
  day in `complete` and never returns to it. The owner release windows (15:10 to 18:45) overlap the stage's
  first ticks after 16:15.
- What goes wrong: `summarise` folds digests only, so the day drops out of records, switch table, sell points
  and sealing rates without an error; only a manual replay restores it.
- Smallest fix: store digest and pairs first and the settled document last. Better: decide `pending` from the
  digest's own `complete` (it is written at `runtime.py:212` and read nowhere) and store the digest last; that
  also removes the read of twelve full settled documents per slot in `settle`.
- Verified by reading. The window is narrow; the consequence is permanent and silent.

### MEDIUM

**6. `settle` loads the exit bars of every pending pick day of both slots at once.**
`runtime.py:228-235` (on the scheduled path through `evening`).
- State: a close day stays pending until its slowest capped rule exits (up to 10 sessions,
  `tactic_exits.py:21,175-178`), and with finding 4 every auction day stays pending for 12. So about 20 pending
  readings is the steady state: 20 x 80 rows x about 33 bars (the query takes 30 calendar days each side,
  `tactic_trial_repository.py:95`) is about 53,000 rows.
- Memory: about 1.5 KB a row, so roughly 80 MB on an ordinary day and up to 160 MB at 120 rows a day, plus
  15 to 25 MB while `dict(row)` copies the fetched rows, plus 18 to 27 MB while the twelve settled documents of
  a slot are held to build the `complete` set, plus the pending readings (a few MB). All estimates from
  synthetic rows, not measured on the service.
- Smallest fix: move `read_exit_bars` into the per-reading loop (`runtime.py:241-246`), one pick day at a time
  (about 4 MB), and take `complete` from the digests (finding 5).

**7. The day sections of the scheduled summary never carry an outcome.**
`runtime.py:268-274` after `runtime.py:232-246`.
- State: `evening` runs `settle` first, which stores a settled document for today's two readings. On the pick
  day's evening nothing of it is resolved: for the close slot the first outcome bar is the next session, for the
  auction slot the exits start at bar index 1 (`tactic_exits.py:227-250,262-271`). `summarise` then takes
  `settled[-1]` of each slot, which is that document.
- What is shown: in every scheduled summary section 一 has "0只，再封板率 无数据" for each bucket, 二 lists the
  picks with "无数据", 三 is empty, 四 is ten lines of "无数据", and the day column of 六 is "无数据"; the
  tab's first table under "单个条件" is dashes. The unit test does not see it because its last day has outcomes.
- Second case, same lines: when a slot has no reading on `as_of` (no 09:25 table that day), `settled[-1]` is an
  older session within the last twelve, and its picks appear under the summary's date with no date of their own.
- Fix: decide which day the sections describe. If it is "today's picks", read the reading of `as_of` alone
  (one single-day read, no settled document needed). If it is "what yesterday's picks did", select the newest
  day whose settled document has session-1 outcomes and put that date in `day` and `tactics`.
- Mechanics verified by reading. The pre-change `_latest_days` did the same; the scheduled order makes it systematic.

**8. A registry edit makes stored digests crash the evening summary or drop history without a word.**
`trials.py:420-425` and `trials.py:447-448`; `runtime.py:261-262`.
- Added tactic: `digest["tactics"].get(key)` returns `None` and the day is skipped, so the replay record is
  empty (decision 0023 line 29 accepts this until a rebuild).
- Outcome removed from a tactic (an exit deleted, a single condition's `claim_outcome` changed): the stored
  digest still carries the old key, and `self._series[key, outcome, period]` raises `KeyError` on the first old
  digest. `summarise` then fails every tick until all digests of the 1200-day window are rebuilt.
- Outcome added (a new exit): `add` passes, but `records()` raises `KeyError` at `result["outcomes"][outcome]`
  for any forward digest in `_recent` that predates the edit.
- Each digest carries `version` (`runtime.py:212-213`); `summarise` never looks at it.
- Smallest fix: skip by slot explicitly (`if tactic.slot != slot: continue`) and index
  `digest["tactics"][tactic.key]` and each outcome of `_record_outcomes(tactic)` directly, so every mismatch is
  one error naming the day; store a fingerprint of the registry in the digest and have `summarise` state which
  days need a rebuild.
- Verified by reading.

**9. The only way to rebuild digests replaces live readings with replayed ones.**
`runtime.py:548-549`; `quant-service/app/derived_daily_readings.py:63-64`; `docs/decisions/0023-tactic-trial-ledger.md:29`.
- Trigger: `replay --apply` over a range that includes sessions on or after `REGISTERED_ON`, which is what a
  rebuild after a registry change needs for every day before the new tactic's registration.
- What goes wrong: a replayed reading differs from the stored one (`replayed: true`), so it is stored and
  becomes the newest. Close readings are then built from the history lists where present, auction readings from
  "bars+auction_list" instead of the stored 09:25 table. The forward record of every other tactic for those
  days now rests on replayed readings, and no record or digest says so (the split is by date alone, `trials.py:423`).
- Smallest fix: a digest-only rebuild that reads the stored reading and settled document of each day and calls
  the digest part of `_store_settled`; or make `replay --apply` refuse a day that has a stored reading without `replayed`.
- Verified by reading.

**10. A day whose evening did not finish before 09:00 is never built, and the next session's readings are stored blind.**
`quant-service/app/peer_close_research.py:57` (`REPAIR_STAGES`); `runtime.py:167-172`; `runtime.py:101`; `quant-service/app/main.py:3174`.
- Trigger: the day's lists or risk reading arrive only through the next evening's repair, or the service is down
  from 16:15 to 09:00. `peer_tactic_trials` is not a repair stage, and the target moves on.
- What goes wrong: the day has no close reading. The next morning `refresh_auction` finds no previous close
  reading, builds the auction reading with every `prev.*` condition unknown, stores it with `complete: True`,
  and answers `already_stored` from then on. The next three close readings get `None` for that session in their
  look-back (`runtime.py:69-75`); I did not trace what each role condition does with it.
- The same happens at go-live: the stage has no flag of its own, so it runs on the first tick after the release
  (16:15 or later on a release day), before any history replay has stored the previous sessions' close readings.
- Fix: a repair entry that builds only the two readings of the session before (not `summarise`: a summary dated
  D written on D+1 would carry D+1 outcomes into the D+1 board's labels). For the release: apply the close replay
  through the last session before the service restarts, or hold the stage back until it has run.
- Verified by reading.

**11. The cases route holds up to 20 sessions of full settled documents and readings per request.**
`runtime.py:345-348`; `frontend/src/views/research/TacticTrialsTab.vue:45` always asks `days=20`.
- Memory: 20 settled documents are 29 MB at 100 rows a day and 44 MB at 150, plus 20 readings (auction rows
  embed the whole previous close row; my estimate is 6 to 12 MB). So 35 to 55 MB per click, held for the whole
  request; a second click does not cancel the first.
- Smallest fix: walk the sessions newest first and read one day's reading and settled document at a time.

**12. The board shows raw keys for every "previous session" condition of an auction row.**
`trials.py:749`.
- Trigger: any auction row with a `prev.*` flag (`tactic_conditions.py:536-544` appends `prev.<key>` to `flags`).
  `names` holds only the registry keys, so `names.get(key, key)` returns `prev.theme_7plus` and the column
  "满足的条件" prints it. `_cases` at `trials.py:805` has the same line.
- Smallest fix: use `_pair_condition_name` (it already renders "昨日…") in both places and index instead of `.get`.
- Verified by reading.

**13. "刷新" in the morning empties the summary and both record tables.**
`TacticTrialsTab.vue:23-27,66`; `runtime.py:309-310`.
- Trigger: the tab opens with the newest summary (yesterday's). Pressing 刷新, or picking today's date, requests
  `summary?date=<today>`, which does not exist before the evening run, so `reading` is null and the cards under
  `v-if="reading"` disappear while the board stays.
- Smallest fix: have `read_summary(day)` return the newest summary at or before `day` (the card header already
  prints the summary's own date).
- Verified by reading.

**14. The equivalence test compares the fold with itself.**
`quant-service/tests/test_tactic_trials.py:180-196`; `trials.py:518-525,568-571,662-667,993-1001`.
- `build_daily_summary` now runs `history_of` and `PairHistory`, so the test shows that a JSON round trip does
  not change the fold. It cannot show a difference from the pre-change computation, which no longer exists.
  `json.loads(json.dumps(...))` also keeps key order, while the `jsonb` column does not (finding 18).
- `build_records`, `build_switch_records`, `build_sell_points`, `build_pairs`, `build_daily_summary`,
  `history_of` and `_latest_days` have no caller outside the tests.
- Fix: keep one frozen copy of the pre-change functions in the test module as the reference (or golden numbers
  from b04be9b9), and delete the seven test-only functions from the module.
- By reading, the fold and the pre-change code agree except where listed in findings 8 and 18 and in the
  250-session pair window, which is intended.

### LOW

**15. Reads that fetch a range to use one document.** `runtime.py:301-306` (`_newest` without a day reads
14 days), `runtime.py:270` (twelve settled documents per slot, 18 to 27 MB, to use the last), `runtime.py:275`.
`read_summary()` and `read_board()` parse about ten summaries each (the plan puts one under 200 KB, so about
5 MB), the board also about ten auction readings. Fix: one query ordered newest first with `LIMIT 1`.

**16. `peer_tactic_trials` sits before three unrelated products with a 600-second budget.**
`peer_close_research.py:46`. A slow or hanging run delays temperature, fund flow and timing by up to ten
minutes a tick. Its only ordering need is "after `peer_limit_up_risk`"; put it last.

**17. Auction loop.** `runtime.py:374-384`. (a) A database that is busy until 09:45 is logged as `no_table`.
(b) A pool timeout is `psycopg_pool.PoolTimeout`, not `TimeoutError`, and the pool's default timeout equals the
call's 10 seconds (`database.py:1864`), so "busy" can also leave through the supervisor's restart (5 to 60 s
backoff). It recovers, but the docstring's "waited out, not an exit" does not hold for it. (c) `session_open`
is asked again on every 5-second poll; once per morning is enough. (d) The loop runs wherever
`TACTIC_AUCTION_ENABLED` is true, while the evening stage also needs `longhu_vendor_configured()` (`main.py:3174-3175`).

**18. Order that the code assumes and the store does not keep.** (a) `_add_sell` (`trials.py:350-357`) keeps
buckets in first-seen order where the old code sorted them, so the per-bucket lines of section 六
(`trials.py:933`) change order when the oldest folded day lacks a bucket. (b) `recordRows`
(`frontend/src/research/tactic-trials.ts:178-188`) promises registry order from `Object.entries(summary.records)`;
`normalized` is `jsonb` (`database.py:406`), which returns keys by length, then bytes. Order by `board.tactics`.
(c) Pairs with equal `t` are ranked in digest key order, which `jsonb` also changes.

**19. Dead, duplicate and defensive code added by the change.**
- `"complete"` on the digest and `"version"` on digest and pairs are written and never read (`runtime.py:212-213`).
- `History.seal_rates` returns `"day_rate": None`, which nothing reads (`trials.py:503`).
- `_day_exit_tables` (`trials.py:537-550`) repeats `History.exit_tables`; a one-day `History` gives both the
  day's exit table and the day's sell means.
- `_connected` in the router (`quant-service/app/routers/market_radar_reads.py:128-132`) repeats `runtime._read`;
  the other reads of this router take `database` and need neither.
- `if sessions else as_of` twice in `summarise` (`runtime.py:256,268`) and `if not sessions` in `read_cases`
  (`runtime.py:343-344`): the calendar is never empty on these paths.
- Four spellings of "is a combination": `tactics[:20]` (`trials.py:491,540,974`), `TACTICS[:20]`
  (`:896,943`), `key.startswith("only.")` (`:274`), `tactic in _COMBINATIONS` (`:764`); `read_board` builds
  each card twice to test it (`runtime.py:330-331`).
- `names.get(key, key)` fallbacks (`trials.py:617,749`).
- `BOARD_FIELDS` sends `opens_at_limit`, `amount` and `seal_money`, which the tab does not render (`trials.py:734-735`).

**20. `date` on the board route has no lower bound.** `market_radar_reads.py:135`. A date before 0001-01-31
overflows in `_prior_sessions` (`runtime.py:65`) and returns 500.

**21. Tab requests are not sequenced.** `TacticTrialsTab.vue:33-48`. Two quick clicks on record rows, or two
quick date changes, let the slower response win; the drawer title and the card headers stay consistent with
the data shown, but it may not be the last thing asked for.

**22. After the evening run the default view still shows the previous session's close picks.**
`runtime.py:318-321`. The board follows the newest auction reading, so today's close picks appear only when
tomorrow's date is chosen or after 09:25 tomorrow.

## Suspected, not verified (needs the production store)

- If a `tactic-trials-v1` summary was ever stored by hand before this change, it has no `names` on its pairs,
  no `primary_name` and no `pair_sessions`; `VERSION` did not change, so the read returns it and
  `row.names.join` in the tab throws for that date.
- Each pick day's settled document is stored again on every evening it changes (several versions of 0.3 to
  0.8 MB per slot and day); I did not size what that adds to `raw_market_observations` over a year.

## Checked and found sound

- Record series: `_Series` against the old `_record` (days with picks, picks, mean of daily means, t,
  positive share), including zero-pick days, zero-row days and `None` means.
- Replay and forward split by `registered_on`, and `deque(maxlen=60)` against the old `forward[-60:]`.
- Switch table: unknown excluded, absent key counted false, a day without a mean skipped, sample order kept.
- Sell points and sealing rates: pooled sums and counts reproduce the old pooled means; exit tables remain
  means of daily means; empty slot and empty day shapes match.
- Pairs: `day_pairs` is the old inner loop; `pairs_examined` equals the old count; `t` values are the same floats.
- JSON round trip: lists for tuples, string keys, integer counts; the float rounding is at most 1e-19.
- Fold order: chronological within a slot, chunks contiguous without overlap, the 250-session pair boundary.
- Memory of the fold: about 16 bytes per day and series (about 15 MB at 800 sessions), a chunk of 60 digests
  6 to 9 MB per slot, pair arrays about 10 MB.
- `read_board` point in time: labels from the newest summary strictly before the board day, close picks from
  the session before; a summary dated D is only written between D 16:15 and D+1 09:00.
- `evening` statuses: `blocked` only without the close inputs, otherwise `completed`; an exception is recorded
  failed and retried until 09:00; a completed dependency receipt reads back as `completed` on later ticks;
  nothing is retried beyond the next morning.
- Auction loop clock: start before, inside and after the window, weekends, a closed weekday; the sleep is
  always positive and the loop cannot spin; other exceptions go to the supervisor.
- Task wiring: catalog label, contract, flag, enabled and loop maps, lease.
- Routes: `tactic` and `days` validated, 404 for an unknown tactic; the relay map has the three paths; the
  dashboard paths are literal up to `?`.
- TypeScript interfaces against the Python payloads (`read_board`, `tactic_card`, `_summary_records`,
  `daily_summary`, `PairHistory.result`, `read_cases`, `tactic_day`): the field names match.
