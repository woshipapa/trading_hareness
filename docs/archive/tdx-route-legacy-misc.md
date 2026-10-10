# TDX legacy-misc route

This route is research-only. It does not register a provider binding or feed a
live threshold/order path. The implementation is in
`quant-service/app/datasources/sources/tdx_legacy_misc.py`; the bounded probe is
`scripts/verify-tdx-legacy-misc.py`.

## Command table

| Opcode | Request layout | Response rows / units | Hosts | Correctness evidence | Capability ids |
| --- | --- | --- | --- | --- | --- |
| `0x054b` | `<category, sort_type, start, count, reverse, mode=5, filter, 1, 0>`; 20-byte body | sorted quote items; prices yuan, volume lots, amount float32 | 60.191.117.167, 218.75.126.9, 117.34.114.13 | gotdx/zytdx layout; synthetic parser fixture; live probe required | `quote.all_a_snapshot`, `derived.market_sentiment` |
| `0x054c` | mode/reserved/count then repeated `<market, code[6]>` | quote items; same units as `0x054b` | same | shared gotdx quote-item decoder; synthetic builder fixture; probe cross-checks top rows | `quote.all_a_snapshot` |
| `0x0547` | count then `<market, code[6], 22234, 2>`; response XOR `0x93` | encrypted quote items; prices yuan, volume lots | same | gotdx XOR and five-level layout; synthetic implementation | `quote.all_a_snapshot` |
| `0x051d` | `<market, code[6], zero:uint32>` | one index summary; `up_count`/`down_count`, price yuan, volume lots | same | gotdx field walk; synthetic parser coverage | `derived.market_sentiment`, `reference.*` |
| `0x051c` | `<market, code[6]>` | delta-coded momentum values | same | gotdx cumulative decoder; synthetic delta fixture | `derived.market_sentiment` |
| `0x0fc5` | `<market, code[6], start, count>` | transaction rows; time, price delta, lots, direction code | same | gotdx/zytdx request and side-code mapping; existing `TdxClient.ticks` uses 0/1/2/5/8 | `flow.tick_derived` |
| `0x0fc6` | same stock page; history variant adds `<date:uint32>` | transaction rows with explicit little-endian uint16 direction after `num`; 0 BUY, 1 SELL, 2 NEUTRAL | same | gotdx trans parser plus synthetic 16-bit direction fixture | `flow.tick_derived` |
| `0x0fb5` | `<date, market, code[6], start, count>` | historical transaction rows; no `num` field | same | existing history tick parser and gotdx history layout | `flow.tick_derived` |
| `0x0fd1` | `<market, code[6], reserved[28]>` | sampled float32 prices; `pre_close` float32 | same | gotdx layout and synthetic row fixture | `reference.*` |
| `0x052d` | offset bars `<market, code, category, times=1, start, count, adjust, reserved>` | K-line rows; prices and amounts follow existing bar parser | same | opcode alias in gotdx; existing `parse_bars` | `reference.*` |
| `0x0452` | `<start:uint32, count:uint32, one:uint32, zero:uint16>` | 13-byte feature rows `(market, numeric code, float32 p1, float32 p2)` | same | zytdx experimental decoder and synthetic row fixture | `reference.*`, `limits.*` |
| `0x0002` | empty payload | version byte plus GBK/UTF-8 text | same | zytdx server parser; raw length/text always recorded | `news.announcements` |
| `0x000a` | 54 zero bytes | optional date/title/author/content, otherwise raw | same | zytdx announcement parser; raw length/text recorded | `news.announcements` |
| `0x000b` | legacy raw command payload | raw bytes/text only | same | zytdx `TodoB` request; no semantic claim | `reference.*` |
| `0x0fde` | empty payload | raw bytes/text only | same | zytdx raw command; no semantic claim | `reference.*` |
| `0x0015` | empty payload | raw server-info response; latency measured | same | zytdx ping request; compared with `0x054c` latency | `reference.*` |
| `0x0004` | empty payload | date at response offset 6 when present | same | zytdx heartbeat parser; raw length recorded | `reference.*` |

## 0x054b category/sort notes

The checked-in upstream constants give category values `SH A=0`, `SZ A=2`,
`A=6`, `B=7`, `STAR=8`, `BJ=12`, and `ChiNext=14`. The filter bits are
`new=1`, `STAR=2`, `ST=4`, `ChiNext=8`, `BJ=16`; ST is therefore a filter on
the A category rather than a distinct category. The authoritative sort values
are `code=0`, `name=1`, `pre_close=2`, `open=3`, `high=4`, `low=5`, `price=6`,
`bid=7`, `ask=8`, `volume=9`, `amount=10`, `last_volume=11`, `change=12`, and
`change_pct=14`. Turnover, amplitude, volume-ratio and speed are not assigned
numeric values by the referenced implementations and are intentionally not
invented here; the probe can be extended once a host response establishes
their semantics.

`count` is clamped to 500 in the builder. The probe requests 80 rows and must
be run repeatedly with `start += 80` to measure the full ranking. It records
row counts, fields and latency, and compares the top five symbols with a batch
quote response when a host supplies usable rows. No live result is promoted by
this module.

## Probe contract

Run `python3 scripts/verify-tdx-legacy-misc.py`. It uses one connection per
host, a five-second socket timeout, and only the first setup packet. It exits
non-zero if no host returns usable rows for both `0x054b` and `0x054c`; raw
announcement/experimental emptiness is informational. A second cadence sample
was not added because the bounded task does not require waiting 60 seconds.
