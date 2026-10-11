# Q-EXT: extended-market codes, units and delay markers

## Verdict

The 7727 service is usable for research snapshots and bars when the **market
byte** is supplied. The category-list field names in the old module are
reversed: its `goods_type` is the broad type/category (for example 47 = CFFEX
futures), while `market` is the byte used in quote/K-line requests (31 = HK
main board, 74 = US stocks, 47 = CFFEX, 30 = Shanghai futures). Do not use a
`goods_type` value as the quote market.

Live evidence below was collected 2026-10-10 local time from four supplied
hosts. The market was closed; the latest data is the 2026-10-09 close. The
probe is [probe-tdx-q-extcodes.py](../../scripts/probe-tdx-q-extcodes.py).

## Hosts and enumeration

Every host completed setup -> login -> count -> categories. Three 100-row
instrument pages (the protocol's list is global, not market-filtered) returned
300 rows. The global count and category count were:

| Host | login `server_name` | description | count | categories |
| --- | --- | --- | ---: | ---: |
| 113.45.175.47 | `TDX_DS` | `基本资料` | 107,735 | 52 |
| 150.158.20.127 | `TDX延时全` | `基本资料` | 107,735 | 52 |
| 139.9.191.175 | `TDX延时全` | `基本资料` | 107,735 | 52 |
| 106.52.170.195 | blank | `基本资料` | 107,163 | 51 |

The list request is `start:uint32, count:uint16`; it has no market argument.
Therefore a bounded three-page run cannot claim per-market totals. The first
three pages happened to contain 89 CFFEX rows and 24 commodity-index rows; the
other requested markets were not in those pages. Full-market totals require
walking all global pages (and were deliberately outside this bounded probe).

| market id | category-list name / intended convention | examples observed or verified | daily category |
| ---: | --- | --- | ---: |
| 12 | category `国际指数` is **not** the quote market; this row is the `goods_type` byte | no `HSI`/global-index quote under 12; use market 27 for HK indices | 9 |
| 47 | `中金所期货`; contract strings `IF/IH/IC/IMYYMM`, `T/TF/TS/TL` | `IF2610` 沪深2610, `IC2610` 中证2610, `T2612` 十债2612, `TF2612` 五债2612, `TS2612` 二债2612, `TL2612` 30债2612; continuous aliases `IFL0`, `IFL1`, etc. | 4 |
| 60 | `主力期货合约` goods row; not a working quote market for `IFL0` on these hosts | `(60, IFL0)` returned zero; use `(47, IFL0)` for CFFEX main | 4 |
| 30 | `上海期货`; contracts such as `AU2610`, `AU2612` | `AU2610` / `AU2612` | 4 |
| 29 | `大连商品`; exchange contracts are product + YYMM (for example `A2610`, not gold) | no `AU` quote (gold is Shanghai market 30) | 4 |
| 28 | `郑州商品`; exchange contracts are product + YYMM | no quote for the test `TA610` spelling; enumerate before use | 4 |
| 31 | `香港主板`; five-digit numeric HK codes | `00700` 腾讯控股 | 9 |
| 48 | `香港创业板`; five-digit numeric HK codes | `08083` returned zero in the closed snapshot; code convention is still 5 digits | 9 |
| 74 | `美国股票`; ticker strings, including mixed ETF/index content | `AAPL` | 9 |
| 10 / 11 | `基本汇率` / `交叉汇率` goods rows; pair strings without slash | market 10: `USDCNH`, `USDCNY`; market 11 test symbols were zero | 9 |
| 16 / 17 / 18 | COMEX / NYMEX / CBOT goods rows; product + YYMM | test aliases `GC`, `CL`, `ZC` were zero; enumerate the exchange contracts | 4 |
| 38 | `宏观指标` goods row | no quote for the placeholder `GDP`; treat as indicator/table data | 9 |
| 46 | `上海黄金` goods row, not the quote market for the AU contracts above | use market 30 for `AU2610` | 4 |
| 62 / 69 / 70 | CSI / Huazheng / extended-sector index goods rows | market 62 quote `000300` = 4317.254883; 69/70 placeholder was zero | 9 |
| 75 | international-index category label in a source map, but no quote for tested symbols | no `HSI`, `SPX`, `DJI`, `IXIC`, `N225`, `A50` quote | 9 |

The independently observed index mapping is market **27** (HK indices): HSI,
HSTECH and the Hang Seng family. A separate 2026-09 full enumeration recorded
331 market-27 instruments (`HSI`, `VHSI`, `HZ50xx`, `CES100`/`CES120`); this is
read-only sibling evidence, not a claim from the three-page run above. The
same sibling enumeration records US market 74 as a mixed security universe
(stocks, ETFs and indices), so do not infer asset class from `category` alone.

## Correct codes, quotes and five-day K-lines

| Instrument | Correct `(market, code)` | live quote on 2026-10-09 close | five daily bars |
| --- | --- | ---: | --- |
| CSI300 futures | `(47, IF2610)` main/current; `(47, IFL0)` main alias; `(47, IFL1)` next-month alias | 4307.799805; alias IFL0 identical | 2026-09-28..2026-10-09 (holiday gaps), latest close 4307.799805 |
| CSI50 futures | `(47, IH2610)`; `(47, IHL0)` / `IHL1` aliases | 2802.600098 | contract enumeration verified; quote path is 47 |
| CSI500 futures | `(47, IC2610)`; `(47, ICL0)` / `ICL1` aliases | 7233.600098 | contract enumeration verified; quote path is 47 |
| CSI1000 futures | `(47, IM2610)`; `(47, IML0)` / `IML1` aliases | 7142.799805 | contract enumeration verified; quote path is 47 |
| Treasury futures | `(47, TYYMM)`, `(47, TFYYMM)`, `(47, TSYYMM)`, `(47, TLYYMM)` | instrument rows include `T2612`, `TF2612`, `TS2612`, `TL2612`; use the listed delivery month, not `T0` | category 4 |
| Gold | `(30, AUYYMM)`, e.g. `(30, AU2610)` / `(30, AU2612)` | 903.739990 / 908.500000 | five bars returned for AU2610 |
| Hang Seng / HSTECH | `(27, HSI)` / `(27, HSTECH)` | market 12/75 probes were zero; market 27 is the verified index market | category 9 |
| S&P 500, Nasdaq, Dow, Nikkei, FTSE China A50 | enumerate market 27/74/12 first; do not hard-code the zero test symbols | no non-zero quote in this run; code names vary by TDX index feed | category 9 |
| USD/CNH, USD/CNY | `(10, USDCNH)`, `(10, USDCNY)` | 6.693140 / 6.692400 | five daily FX bars for USDCNH |

The five bars returned by live TDX for `(31,00700)`, `(74,AAPL)`, `(47,IF2610)`,
and `(30,AU2610)` are retained in the probe output. TDX rows are ordered old to
new. HK/US daily bars use category 9; futures and Shanghai gold use category 4.

Cross-checks against public feeds (same 2026-10-09 close):

* Tencent `hk00700`: 424.800 HKD at `2026/10/09 16:08:14`; TDX `(31,00700)`
  424.800018 (float32 representation), pre-close 411.400024.
* Tencent `usAAPL`: 336.64 USD at `2026-10-09 16:00:01`; TDX `(74,AAPL)`
  336.640015, pre-close 340.420013.
* Sina `nf_IF2610`: 4307.800, volume 36,158, open interest 50,002,
  pre-settlement 4294.000; TDX agrees within float32 rounding.
* Sina `nf_IH2610`, `nf_IC2610`, and `nf_IM2610` agree respectively at
  2802.600, 7233.600, and 7142.800, including volume/open interest.
* Sina `nf_AU2610`: 903.740, volume 60; TDX `(30,AU2610)` 903.739990,
  volume 60. Sina `fx_usdcnh` reported 6.692700 at its 2026-10-10 04:59:59
  server timestamp, so it is not a same-close value and is not used as a
  quote-equality claim. The requested Sina USD/CNY symbol was empty.

## Units and field meaning

The quote response fields are `pre_close`, `open`, `high`, `low`, `price`,
`volume` (total volume), `current_volume`, `inner_volume`, `outer_volume`,
`open_interest` (持仓), then five bid/ask prices and volumes. Futures volume is
contracts/lots, not shares; open interest is contracts. The quote's
`pre_close` is the previous settlement for futures (Sina confirms IF2610
4294.000), while `price` is the last/traded price. In daily futures K-lines,
the row's `volume` is contracts, `position` is open interest, and the fifth
float (`price` in the wire parser, called `settlement` by the probe) is the
settlement price. HK transactions are encoded in thousandths and must be
divided by 1000; quote and K-line prices otherwise arrive as float32 in the
instrument's displayed unit. Do not treat the raw K-line `amount` slot as a
reliable amount for futures: the protocol overlays a float/position field.

Category 9 daily timestamps are `YYYY-MM-DD`; category 4 daily timestamps are
also dates. Categories 7/8 are minute bars encoded as a packed day plus
minutes; category 8 is the observed 1-minute choice in the sibling client.
Bars are exchange-local feed dates, not UTC instants; persist the market
session timezone separately.

## Delay verdict

The login response contains `server_name` and `description`, but no delay
minutes, server time, or explicit `delay` text. `TDX延时全` is therefore a
server-name marker, not a quantified delay contract. On Saturday, the same
`(31,00700)`, `(74,AAPL)`, `(47,IF2610)`, `(30,AU2610)`, and FX values were
identical byte-for-byte (within float decoding) on `TDX_DS` and both
`TDX延时全` hosts, as expected for a closed market. This does not prove
real-time behavior during an open session.

Classify the hosts operationally as:

* marker `TDX_DS`: **real-time candidate**, 113.45.175.47;
* marker `TDX延时全`: **delayed candidate**, 150.158.20.127 and
  139.9.191.175;
* blank marker: **unknown**, 106.52.170.195 (also one fewer category and
  572 fewer instruments).

Do not publish a numeric delay or call the marker a guarantee until an open
session comparison records a quote timestamp/server-time field.

## Recommended research-only capabilities

Prioritize `tdx_ex.instrument_catalog` (instrument grain; global pages filtered
by market), `tdx_ex.quote_snapshot` (symbol grain; HK/US/FX/futures),
`tdx_ex.bars_daily` and `tdx_ex.bars_minute` (symbol x session),
`tdx_ex.derivatives_contracts` (contract grain for CFFEX/SHFE/DCE/CZCE), and
`tdx_ex.delay_marker` (host grain, marker plus observed comparison timestamp).
Add `tdx_ex.ticks_session` only after its units and date semantics are tested.
Keep all values replay/research-only with `observed_at`, `available_at`, host
and server marker; no result may feed a live threshold or order path without a
separate promotion record.
