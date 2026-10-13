# Tactic trial ledger: register conditions, combine them into tactics, judge them from every day's summary

Status: rev 8, 2026-10-14. Rev 7 was APPROVED by the user on 2026-10-13 ("可以，按这个方案开工"). Rev 8 keeps the product and changes how it is stored and scheduled, for the measured reasons in the paragraph "Rev 8" below; its key points were reported to the user on 2026-10-14, it has no approval of its own. In implementation on `codex/tactic-trials` (base `932f6b5c`). Rev 5 was audited in three rounds; the exit layer of rev 6 was audited once (ten objections, all accepted in rev 7). The user's words: "不一定要设立很严格的限制，有一些策略思路可以先注册，然后拿每天的样本
去尝试"; "有几个不成立的你也要加入，可以组合成不同的策略战法，去做判断，咱们就是要从每天的总结出来". Earlier requests
it also serves: a 09:25 board of picks and of stocks to avoid; a daily review of consecutive-board stocks with their
success cases and the patterns found.
Rev 2: conditions and tactics are separate layers; rules whose history is against them are registered too;
day-level conditions; the evening summary is a stored product.
Rev 3 (audit round 1, eight objections): sample fields and joins spelled out; the auction slot is its own loop
reading the stored table (the capture runtime is not edited); stage wiring and receipt statuses listed; the date
is the storage key and the decision time is metadata; outcomes are built on `t1_settlement`; the boundary to the
strategy registry is stated and tested; route conventions; seven dispatches. Rejected: "the theme is at position
12 and never stored": the theme of the studies and of this plan is the list's `sector`, position 5, which
`score_day` stores (`limit_up_risk.py:102`).
Rev 4 (audit round 2, seven objections, all accepted; plus the ladder-role conditions the user asked for):
`score_day` keeps its row shape and the trial row is built beside it; units and the new window and market
queries are named; day-level flags are stored and combinations use three-valued logic; `t1_settlement` gets
public helpers first; the auction loop's task registration is listed; the summary reads three capabilities under
a stage timeout; the boundary test covers all four modules; eleven dispatches.
Rev 5 (audit round 3, five objections): `one_word` is a field of the trial row; a reading says whether it is
complete, and a role that needs a missing or incomplete earlier reading is unknown; the decoder stops before the
history gate; the limit helpers take the symbol and return `bool | None`; the replay is a range path in memory;
the auction join keeps every auction row and carries yesterday's day flags; fourteen dispatches. Rejected:
"`leader_switch` cannot be computed": yesterday's leader of a theme is in yesterday's reading (it sealed then);
its absence from today's complete sample is the evidence (`checks/ladder_roles_check.py` computes it this way).

Rev 6 (the user, 2026-10-13: "不能只看次日的收盘价…sell点你也可以预测或者判断一个出来，或者什么情况下就要出局。所以你前面
计算收益的方法可能也不正确"): a pick is no longer scored by one next-day price. Sell rules are registered like
conditions, every pick is settled under all of them, and a tactic is conditions plus an entry plus a sell rule.
First look (`checks/exit_rules_check.out.txt`, `exit_rules_buyer_check.out.txt`): for a holder of yesterday's
limit-up stocks, selling at the next close gives +1.95% / +1.57%, "keep while it seals, else sell by the gap
rule" +3.01% / +2.16%, a 3% stop +1.56% / +1.21% (worse than no stop); for a buyer at the next open, selling at
the following open gives -0.91% / -0.45% and "keep if it seals, else the close" -0.20% / +0.12%. The sell rule
moves a result by 0.5 to 1 point, as much as most conditions.

Rev 8 (2026-10-14; forced by three facts measured on 2026-10-13, not by a change of scope; the user on
2026-10-13: "codex当前推进太慢了，全部交给你来处理现在，全部推进", so the main session wrote steps 10 to 14 itself):
1. The full post-close stage table is not scheduled on the peer (FACTS F023, decision 0021). The evening run is
   the stage `peer_tactic_trials` of the peer's own loop, after `peer_limit_up_risk`, which it depends on.
2. The owner host has 894 MB. `summarise` over the whole stored history took 136 s and 1,211 MB on a synthetic
   760 x 100 history. Every settled day now also stores a **digest** (what the records, the switch table, the sell
   points and the sealing rates need from that day; about 25 KB) and its pairs (about 25 KB); the summary folds
   the digests in chunks of 60 sessions and reads only the latest settled day of each slot in full: 3.6 s and
   103 MB on the same history. The pure functions on full days are the same code driven through digests, and a
   test compares the two paths. The mined pairs look at the last 250 sessions.
3. The store keeps one document a session and capability, so the settled close and auction documents of a day
   cannot share a capability: settled days, digests and pairs have one capability per slot.
4. The replay holds 20 sessions at once and reads the history windows one day at a time (a 60-session chunk asked
   for about two million bar rows); the CLI prints its peak memory.
5. The decision record is 0023 (0021 and 0022 were taken on 2026-10-13).
Where the body below still says otherwise, this paragraph and decision 0023 win.

## Goal

1. Every rule we have (the teacher's, the studies', common practice), supported by history or not, is a
   registered **condition**: an exact yes/no test on a stock of the day's sample, or on the day itself.
2. A **tactic** is a named combination of conditions with a side (watch / avoid / hold). Combining is cheap: a
   tactic is a list of condition keys. Each single condition is also a tactic of its own.
3. Every session all conditions are evaluated on the day's sample and stored with it, so any combination, also
   one registered later, can be scored on every stored day.
4. Every evening a **daily summary** is produced from the numbers: what each tactic picked and how the picks
   did, the day's success and failure cases with the conditions they met, which conditions separated winners from
   losers that day, the running record of every tactic, and the pairs of conditions that stand out so far.
   Judgements come from these summaries, not from an entry bar.

Research evidence only, `live_effect: none`.

## Boundary to the strategy registry

Tactics and conditions are **evidence observations**, not strategies: no `StrategyContract`, no row in
`quant.strategy_promotion_registry`, no row in `quant.strategy_daily_candidates`, no weight. Decision 0006 keeps
applying to strategies; decision 0023 (0019 to 0022 were taken by other workstreams on 2026-10-13) records this classification and the rule that no strategy,
decision, alert or order path may read the trial readings. No existing guard checks import direction inside the
service, so a new static-import test under `quant-service/tests` pins it: the five trial modules
(`tactic_conditions`, `tactic_exits`, `tactic_trials`, `tactic_trial_repository`, `tactic_trial_runtime`) import
nothing from
`strategy_promotion`, `strategy_daily_candidate_ledger` or `platform.strategy_registry`; `STRATEGY_CONTRACTS` has
no tactic key; the only importers of the trial modules are the trial modules, `routers/market_radar_reads.py`
and the composition root `main.py`.
Graduation of a tactic into the candidate ledger is a later, separate decision and out of scope.

## What exists

- The limit-performance lists of T (raw vendor rows; post-close stage `limit_performance_lists`), read with the
  day's bars by `limit_up_risk_repository.read_inputs`. Row positions (as `limit_up_risk.score_day` and the
  studies use them): 0 code, 1 name, 4 first seal (epoch seconds), 5 `sector` (the theme label), 6 closing seal
  (CNY), 7 largest seal, 8 main net, 11 turnover (CNY), 13 free-float value, 15 boards, 16 reopened flag.
  History back to 2023-09-01.
