#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data")
CACHE = Path("/tmp/st_chip_distribution_proxy.parquet")
ROOT = Path("docs/archive/2026-10-13-st-chip-distribution-data")
FEATURES = ["profit", "over010", "over1030", "overhead30", "concentration", "avg_cost_dist", "q95_rel"]
OUTCOMES = ["next_seal", "next_streak", "overnight1", "ret1", "ret_2_3", "o2o", "big_loss"]


def day_t(diffs: pd.Series) -> tuple[float, float, int]:
    x = diffs.dropna().to_numpy(float)
    if len(x) < 2:
        return np.nan, np.nan, len(x)
    mean = float(x.mean())
    t = mean / (x.std(ddof=1) / np.sqrt(len(x))) if x.std(ddof=1) > 0 else np.nan
    return mean, float(t), len(x)


def contrast(df: pd.DataFrame, feature: str, outcome: str, high_is_good: bool = True) -> dict:
    d = df[["trading_date", "bucket", feature, outcome]].dropna().copy()
    if d.empty:
        return {"estimate": np.nan, "t": np.nan, "days": 0, "stocks": 0}
    # Median split within each day and return high minus low. For inverse features,
    # caller flips the sign so a positive estimate is the preregistered direction.
    d["high"] = d.groupby(["trading_date", "bucket"], sort=False)[feature].transform(lambda x: x > x.median())
    daily = d.groupby(["trading_date", "bucket"], sort=False).apply(
        lambda x: x.loc[x.high, outcome].mean() - x.loc[~x.high, outcome].mean()
        if x.high.any() and (~x.high).any() else np.nan,
        include_groups=False,
    ).dropna()
    est, t, days = day_t(daily)
    if not high_is_good:
        est, t = -est, -t
    return {"estimate": est, "t": t, "days": days, "stocks": int(len(d))}


def event_table(df: pd.DataFrame, event: str) -> list[dict]:
    rows = []
    for hl in (20, 60):
        use = df[df.event == event].copy()
        for f in FEATURES:
            col = f"hl{hl}_{f}"
            for y in OUTCOMES:
                if y not in use or use[y].notna().sum() == 0:
                    continue
                inv = f in {"over010", "over1030", "overhead30", "concentration", "avg_cost_dist"}
                r = contrast(use, col, y, high_is_good=not inv)
                rows.append({"event": event, "half_life": hl, "feature": f, "outcome": y, **r})
    return rows


