# TDX extended-market route (7727)

“全部主机超时”的结论来自探索时沙箱的网络限制，并不成立，实测结果见 Claude 后续补充的小节。

Status: protocol implementation and synthetic parsers verified; live route not
verified in this environment. The probe is read-only and exits non-zero unless a
host returns both a non-empty category list and instrument rows.

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
(`0x23f4`), paged instrument list (`0x23f5`), single and batch quotes
(`0x23fa`, `0x248a`, `0x23fb`), K-lines (`0x23ff`, `0x2489`), current and
historical tick charts (`0x248b`, `0x248c`), historical transactions (`0x2412`),
and table/detail responses (`0x2422`, `0x2423`). Category-list rows are 64
bytes (`market`, 32-byte name, goods/category byte, two-byte abbreviation);
instrument rows are 64 bytes with only the first 40 bytes meaningful. Quote
rows are 300 bytes for a single quote and 314 bytes for a batch quote. K-line
rows are 32 bytes; the amount field is the float representation at the same
offset used by the protocol's position field, so both raw `position` and the
float `amount` are retained.

Daily K-line category is market-dependent: use category `9` for HK/US equities
and category `4` for futures. Minute categories are `7`/`8`. HK transaction
prices (markets 31/48) are in thousandths and are divided by 1000; other
markets retain the protocol integer price.

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

Command:

```text
PYTHONPATH=quant-service python3 scripts/verify-tdx-ex-market.py
```

The bounded run attempted six distinct `:7727` hosts from the supplied pools
(`112.74.214.43`, `120.25.218.6`, `47.107.75.159`, `47.106.204.218`,
`47.106.209.131`, `119.97.185.5`). Every attempt timed out before usable
category/instrument rows. The two known MACEx addresses
(`116.205.135.205`, `121.37.232.167`) were not retried after that six-host
budget was exhausted. Therefore no live market enumeration, quote, bar, or
TDX/Tencent scale equality is claimed here.

Independent public cross-check baseline (not TDX evidence):
`qt.gtimg.cn/q=hk00700` returned `00700` last `424.800` HKD at
`2026/10/09 16:08:14`. A future successful 7727 probe should compare this
value to market `31`, code `00700` and record the observed raw/decoded units.

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
