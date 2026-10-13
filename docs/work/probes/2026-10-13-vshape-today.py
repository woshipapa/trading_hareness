"""Today's intraday shape from the stored market radar, beside the two previous sessions; and the daily picture of the last sessions."""
import json, sys
from datetime import datetime, timedelta, timezone
import pandas as pd
CN = timezone(timedelta(hours=8))
def series(day):
    d = json.load(open(f"radar_{day}.json"))
    rows = []
    for p in d["points"]:
        if p.get("phase") != "continuous":
            continue
        t = datetime.fromisoformat(p["observed_at"]).astimezone(CN)
        b2, br = p["bands"]["2"], p.get("breadth") or {}
        rows.append({"t": t.strftime("%H:%M"), "up": br.get("up"), "down": br.get("down"), "now_dn": b2["now_down"]["count"], "now_up": b2["now_up"]["count"],
                     "dn_to": b2["now_down"]["turnover"] / 1e8, "up_to": b2["now_up"]["turnover"] / 1e8, "turnover": p["pool"]["turnover"] / 1e8})
    f = pd.DataFrame(rows).drop_duplicates("t").set_index("t")
    m = pd.DataFrame([{"t": datetime.fromisoformat(x["observed_at"]).astimezone(CN).strftime("%H:%M"), "main_net": x["main_net"] / 1e8} for x in d["main_net"] if x.get("main_net") is not None]).drop_duplicates("t").set_index("t")
    f = f.join(m, how="left"); f["main_net"] = f["main_net"].ffill()
    f["per_min"] = f["turnover"].diff()
    return f
pd.set_option("display.width", 200)
for day in ("2026-10-13", "2026-10-12", "2026-10-09"):
    f = series(day)
    low_t = f["down"].idxmax(); net_t = f["main_net"].idxmin()
    print(f"\n== {day}: {len(f)} minute points {f.index[0]}..{f.index[-1]}")
    print(f"   most stocks down at {low_t}: down {int(f.down[low_t])} / up {int(f.up[low_t])}; below -2% then {int(f.now_dn[low_t])}; main net lowest at {net_t}: {f.main_net[net_t]:.0f} 亿")
    last = f.iloc[-1]
    print(f"   latest {f.index[-1]}: down {int(last.down)} / up {int(last.up)}; below -2% {int(last.now_dn)}, above +2% {int(last.now_up)}; main net {last.main_net:.0f} 亿; turnover {last.turnover:.0f} 亿")
    keys = [t for t in ("09:31", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:29", "13:01", "13:10", "13:20", "13:27", "14:00", "14:30", "14:56") if t in f.index]
    print(f.loc[keys, ["up", "down", "now_dn", "now_up", "main_net", "turnover"]].round(0).astype(int).T.to_string())
    i = f.index.get_loc(low_t)
    before, after = f["per_min"].iloc[max(1, i - 15):i].mean(), f["per_min"].iloc[i:i + 15].mean()
    print(f"   turnover per minute: 15 min before the low {before:.1f} 亿, 15 min after {after:.1f} 亿, first 15 min of the day {f['per_min'].iloc[1:16].mean():.1f} 亿")
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["trading_date", "ret", "amount", "limit_up_close", "limit_down_close", "is_st_like"])
p["trading_date"] = pd.to_datetime(p["trading_date"]); p = p[p.trading_date >= "2026-09-15"]
g = p.groupby("trading_date").agg(n=("ret", "size"), up=("ret", lambda s: int((s > 0).sum())), median=("ret", "median"), turnover=("amount", lambda s: s.sum() / 1e8 * 1000 / 1e4),
                                  lu=("limit_up_close", "sum"), ld=("limit_down_close", "sum"))
g["up_share"] = (g.up / g.n * 100).round(0); g["median"] = (g["median"] * 100).round(2); g["turnover"] = g["turnover"].round(2)
print("\n== daily picture (local panel, to 2026-10-12; turnover in trillion CNY, medians in %)")
print(g[["up_share", "median", "turnover", "lu", "ld"]].tail(12).to_string())
