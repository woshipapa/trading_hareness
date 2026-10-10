# TDX route files and statistics (2026-10-09)

This is a read-only research-source report. The probe used
`scripts/verify-tdx-files.py`, one TCP connection per host, a five-second
timeout, and one download per filename per host. It ran against the three
requested public hosts and the requested MAC host (`121.36.248.138:7709`).
All four hosts served the same snapshot in this probe; the statistics rows
carried `20261009`, so the observed update lag was same-day at probe time
(the exact publication time is not exposed by the files).

| file | hosts observed | size (bytes) | rows / members | research use and provenance |
|---|---|---:|---:|---|
| `block_gn.dat` | all four | 757,083 | 269 blocks / 41,332 members | Concept membership. Binary TDX server snapshot; block name/type and 7-byte market+code members. No point-in-time history; retain `known_at` as collection time. |
| `block_fg.dat` | all four | 453,279 | 161 blocks / 20,144 members | Style/region membership, same TDX snapshot and PIT limitation. |
| `block_zs.dat` | all four | 329,507 | 117 blocks / 11,557 members | Index/industry-style membership, same TDX snapshot and PIT limitation. |
| `tdxzs.cfg` (inside `zhb.zip`) | all four | archive member | 604 rows | Board name to index code/type/subtype/ref mapping. GBK pipe rows; unknown columns retained. Joins block names to index codes without inventing IDs. |
| `tdxzs3.cfg` (inside `zhb.zip`) | all four | archive member | 1,071 rows | Extended board-index mapping; same provenance and lag as `tdxzs.cfg`. |
| `spblock.dat` (inside `zhb.zip`) | all four | archive member | 35 groups | Large/professional board membership not bounded by the 400-member binary block record. GBK section headers and 7-digit market+code members. |
| `tdxstat.cfg` (inside `zhb.zip`) | all four | archive member | 8,080 rows | Descriptive per-stock statistics: PE TTM/static, dividend yield, trend days and documented 5/10/20/60-day/YTD change columns. All 35 raw fields are retained; undocumented columns remain unnamed. |
| `tdxstat2.cfg` (inside `zhb.zip`) | all four | archive member | 8,080 rows | Per-stock turnover/amount fields, 52-week high/low, IPO price and board-index code. Capital-flow-like columns whose semantics are not independently verified remain in `fields`; this is not an order-flow feed. |
| `zhb.zip` (report file `0x06B9`) | all four | 1,332,578 | 47 archive members | Atomic post-close package containing the config/stat files above. ZIP members are bounded and path-checked before parsing. |

The report file protocol has no separate size query in the reference clients:
`0x06B9` is paged with `offset,size,filename[100]` until a short/empty chunk.
Binary block files do have a `0x02C5` size query, followed by `0x06B9` chunks.
Both workflows enforce a 64 MiB compressed/download cap and the ZIP reader
enforces a 128 MiB uncompressed cap.

## Catalog fit

These files could provide research-only evidence for existing capability IDs,
without changing `app/datasources/catalog.py` or any binding:

* `sector.membership`: `block_gn.dat`, `block_fg.dat`, `block_zs.dat`,
  `spblock.dat`, joined through `tdxzs(.cfg|3.cfg)`. Store the TDX file name,
  host, collection time and raw member code as provenance; do not treat the
  snapshot as historical PIT data before it was observed.
* `sector.index_quote`: `tdxzs` supplies index identifiers only; it does not
  supply prices, so it cannot replace the existing quote source by itself.
* `flow.stock_daily`: `tdxstat2.cfg` has descriptive amount fields and raw
  capital-flow-like columns, but no verified size-bucket contract. It can back
  an explicitly labelled research projection only after field-level validation.
* `fundamentals.daily_basic`: documented valuation/change fields from
  `tdxstat.cfg` are candidate descriptive inputs, not a promotion to a live
  threshold or order path.

The repository already has local-file readers in
`app/datasources/sources/tdx_local_files.py`; this new module intentionally
does not edit or duplicate those bar readers. A parallel branch is adding
local block/config readers, so consolidation should happen after that branch
lands rather than silently changing ownership here.
