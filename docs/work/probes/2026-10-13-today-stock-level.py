"""W007: the session of 2026-10-13 at stock level, from the whole-market minute snapshots at key minutes (local copy) and the
local daily panel up to 2026-10-12. Descriptive first look: who fell, who led the rebound from the market low (11:18), who kept it."""
import json
import numpy as np, pandas as pd
R = "/Users/papa/codebase/n8n/state/research/"
d = json.load(open(R + "vshape_20261013/data/today_snapshots.json"))
last = d["minutes"]["14:59"]["columns"]
f = pd.DataFrame({"symbol": last["symbol"], "prev": last["raw.prev_price"], "open": last["raw.open_price"], "high": last["raw.high_price"], "low": last["raw.low_price"],
                  "amount": last["turnover"]}).set_index("symbol")
for key, doc in d["minutes"].items():
    c = doc["columns"]
    f["p" + key] = pd.Series(c["price"], index=c["symbol"]).reindex(f.index)
    f["a" + key] = pd.Series(c["turnover"], index=c["symbol"]).reindex(f.index)
f = f.apply(pd.to_numeric, errors="coerce")
f = f[(f.prev > 0) & (f.open > 0) & (f["p14:59"] > 0)]
p = pd.read_parquet(R + "shortterm_tactics_20261013/data/daily_panel.parquet", columns=["symbol", "trading_date", "ret", "limit_up_close", "limit_down_close", "streak", "amount20", "is_st_like", "limit_pct"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
tail = p[p.trading_date >= "2026-09-29"].sort_values(["symbol", "trading_date"])
r5 = tail.groupby("symbol")["ret"].apply(lambda s: float(np.prod(1 + s.tail(5)) - 1)).rename("ret5")
y = p[p.trading_date == "2026-10-12"].set_index("symbol")
f = f.join(y[["ret", "limit_up_close", "limit_down_close", "streak", "amount20", "is_st_like", "limit_pct"]]).join(r5)
f = f[f.is_st_like == False]
t = lambda k: f["p" + k] / f["prev"] - 1
f["gap"] = f["open"] / f["prev"] - 1
f["at_low"] = t("11:18"); f["day"] = t("14:59")
f["leg_up"] = f["p13:30"] / f["p11:18"] - 1
f["fade"] = f["p14:59"] / f["p13:30"] - 1
f["low_to_close"] = f["p14:59"] / f["p11:18"] - 1
f["own_low"] = f["low"] / f["prev"] - 1
f["rebound_share"] = (f["a13:30"] - f["a11:18"]) / f["amount"]
f["limit"] = f["limit_pct"].fillna(0.10)
f["at_limit_close"] = f["day"] >= f["limit"] - 0.003
pc = lambda s: f"{s*100:+.2f}%"
print(f"stocks {len(f)} (non-ST with yesterday's row)")
print("\n[1] the median stock against yesterday's close, by time")
print("  " + "  ".join(f"{k} {pc(t(k).median())}" for k in d["minutes"]))
print("  share above yesterday's close: " + "  ".join(f"{k} {(t(k) > 0).mean()*100:.0f}%" for k in ("09:31", "10:15", "10:30", "11:18", "11:29", "13:30", "14:59")))
print(f"  open gap median {pc(f.gap.median())}; own low of the day median {pc(f.own_low.median())}; close median {pc(f.day.median())}")
print(f"  from the 11:18 level: to 13:30 median {pc(f.leg_up.median())}, 13:30 to close {pc(f.fade.median())}, 11:18 to close {pc(f.low_to_close.median())}")

def table(title, groups, cols=("at_low", "leg_up", "fade", "low_to_close", "day")):
    print(f"\n{title}\n  {'group':34s} {'n':>5s} " + " ".join(f"{c:>13s}" for c in cols) + "  share of day's turnover in 11:18-13:30")
    for name, mask in groups:
        g = f[mask]
        if len(g) >= 5:
            print(f"  {name:34s} {len(g):5d} " + " ".join(f"{pc(g[c].median()):>13s}" for c in cols) + f"  {g.rebound_share.median()*100:.0f}%")

q = pd.qcut(f.at_low, 5, labels=False)
table("[2] by how deep the stock was at 11:18 (fifths; medians)", [(f"fifth {i+1} at 11:18 ({pc(f.at_low[q==i].min())}..{pc(f.at_low[q==i].max())})", q == i) for i in range(5)])
table("[3] by yesterday's state", [("limit-up close yesterday", f.limit_up_close == True), ("  of which 2+ boards", (f.limit_up_close == True) & (f.streak >= 2)),
                                   ("limit-down close yesterday", f.limit_down_close == True), ("fell 5%+ yesterday, not at limit", (f.ret <= -0.05) & (f.limit_down_close != True)),
                                   ("rose yesterday", f.ret > 0), ("5-day return in the worst fifth", f.ret5 <= f.ret5.quantile(0.2)), ("5-day return in the best fifth", f.ret5 >= f.ret5.quantile(0.8))])
board = np.where(f.index.str.startswith("68"), "STAR", np.where(f.index.str.startswith("30"), "ChiNext", "main"))
lq = pd.qcut(f.amount20, 5, labels=False)
table("[4] by board and by liquidity (20-day average turnover)", [(b, board == b) for b in ("main", "ChiNext", "STAR")] + [(f"liquidity fifth {i+1}", lq == i) for i in (0, 2, 4)])
table("[5] by behaviour before the low", [("above yesterday's close at 11:18", f.at_low > 0), ("opened down 3%+ and still below the open at 11:18", (f.gap <= -0.03) & (f["p11:18"] < f["open"])),
                                          ("rose 11:00-11:18 while the market fell", f["p11:18"] > f["p11:00"] * 1.003), ("fell 2%+ in 11:00-11:18", f["p11:18"] < f["p11:00"] * 0.98),
                                          ("up in the failed bounce 10:00-10:15 by 1%+", f["p10:15"] > f["p10:00"] * 1.01)])
lim = f[f.at_limit_close]
first = pd.Series({s: next((k for k in d["minutes"] if (f.at[s, "p" + k] / f.at[s, "prev"] - 1) >= f.at[s, "limit"] - 0.003), None) for s in lim.index})
print(f"\n[6] at the up-limit at 14:59: {len(lim)} stocks; first key minute already at the limit:")
print("  " + "  ".join(f"{k} {int((first == k).sum())}" for k in d["minutes"]))
print(f"  of them: limit-up yesterday {int((lim.limit_up_close == True).sum())}; below yesterday's close at 11:18 {int((lim.at_low < 0).sum())}; median level at 11:18 {pc(lim.at_low.median())}")
dn = f[f.day <= -(f.limit - 0.003)]
print(f"  at the down-limit at 14:59: {len(dn)}; at the down-limit level at 11:18: {int((f.at_low <= -(f.limit - 0.003)).sum())}")
for col in ("at_low", "ret", "ret5", "gap", "amount20"):
    v = f[[col, "leg_up", "low_to_close"]].dropna()
    print(f"  rank correlation of {col:8s} with the leg up {v[col].corr(v.leg_up, method='spearman'):+.2f}, with 11:18-to-close {v[col].corr(v.low_to_close, method='spearman'):+.2f}")
