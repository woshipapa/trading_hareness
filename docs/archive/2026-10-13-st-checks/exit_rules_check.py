"""How much the exit rule changes the result: yesterday's limit-up stocks held from the close of T, sold on T+1 by fixed rules.
Daily bars only, so a day that touches both a target and a stop is bracketed (stop first / target first)."""
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["symbol", "trading_date", "di", "open", "high", "low", "close", "pre_close",
    "is_st_like", "limit_up_close", "limit_down_close", "open_at_limit_up", "overnight1", "streak", "vr"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
p = p[p["trading_date"] <= "2026-09-18"].sort_values(["symbol", "di"]).reset_index(drop=True)
g = p.groupby("symbol", sort=False)
ok = g["di"].shift(-1) == p["di"] + 1
for c in ("open", "high", "low", "close", "pre_close", "limit_up_close", "open_at_limit_up", "overnight1"):
    p["n_" + c] = g[c].shift(-1).where(ok)
y = p[p["limit_up_close"] & ~p["is_st_like"] & p["n_close"].notna()].copy()
o, h, l, c = (y["n_" + k] / y["n_pre_close"] - 1 for k in ("open", "high", "low", "close"))
y["gap"] = o
sealed = y["n_limit_up_close"].astype(bool)
R = pd.DataFrame(index=y.index)
R["最高价（上限，做不到）"] = h
R["开盘卖"] = o
R["收盘卖"] = c
for x in (0.03, 0.05, 0.07):
    R[f"冲高{int(x*100)}%止盈，否则收盘"] = np.where(o >= x, o, np.where(h >= x, x, c))
R["跌3%止损，否则收盘"] = np.where(o <= -0.03, o, np.where(l <= -0.03, -0.03, c))
tp, sl = 0.05, -0.03
R["止盈5%+止损3%（先触止损）"] = np.where(o >= tp, o, np.where(o <= sl, o, np.where(l <= sl, sl, np.where(h >= tp, tp, c))))
R["止盈5%+止损3%（先触止盈）"] = np.where(o >= tp, o, np.where(o <= sl, o, np.where(h >= tp, tp, np.where(l <= sl, sl, c))))
nxt_open = (1 + c) * (1 + y["n_overnight1"].fillna(0)) - 1
R["封住就拿到后天开盘，没封收盘卖"] = np.where(sealed, nxt_open, c)
R["按跳空处置：一字拿、高开≥5%开盘卖、其余收盘卖"] = np.where(y["n_open_at_limit_up"] == 1, np.where(sealed, nxt_open, c), np.where(o >= 0.05, o, c))
R["按跳空处置＋封住续拿"] = np.where(y["n_open_at_limit_up"] == 1, np.where(sealed, nxt_open, c), np.where(o >= 0.05, o, np.where(sealed, nxt_open, c)))
R["trading_date"] = y["trading_date"]; R["bb"] = y["streak"].clip(upper=3)
R["dev"] = R["trading_date"] <= "2025-12-31"

def line(frame, col):
    out = []
    for dev in (True, False):
        s = frame[frame.dev == dev].groupby("trading_date")[col].mean()
        out.append(f"{s.mean()*100:+5.2f}% (t {s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+5.1f})")
    return " | ".join(out)

cols = [k for k in R.columns if k not in ("trading_date", "bb", "dev")]
for label, sub in [("昨日全部涨停股", R), ("首板", R[R.bb == 1]), ("连板（2板及以上）", R[R.bb >= 2]),
                   ("放量板 量比>=3", R[(y.vr >= 3)]), ("缩量或平量板 量比<1.5", R[(y.vr < 1.5)])]:
    print(f"\n== {label}: {len(sub)} 个股票日；持有者从涨停日收盘算起，日均值 开发段 | 留出段")
    for col in cols:
        print(f"  {col:34s} {line(sub, col)}")
hi = (h - c)
print("\n最高价比收盘价高出（日均）:", f"{hi[R.dev].groupby(R.trading_date[R.dev]).mean().mean()*100:.2f}% | {hi[~R.dev].groupby(R.trading_date[~R.dev]).mean().mean()*100:.2f}%",
      "; 最高价出现在开盘价的占比:", f"{(h <= o + 1e-9).mean()*100:.1f}%", "; 当日同时触及+5%和-3%的占比:", f"{((h >= tp) & (l <= sl) & (o < tp) & (o > sl)).mean()*100:.1f}%")
