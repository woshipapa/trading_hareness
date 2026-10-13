"""Dispatch 3: the conditions together, the two periods, and the sessions since 2026-07-01 one by one.

The six conditions counted are the ones whose sign in dispatch 2 was the same in the design period (to 2025-12)
and in the check period (2026) on the -1.5% and -2% samples; they were picked on dispatch 2's tables, so this is
a description of how they stack, in sample, not a test. A condition that is unknown for a session (no data) is
left out of the count, never counted as false.
Run from the worktree root: /Users/papa/.venvs/svc/bin/python -I docs/archive/2026-10-13-v-reversal-study/05-combinations.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

DERIVED = Path("/Users/papa/codebase/n8n/state/research/vshape_20261013/derived")
HERE = Path(__file__).resolve().parent
PICKED = ["fund_day_before_surge", "ice_down_5pct_in_5", "ice_many_limit_downs", "fund_512100_so_far_heavy15",
          "fund_six_last5_heavy2", "bar30_sh_price_div_hist"]
SHORT = {"fund_day_before_surge": "昨日宽基放量", "ice_down_5pct_in_5": "五日跌5%", "ice_many_limit_downs": "昨日跌停多",
         "fund_512100_so_far_heavy15": "1000ETF放量", "fund_six_last5_heavy2": "宽基近5分钟放量", "bar30_sh_price_div_hist": "30分钟背离"}
DESIGN_END, RECENT = pd.Timestamp("2025-12-31"), pd.Timestamp("2026-07-01")
depth = pd.read_parquet(DERIVED / "event_factors_depth.parquet")
rebound = pd.read_parquet(DERIVED / "event_factors_rebound.parquet")


def scored(events):
    flags = events[PICKED].apply(lambda column: column.map(lambda value: np.nan if pd.isna(value) else float(bool(value))))
    events = events.copy()
    events["true_count"] = flags.sum(axis=1, min_count=1)
    events["known_count"] = flags.notna().sum(axis=1)
    events["bucket"] = np.where(events["true_count"] >= 2, "2+", np.where(events["true_count"] == 1, "1", "0"))
    events["which"] = flags.apply(lambda row: "、".join(SHORT[name] for name in PICKED if row[name] == 1.0) or "无", axis=1)
    return events


def table(events, label, held=False):
    print(f"\n== {label}: sessions {len(events)}")
    for period, mask in (("all", slice(None)), ("to 2025-12", events["day"] <= DESIGN_END), ("2026", events["day"] > DESIGN_END),
                         ("since 2026-07-01", events["day"] >= RECENT)):
        part = events.loc[mask]
        line = []
        for bucket in ("0", "1", "2+"):
            cell = part[part["bucket"] == bucket]
            text = f"{bucket}: n={len(cell):>3}"
            if len(cell):
                text += f" close {cell['out_avg_to_close'].mean() * 100:+.2f}% nextC {cell['out_avg_to_next_close'].mean() * 100:+.2f}%"
                text += f" up-share {np.mean(cell['out_avg_to_close'] > 0):.2f}"
                if held:
                    text += f" held {cell['out_held_no_new_low'].mean():.2f}"
            line.append(text)
        print(f"  {period:<17} " + " | ".join(line))


for value in (-0.015, -0.02):
    table(scored(depth[depth["depth"] == value]), f"depth entry {value:.1%}, by how many of the six conditions are true")
first_rebound = rebound[(rebound["depth"] == -0.02) & (rebound["rebound_size"] == 0.005)].sort_values("confirm_minute").groupby("day").head(1)
table(scored(first_rebound), "first rebound of 0.5 points after -2%", held=True)

recent = scored(depth[(depth["depth"] == -0.015) & (depth["day"] >= RECENT)]).sort_values("day")
recent_rebound = scored(first_rebound[first_rebound["day"] >= RECENT]).set_index("day")
rows = []
for _, row in recent.iterrows():
    confirm = recent_rebound.loc[row["day"]] if row["day"] in recent_rebound.index else None
    rows.append({"day": row["day"].date().isoformat(), "entry_-1.5%": row["entry_minute"], "conditions_at_entry": row["which"],
                 "to_close_%": round(row["out_avg_to_close"] * 100, 2), "to_next_close_%": round(row["out_avg_to_next_close"] * 100, 2),
                 "rebound_after_-2%": "" if confirm is None else confirm["confirm_minute"],
                 "held": "" if confirm is None else bool(confirm["out_held_no_new_low"]),
                 "rebound_to_close_%": "" if confirm is None else round(confirm["out_avg_to_close"] * 100, 2)})
listing = pd.DataFrame(rows)
listing.to_csv(HERE / "05-sessions-since-2026-07-01.csv", index=False)
pd.set_option("display.width", 220, "display.max_colwidth", 60)
print("\n== sessions since 2026-07-01 in which the average stock fell through -1.5% (returns of the average-stock line from the minute after)")
print(listing.to_string(index=False))
