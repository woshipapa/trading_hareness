# Q-FLOW: TDX capital-flow semantics

Date of live observations: 2026-10-10. Market session under test: 2026-10-09 close. This is research evidence only.

## Verdict

TDX `0x122b` fields `0x38` and `0x6b` are **CONFIRMED** as the same value: the current-session `main in - main out` in yuan from the first JSON row returned by MAC `0x1218` (`Stock_ZJLX`, `head=2`). They are not a reconstruction from public trade prints. Across the eight-stock basket, no tested side-code, price-change, yuan-threshold, or lot-threshold definition reproduced them within 2%.

They can populate `flow.stock_daily.net_amount` only as explicitly provider-defined TDX main-flow evidence, with source/version/raw components retained. They must not be described as tick-derived active-buy net flow, and the remaining fields must not be promoted under their registry guesses.

## Basket and live sources

The fixed names were `000001`, `600519`, `300750`, `688981`, and `920000`. The additional names were the largest positive movers returned by MAC board quotes: `300530` (+20.02%) and `600812` (+10.04%) from `880812`, and `300821` (+20.01%) from `880842`.

Live requests used one five-second socket at a time. MAC came from `121.36.248.138:7709`; historical ticks came from the allowed legacy hosts with a local `TdxClient` subclass whose `__enter__` sends only `_SETUP_COMMANDS[0]`; `zhb.zip` came from `120.76.152.87:7709`. Eastmoney reused the repository's `push2 ... ulist.np/get` shape (`f62`, `f184`). Tencent `qt.gtimg.cn/q=ff_...` responded only `v_pv_none_match="1";`, so it supplied no numeric reference.

## Current-session identity

All monetary values below are yuan. Relative error is against Eastmoney `f62`; `EM` is not treated as ground truth.

| Code | TDX 0x38/0x6b | 0x1218 main in | main out | Eastmoney f62 | EM error | tick side net | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 000001 | -36,053,088 | 402,656,000 | 438,709,120 | -46,478,428 | 22.43% | -139,095,894 | CONFIRMED to 0x1218 |
| 600519 | 214,378,112 | 2,009,917,952 | 1,795,539,712 | 275,143,840 | 22.09% | 557,851,312 | CONFIRMED to 0x1218 |
| 300750 | 2,263,170,560 | 8,280,769,536 | 6,017,598,464 | 2,609,791,952 | 13.28% | 1,578,543,822 | CONFIRMED to 0x1218 |
| 688981 | -12,267,392 | 1,919,550,848 | 1,931,818,240 | -7,380,864 | 66.21% | 239,211,690 | CONFIRMED to 0x1218 |
| 920000 | 0 | 0 | 0 | 1,020,778 | 100% | 1,287,883 | CONFIRMED TDX zero / incomplete BJ coverage |
| 300530 | 36,315,248 | 258,746,096 | 222,430,848 | 18,119,973 | 100.42% | 16,336,631 | CONFIRMED to 0x1218 |
| 600812 | 71,846,752 | 376,164,096 | 304,317,312 | 75,804,002 | 5.22% | -160,381,571 | CONFIRMED to 0x1218 |
| 300821 | 649,243,328 | 1,526,197,120 | 876,953,792 | 634,433,920 | 2.33% | -169,751,609 | CONFIRMED to 0x1218 |

The subtraction identity is exact to float32 rounding for all eight rows (maximum observed rounding residue 512 yuan). Pearson correlation with Eastmoney `f62` is 0.9989, so sign/rank are **PLAUSIBLE**, but the absolute values are a different vendor classification. Correlation with the raw TDX tick side net is 0.8581 and several signs disagree.

## Candidate-field verdicts

