"""W007, hypothesis 3 on history, from the local daily panel (2023-08..2026-10-12): days on which the median stock fell deep and came back
(a V day by daily bars: each stock's own low, not one common minute), who led from the low to the close, and what the next day gave.
A first look: one definition, not tuned; thresholds are stated, not searched."""
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["symbol", "trading_date", "di", "open", "low", "close", "pre_close", "ret", "is_st_like",
                                                        "limit_up_close", "limit_down_close", "overnight1", "ret1", "o2o", "next_open_at_limit_up"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
p = p[~p.is_st_like].sort_values(["symbol", "di"]).reset_index(drop=True)
lr = np.log1p(p["ret"].clip(lower=-0.5))
cum = lr.groupby(p["symbol"]).cumsum()
g = cum.groupby(p["symbol"])
p["ret5"] = np.exp(g.shift(1) - g.shift(6)) - 1
p["to_low"] = p["low"] / p["pre_close"] - 1
p["low_to_close"] = p["close"] / p["low"] - 1
p["o2o_buy"] = p["o2o"].where(p["next_open_at_limit_up"] != 1)
day = p.groupby("trading_date").agg(n=("ret", "size"), med_low=("to_low", "median"), med_rebound=("low_to_close", "median"), med_ret=("ret", "median"),
                                    up=("ret", lambda s: (s > 0).mean()), ld=("limit_down_close", "sum"), next_med=("ret1", "median"), next_open=("overnight1", "median"))
day["v"] = (day.med_low <= -0.02) & (day.med_rebound >= 0.02)
day["one_sided"] = (day.med_low <= -0.02) & (day.med_rebound < 0.01)
pc = lambda x: f"{x*100:+.2f}%"
def tstat(s):
    s = s.dropna()
    return f"{s.mean()*100:+.2f}% (t {s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+.1f}, {len(s)}d)" if len(s) > 2 else f"{s.mean()*100:+.2f}% ({len(s)}d)"
print(f"sessions {len(day)}; deep-low days (median stock's low <= -2%): {int((day.med_low <= -0.02).sum())}; V days (and median low-to-close >= +2%): {int(day.v.sum())}; one-sided (low-to-close < 1%): {int(day.one_sided.sum())}")
by = day.assign(year=day.index.year, half=np.where(day.index >= "2026-07-01", "2026-07..10", "earlier"))
print("V days by year:", by[by.v].groupby("year").size().to_dict(), "| since 2026-07-01:", int(by[(by.half == '2026-07..10') & by.v].shape[0]), "of", int((by.half == '2026-07..10').sum()), "sessions")
print("\nV days since 2026-07-01 (median low, low-to-close, day, next day's median):")
for d, r in day[day.v & (day.index >= "2026-07-01")].iterrows():
    print(f"  {d:%m-%d}  low {pc(r.med_low)}  low-to-close {pc(r.med_rebound)}  day {pc(r.med_ret)}  up {r.up*100:.0f}%  next open {pc(r.next_open) if pd.notna(r.next_open) else 'n/a'}  next close {pc(r.next_med) if pd.notna(r.next_med) else 'n/a'}")
print("\n[A] the day after, median stock (day means; all | since 2026-07)")
for name, m in (("after a V day", day.v), ("after a one-sided down day", day.one_sided), ("after any other day", ~day.v & ~day.one_sided)):
    print(f"  {name:28s} next open {tstat(day.next_open[m])} | {tstat(day.next_open[m & (day.index >= '2026-07-01')])} ; next close {tstat(day.next_med[m])} | {tstat(day.next_med[m & (day.index >= '2026-07-01')])}")
v = p[p.trading_date.isin(day.index[day.v])].dropna(subset=["ret5"]).copy()
v["q5"] = v.groupby("trading_date")["ret5"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop"))
v["qlow"] = v.groupby("trading_date")["to_low"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop"))
def spread(frame, key, col):
    t = frame.groupby(["trading_date", key])[col].mean().unstack()
    return t[0] - t[4]
print("\n[B] on V days: the fifth with the worst prior 5-day return minus the best fifth (day means; all V days | since 2026-07)")
for col, label in (("low_to_close", "own low to the close"), ("ret", "the V day itself"), ("overnight1", "close to next open"), ("ret1", "close to next close"), ("o2o_buy", "next open to the open after")):
    s = spread(v, "q5", col)
    print(f"  {label:28s} {tstat(s)} | {tstat(s[s.index >= '2026-07-01'])}")
print("[C] on V days: the fifth that fell deepest intraday minus the shallowest fifth")
for col, label in (("low_to_close", "own low to the close"), ("overnight1", "close to next open"), ("ret1", "close to next close"), ("o2o_buy", "next open to the open after")):
    s = spread(v, "qlow", col)
    print(f"  {label:28s} {tstat(s)} | {tstat(s[s.index >= '2026-07-01'])}")
print("[D] on V days, absolute, by prior 5-day fifth (worst .. best): close to next close, day means")
t = v.groupby(["trading_date", "q5"])["ret1"].mean().unstack()
print("  all V days:   " + "  ".join(pc(t[i].mean()) for i in range(5)))
t2 = t[t.index >= "2026-07-01"]
print("  since 2026-07: " + "  ".join(pc(t2[i].mean()) for i in range(5)))
n = p[p.trading_date.isin(day.index[~day.v])].dropna(subset=["ret5"]).copy()
n["q5"] = n.groupby("trading_date")["ret5"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop"))
s = spread(n, "q5", "ret1")
print(f"[E] the same worst-minus-best spread of close to next close on all other days: {tstat(s)} | {tstat(s[s.index >= '2026-07-01'])}")
