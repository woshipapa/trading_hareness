"""Pre-registered breakout/volume study.

Usage:
  python analyze.py dev
  python analyze.py holdout

The hold-out command reads the panel once after the development thresholds are
frozen in dev_thresholds.json.  It writes a small text result beside this file.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data")
OUT = Path(__file__).resolve().parent
PERIODS = {
    "dev": ("2023-08-15", "2025-12-31"),
    "holdout": ("2026-01-01", "2026-09-18"),
}
COLS = [
    "symbol", "trading_date", "di", "open", "high", "low", "close", "pre_close",
    "volume", "amount", "limit_up", "limit_pct", "is_st_like", "vr",
    "limit_up_close", "open_at_limit_up", "next_open_at_limit_up", "next_limit_down_close",
    "overnight1", "ret1", "o2o", "ret_2_3", "gap", "streak",
]
Y = ["overnight1", "ret1", "ret_2_3", "o2o"]
N_LIST = (20, 60, 120)


def finite(x) -> bool:
    return pd.notna(x) and np.isfinite(float(x))


def fmt_pct(x: float) -> str:
    return "NA" if not finite(x) else f"{100 * float(x):+.3f}%"


def fmt_t(x: float) -> str:
    return "NA" if not finite(x) else f"{float(x):+.2f}"


def mean_t(values: pd.Series) -> tuple[float, float, int]:
    x = pd.to_numeric(values, errors="coerce").dropna().to_numpy(float)
    if not len(x):
        return math.nan, math.nan, 0
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 1 and x.std(ddof=1) else math.nan, len(x)


def daily_arm(df: pd.DataFrame, mask: pd.Series, label: str) -> dict:
    f = df.loc[mask & df[Y].notna().all(axis=1), ["trading_date", *Y]].copy()
    if f.empty:
        return {"label": label, "stock_days": 0, "days": 0, **{c: (math.nan, math.nan) for c in Y}}
    d = f.groupby("trading_date", sort=True)[Y].mean()
    out = {"label": label, "stock_days": int(len(f)), "days": int(len(d))}
    for c in Y:
        out[c] = mean_t(d[c])[:2]
    return out


def daily_diff(df: pd.DataFrame, a: pd.Series, b: pd.Series) -> dict:
    aa = df.loc[a & df[Y].notna().all(axis=1), ["trading_date", *Y]].groupby("trading_date")[Y].mean()
    bb = df.loc[b & df[Y].notna().all(axis=1), ["trading_date", *Y]].groupby("trading_date")[Y].mean()
    z = (bb - aa).dropna(how="any")
    out = {"days": int(len(z))}
    for c in Y:
        out[c] = mean_t(z[c])[:2]
    return out


def print_arm(out: dict) -> None:
    vals = " ".join(f"{c}={fmt_pct(out[c][0])},t={fmt_t(out[c][1])}" for c in Y)
    print(f"ARM {out['label']} stock_days={out['stock_days']} days={out['days']} {vals}")


def print_diff(label: str, d: dict) -> None:
    vals = " ".join(f"{c}={fmt_pct(d[c][0])},t={fmt_t(d[c][1])}" for c in Y)
    print(f"DIFF {label} days={d['days']} {vals}")


def add_adjusted_prices(p: pd.DataFrame) -> pd.DataFrame:
    p = p.sort_values(["symbol", "di"], kind="mergesort").reset_index(drop=True)
    g = p.groupby("symbol", sort=False)
    prev_close = g["close"].shift(1)
    factor = p["pre_close"] / prev_close
    factor = factor.where(factor.between(0.05, 20.0))
    # A row's multiplier contains only factors strictly after that row.  This
    # puts all historical prices on the latest raw-price scale.
    future = factor.iloc[::-1].groupby(p["symbol"].iloc[::-1], sort=False).cumprod().iloc[::-1]
    # cumprod above includes the current factor; shift it back one row within symbol.
    p["adj_mult"] = future.groupby(p["symbol"], sort=False).shift(-1).fillna(1.0)
    for c in ("open", "high", "low", "close", "pre_close", "limit_up"):
        p["adj_" + c] = p[c] * p["adj_mult"]
    p["ex_factor"] = factor
    return p


def add_features(p: pd.DataFrame, thresholds: dict | None, period: str) -> tuple[pd.DataFrame, dict]:
    p = add_adjusted_prices(p)
    g = p.groupby("symbol", sort=False, group_keys=False)
    for n in N_LIST:
        p[f"prior_{n}"] = g["adj_high"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())
        p[f"prior_raw_{n}"] = g["high"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())
        p[f"below5_{n}"] = True
        for k in range(1, 6):
            p[f"below5_{n}"] &= g["adj_close"].transform(lambda s, k=k: s.shift(k)) <= g[f"prior_{n}"].transform(lambda s, k=k: s.shift(k))
        p[f"break_{n}"] = (p["adj_close"] > p[f"prior_{n}"]) & p[f"below5_{n}"]
        p[f"break_raw_{n}"] = (p["close"] > p[f"prior_raw_{n}"]) & p[f"below5_{n}"]
        p[f"touch_{n}"] = (p["adj_high"] >= 0.99 * p[f"prior_{n}"]) & (p["adj_close"] < p[f"prior_{n}"])
        p[f"width_{n}"] = g["adj_high"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())
        p[f"width_{n}"] = (p[f"width_{n}"] - g["adj_low"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).min())) / g["adj_low"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).min())
        p[f"range_{n}"] = (p["adj_high"] - p["adj_low"]) / p["adj_pre_close"]
        p[f"upper_{n}"] = (p["adj_high"] - p[["adj_open", "adj_close"]].max(axis=1)) / p["adj_pre_close"]
    p["vr_bin"] = pd.cut(p["vr"], [-np.inf, 1, 2, 4, np.inf], labels=["vr<1", "vr1-2", "vr2-4", "vr>=4"], right=False)
    p["range_dev"] = math.nan
    p["upper_dev"] = math.nan
    p["shrink5"] = (
        g["volume"].transform(lambda s: s.shift(1).rolling(3, min_periods=3).mean())
        >= 1.20 * g["volume"].transform(lambda s: s.shift(1).rolling(2, min_periods=2).mean())
    )
    # Ensure the two rolling means use disjoint T-5:T-3 and T-2:T-1 windows.
    p["shrink5"] = g["volume"].transform(lambda s: s.shift(1).rolling(3, min_periods=3).mean())
    p["shrink5"] = g["volume"].transform(lambda s: s.shift(3).rolling(3, min_periods=3).mean()) >= 1.20 * g["volume"].transform(lambda s: s.shift(1).rolling(2, min_periods=2).mean())

    if period == "dev":
        thresholds = {"platform": {}, "range": {}, "upper": {}}
        for n in N_LIST:
            ev = p.loc[p[f"break_{n}"] & ~p["is_st_like"]]
            thresholds["platform"][str(n)] = float(ev[f"width_{n}"].quantile(0.20))
            thresholds["range"][str(n)] = [float(ev[f"range_{n}"].quantile(1 / 3)), float(ev[f"range_{n}"].quantile(2 / 3))]
            thresholds["upper"][str(n)] = [float(ev[f"upper_{n}"].quantile(1 / 3)), float(ev[f"upper_{n}"].quantile(2 / 3))]
    for n in N_LIST:
        q = thresholds["range"][str(n)]
        p[f"range_bin_{n}"] = pd.cut(p[f"range_{n}"], [-np.inf, q[0], q[1], np.inf], labels=["low", "mid", "high"], right=False)
        q = thresholds["upper"][str(n)]
        p[f"upper_bin_{n}"] = pd.cut(p[f"upper_{n}"], [-np.inf, q[0], q[1], np.inf], labels=["low", "mid", "high"], right=False)
        p[f"platform_{n}"] = p[f"width_{n}"] <= thresholds["platform"][str(n)]
        # The within-day thirds are defined on each event family at reporting time.
        p[f"vr_third_{n}"] = math.nan
        evmask = p[f"break_{n}"] | p[f"touch_{n}"]
        p.loc[evmask, f"vr_third_{n}"] = p.loc[evmask].groupby("trading_date")["vr"].rank(pct=True, method="first").map(lambda x: 0 if x <= 1 / 3 else (1 if x <= 2 / 3 else 2))
    return p, thresholds


def add_next_day(p: pd.DataFrame) -> pd.DataFrame:
    g = p.groupby("symbol", sort=False)
    p["next_di"] = g["di"].shift(-1)
    p["next_gap"] = g["gap"].shift(-1).where(p["next_di"] == p["di"] + 1)
    p["next_lu_close"] = g["limit_up_close"].shift(-1).where(p["next_di"] == p["di"] + 1)
    return p


def summarize_breakouts(p: pd.DataFrame, period: str) -> None:
    lo, hi = PERIODS[period]
    p = p[(p.trading_date >= lo) & (p.trading_date <= hi) & ~p.is_st_like].copy()
    print(f"PERIOD {period} start={lo} end={hi} rows={len(p):,} days={p.trading_date.nunique()}")
    usable = p[Y].notna().all(axis=1)
    print(f"TRADABILITY outcomes_with_all={int((usable).sum()):,} o2o_excluded_next_open_limit={int((p.next_open_at_limit_up == True).sum()):,} next_down_limit={int((p.next_limit_down_close == True).sum()):,}")
    for n in N_LIST:
        event = p[f"break_{n}"] & usable
        print(f"\n=== H1 N={n} breakout ===")
        print(f"EVENT stock_days={int(event.sum())} days={p.loc[event, 'trading_date'].nunique()} adj_vs_raw_changed={int((p[f'break_{n}'] != p[f'break_raw_{n}']).loc[p[f'break_{n}'].notna() & p[f'break_raw_{n}'].notna()].sum())}")
        for col in ("vr_bin", f"vr_third_{n}", "limit_up_close", f"range_bin_{n}", f"upper_bin_{n}"):
            print(f"GROUP {col}")
            for val in sorted(p.loc[event, col].dropna().unique(), key=str):
                print_arm(daily_arm(p, event & (p[col] == val), str(val)))
            vals = sorted(p.loc[event, col].dropna().unique(), key=str)
            if len(vals) >= 2:
                print_diff(f"{col} high-low", daily_diff(p, event & (p[col] == vals[0]), event & (p[col] == vals[-1])))
        print("GROUP limit_up_close high-minus-low")
        print_diff("limit_up_close false->true", daily_diff(p, event & ~p.limit_up_close, event & p.limit_up_close))
        print("=== H2 platform ===")
        print_arm(daily_arm(p, event & p[f"platform_{n}"], "narrow"))
        print_arm(daily_arm(p, event & ~p[f"platform_{n}"], "not_narrow"))
        print_diff("narrow-vs-not_narrow", daily_diff(p, event & ~p[f"platform_{n}"], event & p[f"platform_{n}"]))
        print("=== H5 shrink5 inside vr>=2 ===")
        highvol = event & (p.vr >= 2)
        print_arm(daily_arm(p, highvol & p.shrink5, "shrink5"))
        print_arm(daily_arm(p, highvol & ~p.shrink5, "no_shrink5"))
        print_diff("shrink5-vs-no_shrink5", daily_diff(p, highvol & ~p.shrink5, highvol & p.shrink5))
        print("=== H3 failure at level ===")
        fail = p[f"touch_{n}"] & usable
        print(f"FAIL stock_days={int(fail.sum())} days={p.loc[fail, 'trading_date'].nunique()}")
        for col in ("vr_bin", f"vr_third_{n}", f"upper_bin_{n}"):
            vals = sorted(p.loc[fail, col].dropna().unique(), key=str)
            for val in vals:
                print_arm(daily_arm(p, fail & (p[col] == val), str(val)))
            if len(vals) >= 2:
                print_diff(f"{col} high-low", daily_diff(p, fail & (p[col] == vals[0]), fail & (p[col] == vals[-1])))

    print("\n=== H4 first boards and 60-day level ===")
    n = 60
    first = p[(p.streak == 1) & p.limit_up_close & usable & p[f"prior_{n}"].notna()].copy()
    first["A_above"] = first.adj_close > first[f"prior_{n}"]
    first["B_below"] = first.adj_close < first[f"prior_{n}"]
    first["C_just_under"] = (first.adj_limit_up < first[f"prior_{n}"]) & ((first[f"prior_{n}"] - first.adj_limit_up) <= 0.01 * first[f"prior_{n}"])
    for name, mask in (("A_above", first.A_above), ("B_below", first.B_below), ("C_just_under", first.C_just_under)):
        print_arm(daily_arm(first, mask, name))
        q = first.loc[mask, "next_lu_close"]
        print(f"SEAL_AGAIN {name} stock_days={int(q.notna().sum())} days={first.loc[mask & q.notna(), 'trading_date'].nunique()} rate={fmt_pct(q.mean())}")
    print_diff("A_above-B_below", daily_diff(first, first.B_below, first.A_above))
    # Gap buckets are next-day information and are shown as a baseline stratification only.
    first["gap_bucket"] = pd.cut(first.next_gap, [-np.inf, -0.02, 0, 0.02, 0.05, 0.09, np.inf], labels=["<-2%", "-2%-0", "0-2%", "2-5%", "5-9%", ">=9%"], right=False)
    for name, mask in (("all_first", pd.Series(True, index=first.index)), ("A_above", first.A_above), ("B_below", first.B_below), ("C_just_under", first.C_just_under)):
        for gb in first.loc[mask, "gap_bucket"].dropna().unique():
            z = first.loc[mask & (first.gap_bucket == gb), "next_lu_close"]
            print(f"GAP_BASELINE {name} bucket={gb} stock_days={int(z.notna().sum())} rate={fmt_pct(z.mean())}")


def main() -> None:
    period = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if period not in PERIODS:
        raise SystemExit("use dev or holdout")
    print(f"COMMAND period={period} panel={DATA/'daily_panel.parquet'}")
    p = pd.read_parquet(DATA / "daily_panel.parquet", columns=COLS)
    p["trading_date"] = pd.to_datetime(p["trading_date"]).dt.date.astype(str)
    p, thresholds = add_features(p, None if period == "dev" else json.loads((OUT / "dev_thresholds.json").read_text()), period)
    p = add_next_day(p)
    if period == "dev":
        (OUT / "dev_thresholds.json").write_text(json.dumps(thresholds, indent=2, sort_keys=True) + "\n")
    summarize_breakouts(p, period)


if __name__ == "__main__":
    main()
