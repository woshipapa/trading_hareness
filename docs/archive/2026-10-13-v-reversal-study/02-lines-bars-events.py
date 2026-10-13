import json
from pathlib import Path

import numpy as np
import pandas as pd


INDEX_DIR = Path("/Users/papa/codebase/n8n/state/research/vshape_20261013/data")
DAILY_PATH = Path("/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/daily_panel.parquet")
DERIVED = Path("/Users/papa/codebase/n8n/state/research/vshape_20261013/derived")
ARCHIVE = Path(__file__).resolve().parent
CODES = ["SH000001", "SZ399001", "SZ399006", "SH000852", "SH000300"]
BAR_ENDS = {
    5: [*pd.date_range("09:35", "11:30", freq="5min").strftime("%H:%M"), *pd.date_range("13:05", "15:00", freq="5min").strftime("%H:%M")],
    15: [*pd.date_range("09:45", "11:30", freq="15min").strftime("%H:%M"), *pd.date_range("13:15", "15:00", freq="15min").strftime("%H:%M")],
    30: [*pd.date_range("10:00", "11:30", freq="30min").strftime("%H:%M"), *pd.date_range("13:30", "15:00", freq="30min").strftime("%H:%M")],
    60: ["10:30", "11:30", "14:00", "15:00"],
}


def load_index(code):
    rows = []
    with (INDEX_DIR / f"{code}.jsonl").open() as handle:
        for line in handle:
            item = json.loads(line)
            seen = set()
            for pos, point in enumerate(item["trend"]):
                minute = point[0]
                if minute in seen:
                    continue
                seen.add(minute)
                rows.append(
                    {
                        "day": pd.to_datetime(item["day"], format="%Y%m%d"),
                        "minute": minute,
                        "pos": pos,
                        "price": point[1],
                        "third": point[2],
                        "volume": point[3],
                        "preclose": item["preclose"],
                    }
                )
    return pd.DataFrame(rows)


def build_lines(indexes):
    daily = pd.read_parquet(DAILY_PATH, columns=["symbol", "trading_date"])
    daily["trading_date"] = pd.to_datetime(daily["trading_date"])
    counts = daily.assign(exchange=np.where(daily.symbol.astype(str).str.endswith(".SH"), "sh", "sz"))
    counts = counts.groupby(["trading_date", "exchange"]).size().unstack().reindex(columns=["sh", "sz"])
    counts["w_sh"] = counts["sh"].shift(1)
    counts["w_sz"] = counts["sz"].shift(1)
    counts = counts.reset_index().rename(columns={"trading_date": "day"})

    sh = indexes["SH000001"].rename(columns={"price": "sh_price", "third": "sh_third", "volume": "sh_volume", "preclose": "sh_preclose"})
    sz = indexes["SZ399001"].rename(columns={"price": "sz_price", "third": "sz_third", "volume": "sz_volume", "preclose": "sz_preclose"})
    line = sh.merge(sz, on=["day", "minute"], how="inner", suffixes=("_sh", "_sz"))
    line = line.merge(counts[["day", "w_sh", "w_sz"]], on="day", how="left").dropna(subset=["w_sh", "w_sz"])
    line["sh_ew"] = line.sh_third / line.sh_preclose - 1
    line["sz_ew"] = line.sz_third / line.sz_preclose - 1
    line["cw"] = line.sh_price / line.sh_preclose - 1
    line["avg"] = (line.sh_ew * line.w_sh + line.sz_ew * line.w_sz) / (line.w_sh + line.w_sz)
    for code, prefix in [("SH000852", "small"), ("SH000300", "large")]:
        extra = indexes[code][["day", "minute", "price", "preclose"]].rename(columns={"price": f"{prefix}_price", "preclose": f"{prefix}_preclose"})
        line = line.merge(extra, on=["day", "minute"], how="left")
        line[f"{prefix}_cw"] = line[f"{prefix}_price"] / line[f"{prefix}_preclose"] - 1
    line = line.sort_values(["day", "minute"]).reset_index(drop=True)
    return line


