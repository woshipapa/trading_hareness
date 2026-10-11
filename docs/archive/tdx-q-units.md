# TDX Q-UNITS: volume, amount and time units

Date of live probe: 2026-10-10 (Saturday, Asia/Shanghai). The last closed
session was 2026-10-09. All network observations below are read-only and use a
five-second timeout. The checked-in probe is
[`scripts/probe-tdx-q-units.py`](../../scripts/probe-tdx-q-units.py).

## Verdicts

1. **Legacy OHLC bars: resolved.** In the `LOGIN_ONE` session, `0x052d`
   category 8/0/1/2/3 records are `date/time + four price varints + two IEEE
   little-endian float32 values`. The first float is volume in **shares** and
   the second is amount in **yuan**. The old parser used `decode_volume` on
   those IEEE bit patterns. `decode_volume` is pytdx's packed exponent/mantissa
   decoder, so it turns a normal float bit pattern into nonsense; a zero
   volume bit pattern can appear as `5.877471754111438e-39`. The corrected
   decoder is the small `decode_legacy_bar` function in the probe (and shown
   below).

   The live comparison used the 2026-10-09 session and all 240 one-minute
   bars. MAC `0x122e` returns a leading pre-close sentinel, hence 241 rows;
   rows 1..240 are shares and divide by 100 to lots. Every symbol had zero
   per-minute differences after scaling and a total difference of zero lots:

   | symbol | legacy rows | legacy shares/100 | MAC rows | MAC shares/100 | per-minute mismatches >1 lot |
   | --- | ---: | ---: | ---: | ---: | ---: |
   | 000001.SZ | 240 | 1,078,106 | 241 | 1,078,106 | 0 |
   | 600519.SH | 240 | 35,111 | 241 | 35,111 | 0 |
   | 300750.SZ | 240 | 541,527 | 241 | 541,527 | 0 |
   | 688981.SH | 240 | 461,045 | 241 | 461,045 | 0 |
   | 510300.SH | 240 | 12,767,331 | 241 | 12,767,331 | 0 |

   Categories 0/1/2/3 were also decoded live with the same record layout and
   returned 240 rows each. Their requested 240-row windows span historical
   dates for the larger periods; the full same-day equality gate is therefore
   applied to category 8.

   ```python
   def decode_legacy_bar(category, body):
       count = struct.unpack_from("<H", body)[0]
       pos, base, rows = 2, 0, []
       for _ in range(count):
           stamp, pos = tdx_protocol._bar_datetime(category, body, pos)
           opens, pos = tdx_protocol.decode_price(body, pos)
           closes, pos = tdx_protocol.decode_price(body, pos)
           highs, pos = tdx_protocol.decode_price(body, pos)
           lows, pos = tdx_protocol.decode_price(body, pos)
           volume, amount = struct.unpack_from("<ff", body, pos); pos += 8
           volume = 0.0 if 0 < volume < 1e-20 else volume
           amount = 0.0 if 0 < amount < 1e-20 else amount
           opening = base + opens
           rows.append((stamp, opening / 1000, (opening + closes) / 1000,
                        volume, amount))
           base = opening + closes
       return rows
   ```

2. **History-minute versus ticks: explained, not a parser loss.** The live
   2026-10-09 comparison for 000001.SZ was 1,078,106 lots in `0x0feb` versus
   1,078,143 in `0x0fb5`/`0x0fc5`; 600519.SH was 35,111 versus 35,113. The
   minute-level investigation found three causes:

   * the first 09:31 bar includes the 09:25 opening-cross print plus the 09:31
     trade (000001: 7,508 + 54,836 lots; 600519: 195 + 1,400);
   * closing-call zeroes are represented by the float32 denormal and must be
     normalized to zero (000001 at 14:59; 600519 at 14:58 and 14:59);
   * the remaining 37 / 2 lots are post-close prints (000001 from 15:11 and
     600519 from 15:18 through 15:30), outside the 240 bars ending at 15:00.

   Therefore the exchange-session daily value is the daily bar/MAC batch quote
   value, not the all-print history-tick sum. For 000001 the daily bar and MAC
   quote are 1,078,105 lots; the minute series is 1,078,106 because of its
   minute boundary convention, and the tick feed includes after-hours prints.
   The same source distinction is confirmed by the 600519 live totals above.

