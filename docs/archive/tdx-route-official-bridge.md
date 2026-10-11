# TDX Routes R5/R7: official bridge findings

Date checked: 2026-10-09. The official pages were fetched from
`https://help.tdx.com.cn/quant/` and their VuePress bundle was searched and
read locally. No TDX terminal, Windows host, `tqcenter` import, account or
paid data subscription was available in this Mac worktree. Therefore every
item below is either **verified-docs** (the published page/signature/sample
was read) or **unverified-runtime** (not executed against a real client).

## Runtime and licence conditions

**Verified-docs:** scripts import `from tqcenter import tq` and must call
`tq.initialize(__file__)`. The normal mode requires the TDX terminal to be
running and logged in. The FAQ says backend-only/server mode does not require
the terminal, but requires a paid `tdxaidata` subscription. Historical K-line
data is normally downloaded in the client first; `refresh_kline` may trigger a
client download. TdxQuant is a data/research API; this repository's bridge
never calls trading functions.

**Unverified-runtime:** actual entitlement, point consumption, local cache
contents, field casing in a particular client build, and Windows path/import
behavior were not tested here.

## Read-only TdxQuant surface

The signatures and shapes below are the published docs' contracts. `dict`
means a JSON-like mapping; DataFrame means a pandas DataFrame or a dict of
field-to-series as shown in the examples. Limits are only listed where the
docs state one; an omitted limit is not evidence that the call is unlimited.

