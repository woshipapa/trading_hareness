# TDX statistics files: column dictionary

Scope: `tdxstat.cfg` (35 pipe fields, 8,080 rows) and `tdxstat2.cfg` (21 fields, 8,080 rows),
UTF-8, LF with CRLF line ends, last close 2026-10-09. Every row has a unique (market, code).
Market: 0 SZ (4,132 rows), 1 SH (3,596 rows), 2 BJ (352 rows).

Method. A 420-symbol sample (seed 7: SZ main 130, SH main 110, ChiNext 60, STAR 60, BJ 30, ETF/fund 30)
was matched against three references:

- MAC daily bars (800 unadjusted bars, newest last; server 121.36.248.138).
- MAC batch quote fields, bits 0-125 (registry names in `tdxpkg/tdx_mac_fields.py`).
- Tencent `qt.gtimg.cn` (used during exploration: PE, 52-week high/low, change, amplitude).

A value hits when `|a - b| <= max(1e-3 * |b|, 0.01)` after the candidate is scaled
(x1, x-1, x0.01, x100, x1e4, x1e-4 and sign). Verdict: CONFIRMED >= 95 %, PLAUSIBLE >= 60 %, otherwise UNKNOWN.
Columns marked "probe" are recomputed by `scripts/probe-tdx-q-stats.py`; the UNKNOWN rows used the
exploration draw of 420 symbols (different quota mix) and were not re-run by the probe.

Sentinels and blanks (whole file, 8,080 rows): the value `-1` occurs only in tdxstat col 5, where it is a
valid one-day down streak (not a sentinel). Empty fields are not used as zero anywhere in this document.
Blank rows are mostly non-equity rows (ETF/fund prefixes 15, 16, 18, 50-53, 55, 56, 58: 2,186 rows) and the
handful of newest listings (7 rows blank in the change columns: ChiNext 3, BJ 3, main 1, not inspected further).

## tdxstat.cfg (35 fields)

| idx | inferred name | type | unit / scale | best reference | hit-rate (n) | verdict |
|---|---|---|---|---|---|---|
| 0 | market | enum 0/1/2 | SZ / SH / BJ | code-prefix rule (SZ 00,30,12,15,16,180; SH 11,50,51,52,53,55,56,58,60,68,90; BJ leading 4, 8, 9) | 7,973 rows classified by the rule, 0 mismatches; 107 rows (SH 520/526/530/551, SZ 180) lie outside the rule and were not checked further | CONFIRMED (rule) |
| 1 | stock code | string | 6 digits | identity | all 420 sampled codes resolve on MAC | CONFIRMED (identity) |
| 2 | unknown signed float (-35 to +65) | float | % or ratio, unresolved | bar change 250d (-0.01) | 2.5 % (n=399) | UNKNOWN |
| 3 | PE static | float | ratio (x) | MAC bit 49 pe_static | 99.7 % (389/390), probe | CONFIRMED |
| 4 | last trade date | date | YYYYMMDD | last bar date | 100 % (420/420), probe | CONFIRMED |
| 5 | up/down streak | int, signed | days (+up, -down) | consecutive same-sign closes, incl. today | 95.2 % (400/420), probe | CONFIRMED (borderline) |
| 6 | change 1 day | float | % | close / prev close - 1 | 100 % (420/420), probe | CONFIRMED |
| 7 | previous-day change | float | % | MAC bit 66 prev_change_pct | 99.3 % (417/420), probe | CONFIRMED |
| 8 | change two days ago | float | % | MAC bit 71 prev2_change_pct | 99.3 % (417/420), probe | CONFIRMED |
| 9 | PE TTM | float | ratio (x) | MAC bit 48 pe_ttm | 100 % (390/390), probe | CONFIRMED |
| 10 | dividend yield | float | % | MAC bit 91 dividend_yield_rate | 100 % (390/390), probe | CONFIRMED |
| 11 | circulating capital | float | MAC bit 45 unit (10k shares per registry); unit not reconciled with float shares | MAC bit 45 | 100 % (390/390), probe | CONFIRMED (identity to MAC bit; unit unresolved) |
| 12 | listing date (hypothesis) | date | YYYYMMDD | bars first date | 8 of 190 exact; bars cap at 699 bars, so not testable | UNKNOWN |
| 13 | small integer code / count (0 dominant, 1,094 zeros) | int | unresolved | Tencent 74 | 48 % (n=155) | UNKNOWN |
| 14 | amount-like, yuan? (2.56 M for 000001) | float | unresolved; likely a flow amount | rank vs MAC bit 56 main net | 2.3 % best value match; Spearman -0.00 | UNKNOWN |
| 15 | integer count (41,698 for 000001) | int | unresolved | none above 1 % | 0.5 % | UNKNOWN |
| 16 | amount-like float | float | unresolved | none above 1 % | 0.9 % (n=356); Spearman -0.18 vs main net | UNKNOWN |
| 17 | change over ~20 days | float | % | bar close 19 bars back | 91.4 % (383/419), probe | PLAUSIBLE |
| 18 | change 20 days | float | % | MAC bit 59 change_20d_pct | 99.8 % (419/420), probe | CONFIRMED |
| 19 | change over ~60 days | float | % | bar close 59 bars back | 80.7 % (335/415), probe | PLAUSIBLE |
| 20 | change 60 days | float | % | MAC bit 68 change_60d_pct | 99.8 % (419/420), probe | CONFIRMED |
| 21 | YTD change | float | % | MAC bit 60 ytd_pct | 100 % (420/420), probe | CONFIRMED |
| 22 | code-like int (70110, 130110, 91310) | int | unresolved | MAC integer bits | 0.7 % | UNKNOWN |
| 23 | MAC bit 125 (unnamed in registry) | float as int | raw | MAC bit 125 | 100 % (420/420), probe | CONFIRMED (identity; meaning unknown) |
| 24 | amount-like (35,487,700 for 000001) | float | unresolved | rank vs MAC main net | 0.5 %; Spearman -0.11 | UNKNOWN |
| 25 | amount-like float | float | unresolved | rank vs MAC main net | 0.5 %; Spearman -0.09 | UNKNOWN |
| 26 | annual limit-up days | int | days | MAC bit 88 annual_limit_up_days | 100 % (390/390), probe | CONFIRMED |
| 27 | change 4 days | float | % | bar close 4 bars back | 98.1 % (412/420), probe | CONFIRMED |
| 28 | change 5 days | float | % | MAC bit 69 change_5d_pct | 100 % (420/420), probe | CONFIRMED |
| 29 | change 9 days | float | % | bar close 9 bars back | 94.8 % (398/420), probe | PLAUSIBLE |
| 30 | change 10 days | float | % | MAC bit 70 change_10d_pct | 100 % (420/420), probe | CONFIRMED |
| 31 | flag 0/1 (19 non-blank) | int flag | unresolved | none (n too small) | n=19 | UNKNOWN |
| 32 | flag 0/1 (19 non-blank) | int flag | unresolved | none (n too small) | n=19 | UNKNOWN |
| 33 | flag 0/1 (14 non-blank) | int flag | unresolved | none (n too small) | n=14 | UNKNOWN |
| 34 | mostly 0.00 (200000.00 in 4 rows) | float | unresolved | Tencent 74 at 1e-4 | 93.8 % but artifact: 5,101 rows are 0.00 | UNKNOWN |

