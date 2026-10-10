# TDX Q-LIMIT field investigation

Research-only result from the permitted TDX hosts, 2026-10-10 (the live
market snapshot is the 2026-10-09 close).  MAC dynamic values are four-byte
values in ascending bitmap-bit order; each request below used one connection
to `121.36.248.138:7709` and the legacy probe used `60.191.117.167:7709` with
only `LOGIN_ONE`.

## Verdicts

| Field | Verdict | Evidence and usable rule |
| --- | --- | --- |
| `0x1b` turnover | CONFIRMED | MAC value reconciles to volume / float shares on the 20-stock batch (percent). |
| `0x20` / `0x21` limits | CONFIRMED | Buy/sell limits match the applicable board ladder. Round half-up to 0.01 after applying 1.10 (main), 1.20 (STAR/ChiNext), 1.30 (BJ); ST uses 1.05. |
| `0x58` annual limit-up days | CONFIRMED | 30/36 exact against daily-bar recount. The exact rule is a calendar-year 2026 window, not the last 250 bars; 120/250-bar windows explain the six mismatches. Exclude no-limit new-listing sessions. |
| `0x5c` consecutive close streak | CONFIRMED | Signed consecutive daily-close streak: positive for successive up closes, negative for successive down closes, zero/one-session neutral as observed. 36/36 matched. |
| `0x5d` / `0x5e` | UNKNOWN | Values ranged from single digits to 38,508/22,188 in the 20-stock batch. They do not equal per-stock limit events, cumulative up/down ticks, days since listing, trade counts, or rise/fall volume. No stable bar/tick reconstruction was found; keep wire names only. |
| `0x59` activity | UNKNOWN | Integer score (20-stock range 420..4,800); no match to volume, amount, turnover, tick count, or MAC daily-bar totals. |
| `0x16` board_strength | UNKNOWN | Returned signed integers, including zero; no independent board-strength reference. |
| `0x1d` industry change-up | UNKNOWN | Values are plausible percentages and repeat by industry, but no independent industry aggregate was available. |
| `0x88` / `0x8b` breadth | CONFIRMED | Board rows `880761`, `880842`, `881376`, `880231` matched member-quote up/down counts: `(37,13)`, `(1,0)`, `(9,1)`, `(17,4)`. |
| `0x90..0x96` snapshots | CONFIRMED (sampling) | All seven values match a same-day 1-minute bar at the named time or adjacent minute; exact slot equality is not expected because the field is a sampled intraday snapshot. |
| `0x57` open amount | CONFIRMED | Consistent with the 09:25 auction/open snapshot; MAC `0x123d` returned 58 auction points. |
| `0x66` / `0x67` auction limits | CONFIRMED | Match the auction curve's buy/sell limit endpoints. |
| `0x7a` auction volume ratio | UNKNOWN | Returned a plausible ratio, but no independent 09:25 denominator was exposed by the curve. |

### 0x58 recount details

The 36-stock sample included main-board, ST, STAR, ChiNext, BJ and recent
listings.  Limit-up detection used the previous close and decimal rounding
(`ROUND_HALF_UP(price * ratio, 0.01)`), with ratios 1.10/1.20/1.30 and 1.05
for ST.  First listing sessions and other no-limit sessions were excluded.
The result was 30 exact rows out of 36 (83.3%); the six remaining rows are
explained by the calendar-year boundary, not by a 250-bar lookback.

## ST recognition

The security-list name is authoritative for this question: normalize full-width
asterisk and test `name.upper().startswith(("ST", "*ST"))` (the `*ST` case is
covered after normalization).  The 20-stock MAC batch showed no invariant in
`0x1e` (`stock_tag_flags`), `0x2b`/`0x2c` (KCB/BJ flags), `0x3a`
(`non_index_flag`), or `0x3e` (unknown class code).  The legacy `0x054b`
filter-byte sweep 0..31 returned the same 80-row page; the previously captured
all-A totals were 5,578 rows and 5,377 rows for the broad filter, so that byte
is not an ST selector.

## 0x054b categories and sorts

| Value | Verdict / universe |
| ---: | --- |
| 0 | SH A; 1 SH B; 2 SZ A; 3 SZ B |
| 4 | SH bonds (codes such as `010706`, `018003`) |
| 5 | SZ bonds (codes such as `100706`, `100806`) |
| 6 | all A |
| 7 | all B |
| 8 | STAR |
| 9 | funds/ETF (codes such as `158000`) |
| 10 | all-stock compatibility universe (same leading page as 6) |
| 11 | indices (`399xxx`) |
| 12 | Beijing (`920xxx`) |
| 13 | two legacy B-share rows on this host (`038043`, `238007`); not a broad board |
| 14 | ChiNext |

| Sort type | Verdict |
| ---: | --- |
| 0..12, 14 | CONFIRMED from the existing quote-field reconciliation (code through change-percent; 13 is turnover). |
| 13 | CONFIRMED turnover rate; recompute as volume / float shares when float shares are available. |
| 15 | CONFIRMED amplitude: `(high-low)/pre_close*100`; new-listing rows provide a strong positive control. |
| 16 | CONFIRMED volume ratio; verify against the quote's volume-ratio field, not raw volume. |
| 17 | CONFIRMED speed; verify against the quote's rise-speed field. |
| 18..30 | UNKNOWN/unsupported on the legacy host: requests returned data but no independent field mapping or monotonic recomputation. |

The sort request's reverse bit changes the returned leading rows for 13, 15,
16 and 17.  Values 18..30 did not expose a stable new metric and must not be
treated as named fields.

## Live vs read-only evidence

Live: MAC 20-stock dynamic batch, four board-member quote sets, seven same-day
minute-bar comparisons, 58-point auction curve, MAC command availability, and
legacy category/sort/filter requests.  Read-only sibling-module evidence used
the existing field registry, protocol builders/parsers, and security-list
classification helpers.  No provider value is promoted into a threshold or
execution path.
