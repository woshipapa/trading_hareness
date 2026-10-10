# TDX MAC route

## Protocol findings

`bensema/gotdx` (MIT) documents a separate MAC service using a 12-byte little-endian request header and 16-byte response header. Responses are optionally zlib-compressed. The implemented opcodes are board list `0x1231`, board members/member quotes `0x122c`, single quote `0x122d`, batch symbol quotes `0x122b`, and unified K-line `0x122e`. The MAC host lists in the checkout were `121.36.248.138`, `123.60.47.136`, `121.37.207.165` on port 7709, plus extended hosts `116.205.135.205` and `121.37.232.167` on 7727.

## Implementation

`quant-service/app/datasources/sources/tdx_mac.py` is an isolated stdlib client with pure request builders/parsers and a blocking failover client. It is intentionally not added to datasource bindings or the catalog. `scripts/verify-tdx-mac.py` is a read-only probe for board data and `000001.SZ`/`600519.SH` quotes and K-lines.

## Machine observation

On 2026-10-09 from this machine, `python3 scripts/verify-tdx-mac.py` completed against `121.36.248.138:7709`. The board-code transform is required: `881376 -> 21376`; that returned 10 members and 10 dynamic member-quote rows. Board-type pagination returned: type 0 = 128 (`881376`, `881373`, `881106`), type 1 = 345 (`881377`, `881383`, `881110`), type 2 = 0, type 3 = 269 (`880710`, `880692`, `880668`), type 4 = 158 (`880842`, `880812`, `880963`), type 5 = 32 (`880231`, `880220`, `880232`), type 6 = 397 (`881376`, `880710`, `881373`).

Batch quotes returned one row for each `000001.SZ` and `600519.SH`. For `000001.SZ`, decoded fields were close `11.59`, pre-close `11.78`, high `11.89`, low `11.58`, volume `1078105`, volume ratio `1.0830`, amount `1,259,717,120`, turnover `0.55556`, limit-up `12.96`, limit-down `10.60`, speed `-0.0862`. The batch volume exactly matches the legacy client volume value reported as lots; daily MAC K-line volume is shares (for example `104535744.0` versus legacy `1045357` lots), so the two fields must not be conflated. Fields absent from the bitmap remain absent; exchange date/time are not invented when not sent.

The unreliable single-symbol `0x122d` method was removed from the operational probe: its apparent price/close equals pre-close (`11.78`) instead of the real last (`11.59`). Batch `0x122b` is the supported quote path. MAC daily bars returned 5 requested rows for both symbols. Their last five closes matched legacy host `117.34.114.13:7709` for both `000001.SZ` (`11.30, 11.35, 11.57, 11.78, 11.59`) and `600519.SH` (`1243.88, 1235.58, 1258.62, 1255.79, 1263.00`).

Remaining MAC probes on the same host reported auction 58 rows, capital-flow JSON 14 rows, tick charts 1200 ticks, K-line offset returned a one-byte empty response (0 parsed entries), market monitor 500 rows, and symbol-belong-board JSON 14 rows. Both extended hosts `116.205.135.205:7727` and `121.37.232.167:7727` failed the MAC handshake. Go was unavailable, so gotdx row-for-row comparison was skipped.
