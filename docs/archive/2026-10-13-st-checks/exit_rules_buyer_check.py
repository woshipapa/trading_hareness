"""The same question for a buyer: yesterday's limit-up stock bought at the open of T+1 (not at the limit), sold on T+2 by fixed rules."""
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["symbol", "trading_date", "di", "open", "high", "low", "close", "pre_close",
    "is_st_like", "limit_up_close", "open_at_limit_up", "streak"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
p = p[p["trading_date"] <= "2026-09-18"].sort_values(["symbol", "di"]).reset_index(drop=True)
g = p.groupby("symbol", sort=False)
for k in (1, 2, 3):
    ok = g["di"].shift(-k) == p["di"] + k
    for c in ("open", "high", "low", "close", "pre_close", "limit_up_close", "open_at_limit_up"):
        p[f"n{k}_{c}"] = g[c].shift(-k).where(ok)
y = p[p["limit_up_close"] & ~p["is_st_like"] & p["n2_close"].notna() & (p["n1_open_at_limit_up"] != 1)].copy()
gap = y["n1_open"] / y["n1_pre_close"] - 1
k = y["n1_close"] / y["n1_open"]
o2, h2, l2, c2 = (k * y[f"n2_{x}"] / y["n2_pre_close"] - 1 for x in ("open", "high", "low", "close"))
sealed1, sealed2 = y["n1_limit_up_close"].astype(bool), y["n2_limit_up_close"].astype(bool)
o3 = (1 + c2) * (y["n3_open"] / y["n3_pre_close"]).fillna(1.0) - 1
R = pd.DataFrame(index=y.index)
R["次日最高价（上限，做不到）"] = h2
R["次日开盘卖"] = o2
R["次日收盘卖"] = c2
for x in (0.03, 0.05, 0.08):
    R[f"盈利{int(x*100)}%止盈，否则次日收盘"] = np.where(o2 >= x, o2, np.where(h2 >= x, x, c2))
R["亏3%止损，否则次日收盘"] = np.where(o2 <= -0.03, o2, np.where(l2 <= -0.03, -0.03, c2))
R["买入当天封板才留到次日，否则次日开盘卖"] = np.where(sealed1, np.where(sealed2, o3, c2), o2)
R["次日封住续拿到第三天开盘，否则次日收盘卖"] = np.where(sealed2, o3, c2)
R["trading_date"] = y["trading_date"]; R["dev"] = R["trading_date"] <= "2025-12-31"; R["bb"] = y["streak"].clip(upper=3)

def line(frame, col):
    out = []
    for dev in (True, False):
        s = frame[frame.dev == dev].groupby("trading_date")[col].mean()
        out.append(f"{s.mean()*100:+5.2f}% (t {s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+5.1f})")
    return " | ".join(out)

cols = [c for c in R.columns if c not in ("trading_date", "dev", "bb")]
for label, sub in [("昨日涨停股，开盘可买的全部", R), ("低开超过2%", R[gap < -0.02]), ("高开0-5%", R[(gap >= 0) & (gap < 0.05)]), ("高开5%以上未一字", R[gap >= 0.05]),
                   ("连板股（2板以上），高开0-5%", R[(R.bb >= 2) & (gap >= 0) & (gap < 0.05)])]:
    print(f"\n== {label}: {len(sub)} 个股票日；买在开盘价，日均值 开发段 | 留出段")
    for col in cols:
        print(f"  {col:32s} {line(sub, col)}")
