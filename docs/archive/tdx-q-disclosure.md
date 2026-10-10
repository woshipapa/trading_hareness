# TDX Q-DISC disclosure findings

Observed 2026-10-10. The evidence is read-only research data. One `zhb.zip`
was downloaded from `60.191.117.167:7709` using `LOGIN_ONE` and cached outside
the repository. It contains 5,652 `tipinfo.dat` rows, 1,375 `tdxpkmore.cfg`
rows, 12 `importzs.cfg` rows, 15 `xgsg.cfg` rows and 12 `othersg.cfg` rows.

## Q1: is `tipinfo.dat` column 4 a PIT disclosure date?

Eastmoney `RPT_PUBLIC_BS_APPOIN`, filtered to `REPORT_DATE=2026-06-30`,
returned 5,551 rows over 12 pages. All 5,551 symbols were present in
`tipinfo.dat`. Column 4 equals Eastmoney `ACTUAL_PUBLISH_DATE` for 5,549/5,551
(99.96%). The two exceptions were `002107` (`tipinfo=20261010`, actual H1
publication `20260716`) and `002731` (`tipinfo=20251028`, with no H1 actual
publication in the response). By board, the sample included 1,452 SZ-main,
1,403 ChiNext, 1,699 SH-main, 612 STAR and 339 BJ rows, including early and
late reporters. Columns 5--7 do not reproduce the actual date (56/5,296,
43/5,513 and 0/494 exact matches respectively); they are not disclosure dates.

**Verdict: CONFIRMED as a high-coverage report-publication/appointment date for
the period snapshot, but NOT a universal PIT availability timestamp.** The
field is a vendor snapshot value and can be stale or move to a later date
(`002107`); use an independent event's publication/ingest clock for PIT
eligibility. It is not equivalent to finance-summary `updated_date` in the
general case.

## `tipinfo.dat` 22-column evidence table

