import json
from pathlib import Path

import numpy as np
import pandas as pd


INDEX_DIR = Path("/Users/papa/codebase/n8n/state/research/vshape_20261013/data")
DAILY_PATH = Path("/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data/daily_panel.parquet")
RADAR_DIR = Path("/Users/papa/codebase/n8n/state/research/vshape_20261013/radar")
CODES = ["SH000001", "SZ399001", "SZ399006", "SH000852", "SH000300"]


def load_index(code):
    rows = []
    duplicate = 0
    duplicate_labels = {}
    duplicate_value_disagree = 0
    duplicate_volume_diff_days = 0
    with (INDEX_DIR / f"{code}.jsonl").open() as handle:
        for line in handle:
            item = json.loads(line)
            seen = set()
            trend = []
            for point in item["trend"]:
                if point[0] in seen:
                    duplicate += 1
                    duplicate_labels[point[0]] = duplicate_labels.get(point[0], 0) + 1
                    kept = next(value for value in trend if value[0] == point[0])
                    if kept[1:4] != point[1:4]:
                        duplicate_value_disagree += 1
                        if kept[3] != point[3]:
                            duplicate_volume_diff_days += 1
                    continue
                seen.add(point[0])
                trend.append(point)
            for pos, (minute, price, third, volume, flag) in enumerate(trend):
                rows.append(
                    {
                        "day": item["day"],
                        "minute": minute,
                        "pos": pos,
                        "price": price,
                        "third": third,
                        "volume": volume,
                    }
                )
    frame = pd.DataFrame(rows)
    return frame, duplicate, duplicate_labels, duplicate_value_disagree, duplicate_volume_diff_days


def build_avg(indexes, daily):
    sh = indexes["SH000001"].rename(columns={"price": "sh_price", "third": "sh_third"})
    sz = indexes["SZ399001"].rename(columns={"price": "sz_price", "third": "sz_third"})
    line = sh.merge(sz, on=["day", "minute", "pos"], how="inner", suffixes=("_sh", "_sz"))
    counts = daily[["symbol", "trading_date"]].copy()
    counts["exchange"] = np.where(counts["symbol"].astype(str).str.endswith(".SH"), "sh", "sz")
    counts = counts.groupby(["trading_date", "exchange"], sort=True).size().unstack()
    counts = counts.reindex(columns=["sh", "sz"])
    counts["w_sh"] = counts["sh"].shift(1)
    counts["w_sz"] = counts["sz"].shift(1)
    counts = counts.reset_index().rename(columns={"trading_date": "day"})
    line["day"] = pd.to_datetime(line["day"], format="%Y%m%d")
    counts["day"] = pd.to_datetime(counts["day"])
    line = line.merge(counts[["day", "w_sh", "w_sz"]], on="day", how="left")
    line = line.dropna(subset=["w_sh", "w_sz"]).copy()
    pre = indexes["SH000001"].groupby("day", as_index=False)["preclose"].first() if "preclose" in indexes["SH000001"] else None
    if pre is None:
        raise RuntimeError("preclose missing in index loader")
    line = line.merge(pre.rename(columns={"preclose": "sh_preclose"}), on="day", how="left")
    szpre = indexes["SZ399001"].groupby("day", as_index=False)["preclose"].first()
    line = line.merge(szpre.rename(columns={"preclose": "sz_preclose"}), on="day", how="left")
    line["sh_ew"] = line["sh_third"] / line["sh_preclose"] - 1
    line["sz_ew"] = line["sz_third"] / line["sz_preclose"] - 1
    line["cw"] = line["sh_price"] / line["sh_preclose"] - 1
    line["avg"] = (line["sh_ew"] * line["w_sh"] + line["sz_ew"] * line["w_sz"]) / (line["w_sh"] + line["w_sz"])
    return line.sort_values(["day", "pos"]).reset_index(drop=True)


def radar_pairs(avg):
    pairs = []
    for path in sorted(RADAR_DIR.glob("radar_2026-10-*.json")):
        data = json.loads(path.read_text())
        day = pd.to_datetime(data["trade_date"])
        latest = {}
        for point in data["points"]:
            if point.get("phase") != "continuous" or "breadth" not in point:
                continue
            stamp = pd.to_datetime(point["observed_at"], utc=True).tz_convert("Asia/Shanghai")
            minute = stamp.strftime("%H:%M")
            latest[minute] = point
        for minute, point in latest.items():
            breadth = point["breadth"]
            total = breadth["up"] + breadth["down"] + breadth["flat"]
            pairs.append({"day": day, "minute": minute, "adv_share": breadth["up"] / total})
    radar = pd.DataFrame(pairs)
    merged = avg.merge(radar, on=["day", "minute"], how="inner")
    return merged


