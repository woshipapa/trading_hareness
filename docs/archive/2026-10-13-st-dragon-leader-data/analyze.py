#!/Users/papa/.venvs/svc/bin/python
"""Pre-registered st-dragon-leader analysis.

Run once with --period development, then once with --period holdout after the
development script and rules have been committed. Outputs are deliberately
small text files so every reported number has a committed source.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

DATA = Path("/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data")
OUT = Path(__file__).resolve().parent
PANEL_COLS = [
    "symbol", "trading_date", "di", "gap", "overnight1", "ret1", "o2o",
    "ret_f2", "next_open_at_limit_up", "next_limit_down_close", "limit_up_close",
    "broken_board", "is_st_like",
]
LIST_COLS = [
    "symbol", "trading_date", "seal_hm", "sector", "seal_money", "seal_max", "boards",
]


def t0(values: pd.Series) -> tuple[float, float, int]:
    x = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(x) < 2:
        return (float(np.nanmean(x)) if len(x) else np.nan, np.nan, len(x))
    m = float(x.mean())
    s = float(x.std(ddof=1))
    return m, (m / (s / np.sqrt(len(x))) if s > 0 else np.nan), len(x)


def group_stats(df: pd.DataFrame, group: str, outcome: str) -> dict:
    z = df.dropna(subset=[group, outcome]).copy()
    daily = z.groupby(["trading_date", group], observed=True)[outcome].mean().reset_index()
    rows = []
    for g, q in daily.groupby(group, observed=True):
        m, t, nd = t0(q[outcome])
        rows.append({"group": str(g), "estimate": m, "t": t, "days": nd,
                     "stock_days": int(z.loc[z[group] == g, outcome].notna().sum())})
    return rows


def contrast(df: pd.DataFrame, arm: str, a, b, outcome: str) -> dict:
    z = df[df[arm].isin([a, b])].dropna(subset=[outcome]).copy()
    daily = z.groupby(["trading_date", arm], observed=True)[outcome].mean().unstack(arm)
    if a not in daily or b not in daily:
        return {"a": str(a), "b": str(b), "estimate": np.nan, "t": np.nan, "days": 0,
                "a_stock_days": 0, "b_stock_days": 0}
    d = (daily[a] - daily[b]).dropna()
    m, t, nd = t0(d)
    return {"a": str(a), "b": str(b), "estimate": m, "t": t, "days": nd,
            "a_stock_days": int((z[arm] == a).sum()), "b_stock_days": int((z[arm] == b).sum())}


def independent_day_contrast(df: pd.DataFrame, arm: str, a, b, outcome: str) -> dict:
    """Welch comparison of daily means when the market state is day-level."""
    z = df[df[arm].isin([a, b])].dropna(subset=[outcome]).copy()
    daily = z.groupby(["trading_date", arm], observed=True)[outcome].mean().reset_index()
    xa = daily.loc[daily[arm] == a, outcome].to_numpy(dtype=float)
    xb = daily.loc[daily[arm] == b, outcome].to_numpy(dtype=float)
    if len(xa) < 2 or len(xb) < 2:
        t = np.nan
    else:
        t = float(stats.ttest_ind(xa, xb, equal_var=False, nan_policy="omit").statistic)
    return {"a": str(a), "b": str(b), "estimate": float(np.mean(xa) - np.mean(xb)) if len(xa) and len(xb) else np.nan,
            "t": t, "a_days": len(xa), "b_days": len(xb),
            "a_stock_days": int((z[arm] == a).sum()), "b_stock_days": int((z[arm] == b).sum())}


def gap_bucket(x: pd.Series) -> pd.Series:
    return pd.cut(x, [-np.inf, -0.02, 0.02, 0.05, 0.09, np.inf],
                  labels=["below_-2", "-2_to_2", "2_to_5", "5_to_9", "9_plus"],
                  right=False)


def load(period: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    p = pd.read_parquet(DATA / "daily_panel.parquet", columns=PANEL_COLS)
    p["trading_date"] = p["trading_date"].astype(str)
    p["is_st_like"] = p["is_st_like"].fillna(False).astype(bool)
    p = p.sort_values(["symbol", "di"])
    # T+1 fields are aligned by di, never by row order.
    nxt = p[["symbol", "di", "gap", "limit_up_close"]].copy()
    nxt["di"] = nxt["di"] - 1
    nxt = nxt.rename(columns={"gap": "next_gap", "limit_up_close": "seal_next"})
    p = p.merge(nxt, on=["symbol", "di"], how="left")
    p["next_gap_bucket"] = gap_bucket(p["next_gap"])
    lu = pd.read_parquet(DATA / "limit_up_lists.parquet", columns=LIST_COLS)
    lu["trading_date"] = lu["trading_date"].astype(str)
    lu["boards"] = pd.to_numeric(lu["boards"], errors="coerce")
    lu["seal_ratio"] = pd.to_numeric(lu["seal_money"], errors="coerce") / pd.to_numeric(lu["seal_max"], errors="coerce")
    d = lu.merge(p, on=["symbol", "trading_date"], how="inner", suffixes=("", "_panel"))
    d = d[(~d["is_st_like"]) & (d["boards"] >= 1)].copy()
    d["trading_date"] = pd.to_datetime(d["trading_date"]).dt.strftime("%Y-%m-%d")
    if period == "development":
        d = d[(d.trading_date >= "2023-09-01") & (d.trading_date <= "2025-12-31")]
    else:
        d = d[(d.trading_date >= "2026-01-01") & (d.trading_date <= "2026-09-18")]
    cyc = pd.DataFrame(json.loads((DATA / "cycle_series.json").read_text()))
    cyc["date"] = cyc["date"].astype(str)
    cyc["trading_date"] = cyc["date"]
    cyc = cyc.drop(columns=["date"])
    c = cyc[(cyc.trading_date >= ("2023-09-01" if period == "development" else "2026-01-01")) &
            (cyc.trading_date <= ("2025-12-31" if period == "development" else "2026-09-18"))].copy()
    return d, p, c


def add_leaders(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["sector"] = d["sector"].fillna("").astype(str)
    d = d[d["sector"].str.len() > 0].copy()
    d["theme_n"] = d.groupby(["trading_date", "sector"])["symbol"].transform("size")
    # Stable tie-breaks are explicit and do not use outcomes.
    key = ["trading_date", "sector"]
    d["leader_boards"] = d.groupby(key)["boards"].transform("max")
    board_order = d.sort_values(["trading_date", "sector", "boards", "seal_hm", "symbol"],
                                ascending=[True, True, False, True, True])
    board_idx = board_order.groupby(key, sort=False).head(1).index
    d["leader_boards_flag"] = d.index.isin(board_idx)
    earliest_order = d.sort_values(["trading_date", "sector", "seal_hm", "boards", "symbol"],
                                   ascending=[True, True, True, False, True])
    earliest_idx = earliest_order.groupby(key, sort=False).head(1).index
    d["leader_earliest_flag"] = d.index.isin(earliest_idx)
    ratio_order = d.sort_values(["trading_date", "sector", "seal_ratio", "seal_hm", "symbol"],
                                ascending=[True, True, False, True, True])
    ratio_idx = ratio_order.groupby(key, sort=False).head(1).index
    d["leader_ratio_flag"] = d.index.isin(ratio_idx)
    d["theme_strength"] = pd.cut(d["theme_n"], [0, 1, 3, 6, np.inf],
                                  labels=["1", "2_3", "4_6", "7_plus"], right=True)
    d["board_bucket"] = pd.cut(d["boards"], [0, 1, 2, 3, np.inf],
                                labels=["1", "2", "3", "4_plus"], right=True)
    d["top_theme_flag"] = d["leader_boards"].ge(3)
    d["supplement_flag"] = d["boards"].eq(1) & d["top_theme_flag"]
    d["break_flag"] = d["broken_board"].fillna(False).astype(bool)
    return d


def market_height(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = d.copy()
    top = d.groupby("trading_date")["boards"].transform("max")
    d["market_top"] = d["boards"].eq(top)
    top_rows = d[d.market_top].copy()
    # A market top fails only when every top-board stock fails to seal on T+1.
    fail = top_rows.groupby("trading_date")["seal_next"].apply(lambda x: not x.fillna(False).astype(bool).any())
    d["top_fail"] = d["trading_date"].map(fail).fillna(False)
    others = d[(d["boards"] >= 2) & (~d.market_top)].copy()
    return top_rows, others


def state_bins(c_dev: pd.DataFrame) -> dict:
    out = {}
    for col in ["promotion", "big_loss"]:
        q = c_dev[col].dropna().quantile([1/3, 2/3]).to_numpy()
        out[col] = [float(q[0]), float(q[1])]
    return out


def assign_state(c: pd.DataFrame, cuts: dict) -> pd.DataFrame:
    c = c.copy()
    for col, q in cuts.items():
        c[col + "_state"] = pd.cut(c[col], [-np.inf, q[0], q[1], np.inf],
                                    labels=["low", "mid", "high"], right=True)
    return c


def fmt(x):
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return "NA"
    if isinstance(x, float):
        return f"{x:.6f}"
    return str(x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["development", "holdout"], required=True)
    args = ap.parse_args()
    d, panel, cyc = load(args.period)
    d = add_leaders(d)
    # State cut-offs are always learned from development only.
    cdev = pd.DataFrame(json.loads((DATA / "cycle_series.json").read_text()))
    cdev["date"] = cdev["date"].astype(str)
    cdev = cdev[(cdev.date >= "2023-09-01") & (cdev.date <= "2025-12-31")]
    cuts = state_bins(cdev)
    cyc = assign_state(cyc, cuts)
    d = d.merge(cyc[["trading_date", "promotion_state", "big_loss_state", "promotion", "big_loss"]],
                on="trading_date", how="left")
    out_lines = [f"period={args.period}", f"rows={len(d)}", f"dates={d.trading_date.nunique()}",
                 f"date_min={d.trading_date.min()}", f"date_max={d.trading_date.max()}",
                 "state_cutoffs=" + json.dumps(cuts, ensure_ascii=False)]

    outcomes = ["overnight1", "ret1", "seal_next", "o2o"]
    # 1. Three leader definitions; report raw leader-vs-follower and matched board/gap.
    for name, flag in [("leader_boards", "leader_boards_flag"), ("leader_earliest", "leader_earliest_flag"),
                       ("leader_ratio", "leader_ratio_flag")]:
        z = d[d.theme_n >= 2].copy()
        z["arm"] = np.where(z[flag], "leader", "follower")
        out_lines.append(f"\n[leader:{name}]")
        for o in outcomes:
            q = contrast(z, "arm", "leader", "follower", o)
            out_lines.append("raw\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))
            # Controlled on same boards and T+1 open-gap bucket, within day.
            m = z.dropna(subset=["next_gap_bucket", "boards", o]).copy()
            counts = m.groupby(["trading_date", "boards", "next_gap_bucket", "arm"], observed=True).size().unstack("arm", fill_value=0)
            valid = counts[(counts.get("leader", 0) > 0) & (counts.get("follower", 0) > 0)].reset_index()
            m = m.merge(valid[["trading_date", "boards", "next_gap_bucket"]], on=["trading_date", "boards", "next_gap_bucket"], how="inner")
            q = contrast(m, "arm", "leader", "follower", o)
            out_lines.append("matched_board_gap\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))

    # 2. Theme strength: group means and contrasts to strength=1.
    out_lines.append("\n[theme_strength]")
    for subset_name, subset in [("members", d), ("leaders", d[d.leader_boards_flag]), ("first_boards", d[d.boards == 1])]:
        out_lines.append("subset=" + subset_name)
        for o in outcomes:
            for row in group_stats(subset, "theme_strength", o):
                out_lines.append("group\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in row.items()))
            for g in ["2_3", "4_6", "7_plus"]:
                q = contrast(subset, "theme_strength", g, "1", o)
                out_lines.append("contrast\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))

    # 3. Market height and failure feedback.
    top, others = market_height(d)
    out_lines.append("\n[market_height]")
    for o in ["overnight1", "ret1", "seal_next", "o2o"]:
        for row in group_stats(top, "market_top", o):
            out_lines.append("top\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in row.items()))
    out_lines.append(f"top_stock_days={len(top)} top_dates={top.trading_date.nunique()} failure_dates={int(others.loc[others.top_fail, 'trading_date'].nunique())}")
    for o in ["ret1", "ret_f2", "seal_next", "o2o"]:
        q = independent_day_contrast(others.assign(failure_arm=np.where(others.top_fail, "failure", "nonfailure")),
                                     "failure_arm", "failure", "nonfailure", o)
        out_lines.append("failure_vs_nonfailure\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))

    # 4. Ladder shape by fixed market-state thirds.
    out_lines.append("\n[ladder_state]")
    for state in ["promotion_state", "big_loss_state"]:
        out_lines.append("state=" + state)
        for bb in ["1", "2", "3", "4_plus"]:
            z = d[d.board_bucket.astype(str) == bb]
            for o in outcomes:
                for row in group_stats(z, state, o):
                    out_lines.append(f"board={bb}\t" + o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in row.items()))

    # 5. Supplement and broken-board rules.
    out_lines.append("\n[supplement]")
    z = d[d.boards == 1].copy()
    z["arm"] = np.where(z.supplement_flag, "strong_theme", "no_3plus_theme")
    for o in outcomes:
        q = contrast(z, "arm", "strong_theme", "no_3plus_theme", o)
        out_lines.append(o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))
    for strength in ["1", "2_3", "4_6", "7_plus"]:
        q = contrast(z[z.theme_strength.astype(str) == strength], "arm", "strong_theme", "no_3plus_theme", "ret1")
        out_lines.append("strength=" + strength + "\tret1\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))

    out_lines.append("\n[broken_board]")
    z = d.dropna(subset=["next_gap_bucket"]).copy()
    z["arm"] = np.where(z.break_flag, "broken_then_sealed", "never_broken")
    for o in outcomes:
        # same board count and next-gap bucket: retain strata with both arms on a day.
        counts = z.groupby(["trading_date", "boards", "next_gap_bucket", "arm"], observed=True).size().unstack("arm", fill_value=0)
        valid = counts[(counts.get("broken_then_sealed", 0) > 0) & (counts.get("never_broken", 0) > 0)].reset_index()
        m = z.merge(valid[["trading_date", "boards", "next_gap_bucket"]], on=["trading_date", "boards", "next_gap_bucket"], how="inner")
        q = contrast(m, "arm", "broken_then_sealed", "never_broken", o)
        out_lines.append(o + "\t" + "\t".join(f"{k}={fmt(v)}" for k, v in q.items()))

    # Tradability accounting (not silently hidden).
    out_lines.append("\n[tradability]")
    for col in ["next_open_at_limit_up", "next_limit_down_close"]:
        out_lines.append(f"{col}_count={int(d[col].fillna(False).astype(bool).sum())}")
    out_lines.append(f"o2o_nonmissing={int(d.o2o.notna().sum())} o2o_missing={int(d.o2o.isna().sum())}")
    out_lines.append(f"forward_ret1_missing={int(d.ret1.isna().sum())} seal_next_missing={int(d.seal_next.isna().sum())}")
    (OUT / f"{args.period}_results.txt").write_text("\n".join(out_lines) + "\n")


if __name__ == "__main__":
    main()