| Function | Arguments | Return shape / limits / conditions | Status |
| --- | --- | --- | --- |
| `get_market_data` | `field_list`, `stock_list`, `period`, `start_time`, `end_time`, `count=-1`, `dividend_type`, `fill_data` | `dict[field, DataFrame indexed by time and symbol]`; periods `1d`, `1m`, `5m`; `count>0` takes the last N per symbol; volume is lots and amount is 10k yuan in the example | verified-docs; runtime unverified |
| `get_financial_data` | `stock_list`, `field_list`, `start_time`, `end_time`, `report_type='report_time'` | `dict[symbol, DataFrame]`, selected FN fields; field list required | verified-docs |
| `get_financial_data_by_date` | `stock_list`, `field_list`, `year=0`, `mmdd=0` | `dict[symbol, dict[field, string]]`; zero/zero means latest | verified-docs |
| `get_gp_one_data` | `stock_list`, `field_list` | `dict[symbol, dict[GO-field, string]]`; field list required | verified-docs |
| `get_gpjy_value` / `_by_date` | stock/field/date args | `dict` of GP fields to dated value lists, or field-to-value lists for one date | verified-docs |
| `get_bkjy_value` / `_by_date` | stock/field/date args | `dict` of BK fields to dated value lists, or field-to-value lists | verified-docs |
| `get_scjy_value` / `_by_date` | field/date args | `dict[field, list[{Date,Value}]]`, or `dict[field, list[string]]` | verified-docs |
| `get_gb_info` | `stock_code`, `date_list`, `count=1` | share-capital dict/list for requested dates | verified-docs; exact field table not independently executed |
| `get_gb_info_by_date` | `stock_code`, `start_date`, `end_date` | rows with `Date`, `Zgb`, `Ltgb`; requires corresponding daily K-line cache | verified-docs |
| `get_market_snapshot` / `_batch` | one code + `field_list`, or `stock_list` | dict of snapshot fields; batch maps symbols to snapshots | verified-docs |
| `get_pricevol` | `stock_list` | dict fields `LastClose`, `Now`, `Zaf`, `Volume`; A-share volume is lots | verified-docs |
| `get_full_tick` | `code` | one real-time tick/report dict | verified-docs from published examples |
| `get_stock_info` / `_batch` | code or `stock_list` | dict/object security metadata | verified-docs |
| `get_match_stkinfo` | `key_word` | list/dict rows containing `Code`, `Name` | verified-docs |
| `get_stock_list` | `market=None`, `list_type=0` | list of codes, or code/name pairs; market codes are client-defined | verified-docs |
| `get_sector_list` / `_ds` | `list_type=0` | list of sector codes or code/name pairs | verified-docs |
| `get_user_sector` | no args | user/custom-sector list from the client | verified-docs |
| `get_stock_list_in_sector` | `block_code`, `block_type=0`, `list_type=0` | list of member codes or code/name pairs | verified-docs |
| `get_relation` | `stock_code` | dict fields `BlockCode`, `BlockName`, `BlockType`, `GPNume` | verified-docs |
| `get_index_stocks` / `get_index_weight` | index code, optional date | component list / weight mapping | verified-docs |
| `get_industry_stocks` / `get_concept_stocks` | industry/concept code, optional date | member-code list | verified-docs |
| `get_zzgz_stocklist` | `index_code`, `setcode=None`, `list_type=0` | constituent list or code/name pairs | verified-docs |
| `get_trading_dates` | start/end/count variants | list of dates; exact overload depends on docs version | verified-docs |
| `get_ipo_info` | documented code/list args | IPO metadata dict/list | verified-docs |
| `get_kzz_info` / `_batch` | code + optional `field_list`, or `stock_list` | convertible-bond metadata; fields include `KZZCode`, `HSCode`, `ZGPrice`, `CurRate`, `RestScope` | verified-docs |
| `get_trackzs_etf_info` | `zs_code` | ETF tracking dict: `Code`, `Name`, `NowPrice`, `PreClose`, `IOPV`, `Zgb`, `Sz` | verified-docs |
| `get_exday_data` | `stock_code`, `count=1` | ex-right/dividend rows | verified-docs |
| `get_divid_factors` | documented stock/date args | dividend/factor rows | verified-docs |
| `get_zdt_data` | `stock_list=[]` | limit-up/down data mapping | verified-docs |
| `get_more_info` / `_batch` | code or `stock_list`, optional `field_list` | security auxiliary-info dict(s) | verified-docs |
| `get_call_auction` / `_batch` | code or `stock_list`, batch `return_df=True` | auction rows or DataFrame | verified-docs |
| `get_minute_data` | `stock_code`, `date`, `field_list=None` | one-day minute dict/records; separate TdxAiData examples | verified-docs; paid backend condition applies |
| `get_tick_data` | code/date, `startxh=0`, `wantnum=2000`, optional fields | tick dict/records; docs call it transaction ticks, not K-line | verified-docs; runtime unverified |
| `get_sector_list_ds` | `list_type=0` | data-service sector list | verified-docs |
| `get_subscribe_hq_stock_list` | no args | currently subscribed-code list | verified-docs |
| `get_market_data` cache helpers `refresh_cache`, `refresh_kline` | market/force, or stock list + period | status dict; refresh cache skips within 10 minutes unless forced; `refresh_kline` only `1d/1m/5m` | verified-docs; side effect is cache-only |

The bundle also exposes formula/profile/runtime helpers and account/position
queries. They are intentionally **not used by this bridge**: formula helpers
can mutate client-side strategy state, and account/position/order APIs cross
the repository's trading boundary even when a particular call is read-only.
Trading/mutation functions (`order_stock`, `cancel_order_stock`, sector/formula
setters, send/download helpers, subscriptions and messages) are excluded.

## Bridge and parser status

`scripts/tdx-quant-export.py` is owner-Windows-only, imports `tqcenter` lazily,
supports `--dry-run`, calls only `initialize` and `get_market_data`, and writes
the existing daily/1m/5m CSV contracts. TdxQuant's documented lots and 10k-yuan
units are converted to shares and yuan. CSV conversion is verified with a fake
module; no real TDX call was made.

`tdx_local_files.py` now includes pure readers for `block_gn.dat`,
`block_fg.dat`, `block_zs.dat` (pytdx/MIT 384-byte header, GBK 9-byte names,
2-byte count/type, 7-byte codes and 2,800-byte block slots), pipe-delimited
`tdxzs.cfg`/`tdxzs3.cfg`, and a fail-closed gbbq envelope parser. The gbbq
record layout and encrypted transform are based on pytdx's MIT
`gbbq_reader.py`; the proprietary key table and real client bytes remain
**unverified**. Fixture tests cover synthetic clear bytes only.
