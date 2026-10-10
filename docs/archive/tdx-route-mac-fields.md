# MAC dynamic fields and route reconciliation

Research-only probe record for `121.36.248.138:7709`.  The wire registry is
`quant-service/app/datasources/sources/tdx_mac_fields.py`; it contains all 160
bitmap positions (`0x00` through `0x9f`).  The server returns four bytes per
set bit in ascending bit order.  Requests must be split into small bitmaps:
some MAC hosts silently cap the number of dynamic values in one response.

## Registry and reconciliation

`MAC_FIELDS` is the authoritative full table (`bit`, name, format, unit,
canonical key, confidence, status, capability IDs, note).  The following table
lists the fields with a trusted or potentially useful business interpretation,
and the fields delta-3 Q4/Q5 closed as UNKNOWN.  The other entries of the
registry carry gotdx's name and `NO_REFERENCE`; a position with no entry is
named `bit_0xNN`, has no canonical key and is `NO_REFERENCE`.  A row whose name
is `bit_0xNN` is an UNKNOWN field: gotdx guesses a meaning, no reconciliation
supports it, so it keeps its wire name, has no unit or canonical key and binds
to no capability.

| Bit | Name | Format / unit | Status | Capability IDs |
| --- | --- | --- | --- | --- |
| 00-04 | pre_close, open, high, low, close | float32 / yuan | MATCH (daily bars) | `quote.watch_snapshot`, `auction.open_snapshot` |
| 05 | vol | uint32 / lots | MATCH (daily bars) | `quote.watch_snapshot` |
| 06 | vol_ratio | float32 / ratio | NO_REFERENCE | |
| 07 | amount | float32 / yuan | MATCH (daily bars) | `quote.watch_snapshot`, `flow.stock_daily` |
| 08-09 | inside_volume, outside_volume | uint32 / shares | NO_REFERENCE | |
| 0a-0d | total_shares, float_shares, eps, net_assets | float32 | NO_REFERENCE | |
| 11-12 | bid_price, ask_price | float32 / yuan | NO_REFERENCE | |
| 13-14 | server_update_date, server_update_time | uint32 | NO_REFERENCE | |
| 16 | bit_0x16 | int32 | UNKNOWN (delta-3 Q5) | |
| 1b | turnover | float32 / percent | MATCH when float shares are trusted | `quote.watch_snapshot` |
| 1d | bit_0x1d | float32 | UNKNOWN (delta-3 Q5) | |
| 20-21 | buy_price_limit, sell_price_limit | float32 / yuan | MATCH (pre-close and board ratio) | `limits.ladder`, `limits.stock_anomaly_reason`, `limits.prices` |
| 24 | pre_iopv | float32 / yuan | NO_REFERENCE (after the 2026-10-09 close: 510300 0.0, 159915 305.58; scripts/data/tdx_mac_iopv_2026-10-10_mac.json) | |
| 25-26 | speed_pct, avg_price | float32 | NO_REFERENCE / MATCH (amount/vol) | `quote.watch_snapshot` |
| 27 | iopv | float32 / yuan | MATCH on ETF 510300 | `fund.iopv` |
| 38 | main_net_amount | float32 / yuan | NO_REFERENCE (provider-defined main_in - main_out, yuan; delta-3 Q4) | `flow.stock_daily` |
| 3c | ytd_pct | float32 / percent | MATCH against bar closes where window exists | `quote.watch_snapshot` |
| 40-41 | mtd_pct, change_1y_pct | float32 / percent | MATCH against bar closes where window exists | `quote.watch_snapshot` |
| 43-47 | change_3d_pct, change_60d_pct, change_5d_pct, change_10d_pct, prev2_change_pct | float32 / percent | MATCH for computable windows; otherwise NO_REFERENCE | `quote.watch_snapshot` |
| 57 | open_amount | float32 / yuan | MATCH when auction rows exist | `auction.open_snapshot` |
| 58 | annual_limit_up_days | int32 / days | MATCH against a daily-bar recount (limit-up days in the calendar year, not a rolling window; delta-3 Q5) | |
| 59 | bit_0x59 | uint32 | UNKNOWN (delta-3 Q5) | |
| 5c | close_streak | int32 / days | MATCH against 60 daily bars (rising +n, falling -n) | `quote.watch_snapshot` |
| 5d-5e | bit_0x5d, bit_0x5e | uint32 | UNKNOWN (delta-3 Q5); the board aggregates that matched are 88 and 8b | |
| 66-67 | auction_buy_limit, auction_sell_limit | float32 / yuan | MATCH when auction rows exist | `auction.open_snapshot` |
| 6b | main_net_amount_copy | float32 / yuan | NO_REFERENCE (equals 38: provider-defined main_in - main_out, yuan; delta-3 Q4) | `flow.stock_daily` |
| 6c-72 | bit_0x6c, bit_0x6d, bit_0x6e, bit_0x6f, bit_0x70, bit_0x71, bit_0x72 | float32 | UNKNOWN (delta-3 Q4) | |
| 73 | ddx | float32 / ratio | NO_REFERENCE (plausible as DDX; delta-3 Q4) | `flow.stock_daily` |
| 74-76 | bit_0x74, bit_0x75, bit_0x76 | float32 | UNKNOWN (delta-3 Q4) | |
| 7a | bit_0x7a | float32 | UNKNOWN (delta-3 Q5) | |
| 88-8b | up_count, down_count | uint32 / count | MATCH against board members | `sector.index_quote` |
| 90-96 | change_at_1000, change_at_1030, change_at_1100, change_at_1130, change_at_1330, change_at_1400, change_at_1430 | float32 / percent | MATCH (sampled intraday change: equals a same-day 1-minute bar at or next to the named time, so slot equality is not expected; delta-3 Q5) | |

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
