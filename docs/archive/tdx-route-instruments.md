# TDX instrument route

Status: implementation plus live verification on 2026-10-10 using LOGIN_ONE.
The verifier opens a fresh connection for every section and records each step
independently.

## Type table

| Type | Market/code rule | Notes |
| --- | --- | --- |
| `stock_main` | SH `600/601/603/605`, SZ `000/001/002/003` | A-share main board |
| `stock_star` | SH `688/689` | STAR; `ST` is a separate name flag |
| `stock_chinext` | SZ `300/301/302` | ChiNext |
| `stock_bj` | market BJ or `43/83/87/92` | Includes new 920xxx symbols |
| `etf` | names containing ETF or `159/510/511/512/513/515/516/518/560/561/588` | ETF price scales are list-driven |
| `lof` / `fund` | `16xxxx` / `50xxxx` and `18xxxx` | Kept out of A-share equity normalization |
| `cb` | `110/111/113/118/123/127/128` | Convertible bonds |
| `bond` | `10/12/13` after CB check | Other bonds |
| `index` | SH `000/999`, SZ `399` | Board indices are separate |
| `board` | SH `880/881` | Industry/sector board index |
| `b_share` | SH `900`, SZ `200` | B shares |

Unknown rows remain `other` and must not be promoted into the platform stock
universe.

## Scale rule

0x0450 rows expose `decimal_point` and raw pre-close. The divisor is
`10 ** decimal_point`; a quote parsed by the existing client (which assumes
`/100`) is corrected by `10 ** (2 - decimal_point)`. Verified observations:
CB `127045` quote 11697.1 requires decimal point 4 to become 116.971, while
ETF `510300` quote 43.85 requires decimal point 3 to become 4.385. Ordinary
stocks with point 2 retain `/100`.

## Index/board bars

`parse_index_bars` handles the compressed TDX record with date, four signed
price deltas, packed volume/amount and trailing `up_count`/`down_count`, plus
the fixed-float compatibility record. The old generic bar parser must not be
used for 880xxx/999999/399xxx records because it decodes their extra fields as
the next datetime.

Live results from both `60.191.117.167:7709` and `117.34.114.13:7709`:

| Symbol | Quote | Index/board bars | Latest TDX close | Tencent close |
| --- | --- | --- | ---: | ---: |
| `399300.SZ` | OK | OK | 4317.25 | 4317.25 |
| `999999.SH` | OK | OK | 3813.79 | 3813.79 (`sh000001`) |
| `880761.SH` | OK | OK | 1020.12 | not queried |
| `510300.SZ` | OK | no usable rows | n/a | not queried |
| `127045.SZ` | OK | n/a | quote 11697.1 | not queried |
| `920000.BJ` | OK | n/a | quote 14.19 | not queried |

The 510300 bar command returned no usable rows on both hosts; this is recorded
as a required-step failure, not a parser success claim. Tencent public quote
responses matched the TDX closes for 399300 and 999999 exactly.

## Beijing mapping and list failure

`parse_bj_mapping` handles both delimiter styles found in `addedcode_bj.cfg` and
`tdxbjmore.cfg`. `parse_tdxbjmore` accepts rows such as
`44|920000|2|安徽凤凰|1|`; `bj_rows_from_zhb` labels every row
`source: zhb_tdxbjmore`. Server-list rows are labelled `source: server_list`.
`normalize_bj_symbol("832000.BJ", mapping)` returns the mapped `920000.BJ`.

On both hosts `security_count(BJ)=386`, while old 0x0450 and new 0x044d list
requests timed out for start 0 with page probes of 100 and 1000. Each timeout
was isolated on a fresh connection and did not prevent subsequent quote/bar
steps. The read-only downloaded `zhb.zip` was parsed for this report and is not
checked into the repository. It contained 352 `tdxbjmore.cfg` rows and 351 old-to-new
entries, so the config universe is 34 rows smaller than the server count. Five
live quote comparisons on 60.191.117.167 showed the old codes are unusable
(the parser receives a spurious `600839` row at price 0.0), while the mapped
new codes returned valid quotes: `832000->920000` 14.19,
`832023->920023` 1.37, `830799->920799` 31.78, `871981->920981` 27.40,
and `430047->920047` 15.18.

## Live list coverage

`60.191.117.167` returned SZ 24,401 and SH 27,578 rows. `117.34.114.13`
returned SZ 24,401 and SH 27,401 rows, a difference of 177 SH rows. The
captured verifier output records the type counts; the differing codes are not
asserted without retaining both host payloads, so this is a host snapshot
difference, not evidence that either list is canonical. The first codes found
only on the 60-host snapshot include `519001`, `519002`, `519003`, `519005`,
`519007`, `519008`, `519011`, and `519013`; the 117-host snapshot had no
codes absent from the 60-host set.

The 60-host type counts were: board 1,119; bond 5,167; CB 571; ETF 1,427;
fund 3,044; index 557; LOF 1,220; other 33,568; ChiNext 1,412; main-board
stock 3,198; STAR 618; B-share 78. The 117-host run had the same named counts
except `other=33,391` because of the 177-row SH difference.

## Capability coverage

The module can back `reference.instruments` (classification and scale),
`reference.security_list` (0x044e/0x0450 paging plus zhb BJ fallback),
`bars.index_daily` (breadth-aware parser), and `sector.index_quote`.
`quote.all_a_snapshot` remains a prerequisite for a complete live sweep.

The platform's `app/datasources/http.py::ashare_symbol` accepts SH
600/601/603/605/688/689, SZ 000/001/002/003/300/301/302, and BJ 920/43/83/87;
it intentionally rejects indices, boards, ETFs, funds and bonds.
