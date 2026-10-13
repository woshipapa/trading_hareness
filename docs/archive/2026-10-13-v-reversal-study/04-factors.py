"""Dispatch 2 of docs/work/plans/intraday-v-reversal-study.md: the five families of conditions, one by one,
each against its base (all events of the same depth, or of the same depth and rebound size).

Reads the derived tables of dispatch 1 and the fund minute files; local files only. Writes
`derived/event_factors_depth.parquet`, `derived/event_factors_rebound.parquet` (events with every condition and the
fund outcomes) and, beside this script, `04-factor-results.csv`.
Run from the worktree root: /Users/papa/.venvs/svc/bin/python -I docs/archive/2026-10-13-v-reversal-study/04-factors.py

No look-ahead: a condition attached to minute t uses minutes up to t, bars closed by t, daily state up to the
previous close and 20-session means that end at the previous session. Outcome columns start with `out_`.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/papa/codebase/n8n/state/research")
DERIVED = BASE / "vshape_20261013/derived"
DATA = BASE / "vshape_20261013/data"
DAILY = BASE / "shortterm_tactics_20261013/data"
HERE = Path(__file__).resolve().parent
FUNDS = ("510300", "510050", "510500", "512100", "159915", "588000")
OUTCOME_FUNDS = ("510300", "512100", "159915", "510500")
DESIGN_END = pd.Timestamp("2025-12-31")
RECENT = pd.Timestamp("2026-07-01")


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return np.nan
    spread = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / spread if spread > 0 else np.nan


def bh(p_values):
    p = np.asarray(p_values, float)
    order = np.argsort(p)
    scaled = p[order] * len(p) / (np.arange(len(p)) + 1)
    q = np.minimum.accumulate(scaled[::-1])[::-1]
    out = np.empty(len(p))
    out[order] = np.minimum(q, 1)
    return out


def p_from_t(t):
    from math import erf, sqrt
    return np.nan if np.isnan(t) else 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))   # normal approximation, n is in the dozens


# ---------------------------------------------------------------- minute lines and index volume
lines = pd.read_parquet(DERIVED / "minute_lines.parquet").sort_values(["day", "pos_sh"]).reset_index(drop=True)
lines["index_volume"] = lines["sh_volume"] + lines["sz_volume"]
days = sorted(lines["day"].unique())
by_day = {day: frame.reset_index(drop=True) for day, frame in lines.groupby("day")}
minutes = sorted(lines["minute"].unique())


def same_minute_ratio(wide):
    """A day-by-minute table over the mean of the same minute across the previous 20 sessions."""
    return wide, wide.rolling(20, min_periods=10).mean().shift(1)


index_wide, index_base = same_minute_ratio(lines.pivot(index="day", columns="minute", values="index_volume"))

# ---------------------------------------------------------------- funds: checks, traded value, outcomes
print("FUND CHECKS")
fund_value, fund_price = {}, {}
for code in FUNDS:
    rows = [json.loads(line) for line in (DATA / f"{code}.jsonl").open(encoding="utf-8")]
    records, ratios, inside = [], [], []
    for row in rows:
        seen, low, high = set(), np.inf, -np.inf
        total = 0.0
        for label, price, third, volume, _flag in row["trend"]:
            if label in seen:
                continue
            seen.add(label)
            low, high = min(low, price), max(high, price)
            inside.append(low - 1e-4 <= third <= high + 1e-4)
            total += price * volume
            records.append((pd.Timestamp(row["day"]), label, price, price * volume))
        if row["total_turnover"]:
            ratios.append(row["total_turnover"] / total)
    frame = pd.DataFrame(records, columns=["day", "minute", "price", "value"])
    fund_value[code] = frame.pivot(index="day", columns="minute", values="value")
    fund_price[code] = frame.pivot(index="day", columns="minute", values="price")
    print(f"  {code}: sessions {len(rows)} | third value inside the day's price range so far: {np.mean(inside):.4f} of points"
          f" | total_turnover / sum(price x volume): median {np.median(ratios):.2f} (p10 {np.percentile(ratios, 10):.2f}, p90 {np.percentile(ratios, 90):.2f})")
fund_minutes = sorted(set.intersection(*(set(table.columns) for table in fund_value.values())))
fund_total = sum(fund_value[code].reindex(columns=fund_minutes) for code in FUNDS)
fund_tables = {**{code: fund_value[code].reindex(columns=fund_minutes) for code in FUNDS}, "six": fund_total}
fund_bases = {name: table.rolling(20, min_periods=10).mean().shift(1) for name, table in fund_tables.items()}

# ---------------------------------------------------------------- daily state, as of the previous close
panel = pd.read_parquet(DAILY / "daily_panel.parquet", columns=["trading_date", "ret", "limit_down_close"])
panel["day"] = pd.to_datetime(panel["trading_date"])
daily = panel.groupby("day").agg(mean_ret=("ret", "mean"), up_share=("ret", lambda s: (s > 0).mean()),
                                 limit_down=("limit_down_close", "sum"))
cycle = pd.DataFrame(json.load(open(DAILY / "cycle_series.json")))
cycle["day"] = pd.to_datetime(cycle["date"])
daily = daily.join(cycle.set_index("day")[["limit_up_prev", "premium", "promotion", "big_loss"]], how="left")
temperature = pd.DataFrame(json.load(open(BASE / "vshape_20261013/owner/temperature_daily.json"))["rows"])
temperature["day"] = pd.to_datetime(temperature["trade_date"])
daily = daily.join(temperature.set_index("day")[["temperature", "etf_flow"]], how="left")
daily["etf_ratio"] = daily["etf_flow"].map(lambda value: value.get("ratio") if isinstance(value, dict) else np.nan)


def trailing_rank(series):
    """Share of the previous 250 sessions (itself included) at or below the value."""
    return series.rolling(250, min_periods=120).apply(lambda window: (window <= window[-1]).mean(), raw=True)


state = pd.DataFrame(index=daily.index)
# `limit_up_prev` of a day is the count of the day before: already known at the open. Everything else is shifted.
state["ice_few_limit_ups"] = trailing_rank(daily["limit_up_prev"]) <= 0.2
state["ice_many_limit_downs"] = trailing_rank(daily["limit_down"]).shift(1) >= 0.8
state["ice_premium_negative"] = daily["premium"].shift(1) < 0
state["ice_low_promotion"] = trailing_rank(daily["promotion"]).shift(1) <= 0.2
state["ice_many_big_losses"] = trailing_rank(daily["big_loss"]).shift(1) >= 0.8
state["ice_two_weak_days"] = (daily["up_share"].shift(1) < 0.3) & (daily["up_share"].shift(2) < 0.3)
state["ice_down_5pct_in_5"] = ((1 + daily["mean_ret"]).rolling(5).apply(np.prod, raw=True).shift(1) - 1) <= -0.05
down = (daily["mean_ret"] < 0).astype(int)
state["ice_three_down_days"] = (down.rolling(3).sum().shift(1) == 3)
previous_temperature = daily["temperature"].shift(1)
state["ice_temperature_40"] = (previous_temperature <= 40).where(previous_temperature.notna())
state["ice_temperature_20"] = (previous_temperature <= 20).where(previous_temperature.notna())
state["ice_temperature_fell_10"] = ((previous_temperature - daily["temperature"].shift(4)) <= -10).where(
    previous_temperature.notna() & daily["temperature"].shift(4).notna())
previous_ratio = daily["etf_ratio"].shift(1)
state["fund_day_before_surge"] = (previous_ratio >= 1.5).where(previous_ratio.notna())
for name in [column for column in state if not column.startswith(("ice_temperature", "fund_day"))]:
    state[name] = state[name].where(daily["mean_ret"].shift(1).notna())
state["ice_count"] = state[[c for c in state if c.startswith("ice_") and "temperature" not in c]].sum(axis=1, min_count=1)
state["ice_two_or_more"] = (state["ice_count"] >= 2).where(state["ice_count"].notna())


# ---------------------------------------------------------------- conditions at an event minute
def at_event(day, minute):
    frame = by_day[day]
    i = int(frame.index[frame["minute"] == minute][0])
    out = {}
    avg, cw = frame["avg"].to_numpy(), frame["cw"].to_numpy()
    small, large = frame["small_cw"].to_numpy(), frame["large_cw"].to_numpy()

    def holds(strong, weak):
        """`weak` made a new low of the day within the last 15 minutes while `strong` kept 0.3 points above its own."""
        if i < 15:
            return np.nan
        return bool(weak[i - 14:i + 1].min() <= weak[:i - 14].min() and strong[i - 14:i + 1].min() >= strong[:i + 1].min() + 0.003)

    out["line_avg_holds"] = holds(avg, cw)
    out["line_weighted_holds"] = holds(cw, avg)
    out["line_small_holds"] = holds(small, large) if not np.isnan(small[: i + 1]).any() and not np.isnan(large[: i + 1]).any() else np.nan
    out["line_large_holds"] = holds(large, small) if not np.isnan(small[: i + 1]).any() and not np.isnan(large[: i + 1]).any() else np.nan
    gap = avg[i] - cw[i]
    out["gap_avg_minus_weighted"] = gap
    out["line_avg_weaker_half_point"] = bool(gap <= -0.005)
    out["line_gap_closing_30m"] = bool(gap - (avg[i - 30] - cw[i - 30]) >= 0.002) if i >= 30 else np.nan
    out["line_gap_widening_30m"] = bool(gap - (avg[i - 30] - cw[i - 30]) <= -0.002) if i >= 30 else np.nan

    # Index volume on the rebound leg: from the running low to t, against as many minutes before the low.
    low_at = int(np.argmin(avg[: i + 1]))
    leg = i - low_at
    labels = frame["minute"].to_numpy()
    if leg >= 3 and low_at - leg >= 0 and day in index_base.index:
        now_cols, before_cols = list(labels[low_at + 1:i + 1]), list(labels[low_at - leg + 1:low_at + 1])
        base_now, base_before = index_base.loc[day, now_cols].sum(), index_base.loc[day, before_cols].sum()
        if base_now > 0 and base_before > 0:
            ratio = (index_wide.loc[day, now_cols].sum() / base_now) / (index_wide.loc[day, before_cols].sum() / base_before)
            out["leg_volume_ratio"] = ratio
            out["leg_volume_up_20pct"] = bool(ratio >= 1.2)
            out["leg_volume_down_20pct"] = bool(ratio <= 0.8)
    # The whole market's volume so far against its usual pace.
    if day in index_base.index and index_base.loc[day, list(labels[: i + 1])].sum() > 0:
        pace = index_wide.loc[day, list(labels[: i + 1])].sum() / index_base.loc[day, list(labels[: i + 1])].sum()
        out["market_volume_pace"] = pace
        out["market_heavy_so_far"] = bool(pace >= 1.3)
        out["market_light_so_far"] = bool(pace <= 0.8)

    # Fund turnover: the last five minutes and from the open, against the same minutes of the last 20 sessions.
    usable = [label for label in labels[: i + 1] if label in fund_minutes]
    low_label = labels[low_at]
    for name in ("six", "510300", "512100"):
        table, base = fund_tables[name], fund_bases[name]
        if day not in table.index or len(usable) < 5 or base.loc[day, usable].isna().any():
            continue
        last5 = table.loc[day, usable[-5:]].sum() / base.loc[day, usable[-5:]].sum()
        so_far = table.loc[day, usable].sum() / base.loc[day, usable].sum()
        out[f"fund_{name}_last5_ratio"], out[f"fund_{name}_so_far_ratio"] = last5, so_far
        out[f"fund_{name}_last5_heavy2"] = bool(last5 >= 2)
        out[f"fund_{name}_last5_heavy3"] = bool(last5 >= 3)
        out[f"fund_{name}_so_far_heavy15"] = bool(so_far >= 1.5)
        if name == "six":
            window = (table.loc[day, usable].rolling(5).sum() / base.loc[day, usable].rolling(5).sum())
            heavy = window[window >= 2]
            out["fund_six_heavy_seen"] = bool(len(heavy))
            out["fund_six_heavy_before_low"] = bool(len(heavy) and heavy.index[0] <= low_label)
    # Fund outcomes from the minute after t.
    position = days.index(day)
    next_day = days[position + 1] if position + 1 < len(days) else None
    for code in OUTCOME_FUNDS:
        prices = fund_price[code]
        if day not in prices.index or i + 1 >= len(labels) or labels[i + 1] not in prices.columns:
            continue
        entry = prices.loc[day, labels[i + 1]]
        close = prices.loc[day].dropna().iloc[-1]
        out[f"out_{code}_to_close"] = close / entry - 1
        if next_day is not None and next_day in prices.index:
            following = prices.loc[next_day].dropna()
            out[f"out_{code}_to_next_open"] = following.iloc[0] / entry - 1
            out[f"out_{code}_to_next_close"] = following.iloc[-1] / entry - 1
    return out


def enrich(events, minute_column):
    extra = pd.DataFrame([at_event(day, minute) for day, minute in zip(events["day"], events[minute_column])], index=events.index)
    frame = pd.concat([events, extra], axis=1).join(state, on="day")
    return frame


depth = enrich(pd.read_parquet(DERIVED / "depth_entry_events.parquet"), "entry_minute")
rebound = enrich(pd.read_parquet(DERIVED / "rebound_confirmation_events.parquet"), "confirm_minute")
depth.to_parquet(DERIVED / "event_factors_depth.parquet")
rebound.to_parquet(DERIVED / "event_factors_rebound.parquet")

# ---------------------------------------------------------------- tests, one condition at a time
FAMILIES = {
    "1 divergence on bars": lambda columns: [c for c in columns if "_div_" in c and "_swing_" not in c],
    "1b divergence, confirmed swings": lambda columns: [c for c in columns if "_div_swing_" in c],
    "2 average stock against weights": lambda columns: [c for c in columns if c.startswith("line_")],
    "3 sentiment ice point": lambda columns: [c for c in columns if c.startswith("ice_") and c != "ice_count"],
    "4 index-fund turnover": lambda columns: [c for c in columns if c.startswith("fund_") and not c.endswith("_ratio")],
    "5 volume": lambda columns: [c for c in columns if c.startswith(("leg_volume_", "market_")) and not c.endswith(("_ratio", "_pace"))],
}
OUTCOMES = ["out_avg_to_close", "out_avg_to_next_open", "out_avg_to_next_close", "out_510300_to_next_open", "out_512100_to_next_open"]
results = []


def test(events, label, extra_outcomes=()):
    # One event per session: the first.
    first = events.sort_values(events.columns[events.columns.str.endswith("_minute")][-1]).groupby("day").head(1)
    for family, pick in FAMILIES.items():
        rows = []
        for condition in pick(first.columns):
            flag = first[condition]
            known = flag.notna()
            true, false = first[known & (flag == True)], first[known & (flag == False)]   # noqa: E712 - three-valued
            if len(true) == 0:
                continue
            row = {"sample": label, "family": family, "condition": condition, "n_true": len(true), "n_false": len(false)}
            for outcome in [*OUTCOMES, *extra_outcomes]:
                short = outcome.replace("out_", "")
                row[f"{short}|true"], row[f"{short}|false"] = true[outcome].mean(), false[outcome].mean()
                row[f"{short}|t"] = welch(true[outcome], false[outcome])
            for period, mask in (("design", first["day"] <= DESIGN_END), ("check", first["day"] > DESIGN_END), ("recent", first["day"] >= RECENT)):
                part_true, part_false = first[mask & known & (flag == True)], first[mask & known & (flag == False)]   # noqa: E712
                row[f"n_true|{period}"] = len(part_true)
                row[f"avg_to_close|diff|{period}"] = part_true["out_avg_to_close"].mean() - part_false["out_avg_to_close"].mean()
                row[f"avg_to_next_close|diff|{period}"] = part_true["out_avg_to_next_close"].mean() - part_false["out_avg_to_next_close"].mean()
            rows.append(row)
        if rows:
            table = pd.DataFrame(rows)
            for short in ("avg_to_close", "avg_to_next_close"):
                table[f"{short}|q"] = bh([p_from_t(t) if not np.isnan(t) else 1.0 for t in table[f"{short}|t"]])
            results.extend(table.to_dict("records"))


for value in (-0.015, -0.02, -0.03):
    test(depth[depth["depth"] == value], f"depth entry {value:.1%}")
for value in (-0.015, -0.02, -0.03):
    for size in (0.005, 0.008):
        part = rebound[(rebound["depth"] == value) & (rebound["rebound_size"] == size)].copy()
        part["out_held"] = part["out_held_no_new_low"].astype(float)
        test(part, f"rebound {size:.1%} after {value:.1%}", extra_outcomes=("out_held",))
table = pd.DataFrame(results)
table.to_csv(HERE / "04-factor-results.csv", index=False)

# ---------------------------------------------------------------- what to read first
pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_rows", 400)
print(f"\nEVENTS depth {len(depth)} rows / {depth['day'].nunique()} sessions; rebound {len(rebound)} rows / {rebound['day'].nunique()} sessions; conditions tested {len(table)} rows")
for label in ("depth entry -2.0%", "rebound 0.5% after -2.0%"):
    part = table[table["sample"] == label]
    base = depth[depth["depth"] == -0.02] if label.startswith("depth") else rebound[(rebound["depth"] == -0.02) & (rebound["rebound_size"] == 0.005)].sort_values("confirm_minute").groupby("day").head(1)
    print(f"\n=== {label}: base n={len(base)} sessions | avg to close {base['out_avg_to_close'].mean():+.4f} | to next open {base['out_avg_to_next_open'].mean():+.4f}"
          f" | to next close {base['out_avg_to_next_close'].mean():+.4f}" + (f" | held {base['out_held_no_new_low'].mean():.2f}" if "out_held_no_new_low" in base else ""))
    for family in FAMILIES:
        block = part[(part["family"] == family) & (part["n_true"] >= 8)].copy()
        if block.empty:
            continue
        block["strength"] = block["avg_to_close|t"].abs()
        cols = ["condition", "n_true", "n_false", "avg_to_close|true", "avg_to_close|false", "avg_to_close|t", "avg_to_close|q",
                "avg_to_next_close|true", "avg_to_next_close|false", "avg_to_next_close|t", "n_true|check", "avg_to_close|diff|design", "avg_to_close|diff|check"]
        if "held|t" in block:
            cols[7:7] = ["held|true", "held|false", "held|t"]
        print(f"-- {family} (conditions with at least 8 true sessions; strongest first, at most 8)")
        print(block.sort_values("strength", ascending=False).head(8)[cols].round(4).to_string(index=False))
