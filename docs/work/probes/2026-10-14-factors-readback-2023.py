"""Read-only readback after the 2023 factor write."""
import json
from collections import defaultdict
from app.database import Database
database = Database(); database.open()
out = {}
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '120s'")
    out["by_month"] = [dict(r) for r in c.execute("""SELECT to_char(trading_date,'YYYY-MM') AS month, count(*) AS bars, count(adj_factor) AS with_factor
                         FROM quant.canonical_bars_daily WHERE trading_date BETWEEN '2023-08-15' AND '2024-01-31'
                           AND symbol ~ '^[0-9]{6}\\.(SH|SZ)$' GROUP BY 1 ORDER BY 1""").fetchall()]
    out["written"] = dict(c.execute("""SELECT count(*) AS rows, count(DISTINCT trading_date) AS sessions, min(trading_date) AS first, max(trading_date) AS last
                         FROM quant.daily_adjustment_factors WHERE provider='longhu_qfq_derived' AND raw->>'method'='pre_close_chain_v1'""").fetchone())
    out["fetch_runs"] = dict(c.execute("""SELECT count(*) AS runs, sum(row_count) AS rows FROM quant.fetch_runs
                         WHERE capability='adj_factor_pre_close_chain' AND status='completed'""").fetchone())
    out["raw_records"] = dict(c.execute("""SELECT count(*) AS n FROM quant.tushare_raw_records WHERE provider_key='longhu_qfq_derived'
                         AND api_name='adj_factor' AND row_data->>'method'='pre_close_chain_v1'""").fetchone())
    rows = c.execute("""SELECT symbol, trading_date, close, pre_close, adj_factor FROM quant.canonical_bars_daily
                         WHERE trading_date BETWEEN '2023-08-01' AND '2023-12-29' AND symbol ~ '^[0-9]{6}\\.(SH|SZ)$'
                           AND close > 0 AND pre_close > 0 ORDER BY symbol, trading_date""").fetchall()
by = defaultdict(list)
for r in rows: by[r["symbol"]].append(r)
pairs = agree = events = event_agree = 0; example = None
for sym, s in by.items():
    for k in range(1, len(s)):
        a, b = s[k - 1], s[k]
        if a["adj_factor"] is None or b["adj_factor"] is None: continue
        pairs += 1
        implied = float(a["close"]) / float(b["pre_close"])
        actual = float(b["adj_factor"]) / float(a["adj_factor"])
        ok = abs(actual / implied - 1) < 1e-3
        agree += ok
        if abs(implied - 1) > 2e-3:
            events += 1; event_agree += ok
            if example is None and 3 <= k < len(s) - 3 and str(b["trading_date"]) < "2023-11-01":
                example = {"symbol": sym, "ex_date": str(b["trading_date"]),
                           "factors": [[str(x["trading_date"]), float(x["adj_factor"]) if x["adj_factor"] is not None else None] for x in s[k - 2:k + 2]]}
out["chain_check"] = {"consecutive_pairs": pairs, "agree": agree, "agree_share": agree / max(1, pairs),
                      "event_pairs": events, "event_agree": event_agree, "example": example}
print(json.dumps(out, default=str))
