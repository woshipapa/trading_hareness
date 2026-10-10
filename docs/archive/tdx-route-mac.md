# TDX MAC route

## Protocol findings

`bensema/gotdx` (MIT) documents a separate MAC service using a 12-byte little-endian request header and 16-byte response header. Responses are optionally zlib-compressed. The implemented opcodes are board list `0x1231`, board members/member quotes `0x122c`, single quote `0x122d`, batch symbol quotes `0x122b`, and unified K-line `0x122e`. The MAC host lists in the checkout were `121.36.248.138`, `123.60.47.136`, `121.37.207.165` on port 7709, plus extended hosts `116.205.135.205` and `121.37.232.167` on 7727.

## Implementation

`quant-service/app/datasources/sources/tdx_mac.py` is an isolated stdlib client with pure request builders/parsers and a blocking failover client. Every catalog binding of the source is UNSUPPORTED, so the resolver cannot route to it: the module is research evidence. `scripts/verify-tdx-mac.py` is a read-only probe for board data and `000001.SZ`/`600519.SH` quotes and K-lines.

## Machine observation

On 2026-10-09 from this machine, `python3 scripts/verify-tdx-mac.py` completed against `121.36.248.138:7709`. The board-code transform is required: `881376 -> 21376`; that returned 10 members and 10 dynamic member-quote rows. Board-type pagination returned: type 0 = 128 (`881376`, `881373`, `881106`), type 1 = 345 (`881377`, `881383`, `881110`), type 2 = 0, type 3 = 269 (`880710`, `880692`, `880668`), type 4 = 158 (`880842`, `880812`, `880963`), type 5 = 32 (`880231`, `880220`, `880232`), type 6 = 397 (`881376`, `880710`, `881373`).

Batch quotes returned one row for each `000001.SZ` and `600519.SH`. For `000001.SZ`, decoded fields were close `11.59`, pre-close `11.78`, high `11.89`, low `11.58`, volume `1078105`, volume ratio `1.0830`, amount `1,259,717,120`, turnover `0.55556`, limit-up `12.96`, limit-down `10.60`, speed `-0.0862`. The batch volume exactly matches the legacy client volume value reported as lots; daily MAC K-line volume is shares (for example `104535744.0` versus legacy `1045357` lots), so the two fields must not be conflated. Fields absent from the bitmap remain absent; exchange date/time are not invented when not sent.

The unreliable single-symbol `0x122d` method was removed from the operational probe: its apparent price/close equals pre-close (`11.78`) instead of the real last (`11.59`). Batch `0x122b` is the supported quote path. MAC daily bars returned 5 requested rows for both symbols. Their last five closes matched legacy host `117.34.114.13:7709` for both `000001.SZ` (`11.30, 11.35, 11.57, 11.78, 11.59`) and `600519.SH` (`1243.88, 1235.58, 1258.62, 1255.79, 1263.00`).

Remaining MAC probes on the same host reported auction 58 rows, tick charts 1200 ticks, K-line offset returned a one-byte empty response (0 parsed entries), market monitor 500 rows, and symbol-belong-board JSON 14 rows. The "capital-flow JSON 14 rows" first listed here were those belong-board rows: capital flow is a separate query (`0x1218` head=2, `Stock_ZJLX`) and its answer is described in `docs/archive/tdx-q-flow.md`. Both extended hosts `116.205.135.205:7727` and `121.37.232.167:7727` failed the MAC handshake. Go was unavailable, so gotdx row-for-row comparison was skipped.

## Live check of the six adapters, 2026-10-10 13:04-13:08 UTC (Mac egress)

Run by Claude against the MAC pool. 121.36.248.138:7709 answered every call. It was a Saturday, so the values are the 2026-10-09 session.

- **Quotes and limits (0x122b).** Four symbols (600519.SH, 000001.SZ, 300750.SZ, 688981.SH) gave four quote rows and four limit rows; the strict decode, which rejects trailing bytes, held on both answers.
  - 600519.SH: exchange_time 2026-10-09 15:30:02+08:00, from bits 0x13/0x14. Pre_close 1,255.79, close 1,263.00. Limits 1,381.37 / 1,130.21, which are pre_close x1.1 / x0.9, with trade_date 2026-10-09.
  - 000001.SZ: close 11.59, limits 12.96 / 10.60.
- **Unknown codes are left out, not answered with placeholders.** One request for 600000-600079.SH returned 57 rows:
  - every row was a requested code, in request order, with no repeats;
  - the 23 codes left out include 600001, 600002, 600003 and 600005, all delisted.

  At 13:04 the positional R1 check raised on all three hosts. After 515545d4, the 81-symbol call (13:07) returned 58 rows with coverage 0.716 and named the 23 missing symbols.
- **Board catalog.** 932 boards: type 0: 128, 1: 345, 3: 269, 4: 158, 5: 32. Members of 880710 (type 3): 13.
- **Bars.** Daily and minute bars of 600519.SH: 5 rows each.

Evidence:
- `scripts/data/tdx_mac_adapters_live_2026-10-10_mac.json` (13:04);
- `scripts/data/tdx_mac_adapters_live_2026-10-10_mac_after_omission_fix.json` (13:07);
- `scripts/data/tdx_mac_batch_omission_2026-10-10_mac.json` (13:08).
