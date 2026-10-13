"""First historical look at the teacher's rules that the shared data can express (limit-up closes; day switches)."""
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["symbol", "trading_date", "di", "high", "low", "close", "pre_close", "volume", "amount",
    "vr", "is_st_like", "limit_up_close", "streak", "ret1", "o2o", "next_open_at_limit_up"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
p = p[p["trading_date"] <= "2026-09-18"].sort_values(["symbol", "di"]).reset_index(drop=True)
bad = (p["volume"] > 0) & (p["amount"] / (p["volume"] * p["close"] / 10) > 50)
p.loc[bad, "amount"] /= 1000
p["cny"] = p["amount"] * 1000
g = p.groupby("symbol", sort=False)
f = (p["pre_close"] / g["close"].shift(1)).fillna(1.0)
cum = f.groupby(p["symbol"], sort=False).cumprod()
ac = p["close"] * cum.groupby(p["symbol"], sort=False).transform("last") / cum
p["run20"] = ac / ac.groupby(p["symbol"], sort=False).shift(20) - 1
p["max250"] = p.groupby("symbol", sort=False)["cny"].transform(lambda s: s.shift(1).rolling(250, min_periods=60).max())
nxt = g["di"].shift(-1) == p["di"] + 1
p["seal_next"] = g["limit_up_close"].shift(-1).where(nxt).astype(float)
p["o2o_buy"] = p["o2o"].where(p["next_open_at_limit_up"] != 1)
p["big_loss"] = (p["ret1"] <= -0.05).astype(float).where(p["ret1"].notna())
p["one_word"] = p["high"] == p["low"]
market = p.groupby("trading_date")["cny"].sum()

L = pd.read_parquet(D + "limit_up_lists.parquet", columns=["symbol", "trading_date", "seal_hm", "sector", "boards", "seal_money"])
L["trading_date"] = pd.to_datetime(L["trading_date"])
m = L.merge(p[~p["is_st_like"]], on=["symbol", "trading_date"])
m["bb"] = m["boards"].clip(upper=3)
m["theme_n"] = m.groupby(["trading_date", "sector"])["symbol"].transform("size").where(m["sector"].notna() & (m["sector"] != ""), 1)
OUT = ["ret1", "seal_next", "big_loss", "o2o_buy"]

def tt(s):
    s = s.dropna()
    return "   n/a   " if len(s) < 10 else f"{s.mean()*100:+6.2f} t={s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+5.1f}"

def rule(label, mask, cell=("trading_date", "bb")):
    mm = m.assign(grp=mask)
    c = mm.groupby(list(cell) + ["grp"])[OUT].mean().unstack("grp")
    parts = []
    for col in OUT:
        d = (c[(col, True)] - c[(col, False)]).dropna().groupby("trading_date").mean()
        parts.append(f"{col} {tt(d[d.index <= '2025-12-31'])} | {tt(d[d.index > '2025-12-31'])}")
    print(f"{label:44s} n={int(mask.sum()):5d}  " + "  ;  ".join(parts))

print("rule group minus the rest of the same day and board bucket, %, development | hold-out")
nw = ~m["one_word"]
rule("R34 amount >= largest of prior 250 sessions", nw & (m.cny >= m.max250))
rule("R34 amount < half of that largest", nw & (m.cny < 0.5 * m.max250))
rule("R19 shrunk-volume board (vr<1, boards>=2)", nw & (m.vr < 1) & (m.boards >= 2))
rule("R15/R27 heavy-volume board (vr>=3)", nw & (m.vr >= 3))
rule("R06 theme with >=10 limit-ups", m.theme_n >= 10)
rule("R06 theme with 1-3 limit-ups", m.theme_n <= 3)
rule("R49 run-up of 20 sessions >= 40%", m.run20 >= 0.40)
rule("R20 closing seal below 20 million", nw & (m.seal_money < 2e7))
rule("R20 closing seal above 300 million", nw & (m.seal_money >= 3e8))
rule("R21 first seal 09:30-09:45", (m.seal_hm > "09:30:00") & (m.seal_hm <= "09:45:00"))
rule("R21 first seal after 14:30", m.seal_hm >= "14:30:00")
rule("R36 turnover >= 10 billion", m.cny >= 1e10)
rule("R11 second or third board", m.boards.isin([2, 3]) , cell=("trading_date",))
rule("R11 sixth board or higher", m.boards >= 6, cell=("trading_date",))

days = m.groupby("trading_date").agg(n=("symbol", "size"), n3=("boards", lambda s: int((s >= 3).sum())))
multi = m[m.boards >= 2].groupby("trading_date")[OUT].mean()
days = days.join(multi).join(market.rename("market"))
def switch(label, on):
    a, b = days[on], days[~on]
    parts = []
    for col in OUT:
        for lo, hi in (("2000", "2025-12-31"), ("2026-01-01", "2030")):
            x, y = a.loc[lo:hi, col].dropna(), b.loc[lo:hi, col].dropna()
            se = np.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y)) if len(x) > 5 and len(y) > 5 else np.nan
            parts.append(f"{(x.mean()-y.mean())*100:+.2f} t={(x.mean()-y.mean())/se:+.1f} ({len(x)}/{len(y)}d)")
    print(f"{label}\n    " + "  ;  ".join(f"{c}: {parts[2*i]} | {parts[2*i+1]}" for i, c in enumerate(OUT)))
print("\nday switches: next-day outcome of the day's consecutive-board stocks, on-days minus off-days, %")
switch("R13 more than 5 stocks with 3+ boards and more than 20 limit-ups", (days.n3 > 5) & (days.n > 20))
switch("R38 market turnover >= 2.1 trillion", days.market >= 2.1e12)
switch("R38 market turnover < 1.7 trillion", days.market < 1.7e12)
print("market turnover quantiles (trillion):", (market.quantile([0.1, 0.5, 0.9]) / 1e12).round(2).tolist(), "; days >=2.1:", int((market >= 2.1e12).sum()), "; days R13 on:", int(((days.n3 > 5) & (days.n > 20)).sum()), "of", len(days))