def make_bars(line, value, label, frequency):
    rows = []
    for day, group in line.groupby("day", sort=True):
        group = group.sort_values("minute")
        previous = "09:29"
        for end in BAR_ENDS[frequency]:
            block = group[(group.minute > previous) & (group.minute <= end)]
            previous = end
            if block.empty:
                continue
            rows.append(
                {
                    "day": day,
                    "bar_end": end,
                    "line": label,
                    "frequency": frequency,
                    "close": block[value].iloc[-1],
                    "low": block[value].min(),
                    "high": block[value].max(),
                    "volume": block["sh_volume"].sum() + block["sz_volume"].sum(),
                    "source_last_minute": block.minute.iloc[-1],
                }
            )
    return pd.DataFrame(rows)


def add_indicators(frame):
    frame = frame.sort_values(["line", "frequency", "day", "bar_end"]).reset_index(drop=True)
    pieces = []
    for (_, _), group in frame.groupby(["line", "frequency"], sort=False):
        group = group.copy()
        ema12 = group.close.ewm(span=12, adjust=False, min_periods=1).mean()
        ema26 = group.close.ewm(span=26, adjust=False, min_periods=1).mean()
        group["dif"] = ema12 - ema26
        group["signal"] = group.dif.ewm(span=9, adjust=False, min_periods=1).mean()
        group["hist"] = group.dif - group.signal
        delta = group.close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        group["rsi"] = 100 - 100 / (1 + rs)
        for indicator in ["dif", "hist", "rsi"]:
            p_last = group.low.rolling(6, min_periods=6).min()
            p_prev = p_last.shift(6)
            i_last = group[indicator].rolling(6, min_periods=6).min()
            i_prev = i_last.shift(6)
            group[f"div_{indicator}"] = (p_last < p_prev) & (i_last > i_prev)
        robust = {"dif": [], "hist": [], "rsi": []}
        swing_points = []
        lows = group.low.to_numpy()
        for current in range(len(group)):
            candidate = current - 2
            if candidate >= 2 and current < len(group) and candidate + 2 < len(group):
                if lows[candidate] <= lows[candidate - 2 : candidate + 3].min():
                    swing_points.append(candidate)
            for indicator in robust:
                value = False
                if len(swing_points) >= 2:
                    a, b = swing_points[-2:]
                    if lows[b] < lows[a] and group[indicator].iloc[b] > group[indicator].iloc[a]:
                        value = True
                robust[indicator].append(value)
        for indicator, values in robust.items():
            group[f"div_swing_{indicator}"] = values
        pieces.append(group)
    return pd.concat(pieces, ignore_index=True)


def last_bar_features(bars, day, minute):
    result = {}
    for frequency in sorted(bars.frequency.unique()):
        for label in ["sh_price", "avg"]:
            subset = bars[(bars.day == day) & (bars.frequency == frequency) & (bars.line == label) & (bars.bar_end <= minute)]
            if subset.empty:
                continue
            row = subset.iloc[-1]
            for col in ["dif", "hist", "rsi", "div_dif", "div_hist", "div_rsi", "div_swing_dif", "div_swing_hist", "div_swing_rsi"]:
                result[f"bar{frequency}_{label}_{col}"] = row[col]
    return result


def outcome(line_by_day, days, day, index):
    current = line_by_day[day]
    if index + 1 >= len(current):
        return {"out_entry_next": np.nan}
    entry = current.avg.iloc[index + 1]
    later = current.iloc[index + 1 :]
    day_close = current.avg.iloc[-1]
    high_index = later.avg.idxmax()
    high = later.avg.loc[high_index]
    next_pos = days.index(day) + 1
    next_day = line_by_day[days[next_pos]] if next_pos < len(days) else None
    values = {
        "out_entry_next": entry,
        "out_avg_to_close": (1 + day_close) / (1 + entry) - 1,
        "out_new_low_before_close": bool((later.avg < current.avg.iloc[: index + 1].min()).any()),
        "out_rise_to_later_high": (1 + high) / (1 + entry) - 1,
        "out_fall_high_to_close": (1 + day_close) / (1 + high) - 1,
        "out_later_high_minute": current.minute.loc[high_index],
    }
    if next_day is None:
        values.update({"out_avg_to_next_open": np.nan, "out_avg_to_next_close": np.nan, "out_fall_high_to_next_open": np.nan})
    else:
        values["out_avg_to_next_open"] = (1 + day_close) / (1 + entry) * (1 + next_day.avg.iloc[0]) - 1
        values["out_avg_to_next_close"] = (1 + day_close) / (1 + entry) * (1 + next_day.avg.iloc[-1]) - 1
        values["out_fall_high_to_next_open"] = (1 + next_day.avg.iloc[0]) / (1 + high) - 1
    return values


