# Intraday V reversal: research registration

Workstream W007. Registered 2026-10-13 (Asia/Shanghai), before any outcome of the minute data was looked at.
Research evidence only: nothing here is a trading instruction, and no result changes a live threshold.

## What the user asked

- 2026-10-13: "今天出现了V的情况，就是到了底部然后可能开始放量，有资金进场了，研究一下增强我们的能力"
- 2026-10-13: "这几个假设你可以放到历史上去验证，我觉得2026年尤其是7月到现在见过的挺多的。"
- 2026-10-13: "这里就是可以综合看时间30分钟，60分钟，或者其他级别的背离，以及情绪冰点，国家队宽基etf放量多种结合"
- Standing instruction for this project (2026-10-13): thresholds need not be strict; an idea that is not supported is
  registered too, so that it can be tried on each day's sample and combined with others.

## Questions

1. On a day when the average stock has fallen deep, what, known at that minute, separates the days that turn
   (a V) from the days that keep falling?
2. Do these help, alone and combined: divergence on several bar sizes, divergence between the average stock and
   the weighted index, a sentiment ice point, heavy volume in the broad index funds, volume on the rebound leg?
3. Once a rebound has started, what separates one that holds from one that fails (2026-10-13: the 10:15 bounce
   failed within 15 minutes, the one after 11:15 held), and what is the fade after the day's peak worth overnight?

## Data (read-only, absolute paths; nothing is fetched by this study)

### Minute series: `/Users/papa/codebase/n8n/state/research/vshape_20261013/data/<code>.jsonl`

One JSON line per session, 2023-09-01 .. 2026-10-12 (749 sessions when complete):
`day` (`YYYYMMDD`), `preclose`, `trend`, `total_turnover` (CNY), and `open`, `high`, `low`.
`trend` is a list of `[HH:MM, price, third, volume, flag]`, 09:30 .. 15:00 (242 points for an index, 241 for a fund).

| code | what |
|---|---|
| `SH000001`, `SZ399001`, `SZ399006` | Shanghai Composite, Shenzhen Component, ChiNext index |
| `SH000852`, `SH000300` | CSI 1000 (small caps), CSI 300 (large caps) |
| `510300`, `510050`, `510500`, `512100`, `159915`, `588000` | index funds: CSI 300, SSE 50, CSI 500, CSI 1000, ChiNext, STAR 50 |

Measured on 2026-10-13 (`docs/work/probes/2026-10-13-index-minute-third-field.py`, FACTS F020):

- **For an index, `third` is not an average traded price. It is the equal-weighted mean of that exchange's stocks,
  written as an index level.** `third / preclose - 1` at the close against the mean return of the exchange's stocks
  in the daily panel: correlation 0.9994 and a mean absolute gap of 0.026 points for `SH000001` (749 sessions),
  0.9915 and 0.033 points for `SZ399001`. The weighted index itself correlates 0.86 and 0.79 with that mean.
  This gives three years of a minute-level "average stock" line; the project had breadth for three sessions only.
- `SZ399006`'s third value is 0.29 points from the mean of all Shenzhen stocks: it presumably follows ChiNext
  stocks. Not verified. Do not use it as a market-wide line.
- `open` is null in these files. `high` and `low` are not the index's own in most sessions (in 480 of 749 the stored
  low is nearer the third value's low than the price's): take highs and lows from `trend`.
- Index `volume` is in lots; `total_turnover / sum(volume)` is about 1,100 to 1,500 CNY per lot.
- `flag` (0, 1, 2) is not understood. Do not use it.
- For a fund, `third` looked like the running average traded price in a six-day probe. Verify on the full files
  (it must stay inside the range of prices seen so far that day) and verify the volume unit against `total_turnover`
  before using either.
- `SZ399001` lacks seven sessions (2023-12-28 .. 2024-01-08) lost to a service restart during the fetch; they are
  fetched again when the fetch resumes. The series for 2026-10-13 was not yet served by the history interface at
  17:30 on that day. Report which sessions each file holds; do not fill a missing session.