3. **Auction quantity unit: unresolved.** Legacy `0x056a` matched/unmatched
   fields and MAC `0x123d` auction quantities are raw server quantities. The
   observed values do not establish whether they are lots or shares: the
   matched value does not equal the 09:25 tick volume under a single scale
   across the five tested equity/ETF examples. Keep names as raw quantities;
   do not label them `lots` or `shares` in downstream code.

   The legacy auction stream ends at `09:24:57` and has no literal `09:25`
   row. The 09:25 matched print is present in the historical ticks instead.
   The MAC auction response observed in the sibling MAC probe is a separate
   58-row curve; no literal 09:25 row was established there either. This is a
   representation boundary, not evidence of a missing exchange print.

4. **Transaction routes: layout resolved; side semantics resolved.** `0x0fc5`
   returns current transaction rows in chronological server order. `0x0fb5`
   is the historical row shape used by the existing client. `0x0fc6` is the
   history/trans variant: after the `num` varint it adds a little-endian
   uint16 direction field (and omits the extra unknown varint used by 0x0fc5).
   It does not add a new economic quantity. Rows are oldest first after the
   client's page reversal; the final observed row is the session's last print.
   Direction codes are stable across `0x0fc5` and `0x0fc6`: `0=buy`, `1=sell`,
   `2=neutral`, `5=after-hours fixed-price (P)`, `8=call-auction indicative
   (A)`. A price above the prior tick is buy/`B`, below is sell/`S`; equal
   prices remain neutral unless the provider emits one of the special 5/8
   codes. The source-level mapping and synthetic parser tests agree; the live
   2026-10-09 sample contained 2/5/8 exactly as reported, with 8 confined to
   zero-volume auction indications and 5 to post-close prints.

5. **Time convention: resolved.** All times are exchange local time
   `Asia/Shanghai`. Legacy OHLC bar timestamps are the **opening minute label**
   (the first 1-minute row is 09:31 and contains the 09:30/09:31 boundary
   volume); the MAC K-line `seconds` field is seconds since midnight for the
   bar label, not a trade timestamp. History-minute rows are zero-based server
   order with no wire timestamp; ticks carry minute (and, for the extended
   transaction form, seconds) fields. Persisted application timestamps remain
   timezone-aware UTC.

## Unit table

| command / field | unit | scale | time convention |
| --- | --- | ---: | --- |
| legacy `0x052d` category 8/0/1/2/3 volume | shares | 1; divide by 100 for lots | bar label is opening minute, exchange local |
| legacy `0x052d` amount | yuan | 1 | same bar label |
| legacy `decode_volume` fields in quotes/XDXR | packed provider number | pytdx custom decoder | not interchangeable with OHLC float32 fields |
| MAC `0x122b` batch quote volume | lots | 1 | quote snapshot |
| MAC `0x122e` K-line volume | shares | divide by 100 for lots | `seconds` = seconds since midnight for label; leading sentinel is pre-close |
| legacy `0x0feb` history-minute volume | lots | 1 | 240 server-order rows, ending 15:00 |
| legacy `0x0fb5`/`0x0fc5`/`0x0fc6` transaction volume | lots | 1 | print minute; history pages reverse to oldest first |
| legacy `0x056a` matched/unmatched | raw provider quantity | unknown | auction curve ends 09:24:57 in observed response |
| MAC `0x123d` auction quantity | raw provider quantity | unknown | separate MAC auction curve; no 09:25 row established |
| prices in these routes | yuan | wire integer prices /100 (ETF legacy/MAC variants may use /1000) | Asia/Shanghai labels |

## Evidence boundary

Live in this branch: the `LOGIN_ONE` legacy bar decode and five-symbol,
240-minute equality against MAC `0x122e` on 2026-10-10. Read-only sibling
evidence from the same 2026-10-09 close: history-minute/tick minute attribution,
auction row shape and `0x0fc5`/`0x0fc6` route comparisons. The auction quantity
unit remains deliberately unconfirmed; no provider field is promoted to a
threshold or order path.
