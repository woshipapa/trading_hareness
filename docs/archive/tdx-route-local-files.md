# TDX local-file route

Date checked: 2026-10-09. This worktree contains pure, read-only parsers for
metadata and extended-market files that are not covered by
`tdx_local_files.py`'s A-share `.day`/`.lc1`/`.lc5` readers. The parsers return
Python dictionaries and never write a provider response to a live threshold,
order path, or database.

**Important verification boundary:** nothing in this route was verified against
a real TDX client installation or the owner's Windows workstation. The Mac
search found only application-support/container directory names under
`~/Library`; no installed client executable or usable local dataset was
available. Evidence below is synthetic fixtures plus public open-source source
and fixture material where explicitly noted.

## File layouts and capabilities

| File | Layout and provided data | Capability(s) it could back | Verification status |
| --- | --- | --- | --- |
| `gbbq` | Little-endian `uint32` record count, followed by 29-byte records. Each record has three encrypted 8-byte Feistel blocks and five clear bytes. Decrypted fields are market, 7-byte code, `YYYYMMDD`, category, and four floats for capital/ex-rights values. `GBBQ_CATEGORY_NAMES` preserves the published category labels. | `fundamentals.capital_changes` | Synthetic encrypt/decrypt round trip and parser fixture. The key schedule and transform were compared with the published `rainx/pytdx` implementation; no real file or independent upstream ciphertext vector was available. |
| `tdxhy.cfg` + `incon.dat` | `tdxhy.cfg` is pipe-delimited stock/TDX-industry/CSRC-industry data. `incon.dat` is a sectioned pipe-delimited code/name taxonomy. `map_tdxhy_to_incon()` performs a left join and keeps missing names as `None`. | `sector.membership`, taxonomy metadata | Synthetic rows; public `mootdx` fixture was read and parsed (4,906 assignments), but it is not an owner-client sample. |
| `shs.tnf` / `szs.tnf` | Fixed 314-byte security records. Public variants carry a 50-byte file prefix; code is a six-byte field at record offset 0 and the GBK name begins at offset 23. The parser auto-detects the 50-byte prefix and permits explicit offsets for client variants. | `reference.instruments` | Synthetic header-plus-record fixture and public `shm.tnf` fixture (20,393 rows) smoke check. No real owner client verification. |
| `base.dbf` | dBase III/IV-style 32-byte header, 32-byte field descriptors terminated by `0x0d`, then fixed-width records with a deletion flag. The reader exposes field name/type/length/decimals and decoded row strings. | `reference.instruments` | Synthetic DBF fixture only. No real `base.dbf` sample. |
| `vipdoc/ds/lday/*.day` | Extended-market daily records are 32 bytes, `<IffffIIf`: date, native-float OHLC, integer/bit-preserved amount, volume, and settlement. The parser also exposes the amount word reinterpreted as the HK float amount used by public `tdxpy`. `price_scale` is explicit for client variants. | `bars.daily`, and the extended-market portion of `bars.*` | Synthetic float-layout fixture. Public `mootdx` documents the extended reader and fixture names, but no binary sample was obtained in this worktree. |
| `hq_cache/zxg.blk`, user `*.blk` | Text lines containing a market digit and security code (`1` + six digits or `1#` + six digits). `parse_zxg_bytes()` retains both market and code; the compatibility helper returns codes only. | `sector.membership` for user boards | Synthetic text fixture and public pytdx layout reference. No owner `hq_cache` sample. |
| `hq_cache/blocknew/blocknew.cfg` | Repeated 120-byte records: 50-byte GBK board display name and 70-byte board file id. The corresponding `<file_id>.blk` contains the member lines above. | `sector.membership` | Synthetic config fixture and public pytdx `CustomerBlockReader` reference. No owner user-board directory. |
| GPJY financial pack | No stable, documented local GPJY byte layout was found in the permitted public sources. The published material describes GPJY server/TdxQuant calls, not a portable local pack. | None added | Not implemented; fail closed rather than guessing a financial schema. |
| `cw/gpcw*.dat` | These are the same GPCW report payload family as the server `gpcwYYYYMMDD.dat`/zip response: header `<hI H 3L>`, stock index entries `<6s1sL>`, and float report vectors. | `fundamentals.*` (through the existing F10/GPCW parser) | Deliberately not duplicated here. Reference the F10 parser in the `n8n-tdx-f10-finance` sibling worktree; this local branch has no independent local GPCW implementation. |

## `tdx-local-export.py` extension

The existing exporter already accepts a comma-separated `--kinds` value for
`daily`, `1m`, and `5m`. A follow-up owner-workstation extension can keep that
interface and add read-only kinds such as `gbbq`, `instruments`, `industries`,
`boards`, and `extended-daily`:

```text
--kinds daily,1m,5m,extended-daily,instruments,industries,boards,gbbq
```

Each kind should have a small discovery/parser branch, an explicit output
contract, and an evidence label in the exported manifest. Binary metadata should
be exported as JSON/JSONL (preserving raw fields and source path); bars should
reuse the existing CSV contracts with `source_available_at` set from file mtime.
The exporter should skip malformed files, report per-kind counts, and never
promote these files directly to canonical bars or live strategy inputs. This
document describes the extension only; the script was not changed in this
route.

## Implementation and evidence

Implementation is in
`quant-service/app/datasources/sources/tdx_local_extra.py`, with synthetic byte
coverage in `quant-service/tests/test_datasource_tdx_local_extra.py`. The
encrypted GBBQ transform is pure Python and has a deterministic encrypt helper
used only to construct synthetic fixtures. Public source inspection covered
`rainx/pytdx`, `mootdx`, and `tdxpy`; those references are not treated as owner
data evidence. No credentials, client login, broker connection, or order API was
used.
