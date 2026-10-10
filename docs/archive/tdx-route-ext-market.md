# TDX extended-market route (7727)

Status: protocol implementation and parsers verified against live evidence,
2026-10-10. See “Live check, 2026-10-10” section below for latest data.

## Wire contract

The extended service uses the normal TDX response envelope but a distinct
request prefix:

```
request:  [0] 01 | [1:5] uint32 client marker | [5] control
          [6:8] uint16 body length | [8:10] same length
          [10:12] uint16 method | [12:] payload
response: b1 cb 74 00 | control | uint32 sequence | reserved | method
          uint16 compressed length | uint16 uncompressed length | body
```

Lengths include the two-byte method. Payloads are little-endian. A compressed
response is zlib data; an uncompressed response has equal length fields.

`EX_SETUP_PAYLOAD` is the public 80-byte `ExSetupCmd1` hello (eight repetitions
of `1f32c6e5d53dfb41`, followed by
`cce16dffd5ba3fb8cbc57a054f7748ea`). The historical setup header has marker
`0x65480101` and method `0x2454`, so the complete frame is 92 bytes. Ordinary
requests use a zero marker. The separate public login request is also method
`0x2454`, with the 80-byte payload:

```
e5bb1c2fafe525941f32c6e5d53dfb415b734cc9cdbf0ac92021bfdd1eb06d22
d008884c1611cb1378f6abd824d899d21f32c6e5d53dfb411f32c6e5d53dfb41
a9325ac935dc0837335a16e4ce17c1bb
```

Open-source clients use both forms: `pytdx`/`gotdx` issue the marked setup or
the login request, while newer clients issue the marked setup and ignore its
ack before requesting markets. No credential or broker session is involved.

## Commands and parsers

`tdx_ex_market.py` implements setup/login, count (`0x23f0`), category list
(`0x23f4`), paged instrument list (`0x23f5`), and single quotes
(`0x23fa`). Category-list rows are 64 bytes with the broad type at offset 0 and
the market id at offset 33; instrument rows are 64 bytes with only the first 40
bytes meaningful. Quote rows are 300 bytes; the amount field is a float and
pre_close is the prior settlement price (for futures, the settlement date of
the prior trading day).

Daily K-line category is market-dependent: use category `9` for HK/US equities
and category `4` for futures. K-line rows carry market_id and code; for futures
the amount field holds open-interest bits.

## Market IDs

The byte returned by `EXCATEGORYLIST` is the venue/market ID. The companion
goods byte is the broad type (`1` stock, `2` HK, `3` futures, `4` FX, `5`
index, `8` fund, `12` option, `13` US, `14` Germany, `15` Singapore). Known
market IDs used by the source clients are:

| ID | Meaning | Useful catalog scope |
| ---: | --- | --- |
| 10/11 | basic/cross FX | `fx/spot`, pair grain |
| 12 | international indices | `global/index`, instrument grain |
| 13 | US (legacy) | `us/security`, symbol grain |
| 16/17/18 | COMEX/NYMEX/CBOT futures | `us/futures`, contract grain |
| 23/24/25/26 | HK financial/stock futures/options | `hk/derivatives`, contract grain |
| 27 | HK indices | `hk/index`, symbol grain |
| 28/29/30 | Zhengzhou/Dalian/Shanghai futures | `cn/futures`, contract grain |
| 31 | HK main board | `hk/stock`, symbol grain |
| 42 | futures indices | `cn/futures-index`, symbol grain |
| 47 | CFFEX futures (IF/IH/IC/IM) | `cn/futures`, contract grain |
| 48 | HK GEM/alternate | `hk/stock`, symbol grain |
| 49 | HK funds/ETFs | `hk/fund`, symbol grain |
| 60 | main futures contracts | `cn/futures`, contract grain |
| 66/67 | Guangzhou futures/options | `cn/futures`, contract grain |
| 71 | HK stocks/Connect feed | `hk/stock`, symbol grain |
| 74 | US stocks (current) | `us/stock`, symbol grain |
| 75 | international indices (current) | `global/index`, symbol grain |

Additional table entries in `MARKET_IDS` preserve the source client's known
open-end-fund, NEEQ, gold, treasury, CSI, risk-control, Germany/Singapore and
dark-pool IDs without guessing an instrument's asset class.

## Probe evidence

See "Live check, 2026-10-10" section below for current evidence. An earlier
exploration run (archived) attempted six distinct `:7727` hosts from the supplied
pools but ran out of budget before completing. The successful run is documented
with live data.

## What the platform lacks