def depth_events(line, bars):
    line_by_day = {day: group.sort_values("minute").reset_index(drop=True) for day, group in line.groupby("day", sort=True)}
    days = list(line_by_day)
    depths = (-0.015, -0.02, -0.03)
    rows = []
    rebound_rows = []
    for day in days:
        group = line_by_day[day]
        for depth in depths:
            hits = np.flatnonzero(group.avg.to_numpy() <= depth)
            if not len(hits):
                continue
            entry_idx = int(hits[0])
            base = {
                "day": day,
                "depth": depth,
                "entry_minute": group.minute.iloc[entry_idx],
                "entry_index": entry_idx,
                "entry_avg": group.avg.iloc[entry_idx],
                "entry_running_low": group.avg.iloc[: entry_idx + 1].min(),
                "entry_cw": group.cw.iloc[entry_idx],
            }
            base.update(last_bar_features(bars, day, group.minute.iloc[entry_idx]))
            base.update(outcome(line_by_day, days, day, entry_idx))
            rows.append(base)
            for rebound_size in (0.005, 0.008):
                running_low = group.avg.iloc[entry_idx]
                armed = True
                sequence = 0
                for idx in range(entry_idx + 1, len(group)):
                    value = group.avg.iloc[idx]
                    if value < running_low:
                        running_low = value
                        armed = True
                    if armed and value >= running_low + rebound_size:
                        row = {
                            "day": day,
                            "depth": depth,
                            "rebound_size": rebound_size,
                            "confirm_sequence": sequence,
                            "depth_entry_minute": group.minute.iloc[entry_idx],
                            "confirm_minute": group.minute.iloc[idx],
                            "confirm_index": idx,
                            "confirm_avg": value,
                            "confirm_running_low": running_low,
                            "confirm_gain_from_low": value - running_low,
                            "out_held_no_new_low": bool(not (group.avg.iloc[idx + 1 :] < group.avg.iloc[: idx + 1].min()).any()),
                        }
                        row.update(last_bar_features(bars, day, group.minute.iloc[idx]))
                        row.update(outcome(line_by_day, days, day, idx))
                        rebound_rows.append(row)
                        sequence += 1
                        armed = False
    return pd.DataFrame(rows), pd.DataFrame(rebound_rows)


def main():
    DERIVED.mkdir(parents=True, exist_ok=True)
    indexes = {code: load_index(code) for code in CODES}
    line = build_lines(indexes)
    line.to_parquet(DERIVED / "minute_lines.parquet", index=False)
    bars = pd.concat(
        [make_bars(line, "sh_price", "sh_price", frequency) for frequency in BAR_ENDS for _ in [0]]
        + [make_bars(line, "avg", "avg", frequency) for frequency in BAR_ENDS for _ in [0]],
        ignore_index=True,
    )
    bars = add_indicators(bars)
    bars.to_parquet(DERIVED / "bars_with_indicators.parquet", index=False)
    depth, rebound = depth_events(line, bars)
    depth.to_parquet(DERIVED / "depth_entry_events.parquet", index=False)
    rebound.to_parquet(DERIVED / "rebound_confirmation_events.parquet", index=False)
    depth.to_csv(ARCHIVE / "depth_entry_events.csv", index=False)
    rebound.to_csv(ARCHIVE / "rebound_confirmation_events.csv", index=False)
    print("DISPATCH1 lines-bars-events; fund files intentionally not opened")
    print(f"minute_lines rows={len(line)} sessions={line.day.nunique()} columns={len(line.columns)}")
    print(f"bars rows={len(bars)} frequencies={sorted(bars.frequency.unique())} lines={sorted(bars.line.unique())}")
    print(f"depth_events rows={len(depth)} by_depth={depth.groupby('depth').size().to_dict()}")
    print(f"rebound_events rows={len(rebound)} by_depth_size={rebound.groupby(['depth','rebound_size']).size().to_dict()}")
    print("outcome_columns=" + ",".join(sorted(col for col in rebound.columns if col.startswith("out_"))))
    print("bar_columns_last_minute=source_last_minute; indicators and divergence use only closed bars ending at source_last_minute")


if __name__ == "__main__":
    main()
