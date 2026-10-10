# ZHB extras route report

Snapshot: `zhb.zip` downloaded 2026-10-09 from `60.191.117.167:7709` using one
connection and only the first setup packet (`LOGIN_ONE`). The cached archive is
1,332,578 bytes and contains 47 non-empty members. These are research evidence
only; no capability binding was changed.

## Member inventory

`UTF-8` and `GB18030` below mean the whole member decodes with that codec.
Pipe columns are counted after splitting on `|`; comma columns use `,`. Binary
members have no trustworthy line structure.

| Member | Encoding / structure | Sample | Meaning and confidence |
| --- | --- | --- | --- |
| addedcode_bj.cfg | GB18030; header comma then 5 pipe columns | `44|832000|920000|安徽凤凰(已切换)|20251009` | Beijing legacy-to-current code migration; CONFIRMED |
| tdxbjmore.cfg | GB18030; 6 pipe columns | `44|920000|2|安徽凤凰|1|` | Current Beijing security metadata/status flags; CONFIRMED shape, flags UNKNOWN |
| brkcomp.dat | GB18030; 3 pipe columns | `1|巴克莱|巴克莱亚洲有限公司` | Broker id, display name, legal name; CONFIRMED from labels/content |
| brkseat.dat | UTF-8; 3 pipe columns | `21|2|0014` | Broker id, seat/type code, seat code; first two semantics PLAUSIBLE, names absent |
| csiblock.dat | GB18030; `#` group then comma rows | `62,000001CNY01` | CSI index constituent groups; PLAUSIBLE |
| hkblock.dat | GB18030; `#` group then one code/line | `#恒指成份股` | Hong Kong index/watch lists; PLAUSIBLE |
| hkzsinfo.cfg | UTF-8; INI key/value | `[HSI_QZ]`, `QZ1=00001,1.10` | HSI constituent weights/settings; PLAUSIBLE |
| hqrule.dat | UTF-8; INI key/value | `CYBZDRatio=0.20` | Quote/board rules and dates; CONFIRMED as configuration, individual semantics PLAUSIBLE |
| hspy.dat | UTF-8; 3 pipe columns | `0|002839|ZJGH` | Stock code and short mnemonic list; CONFIRMED shape, purpose PLAUSIBLE |
| ihelp.dat | GB18030; `#` headings and tab text | `#FUNC_ABCol,常规行情栏目` | Client help text; CONFIRMED |
| ilong.dat | GB18030; 4 pipe columns | `62|CES100||中华港股通精选100` | Long/foreign index catalogue; PLAUSIBLE |
| importzs.cfg | UTF-8; 8 pipe columns | `000009|20261008|380|13.09|21.13|10.52|15.61|23.06` | Index constituent import/weights snapshot; CONFIRMED shape, column meanings UNKNOWN |
| incon.dat | GB18030; 2 pipe columns plus `#ZJHHY` | `A01|农业` | Industry taxonomy tree; CONFIRMED |
| jjblock.dat | GB18030; `#` group then comma rows | `33,000176` | Fund/index constituent groups; PLAUSIBLE |
| mgblock.dat | GB18030; `#` group then one ticker/line | `#道琼斯成份股` | US index groups; PLAUSIBLE |
| nacomte.dat | binary; no lines | 16-byte common prefix `7a9969...9b86e` | Opaque encrypted/compressed-looking NA commentary; UNKNOWN |
| nbcomte.dat | binary; no lines | same common prefix | Opaque NB commentary; UNKNOWN |
| needini.dat | UTF-8; INI with YN comma lists | `Y36=2026,0101,0102,...` | Holiday dates; CONFIRMED as holiday list |
| neednote.dat | GB18030; INI key/value | `RecentHSHoliday=20260101,...` | Exchange-specific holiday/session lists; CONFIRMED shape |
| nscomte.dat | binary; no lines | same common prefix | Opaque NS commentary; UNKNOWN |
| nscomte_std.dat | binary; no lines | same common prefix | Opaque standard NS commentary; UNKNOWN |
| nvcomte.dat | binary; no lines | same common prefix | Opaque NV commentary; UNKNOWN |
| nzcomte.dat | binary; no lines | same common prefix | Opaque NZ commentary; UNKNOWN |
| othersg.cfg | GB18030; 12 pipe columns | `0|301149|123288|96000.0000|9.160|...` | Convertible-bond/other subscription rows; PLAUSIBLE |
| profile.dat | binary; fixed-looking records, many zero bytes | `0030303030303100...` | Per-security profile blob; UNKNOWN |
| pttab.dat | GB18030; 3 comma columns | `0,000003,深金田A` | Historical/placeholder stock names; PLAUSIBLE |
| relation.dat | binary; integer-like header and padded tail | `e8030200020002...` | Relationship/index blob; UNKNOWN |
| sbblock.dat | GB18030; `#` group then code | `#三板拟转A股` | OTC/三板 groups; PLAUSIBLE |
| sgxblock.dat | GB18030; `#` group then ticker | `#新加坡中国股` | SGX groups; PLAUSIBLE |
| spblock.dat | GB18030; `#` group then 7-char code | `#融资融券` | Special A-share groups; PLAUSIBLE |
| tdxadr.cfg | GB18030; 4 pipe columns | `阿里巴巴-SW|09988|BABA|8` | HK/ADR mapping and ratio/type code; mapping CONFIRMED, final code UNKNOWN |
| tdxahrate.cfg | GB18030; 4 pipe columns | `比亚迪|002594|01211|1` | A/H pair and ratio/type; CONFIRMED shape |
| tdxbk.cfg | GB18030; 4 pipe columns | `1|锂电池|锂电池概念|0` | Board catalogue; PLAUSIBLE |
| tdxchain.cfg | GB18030; 3 pipe columns | `880506|CYL00210|新基建-5G` | Chain/derived board links; CONFIRMED shape, semantics PLAUSIBLE |
| tdxdszs.cfg | GB18030; 6 pipe columns | `港股-内地房地产|HK0201|31|1|0|...` | HK sector definitions; PLAUSIBLE |
| tdxhkag.cfg | GB18030; 8 pipe columns | `00003|香港中华煤气||天然气||880750|...` | HK stock-to-board mappings; PLAUSIBLE |
| tdxmgag.cfg | GB18030; 8 pipe columns | `AAPL|苹果电脑||苹果概念||880574|...` | US stock-to-board mappings; PLAUSIBLE |
| tdxpkmore.cfg | GB18030; 10 pipe columns | `0|000301|东方盛虹|0|000301|||10.00|1|1` | Per-stock price-limit rule; ratio column 7 CONFIRMED as numeric percentage, other flags UNKNOWN |
| tdxsbzs.cfg | GB18030; 2 pipe columns | `899001|三板成指成份股` | 三板 index definitions; PLAUSIBLE |
| tdxstat.cfg | UTF-8; 34+ pipe columns | `0|000001|-0.2027|5.18|20261009|...` | Daily statistics snapshot; CONFIRMED shape, most columns UNKNOWN |
| tdxstat2.cfg | UTF-8; 21 pipe columns | `0|000001|20261009|125971.71|...` | Statistics/capital-flow companion; CONFIRMED shape, most columns UNKNOWN |
| tdxzs.cfg | GB18030; 6 pipe columns | `轮动趋势|880081|5|2|0|轮动趋势` | Board/index definitions; CONFIRMED shape |
| tdxzs3.cfg | GB18030; 6 pipe columns | same prefix as tdxzs | Expanded board/index definitions; CONFIRMED shape |
| tend_std.cfg | UTF-8; INI group and NameNN keys | `[GROUP]`, `Name01=热点数据` | Client trend groups; PLAUSIBLE |
| tipinfo.dat | UTF-8; 22 pipe columns | `0|000001|20260630|1.240000|20260815|...` | Per-stock EPS/report/rights metadata; first 4 fields CONFIRMED shape, announcement candidate field 4 PLAUSIBLE |
| ukblock.dat | GB18030; `#` group then ticker | `#知名英股` | UK stock groups; PLAUSIBLE |
| xgsg.cfg | GB18030; 18 pipe columns | `0|001381|20261019||5630.4768|...` | Equity IPO subscription/allotment snapshot; dates/codes CONFIRMED, unlabeled quantities UNKNOWN |