| Field | Observed unit/sign | Meaning | Verdict |
| --- | --- | --- | --- |
| `0x38`, `0x6b` | yuan; positive means TDX main inflow | current `0x1218[0][0] - [0][1]` | **CONFIRMED** |
| `0x39` | percent-like signed scalar | bid/ask ratio label was not independently reconstructed | **UNKNOWN** |
| `0x6c` | signed ratio, not percent display | registry says main-net ratio, but it matches neither net/amount nor Eastmoney `f184` | **UNKNOWN** |
| `0x6d` | signed scalar; values include 11.63, -73.86, -360.61 | registry's `retail_net_amount/yuan` is disproved by scale | **UNKNOWN** |
| `0x6e` | yuan; signed | changes independently of the post-close daily total; likely a short window, not verifiable after close | **UNKNOWN** |
| `0x6f`-`0x71` | yuan; signed | named 3/5/10-day main-net windows upstream; no daily component series was exposed | **PLAUSIBLE** names only, numerically **UNKNOWN** |
| `0x72` | yuan; signed | registry's `main_buy_net_amount` is not any current-session in/out/net component | **UNKNOWN** |
| `0x73` DDX | percent of float shares; signed | `main_net_amount / (close * float_shares)` matches its scale/sign where float shares are available | **PLAUSIBLE** |
| `0x74`-`0x76` DDY/DDZ/DDF | signed ratios | proprietary indicators; no independent definition reproduced them | **UNKNOWN** |

No field other than `0x38/0x6b` met the requested `<2%` confirmation criterion.

## Tick reconstructions

History tick amounts were computed as `price * lots * 100`. Side-code nets used `B=+1`, `S=-1`, `N/P/A=0`. The price-change rule used uptick `+1`, downtick `-1`, carrying the last direction through equal prices. Tested filters were all prints and prints at least 200,000, 500,000, and 1,000,000 yuan; lot filters were at least 500, 1,000, and 2,000 lots. None matched `0x38` across the basket. The sharp sign reversals on limit-up names (`600812`, `300821`) are decisive evidence that TDX's main-flow classification is not simply the returned tick side code or tick direction.

The tick pages covered 435 to 4,972 prints per stock and their lot totals matched MAC daily volume to one lot, establishing session completeness. Therefore the mismatch is semantic, not a truncated-history artifact.

## `0x1218` JSON

The decoded payload is a two-row JSON array of decimal strings:

1. Row 0 has four yuan fields: `[today_main_in, today_main_out, today_retail_in, today_retail_out]`. The first pair is confirmed by `0x38/0x6b`; the second pair balances the opposite side within small vendor rounding.
2. Row 1 has six yuan fields and is described by the upstream client as a five-day aggregate. Its exact six labels are not present on the wire. Treating them as named fields would be invention; only `five_day_slot_0` through `five_day_slot_5` are justified.

For example, `000001` returned row 0 `[402656000, 438709120, 856985600, 820957632]` and row 1 `[3274534912, 2528755200, -317484384, 86020816, 89306816, 141949792]`.

`tdxstat2.cfg` has 21 pipe columns, but it is not a daily flow table. For `000001`, columns 3/5/7 were `125971.71`, `174340.25`, `120581.49` 万元 and reconcile to current amount, previous amount, and another turnover statistic; columns 9-20 are counts, change/range, board, auction amounts, IPO/52-week prices, and percentages. There is no dated main-in/main-out series to sum. Consequently the claimed five-day row cannot be checked by summing `tdxstat2`; the answer is **not present**, not mismatch.

## Storage decision

`flow.stock_daily` may store the confirmed current-session pair as:

- `net_amount = 0x38` (yuan), `source = tdx_mac_stock_zjlx`, `definition = provider_main_in_minus_main_out`;
- raw `main_in`, `main_out`, `retail_in`, `retail_out`, MAC field copies, host, observation time, and session date;
- `decision_eligible = false` until coverage and historical stability are separately established.

Do not populate canonical retail flow, 3/5/10-day windows, DDX/DDY/DDZ/DDF, or a tick-derived label from the unresolved fields. `920000` demonstrates that a successful zero response can still mean unsupported coverage.