Column numbers below are zero-based (the parser's `field_N` names).

| Col | Observed meaning | Verdict / evidence |
| ---: | --- | --- |
| 0 | market (0 SZ, 1 SH; BJ rows also use the vendor market code) | CONFIRMED by code/market partition |
| 1 | six-digit security code | CONFIRMED |
| 2 | report period (`20260630`) | CONFIRMED; same period as the Eastmoney filter |
| 3 | EPS | CONFIRMED; `000001=1.24`, matching the finance summary |
| 4 | period report publication/appointment date | CONFIRMED high-coverage (5,549/5,551 actual-date matches); not PIT clock |
| 5 | another report/event date | UNKNOWN; only 56/5,296 match actual publication |
| 6 | another report/event date | UNKNOWN; only 43/5,513 match actual publication |
| 7 | sparse date (494 rows) | UNKNOWN; 0 exact actual-date matches |
| 8 | sparse date paired with col 9 | PLAUSIBLE corporate-event date; no public join performed |
| 9 | amount paired with col 8 | PLAUSIBLE event amount; no public join performed |
| 10 | date paired with col 9 | PLAUSIBLE event date; no public join performed |
| 11 | sparse date paired with col 12 | UNKNOWN |
| 12 | sparse one-digit event type/code | UNKNOWN |
| 13 | date paired with col 14 | PLAUSIBLE capital/share event date; no public join performed |
| 14 | amount paired with col 13 | PLAUSIBLE share/amount value; no public join performed |
| 15 | date paired with col 16 | PLAUSIBLE capital/share event date; no public join performed |
| 16 | amount paired with col 15 | PLAUSIBLE share/amount value; no public join performed |
| 17 | empty in all 5,652 rows | UNKNOWN / reserved |
| 18 | empty in all 5,652 rows | UNKNOWN / reserved |
| 19 | date paired with col 20 | PLAUSIBLE dividend/event date; no public join performed |
| 20 | amount/rate paired with col 19 | PLAUSIBLE dividend/event value; no public join performed |
| 21 | sparse date | UNKNOWN; not the H1 disclosure date |

The paired date/number shapes are useful structural evidence only; they are
deliberately not promoted to named financial facts without a row-level public
join.

## `tdxpkmore.cfg`

Columns 0/1/2 are market, security code and name; column 4 repeats the code
(CONFIRMED structurally). Columns 3, 5, 6, 8 and 9 are flags or reserved
values (UNKNOWN). Column 7 is a sparse numeric override: 24 rows contain
`10.00`, `5.00`, `2.00`, `1.00` or `0.10`; 1,351 rows are blank. The values do
not describe the ordinary board limit ratio: ordinary main-board/STAR/ChiNext
securities are mostly blank, while named exceptions include non-ST companies.
The rows with blank column 7 and flags `1|1` are largely recent IPOs, which is
consistent with a special-listing rule, but does not prove the exact flag
semantics or exemption duration. **Column 7 and all flags: UNKNOWN** pending a
listing-date/trading-day history join; no limit-ratio meaning is claimed.

## `importzs.cfg`

All rows share date `20261008` and are index-like codes with member counts
(e.g. `000016` count 50, `000688` count 50, `999999` count 2,361). The final
five numeric positions are valuation/statistics-shaped. The live snapshot
does not include the requested `000300`, `000905` or `399006` rows, and no
public index page was used to establish a one-to-one field mapping.

| Col | Meaning | Verdict |
| ---: | --- | --- |
| 0 | index/security code | CONFIRMED shape |
| 1 | valuation snapshot date | CONFIRMED shape |
| 2 | member count | CONFIRMED by index identities/counts |
| 3--7 | five index valuation/statistics values (possibly PE/PB/yield-style) | UNKNOWN; no exact public cross-check |

## `xgsg.cfg` and `othersg.cfg`

Eastmoney's IPO calendar was queried for 2026-09-20..2026-10-20. Five sampled
`xgsg` rows reproduce issue price, total issue, online issue, after-issue PE,
winning rate, application code/cap and security name; duplicate trailing
positions reproduce the same values. The following labels are therefore
CONFIRMED for the sampled rows; blank/residual positions remain UNKNOWN.

| `xgsg` cols | Meaning | Verdict |
| --- | --- | --- |
| 0,1,2,3 | market, security code, application date, issue price | CONFIRMED |
| 4,5,6 | total issue, online issue, after-issue PE | CONFIRMED (5-row IPO join) |
| 7 | reserved/blank IPO value | UNKNOWN |
| 8 | listing date | PLAUSIBLE/CONFIRMED where populated by IPO join |
| 9 | online winning rate | CONFIRMED |
| 10 | subscription/application code | CONFIRMED |
| 11 | online application cap (10,000-share units) | CONFIRMED |
| 12,13 | result/listing-related date and amount | PLAUSIBLE; exact vendor subfield not isolated |
| 14 | security name | CONFIRMED |
| 15,16,17 | duplicate issue price, cap and PE | CONFIRMED |

For `othersg.cfg`, rows match convertible-bond subscriptions (underlying stock,
bond code, issue amount, price, winning rate, subscription date and Chinese
name). Columns 0--5, 6 (stock subscription code), 7 (face value, 1000), 8
(subscription date), 9 (winning/placement rate), 11 (bond name) are
CONFIRMED/strongly evidenced by the public IPO/bond shape; column 10 is
UNKNOWN (often a result date, blank otherwise).

## Probe and reproducibility

`python3 -I scripts/probe-tdx-q-disclosure.py /path/to/zhb.zip` performs the
12 bounded Eastmoney pages and exits non-zero when the match rate cannot be
computed. It reported `common_rows=5551`, `field_4_actual_publish_matches=5549`
and `field_4_match_rate=0.9996397` in this run. No credentials, trading
functions or writes were used.

## Final exhaustive dictionary

The probe emits a result for every column. Tipinfo rates use 5,497 joined
six-digit H1 finance rows (over the requested 300-stock gate); archive-specific
IPO/bond rates use their available calendar rows. `UNKNOWN` is an explicit
verdict when the tested candidate is below 60%.

### tipinfo.dat

| Col | Name (type/unit) | Verdict | Hit-rate | Evidence |
| ---: | --- | --- | ---: | --- |
| 0 | market code (enum) | CONFIRMED | 100% | valid TDX market values |
| 1 | security code (six digits) | CONFIRMED | 100% | finance code join |
| 2 | report period (YYYYMMDD) | CONFIRMED | 100% | H1 period join |
| 3 | EPS (yuan/share) | CONFIRMED | 99.95% | finance EPSJB |
| 4 | first disclosure date (date) | CONFIRMED | 99.96% | actual publish/NOTICE_DATE |
| 5 | event date (date) | UNKNOWN | 1.04% | H1 notice candidate |
| 6 | event date (date) | UNKNOWN | 0.77% | H1 notice candidate |
| 7 | event date (date) | UNKNOWN | 0% | H1 notice candidate |
| 8 | event date (date) | UNKNOWN | 0.24% | H1 notice candidate |
| 9 | event amount (numeric) | UNKNOWN | 0% | no finance candidate |
| 10 | event date (date) | UNKNOWN | 0.09% | H1 notice candidate |
| 11 | sparse event date (date) | UNKNOWN | 43.75% | below plausibility threshold |
| 12 | event type (enum) | UNKNOWN | 0% | no candidate |
| 13 | capital event date (date) | UNKNOWN | 0.02% | H1 notice candidate |
| 14 | capital amount (shares/value) | UNKNOWN | 0% | no candidate |
| 15 | capital event date (date) | UNKNOWN | 0.04% | H1 notice candidate |
| 16 | capital amount (shares/value) | UNKNOWN | 0% | no candidate |
| 17 | reserved (empty) | UNKNOWN | 0% | empty in all rows |
| 18 | reserved (empty) | UNKNOWN | 0% | empty in all rows |
| 19 | dividend/event date (date) | UNKNOWN | 1.95% | H1 notice candidate |
| 20 | dividend/event value (numeric) | UNKNOWN | 0% | no candidate |
| 21 | sparse event date (date) | UNKNOWN | 3.19% | H1 notice candidate |

### tdxpkmore.cfg

| Col | Name (type/unit) | Verdict | Hit-rate | Evidence |
| ---: | --- | --- | ---: | --- |
| 0 | market (enum) | CONFIRMED | 100% | row structure |
| 1 | security code (six digits) | CONFIRMED | 100% | code format |
| 2 | security name (text) | CONFIRMED | 100% | GB18030 name |
| 3 | board flag (enum) | UNKNOWN | 0% | no unique rule |
| 4 | repeated code (six digits) | CONFIRMED | 100% | exact col-1 duplicate |
| 5 | status/IPO flag (enum) | UNKNOWN | 0% | recent-IPO correlation only |
| 6 | status/IPO flag (enum) | UNKNOWN | 0% | recent-IPO correlation only |
| 7 | exceptional price parameter (percent-like) | UNKNOWN | 0% | 24 overrides, no 60% rule |
| 8 | status flag (enum) | UNKNOWN | 0% | no public join |
| 9 | status flag (enum) | UNKNOWN | 0% | no public join |

### importzs.cfg

| Col | Name (type/unit) | Verdict | Hit-rate | Evidence |
| ---: | --- | --- | ---: | --- |
| 0 | index code (six digits) | CONFIRMED | 100% | internal index identities |
| 1 | snapshot date (date) | CONFIRMED | 100% | all `20261008` |
| 2 | constituent count (count) | CONFIRMED | 100% | index member counts |
| 3 | index statistic (unknown unit) | UNKNOWN | 0% | no constituent recomputation |
| 4 | index statistic (unknown unit) | UNKNOWN | 0% | no constituent recomputation |
| 5 | index statistic (unknown unit) | UNKNOWN | 0% | no constituent recomputation |
| 6 | index statistic (unknown unit) | UNKNOWN | 0% | no constituent recomputation |
| 7 | index statistic (unknown unit) | UNKNOWN | 0% | no constituent recomputation |

### xgsg.cfg

The direct IPO join has 15 rows; rates here are identity rates, not the
300-stock tipinfo gate.

| Col | Name (type/unit) | Verdict | Hit-rate | Evidence |
| ---: | --- | --- | ---: | --- |
| 0 | market (enum) | CONFIRMED | 100% | IPO market |
| 1 | security code (six digits) | CONFIRMED | 100% | SECURITY_CODE |
| 2 | subscription date (date) | CONFIRMED | 100% | APPLY_DATE |
| 3 | issue price (yuan) | CONFIRMED | 100% | ISSUE_PRICE |
| 4 | total issue (10,000 shares) | CONFIRMED | 100% | ISSUE_NUM |
| 5 | online issue (10,000 shares) | CONFIRMED | 100% | ONLINE_ISSUE_NUM/10000 |
| 6 | after-issue PE (ratio) | CONFIRMED | 100% | AFTER_ISSUE_PE |
| 7 | reserved IPO value (unknown) | UNKNOWN | 0% | blank/residual |
| 8 | listing date (date) | UNKNOWN | 0% | no stable IPO identity in the probe |
| 9 | online winning rate (ratio) | CONFIRMED | 100% | ONLINE_ISSUE_LWR |
| 10 | application code (six digits) | CONFIRMED | 100% | APPLY_CODE |
| 11 | application cap (10,000 shares) | CONFIRMED | 100% | ONLINE_APPLY_UPPER/10000 |
| 12 | result/listing milestone (date) | UNKNOWN | 0% | no stable IPO identity in the probe |
| 13 | post-issue quantity/value (numeric) | UNKNOWN | 0% | no stable identity |
| 14 | security name (text) | CONFIRMED | 100% | SECURITY_NAME_ABBR |
| 15 | duplicate issue price (yuan) | CONFIRMED | 100% | col 3 duplicate |
| 16 | duplicate application cap (10,000 shares) | CONFIRMED | 100% | col 11 duplicate |
| 17 | duplicate PE (ratio) | CONFIRMED | 100% | col 6 duplicate |

### othersg.cfg

The direct convertible-bond calendar sample has 12 rows.

| Col | Name (type/unit) | Verdict | Hit-rate | Evidence |
| ---: | --- | --- | ---: | --- |
| 0 | market (enum) | CONFIRMED | 100% | bond calendar market |
| 1 | underlying stock code (six digits) | CONFIRMED | 100% | stock/bond relation |
| 2 | bond subscription code (six digits) | CONFIRMED | 100% | bond code |
| 3 | issue amount (10,000 bonds) | CONFIRMED | 100% | issue size |
| 4 | subscription price (yuan) | CONFIRMED | 100% | issue price |
| 5 | winning/placement rate (ratio) | CONFIRMED | 100% | lottery rate |
| 6 | stock allotment code (six digits) | CONFIRMED | 100% | stock prefix/code |
| 7 | lot/face value (yuan) | CONFIRMED | 100% | observed 1000 face value |
| 8 | subscription date (date) | CONFIRMED | 100% | application date |
| 9 | placement rate/value (ratio) | CONFIRMED | 100% | placement field |
| 10 | result/listing milestone (date) | UNKNOWN | 0% | blank/non-uniform |
| 11 | bond name (text) | CONFIRMED | 100% | GB18030 name |