## Parsers and capability candidates

`quant-service/app/datasources/sources/tdx_zhb_extras.py` implements pure
parsers for holidays, BJ migration, IPO/other subscriptions, industry trees and
optional `tdxhy.cfg` stock references, price-limit ratios, tipinfo, importzs,
broker companies/seats, chain boards, ADR/AH mappings, pttab and hspy. Raw
fields are always retained. Potential (not bound) capability IDs are:

| Parser | Candidate capability |
| --- | --- |
| needini/hqrule | `reference.trade_calendar` |
| xgsg/othersg | `events.ipo_calendar` |
| incon/tdxhy | `sector.membership`, `taxonomy` |
| tdxpkmore | `limits.prices` |
| brkcomp/brkseat | `lhb.seat_statistics` |
| tipinfo/importzs | `fundamentals.*`, `reference.instruments` |
| tdxchain/pttab/hspy/ADR/AH | `sector.membership`, `reference.instruments` |

## Independent checks

* `needini.dat` Y36 and `neednote.dat` agree on 2026-01-01/02, Spring Festival
  2026-02-16..20 plus 02-23, Qingming 04-06, Labour Day 05-01/04/05,
  Dragon Boat 06-19, Mid-Autumn 09-25 and National Day 10-01/02/05/06/07.
  This matches the exchange-specific 2026 holiday lists in `neednote.dat` and
  known CN market closures. The files do **not** contain 10-08; the often-cited
  “Oct 1-8” wording includes a weekend/adjacent schedule, so the extra day is
  not claimed here.
