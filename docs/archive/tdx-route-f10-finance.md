# TDX F10 and finance route

Source harvest: `codex/tdx-f10-finance` at `3d4c5a06`.

Status: implementation and synthetic parsing fixtures are verified locally. Live host results below are only populated by `scripts/verify-tdx-f10-finance.py`; no host was assumed to answer.

## Wire commands

The stdlib client uses the existing `TdxClient` setup packets and `<IIIHH` response framing. The added builders match the public pytdx/gotdx/mitdx layouts:

Layout references read in a temporary scratch checkout were `rainx/pytdx`
(`get_finance_info.py`, `get_company_info_category.py`,
`get_company_info_content.py`, `get_report_file.py`), `bensema/gotdx` and
`Bit-Shine/zytdx` (`proto/get_company_info.go`, `TdxProtocol.md`),
`Michaol/mitdx` (`src/network.rs`), and `mootdx`
(`mootdx/financial/financial.py`, `columns.py`). They were read only and not
copied into this repository.

| Command | Opcode | Request payload | Response |
| --- | --- | --- | --- |
| Finance summary | `0x0010` | count `<H=1>`, market `<B>`, six-digit code | count + market/code + 34-field summary |
| F10 category | `0x02cf` | `<H6sI>` market/code/zero | count + repeated `<64s80sII>` |
| F10 content | `0x02d0` | `<H6sH80sIII>` market/code/padding/file/start/length/reserved | 10-byte prefix + `<H>` byte length + GBK |
| Report file | `0x06b9` | offset, max chunk (30,000), 100-byte filename | `<I>` chunk size + bytes |

`parse_finance_info()` uses separate raw scales: monetary floats are 千元 and are multiplied by 1,000; capital floats are 万股 and are multiplied by 10,000. EPS and net-assets-per-share remain 元/股; shareholder count is a count; province, industry and dates are integer codes (`YYYYMMDD`). `field_units` exposes the normalized unit and the raw-unit metadata. The FINVALUE/GPCW source documentation says unmarked GPCW money is yuan and capital is shares; explicit `(万元)`, `(万股)`, per-share and percentage fields retain those stated units.

## GPCW parser

The `.dat` header is `<hI H 3L>`, followed by fixed stock entries `<6s1sL>` and float records. The parser returns `code`, `report_date`, `colN`, raw `values`, and a `fields` mapping. The field index is fixed: `col1` is `基本每股收益`; `col4` is `每股净资产`; `col21` is `流动资产合计`; `col40` is `资产总计`; `col95` is `五、净利润`; `col238` is `总股本`; unknown/newer indexes remain `colN`.

## Host matrix

Run the probe from the repository root. It makes at most one five-second connection per host and prints finance field counts, category/content lengths and (for the two candidate finance hosts) a bounded `gpcw.txt` manifest read. The four Guotai Junan hosts are `117.34.114.13/.14/.15/.18:7709`; candidate finance hosts are `120.76.152.87:7709` and `119.147.212.81:7709`. The script records connection refusals, short/error responses and successful row counts without treating an empty response as valid data.

Observed 2026-10-09 with `000001.SZ` and `600519.SH`:

| Hosts | Finance | F10 category/content | `gpcw.txt` |
| --- | --- | --- | --- |
| `117.34.114.13/.14/.15/.18` | 2 rows/host; 39 normalized keys | 16 categories and 152 decoded preview chars per symbol | not attempted |
| `120.76.152.87` | 000001 answered; connection closed before 600519 | connection closed | 8,574 bytes |
| `119.147.212.81` | timeout | timeout | timeout |

The probe reads Tencent quote field 73 as a comparison for TDX `total_shares`; this is a plausibility comparison, not proof of identical point-in-time semantics. The 2026-10-09 probe returned TDX/Tencent totals of 19,405,918,750 vs 19,405,918,198 for 000001 (difference 552) and 1,250,081,562.5 vs 1,250,081,601 for 600519 (difference -38.5), consistent with TDX float32/scaling precision. Until observation dates are aligned, the output must not be promoted into a fundamentals value.

## Unit cross-check

On 2026-10-10, `120.76.152.87:7709` served `gpcw.txt` and the newest archive containing the requested symbols, `gpcw20260630.zip` (the newer `gpcw20260930.zip` contained only one unrelated row). The parsed report date was `20260630`. Comparing the 0x0010 summary from `117.34.114.13:7709` with GPCW columns `col40`, `col72`, `col74`, and `col96` gave these finance/GPCW ratios after the fix:

| Code | Total assets | Parent equity / `col72` | Revenue | Net profit | Finance `updated_date` |
| --- | ---: | ---: | ---: | ---: | --- |
| 000001 | 1.00000001 | 1.00000000 | 1.00000003 | 1.00000000 | 20260815 |
| 600519 | 1.00000005 | 0.9586 | 1.00000007 | 1.00000000 | 20260815 |
| 300750 | 1.00000000 | 0.9164 | 1.00000000 | 1.00000000 | 20260925 |

The approximately 1.0 ratios validate `千元 -> 元` for monetary fields. The equity ratios compare two different quantities and are not a report-period or restatement difference: the summary field is `parent_equity`, the equity attributable to the parent (GPCW column 271), while `col72` is total equity, which includes minority interests. Separately, the archive is 20260630 while the summary carries a later `updated_date`; the summary must be combined with announcement metadata (via tipinfo) to establish the report period, and the `updated_date` field must never be used as a period proxy.

## Capability fit

The output can support research-only enrichment for the existing `fundamentals.financial_statements` capability (currently bound to `fuyao_ths`) and the existing `fundamentals.capital_changes` capability only indirectly for share-count context. The 0x0010 summary is not a replacement for `fundamentals.daily_basic`, whose catalog binding is `longhuvip_composite`. The company_profile capability carries F10 text/categories with effective=collection time (no report period semantic). Catalog bindings have been added with the UNSUPPORTED status, which `quant-service/app/datasources/contracts.py` defines as "the upstream refuses it (kept to re-probe)"; the resolver does not route to a binding in that status.