Counts, tdxstat.cfg: CONFIRMED 19 (cols 0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 18, 20, 21, 23, 26, 27, 28, 30),
PLAUSIBLE 3 (17, 19, 29), UNKNOWN 13 (2, 12, 13, 14, 15, 16, 22, 24, 25, 31, 32, 33, 34).

## tdxstat2.cfg (21 fields)

| idx | inferred name | type | unit / scale | best reference | hit-rate (n) | verdict |
|---|---|---|---|---|---|---|
| 0 | market | enum 0/1/2 | SZ / SH / BJ | code-prefix rule, as tdxstat col 0 | 8,080 of 8,080 rows | CONFIRMED (rule) |
| 1 | stock code | string | 6 digits | identity | 420 of 420 | CONFIRMED (identity) |
| 2 | last trade date | date | YYYYMMDD | last bar date | 100 % (420/420), probe | CONFIRMED |
| 3 | amount today | float | yuan / 1e4 = 万元 | bar amount / 1e4 | 100 % (420/420), probe | CONFIRMED |
| 4 | unresolved (87 distinct, 99 % blank) | float | unresolved | none tested with a hit | n < 50 usable | UNKNOWN |
| 5 | amount previous day | float | 万元 | bar amount, prior bar / 1e4 | 100 % (420/420), probe | CONFIRMED |
| 6 | unresolved (2 non-blank) | float | unresolved | none | n=2 | UNKNOWN |
| 7 | amount two days ago | float | 万元 | bar amount, 2 bars back / 1e4 | 100 % (420/420), probe | CONFIRMED |
| 8 | unresolved (5 non-blank) | float | unresolved | none | n=5 | UNKNOWN |
| 9 | integer (0 dominant, ~2,200 distinct) | int | unresolved | rank vs MAC main net -0.14 | 3.7 % best value | UNKNOWN |
| 10 | integer (0 dominant) | int | unresolved | rank vs MAC main net -0.13 | 3.5 % best value | UNKNOWN |
| 11 | month-to-date change | float | % | bar close vs last close of prior month | 99.3 % (417/420), probe | CONFIRMED |
| 12 | change 1 year | float | % | MAC bit 65 change_1y_pct | 100 % (420/420), probe | CONFIRMED |
| 13 | board / sector code (880xxx) | int | block index code, unverified | none | 0.5 % | UNKNOWN |
| 14 | open-auction amount | float | yuan / 1e4 = 万元 | MAC bit 87 open_amount / 1e4 | 99.5 % (403/405), probe | CONFIRMED |
| 15 | unresolved (0.00 dominant) | float | unresolved | none; ytd 3.5 % | 3.5 % | UNKNOWN |
| 16 | unresolved (1.000 / 100.000 / 20.000) | float | unresolved | none | 7.1 % | UNKNOWN |
| 17 | 52-week high | float | yuan | MAC bit 53 (= Tencent qt 67) | 100 % (420/420), probe | CONFIRMED |
| 18 | 52-week low | float | yuan | MAC bit 54 (= Tencent qt 68) | 100 % (420/420), probe | CONFIRMED |
| 19 | change ~30 days | float | % | bar close 29 bars back | 90.0 % (376/418), probe | PLAUSIBLE |
| 20 | change 30 days | float | % | bar close 30 bars back | 89.2 % (373/418), probe | PLAUSIBLE |