The existing platform has A-share public TDX/HTTP bars and ticks, but no
provider contract for HK/US securities, overseas indices, FX pairs, futures
contracts/options, cross-market instrument discovery, five-level depth outside
the existing Tencent snapshot, or historical extended-market transaction/tick
charts. Suggested research-only capability keys are:

* `tdx_ex.instrument_catalog` (instrument grain, market/category scope);
* `tdx_ex.quote_snapshot` and `tdx_ex.order_book` (symbol grain, HK/US/FX/
  futures scopes);
* `tdx_ex.bars_daily` and `tdx_ex.bars_minute` (symbol × session grain);
* `tdx_ex.ticks_session` and `tdx_ex.ticks_history` (trade grain, bounded by
  date/count);
* `tdx_ex.derivatives_contracts` (contract grain, exchange scope);
* `tdx_ex.table_snapshot` (opaque table/chunk grain, replay-only until schema
  decoding is independently validated).

All values should remain raw/research evidence with explicit `observed_at` and
`available_at`; nothing from this route should feed a live threshold or order
path without a separate promotion record.

## Live check, 2026-10-10 (Claude, Mac egress)

`python scripts/verify-tdx-ex-market.py` ran at 08:49 UTC on Saturday 2026-10-10 (raw output:
`scripts/data/tdx_ex_market_verify_2026-10-10_mac.jsonl`).
- The first candidate, 47.107.228.47:7719, timed out.
- The next one, 112.74.214.43:7727, completed setup and login, then answered:
  - count 107,163 instruments;
  - 51 categories;
  - 6,400 instrument rows in the bounded scan of 64 pages.

The category rows confirm delta 2, D6:
- The first byte is the broad type: 2 HK, 3 futures, 5 index, 13 US, 14 Germany, 15 Singapore.
- The byte at offset 33 is the market id that requests use:
  - 31 HK main board, 48 HK GEM, 49 HK funds, 98 HK dark pool;
  - 47 CFFEX, 60 main contracts, 30/29/28 SHFE/DCE/CZCE;
  - 74 US stocks, 73 Germany, 78 Singapore;
  - 12 international indices.

The parser reads them that way since 18bdb8eb.

| sample | market id | price | pre-close | last two daily closes |
| --- | ---: | ---: | ---: | --- |
| 00700 | 31 | 424.80 | 411.40 | 2026-10-08 411.40, 2026-10-09 424.80 |
| AAPL | 74 | 336.64 | 340.42 | 2026-10-08 340.42, 2026-10-09 336.64 |
| IF2610 | 47 | 4,307.80 | 4,294.00 | 2026-10-08 4,304.00, 2026-10-09 4,307.80 |

- 00700 and the IF2610 price equal the values recorded in delta 2, D3.
- Prices arrive as float32 (424.8000183105469). A consumer must round them to the instrument's
  precision.
- The other host groups, the owner egress and an intraday cadence check were not part of this
  run, so all context.* bindings stay UNSUPPORTED.

## Re-check after the gotdx quote layout fix, 2026-10-10 10:44 UTC

Run by Claude from the Mac egress: `scripts/verify-tdx-ex-market.py` at the commit after 012dd785 (quote open interest kept for futures only). 47.107.228.47:7719 failed; 112.74.214.43:7727 answered with count 107,163 and 51 categories. The run was on a Saturday, so the values are the 2026-10-09 session. Evidence: `scripts/data/tdx_ex_market_verify_2026-10-10_mac_gotdx_layout.jsonl`.

- IF2610 (market 47):
  - The quote gives price 4,307.8, pre_close 4,294.0 (the prior settlement), open interest 50,002, best bid 4,307.6 x 1 and best ask 4,307.8 x 2.
  - Its volume of 36,158 equals the 2026-10-09 bar's volume.
  - The bars' amount slot holds open interest: 50,020 on 2026-10-08 and 50,002 on 2026-10-09. The last word holds the settlement price: 4,294.0 and 4,313.6.
- AAPL (market 74):
  - The best bid is 335.55 x 201 and the best ask 336.09 x 287.
  - The quote amount 12,679,452,672 equals the 2026-10-09 bar's amount exactly.
- 00700 (market 31):
  - The quote amount 8,610,387,968 equals the 2026-10-09 bar's amount exactly.
  - The order book is all zero.
  - The open-interest word reads 3,209,381,148, so the decoder keeps open interest for futures markets only.
- All 15 bars (5 per symbol) satisfy low <= open, close <= high.
- Bar volume units differ by market:

  | symbol | quote volume | 2026-10-09 bar volume_raw |
  |---|---|---|
  | 00700 | 20,422,500 | 2,042 |
  | AAPL | 37,881,199 | 378,811 |
  | IF2610 | 36,158 | 36,158 |

  context.bars_daily therefore keeps `volume_raw`.
