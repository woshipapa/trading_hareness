# Temperature method review

Research-only investigation, phases 1 and 2. No threshold, weight, band, or
execution path was changed. All code observations below are from the worktree
at the phase-1 commit; phase 1 used no network requests.

## As built

### Construction

The daily aggregate is produced by `DAILY_SQL` in
[`market_temperature_repository.py`](../../quant-service/app/market_temperature_repository.py:40).
It reads `quant.canonical_bars_daily`, joins the point-in-time `all_a`
membership history, excludes `is_suspended`, requires positive `pre_close` and
`close`, and scales an amount that looks like CNY rather than thousand CNY by
1,000 when `amount/(volume*close/10) > 50` (`:51-63`). Missing bar limits are
filled from the newest `quant.daily_trade_limits` row for that symbol/date
(`:45-61`), with a 0.005 price tolerance (`:31`, `:66-69`). A session is
`limits_ok` only when at least 90% of its rows have a positive limit-up price
(`:33`, `:124-131`). The SQL's `stocks` denominator is therefore the PIT all-A
bar rows that survive the filters; it does not separately remove ST/*ST,
newly listed no-limit names, or a particular exchange/board.

The source can therefore include ST/*ST, 10/20/30% boards, Beijing names, and
one-price boards when they are present in `all_a` and have a valid limit price.
Names with no limit price remain in `stocks` but are not in `with_limits`; if
more than 10% of rows lack limits, all limit-based components are withheld by
`components_of` rather than changed to zero (`market_temperature.py:87-106`).
Suspended names are excluded in the daily SQL. A row that touched the limit but
closed below it is included in `touched` but not `sealed`, hence contributes to
the broken-board complement. The SQL does not distinguish natural versus
one-price boards.

The eight raw components are:

| Component | Formula and direction | Evidence |
| --- | --- | --- |
| `limit_up` | Count of rows with `close >= limit_up - 0.005`; warmer when larger. | SQL `sealed` and `limit_up` count, `market_temperature_repository.py:64-89`; direction `market_temperature.py:46-55`. |
| `limit_down` | Count of rows with `close <= limit_down + 0.005`; score is inverted, so more is colder. | SQL `floored` and count, `market_temperature_repository.py:64-92`; inversion `market_temperature.py:137-143`. |
| `seal_rate` | `limit_up / touched`, where `touched` is `high >= limit_up - 0.005`; no touched rows means missing. This is `1 - broken_rate` only when both use the same attempted set. | `market_temperature.py:93-105`; SQL flags `:66-68`. |
| `up_ratio` | `advancers / (advancers + decliners)`; unchanged names are excluded from the denominator. | `market_temperature.py:94-103`; counts are SQL `:90-92`. |
| `premium` | Mean `close / pre_close - 1`, in percent, for rows whose previous session was sealed (`prev_sealed`). It is close-to-close, not open-to-close. | SQL `avg(ret) FILTER (WHERE prev_sealed) * 100`, `market_temperature_repository.py:71-80,94-97`; projection `market_temperature.py:103`. |
| `promotion` | `promoted / prev_sealed`, where `promoted` means `prev_sealed AND sealed` today. Missing when the previous set is empty or limits are not usable. | `market_temperature.py:103-105`; SQL counts `market_temperature_repository.py:94-96`. |
| `max_streak` | Maximum current consecutive sealed-session run per symbol. A run is reset by a non-sealed row or a gap in trading dates; SQL warmup is 40 calendar days. | Streak CTE `market_temperature_repository.py:71-81`; projection `market_temperature.py:105`. |
| `turnover_ratio` | Today's positive `turnover_cny` divided by the arithmetic mean of the previous 20 positive-turnover sessions. Fewer than 20 prior values or a non-positive base means missing. | `market_temperature.py:110-122`, with `TURNOVER_BASE=20` at `:60`. |

`with_turnover_ratio` processes rows in date order. It appends today's value
before scoring (`market_temperature.py:149-160`), and `score` ranks against
the supplied history with a mid-rank for ties:
`100 * (count(below) + 0.5*count(equal)) / len(history)`
(`market_temperature.py:125-130`). Each component needs at least 60 history
values (`:58-59`, `:137-143`); the daily value is appended to history before
the rank is calculated (`:154-160`), so **today is inside its own daily
percentile window**. The last 250 values are retained by
`temperature_series` (`:159`), but the initial reading can have fewer than 250
values. A component is omitted rather than zero-filled; the temperature is the
rounded arithmetic mean of available scores and is withheld unless at least
five of eight score (`:144-146`). Component weights are equal because the mean
has no other weights. Bands are `<=20` ice, `<40` cold, `<60` neutral, `<80`
hot, and `>=80` boiling (`market_temperature.py:64-76`). The 20/80 bands and
the five-component minimum are constants in that module; the decision records
the rationale and marker thresholds, but does not provide a fitted weight or
band-estimation procedure (`docs/decisions/0013-sentiment-temperature-and-flows.md:43-60`).

The daily runtime scans about 600 calendar days and stores the latest 30
scored readings (`market_temperature_runtime.py:37-44,62-80`). Missing limits
or too few history values leave a reading blocked, not fabricated
(`:73-81`). The related marker is research-only: temperature `<=40` and ETF
ratio `>=1.5`, optionally labelled counter-trend when the index fell
(`market_temperature_runtime.py:85-91`).

### Intraday calculation and ranking

Every five-minute sample aggregates the all-A minute cross-section as if it
were the close (`market_temperature_intraday.py:1-21`). Rows without a symbol,
price, previous price, or positive volume are skipped (`:100-112`), which also
removes suspended/no-trade rows. The live limit mapping comes from the day's
`daily_trade_limits` (`:113-120`); `touched` uses the running intraday high,
and the count, broken complement, prior-board premium, promotion and current
streak are all recomputed from the cross-section (`:113-129`). No component is
carried from the previous close. Turnover is cumulative-to-now and divided by
the median historical same-time share of daily turnover, then by the previous
20-session mean (`:133-141,193-202`). Until five stored sessions provide that
profile, turnover is missing (`:73`, `:133-141`).

The ranking history is explicitly end-of-day history. `stored_history` reads
stored daily readings strictly through `day - 1` and takes the last 250 daily
component values (`:148-157`); before 250 stored readings exist it falls back
to the same pre-day daily-bar aggregates (`:160-164,167-176`). Thus the
intraday value **is ranked against end-of-day history**, not a same-time
intraday distribution. With a running count of limit-ups/touched boards, the
morning value is consequently structurally low relative to a closing-history
distribution. The current owner has about 120 daily readings, so the fallback
history can be shorter than 250 (but still must meet the 60-value component
minimum in `score`).

The 15:00 sample is actually the latest captured document from 14:55 onward,
because capture stops before the closing auction (`market_temperature_intraday.py:28-33,68-70,185-190`). Daily SQL uses the full daily bar; therefore the apparent close agreement is a convention, not identity. Stored sessions require 40 of 49 samples and a close sample, but a close temperature can still be blocked when limits or history are missing (`:35-38,237-264`).

### Duplicate sentiment definitions

`sentiment_cycle.py` and `sentiment_cycle_daily.py` materialize a second,
overlapping quantity (`MODEL_VERSION="sentiment-cycle-v1"`,
`sentiment_cycle_daily.py:23-36,76-104`). It defines sealed and broken names
with the same 0.005 tolerance (`sentiment_cycle.py:86-109`), and its broken rate
is `broken/(sealed+broken)` (`:163-182`). It calculates promotion from the
previous sealed set (`:168-175`) and a 15-session ladder height with
`high_board_count >= 3` (`:30-34,111-130,177-186`). Its premium is mean
open-to-close for yesterday's sealed names (`:133-148`), deliberately avoiding
an unbuyable prior close.

The two modules therefore do not define one quantity in one way:

* `seal_rate` versus `broken_rate` has the same algebra only on a common
  attempted set, but market temperature uses all-A canonical rows plus limit
  fallback, while sentiment-cycle requires `volume > 0`, `limit_up IS NOT NULL`,
  a non-null listing date, fresh quality, and point-in-time availability
  (`sentiment_cycle_daily.py:29-36`). Earlier ladder sessions are filtered to
  names that touched the limit; the final session is loaded whole for premium
  (`:39-73`).
* Market temperature's premium is close-to-close; sentiment-cycle's is
  open-to-close. They can have opposite practical interpretations on locked
  boards.
* Market temperature's SQL streak is limited by a 40-calendar-day warmup;
  sentiment-cycle truncates to 15 sessions. The resulting `max_streak` values
  can diverge even on the same bars.
* Sentiment-cycle exposes a ladder distribution and stage labels; market
  temperature retains only the maximum height and a percentile score.

### Phase-1 defects

1. **Point-in-time percentile defect (high).** `temperature_series` appends the
   current component before scoring (`market_temperature.py:154-160`), contrary
   to the module's “preceding sessions” claim (`:14-22`) and to decision 0013's
   point-in-time wording. Intraday excludes the current day, so daily and
   intraday temperatures are on different rank definitions.
2. **Intraday comparability defect (high).** Cumulative morning counts and
   touched-board counts are ranked against close-of-day history
   (`market_temperature_intraday.py:148-157,193-205`). This creates the exact
   low-morning bias the brief asks about; no time-of-day normalization exists
   for those count components.
3. **Close mismatch (medium).** The intraday “close” is a 14:55-or-later
   minute document, while the daily aggregate includes the full daily bar and
   auction (`market_temperature_intraday.py:28-33,185-190`; repository SQL
   `market_temperature_repository.py:84-103`). The claim that 15:00 is the
   daily reading is therefore not guaranteed.
4. **Duplicate, conflicting premium definitions (medium).** The temperature
   uses close-to-close (`market_temperature_repository.py:94-97`), while the
   parallel sentiment-cycle reading uses open-to-close
   (`sentiment_cycle.py:133-148`). Both are labelled yesterday's limit-up
   premium, so consumers can compare unlike numbers.
5. **Duplicate, conflicting board universes and lookbacks (medium).** The
   all-A temperature SQL and the fresh/list-date/volume-filtered sentiment-cycle
   loader use different populations (`market_temperature_repository.py:56-63`;
   `sentiment_cycle_daily.py:29-73`), and the streak windows are 40 calendar
   days versus 15 sessions. A displayed “highest board” is not reproducibly one
   quantity across modules.
6. **Missing denominator policy is implicit (medium).** `up_ratio` excludes
   unchanged names and `limit_up`/`seal_rate` use only rows with positive
   limits, while `limits_ok` uses all surviving rows. This is defensible but not
   declared as a stable universe contract in the temperature output; changes in
   no-limit IPO/ST/board mix can move missingness and the denominator together.

### Phase-1 design questions

* Should the daily rank be strictly prior-session (matching the stated PIT
  rule and intraday), or intentionally self-inclusive? The choice changes
  historical values and must be versioned.
* Should intraday counts be compared with close history (simple but biased), a
  same-minute history, or a model that estimates a completed-session count?
* Is the research “premium” intended to be an executable open-to-close effect or
  a descriptive close-to-close effect? Pick one and use it in both modules.
* Is `all_a` the intended universe for every board, including ST/*ST, IPO
  no-limit rows, 20%/30% boards and Beijing, or should separate pools be
  reported? The current 90% gate can withhold all board components on a mixed
  day.
* Should one-price boards, re-seals, first boards and per-height promotion be
  explicit outputs rather than hidden in aggregate counts?
* Should the 40-calendar-day streak warmup be replaced by a session-count
  window, and should suspended days be a break or an unknown?
* Are equal weights acceptable when `limit_up`, `seal_rate`, `promotion` and
  `max_streak` all respond to the same board population? This cannot be answered
  from code alone; it needs the stored-history redundancy test in phase 3.

## Reference set

Mapping classes are **in temperature**, **elsewhere**, **catalog-computable**,
and **unavailable**. A catalog capability is called PIT only when its effective
date and availability clock support replay; a later backfill or a live snapshot
does not establish PIT history.

| Indicator | Usual definition and weakness | Mapping |
| --- | --- | --- |
| Limit-up/down counts | Counts closing at each applicable limit. Counts shift with ST/board rules, IPO no-limit periods and universe coverage. | **In temperature**: `limit_up`, `limit_down`; no natural/one-price split. |
| Natural vs one-price boards | Separate names that traded away from the limit from names locked from the open; one-price boards are less tradable. | **Elsewhere**: `limit_up_continuation.py:61-95` (`one_word`) and `limit_detail_read_model.py:69-84`. |
| First boards, ladder 2/3/4/5+, highest board | Height distribution and gaps, not just the maximum. Weakness: vendor labels and small denominators. | **Elsewhere**: `datasources/derived/market_sentiment.py:67-99`, `short_term_review.py:129-149`; **catalog-computable** `limits.ladder`, Fuyao `live_verified`, `market_events:limit_chain`, depth not stated (`catalog.py:569`). |
| Promotion by height | For each prior height, the share sealing again (1->2, 2->3, 3->4, 4+). Weakness: noisy small bases. | **In temperature** only pooled; **elsewhere** per-layer `market_sentiment.py:87-99`; same `limits.ladder` capability. |
| Broken-board rate | Broken/(sealed+broken) or broken/touched; denominator and re-seals must be fixed. | **In temperature** as complement of `seal_rate`; **elsewhere** `sentiment_cycle.py:163-182` and `market_sentiment.py:223-240`; **catalog-computable** `limits.broken_pool`/`limits.seal_detail` (Fuyao live-verified; Longhu/Xuangubao declared). |
| Broken-board next-day return | Mean/median next-day return of broken names, with tradability and survivorship stated. | **Catalog-computable** `limits.previous_limit_up`, Eastmoney declared with recent retained archive; exact history/PIT depth not established (`catalog.py:199,578-580`). |
| Seal time, re-seal, sealed amount | First/last seal, opens/re-seals and queue amount; provider timestamps and queue semantics vary. | **Catalog-computable** `limits.seal_detail`, Longhu/Xuangubao declared with recent close archive, not established as 250-day PIT (`catalog.py:197-199,570-577`). |
| Limit-up/down ratio | Up count divided by down count (or log ratio); unstable near zero. | **Unavailable as a named component**; raw counts exist in temperature. |
| 天地板 / 地天板 | Same-session up-limit-to-down-limit or reverse path; rare and minute-data intensive. | **Unavailable** as a market-wide historical capability; TDX minute bindings are unsupported and other minute bars are watchlist-oriented (`catalog.py:158-160,433-449`). |
| Yesterday's limit-up premium | Close-to-close, open-to-close, median, red rate, and multi-board subsets are all used; locked opens make a mean non-executable. | **In temperature** close-to-close; **elsewhere** `market_sentiment.py:101-115` and `sentiment_cycle.py:133-148`. |
| Consecutive-board / broken-board premium | Split next-day premium by prior height and broken/held status; subsets are correlated and small. | **Catalog-computable** from `limits.previous_limit_up` plus `limits.seal_detail`; history/PIT depth not established. |
| Large-loss share and leader drawdown | Share of prior limit-ups opening/closing below -5%, plus the leader's drawdown; cutoffs and leader selection are conventions. | **Elsewhere** `short_term_review.py:300-364`; bars plus prior-limit capability can compute it, but no dedicated PIT feature is stored. |
| Advance/decline and median return | Counts or median daily returns; median reduces cap-weight effects but depends on unchanged/invalid-row policy. | **In temperature** advance ratio, unchanged excluded; **elsewhere** `market_sentiment.py:118-146`; canonical `bars.daily` is live-verified (`catalog.py:155-156,397-419`). |
| Share above moving average | Fraction above MA5/20/200; lookback and corporate-action basis matter. | **Elsewhere** MA5 breadth in `xiaojie_indicators.py:123-177`; daily bars and adjustment factors are catalog-computable, but no stored PIT market-wide series was established. |
| New highs/lows | Trailing-window counts; sensitive to listing age, delistings and corporate actions. | **Unavailable** as a named retained PIT feature. |
| Turnover history and concentration | Total turnover versus own history; concentration is top-name/sector share. Both can reflect rebalancing rather than sentiment. | **In temperature** 20-session all-A ratio; concentration **unavailable** as a named feature. |
| Broad ETF volume ratio | Basket turnover / previous 20-session mean; ETF creation, rebalancing and substitutions are confounds. | **Elsewhere** `broad_etf_flow.py:117-137` and marker in `market_temperature_runtime.py:85-91`; 12-code basket, >=9 common ETFs, research-only. |
| Margin balance/net financing | T-day balances and flows, normally available T+1; leverage can mean participation or forced deleveraging. | **Catalog-computable** `fundamentals.margin`, Eastmoney declared, effective T/available T+1, raw `margin_market`; depth not stated (`catalog.py:251,744-745`). |
| Hot-rank attention | Rank/heat and change, ideally archived daily; platform users and revisions are biases. | **Catalog-computable** THS current/history declared and Eastmoney history declared about one year; backfilled history is not PIT (`catalog.py:224-227,662-672`). |
| Analyst/news tone | Text polarity/volume/surprise; publication-time leakage, duplicated wires and model drift are weaknesses. | **Catalog-computable** `news.flash` declared sources and CNINFO announcements live-verified, with publication/availability clocks; no validated tone model stored (`catalog.py:232-243,674-680`). |
| Equal-weight percentile average | Transparent rank transform and mean; correlated components are double-counted and missingness changes weights. | **In temperature** (`market_temperature.py:125-146`). |
| Z-score sum | Standardized sum; outlier and regime sensitive. | **Elsewhere** weighted z-scores in `market_sentiment.py:38-45,156-204`; not temperature. |
| PCA / Baker-Wurgler-style index | First principal component of several sentiment proxies; loadings are sample/regime dependent and less interpretable, while classic work is lower frequency. | **Unavailable** as a stored temperature composite; component capabilities remain separate. |
| Cycle labels | Icepoint/repair/ferment/climax/ebbing threshold labels; thresholds are conventions and labels can overlap. | **Elsewhere** `sentiment_cycle.py:37-83,192-197`, persisted by `sentiment_cycle_daily.py:76-104`; `derived.sentiment_cycle` is catalog `live_verified` (`catalog.py:270,795-796`). |

The TDX-derived pools are an extension, not a 250-day source. The catalog says
`derived_tdx_limits` compares TDX `quote.all_a_snapshot` with MAC
`limits.prices`, supplies only a current-session snapshot, and lacks seal time,
reason, board count, seal amount and history (`catalog.py:283-293,557-567`).
The owner-egress check in the brief therefore establishes a path for future
archiving, not a usable historical component. A complete 250-session window
would begin only after 250 trading sessions of archival; the calendar date is
not established here.

### Sources cited

Web sources used to distinguish research from practitioner convention:

* Ackert, Huang & Jiang, “Investor sentiment and price limit rules,” paper:
  https://doi.org/10.1016/j.jbef.2015.01.001
* Baker & Wurgler, “Investor Sentiment in the Stock Market,” NBER working
  paper: https://doi.org/10.3386/w13189
* Baker & Wurgler, “Investor Sentiment and the Cross-Section of Stock Returns,”
  paper: https://doi.org/10.1111/j.1540-6261.2006.00885.x
* Economic Modelling paper on Chinese price-limit/sentiment evidence:
  https://doi.org/10.1016/j.econmod.2020.04.006
* Physica A paper on Chinese price-limit/market behavior evidence:
  https://doi.org/10.1016/j.physa.2016.11.114
* Shanghai Stock Exchange trading-rule portal, exchange rule source:
  https://www.sse.com.cn/lawandrules/sselawsrules/trade/
* Shenzhen Stock Exchange trading-rule portal, exchange rule source:
  https://www.szse.cn/lawrules/rule/stock/trade/

Repository sources for the mapping are
[`docs/decisions/0013-sentiment-temperature-and-flows.md`](../../docs/decisions/0013-sentiment-temperature-and-flows.md:18),
[`docs/DATA_SOURCE_LAYER.md`](../../docs/DATA_SOURCE_LAYER.md:1),
and [`quant-service/app/datasources/catalog.py`](../../quant-service/app/datasources/catalog.py:1).

## Evidence from stored readings

### Retrieval and coverage

The daily GET was made once at Beijing time 2026-10-12 13:30:02:
`http://127.0.0.1:15682/api/v1/market/temperature/daily?days=250`. It returned
`n=250` readings from 2025-09-19 through 2026-10-09. The ten intraday GETs were
the ten most recent dates in that answer; their URLs, observed Beijing times,
sample counts, and responses are in
[`fetch-log.txt`](2026-10-12-temperature-method-review-data/fetch-log.txt)
and the adjacent `intraday-*.json` files. The saved daily response is
[`daily.json`](2026-10-12-temperature-method-review-data/daily.json).

Of the `n=250` daily readings, `n=248` have all eight raw component values.
The two incomplete dates are 2026-08-28 and 2026-09-01; both lack `premium` and
`promotion`. The daily temperature itself is present for all 250 rows because
the five-component minimum still holds. Intraday sample counts were 45, 44,
47, 48, 48, 48, 47, 0, 33 and 32 for the ten dates. 2026-09-30 returned a
valid empty live series (`n=0` samples), not a retry or fabricated value.

### Redundancy

Spearman correlations use pairwise-complete stored scores, average ranks for
ties, and report pairwise `n`. The largest positive pairs were:

| Pair | rho | n |
| --- | ---: | ---: |
| `limit_up` / `up_ratio` | 0.6376 | 250 |
| `premium` / `promotion` | 0.6238 | 248 |
| `limit_up` / `premium` | 0.5923 | 248 |
| `limit_up` / `seal_rate` | 0.5192 | 250 |

The first two pairs are the clearest candidates for double-counting in an
equal-weight mean, but correlation is descriptive and does not prove duplicate
information. PCA used the `n=248` listwise-complete rows, standardized each
score column, and diagonalized the correlation matrix. The first two
eigenvalues were 3.301372 and 1.322270, carrying `57.7955%` of variance
(`n=248`). The complete correlation matrix and pairwise sample sizes are in
[`stats.json`](2026-10-12-temperature-method-review-data/stats.json).

### Distribution and forward changes

Among `n=250` temperatures, 21 were at or below 20 (`8.4%`), 221 were between
20 and 80 (`88.4%`), and 8 were at or above 80 (`3.2%`). Band counts were ice
21, cold 71, neutral 95, hot 55, boiling 8. Treating adjacent returned trading
rows as consecutive, the ice spells were `n=19` spells, mean 1.1053 sessions,
maximum 2; boiling spells were `n=8`, mean 1.0000, maximum 1. These are spell
descriptions, not significance tests.

Forward returns are SSE index close-to-close percentage changes at the next 1,
3 and 5 returned trading rows. Quintiles are sorted by the current temperature
with approximately equal counts; horizon overlap is substantial. Values are
means with observation counts, and are descriptive only.

| Temperature quintile (low to high) | 1d | 3d | 5d |
| --- | ---: | ---: | ---: |
| Q1 | +0.0863% (n=50) | +0.5439% (n=50) | +0.5090% (n=49) |
| Q2 | -0.1407% (n=50) | -0.3885% (n=49) | -0.5862% (n=49) |
| Q3 | -0.1144% (n=50) | -0.1341% (n=49) | +0.1476% (n=49) |
| Q4 | +0.2052% (n=50) | +0.0093% (n=50) | +0.3457% (n=49) |
| Q5 | -0.0196% (n=49) | +0.0084% (n=49) | -0.3626% (n=49) |

By band, the corresponding 1d / 3d / 5d means were: ice `+0.4093%`
(`n=21`) / `+0.5890%` (`n=21`) / `+0.7350%` (`n=20`); cold `-0.1606%`
(`n=71`) / `-0.1338%` (`n=70`) / `-0.2248%` (`n=70`); neutral `-0.0120%`
(`n=95`) / `-0.1594%` (`n=94`) / `+0.0425%` (`n=93`); hot `+0.1360%`
(`n=54`) / `+0.2471%` (`n=54`) / `-0.0104%` (`n=54`); boiling `-0.3165%`
(`n=8`) / `+0.1383%` (`n=8`) / `+0.0333%` (`n=8`). The small boiling sample and
overlapping horizons do not support an inference.

The two component scores with the largest mean absolute distance from the
composite were `turnover_ratio` (21.8661 points, `n=248`) and `max_streak`
(21.5496 points, `n=248`). Their forward means by component-score quintile
(Q1 low to Q5 high) were:

| Component | 1d Q1..Q5 | 3d Q1..Q5 | 5d Q1..Q5 |
| --- | --- | --- | --- |
| `turnover_ratio` | +0.0828 (50), -0.0477 (50), -0.0854 (50), +0.1469 (49), -0.0764 (50) | +0.4761 (49), -0.1610 (50), -0.5043 (49), +0.0471 (49), +0.1918 (50) | +0.8277 (48), -0.0984 (50), -0.6287 (48), -0.0912 (49), +0.0492 (50) |
| `max_streak` | -0.0306 (50), +0.0583 (50), +0.0124 (50), +0.0209 (50), -0.0445 (49) | -0.0501 (50), -0.0649 (50), +0.1832 (50), -0.0391 (49), +0.0204 (48) | +0.0485 (50), -0.1432 (50), +0.0145 (49), -0.0378 (48), +0.1763 (48) |

The same component-by-band breakdown is retained in `stats.json`; it is not
interpreted as a test result.

### Self-inclusive ranking check

The script recomputed each component's percentile from prior values only (same
tie rule, direction and at least 60 prior observations), then averaged the
available components. Because the saved response starts at 2025-09-19, this
comparison is limited to rows with at least 60 prior rows in the response:
`n=190`. Relative to the stored self-inclusive temperatures, the prior-only
temperature changed by mean absolute `1.1495` points (signed mean `+1.0642`
points). The band changed on `11/190` days (`5.79%`). This is a reproducible
comparison on the returned window; it does not reconstruct the owner's older
than-250 history for the first 60 rows.

### Intraday gap to the daily close

Each cell is intraday temperature minus that date's daily temperature. A blank
means the requested sample time was absent; the mean/range uses only the stated
non-missing `n`.

| Trade date | Daily | 09:35 | 10:30 | 11:30 | 14:00 | Last sample |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-09-18 | 65.9 | -10.1 | -14.8 | — | -8.8 | -2.6 |
| 2026-09-21 | 78.4 | — | -14.9 | — | -5.1 | +0.2 |
| 2026-09-22 | 57.7 | -9.5 | -0.6 | — | -6.5 | +0.5 |
| 2026-09-23 | 26.8 | +7.2 | -1.6 | — | -1.3 | +1.3 |
| 2026-09-24 | 36.0 | +5.9 | +6.8 | — | -4.5 | +0.5 |
| 2026-09-28 | 16.1 | +4.2 | -1.9 | — | -2.1 | -0.9 |
| 2026-09-29 | 58.0 | -6.7 | -3.9 | — | +0.2 | +0.8 |
| 2026-09-30 | 49.0 | — | — | — | — | — |
| 2026-10-08 | 33.2 | — | — | — | -6.0 | -2.5 |
| 2026-10-09 | 72.6 | — | -37.9 | — | -21.1 | -1.4 |

The means and ranges were: 09:35 `-1.5000` points, range `[-10.1,+7.2]`,
`n=6`; 10:30 `-8.6000`, range `[-37.9,+6.8]`, `n=8`; 11:30 had no returned
sample on any date, `n=0`; 14:00 `-6.1333`, range `[-21.1,+0.2]`, `n=9`; and
the last available sample `-0.4556`, range `[-2.6,+1.3]`, `n=9`. The missing
11:30 column and the incomplete 09:35/10:30 coverage prevent a strong
time-of-day claim, but the observed 10:30 and 14:00 gaps are below the daily
close in this small sample.

The exact calculations are reproducible with
[`compute_stats.py`](2026-10-12-temperature-method-review-data/compute_stats.py)
and the tracked response files beside it.

## Assessment

The construction is a useful descriptive dashboard, but it is not yet a
methodologically stable single temperature.

* **Definitions:** The eight formulas are explicit and fail closed for missing
  limits, but premium and board definitions diverge from `sentiment_cycle` (see
  `market_temperature_repository.py:94-97` versus `sentiment_cycle.py:133-148`).
  Phase 3 found two incomplete component rows in `n=250`, so the partial-data
  policy is active in the retained series.
* **Universe:** The daily SQL's PIT all-A membership and suspension exclusion
  are appropriate foundations (`market_temperature_repository.py:56-63`). It
  still mixes no-limit names into the coverage denominator and does not expose
  ST/board/one-price strata. That makes a nominal count change hard to interpret.
* **Percentile window:** The intended 250-session, 60-session minimum and
  mid-rank ties are transparent (`market_temperature.py:125-146`), but daily
  scoring includes the current value (`:154-160`). In the stored evidence,
  removing self-inclusion changed the composite by mean absolute 1.1495 points
  on `n=190` and changed 11 bands. The defect is material near 20/40/60/80,
  even though it does not move most observations.
* **Weights and redundancy:** Equal weights are explainable, but the stored
  score correlations include `limit_up/up_ratio rho=0.6376 (n=250)` and
  `premium/promotion rho=0.6238 (n=248)`. Standardized PCA's first two
  components carry 57.7955% of variance (`n=248`). This is enough to test
  double-counting, not enough to justify replacing the transparent composite by
  a fitted model.
* **Missing data:** The component-level omission and five-component minimum
  avoid false zeros (`market_temperature.py:137-146`). They also change the
  effective weights by day. The report should expose component coverage and the
  exact denominator with every reading before treating a partial composite as
  comparable.
* **Intraday comparability:** The code recomputes all eight fields from the
  live cross-section and ranks them against daily closes
  (`market_temperature_intraday.py:193-205`). Phase 3's ten-day sample had no
  11:30 observations (`n=0`), 10:30 mean gap -8.6000 points (`n=8`), 14:00
  mean gap -6.1333 (`n=9`), and last-sample mean gap -0.4556 (`n=9`). This is
  evidence of comparability risk, not a significance claim; missing timestamps
  and overlapping sessions make it exploratory.
* **Limit-board completeness:** `max_streak`, pooled promotion and seal rate
  capture a real board dimension, but the temperature does not expose the
  ladder, first/natural versus one-price boards, re-seals, seal time, sealed
  amount, or broken-board next-day loss. The separate `market_sentiment` and
  `sentiment_cycle` modules already compute several of these, so the immediate
  risk is inconsistent duplication rather than absence of every raw signal.

## Proposal

The ordering is a research-maintenance order. Every test below is to be
registered before rerunning it; “reject” means reject the proposed change as
the default research definition, not accept a trading rule. All outputs remain
`research_only`/`live_effect=none`.

### (a) Corrections to what exists

1. **Use prior-only daily percentiles.** Define each daily score from the prior
   250 available component values, retaining the existing 60-value minimum,
   tie rule, direction and equal mean. Change `market_temperature.py:154-160`
   and version the stored reading; intraday already follows the prior-day
   convention in `market_temperature_intraday.py:148-157`. The correction needs
   no new data. Test on the saved `n=250` response and a forward rerun: compare
   mean absolute temperature difference, band changes, and exact replay
   invariance. Reject the code change if prior-only replay is not deterministic
   or if the implementation still includes the current row. Cost of error:
   percentile leakage and a daily/intraday scale mismatch.

2. **Make the close contract explicit.** Keep the daily bar as the authoritative
   15:00 reading; label the intraday last sample as a 14:55-or-later proxy and
   return its observation time separately. Change the close wording and read
   model around `market_temperature_intraday.py:185-205`, not the stored daily
   bar. No new history is needed. Pre-register a 20-session comparison once
   available: the proxy's mean absolute gap to the daily reading must be no
   more than 2 temperature points and its signed mean must straddle zero; reject
   the proxy label if either condition fails. The present sample is only `n=9`
   for last values, so this is not yet judged. Cost of error: users read a
   pre-auction value as a settled close.

3. **Choose one premium and one board universe.** Use the executable
   open-to-close premium already documented in `sentiment_cycle.py:133-148`,
   and define the board universe from the same PIT canonical all-A bars,
   excluding suspended/no-trade rows while leaving no-limit names outside
   limit-specific numerators. Update the daily SQL premium at
   `market_temperature_repository.py:94-97`, the intraday aggregate to use
   open-to-now for the same prior-limit set, and `_BARS`/filters in
   `sentiment_cycle_daily.py:29-73`. Canonical bars and their PIT availability
   already exist. On at least the returned `n=250` common sessions, compare
   open-to-close and close-to-close against the same prior sealed sets; reject
   the migration if the chosen variant has more than 10% additional missing
   days or its paired next-session cross-sectional rank correlation is lower by
   more than 0.05. Cost of error: either an unexecutable “premium” or a
   historical break in the displayed series. The two current definitions must
   not continue under one label.

4. **Remove arbitrary streak truncation.** Define a streak as consecutive
   trading-session seals in the PIT canonical history, bounded only by the
   available 250-session research window; a suspension or missing bar is
   unknown and breaks the run, consistent with the current pure-function test.
   Change the streak CTE warmup at `market_temperature_repository.py:32,71-81`
   and use the same definition in `sentiment_cycle.py:111-130` rather than
   15-session versus 40-calendar-day limits. The existing bar history is
   sufficient. Test all common sessions with the stored ladder/limit detail;
   reject if any fixture violates consecutive-session semantics or if at least
   98% of overlapping vendor ladder rows do not agree. Cost of error: false
   cycle breaks or overstated height.

5. **Publish component coverage.** Add explicit per-component availability and
   denominator fields to the research reading, while retaining fail-closed
   `None` values and the five-component minimum. This is a schema/read-model
   correction in `market_temperature.py:137-146` and the daily response, not a
   new signal. Test that missing limits and missing prior boards never become
   zero, and that the two known incomplete dates remain visibly incomplete.
   Reject if a partial reading can no longer be distinguished from an all-eight
   reading. Cost of error: analysts compare changing effective weights as if
   they were identical composites.

### (b) Candidates evaluable on stored history now

6. **Natural/one-price split plus ladder shape.** Add natural-board count,
   one-price count, first-board count, height counts 2, 3, 4 and 5+, and
   promotion by height as research fields joined to, not silently substituted
   for, `max_streak`. Canonical OHLC bars support one-price derivation and the
   existing `market_sentiment.py:67-99` code supplies the ladder arithmetic;
   the catalog's vendor ladder is an additional validation source. This adds
   tradability and distribution shape that a single maximum does not carry;
   the redundancy table does not show these fields, so they should not be
   added merely as another correlated count. Pre-register a common-day study
   on at least `n=120` sessions: compare the extended ladder against the
   existing maximum using next-session median returns and rank correlation;
   retain it only if the ladder adds at least 0.05 absolute rank correlation
   with the outcome after conditioning on `max_streak`, otherwise reject it.
   Cost of error: extra vendor dependence and many small-denominator rates.

7. **Broken-board money-losing effect.** Add next-day median/mean return of
   yesterday's broken names, the share below -5%, and the corresponding
   consecutive-board subset. Derive it from canonical bars and the existing
   touched/sealed logic; no new provider is required for a retrospective
   study. This adds a loss-effect dimension rather than another same-day board
   count. Pre-register paired comparisons with the existing `premium` over the
   common `n>=120` sessions: retain only if broken-board return improves the
   next-session return rank correlation by at least 0.05 and has no more than
   10% missing eligible days; otherwise reject. Cost of error: survivorship,
   locked-open and tiny-sample bias can make a dramatic-looking loss metric
   unstable.

8. **Median breadth and trend breadth as diagnostics, not automatic weights.**
   Evaluate median return, the share above a fixed moving average, and
   concentration using canonical bars; `market_sentiment.py:118-153` and
   `xiaojie_indicators.py:123-177` already implement parts of the arithmetic.
   They add distributional breadth and concentration that `up_ratio` does not,
   but the current redundancy table only tests the eight existing scores. On
   the `n=250` daily window, compare each diagnostic's quintile forward-return
   spread with `up_ratio`; retain a diagnostic only if its outer-quintile
   5-session spread exceeds `up_ratio` by at least 0.10 percentage points and
   it is available on at least 90% of days. Otherwise reject it as redundant or
   too incomplete. Cost of error: cap/sector composition and corporate-action
   artifacts masquerade as sentiment.

9. **PCA as a robustness check, not a replacement.** The stored score matrix
   already supports a standardized PCA on `n=248`; its first two components
   explain 57.7955%. Compare equal-weight temperature with a PCA score using
   rolling, prior-only loadings. Pre-register the same 1/3/5-session forward
   return tables, rank correlation with the equal-weight series, and band
   agreement; reject PCA as the default unless it improves the 5-session
   outer-quintile spread by at least 0.10 percentage points without changing
   more than 10% of bands. Cost of error: unstable, opaque loadings and
   in-sample overfit on a small overlapping sample.

### (c) Candidates that first need history to accumulate

10. **Same-time intraday ranking history.** Replace close-history ranking for
    cumulative counts with a 250-session history at each sample time, or
    explicitly keep the current close-history rank as a proxy. Existing
    intraday data has only the ten sampled dates here and no 250-session
    same-time archive. The first date for a complete 250-session window is
    **not established** because the archival start and exchange holiday
    calendar for this capability are not in the evidence. Test after the
    window exists: compare morning-vs-close gap distributions and band changes;
    reject same-time ranking if it does not reduce mean absolute 10:30 gap by
    at least 50% while preserving close-sample agreement. Cost of error:
    continued morning-low bias or a new time-of-day artifact.

11. **Sealing time, re-seal count, sealed amount and board-path events.**
    Archive `limits.seal_detail`, `limits.broken_pool`, and the vendor ladder
    daily. The catalog marks Longhu/Xuangubao detail as declared and the TDX
    pools as current-session/no-history (`catalog.py:557-577`), so no 250-day
    PIT window exists; its earliest complete date is **not established**.
    These fields add path and liquidity information beyond seal rate and
    `max_streak`. After 250 sessions, compare each field's incremental
    next-session return/loss-effect rank correlation against `seal_rate` and
    `max_streak`; reject if the pre-registered 0.05 incremental threshold is
    not met or coverage is below 90%. Cost of error: provider timestamp and
    queue semantics are not interchangeable with executable liquidity.

12. **Margin, attention and news tone.** The catalog has declared margin,
    hot-rank and news capabilities, but margin is available T+1, some rank
    history is backfilled rather than PIT, and no validated tone model is
    stored (`catalog.py:224-251,662-680,744-745`). They therefore need an
    explicit point-in-time archive before a 250-session comparison; the first
    complete date is **not established** for each. Their proposed addition is
    positioning/attention/information flow, which is orthogonal to the
    board/breadth scores, not another board count. After accumulation, compare
    each against the current composite with the same 1/3/5-session metrics and
    retain only a pre-registered 0.05 incremental rank-correlation gain with
    at least 90% PIT coverage. Cost of error: publication lag, revisions and
    platform-specific attention can create look-ahead or unstable regimes.

No proposal changes a threshold, weight, band, live path, or execution rule in
this branch. Phase 3 data and the computation script remain the evidence base
for the tests above.
