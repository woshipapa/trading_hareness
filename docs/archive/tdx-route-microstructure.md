# Legacy TDX microstructure route

This is a research-only protocol note.  The implementation is in
`quant-service/app/datasources/sources/tdx_microstructure.py`; it subclasses
the existing stdlib `TdxClient` so setup, timeout, response framing and zlib
handling remain shared.  Capability `microstructure.minute_series` was added
for 0x0fb4 minute-series data; 0x0537/0x0feb minute data and unusual/top-board
capabilities remain research-only.

## Wire commands observed

The request and response layouts were read from the current `bensema/gotdx`
and `Bit-Shine/zytdx` protocol sources in a temporary directory, with the
opcode names cross-checked against both projects' `TdxProtocol.md` files.
The row counts below are from `scripts/verify-tdx-microstructure.py` on
2026-10-10 Asia/Shanghai for session `2026-10-09`, using both hosts.  The
second host is retained as the historical tick-only control described by the
existing client, although it returned the same microstructure responses in
this probe.

| command | opcode | observed hosts | rows / shape | units |
| --- | ---: | --- | --- | --- |
| volume profile (`成交量分布`) | `0x051a` | `117.34.114.13`, `60.191.117.167` | 29 profiles for 000001.SZ; 1241 for 600519.SH, plus quote header and 3+3 levels | price yuan; volume/buy/sell lots; amount yuan (float32) |
| minute series (`历史分时`) | `0x0fb4` | both | 240 | price yuan; volume lots; `unknown` raw signed field; indexed by minute (09:31..11:30, 13:01..15:00) |
| auction (`集合竞价`) | `0x056a` | both | 77 for 000001.SZ; 131 for 600519.SH | price yuan; matched/unmatched values are raw server units; unmatched side B/S |
| unusual (`主力监控`) | `0x0563` | both | 5 with request count 5 | code/market; event time; event type; decoded description/value where known; unknown types kept as hex in `payload_raw` |
| top board (`排行榜`) | `0x053f` | both | 27 with size 3 (9 categories x 3) | price/value float32; category-specific value semantics |
| minute data (`分时`) | `0x0537` | both | 240 | price/average yuan; volume lots |
| history minute data (`历史分时`) | `0x0feb` | both | 240 | price/average yuan; volume lots |

The minute-series (0x0fb4) and minute-data (0x0537/0x0feb) records have no
timestamp field on the wire, so the parser computes a minute index from row
position.  Standard equity prices use a 100x wire scale; ETF prefixes
`15/51/56/58` use 1000x, matching the upstream parser.

## Correctness evidence

Synthetic fixture tests are in
`quant-service/tests/test_datasource_tdx_microstructure.py` (builders,
varints, cumulative prices, signed auction imbalance, unusual records, top
board records, and both minute response headers).  The live probe verified
that both listed hosts accepted all seven requests and returned decodable
rows.  A profile delta normalization was required in production data: some
profile price deltas are unsigned 32-bit two's-complement values; those now
decode to normal prices (for example 000001.SZ starts 11.78, 11.81, 11.80).

The requested cross-checks were run against the existing `TdxClient.ticks`:

* History-minute volume did **not** exactly match tick volume on this capture:
  000001.SZ was 1,078,106 vs 1,078,143 lots; 600519.SH was 35,111 vs 35,113.
  This is recorded as a mismatch, not rounded away or promoted to a trusted
  bar source.
* The auction stream ended at `09:24:57` for these symbols; it had no literal
  `09:25` row.  The last pre-09:25 auction price matched the 09:25 tick price
  for 000001.SZ, but the cumulative auction quantity is not the same unit as
  the tick print volume.  The verifier therefore reports the price/row
  comparison and does not claim a full quantity match.

These observations are provider evidence only.  They are not a promotion
record and do not authorize a live threshold or order path.

## Catalog capability implications

No catalog or binding was edited.  The decoded data could support the
following existing capability reviews after a separate evidence/promotion
decision:

* `ticks.session`: history-minute volume and history orders are related
  microstructure evidence, but are not a replacement for the existing
  tick-print feed until the volume discrepancy is explained.
* `auction.history_0925`: the auction stream provides an intraday curve and a
  last pre-09:25 point; its current response does not expose a literal 09:25
  record on the observed session.
* `bars.minute`: minute prices/volume are available, but the exact volume
  cross-check currently fails, so this should remain unbound.
* `limits.anomaly_tape`: unusual records could back this shape, subject to
  event-type coverage and freshness checks.
* `limits.*`: top-board rankings are related evidence for pool/ranking views,
  not a direct replacement for limit-up, broken-board, or limit-down pools.

New capability ids would be clearer for `microstructure.volume_profile`,
`microstructure.history_orders`, `microstructure.auction_curve`,
`microstructure.unusual`, and `microstructure.top_board`; adding them belongs
in a separate catalog change with tests and an explicit promotion decision.