### Daily data: `/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/`

- `daily_panel.parquet`: one row per stock and session, 2023-08-15 .. 2026-10-12. Columns and known defects are
  in `/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/README.md` (read its Data section).
- `cycle_series.json`: 762 sessions; `limit_up_prev` (yesterday's limit-up count), `premium` (their mean return
  today, percent), `open_gap`, `intraday`, `promotion` (share sealing again), `big_loss` (share closing 5% or more
  lower), `n`.

### Our own minute radar: `/Users/papa/codebase/n8n/state/research/vshape_20261013/radar/`

`radar_2026-10-09.json` (afternoon only), `radar_2026-10-12.json`, `radar_2026-10-13.full.json` (244 minutes):
advancers and decliners, counts beyond ±2%, cumulative turnover, main net amount. Three sessions: case material,
and the only check of the average-stock line against real breadth within a day. Not a sample.

## What is already known (build on it or contradict it with evidence)

From `docs/work/probes/2026-10-13-vday-history.out.txt` (daily data, each stock's own low) and
`2026-10-13-today-stock-level.out.txt`:

- The median stock's intraday low was at or under -2% on 193 of 762 sessions. 43 of them (22%) became V days
  (median rise from each stock's low to the close of +2% or more), 94 (49%) fell all day. Since 2026-07-01:
  07-09, 07-20, 07-21, 08-07, 08-21, 10-09, and 10-13.
- After a V day the median stock opens -0.19% lower (t -2.1) and closes -0.28% lower the next day: no continuation
  on average.
- "The weakest rebound most" holds within the day and is given back overnight; buying the close of a V day shows
  nothing in any group. On 2026-10-13 it was stronger than in history.
- 2026-10-13: the median stock bottomed at 09:45 and made higher lows, while the main net amount bottomed at 11:18.
  This divergence is what the minute data can now test.

## Definitions (fixed here; a change is reported as a deviation with its reason)

**Lines.** For an index, `cw(t) = price / preclose - 1` (weighted) and `ew(t) = third / preclose - 1` (average
stock). The market's average-stock line `avg(t)` is the mean of the Shanghai and Shenzhen `ew`, weighted by the
number of stocks each exchange has in the daily panel on the previous session. Validate it first: `avg` at the
close against the panel's mean return of all stocks (correlation, mean absolute gap), and within the three radar
sessions against the advancer share.

**Depth entry.** For a depth `D` in -1.5%, -2%, -3%: the first minute of a session at which `avg(t) <= D`. One
per session and depth. This is the base population: "the average stock has just fallen through `D`".

**Rebound confirmation.** After a depth entry, the first minute at which `avg(t)` is at least `r` above its
running low of the day (`r` in 0.5 and 0.8 points). A further one can occur only after a new running low. Each is
labelled afterwards as held (no new low of `avg` before the close) or failed; the label is an outcome, never an
input.

**Bars.** 5, 15, 30 and 60-minute bars from the minute series, with the usual session boundaries (60-minute bars
end at 10:30, 11:30, 14:00, 15:00). MACD (12, 26, 9) and RSI (14) on bar closes, continued across sessions, on
`SH000001`'s price and on `avg`. A bar's values exist from the minute the bar closes.

**Divergence on a bar size**, at minute `t`: over the closed bars, the lowest low of the last 6 bars is under the
lowest low of the 6 bars before them, while the indicator's minimum over the last 6 is above its minimum over the
6 before. Three indicators: DIF, the MACD histogram, RSI. A robustness variant with confirmed swing lows (two
bars on each side) is reported next to it, not instead of it.

**Average stock against the weighted index**, at `t`:
- `ew_holds`: the weighted line made a new low of the day within the last 15 minutes while `avg`'s low of those 15
  minutes is 0.3 points or more above its own running low; and the mirror case `cw_holds`;
- the gap `avg(t) - cw(t)` and its change over the last 30 minutes;
- the same two conditions for `SH000852` against `SH000300`, using their prices.

**Sentiment ice point**, from sessions before the event's session only. Each is true when the value is in the
named tail of its own trailing 250 sessions: `limit_up_prev` in the lowest fifth; the limit-down count of the
previous session in the highest fifth; `premium` under zero; `promotion` in the lowest fifth; `big_loss` in the
highest fifth; the share of rising stocks under 30% on each of the last two sessions; `avg` down 5% or more over
the last five sessions; three or more falling sessions in a row.

**Index-fund volume.** For a fund and minute, traded value (volume times price) over the mean of the same minute
across the previous 20 sessions. Report for each fund and for the six together: the ratio over the last 5 minutes
and from 09:30 to `t`; "heavy" at 2 and at 3; and whether the first heavy 5-minute window came before or after
the running low. The data cannot say who bought. Write "宽基 ETF 放量", never "国家队".

**Volume on the rebound leg.** `SH000001` plus `SZ399001` volume per minute from the running low to `t`, over the
same number of minutes before the low, each divided by its mean at those minutes across the previous 20 sessions.

## Outcomes

From an event at minute `t`, entered at the value of minute `t + 1`:

- the average stock: to the close; to the next session's 09:30 point; to the next close;
- funds `510300`, `512100`, `159915`, `510500`: to the close (a mark only: a fund bought today cannot be sold
  today), to the next open and the next close (what can be realised);
- the path: a new low before the close, the rise to the day's later high, the fall from that high to the close,
  and that fall against the next open.

Every conditional figure is shown beside its base: all depth-entry events of the same depth, and all rebound
confirmations of the same `r` for the held-or-failed question.

## Protocol

- Nothing from after minute `t` enters a condition: bars are closed by `t`, daily state ends at the previous close,
  20-session means end at the previous session.
- Checks first, reported before any result: sessions and minutes per file; the fund checks named under Data; the
  validation of `avg`; the count of depth-entry sessions against the 193 of the daily definition, with the
  reason for the difference (each stock's own low against one common minute).
- Two periods: 2023-09 .. 2025-12 to choose a variant where a family has several; 2026 to check it. Sessions since
  2026-07-01 are also listed one by one: date, each event's minute, which conditions were true, every outcome.
- Tests use one event per session (the first); all events are described. Report n, mean, median, share above
  zero, the difference from the base and its t value; q-values (Benjamini-Hochberg) over the family of single
  conditions and over the family of combinations. Under 20 sessions: "样本不足", no claim.
- Combinations: one variant per family, chosen on the first period only; all pairs and triples of the five
  families, three-valued (true, false, unknown; unknown is never counted as false).
- Registration is lenient, as the user asked. End with three lists: holds in both periods; holds in the first
  only; not supported. All three are kept: each condition is stated so that it can be computed at a minute of a
  live session, with the fields it needs.
- Read local files only. No network, no request to any host, no write outside the worktree and the system temp
  directory. The data files stay where they are and are never copied into the repository.

## Deliverables (tracked and committed on the research branch)

- `docs/archive/2026-10-13-v-reversal-study.md`: the report, in Chinese. Plain words; numbers with their n and
  base; what was not tested and why; the session-by-session table since 2026-07-01.
- `docs/archive/2026-10-13-v-reversal-study/`: every script with its saved output, and the event tables as CSV.

## Dispatches (one research worktree, disposition `harvest`; one thread, continued)

| # | Content |
|---|---|
| 1 | The checks; `avg`; bars and indicators; depth-entry and rebound-confirmation event tables with outcomes; the description of deep-low sessions (time of the low, share turning, by year, since 2026-07-01) |
| 2 | The five families one by one, each against its base |
| 3 | Combinations; the two periods; the session-by-session table since 2026-07-01 |
| 4 | The report and the three lists |

Dispatch 1 needs the five index files. The fund files are needed from dispatch 2 (fund volume) and for the fund
outcomes; until they are complete those columns are left out and said to be.
