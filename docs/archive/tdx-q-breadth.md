# TDX Q-BREADTH investigation

Status: live read-only probes on 2026-10-10 (the 2026-10-09 close). The probe
is `scripts/probe-tdx-q-breadth.py`; it uses LOGIN_ONE only and exits non-zero
when no index count decodes.

## 0x051d breadth definitions

All three legacy hosts (`60.191.117.167`, `218.75.126.9`, `117.34.114.13`)
returned the same fields: `active`, `amount`, `close`, `code`, `current_volume`,
`diff`, `down_count`, `high`, `low`, `market`, `open`, `open_amount`,
`order_count`, `orders`, `pre_close`, `server_time_raw`, `up_count`, `volume`.
The counts are index constituents, not a documented exchange-wide snapshot.

| request | up | down | flat implied by snapshot | tested definition / verdict |
| --- | ---: | ---: | ---: | --- |
| SH `999999` | 1341 | 956 | 0 | nearest to SH-A (1316/947/57), but not equal; no tested 054b bucket matches |
| SZ `399001` | 1698 | 1166 | 0 | not SZ-A (1673/1171/63); no exact tested bucket |
| SZ `399006` | 708 | 677 | 0 | not the 054b ChiNext-like bucket; constituent set is index-specific |
| SZ `399300` | 179 | 113 | 0 | identical to SH `000300`, as expected for CSI 300 alias |
| SH `000300` | 179 | 113 | 0 | same alias result |
| SH `000905` | 310 | 178 | 0 | CSI 500 constituent result; not exchange breadth |
| SH `880761` | 37 | 13 | 0 | board index breadth, not stock exchange breadth |
| BJ `899050` | 308 | 37 | 0 | BJ index breadth, not the 351-row BJ exchange snapshot |

For reference, same-session 0x054b snapshots were SH-A 2320 (1316/947/57),
SZ-A 2907 (1673/1171/63), BJ 351 (308/37/6), and all-A 5578 (3297/2155/126)
from the earlier bounded full ranking. The exact rule for each index cannot be
recovered from these broad categories; use the provider's constituent list.

## Index bars

The sibling `parse_index_bars` decoder reads daily records with trailing
`uint16 up_count, down_count`; this is structurally historical point-in-time
breadth and would avoid storing snapshots. No live index-bar response was
available in this run, and no three-session MAC-vs-bar comparison was claimed.
Therefore historical usability is **read-only/source-supported, live-unverified**.

## MAC file commands

The gotdx upstream request is 0x1215 payload `<offset:uint32, filename[70],
reserved[30]>`; 0x1217 adds `<index, offset, size>`. All three MAC hosts returned
the same 0x1215 42-byte response (`offset=0,size=0,flag=1,hash=` followed by the
32-byte ASCII token) and 0x1217 8 bytes (`index=1,size=0`, no data). This is an
empty offer, so the commands are **UNSUPPORTED for file retrieval** on these
hosts, not a parser failure.

## 0x124a and 0x0fd1

Upstream MAC 0x124a takes only `<offset:uint32,count:uint32,reserved[5]>` and
returns big-endian `total` plus little-endian `returned`; it is global offset
metadata (total bar count and rows returned), not a per-symbol request. A live
request to the MAC host using offset 0/count 128000 returned 8 bytes
`00000000f4010000`, decoding as total 0 and returned 500; the mirror is not
providing useful offset metadata. The same payload on legacy hosts returned an
unrelated 17,508-byte security-list response, evidence that the opcode is not
portable across services. It does not identify a first date.

The upstream 0x0fd1 chart-sampling layout is `<market, code, reserved[28]>`;
the parser expects float32 `pre_close` and sampled prices. Correct-request
retries to all three legacy hosts timed out. Verdict: **dead/unsupported** on
the allowed legacy service.

## Verdicts

* 0x051d: **SUPPORTED**, but counts are provider index constituents; broad
  exchange definitions must not be substituted.
* Index-bar breadth history: **POSSIBLE by source layout, live-unverified**.
* MAC file list/download: **UNSUPPORTED** (empty offer on all three hosts).
* MAC 0x124a offset info: **SUPPORTED protocol, unusable live result** (0/500).
* 0x0fd1 chart sampling: **UNSUPPORTED** (timeouts).
