# MAC dynamic fields and route reconciliation

Research-only probe record for `121.36.248.138:7709`.  The wire registry is
`quant-service/app/datasources/sources/tdx_mac_fields.py`; it contains all 160
bitmap positions (`0x00` through `0x9f`).  The server returns four bytes per
set bit in ascending bit order.  Requests must be split into small bitmaps:
some MAC hosts silently cap the number of dynamic values in one response.

## Registry and reconciliation

`MAC_FIELDS` is the authoritative full table (`bit`, name, format, unit,
canonical key, confidence, status, capability IDs).  The following table lists
every field with a trusted or potentially useful business interpretation; all
other positions are deliberately named `bit_0xNN`, have no canonical key and
are `NO_REFERENCE`.

| Bit | Name | Format / unit | Status | Capability IDs |
| --- | --- | --- | --- | --- |
| 00-04 | pre_close, open, high, low, close | float32 / yuan | MATCH (daily bars) | `quote.watch_snapshot`, `auction.open_snapshot` |
| 05 | vol | uint32 / lots | MATCH (daily bars) | `quote.watch_snapshot`, `flow.stock_daily` |
| 06 | vol_ratio | float32 / ratio | NO_REFERENCE | |
| 07 | amount | float32 / yuan | MATCH (daily bars) | `quote.watch_snapshot`, `flow.stock_daily` |
| 08-09 | inside_volume, outside_volume | uint32 / shares | NO_REFERENCE | |
| 0a-0d | total_shares, float_shares, eps, net_assets | float32 | NO_REFERENCE | |
| 11-12 | bid_price, ask_price | float32 / yuan | NO_REFERENCE | |
| 13-14 | server_update_date, server_update_time | uint32 | NO_REFERENCE | |
| 16 | board_strength | int32 / count | NO_REFERENCE | |
| 1b | turnover | float32 / percent | MATCH when float shares are trusted | `quote.watch_snapshot` |
| 20-21 | buy_price_limit, sell_price_limit | float32 / yuan | MATCH (pre-close and board ratio) | `limits.ladder`, `limits.stock_anomaly_reason`, `limits.prices` |
| 24 | pre_iopv | float32 / yuan | MATCH on ETF 510300 | `fund.nav` |
| 25-26 | speed_pct, avg_price | float32 | NO_REFERENCE / MATCH (amount/vol) | `quote.watch_snapshot` |
| 27 | iopv | float32 / yuan | MATCH on ETF 510300 | `fund.nav` |
| 38 | main_net_amount | float32 / yuan | NO_REFERENCE (provider semantics) | `flow.stock_daily` |
| 3b-3c | change_20d_pct, ytd_pct | float32 / percent | MATCH against bar closes where window exists | `quote.watch_snapshot` |
| 40-41 | mtd_pct, change_1y_pct | float32 / percent | MATCH against bar closes where window exists | `quote.watch_snapshot` |
| 43-47 | change_3d/60d/5d/10d/prev2_pct | float32 / percent | MATCH for computable windows; otherwise NO_REFERENCE | `quote.watch_snapshot` |
| 57 | open_amount | float32 / yuan | MATCH when auction rows exist | `auction.open_snapshot` |
| 5c | close_streak | int32 / days | MATCH against 60 daily bars (rising +n, falling -n) | `quote.watch_snapshot` |
| 5d-5e | limit_up_count, limit_down_count | uint32 / count | MATCH for board member aggregate | `sector.index_quote` |
| 66-67 | auction_buy_limit, auction_sell_limit | float32 / yuan | MATCH when auction rows exist | `auction.open_snapshot` |
| 6b-72 | main/retail net amount windows | float32 / yuan | NO_REFERENCE (no trusted flow source) | `flow.stock_daily` |
| 73-76 | DDX, DDY, DDZ, DDF | float32 / ratio | NO_REFERENCE | `flow.stock_daily` |
| 7a | auction_vol_ratio | float32 / ratio | NO_REFERENCE | `auction.open_snapshot` |
| 88-8b | up_count, down_count | uint32 / count | MATCH against board members | `sector.index_quote` |
| 90-96 | intraday change snapshots | float32 / percent | NO_REFERENCE without same-day bars | |

The registry marks `MISMATCH` only after a value and an independent reference
are both present.  A provider-only value remains `NO_REFERENCE`; it is never
promoted to a live threshold or order path.

## Unprobed MAC commands

The new module includes request layouts and parsers/building primitives for:

| Opcode | Route | Wire observation / interpretation |
| --- | --- | --- |
| `0x122a` | symbol info | 194-byte summary; fixed offsets for OHLC, volume, amount, turnover, average |
| `0x122f` | transactions | 18-byte prints; date/start/count paging |
| `0x120f` | server info | 68-byte request; response carries two groups of four trading sessions |
| `0x1215` / `0x1217` | file list/download | UNSUPPORTED (delta-3 Q11): every MAC host offers an empty file; no builder or parser is kept |
| `0x1218` head=2 | capital flow | JSON rows: today `[main in, main out, retail in, retail out]`, five-day six-row aggregate |
| `0x1218` | belong-board | query `Stock_GLHQ`; response is JSON board rows, kept separate from flow |
| `0x123d` | auction curve | 16-byte points: seconds, price, matched, unmatched |
| `0x123e` | tick charts | five-day request; response carries dates/pre-closes and 14-byte ticks |
| `0x124a` | K-line offset | eight-byte response (`total`, `returned`); empty responses are not treated as success |
| `0x1237` | market monitor | 32-byte events; `unusual_type` selects event decoding |

`scripts/verify-tdx-mac-fields.py --fixture` exercises every required command
with synthetic bytes.  Live probing is bounded to one connection, five-second
timeout, and the MAC host named above; it exits non-zero when any required
command has no usable rows.

The 2026-10-09 live run returned 30 dynamic rows (six symbols across five
bitmap chunks), 12 rows for each of the six board sort/filter variants, and
usable responses for every opcode.  The file-list response was 42 bytes and
the single bounded download response was 8 bytes with no payload; therefore no
`zhb.zip` member comparison was claimed.  The verifier no longer probes the
file commands.

## Sort/filter notes

Dynamic board-member requests use the corrected `<I9sHIHBB20s` layout and set
bitmap byte 19 bit 0.  `sort_type=14`, `sort_order=1` is the stable default;
the filter byte is copied into bitmap byte 17.  Sort/filter variants remain
research observations until a response is reconciled against an independent
member set; no variant is used by a live strategy.
