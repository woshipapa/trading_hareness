"""Independent spot check of three claims from the returned studies (own code, same data)."""
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
cols = ["symbol", "trading_date", "di", "open", "high", "low", "close", "pre_close", "vr", "is_st_like", "limit_up_close",
        "open_at_limit_up", "overnight1", "ret1", "o2o", "next_open_at_limit_up"]
d = pd.read_parquet(D + "daily_panel.parquet", columns=cols)
d = d[~d.is_st_like].sort_values(["symbol", "di"]).reset_index(drop=True)
d["trading_date"] = pd.to_datetime(d["trading_date"])
g = d.groupby("symbol", sort=False)
f = (d["pre_close"] / g["close"].shift(1)).fillna(1.0)
cum = f.groupby(d["symbol"], sort=False).cumprod()
mult = cum.groupby(d["symbol"], sort=False).transform("last") / cum
ah, ac = d["high"] * mult, d["close"] * mult
ph20 = ah.groupby(d["symbol"], sort=False).transform(lambda s: s.shift(1).rolling(20, min_periods=20).max())
brk = ac > ph20
prior5 = brk.astype(float).groupby(d["symbol"], sort=False).transform(lambda s: s.shift(1).rolling(5, min_periods=5).sum())
d["first_break"] = brk & (prior5 == 0)
d["touch_fail"] = (ah >= 0.99 * ph20) & (ac < ph20)
d["one_word"] = d["limit_up_close"] & (d["high"] == d["low"])
d.loc[d["next_open_at_limit_up"] == 1, "o2o"] = np.nan
d["dev"] = d["trading_date"] <= "2025-12-31"
d = d[d["trading_date"] <= "2026-09-18"]
day_mean = d.groupby("trading_date")[["overnight1", "ret1", "o2o"]].transform("mean")
for c in ("overnight1", "ret1", "o2o"):
    d[c + "_x"] = d[c] - day_mean[c]

def stat(mask, col):
    out = []
    for dev in (True, False):
        s = d[mask & (d["dev"] == dev)].groupby("trading_date")[col].mean().dropna()
        out.append(f"{s.mean()*100:+.2f}% t={s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+.1f} d={len(s)}")
    return " | ".join(out)

print("stock-days: first_break", int(d.first_break.sum()), "touch_fail", int(d.touch_fail.sum()))
print("\n[1] first break of the 20-day high, next close minus the day's all-stock mean (dev | hold-out)")
for name, m in [("vr<1 all", d.vr < 1), ("vr<1 one-word boards", (d.vr < 1) & d.one_word), ("vr<1 limit-up close, not one-word", (d.vr < 1) & d.limit_up_close & ~d.one_word),
                ("vr<1 not limit-up close", (d.vr < 1) & ~d.limit_up_close), ("vr 1-2 not limit-up", (d.vr >= 1) & (d.vr < 2) & ~d.limit_up_close),
                ("vr 2-4 not limit-up", (d.vr >= 2) & (d.vr < 4) & ~d.limit_up_close), ("vr>=4 not limit-up", (d.vr >= 4) & ~d.limit_up_close)]:
    mm = d.first_break & m
    print(f"  {name:36s} n={int(mm.sum()):6d}  ret1_x {stat(mm, 'ret1_x')}  o2o_x {stat(mm, 'o2o_x')}")
print("\n[2] touched the 20-day high and closed back below it, minus the day's all-stock mean (dev | hold-out)")
for name, m in [("vr<1", d.vr < 1), ("vr 1-2", (d.vr >= 1) & (d.vr < 2)), ("vr 2-4", (d.vr >= 2) & (d.vr < 4)), ("vr>=4", d.vr >= 4)]:
    mm = d.touch_fail & m
    print(f"  {name:8s} n={int(mm.sum()):6d}  overnight_x {stat(mm, 'overnight1_x')}  ret1_x {stat(mm, 'ret1_x')}  o2o_x {stat(mm, 'o2o_x')}")

L = pd.read_parquet(D + "limit_up_lists.parquet")
print("\nlist columns:", list(L.columns))
