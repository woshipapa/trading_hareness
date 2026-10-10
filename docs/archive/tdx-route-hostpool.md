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


## LOGIN_ONE sweep, 2026-10-10 (Mac and owner egress)

Run by Claude with the P1 probe. Both sweeps used the same settings:
- profile `login_one`, timeout 5 s, 12 threads, one connection per host;
- required commands: quotes (echo-checked, R1), daily bars and history ticks for 2026-10-09;
- candidates: `tdx_host_candidates.txt` plus `tdx_host_candidates_other.txt`, 212 after deduplication.

The commands were:

```
python scripts/probe-tdx-routes.py --hosts-file scripts/data/tdx_host_candidates.txt \
  --hosts-file scripts/data/tdx_host_candidates_other.txt --profile login_one --egress mac \
  --output scripts/data/tdx_route_matrix_login_one_2026-10-10_mac.json
bash scripts/tdx-owner-probe.sh --profile login_one \
  --output scripts/data/tdx_route_matrix_login_one_2026-10-10_owner.json
```

| | Mac egress | owner egress |
| --- | ---: | ---: |
| observed (UTC) | 2026-10-10 08:21:37 | 2026-10-10 08:21:46 |
| candidates / connected / usable | 212 / 75 / 74 | 212 / 75 / 74 |
| usable for quotes, bars, ticks_hist | 74, 74, 74 | 74, 74, 74 |
| connect errors | TdxProtocolError 101, TimeoutError 36 | TimeoutError 90, TdxProtocolError 26, ConnectionRefusedError 13, ConnectionResetError 5, OSError 2 |
| connect ms of usable hosts, min / median / max | 26.8 / 74.85 / 2,474.2 | 15.0 / 62.0 / 125.9 |

Both egresses found the same 74 usable hosts, all on port 7709. This replaces the 4-host figure
above, which came from the old three-packet handshake with a 0.5 s timeout.

The runtime pool `quant-service/app/datasources/sources/tdx_hosts.py` is generated from the owner
matrix with `scripts/generate-tdx-hosts.py`. It holds the 20 fastest hosts that were usable in
every sample, at most 3 per /16 network. Their median connect times run from 15.0 to 33.3 ms. The
`TDX_HQ_HOSTS` environment variable still overrides it.

Owner evidence record:
- target: the owner egress, reached by `ssh stockpeer@47.110.79.189 -p 3535 'python3 -I -'`. This
  is not the 15682 read path; the probe source was sent over stdin and nothing was written on the
  owner.
- trading date for the history ticks: 2026-10-09; observed 08:21:46 UTC on Saturday 2026-10-10.
- provider: the public TDX quote hosts, `tdx_public`.
- coverage: 212 candidates, 74 usable.
- freshness: a weekend snapshot of reachability, not of intraday cadence.
- `decision_eligible`: false.

An intraday probe on a trading day (evidence gate 4) is still pending.