def claim_table(df: pd.DataFrame, period: str) -> list[str]:
    lines = [f"CLAIMS period={period}"]
    # Rule 1: low overhead30 should be better; report all four readings in LU and non-seal groups.
    for event, label in [("limit_up", "上方无套牢盘/涨停"), ("up5_nonseal", "上方无套牢盘/大涨未封"), ("breakout", "上方无套牢盘/突破")]:
        sub = df[df.event == event].copy()
        for hl in (20, 60):
            for y in ["next_seal", "next_streak", "overnight1", "ret1", "ret_2_3", "o2o"]:
                r = contrast(sub, f"hl{hl}_overhead30", y, high_is_good=False)
                lines.append(f"overhead event={label} hl={hl} y={y} est={r['estimate']:.8g} t={r['t']:.5g} days={r['days']} stocks={r['stocks']}")
    # Rule 2: being above q95 (outside zone) vs inside; positive is outside minus inside.
    sub = df[df.event == "limit_up"].copy()
    for hl in (20, 60):
        s = sub[["trading_date", "bucket", f"hl{hl}_q95_rel"] + OUTCOMES].dropna(subset=[f"hl{hl}_q95_rel"])
        s["outside"] = s[f"hl{hl}_q95_rel"] < 0
        for y in ["next_seal", "next_streak", "overnight1", "ret1", "ret_2_3", "o2o", "big_loss"]:
            z = s.dropna(subset=[y]).groupby(["trading_date", "bucket"], sort=False).apply(
                lambda x: x.loc[x.outside, y].mean() - x.loc[~x.outside, y].mean()
                if x.outside.any() and (~x.outside).any() else np.nan, include_groups=False).dropna()
            est, t, days = day_t(z)
            lines.append(f"through_zone hl={hl} y={y} est={est:.8g} t={t:.5g} days={days} stocks={len(s)} outside={int(s.outside.sum())}")
    # Rule 3: high profit + high volume ratio as a fixed joint score, direction lower outcomes.
    for event, label in [("limit_up", "获利盘兑现/涨停"), ("up5_nonseal", "获利盘兑现/大涨未封"), ("breakout", "获利盘兑现/突破")]:
        sub = df[df.event == event].copy()
        for hl in (20, 60):
            p = f"hl{hl}_profit"
            sub["joint"] = sub.groupby(["trading_date", "bucket"], sort=False)[p].rank(pct=True) + sub.groupby(["trading_date", "bucket"], sort=False)["vr"].rank(pct=True)
            for y in ["next_seal", "next_streak", "overnight1", "ret1", "ret_2_3", "o2o", "big_loss"]:
                r = contrast(sub.rename(columns={"joint": f"hl{hl}_joint"}), f"hl{hl}_joint", y, high_is_good=False)
                lines.append(f"profit_volume event={label} hl={hl} y={y} est={r['estimate']:.8g} t={r['t']:.5g} days={r['days']} stocks={r['stocks']}")
    # Rule 4: concentration lower is better, especially breakouts.
    for event, label in [("breakout", "集中后突破"), ("limit_up", "集中/涨停")]:
        sub = df[df.event == event].copy()
        for hl in (20, 60):
            for y in ["next_seal", "next_streak", "overnight1", "ret1", "ret_2_3", "o2o", "big_loss"]:
                r = contrast(sub, f"hl{hl}_concentration", y, high_is_good=False)
                lines.append(f"concentration event={label} hl={hl} y={y} est={r['estimate']:.8g} t={r['t']:.5g} days={r['days']} stocks={r['stocks']}")
    return lines


def load() -> pd.DataFrame:
    p = pd.read_parquet(CACHE)
    # Add next-session outcomes and a raw-price 60-session breakout flag. The
    # proxy cache is already sorted by symbol/di; no second panel is retained.
    p = p.sort_values(["symbol", "di"], kind="mergesort").reset_index(drop=True)
    p["next_seal"] = p.groupby("symbol", sort=False)["limit_up_close"].shift(-1).astype(float)
    p["next_streak"] = p.groupby("symbol", sort=False)["streak"].shift(-1)
    p["big_loss"] = (p.ret1 <= -0.05).astype(float)
    # Read only close for breakout and merge then discard source frame.
    close = pd.read_parquet(DATA / "daily_panel.parquet", columns=["symbol", "di", "close", "high", "low"])
    p = p.merge(close, on=["symbol", "di"], how="left", sort=False)
    close = None
    p["prior60"] = p.groupby("symbol", sort=False)["close"].transform(lambda x: x.shift(1).rolling(60, min_periods=60).max())
    p["breakout"] = p.close > p.prior60
    p["not_one_word"] = p.high > p.low
    p["event"] = np.select([
        p.limit_up_close.fillna(False),
        (p.bucket == "4_up5_plus") & ~p.limit_up_close.fillna(False),
        p.breakout.fillna(False),
    ], ["limit_up", "up5_nonseal", "breakout"], default="other")
    return p


def turnover_validation() -> list[str]:
    p = pd.read_parquet(CACHE, columns=["symbol", "trading_date", "hl20_profit", "hl60_profit", "hl20_overhead30", "hl60_overhead30"])
    panel = pd.read_parquet(DATA / "daily_panel.parquet", columns=["symbol", "trading_date", "amount", "volume", "close"])
    panel["amount_fix"] = panel.amount.where(panel.amount / (panel.volume * panel.close / 10) <= 50, panel.amount / 1000)
    panel["turnover_calc"] = panel.amount_fix * 1000 / (panel.close * 0 + 1)
    lu = pd.read_parquet(DATA / "limit_up_lists.parquet", columns=["symbol", "trading_date", "float_cap", "turnover_rate"])
    x = p.merge(panel[["symbol", "trading_date", "amount_fix", "close"]], on=["symbol", "trading_date"]).merge(lu, on=["symbol", "trading_date"], how="inner")
    x["turnover_calc_pct"] = x.amount_fix * 1000 / x.float_cap * 100
    lines = [f"TURNOVER_VALIDATION rows={len(x)} float_cap_positive={(x.float_cap>0).sum()}"]
    for hl in (20, 60):
        for feat in ("profit", "overhead30"):
            a = x[f"hl{hl}_{feat}"]
            b = x.turnover_calc_pct
            ok = a.notna() & b.notna() & (x.float_cap > 0)
            lines.append(f"hl={hl} feature={feat} n={int(ok.sum())} spearman={a[ok].corr(b[ok],method='spearman'):.6g} pearson={a[ok].corr(b[ok]):.6g} mean_abs_diff={np.abs(a[ok]-b[ok]).mean():.6g} proxy_median={a[ok].median():.6g} turnover_median={b[ok].median():.6g}")
    return lines