Counts, tdxstat2.cfg: CONFIRMED 11 (cols 0, 1, 2, 3, 5, 7, 11, 12, 14, 17, 18), PLAUSIBLE 2 (19, 20),
UNKNOWN 8 (4, 6, 8, 9, 10, 13, 15, 16).

Notes on the flow-like columns. tdxstat cols 14-16, 24-25 and tdxstat2 cols 9-10 were compared with
the MAC main-net amount (bit 56, yuan). Their Spearman correlations are between -0.18 and 0.00, and the
sign agreement is 36-50 %. They are not main-net amounts. Their magnitudes are only consistent with
amounts in yuan or 万元. No label beyond that is assigned.

## Verdict totals

Both files together (56 columns): CONFIRMED 30, PLAUSIBLE 5, UNKNOWN 21.

Caveats:

- Spec rows are those with a reference. The 0/1 flags (tdxstat 31-33) had too few non-blank rows
  in the sample to score.
- Window-based changes (tdxstat 17, 19, 29; tdxstat2 19, 20) do not equal the MAC change fields
  exactly (MAC bit 59 equals the 20-day change at 99.8 %, while bar close 19 bars back gives 91.4 %).
  The base-day convention is not resolved.
- tdxstat 27 (4-day change) and 29 (9-day change) use windows no MAC field carries; they match by
  bar closes and are reported as measured.
- The MAC bit 45 "circulating capital" values (for example 816,056.56 for 000001) do not match
  float shares x price in any unit tried. The column reproduces the MAC bit; the unit is open.

## Probe summary (scripts/probe-tdx-q-stats.py, run 2026-10-10)

```
sample symbols: 420; MAC quotes: 420; bar series: 420
tdxstat[3] PE static: ref=mac49 hits=389/390 rate=99.7% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[4] date: ref=last_date hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[5] up/down streak (signed): ref=streak hits=400/420 rate=95.2% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[6] change 1d %: ref=ret1 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[7] prev change %: ref=mac66 hits=417/420 rate=99.3% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[8] prev2 change %: ref=mac71 hits=417/420 rate=99.3% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[9] PE TTM: ref=mac48 hits=390/390 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[10] dividend yield %: ref=mac91 hits=390/390 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[11] circulating capital: ref=mac45 hits=390/390 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[17] change ~20d %: ref=ret19 hits=383/419 rate=91.4% verdict=PLAUSIBLE claimed=PLAUSIBLE
tdxstat[18] change 20d %: ref=mac59 hits=419/420 rate=99.8% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[19] change ~60d %: ref=ret59 hits=335/415 rate=80.7% verdict=PLAUSIBLE claimed=PLAUSIBLE
tdxstat[20] change 60d %: ref=mac68 hits=419/420 rate=99.8% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[21] YTD %: ref=mac60 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[23] MAC bit 125 (unnamed): ref=mac125 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[26] annual limit-up days: ref=mac88 hits=390/390 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[27] change 4d %: ref=ret4 hits=412/420 rate=98.1% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[28] change 5d %: ref=mac69 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat[29] change 9d %: ref=ret9 hits=398/420 rate=94.8% verdict=PLAUSIBLE claimed=PLAUSIBLE
tdxstat[30] change 10d %: ref=mac70 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[2] date: ref=last_date hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[3] amount today (万): ref=amt0 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[5] amount prev day (万): ref=amt1 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[7] amount 2 days ago (万): ref=amt2 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[11] MTD %: ref=mtd hits=417/420 rate=99.3% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[12] change 1y %: ref=mac65 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[14] open auction amount (万): ref=mac87 hits=403/405 rate=99.5% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[17] 52-week high: ref=mac53 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[18] 52-week low: ref=mac54 hits=420/420 rate=100.0% verdict=CONFIRMED claimed=CONFIRMED
tdxstat2[19] change ~30d %: ref=ret29 hits=376/418 rate=90.0% verdict=PLAUSIBLE claimed=PLAUSIBLE
tdxstat2[20] change 30d %: ref=ret30 hits=373/418 rate=89.2% verdict=PLAUSIBLE claimed=PLAUSIBLE
all CONFIRMED columns >= 95%
```
