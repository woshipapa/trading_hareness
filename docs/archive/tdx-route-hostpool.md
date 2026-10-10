# TDX route host-pool harvest and reachability matrix

Date: 2026-10-09.  Egress: local Mac workstation (not the owner peer).  This
is read-only market-data probing; no credentials or order paths are involved.

## Harvest

`scripts/data/tdx_host_candidates.txt` contains 205 unique host:port entries
(207 non-comment lines before deduplication).  Provenance is recorded in the
adjacent comments.  The harvest used the public source snapshots for:

- `rainx/pytdx` 1.72 and PyPI `tdxpy` 0.2.7 (`hq_hosts`, `best_ip`);
- `mootdx/mootdx` (`HQ_HOSTS`, `EX_HOSTS`, `GP_HOSTS`);
- `quant1x/gotdx`, `bensema/gotdx`, `injoyai/tdx`, and `godlikefu/tdx`
  (standard, broker, MAC, extended 7727, and ICFQS 7615 pools).

The list includes broker-branded routes, DNS names, 7719/7720/7727/7615
variants, and the extended-market addresses documented as coming from
`connect.cfg`.

## Probe

`scripts/probe-tdx-routes.py` uses the repository's `TdxClient` and one client
connection per host.  For each connected host it records row count or error
class for `quotes(000001.SZ,600519.SH)`, daily bars, 1-minute bars, today's
ticks, and `xdxr(000001.SZ)`.  The complete JSON is retained at
`scripts/data/tdx_route_matrix_2026-10-09.json`.

The first all-host attempt used the client default 5-second socket timeout but
was stopped after unreachable routes made the sweep too long.  The completed
matrix was run with `--timeout 0.5` as a bounded Mac reachability sweep; the
CLI default remains 5 seconds for a deliberate re-run.

## Results

| route(s) | quotes | daily bars | 1m bars | today's ticks | xdxr |
| --- | ---: | ---: | ---: | ---: | ---: |
| `117.34.114.13:7709`, `.14`, `.15`, `.18` | 2 | 5 | 5 | 4000 | 81 |
| 66 other connected routes | 0 | `struct.error` | `struct.error` | 4000 | 81 |
| one route with mid-session disconnect | 0 | `BrokenPipeError` | `BrokenPipeError` | `BrokenPipeError` | `BrokenPipeError` |

Across 205 candidates, 70 routes returned non-empty ticks and xdxr; only the
four Guotai Junan broker routes above returned real quote and K-line rows.  The
remaining candidates either timed out during setup or rejected the protocol
setup.  The result supports the hypothesis that the restriction is pool
specific rather than universal, while remaining only local-Mac evidence.

