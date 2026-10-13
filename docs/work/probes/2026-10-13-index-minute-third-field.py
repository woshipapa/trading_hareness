"""What is the third value of each minute point of an index series (GetStockTrend)?

For an ETF it is the running average traded price. For an index it cannot be: it leaves the range of the
prices seen so far. This compares its close-over-preclose change with the cross-section of stocks from the
local daily panel, to see whether it is the equal-weighted ("lead") line of the index.
Reads local files only. Run: /Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-index-minute-third-field.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "state/research/vshape_20261013/data"
panel = pd.read_parquet(ROOT / "state/research/shortterm_tactics_20261013/data/daily_panel.parquet",
                        columns=["symbol", "trading_date", "ret", "low", "pre_close", "is_st_like"])
panel["day"] = pd.to_datetime(panel["trading_date"]).dt.strftime("%Y%m%d")
panel["sh"] = panel["symbol"].str.endswith(".SH")
panel["low_ret"] = panel["low"] / panel["pre_close"] - 1
cross = {name: frame.groupby("day").agg(mean=("ret", "mean"), median=("ret", "median"), low_median=("low_ret", "median"))
         for name, frame in (("all", panel), ("SH", panel[panel["sh"]]), ("SZ", panel[~panel["sh"]]))}

for code, market in (("SH000001", "SH"), ("SZ399001", "SZ"), ("SZ399006", "SZ")):
    rows = [json.loads(line) for line in (DATA / f"{code}.jsonl").open(encoding="utf-8")]
    frame = pd.DataFrame({
        "day": [row["day"] for row in rows],
        "price": [row["trend"][-1][1] / row["preclose"] - 1 for row in rows],
        "third": [row["trend"][-1][2] / row["preclose"] - 1 for row in rows],
        "third_low": [min(point[2] for point in row["trend"]) / row["preclose"] - 1 for row in rows],
        "outside": [sum(1 for i, point in enumerate(row["trend"])
                        if not min(p[1] for p in row["trend"][:i + 1]) - 1e-6 <= point[2] <= max(p[1] for p in row["trend"][:i + 1]) + 1e-6) / len(row["trend"])
                    for row in rows],
        "low_is_third": [abs(min(point[2] for point in row["trend"]) - row["low"]) < abs(min(point[1] for point in row["trend"]) - row["low"]) for row in rows],
    }).set_index("day")
    print(f"[{code}] sessions {len(frame)} | minutes where the third value lies outside the prices seen so far that day: mean share {frame['outside'].mean():.3f} "
          f"| sessions where the stored day low is nearer the third value's low than the price's low: {int(frame['low_is_third'].sum())}")
    for name in ("all", market):
        joined = frame.join(cross[name], how="inner")
        line = [f"  against the {name} stocks ({len(joined)} sessions):"]
        for column in ("mean", "median"):
            line.append(f"corr(third, {column} stock return) {np.corrcoef(joined['third'], joined[column])[0, 1]:.4f}")
            line.append(f"corr(price, {column}) {np.corrcoef(joined['price'], joined[column])[0, 1]:.4f}")
        line.append(f"mean abs gap third - mean stock return {np.abs(joined['third'] - joined['mean']).mean() * 100:.3f} pt")
        line.append(f"corr(third's intraday low, median stock low) {np.corrcoef(joined['third_low'], joined['low_median'])[0, 1]:.4f}")
        print(" | ".join(line))
    recent = frame.join(cross[market], how="inner").loc[["20261009", "20261012"]]
    for day, row in recent.iterrows():
        print(f"  {day}: index close {row['price'] * 100:+.2f}% | third value at close {row['third'] * 100:+.2f}% | mean {market} stock {row['mean'] * 100:+.2f}% | median {row['median'] * 100:+.2f}%")
