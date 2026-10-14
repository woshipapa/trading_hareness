"""Read-only dry run of B002: fill 2023-08-15..2023-11-30's missing adjustment factors from the pre_close chain,
anchored on the stock's earliest stored factor in the window.  f(t) = f(t-1) * close(t-1)/pre_close(t).
Prints counts and the agreement with every factor that is already stored (in the whole window, both directions)."""
import json
from collections import Counter, defaultdict
from app.database import Database
START, END = "2023-08-01", "2023-12-29"
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '120s'")
    symbols = [r["symbol"] for r in c.execute(
        """SELECT DISTINCT symbol FROM quant.canonical_bars_daily
            WHERE trading_date BETWEEN %s AND %s AND symbol ~ '^[0-9]{6}\\.(SH|SZ|BJ)$' ORDER BY 1""", (START, END)).fetchall()]
stats = Counter(); by_month_missing = Counter(); by_month_fill = Counter(); no_anchor = []; worst = []; ratio_events = 0
for i in range(0, len(symbols), 400):
    batch = symbols[i:i + 400]
    with database.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '120s'")
        rows = c.execute("""SELECT symbol, trading_date, close, pre_close, adj_factor, is_suspended FROM quant.canonical_bars_daily
                             WHERE trading_date BETWEEN %s AND %s AND symbol = ANY(%s) ORDER BY symbol, trading_date""",
                         (START, END, batch)).fetchall()
    by = defaultdict(list)
    for r in rows: by[r["symbol"]].append(r)
    for sym, s in by.items():
        valid = [k for k, r in enumerate(s) if r["close"] and r["pre_close"] and float(r["close"]) > 0 and float(r["pre_close"]) > 0]
        if len(valid) != len(s):
            stats["symbols_with_unusable_rows"] += 1
        anchor = next((k for k, r in enumerate(s) if r["adj_factor"] is not None and float(r["adj_factor"]) > 0), None)
        missing_idx = [k for k, r in enumerate(s) if r["adj_factor"] is None]
        for k in missing_idx: by_month_missing[str(s[k]["trading_date"])[:7]] += 1
        if anchor is None:
            no_anchor.append(sym); stats["rows_without_anchor"] += len(missing_idx); continue
        f = [None] * len(s); f[anchor] = float(s[anchor]["adj_factor"])
        ok_chain = True
        for k in range(anchor + 1, len(s)):               # forward
            if not (s[k]["pre_close"] and s[k - 1]["close"]): ok_chain = False; break
            f[k] = f[k - 1] * float(s[k - 1]["close"]) / float(s[k]["pre_close"])
        for k in range(anchor - 1, -1, -1):               # backward
            if not (s[k + 1]["pre_close"] and s[k]["close"]) or f[k + 1] is None:
                break
            f[k] = f[k + 1] * float(s[k + 1]["pre_close"]) / float(s[k]["close"])
        for k, r in enumerate(s):
            if f[k] is None: continue
            if r["adj_factor"] is not None:
                stats["checked"] += 1
                good = abs(f[k] / float(r["adj_factor"]) - 1) < 5e-4
                stats["agree"] += good
                if not good and len(worst) < 8: worst.append((sym, str(r["trading_date"]), round(f[k], 4), float(r["adj_factor"])))
            else:
                stats["fillable"] += 1; by_month_fill[str(r["trading_date"])[:7]] += 1
        stats["symbols"] += 1
print(json.dumps({"symbols_total": len(symbols), **stats, "missing_by_month": dict(sorted(by_month_missing.items())),
                  "fillable_by_month": dict(sorted(by_month_fill.items())), "symbols_without_anchor": len(no_anchor),
                  "sample_without_anchor": no_anchor[:10], "worst": worst}))
