"""First historical look at ladder roles of consecutive-board play: position grab, assist, back row, catch-up, switches."""
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["symbol", "trading_date", "di", "high", "low", "vr", "is_st_like",
    "limit_up_close", "ret1", "o2o", "next_open_at_limit_up"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
p = p[p["trading_date"] <= "2026-09-18"].sort_values(["symbol", "di"]).reset_index(drop=True)
g = p.groupby("symbol", sort=False)
nxt = g["di"].shift(-1) == p["di"] + 1
p["seal_next"] = g["limit_up_close"].shift(-1).where(nxt).astype(float)
p["o2o_buy"] = p["o2o"].where(p["next_open_at_limit_up"] != 1)
p["big_loss"] = (p["ret1"] <= -0.05).astype(float).where(p["ret1"].notna())
p["one_word"] = p["high"] == p["low"]
L = pd.read_parquet(D + "limit_up_lists.parquet", columns=["symbol", "trading_date", "seal_hm", "sector", "boards", "seal_money", "f16"])
L["trading_date"] = pd.to_datetime(L["trading_date"])
m = L.merge(p[~p["is_st_like"]], on=["symbol", "trading_date"])
m = m[m["sector"].notna() & (m["sector"] != "")].copy()
m["sec"] = pd.to_timedelta(m["seal_hm"]).dt.total_seconds()
m["bb"] = m["boards"].clip(upper=3)
key = ["trading_date", "sector"]
m = m.sort_values(key + ["boards", "sec"], ascending=[True, True, False, True])
m["theme_n"] = m.groupby(key)["symbol"].transform("size")
m["lead_boards"] = m.groupby(key)["boards"].transform("max")
m["is_lead"] = m.groupby(key).cumcount() == 0
lead = m[m.is_lead][["di", "sector", "symbol", "boards", "sec"]].rename(columns={"symbol": "lead_sym", "boards": "lb", "sec": "lead_sec"})
m = m.merge(lead[["di", "sector", "lead_sec"]], on=["di", "sector"])
prev_lead = lead.assign(di=lead.di + 1).rename(columns={"lead_sym": "pl_sym", "lb": "pl_boards", "lead_sec": "pl_sec_y"})[["di", "sector", "pl_sym", "pl_boards"]]
m = m.merge(prev_lead, on=["di", "sector"], how="left")
today_sec = m[["di", "symbol", "sec"]].rename(columns={"symbol": "pl_sym", "sec": "pl_sec_today"})
m = m.merge(today_sec, on=["di", "pl_sym"], how="left")
y = m[["di", "symbol", "sec", "f16", "seal_money", "vr", "one_word"]].copy(); y["di"] += 1
m = m.merge(y.rename(columns={c: "y_" + c for c in ("sec", "f16", "seal_money", "vr", "one_word")}), on=["di", "symbol"], how="left")
allL = L.merge(p[["symbol", "trading_date", "di"]], on=["symbol", "trading_date"])[["symbol", "di", "boards"]]
back = {}
for lag in (2, 3):
    b = allL[allL.boards >= 2].assign(di=lambda x: x.di + lag)[["symbol", "di"]].drop_duplicates(); b[f"streak_lag{lag}"] = True
    m = m.merge(b, on=["symbol", "di"], how="left")
m["tier_n"] = m.groupby(["trading_date", "boards"])["symbol"].transform("size")
m["tier_first"] = m.groupby(["trading_date", "boards"])["sec"].rank(method="first") == 1
tiers = m.groupby(key)["bb"].agg(lambda s: set(s))
m["ladder_full"] = m.set_index(key).index.map(lambda k: tiers[k] == {1, 2, 3})
OUT = ["ret1", "seal_next", "big_loss", "o2o_buy"]

def tt(s):
    s = s.dropna()
    return "   n/a   " if len(s) < 10 else f"{s.mean()*100:+6.2f} t={s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+5.1f}"

def rule(label, mask, cell=("trading_date", "bb")):
    mm = m.assign(grp=mask.fillna(False).astype(bool))
    c = mm.groupby(list(cell) + ["grp"])[OUT].mean().unstack("grp")
    parts = []
    for col in OUT:
        d = (c[(col, True)] - c[(col, False)]).dropna().groupby("trading_date").mean()
        parts.append(f"{col} {tt(d[d.index <= '2025-12-31'])} | {tt(d[d.index > '2025-12-31'])}")
    print(f"{label:52s} n={int(mm.grp.sum()):5d}  " + "  ;  ".join(parts))

print("rule group minus the rest of the same day and board bucket, %, development | hold-out")
has_lead = (m.theme_n >= 2) & (m.lead_boards >= 2)
pl = m.pl_sym.notna() & (m.pl_boards >= 2) & (m.symbol != m.pl_sym)
rule("卡位: seals before yesterday's theme leader (or it failed)", pl & (m.pl_sec_today.isna() | (m.sec < m.pl_sec_today)))
rule("卡位, leader did seal today but later", pl & (m.sec < m.pl_sec_today))
rule("龙头切换: yesterday's leader failed, today's top board", pl & m.pl_sec_today.isna() & m.is_lead & (m.theme_n >= 2))
rule("yesterday's theme leader sealed again", m.pl_sym.notna() & (m.symbol == m.pl_sym) & (m.pl_boards >= 2))
rule("助攻: first board within 30 min after the leader", has_lead & (m.boards == 1) & ~m.is_lead & (m.sec >= m.lead_sec) & (m.sec <= m.lead_sec + 1800))
rule("后排: fewer boards and later than the leader", has_lead & ~m.is_lead & (m.boards < m.lead_boards) & (m.sec > m.lead_sec))
rule("前排: fewer boards but earlier than the leader", has_lead & ~m.is_lead & (m.boards < m.lead_boards) & (m.sec < m.lead_sec))
rule("补涨: first board, theme leader has 3+ boards", (m.boards == 1) & (m.lead_boards >= 3))
rule("同梯队最早封板 (tier of 3+ stocks, boards>=2)", m.tier_first & (m.tier_n >= 3) & (m.boards >= 2), cell=("trading_date", "boards"))
rule("独苗高标: alone at its height, boards>=3", (m.boards >= 3) & (m.tier_n == 1))
rule("题材梯队完整: theme has 1, 2 and 3+ boards", m.ladder_full)
weak_y = (m.y_sec >= 13 * 3600) | (m.y_f16 == 1) | (m.y_seal_money < 2e7)
rule("弱转强: weak board yesterday, sealed by 09:45 today", m.y_sec.notna() & weak_y & (m.sec <= 9 * 3600 + 45 * 60))
rule("分歧转一致: reopened/heavy yesterday, one-word today", m.y_sec.notna() & ((m.y_f16 == 1) | (m.y_vr >= 2)) & m.one_word)
rule("一致转分歧: one-word yesterday, traded board today", (m.y_one_word == True) & ~m.one_word)
rule("反包: new first board 1-2 sessions after a streak broke", (m.boards == 1) & (m.streak_lag2.fillna(False) | m.streak_lag3.fillna(False)))
rule("三进四 (boards == 3) against the day's other boards", m.boards == 3, cell=("trading_date",))
