"""Independent spot check: theme leader by earliest seal; auction-amount rank inside yesterday's limit-up pool."""
import gzip, json
import numpy as np, pandas as pd
D = "/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/"
p = pd.read_parquet(D + "daily_panel.parquet", columns=["symbol", "trading_date", "di", "ret", "gap", "intraday", "is_st_like",
    "limit_up_close", "open_at_limit_up", "streak", "overnight1", "ret1", "o2o", "next_open_at_limit_up"])
p["trading_date"] = pd.to_datetime(p["trading_date"])
p = p.sort_values(["symbol", "di"]).reset_index(drop=True)
g = p.groupby("symbol", sort=False)
nxt = g["di"].shift(-1) == p["di"] + 1
p["seal_next"] = g["limit_up_close"].shift(-1).where(nxt).astype(float)
p["gap_next"] = g["gap"].shift(-1).where(nxt)
p["o2c_next"] = g["intraday"].shift(-1).where(nxt)
p["date_next"] = g["trading_date"].shift(-1).where(nxt)
p["o2o_buy"] = p["o2o"].where(p["next_open_at_limit_up"] != 1)
p["o2c_buy"] = p["o2c_next"].where(p["next_open_at_limit_up"] != 1)

def tstat(s):
    s = s.dropna()
    return f"{s.mean()*100:+.2f} t={s.mean()/(s.std(ddof=1)/np.sqrt(len(s))):+.1f} d={len(s)}"

def both(frame, col):
    return " | ".join(tstat(frame[frame.index <= "2025-12-31"][col]) + "" if dev else tstat(frame[frame.index > "2025-12-31"][col]) for dev in (True, False))

L = pd.read_parquet(D + "limit_up_lists.parquet", columns=["symbol", "trading_date", "seal_hm", "sector", "boards", "seal_money", "seal_max"])
L["trading_date"] = pd.to_datetime(L["trading_date"])
m = L.merge(p[["symbol", "trading_date", "overnight1", "ret1", "seal_next", "o2o_buy"]], on=["symbol", "trading_date"])
m = m[m["sector"].notna() & (m["sector"] != "")]
m["n_theme"] = m.groupby(["trading_date", "sector"])["symbol"].transform("size")
m = m[m["n_theme"] >= 2].sort_values(["trading_date", "sector", "seal_hm", "boards"], ascending=[True, True, True, False])
m["leader"] = m.groupby(["trading_date", "sector"]).cumcount() == 0
print("[A] theme leader = earliest seal, leader minus followers, day means, % (dev | hold-out); stock-days", len(m))
for label, sub in [("all boards", m), ("first boards only", m[m.boards == 1])]:
    lead = sub[sub.leader].groupby("trading_date")[["overnight1", "ret1", "seal_next", "o2o_buy"]].mean()
    foll = sub[~sub.leader].groupby("trading_date")[["overnight1", "ret1", "seal_next", "o2o_buy"]].mean()
    diff = (lead - foll)
    for col in ("overnight1", "ret1", "seal_next", "o2o_buy"):
        print(f"  {label:18s} {col:10s} {both(diff, col)}")

rows = []
with gzip.open(D + "morning_auction_lists.jsonl.gz", "rt") as fh:
    for line in fh:
        day = json.loads(line)
        for rank, row in enumerate(day["rows"], 1):
            code = str(row[0])
            rows.append((pd.Timestamp(day["day"]), code + (".SH" if code[0] in "69" else ".SZ"), rank, float(row[4] or 0)))
A = pd.DataFrame(rows, columns=["date_next", "symbol", "list_rank", "auction_amount"])
y = p[p["limit_up_close"] & ~p["is_st_like"] & p["date_next"].notna()].merge(A, on=["symbol", "date_next"], how="left")
y = y[y["date_next"].isin(set(A["date_next"]))]
y["pool_rank"] = y.groupby("date_next")["auction_amount"].rank(ascending=False, method="first")
y["boards_b"] = y["streak"].clip(upper=3)
y["gap_b"] = np.select([y["next_open_at_limit_up"] == 1, y["gap_next"] < -0.02, y["gap_next"] < 0.02, y["gap_next"] < 0.05, y["gap_next"] < 0.09], [5, 0, 1, 2, 3], 4)
y["hold_close"] = y["ret1"]
print("\n[B] yesterday's limit-up pool, rule group minus the rest inside day x gap bucket x board bucket, % (dev | hold-out); pool stock-days", len(y), "on list", int(y.list_rank.notna().sum()))
for label, mask in [("pool top 5 by auction amount", y.pool_rank <= 5), ("pool top 10 by auction amount", y.pool_rank <= 10), ("vendor list rank <= 10", y.list_rank <= 10)]:
    y["grp"] = mask
    cell = y.groupby(["date_next", "gap_b", "boards_b", "grp"])[["seal_next", "hold_close", "o2c_buy", "o2o_buy"]].mean().unstack("grp")
    out = []
    for col in ("seal_next", "hold_close", "o2c_buy", "o2o_buy"):
        d = (cell[(col, True)] - cell[(col, False)]).dropna().groupby("date_next").mean()
        out.append(f"{col} {both(d.to_frame(col), col)}")
    print(f"  {label:30s} n={int(mask.sum())}\n     " + "\n     ".join(out))
t5 = y[(y.pool_rank <= 5)].groupby("date_next")[["seal_next", "hold_close", "o2c_buy", "o2o_buy"]].mean()
print("  pool top 5, absolute day means:", " ; ".join(f"{c} {both(t5, c)}" for c in t5.columns))
