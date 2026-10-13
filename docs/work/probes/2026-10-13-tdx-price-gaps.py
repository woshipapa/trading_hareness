"""Read-only probe on the owner (W008 prerequisite 3): the single-symbol price gaps of 9.5%-10.8% between the TDX shadow
snapshot and the main snapshot at 09:30, 09:33 and 09:54 on 2026-10-13. For each symbol it prints both sources' rows at
those minutes and the minutes around, with each document's own data time (main: upstream timestamp and cache age;
TDX: when its read returned). Cumulative volume only grows during a session, so of two observations of one symbol
the one with the smaller volume saw the market earlier.
One document is read at a time (single-row lookups by key), decoded with the service's own decoder, and dropped.
Run inside the owner's main service container (calling convention: W008 record, "Resume here")."""
import json
from datetime import datetime, timedelta, timezone
from app.database import Database
from app.minute_cross_section import rows_for
CN = timezone(timedelta(hours=8))
day = datetime(2026, 10, 13, tzinfo=CN)
db = Database(); db.open()
def q(sql, params):
    with db.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '20s'")
        return [dict(r) for r in c.execute(sql, params).fetchall()]
def at(hhmm):
    h, m = hhmm.split(":")
    return day.replace(hour=int(h), minute=int(m))
def hms(t): return t.astimezone(CN).strftime("%H:%M:%S")
def js(v): return v if isinstance(v, (dict, list)) or v is None else json.loads(v)
SOURCES = (("main", "fuyao_ths", "a_share_minute_cross_section"), ("tdx", "tdx_public", "a_share_minute_cross_section_shadow"))
WINDOWS = (("09:27", "09:37", ("000609.SZ", "301660.SZ")), ("09:51", "09:58", ("002549.SZ",)))
print("probe ran at", datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S"), "Asia/Shanghai")
for start, end, symbols in WINDOWS:
    observations = {symbol: [] for symbol in symbols}
    for label, provider, capability in SOURCES:
        keys = q("""SELECT effective_at FROM quant.raw_market_observations
                     WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at < %s
                     ORDER BY effective_at""", (provider, capability, at(start), at(end)))
        for key in keys:
            row = q("""SELECT effective_at, normalized FROM quant.raw_market_observations
                        WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at = %s LIMIT 1""",
                    (provider, capability, key["effective_at"]))[0]
            document = js(row["normalized"])
            metadata = document.get("metadata") or {}
            if label == "main":
                data_time = datetime.fromisoformat(metadata["upstream_observed_at"]) if metadata.get("upstream_observed_at") else None
                note = f"cache {metadata.get('status')} age {metadata.get('age_seconds')} s"
            else:
                data_time = datetime.fromisoformat(str(metadata["available_at"]).replace(" ", "T")) if metadata.get("available_at") else None
                note = "read returned"
            found = rows_for(document, list(symbols))
            for symbol in symbols:
                r = found.get(symbol)
                observations[symbol].append((data_time or row["effective_at"], label, row["effective_at"], data_time, note, document.get("rows"), r))
            del document, found
    for symbol in symbols:
        print(f"[{symbol}] {start}-{end}, ordered by each document's data time")
        for _, label, effective_at, data_time, note, count, r in sorted(observations[symbol], key=lambda item: item[0]):
            head = f"  {label:4} stored {hms(effective_at)} data {hms(data_time) if data_time else '-':8} ({note})"
            if r is None:
                print(head, f"| not in this document ({count} rows)")
                continue
            raw = r.get("raw") or {}
            print(head, f"| price {r.get('price')}  pct {round(r['pct_change'], 2) if r.get('pct_change') is not None else None}  volume {r.get('volume')}  turnover {r.get('turnover')}"
                        f"  open {raw.get('open_price')} high {raw.get('high_price')} low {raw.get('low_price')} prev {raw.get('prev_price')}")