* No MAC quote request was issued in this run. The sibling
  `tdx_mac.py` decoder confirms fields 0/32/33 are `pre_close`/`limit_up`/
  `limit_down`, but the required five-live-stock ratio comparison is therefore
  UNVERIFIED. The expected sanity classes are main 10%, STAR/ChiNext 20%, BJ
  30%, and ST 5%, subject to new-listing and special-rule flags.
* No local 90-day MAC/legacy bar calendar export was available in this
  worktree. Calendar agreement beyond the file-internal cross-check is
  UNVERIFIED.
* For `tipinfo.dat`, 000001 has report period `20260630`, EPS `1.24`, and
  field 4 `20260815`; this is consistent with an H1 disclosure date, but no
  independent gpcw/finance response was available, so field 4 remains
  PLAUSIBLE rather than CONFIRMED. 600519 should be checked the same way.
* IPO rows are structurally consistent with Tushare-style IPO calendars, but
  no external IPO response was used and no names/quantities were invented.
  Broker seat names cannot be independently recovered from `brkseat.dat`; only
  the file's numeric relationships are reported.
* A repository-wide search found no checked-in `tdxhy.cfg` reader or pytdx,
  mootdx, or injoyai implementation to cite. The optional stock-reference
  parser therefore accepts the observed code forms without asserting the
  vendor's delimiter or assignment semantics.

## Opaque binary members

`profile.dat`, `relation.dat`, and `nacomte.dat`, `nbcomte.dat`, `nscomte.dat`,
`nscomte_std.dat`, `nvcomte.dat`, `nzcomte.dat` were inspected without guessing.
The `*comte` files share the exact first 16 bytes
`7a99696405d414671ba17a0587d9b86e`, have near-byte-uniform payloads and very
few NULs, which is consistent with compression or encryption. `profile.dat`
and `relation.dat` have large NUL-padded regions and integer-like prefixes.
No codec, checksum, record length, or semantic decoder is claimed.

The verification script `scripts/verify-tdx-zhb-extras.py` fails non-zero for a
missing/empty ZIP, missing/empty member, malformed archive, or unexpected
member set.