def stratified_limit_up(df: pd.DataFrame) -> list[str]:
    """Requested robustness cells for the three limit-up claims."""
    sub = df[df.event == "limit_up"].copy()
    lines = ["STRATIFIED_LIMIT_UP definition=high>low or streak buckets; estimates are preregistered direction"]
    for hl in (20, 60):
        qcol = f"hl{hl}_q95_rel"
        sub["outside"] = sub[qcol] < 0
        sub["joint"] = sub.groupby(["trading_date", "bucket"], sort=False)[f"hl{hl}_profit"].rank(pct=True) + sub.groupby(["trading_date", "bucket"], sort=False)["vr"].rank(pct=True)
        for name, mask in [("not_one_word", sub.not_one_word), ("streak1", sub.streak == 1), ("streak2", sub.streak == 2), ("streak3plus", sub.streak >= 3)]:
            x = sub[mask].copy()
            for rule, col, inv, special in [("through_zone", "outside", False, True), ("overhead", f"hl{hl}_overhead30", True, False), ("profit_volume", "joint", True, False)]:
                for y in ["next_seal", "overnight1", "ret1", "ret_2_3", "o2o", "big_loss"]:
                    z = x[["trading_date", "bucket", col, y]].dropna()
                    if z.empty: continue
                    if special:
                        z["hi"] = z[col]
                    else:
                        z["hi"] = z[col] > z.groupby(["trading_date", "bucket"])[col].transform("median")
                    daily = z.groupby(["trading_date", "bucket"], sort=False).apply(lambda g: g.loc[g.hi, y].mean() - g.loc[~g.hi, y].mean() if g.hi.any() and (~g.hi).any() else np.nan, include_groups=False).dropna()
                    est, t, days = day_t(daily)
                    if inv: est, t = -est, -t
                    lines.append(f"STRATIFIED hl={hl} cell={name} rule={rule} y={y} est={est:.8g} t={t:.5g} days={days} stocks={len(z)}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--period", choices=["dev", "holdout"], required=True); ap.add_argument("--holdout-once", action="store_true"); args = ap.parse_args()
    p = load()
    if args.period == "dev":
        p = p[p.trading_date <= pd.Timestamp("2025-12-31")].copy()
    else:
        p = p[(p.trading_date >= pd.Timestamp("2026-01-01")) & (p.trading_date <= pd.Timestamp("2026-09-18"))].copy()
    rows = []
    for event in ("limit_up", "up5_nonseal", "breakout"):
        rows.extend(event_table(p, event))
    out = ROOT / f"{args.period}_results.txt"
    lines = [f"PERIOD={args.period} rows={len(p)} days={p.trading_date.nunique()} holdout_reads={1 if args.period=='holdout' else 0}"]
    lines.append("TABLE event half_life feature outcome estimate t days stocks")
    lines.extend("TABLE {event} {half_life} {feature} {outcome} {estimate:.8g} {t:.5g} {days} {stocks}".format(**r) for r in rows)
    lines.extend(claim_table(p, args.period))
    lines.extend(stratified_limit_up(p))
    if args.period == "dev":
        lines.extend(turnover_validation())
    out.write_text("\n".join(lines) + "\n")
    print(out.read_text(), end="")


if __name__ == "__main__":
    main()
