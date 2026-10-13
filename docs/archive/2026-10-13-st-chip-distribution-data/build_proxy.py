#!/usr/bin/env python3
"""Build the preregistered volume-decay cost-distribution proxy."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data")
OUT = Path("/tmp/st_chip_distribution_proxy.parquet")
RESULT = Path("docs/archive/2026-10-13-st-chip-distribution-data/proxy_build.txt")

COLS = [
    "symbol", "trading_date", "di", "close", "pre_close", "volume", "amount",
    "limit_up_close", "limit_up_touch", "broken_board", "streak", "bucket", "gap", "vr",
    "overnight1", "ret1", "ret_2_3", "o2o", "next_open_at_limit_up", "next_limit_down_close",
]


def weighted_quantile(cost: np.ndarray, weight: np.ndarray, q: float) -> float:
    order = np.argsort(cost)
    c = cost[order]
    w = weight[order]
    cs = np.cumsum(w)
    return float(c[np.searchsorted(cs, q * cs[-1], side="left")])


def one_stock(g: pd.DataFrame, half_life: float) -> np.ndarray:
    close = g.close.to_numpy(np.float64)
    pre = g.pre_close.to_numpy(np.float64)
    vol = g.volume.to_numpy(np.float64)
    amount = g.amount.to_numpy(np.float64)
    n = len(g)
    # The adjustment factor is the ex-rights jump: today's exchange reference
    # divided by yesterday's stored close. Ordinary sessions therefore have
    # factor 1; only corporate-action gaps rescale historical prices.
    prev_close = np.empty(n, dtype=np.float64)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    factor = np.divide(pre, prev_close, out=np.ones(n), where=(pre > 0) & (prev_close > 0))
    factor[0] = 1.0
    future = np.ones(n, dtype=np.float64)
    running = 1.0
    for i in range(n - 1, -1, -1):
        future[i] = running
        running *= factor[i]
    adj_close = close * future
    amount_fix = amount.copy()
    bad_scale = (vol > 0) & (close > 0) & ((amount / (vol * close / 10.0)) > 50.0)
    amount_fix[bad_scale] /= 1000.0
    avg = np.divide(amount_fix * 1000.0, vol * 100.0, out=np.full(n, np.nan), where=(vol > 0))
    adj_avg = avg * future
    valid = np.isfinite(adj_avg) & (adj_avg > 0) & np.isfinite(vol) & (vol > 0)
    out = np.full((n, 10), np.nan, dtype=np.float32)
    for t in range(n):
        lo = max(0, t - 120)
        idx = np.arange(lo, t)
        ok = valid[idx]
        if ok.sum() < 20 or not np.isfinite(adj_close[t]) or adj_close[t] <= 0:
            continue
        age = t - idx[ok]
        costs = adj_avg[idx[ok]]
        base_w = vol[idx[ok]]
        decay = np.exp(-np.log(2.0) * age / half_life)
        w = base_w * decay
        total = w.sum()
        if not np.isfinite(total) or total <= 0:
            continue
        c = adj_close[t]
        profit = w[costs < c].sum() / total
        over010 = w[(costs >= c) & (costs < c * 1.10)].sum() / total
        over1030 = w[(costs >= c * 1.10) & (costs < c * 1.30)].sum() / total
        q05 = weighted_quantile(costs, w, 0.05)
        q95 = weighted_quantile(costs, w, 0.95)
        width = (q95 - q05) / c
        avg_dist = np.dot(costs, w) / total / c - 1.0
        out[t, 0:5] = (profit, over010, over1030, width, avg_dist)
        out[t, 5] = (over010 + over1030)
        out[t, 6] = q95 / c - 1.0
        out[t, 7] = q05 / c - 1.0
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if OUT.exists() and not args.force:
        print(f"cached={OUT} size={OUT.stat().st_size}")
        return
    t0 = time.time()
    panel = pd.read_parquet(DATA / "daily_panel.parquet", columns=COLS)
    panel = panel.sort_values(["symbol", "di"], kind="mergesort").reset_index(drop=True)
    sanity = []
    no_event = ["000006.SZ", "000010.SZ", "000020.SZ"]
    for symbol in no_event:
        g = panel[panel.symbol == symbol]
        close = g.close.to_numpy(float); pre = g.pre_close.to_numpy(float)
        prev = np.r_[close[:1], close[:-1]]
        f = np.divide(pre, prev, out=np.ones(len(g)), where=(pre > 0) & (prev > 0))
        f[0] = 1.0
        future = np.ones(len(g)); running = 1.0
        for i in range(len(g)-1, -1, -1): future[i] = running; running *= f[i]
        sanity.append(f"sanity_no_event symbol={symbol} rows={len(g)} max_abs_adj_close_minus_close={np.max(np.abs(close*future-close)):.8g}")
    g = panel[panel.symbol == "000001.SZ"].copy()
    close = g.close.to_numpy(float); pre = g.pre_close.to_numpy(float); prev = np.r_[close[:1], close[:-1]]
    f = np.divide(pre, prev, out=np.ones(len(g)), where=(pre > 0) & (prev > 0)); future = np.ones(len(g)); running = 1.0
    f[0] = 1.0
    for i in range(len(g)-1, -1, -1): future[i] = running; running *= f[i]
    events = np.flatnonzero(np.abs(f - 1) > 1e-4)
    for i in events[:3]: sanity.append(f"sanity_event symbol=000001.SZ di={int(g.iloc[i].di)} factor={f[i]:.8g} close={close[i]:.8g} adj_close={close[i]*future[i]:.8g} prior_adj_close={close[i-1]*future[i-1] if i else np.nan:.8g}")
    all_parts = []
    for k, (symbol, g) in enumerate(panel.groupby("symbol", sort=False), 1):
        a = one_stock(g, 20.0)
        b = one_stock(g, 60.0)
        part = g[["symbol", "trading_date", "di", "bucket", "gap", "vr", "overnight1", "ret1", "ret_2_3", "o2o", "next_open_at_limit_up", "next_limit_down_close", "limit_up_close", "limit_up_touch", "broken_board", "streak"]].copy()
        names = ["profit", "over010", "over1030", "concentration", "avg_cost_dist", "overhead30", "q95_rel", "q05_rel"]
        for j, name in enumerate(names):
            part[f"hl20_{name}"] = a[:, j]
            part[f"hl60_{name}"] = b[:, j]
        all_parts.append(part)
        if k % 250 == 0:
            print(f"stocks={k} elapsed={time.time()-t0:.1f}s", flush=True)
    result = pd.concat(all_parts, ignore_index=True)
    result.to_parquet(OUT, index=False)
    for hl in (20, 60):
        q = result[f"hl{hl}_profit"].dropna().quantile([.05, .25, .5, .75, .95])
        sanity.append(f"sanity_profit_quantiles hl={hl} q05={q.iloc[0]:.8g} q25={q.iloc[1]:.8g} q50={q.iloc[2]:.8g} q75={q.iloc[3]:.8g} q95={q.iloc[4]:.8g}")
        lu = result[result.limit_up_close.fillna(False)]
        sanity.append(f"sanity_limit_up_q95_share hl={hl} n={len(lu)} share={(lu[f'hl{hl}_q95_rel'] < 0).mean():.8g}")
        q = result[f"hl{hl}_avg_cost_dist"].dropna().quantile([.05, .5, .95])
        sanity.append(f"sanity_avg_cost_dist_quantiles hl={hl} q05={q.iloc[0]:.8g} q50={q.iloc[1]:.8g} q95={q.iloc[2]:.8g}")
    RESULT.write_text(
        f"rows={len(result)} stocks={result.symbol.nunique()} cols={len(result.columns)}\n"
        f"output={OUT} bytes={OUT.stat().st_size}\n"
        f"elapsed_seconds={time.time()-t0:.1f}\n"
        f"half_lives=20,60 lookback=120 min_valid=20\n"
        + "\n".join(sanity) + "\n"
    )
    print(RESULT.read_text(), end="")


if __name__ == "__main__":
    main()
