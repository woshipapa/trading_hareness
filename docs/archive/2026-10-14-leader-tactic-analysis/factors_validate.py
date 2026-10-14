"""Read-only: does the pre_close chain reproduce the stored adjustment factors?  Window 2023-11-01..2023-12-29,
rows that already have a factor.  f(t) = f(t-1) * close(t-1)/pre_close(t) (the vendor's 后复权 convention)."""
import json
from collections import defaultdict
from datetime import date
from app.database import Database
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '120s'")
    rows = c.execute("""SELECT symbol, trading_date, close, pre_close, adj_factor FROM quant.canonical_bars_daily
                         WHERE trading_date BETWEEN '2023-10-25' AND '2023-12-29' AND symbol ~ '^[0-9]{6}\\.(SH|SZ)$'
                           AND close > 0 AND pre_close > 0 ORDER BY symbol, trading_date""").fetchall()
by = defaultdict(list)
for r in rows: by[r["symbol"]].append(r)
checked = agree = events = event_agree = 0; worst = []
for sym, series in by.items():
    anchor = None; cum = 1.0
    for i in range(1, len(series)):
        prev, cur = series[i-1], series[i]
        if prev["trading_date"] < date(2023, 11, 1): 
            continue
        ratio = float(prev["close"]) / float(cur["pre_close"])
        if anchor is None and prev["adj_factor"] is not None:
            anchor = (float(prev["adj_factor"]), 1.0); cum = 1.0
        if anchor is None: continue
        cum *= ratio
        if cur["adj_factor"] is not None:
            pred = anchor[0] * cum; stored = float(cur["adj_factor"])
            checked += 1
            ok = abs(pred / stored - 1) < 5e-4
            agree += ok
            if abs(ratio - 1) > 1e-3:
                events += 1; event_agree += ok
            if not ok and len(worst) < 8: worst.append((sym, str(cur["trading_date"]), round(pred, 4), stored, round(ratio, 4)))
print(json.dumps({"symbols": len(by), "checked": checked, "agree": agree, "agree_share": agree / max(1, checked),
                  "event_days": events, "event_agree": event_agree, "worst": worst}))
