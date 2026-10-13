from pathlib import Path

import pandas as pd


DERIVED = Path("/Users/papa/codebase/n8n/state/research/vshape_20261013/derived")
ARCHIVE = Path(__file__).resolve().parent


def main():
    minute = pd.read_parquet(DERIVED / "minute_lines.parquet", columns=["day", "minute", "avg"])
    rows = []
    for day, group in minute.groupby("day", sort=True):
        group = group.sort_values("minute").reset_index(drop=True)
        low_idx = int(group.avg.to_numpy().argmin())
        low = float(group.avg.iloc[low_idx])
        close = float(group.avg.iloc[-1])
        rows.append(
            {
                "day": day,
                "year": day.year,
                "low_minute": group.minute.iloc[low_idx],
                "low_avg": low,
                "close_avg": close,
                "rise_low_to_close": close - low,
                "turned": bool(close - low >= 0.02),
            }
        )
    description = pd.DataFrame(rows)
    deep = description[description.low_avg <= -0.015].copy()
    all_summary = []
    for depth in (-0.015, -0.02, -0.03):
        selected = description[description.low_avg <= depth]
        by_year = (
            selected.groupby("year", sort=True)
            .agg(
                sessions=("day", "size"),
                turned_sessions=("turned", "sum"),
                turn_share=("turned", "mean"),
                median_low_minute=("low_minute", lambda x: x.sort_values().iloc[len(x) // 2]),
                median_low=("low_avg", "median"),
                median_rise=("rise_low_to_close", "median"),
            )
            .reset_index()
        )
        by_year["depth"] = depth
        all_summary.append(by_year)
    summary = pd.concat(all_summary, ignore_index=True)
    summary.to_csv(ARCHIVE / "deep_low_description.csv", index=False)

    depth_events = pd.read_parquet(DERIVED / "depth_entry_events.parquet")
    rebound_events = pd.read_parquet(DERIVED / "rebound_confirmation_events.parquet")
    cutoff = pd.Timestamp("2026-07-01")
    recent_depth = depth_events[depth_events.day >= cutoff].copy()
    recent_rebound = rebound_events[rebound_events.day >= cutoff].copy()
    keep_depth = [
        "day", "depth", "entry_minute", "entry_avg", "out_entry_next", "out_avg_to_close",
        "out_avg_to_next_open", "out_avg_to_next_close", "out_new_low_before_close",
        "out_rise_to_later_high", "out_fall_high_to_close", "out_fall_high_to_next_open",
    ]
    keep_rebound = [
        "day", "depth", "rebound_size", "confirm_sequence", "confirm_minute", "confirm_avg",
        "out_held_no_new_low", "out_entry_next", "out_avg_to_close", "out_avg_to_next_open",
        "out_avg_to_next_close", "out_new_low_before_close", "out_rise_to_later_high",
        "out_fall_high_to_close", "out_fall_high_to_next_open",
    ]
    recent = pd.concat(
        [recent_depth[keep_depth].assign(event_type="depth_entry", event_minute=recent_depth.entry_minute),
         recent_rebound[keep_rebound].assign(event_type="rebound_confirmation", event_minute=recent_rebound.confirm_minute)],
        ignore_index=True,
        sort=False,
    ).sort_values(["day", "event_minute", "event_type", "depth", "rebound_size"], na_position="last")
    recent.to_csv(ARCHIVE / "recent_events_since_2026-07-01.csv", index=False)

    print("DISPATCH1 deep-low description; fund files intentionally not opened")
    print("description_definition=deep when avg day low <= depth; turned when close_avg-low_avg >= 2 percentage points")
    for depth in (-0.015, -0.02, -0.03):
        selected = description[description.low_avg <= depth]
        turned = int(selected.turned.sum())
        print(f"depth={depth:.2%} sessions={len(selected)} turned={turned} share={selected.turned.mean():.6f}")
    print("by_year")
    print(summary.to_string(index=False))
    print(f"since_2026-07-01 depth_events={len(recent_depth)} rebound_events={len(recent_rebound)}")
    for day, group in recent.sort_values(["day", "event_minute"]).groupby("day", sort=True):
        print(f"{day:%Y-%m-%d}")
        for _, row in group.iterrows():
            if row.event_type == "depth_entry":
                print(
                    f"  depth {row.depth:.2%} at {row.event_minute}; out_close={row.out_avg_to_close:.6f}; "
                    f"out_next_open={row.out_avg_to_next_open:.6f}; out_next_close={row.out_avg_to_next_close:.6f}; "
                    f"out_new_low={row.out_new_low_before_close}"
                )
            else:
                print(
                    f"  rebound {row.rebound_size:.2%} at {row.event_minute}; held={row.out_held_no_new_low}; "
                    f"out_close={row.out_avg_to_close:.6f}; out_next_open={row.out_avg_to_next_open:.6f}; "
                    f"out_next_close={row.out_avg_to_next_close:.6f}; out_new_low={row.out_new_low_before_close}"
                )


if __name__ == "__main__":
    main()
