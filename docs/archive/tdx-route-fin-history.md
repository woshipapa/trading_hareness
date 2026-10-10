# TDX historical financial statements route

Source harvest: `codex/tdx-fin-history` at `b72e7c34`.

Observed 2026-10-09 from `tdxfin/gpcw.txt` and six bounded downloads from
`120.76.152.87:7709` (one connection; the cached files are local evidence).

## Manifest and schema

The manifest contains 148 period names from `gpcw19881231.zip` through the
2026-12-31 placeholder. There are four periods per year (Q1, H1, Q3, FY) from
2003 onward; older years have the same quarterly naming pattern but many
placeholder/empty archives. Archive size grows from roughly 0.9 MB in 2003,
to 2.0 MB in 2010, 3.1 MB in 2015, 5.4 MB in 2020 and 5.8 MB in 2024-26.

| period | ZIP bytes | records | float columns |
|---|---:|---:|---:|
| 2026-06-30 | 5,755,893 | 5,573 | 584 |
| 2025-12-31 | 5,814,477 | 5,575 | 584 |
| 2024-12-31 | 5,784,863 | 5,576 | 584 |
| 2020-12-31 | 5,374,248 | 5,446 | 584 |
| 2015-12-31 | 3,124,027 | 3,374 | 584 |
| 2010-12-31 | 2,037,312 | 2,313 | 584 |

No column-count change is observed in these six eras; the oldest sampled file
already has 584 floats. The `.dat` header is `(kind, report_date, count,
unknown, record_size, reserved)` and has no announcement timestamp. Columns
after the documented table are retained as `colN`.

## Availability and PIT verdict

`tipinfo.dat` is a separate 22-column UTF-8 snapshot. Its first four fields are
market, code, report period and EPS; field 4 (`20260815` for `000001` H1 2026,
eight digits, YYYYMMDD) is the announcement date, matching the public
disclosure date. GPCW column 314 carries the same date in its own six-digit
YYMMDD form: a GPCW cell is a float32, which cannot hold an eight-digit date such
as `20260815` exactly. It is not independently confirmed
for five symbols here (the requested five-stock public-list cross-check was not
available in this bounded six-period collection). Therefore GPCW is **not point-in-time**: use an
independent `events.disclosure_schedule`/announcement observation as the
eligibility boundary, and treat the GPCW value as latest/restated snapshot.

The header report date is the accounting period, not first disclosure. The
files contain one latest row per stock for that period; they do not carry
prior versions. Restatement detection across later files is consequently not
possible from the same-period row alone; archive each manifest revision and
compare hashes/values when a later download changes.

## Units and cross-checks

The parser preserves raw values with units: monetary statement items are yuan,
share-capital fields are shares, per-share fields are yuan/share, and ratios
are ratio values. For `000001` and `600519`, columns 74 (revenue) and 96
(attributable net profit) across the five annual periods agree with the TDX
finance-summary scale after the summary's 1,000x 千元 conversion. `600519`
FY2024 is 170.899 billion yuan revenue and 86.228 billion yuan attributable
profit, matching the known approximately 1,741/862 billion figures within the
expected restatement/rounding difference. The TTM helper `ttm_from_cumulative`
computes TTM = FY_prev + cum_now - cum_prior_year_same_period from the
confirmed year-to-date cumulative series (Q1/H1/Q3 are YTD; FY starts fresh).

| report period | 000001 revenue / profit (CNY bn) | 600519 revenue / profit (CNY bn) |
|---|---:|---:|
| 2025-12-31 | 131.442 / 42.633 | 168.838 / 82.320 |
| 2024-12-31 | 146.695 / 44.508 | 170.899 / 86.228 |
| 2020-12-31 | 153.542 / 28.928 | 94.915 / 46.697 |
| 2015-12-31 | 96.163 / 21.865 | 32.660 / 15.503 |
| 2010-12-31 | 18.022 / 6.284 | 11.633 / 5.051 |

The later 2026 H1 row was also parsed successfully. A
three-symbol check (`000001`, `600519`, `000002`) found no embedded prior-year
comparative row or version identifier, so the files expose only the latest
value for each period. Restatement detection requires retaining later manifest
revisions and comparing the same-period ZIP hashes/rows.

## Backfill and capability scope

The manifest implies 148 logical period downloads and 275.99 MB compressed
bytes (the 148 listed sizes). With the implementation's 30,000-byte TDX
chunks this is approximately 9,201 transport requests, plus one manifest
request. The bounded downloader refuses a period whose manifest size is over the 64 MiB
cap before requesting it, raises when a file ends before that size, and `gpcw()`
verifies the manifest MD5/size before parsing.

This route can back research-only `fundamentals.financial_statements` and can
support `fundamentals.daily_basic` only for share/count fields. It cannot by
itself back `events.earnings_forecast`, `events.earnings_express`, or
`events.disclosure_schedule`; those remain event-source capabilities whose
timestamps establish PIT availability.

## 更正（2026-10-11）：可得时间与 PIT 结论

上文 “Availability and PIT verdict” 一节被部分取代，原文照旧保留。

- **tipinfo 第 4 列已被确认。** 原文说它没有独立确认；之后的 `docs/archive/tdx-q-disclosure.md` 用东财 `RPT_PUBLIC_BS_APPOIN` 的 5,551 只对过，相等 5,549 只（99.96 %），其中两个“例外”其实是另一报告期的行（见该文末尾的更正）。它是首次披露日，只有日期，没有时刻。
- **GPCW 现在有一部分行能定可得时间。** `tdx_fin_history.date_gpcw_rows(rows, tipinfo_rows)` 按 (代码, 报告期) 用 `tipinfo.dat` 第 4 列（`tdx_zhb_extras.parse_tipinfo` 的 `first_disclosure_date`）给 GPCW 行设 `available_at` = 首次披露日当天 23:59:59（Asia/Shanghai），下一个交易日起可用，并返回仍无日期的行数。所以“GPCW **不是** point-in-time”不再对每一行成立。
- **更早的期仍无日期。** `tipinfo.dat` 每只证券只留最新一期（`scripts/data/tdx_tipinfo_2026-10-10_mac.json`：5,652 行里 5,649 行的报告期是 20260630），更早的期，以及它没列的证券，`available_at` 只能是采集时刻。盘后任务 `tdx_gpcw` 把这两种行分别记成 `availability_basis` = `tipinfo_first_disclosure` 和 `collection_time_undated`（`quant-service/app/datasources/collectors/post_close.py`）。要给更早的期定日期，得把每次下载的 (代码, 报告期, 首次披露日) 累积起来；盘后任务 `tdx_tipinfo` 按日归档，`tdx_gpcw` 读这些已归档的行。
- **仍然成立：** GPCW 文件本身没有公告时间戳，值是最新或重述后的快照，同一期可能被重述；第 4 列只有日期；`events.earnings_forecast`、`events.earnings_express`、`events.disclosure_schedule` 仍由事件源的时间戳定可得时间。
