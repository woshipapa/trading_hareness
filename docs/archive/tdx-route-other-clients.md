# Route R4: other open-source TDX clients

Research snapshot: 2026-10-09. Sources were read from public GitHub repositories
and PyPI artifacts in throwaway directories; no package was installed and no
broker login or credential was attempted. “Implemented” means a request builder,
parser, public API, or local-file reader was found in source. It does **not** mean
the current public server still serves that command. The in-repo baseline is
`quant-service/app/datasources/sources/tdx_protocol.py` plus the bensema/gotdx
feature set: standard 7709 quote/K-line/tick/history-tick/1m-1y categories and
XDXR, with current evidence that public quote/K-line commands return empty/error
while history ticks and XDXR work.

## Capability matrix

| Project / URL | Licence, language, last update | Commands / files found | Hosts / dialect | Current-server signal and gap vs ours/bensema |
|---|---|---|---|---|
| [Bit-Shine/zytdx](https://github.com/Bit-Shine/zytdx) | MIT, Go, 2026-04-21 | Standard quotes, K-line/index, minute/history-minute, ticks/history ticks, auction, unusual/top-board, volume profile/history orders; finance, F10/company, XDXR, file download, block files; MAC board list/members/quotes/dynamic quotes and unified bars; extended quote/K-line/minute/history-trade/table | Main/broker 7709; MAC 7709; TdxExHq 7727 and MACEx 7727. Host lists are in `hosts.go`. | Broadest protocol inventory and explicit 7727/MAC dialects. README and examples are recent, but no independent issue evidence proves every command is live; treat as research candidates. Adds MAC, extended market, auction/top-board/stat-like tools and file paths absent from `tdx_protocol.py`. |
| [injoyai/tdx](https://github.com/injoyai/tdx) | MIT, Go, 2026-09-19 | Quotes, security list/count, K-line/index, minute/history-minute, ticks/history ticks, auction; GBBQ/XDXR; finance; F10/company; report-file/`zhb.zip`; block and `spblock.dat`; TDX industry; `tdxstat`/`tdxstat2` flow/statistics; IPO subscription; extended-market quote/K-line/minute/trade | Standard 7709 plus TdxExHq 7727 (`hosts_exhq.go`). README documents 7727 as separate handshake/prefix. | Most current maintained candidate and directly names finance/flow/report-file commands. No server acceptance probe was run here; its issue page could not be sampled reliably due GitHub API rate limiting. All capabilities beyond ours are claims until a read-only probe. |
| [Michaol/mitdx](https://github.com/Michaol/mitdx) / PyPI `mitdx` 1.1.10 | MIT, Rust + Python, 2026-05-26 | Security count/list; quote; K-line/index; minute/history-minute; tick/history-tick; XDXR; 34-field finance; F10 category/content; block metadata/data and report-file 0x06B9; local daily/minute readers | Standard 7709 and dedicated GP finance routes. `consts.py` ships seven HQ hosts and `120.76.152.87`/`119.147.212.81` for finance. | Recent changelog and live-integration tests exist in source, but were not executed. Adds finance/F10/block/report-file to our narrow client, though no 7727/extended implementation was found. |
| [jiangtaovan/tdxrs](https://github.com/jiangtaovan/tdxrs) | MIT, Rust + PyO3/Python, 2026-09-15 | 13 network classes: quotes, 12 K-line periods, current/history minute, current/history ticks, security list/count, finance (34 fields), XDXR, blocks; fund/ETF client; local `.day/.lc1/.lc5/.dat/gpcw*.dat` readers; optional F10 feature; downloader | Standard 7709; examples use `218.75.126.9:7709`; no 7727 implementation found in the inspected tree. | Strongest maintained Rust/Python option for standard quotes/bars/finance and local files; README reports network benchmarks but this is not an availability guarantee. F10 is feature-gated and Python binding is not fully exposed; no new dialect/host family. |
| [ten2net-tdxapi](https://pypi.org/project/ten2net-tdxapi/) 0.1.0 | MIT, Python, uploaded 2026-04-10 | Quotes/index/futures quote, K-line/minute K, minute/history-minute, tick/history-tick, security list, XDXR, finance, F10/company, block metadata/data | Five standard 7709 servers; no separate 7727 transport. | PyPI wheel source is compact and pytdx-derived. Its declared repository `github.com/ten2net/tdxapi` returned 404, so issue/update history cannot be independently checked. Market IDs 3-9 are declared, but the same standard quote command is used; this is not evidence of working extended-market service. |
| [quant1x/gotdx](https://github.com/quant1x/gotdx) | MIT, Go, 2025-09-30 | Integrated bensema/gotdx + pytdx: quotes, K-line/index, minute/history-minute, ticks/history ticks, finance, XDXR, block files, F10 category/content; connection pool/fastest-host selection | Standard 7709 list in `quotes/bestip_address.go`; no 7727 found. | Useful Go reference and host expansion, but largely overlaps the in-repo/bensema surface. README says “current” only by active development; live tests in repo are not a substitute for a current server probe. |
| [iOSleep/dart_tdx](https://github.com/iOSleep/dart_tdx) | MIT, Dart, 2026-09-16 | Port of mootdx: quotes, K-line (day/week/month/minute), index, security list/count, minute; finance download/all files; local `.day/.lc1/.lc5`; block data | Standard 7709 list; unusual extended list on **7720**; dedicated finance `120.76.152.87:7709`. | Adds a distinct 7720 extended-market convention and a finance endpoint. No ticks, auction, F10-content or capital-flow API exposed in inspected README. Current date is recent but no live issue acceptance evidence. |
| [interstellarmt/node-tdx](https://github.com/interstellarmt/node-tdx) | MIT, TypeScript/Node, 2026-06-22 | Standard quote/K-line/minute/history-minute; extended quote/K-line/minute/history-minute/trade/range-bars/quote-list; auto host speed selection | Standard 7709 and TdxExHq 7727; server list in `src/server-list.ts`. | Good Node implementation of the separate extended framing/handshake. It does not add finance, F10, boards or auction in the inspected source. README claims free current public servers; no live probe performed. |
| [mootdx/mootdx](https://github.com/mootdx/mootdx) / PyPI `mootdx` | MIT, Python, 2024-07-16 | Quotes, daily/K-line, minute/history-minute, ticks/history ticks, finance, F10 category/content, block file retrieval, local `.day/.lc1/.lc5`, financial-file download/parser; CLI bestip/batch/file actions | Standard 7709; source contains an old/commented 7727 example but no maintained extended client. | Mature Python wrapper and file/finance coverage, but older release and 99 open issues. It overlaps pytdx and is less attractive than Rust/Go maintained forks for a new implementation. |
| [mootdx2](https://pypi.org/project/mootdx2/) 1.7.3 | MIT classifier/licence file, Python, uploaded 2026-09-28; package metadata points to `mootdx/mootdx` | Native/tdxpy dual standard driver; quote/K-line/minute; XDXR/finance/F10/report-file; local daily/minute/block/GBBQ; financial files; offline-first daily cache; CNInfo announcement HTTP client; ETF adjustments | Standard 7709 and an `exhq` module; no novel maintained host family found in the inspected wheel. | Newer and broader operational wrapper than mootdx, but much of its extra value is caching, local files and non-TDX CNInfo HTTP, not a new wire dialect. No `TdxW` endpoint found. |
| [rainx/pytdx](https://github.com/rainx/pytdx) / PyPI `pytdx` | No SPDX licence metadata in GitHub API, Python, archived; last push 2020-04-15 (repo metadata updated 2026-10-07) | Baseline standard quote/K-line/minute/ticks/history, finance, XDXR, F10/company, blocks/files; `exhq.py` includes extended parsers (markets/instruments/quote/bars/minute/trades) | Standard 7709; extended client code exists, but no trustworthy current host list in the inspected metadata. | Already the protocol reference used by this repository. Archived and old; current public quote/K-line refusal is documented in our module. |
| `tdxpy` PyPI 0.2.7 | Package metadata/source artifact read; Python; release artifact present (no reliable VCS update signal) | Same pytdx-derived standard and extended parser families; finance crawler and F10/extended parsers in package listing | 7709/extended client code; no distinct host list found in wheel. | Compatibility/reference only; not a new dialect. |
| [1299172402/tdx](https://github.com/1299172402/tdx) | MIT, Go, 2026-09-13 | Fork/continuation of injoyai-style implementation: standard quote/K-line/minute/ticks/auction/finance/F10/block/spblock/stat/IPO/report-file and 7727 extended; local readers and GBBQ | 7709 plus 7727; host files mirror the injoyai family. | Recent fork with broad coverage; likely duplicate of injoyai rather than an independent protocol discovery. |
| [godlikefu/tdx](https://github.com/godlikefu/tdx) | MIT, Go fork, 2026-09-07 | Fork of injoyai; same broad API surface by source lineage | 7709/7727 inherited | No independent evidence beyond parent; rank below parent. |
| [millken/tdx](https://github.com/millken/tdx) | MIT, Go, 2026-08-07 | Fork/near-copy of injoyai; standard + extended family | 7709/7727 inherited | Tiny fork (1 star) and no issues; use only as a diff source. |
| [Bit-Shine/zytdx](https://github.com/Bit-Shine/zytdx) protocol document | MIT, Go, 2026-04-21 | `TdxProtocol.md` documents command constants including standard 0x053e/0x052d/0x0fc5/0x0fb5/0x0010/0x02cf/0x02d0/0x06b9 and extended 0x23xx/0x24xx plus MAC | 7709, 7727 | The protocol document is the clearest source for dialect/command IDs not present in our Python module. |

### Other search results

GitHub repository search also found `iOSleep/dart_tdx`, `interstellarmt/node-tdx`,
`jiangtaovan/tdxrs`, and small/archived protocol mirrors. No credible independent
Rust crate named `tdx-rs`/`rustdx` surfaced; `jiangtaovan/tdxrs` is the maintained
Rust result. No inspected candidate implemented HTTP `TdxW` or ICFQS/L2 login
endpoints; the “L2” references in this set are TDX five-level quote/MAC data, not
the licensed Longhu HTTP API used by this repository.

## Ranked recommendation for quotes/K-lines/minute/finance today

1. **injoyai/tdx (or the 1299172402 continuation)** — freshest broad standard
   implementation, finance/report-file/board/stat/auction plus a separately coded
   7727 dialect. Probe standard finance and `zhb.zip` first; then 7727.
2. **Bit-Shine/zytdx** — widest documented surface, including MAC and extended
   market, with explicit host pools and protocol constants. Probe a standard quote,
   finance, auction, then MAC/7727 against one host each.
3. **Michaol/mitdx** — active Rust/Python implementation with explicit standard
   command IDs, F10/finance/0x06B9 report files and dedicated finance hosts.
4. **jiangtaovan/tdxrs** — best fit if we need a dependency-light Rust/Python
   reader/client and local financial/minute files; standard network APIs are clean
   and recent. Probe quote/K-line/finance and compare byte-for-byte with pytdx.
5. **quant1x/gotdx** — practical Go standard client and host pool, but mostly
   overlaps bensema/pytdx and does not add 7727.
6. **node-tdx** — useful only if the integration target is Node or extended-market
   7727; no finance/F10/board support found.
7. **dart_tdx** — useful for Dart and its unusual 7720 endpoint, but narrower data
   surface and no independent current-server evidence.
8. **ten2net/mootdx2/mootdx/tdxpy/pytdx** — useful references and local-file/
   finance wrappers; `ten2net-tdxapi` has no reachable source repository, pytdx is
   archived and already the in-repo wire-format reference.

## Exact next probe (top three)

Run from a read-only research environment with one TCP connection per selected
host and a 5-second timeout; record raw response length/error and never feed the
result into a live threshold:

1. **injoyai/tdx:** `124.71.187.122:7709` (standard) and
   `116.205.143.214:7727` (extended). Standard: `GetQuote(sz000001)`,
   `GetKlineDay(sz000001, 10)`, `GetFinanceInfo(sz000001)`,
   `GetCompanyCategory(sz000001)`, `GetBlockData(block_gn.dat)`,
   `GetTdxStat2()`, `GetCallAuction(sz000001)`. Extended: market list, one
   instrument quote, one K-line and one history trade. Compare each result to
   pytdx/bensema and classify `rows`, `2-byte error`, timeout, or protocol mismatch.
2. **zytdx:** `110.41.147.114:7709`, `112.74.214.43:7727`, and MAC
   `121.36.248.138:7709`. Run `StockQuotesDetail`, `StockKLine`, `GetFinanceInfo`,
   `GetAuction`, `GetParsedBlockFile(block_gn.dat)`, `ExGetKLine`, and
   `MACBoardList`/`MACBoardMembersQuotes`; save command IDs and raw lengths.
3. **mitdx:** `110.41.147.114:7709` for standard commands, then exactly one
   connection to `120.76.152.87:7709` for the dedicated finance/report path. Run
   `quotes`, daily and 5-minute `bars`, `minute_data`, `history_transactions`,
   `finance`, `f10`, `block_meta`/`block_info`, and raw `get_report_file`. Compare
   response lengths and decoded rows to our client/pytdx; if quotes/K-lines are
   refused, continue only with finance/F10/block/report commands.

## Evidence boundary

Verified by source inspection: repository URLs, SPDX/licence files or package
metadata, language, GitHub `pushed_at` dates, command names/constants, host lists,
and the existence of the 7720/7727/MAC dialect code. Only read/unpacked public
source artifacts; no setup/install or code execution from downloaded packages.

Not verified here: TCP reachability, server response contents, current issue
resolution, row-level correctness, rate limits, or whether any public host still
accepts quote/K-line/finance/extended commands. GitHub issue API sampling was
rate-limited for this run, so “current” claims are source freshness/README claims,
not availability evidence. The candidate host file is therefore a probe queue,
not an approved production registry.
