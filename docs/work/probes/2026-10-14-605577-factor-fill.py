"""Give 605577.SH (龙版传媒) its adjustment factor on the daily bars of 2023 that carry none.

The user, 2026-10-14: "同时把他的也补充上。龙版传媒（605577）缺失的复权日线". What is missing (probes
2026-10-14-605577-daily-bars, -missing-dates, -factor-gap-2023): 48 of the 61 bars from 2023-08-15 to 2023-11-15 have no
adj_factor, so no adjusted bar can be built for those sessions. (2026-09-09..11 have no bar because the stock did not
trade: the bar of 09-14 has the close of 09-08 as its previous close, and the licensed history has no data for the
three days. Nothing is written for them.)

The factor is written only if three things hold in the whole range, otherwise the script stops:
  1. no break in the price chain: every bar's pre_close equals the close of the bar before (no ex-rights session);
  2. every adj_factor already on a canonical bar of the range is the same value;
  3. every row the factor table holds for the range, from any provider, is that value.
Then that value is the factor of every session of the range. It goes in through the service's own entry for factors
(persist_tushare_rows "adj_factor", provider longhu_qfq_derived: one evidence row, one factor row, and adj_factor on the
canonical bar, per session), with the method and what it rests on in each row.

Run inside the owner's quant-research container from the overlay source, stdin = this file:
  ... python -            dry run: the checks and what would be written; writes nothing
  ... python - --apply    writes (48 sessions); running it again writes nothing new
"""
import resource
import sys
from datetime import date, datetime, timezone

from app.database import Database

SYMBOL, FIRST, LAST = "605577.SH", date(2023, 8, 15), date(2023, 11, 15)
APPLY = "--apply" in sys.argv
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    bars = [dict(row) for row in connection.execute(
        """SELECT trading_date, close, pre_close, adj_factor FROM quant.canonical_bars_daily
            WHERE symbol=%s AND trading_date BETWEEN %s AND %s ORDER BY trading_date""", (SYMBOL, FIRST, LAST)).fetchall()]
    table = [dict(row) for row in connection.execute(
        """SELECT trading_date, provider, adj_factor FROM quant.daily_adjustment_factors
            WHERE symbol=%s AND trading_date BETWEEN %s AND %s""", (SYMBOL, FIRST, LAST)).fetchall()]
breaks = [str(bar["trading_date"]) for before, bar in zip(bars, bars[1:])
          if bar["pre_close"] is None or abs(bar["pre_close"] - before["close"]) > 0.005]
on_bars = {bar["adj_factor"] for bar in bars if bar["adj_factor"] is not None}
in_table = {row["adj_factor"] for row in table}
missing = [bar["trading_date"] for bar in bars if bar["adj_factor"] is None]
print(f"{SYMBOL} {FIRST}..{LAST}: bars {len(bars)}, without adj_factor {len(missing)}")
print(f"  1. price-chain breaks: {breaks or 'none'}")
print(f"  2. factors on the canonical bars: {sorted(str(value) for value in on_bars)}")
print(f"  3. factors in the factor table ({len(table)} rows, providers {sorted({row['provider'] for row in table})}): {sorted(str(value) for value in in_table)}")
if breaks or len(on_bars) != 1 or in_table != on_bars:
    raise SystemExit("STOP: the three conditions do not hold; nothing written")
if not missing:
    raise SystemExit("nothing to write: every bar of the range has its factor")
factor = on_bars.pop()
anchor = next(bar for bar in bars if bar["adj_factor"] is not None)
rows = [{
    "ts_code": SYMBOL, "trade_date": day.strftime("%Y%m%d"), "adj_factor": str(factor), "step": 1.0,
    "method": "constant_factor_over_unbroken_price_chain_v1",
    "anchor": {"date": str(anchor["trading_date"]), "kind": "stored_factor", "adj_factor": str(factor)},
    "basis": f"no pre_close break from {FIRST} to {LAST}; every stored factor of the range equals the anchor",
} for day in missing]
print(f"  to write: adj_factor {factor} on {len(rows)} sessions, {missing[0]} .. {missing[-1]}")
if not APPLY:
    raise SystemExit("dry run: nothing written (pass --apply to write)")

from app.main import persist_tushare_rows  # the whole service is imported only to write

with database.transaction() as connection:
    written = persist_tushare_rows(connection, "adj_factor", f"manual:{SYMBOL}:adj_factor:{missing[0]}..{missing[-1]}",
                                   rows, "longhu_qfq_derived", datetime.now(timezone.utc))
    left = connection.execute(
        "SELECT count(*) AS n FROM quant.canonical_bars_daily WHERE symbol=%s AND trading_date BETWEEN %s AND %s AND adj_factor IS NULL",
        (SYMBOL, FIRST, LAST)).fetchone()["n"]
print(f"written: {written} of {len(rows)}; bars of the range still without a factor: {left}; "
      f"peak memory of this process {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB")