- Reading `limit_up_risk_daily` (`score_day`): `symbol, name, sector, vr, gap, intraday, flow, seal_ratio,
  seal_minutes, first_seal_time, opened, boards, float_cap, sealed_from_open, risk, risk_rank`.
- Reading `auction_open_desk_daily` (`auction_open_desk.build_auction_table`, stored by `auction_pool_runtime`
  from the timetable tick of 09:25:05; two code paths store it, lines 227-236): `symbol, name, open, gap,
  opens_at_limit, bucket` (the gap bucket of `auction_open_desk.gap_bucket`)`, risk_rank, boards,
  sealed_from_open, amount, volume, unmatched`. From 2026-10-13.
- `derived_daily_readings.store` / `newest`: one document per capability and date (`effective_at` is the
  session's close for every reading); `store` skips identical content and appends changed content; `newest`
  returns the latest.
- `t1_settlement`: `SettlementBar`, the limit helpers (`_limits`, `entry_block_reason`, `exit_blocked`) and the
  adjustment by `adj_factor`; `t1_settlement_repository` reads forward bars (limits from the bar only).
- `research_trials.bh_q_values` (pure). `research_trials.evaluate_returns` is Sharpe-based and not used here.
- Post-close stages: `POST_CLOSE_STAGE_ORDER`, `POST_CLOSE_TIMEOUT_OVERRIDES`, `POST_CLOSE_STAGE_DEPENDENCIES`,
  the action map and the dependency dataclass in `post_close_refresh_service.py`; composition in `main.py`;
  receipts accept `completed | partial | blocked | failed` (`post_close_refresh.py:67-80`); tests pin the stage
  list (`tests/test_post_close_refresh.py`).
- Routes of the family: `routers/market_radar_reads.py` (`trade_date` with the public alias `date`); relay GET map
  `researchReads` in `feishu-relay/adapter/research-routes.mjs`; contract test
  `scripts/test_relay_research_routes.py`; generated types `frontend/src/api/generated.ts`
  (`scripts/generate-api-types.mjs` reads the OpenAPI document of a running service); tabs registered in
  `frontend/src/App.vue`.
- The owner database costs about 52 ms per statement: read by ranges, never per row or per day in a loop.

## Design

### Conditions (pure, `quant-service/app/tactic_conditions.py`)

```
key        "theme_big", "seal_small", "gap_up_5_not_sealed", "env_ladder_ok" ...
name       Chinese display name
slot       "close" (known at the close of T) | "auction" (known at 09:25 of X)
level      "stock" | "day"
test       sample row (or day context) -> True | False | None (None: a needed field is absent)
source     "teacher:R20" | "study:st-dragon-leader" | "practice"
claim      +1 | -1 on `claim_outcome`: what the rule's author expects of the stocks that meet it
history    "holds" | "mixed" | "against" | "untested", with one line of numbers and their source
```

### Tactics (pure, `quant-service/app/tactic_trials.py`)

```
key, name, slot, side ("focus" | "avoid" | "hold"), source
all_of      condition keys that must be True
none_of     condition keys that must not be True
at_least    optional (k, [condition keys]): at least k of them True
cell        sample fields that define a pick's comparison group
primary     the outcome the expectation is about;  expects  +1 | -1
registered_on   the date the definition was fixed; a changed definition is a new key
```
A tactic may use stock-level and day-level conditions of its slot; an `auction` tactic may also use the `close`
conditions the stock met the session before (prefixed `prev.`). Every condition yields one single-condition
tactic automatically (`side` from its claim, `cell` = board bucket).
`CONDITIONS` and `TACTICS` are tuples in the modules; adding one is a code-only release.

### Samples

- `close`, day T: one row per stock of the day's limit-up list. The vendor row is decoded by one function
  extracted from `score_day` with its filtering unchanged (malformed, duplicate, suspended, not-at-limit rows)
  and ending before the history gate (`limit_up_risk.py:95-98`): `score_day` applies that gate itself and keeps
  emitting exactly its current row (the stored `limit-up-risk-v1` reading and its parity fixture do not change);
  the trial keeps every decoded row and builds its own row beside it: `symbol, name, sector, boards, board_bucket
  (1, 2, 3+), first_seal_time, seal_money, seal_max, main_net, turnover, float_cap, reopened, sealed_from_open,
  one_word` (the day's high equals its low; `high` and `low` are added to the day's bar query)`, vr, gap`, the
  stored `risk_rank` (absent for a row the history gate drops), and `theme_count` = decoded rows of the day
  sharing the `sector`.
  Units: the list's amounts are CNY; the bars' `amount` is thousand CNY (the convention documented at
  `market_temperature_repository.py:34-37`); every trial field is CNY.
  From the stock's previous 250 stored sessions, by one new range query for all sample symbols (`trading_date,
  close, high, volume, amount, adj_factor`; `read_inputs` returns only 20 prior amounts and cannot serve this):
  `turnover_vs_max250`, `runup_20` and `close_vs_high60` on adjusted prices, and two cost readings over 120
  sessions with a 20-session half-life (`cost_share_below_close`, `cost_share_0_30_above`; the corrected proxy of
  `st-chip-distribution`).
  Day context, by one new aggregate query over the market's bars of the last 61 sessions: `market_turnover`,
  `market_turnover_median60`; from the sample: `limit_up_count`, `boards_3plus_count`; from yesterday's reading:
  `prev_top_board_sealed_again`.
  Ladder roles need the day's own ordering (first seal times inside a theme and inside a board count) and the
  stored `close` readings of the three previous sessions (one range read): yesterday's leader of each theme, the
  stock's own row of yesterday, and whether a streak of 2+ boards of the stock ended one or two sessions ago.
  A `close` reading carries `complete` (the day's lists were stored and decoded). "Did not seal" means absent
  from a complete sample; when a needed earlier reading is missing or not complete the role is unknown (None),
  never False. This is the case on the first days of a replay.
- `auction`, day X: the rows of the stored `auction_open_desk_daily` of X, every one kept; each left-joined by
  symbol to the stock's row in the previous session's `close` reading (its fields and flags, under `prev`), which
  `refresh_auction` reads through the repository. When that reading is missing, not complete or lacks the symbol,
  every `prev.` condition of the row is unknown. `gap_bucket` is the desk's `bucket`; `auction_amount_rank` is the
  rank inside the pool. There are no auction day conditions in v1: the reading's `day` holds empty `flags` and
  `unknown` and, under `prev`, the day flags of the previous `close` reading, which auction tactics may use.
- Every row stores `flags` (keys of the stock conditions that are True) and `unknown` (keys that returned None);
  the reading's `day` stores the same two lists for the day conditions. With these a combination registered
  later is decidable on every stored day.
- A combination uses three-valued logic: `all_of` fails on any False, else is unknown on any None; `none_of`
  fails on any True, else is unknown on any None; `at_least (k, ...)` holds when k are True, fails when True plus
  None are fewer than k, else is unknown. A row that is unknown for a tactic is neither a pick nor a comparison
  row for it and is counted.

### Entries, sell rules and outcomes (pure; one walk over sessions, the one `t1_settlement` already has)

`t1_settlement.settle` walks the sessions after a signal: the first bar is the entry session, `entry_block_reason`
rejects an unfillable entry (a missing or invalid open, a suspension, an open at the up-limit for a long), an
exit on a blocked session (`exit_blocked`: suspended, no valid close, close at the down-limit) rolls forward and
is priced at the next sellable **close**, prices are adjusted by `adj_factor` when every bar of the window has
one and are raw otherwise (`price_basis` says which), and the net return subtracts `ROUND_TRIP_COST` (both
commissions, transfer fee, slippage on both sides, the sale's stamp tax). The exit layer does not re-implement
any of this. A step first refactors `t1_settlement`, with `settle` built on the result and its tests passing
untouched:
- the walk as a shared helper: the bars on one price basis (the existing rule and label), the entry check, the
  roll; a caller supplies the rule that says on which session and at which price field it sells;
- `sale_blocked(symbol, bar, field)`: the generalisation of `exit_blocked` to the action's price: an open-priced
  sale is blocked by an open at the down-limit, a close-priced one by the close (today's `exit_blocked`), both by
  suspension or a missing price. A resting order that the session's high or low reaches is a fill, whatever the
  close does afterwards;
- `opens_at_up_limit(symbol, bar)`, `closes_at_up_limit(symbol, bar)` on the private `_limits` (which needs the
  symbol), `bool | None`.
The trial code imports only public names. The trial repository reads 12 sessions after the pick in the shape
`SettlementBar` takes, and the 4 closes before the entry for the average-based rule; limits from the bar and,
where the bar has none, from `quant.daily_trade_limits`.

**Entries** (who the result is for; always long):
- `held`: the stock is held at the reference close (close slot: the close of T; auction slot: the close of the
  session before X). There is no entry session and `entry_block_reason` is not applied. First sellable session
  S1: the next one. No cost is charged: `held` net equals raw, deliberately (the position exists; every rule
  sells it once), and a `held` figure is not comparable with a `buy_open` net figure.
- `buy_open`: bought at the open of the next session (close slot) or of X (auction slot). When
  `entry_block_reason` rejects that open it is not a trade: counted with its reason, never priced. S1 is the
  session after the entry (T+1 rule). Raw and net are both stored, net = raw minus `ROUND_TRIP_COST`; records
  use net.

**Sell rules** (`EXITS`, registered like conditions: key, name, source, a pure function that, session by
session from S1, says "sell now at this field" or "keep"). Sessions are numbered S1 to S10; a position still
open at S10 is sold at its close. A sale blocked at its price (`sale_blocked`) is made at the first later price
that is not blocked: the same session's close for an open-priced sale, then the following closes, as `settle`
rolls. Targets and stops compare the session's open, high and low with the entry price (`buy_open`) or the
reference close (`held`) on the walk's price basis, so an ex-rights session does not move them. A sale "at the
close" after observing that close is the research convention `settle` already uses (modelled at the observed
close), not a claim that an order sent after the close fills there.

| key | rule on the first sellable session (S1) and after | source |
|---|---|---|
| `open` | sell at the open of S1 | baseline |
| `close` | sell at the close of S1 | baseline (what the studies used) |
| `tp3`, `tp5`, `tp8` | a resting sell order at +3 / +5 / +8%: filled at the open when S1 opens above it, at the target when the high reaches it, else sold at the close of S1 | practice |
| `sl3` | a stop at -3%: at the open when S1 opens below it, at the stop when the low reaches it, else the close | practice |
| `tp5_sl3_stop_first`, `tp5_sl3_target_first` | both orders; a session that reaches both is counted both ways (daily bars cannot order them) | practice |
| `hold_while_sealed` | a session that closes at the up-limit is kept; the first session that does not is sold at its close | teacher R51 |
| `gap_rule` | S1 opens at the up-limit: as `hold_while_sealed`; S1 opens 5% or more up, not at the limit: sell at the open; otherwise sell at the close, except that a close at the up-limit is kept under the same rule the next session | study (the gap buckets) |
| `must_rise` | S1 closes at or below the entry or reference price: sell at that close; otherwise keep, and sell at the first close that is not a new closing high since the entry | teacher R47 |
| `below_ma5` | sell at the open after the first close below the 5-session average of closes; at most 10 sessions | teacher R47/R48 |

Waiting for data (minute bars), listed and not evaluated: not sealed by 10:00, below the session's average price
at a set time, the board breaks, a trailing stop from the day's high. With them the bracket of the two
`tp5_sl3` rules also closes.

**Per pick**, stored: for each entry and each sell rule the return (raw, and net for `buy_open`), the exit
session, the reason and the price basis (absent while not resolved, or when a bar is missing; nothing is
zero-filled); the state outcomes `seal_again` (S1 closes at the up-limit; `seal` for the auction slot),
`big_loss` (`close` return of `held` at or below -5%); and the room measures `s1_high`, `s1_low` (S1's high and
low against the entry or reference) and `s3_high` (the highest high of S1 to S3). These are first-session
measures of their own, not the ledger's whole-window MFE and MAE.

### Results and records

A tactic names its `entry` and its `exit` (its own sell rule): a tactic is conditions plus an entry plus a sell
rule. `primary` is the return under that pair for `focus` and `hold` tactics and a state outcome (`seal_again`,
`big_loss`) for `avoid` tactics. The registered combinations are also scored under every sell rule (the exit
table); single-condition tactics under their own rule and under `close`.

Day result of a tactic, per outcome: picks' mean; for each pick the mean of the non-picked rows of its cell; the
day's difference = mean over picks of (pick - cell mean); counts (picks, picks without a comparison row, rows
unknown). Record over days: days with picks, picks, mean daily difference, its day-clustered t (the one new
statistic: mean of daily differences over their standard error), share of positive days. Three records per
tactic, never mixed: `replay` (days before `registered_on`; marked in-sample), `forward` (from `registered_on`),
`recent` (last 60 forward days).
Day-level conditions get a switch record: the sample's daily mean outcome on days when the condition is True
against the other days (difference, Welch t, day counts).
Forward label on the primary outcome: `collecting` (under 20 days with picks or 60 picks), then `supports`
(t >= 2 in the expected direction), `against` (t <= -2), else `undecided`; with the BH q-value across all
tactics (`research_trials.bh_q_values`). Labels describe; nothing is gated on them.

### Daily summary (the product the user reads; text from fixed templates, no model writes it)

For the evening of T, from the days settled that evening:
1. the day: sample sizes, sealing-again rate by board bucket beside its long-run rate, the day conditions;
2. tactics: each registered combination with its picks, their outcomes and the day's difference;
3. cases: the stocks that sealed again and the stocks with a big loss, each with the conditions it met;
4. conditions of the day: for every stock condition, the picks' primary outcome against the rest, sorted;
5. records: every tactic's three records, and the labels that changed since the previous summary;
5a. sell points: for the day's whole sample and by board bucket and gap bucket, the mean under every sell rule
   beside the room (`mfe1`), today and cumulatively; for every registered combination its exit table (its own
   rule, the best and the worst rule so far, `close` as the reference);
6. pairs: for every pair of stock conditions of a slot, the record of "both True" over all stored days; only the
   15 strongest in each direction are kept, with their counts, marked as mined, with the number of pairs examined.

### Readings (the date is the key; the decision time is a field)

- `tactic_trials_close_daily` (T), `tactic_trials_auction_daily` (X): `{trade_date, slot, version, decided_at,
  day: {...}, rows: [{symbol, name, cell fields, flags, unknown, the numbers shown on the board}], research_only,
  live_effect}`. `decided_at` is when the inputs were fixed (the close; the stored time of the 09:25 table).
  Written once by the scheduled path: it reads `newest` for the day and skips when a reading of the current
  `version` exists. The CLI replay with `--apply` stores a newer document (`replayed: true`), which `newest` then
  returns.
- `tactic_trials_settled_close_daily` and `tactic_trials_settled_auction_daily` (pick day; rev 8: one capability
  per slot, because the store keeps one document a session and capability): each row's returns under every entry and sell rule, state
  outcomes and room measures, with `complete` true once every rule is resolved. Every document is a full
  snapshot of the pick day (all rows, everything resolved so far), so reading only `newest` never loses a
  result. The stage stores a newer snapshot for a pick day until it is complete (usually two or three evenings;
  `store` keeps the older versions, there is no cap).
- `tactic_trials_digest_{close,auction}_daily` and `tactic_trials_pairs_{close,auction}_daily` (pick day; rev 8):
  stored with every settled snapshot of a day; what the summary folds instead of the rows.
- `tactic_trials_summary_daily` (T): the daily summary (records, the day's cases, the kept pairs). The routes read
  this, never the whole history.
- Size: about 100 rows a day and slot, some tens of KB per reading, the summary under 200 KB. The four
  capabilities stay in the hot tier (no entry in `storage_tiering_policy`); decision 0023 records it.

### When it runs

- (Rev 8) Stage `peer_tactic_trials` of the peer's evening loop (`peer_close_research.py`), after
  `peer_limit_up_risk` (its dependency): build the `auction` reading if the morning loop did not; store the
  `close` reading of T; settle the pick days of the last 12 sessions that are not complete, storing each day's
  digest and pairs with it; fold the stored digests into the summary. Timeout 600 seconds. Receipt: `completed`,
  or `blocked` when the day's lists or risk reading are not stored (tried again on the next tick); an exception is
  `failed` and is tried again. The rest of this bullet is rev 7 and no longer holds.
- (Rev 7, superseded) Post-close stage `tactic_trials` after `limit_up_risk` (dependency: `limit_up_risk`): store the `close` reading
  of T; settle the pick days of the last 12 sessions that are not complete; store the summary. Reads are ranges: the
  day's inputs, the window query, the market aggregate, and the whole stored history in three calls of `newest`
  (close, auction and settled readings; about 760 days of about 100 rows). Records and pairs are recomputed from
  the stored rows every evening; there are no accumulators. Timeout override 600 seconds, the same bound on the
  stage's database call. Receipt: `completed`
  (also on a day without limit-up rows), `blocked` (the day's lists or risk reading are not stored), `partial`
  (the close reading is stored and a pick day older than 12 sessions is still not complete, or the summary was
  not stored),
  `failed`.
  Wiring: `POST_CLOSE_STAGE_ORDER`, `POST_CLOSE_TIMEOUT_OVERRIDES`, `POST_CLOSE_STAGE_DEPENDENCIES`, the action
  entry and the dependency dataclass, `main._post_close_refresh_dependencies`, and the tests that pin the list.
- Auction: its own loop `run_tactic_auction_loop`, shaped like `run_auction_pool_capture_loop` and independent
  of it. On a session day from 09:25:05 it reads today's stored `auction_open_desk_daily` every 5 seconds (at
  most until 09:45), then builds and stores the `auction` reading. The stored table is the only interface;
  `auction_pool_runtime` is not edited, and no failure here can reach the capture.
  Its dependencies: a Shanghai clock, `sse_calendar_open_async`, the database and run-database ports, a sleep
  function. Each read goes through the blocking executor with a 10-second timeout; `ExecutorSaturatedError` and
  `TimeoutError` are the database-busy case the capture loop already names (`DATABASE_UNAVAILABLE`) and make the
  loop wait and try again, not exit.
  Registration, as for the capture loop (`main.py:3216-3217`, `main.py:4176-4216`): a task contract in
  `RUNTIME_TASK_CONTRACTS` (`runtime_tasks.py`), its label in `background_task_catalog.py`, a feature flag, the
  enabled and loop maps, the runtime health metadata, and a composition test like
  `tests/test_auction_pool_composition.py`.
- CLI `python -m app.tactic_trial_runtime --slot close|auction --start --end [--apply]`: replay of stored
  history as a range path, not a loop over `refresh_close`: load the raw list observations, the bars and windows
  and the existing trial readings of the requested range in bounded range queries (chunks of about 60 sessions),
  build the days in memory in date order (each day's roles use the readings just built for the days before it),
  then `store` the documents of a chunk in one call. The existing list reader is single-day
  (`limit_up_risk_repository.py:22-33`): the repository gets a range reader beside it.
  History on the owner, measured 2026-10-13: bars 2023-08-15..2026-10-12 (768 sessions, about 5,350 stocks a
  day; `adj_factor` on 48.5% of the 2023 rows and 99.6-99.9% after; bar limits on 91.4% of the 2023 rows and
  98.5-99.9% after; `daily_trade_limits` only from 2026-09-01); limit lists on all 749 sessions from 2023-09-01;
  the vendor's opening-auction list (`longhuvip_history` / `morning_bidding_history`, the largest auction amounts
  only) on 739 sessions to 2026-09-18. Consequences for the replay:
  - the `auction` slot is replayed too, from the bars (open, gap, opens at the limit) and, where the stock is on
    the stored auction list, its auction amount (position 4) and its order on the list; a pool stock that is not
    on the list has no amount, and conditions that need one are unknown for it. Replayed auction rows say so
    (`source: "bars+auction_list"`);
  - windows are shorter at the start: the 60-session fields exist from about 2023-11, the 250-session maximum
    uses what exists (at least 60 sessions) and is a full window only from about 2024-08;
  - the replay reports, per year, how often "closes at the up-limit" from the bars disagrees with membership in
    that day's limit list, and the share of returns on the raw price basis (2023's missing factors); a
    disagreement above 0.5% stops the replay's `--apply` and is reported.

### Routes and page

- `GET /api/v1/market/tactic-trials/board` (`trade_date`, alias `date`): the 09:25 board: stocks by side with the
  tactics that picked them and each tactic's label; plus the `close` picks of the previous session for holders.
- `GET /api/v1/market/tactic-trials/summary` (`trade_date`, alias `date`): the daily summary.
- `GET /api/v1/market/tactic-trials/cases` (`tactic` bounded in length and checked against the registry, `days`
  bounded): a tactic's recent pick days with picks and outcomes.
- Route tests like `tests/test_limit_up_risk.py:212-260`; entries in the relay's `researchReads` map (literal
  paths in the dashboard so `scripts/test_relay_research_routes.py` passes); `npm run api:generate` against a
  service running the branch's code, `generated.ts` checked in.
- Console tab **战法试验台** registered in `App.vue`: the board; the day's summary; the tactic table (slot, side,
  source, history, replay, forward with label and q) with a drawer of cases; the conditions table; the pairs
  table. Typed `getJson` calls, Vitest tests of the pure helpers. Research-only banner; replay figures marked
  in-sample.

## First conditions (v1)

History: development / hold-out (2023-09..2025-12 / 2026-01..09), from `docs/archive/2026-10-13-st-*.md`,
`docs/archive/2026-10-13-st-review-notes.md` and `state/research/shortterm_tactics_20261013/组合战法_v0.md`.
Rules with history against them are registered like the others.

Close, stock level:

| key | test | source | history |
|---|---|---|---|
| `theme_big` | `theme_count >= 10` | teacher R06 | holds |
| `theme_7plus` | `theme_count >= 7` | study | holds |
| `theme_lone` | `theme_count <= 3` | teacher R06/R07 | holds (returns) |
| `theme_leader_earliest` | earliest first seal of a theme with at least 2 (ties: more boards, then symbol) | teacher R01/R21 | holds (holders) |
| `theme_leader_top_board` | most boards of a theme with at least 2 (ties: earlier seal) | teacher R01 | holds (partly) |
| `market_top_board` | the market's highest board count, at least 3 | teacher R11 | holds (holders) |
| `board_2_3`, `board_4plus`, `board_6plus` | board count | teacher R11 | mixed |
| `seal_small` | `seal_money < 20,000,000` | teacher R20 | holds |
| `seal_large` | `seal_money >= 300,000,000` | teacher R20 | mixed |
| `volume_heavy` | `vr >= 3` | teacher R15/R27 | holds |
| `volume_shrunk` | `vr < 1` | teacher R19 | mixed |
| `amount_record_250` | `turnover_vs_max250 >= 1` | teacher R34 | against (next day) |
| `seal_late` | first seal at 14:30 or later | teacher R21 | holds |
| `seal_early` | first seal before 10:00 and not `sealed_from_open` | teacher R21 | untested |
| `sealed_from_open` | sealed in the auction | teacher R17 | holds |
| `reopened` | opened after sealing, closed sealed | teacher R18/R22 | against |
| `runup_40` | `runup_20 >= 0.40` | teacher R49 | holds (big loss) |
| `turnover_10bn` | `turnover >= 10,000,000,000` | teacher R36 | mixed |
| `above_high60` | `close_vs_high60 > 0` | teacher R31 | mixed |
| `main_net_out` | `main_net < 0` | study | holds |
| `risk_top_fifth` | top fifth of `risk_rank` | study | holds |
| `catch_up_first_board` | first board in a theme whose leader has at least 3 boards | practice | mixed |
| `cost_above_band` | `cost_share_below_close >= 0.95` | practice (筹码) | mixed |
| `cost_overhead_heavy` | `cost_share_0_30_above >= 0.30` | practice (筹码) | mixed |

Close, stock level, ladder roles of consecutive-board play (added 2026-10-13 at the user's request: 卡位、补涨、
助攻 and the other leader plays; first look in `checks/ladder_roles_check.out.txt`, same comparison as above):

| key | test | source | history (next close; sealing again) |
|---|---|---|---|
| `tier_first` | earliest first seal among the day's stocks with the same board count (2+ boards, at least 3 in the tier) | teacher R12 同梯队赛马 | holds: +2.51% / +2.18%; +19.6 / +18.1 points |
| `front_row` | fewer boards than its theme's leader and sealed earlier than the leader | 卡位 (teacher R03) | holds: +1.34% / +1.12%; +10.0 / +9.4 points |
| `kawei_leader` | sealed earlier than yesterday's leader of its theme, which sealed later today | 卡位 (teacher R03) | holds for holders: +1.66% / +1.11%; +12.6 / +8.9 points; next-open buyer -0.55% / -1.46% |
| `leader_switch` | yesterday's theme leader did not seal; this is today's top board of the theme (at least 2 limit-ups) | 龙头切换 | mixed: +0.70% / +0.03% |
| `assist` | first board sealed within 30 minutes after its theme's leader (leader has 2+ boards) | 助攻 (teacher R07/R21) | mixed, no edge: +0.20% / +0.42% |
| `back_row` | fewer boards than its theme's leader and sealed later | 后排 (teacher R07) | holds (weaker): -0.54% / -0.33%; -5.6 / -5.5 points |
| `sole_high_board` | alone at its board count, 3+ boards | 独苗高标 | against: no difference |
| `ladder_full` | its theme has a first board, a second board and a 3+ board | 梯队完整 | mixed: +0.70% / +0.38% |
| `weak_to_strong` | 2+ boards; yesterday sealed after 13:00, reopened or sealed under 20 million; today sealed by 09:45 | 弱转强 | against: +0.28% / 0.00% |
| `divergence_to_consensus` | yesterday reopened or `vr >= 2`; today one-word | 分歧转一致 | holds for holders (cannot be bought): +3.32% / +4.64% |
| `consensus_to_divergence` | one-word yesterday, a traded board today | 一致转分歧 (teacher R17/R18) | holds as a risk: -1.91% / -1.84%; big loss +7.9 / +9.2 points |
| `rebound_board` | a new first board one or two sessions after a streak of 2+ boards ended | 反包 (teacher R14) | holds on big loss: +15.0 / +6.1 points; next close -1.10% / -0.49% |

Waiting for data: the intraday side of 卡位 (buying at the moment a stock seals ahead of the leader) needs the
broken-board list and minute data for the stocks that touched and failed; next plan.

Close, day level: `env_ladder_ok` (`boards_3plus_count > 5` and `limit_up_count > 20`; teacher R13; against),
`env_turnover_2p1` (`market_turnover >= 2.1e12`; teacher R38; against), `env_turnover_above_median60` (untested),
`env_top_board_failed` (not `prev_top_board_sealed_again`; study; mixed).

Auction, stock level: `opens_at_limit` (teacher R17; holds), `gap_down_2` (`gap < -0.02`; study; holds),
`gap_up_5_not_sealed` (`gap >= 0.05`, not at the limit; teacher R23; holds), `gap_3_to_8` (teacher R23; against
for a buyer at the open), `gap_0_to_5` (untested), `auction_amount_top5` (study; mixed), `auction_burst`
(`amount >= 0.217 * prev.turnover`; practice; mixed), `board_gap_below_typical` (at least 2 boards, not at the
limit, gap under 5.43% for 2 boards and 8.60% for 3+; study; mixed).
Waiting for data, listed on the page and not evaluated: the teacher's R25 (auction amount about 1.5 times the
previous day's auction; seal growing after 09:20), R03/R24 (the first five minutes), R12 (racing inside a tier),
R18 (volume at the reopening).

## First tactics (combinations, v1)

Entry and sell rule per tactic: `hold` tactics `held` + `gap_rule`; `focus` tactics `buy_open` + `gap_rule`
(`teacher_auction_entry`: `must_rise`); `avoid` tactics a state outcome, with `held` + `open` against `held` +
`close` shown beside it (does leaving at the open beat staying). The `primary` column below names the state
outcome or "return" (the tactic's own entry and sell rule).

| key | slot, side | definition | primary, expects | why |
|---|---|---|---|---|
| `leader_hold` | close, hold | `theme_7plus` and `theme_leader_earliest`; none of `seal_small`, `volume_heavy`, `seal_late` | return + | the teacher's leader, cleaned of the three weak-board signs |
| `relay_2_3` | close, focus | `board_2_3` and `theme_7plus`; none of `seal_small`, `volume_heavy` | `seal_again` + | teacher R11 + R06 + R20 + R27 |
| `far_volume_cleared` | close, focus | `amount_record_250` and `theme_big` | return + | teacher R34 as he states it; history is against it for the next day |
| `shrunk_acceleration` | close, focus | `volume_shrunk`; none of `sealed_from_open`; at least 1 of `board_2_3`, `board_4plus` | `seal_again` + | teacher R19 |
| `ladder_env_relay` | close, focus | day `env_ladder_ok`; at least 1 of `board_2_3`, `board_4plus` | return + | teacher R13 |
| `after_top_failed` | close, avoid | day `env_top_board_failed`; at least 1 of `board_2_3`, `board_4plus` | return - | the negative-feedback claim |
| `weak_board_two_signs` | close, avoid | at least 2 of `seal_small`, `volume_heavy`, `amount_record_250`, `seal_late`, `runup_40`, `risk_top_fifth`, `theme_lone` | `big_loss` + | the avoid list combined |
| `tier_race_winner` | close, hold | `tier_first`; none of `seal_small`, `volume_heavy` | return + | teacher R12: the winner of its tier |
| `kawei_hold` | close, hold | at least 1 of `front_row`, `kawei_leader`; `theme_7plus` | `seal_again` + | a stock that took the position in a strong theme |
| `assist_no_relay` | close, avoid | at least 1 of `assist`, `back_row`; none of `tier_first`, `front_row` | `seal_again` - | teacher R07: assists and back rows are not for relaying |
| `catch_up_low` | close, focus | `catch_up_first_board`; none of `runup_40`, `seal_small`, `seal_late` | return + | 补涨 in a theme with a high leader |
| `first_open_after_one_word` | close, avoid | `consensus_to_divergence` | `big_loss` + | teacher R17/R18 |
| `rebound_board_risk` | close, avoid | `rebound_board` | `big_loss` + | teacher R14 read against the data |
| `kawei_gap_up` | auction, avoid | at least 1 of `prev.front_row`, `prev.kawei_leader`; `gap_up_5_not_sealed` | return - | the position-grabber that opens high |
| `tier_winner_moderate_gap` | auction, focus | `prev.tier_first` and `gap_0_to_5` | return + | the tier winner that does not open too high |
| `leader_moderate_gap` | auction, focus | `prev.theme_leader_earliest`, `prev.theme_7plus`, `gap_0_to_5`; none of `prev.risk_top_fifth` | return + | a leader that does not open too high |
| `teacher_auction_entry` | auction, focus | `gap_3_to_8` and `auction_burst`; none of `prev.sealed_from_open` | return + | the course's auction model (R23 + R25) |
| `amount_top5_big_theme` | auction, focus | `auction_amount_top5` and `prev.theme_big` | `seal` + | the strongest auction of a strong theme |
| `gap_up_weak_board` | auction, avoid | `gap_up_5_not_sealed`; at least 1 of `prev.seal_small`, `prev.volume_heavy` | return - | a weak board that opens high |
| `weak_board_gap_down` | auction, focus | `gap_down_2`; at least 1 of `prev.seal_small`, `prev.volume_heavy` | return + | the repair after a low open (relative edge in history) |

## Steps (sixteen dispatches in one worktree, in this order, each about 15 minutes; backend tests are `test_*.py` under `quant-service/tests`, `unittest.TestCase`)

1. Decoder: extract the vendor-row decoder from `score_day` (ending before the history gate), the stored row
   unchanged; the parity fixture and the existing tests pass untouched; a test of the decoder on malformed,
   duplicate and not-at-limit rows.
2. History features (pure, `tactic_conditions.py` starts here): from a stock's window of sessions, on adjusted
   prices: `turnover_vs_max250`, `runup_20`, `close_vs_high60`, the two cost readings; the trial row and the day
   context builders from plain inputs. Tests with an ex-rights day and with short history.
3. Base conditions: the condition type, the conditions of themes, boards, seal, volume, run-up, cost, risk, the
   day conditions and the auction conditions; evaluation to `flags` / `unknown` for rows and for the day. Tests:
   every condition true, false and unknown.
4. Ladder roles: the twelve role conditions on the day's rows and the three previous readings, unknown when a
   reading is missing or not complete. Tests on a hand-built ladder of two themes over four days.
5. `t1_settlement`: the shared walk (price basis, entry check, roll), `sale_blocked`, `opens_at_up_limit`,
   `closes_at_up_limit`; `settle` rebuilt on them with every existing settlement test passing untouched; tests
   of the new public functions.
5a. `tactic_exits.py` (the fifth trial module, in the boundary test too), part one: the two entries, the
   single-session rules (`open`, `close`, `tp3`, `tp5`, `tp8`, `sl3`, the two `tp5_sl3` brackets), state outcomes
   and room measures. Tests on hand-built bars: a gap over the target, target and stop on one session, an
   ex-rights session, an open at the down-limit, an unfillable entry, a missing bar.
5b. `tactic_exits.py`, part two: the multi-session rules (`hold_while_sealed`, `gap_rule`, `must_rise`,
   `below_ma5`), "not resolved yet", the S10 cap. Tests: each rule over several sessions, a blocked close that
   rolls, a rule still open when the bars end.
6. `tactic_trials.py`, part one: the tactic type with `entry` and `exit`, `TACTICS` (v1) and the
   single-condition tactics, the three-valued combination, picks, day results inside cells. Tests: every tactic
   on a hand-built day.
7. `tactic_trials.py`, part two: the three records, the switch record, labels and q-values, the pairs table, the
   daily summary and its templates. Tests on a hand-built week.
8. `tactic_trial_repository.py`: the range reads (raw list observations of a range, the day's bars with `high`
   and `low`, the window query, the market aggregate, bars with limits for outcomes, the stored readings of three
   capabilities, the next sessions). Tests on the SQL shapes with the fake connection pattern of
   `tests/test_limit_up_risk.py`.
9. `tactic_trial_runtime.py`: `refresh_close`, `settle`, `summarise`, the written-once rule, the range replay and
   the CLI. Tests with a fake database, including a replay whose roles use the days it has just built. A timing
   run of `summarise` on a synthetic history of 760 days of 100 rows, reported: it must fit the stage's 600
   seconds with a wide margin (numpy is in `quant-service/requirements.txt` if the pure-Python path does not).
10. The post-close stage: order, timeout override, dependencies, action entry, dependency dataclass,
    composition, receipt statuses; the tests that pin the stage list. The boundary test.
11. `refresh_auction`, `run_tactic_auction_loop`, its registration (task contract, catalog label, flag, enabled
    and loop maps, health metadata) and the composition test; the loop on a fake clock (table appears, table
    never appears, database busy).
12. Runtime reads `board`, `summary`, `cases`; the three routes and their tests; relay entries; `generated.ts`.
13. `frontend/src/research/tactic-trials.ts` with tests, `TacticTrialsTab.vue`, `App.vue`.
14. `docs/decisions/0023-tactic-trial-ledger.md`; the full acceptance run.

After release: a dry run of one 20-session chunk on the owner first (it prints `peak_rss_mb`; look at `free -m` before), then replay the close slot and after it the auction slot over the stored history (a write on the owner: run by the user), then `--summarise` for the last session.

## Acceptance

```bash
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
cd frontend && npm run typecheck && npm run test -- --run && npm run build && npm run api:check
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q   # test_runtime_hosts.py fails on main already; nothing else may
node scripts/verify-api-contract.mjs                      # after the routes exist on a running service
git diff --check
```

## Risks

- Replay records are in-sample for rules shaped on this history; the page says so and keeps them apart from the
  forward records.
- Many tactics and all pairs are looked at: q-values and the count of pairs examined are shown for that reason.
  A label can flip with more days.
- 40-100 limit-up closes and 5-20 consecutive boards a day: combinations of three or more conditions pick few or
  no stocks on most days; the record shows days with picks, and a tactic that never picks is visible as such.
- The auction slot has no history before 2026-10-13.
- The thresholds of the teacher-derived conditions are mine, taken from his examples; each is one registered
  version, and another threshold is another key.
- The cost readings are a volume-decay proxy, not holders' real cost.

## Out of scope

The follow-ups below, the data to backfill (earlier daily bars, 2023 factors and limits, minute bars) and what
the user must run are tracked in `state/research/shortterm_tactics_20261013/后续执行清单.md`.


- Conditions on the whole market at the close (heavy-volume stall under a prior high, trend pullbacks to the
  5/10-session average): they need all stocks' windows; next plan, same ledger.
- Conditions from auction ticks and minute data (the "waiting for data" list): next plan.
- Graduation into the candidate ledger, alerts, pushing the summary to a chat group, any use by an order path.
- The teacher's per-stock plans and their playbook parameters (a separate list of gaps exists in the digest).