def main():
    indexes = {}
    print("CHECKS dispatch=1; fund files intentionally not opened")
    for code in CODES:
        frame, duplicate, duplicate_labels, duplicate_value_disagree, duplicate_volume_diff_days = load_index(code)
        raw = sum(1 for _ in (INDEX_DIR / f"{code}.jsonl").open())
        frame["day"] = pd.to_datetime(frame["day"], format="%Y%m%d")
        preclose = []
        with (INDEX_DIR / f"{code}.jsonl").open() as handle:
            for line in handle:
                item = json.loads(line)
                preclose.append({"day": pd.to_datetime(item["day"], format="%Y%m%d"), "preclose": item["preclose"]})
        frame = frame.merge(pd.DataFrame(preclose), on="day", how="left")
        indexes[code] = frame
        minutes = frame.groupby("day").size()
        print(
            f"sessions {code} n={raw} range={frame.day.min():%Y-%m-%d}..{frame.day.max():%Y-%m-%d} "
            f"minutes_min={minutes.min()} minutes_max={minutes.max()} duplicate_minutes={duplicate}"
        )
        if duplicate:
            labels = ",".join(f"{label}:{count}" for label, count in sorted(duplicate_labels.items()))
            print(
                f"duplicate_detail {code} labels={labels} value_disagree={duplicate_value_disagree} "
                f"volume_diff_sessions={duplicate_volume_diff_days} kept=first"
            )
    missing = sorted(set(indexes["SH000001"].day.unique()) - set(indexes["SZ399001"].day.unique()))
    print("SZ399001 missing_vs_SH000001", ",".join(pd.Timestamp(x).strftime("%Y-%m-%d") for x in missing))

    daily = pd.read_parquet(DAILY_PATH, columns=["symbol", "trading_date", "low", "pre_close", "ret"])
    daily["trading_date"] = pd.to_datetime(daily["trading_date"])
    avg = build_avg(indexes, daily)
    close = avg.groupby("day").tail(1)[["day", "avg"]].rename(columns={"avg": "avg_close"})
    panel = daily.groupby("trading_date")["ret"].mean().rename("panel_mean_ret").reset_index()
    validation = close.merge(panel, left_on="day", right_on="trading_date", how="inner")
    print(
        f"avg_close_vs_daily_panel n={len(validation)} corr={validation.avg_close.corr(validation.panel_mean_ret):.6f} "
        f"mae={np.abs(validation.avg_close-validation.panel_mean_ret).mean():.6f}"
    )
    panel_by_day = panel.set_index("trading_date")["panel_mean_ret"]
    avg_by_day = close.set_index("day")["avg_close"]
    common_days = sorted(set(avg_by_day.index) & set(panel_by_day.index))
    two_day = pd.DataFrame(
        {
            "avg_two_day": [
                (1 + avg_by_day[common_days[i]]) * (1 + avg_by_day[common_days[i + 1]]) - 1
                for i in range(len(common_days) - 1)
            ],
            "panel_two_day": [
                (1 + panel_by_day[common_days[i]]) * (1 + panel_by_day[common_days[i + 1]]) - 1
                for i in range(len(common_days) - 1)
            ],
        }
    )
    print(
        f"avg_two_day_compound_vs_panel n={len(two_day)} corr={two_day.avg_two_day.corr(two_day.panel_two_day):.6f} "
        f"mae={np.abs(two_day.avg_two_day-two_day.panel_two_day).mean():.6f}"
    )
    event_day = pd.Timestamp("2026-07-09")
    event_depth = -0.015
    event_line = avg[avg.day == event_day].sort_values("pos").reset_index(drop=True)
    event_index = int(np.flatnonzero(event_line.avg.to_numpy() <= event_depth)[0])
    event_e = event_line.avg.iloc[event_index + 1]
    event_c = event_line.avg.iloc[-1]
    next_days = sorted(day for day in avg_by_day.index if day > event_day)
    event_next = next_days[0]
    event_o1 = avg[avg.day == event_next].sort_values("pos").avg.iloc[0]
    event_c1 = avg[avg.day == event_next].sort_values("pos").avg.iloc[-1]
    event_return = (1 + event_c) / (1 + event_e) * (1 + event_c1) - 1
    print(
        f"event_example day=2026-07-09 depth=-1.50% entry={event_line.minute.iloc[event_index]} "
        f"e={event_e:.6f} c={event_c:.6f} next_day={event_next:%Y-%m-%d} o1={event_o1:.6f} c1={event_c1:.6f} "
        f"out_avg_to_next_close={event_return:.6f}"
    )
    radar = radar_pairs(avg)
    print(
        f"avg_vs_radar_advancers n={len(radar)} sessions={radar.day.nunique()} "
        f"corr={radar.avg.corr(radar.adv_share):.6f}"
    )
    avg_day = avg.groupby("day")["avg"].agg(["min", "max", "last"]).reset_index()
    med = daily.assign(stock_ret_low=daily["low"] / daily["pre_close"] - 1).groupby("trading_date")["stock_ret_low"].median()
    print(f"daily_definition_deep_low n={int((med <= -0.02).sum())} sessions={len(med)} threshold=-2.00%")
    for depth in (-0.015, -0.02, -0.03):
        n = int((avg_day["min"] <= depth).sum())
        print(f"avg_depth_entry depth={depth:.2%} sessions={n} base_daily_193_difference={n-193}")


if __name__ == "__main__":
    main()
