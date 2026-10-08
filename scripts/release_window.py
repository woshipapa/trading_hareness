#!/usr/bin/env python3
"""May these runtime surfaces be restarted now?

    release_window.py check <surface> [<surface> ...]   exit 0 if yes, 3 if a window forbids it
    release_window.py list                             print the windows

The windows are data, in config/release-windows.json. RELEASE_CLOCK="<1-7> <HHMM>"
pins the clock (ISO weekday, Beijing time) for tests. RELEASE_WINDOW_OVERRIDE set
to a window's name lets that one window pass with a warning - for an approved
emergency, never as a default. Standard library only: it runs under the
system python3 on every host.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "config" / "release-windows.json"


def load(path: Path = CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clock(config: dict, pinned: str | None = None) -> tuple[int, int]:
    """(ISO weekday, minutes after midnight) in the configured zone."""
    if pinned:
        weekday, hhmm = pinned.split()
        return int(weekday), int(hhmm[:2]) * 60 + int(hhmm[2:])
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=config["utc_offset_hours"])))
    return now.isoweekday(), now.hour * 60 + now.minute


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def blocking_windows(config: dict, surfaces: list[str], weekday: int, minute: int) -> list[dict]:
    known = {surface for window in config["windows"] for surface in window["surfaces"]}
    unknown = sorted(set(surfaces) - known)
    if unknown:
        raise ValueError(f"unknown surface(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    if weekday > 5:
        return []
    return [window for window in config["windows"]
            if set(surfaces) & set(window["surfaces"]) and _minutes(window["from"]) <= minute <= _minutes(window["to"])]


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"check", "list"} or (argv[0] == "check" and len(argv) < 2):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    config = load()
    if argv[0] == "list":
        for window in config["windows"]:
            print(f"{window['name']}: weekdays {window['from']}-{window['to']} Beijing: {', '.join(window['surfaces'])}")
        return 0
    surfaces = argv[1:]
    weekday, minute = clock(config, os.environ.get("RELEASE_CLOCK"))
    try:
        blocking = blocking_windows(config, surfaces, weekday, minute)
    except ValueError as error:
        print(f"release window: {error}", file=sys.stderr)
        return 2
    override = os.environ.get("RELEASE_WINDOW_OVERRIDE", "")
    now = f"{minute // 60:02d}:{minute % 60:02d}"
    refused = []
    for window in blocking:
        if window["name"] == override:
            print(f"WARNING: restarting {', '.join(surfaces)} at Beijing {now} inside the {window['name']} window "
                  f"({window['from']}-{window['to']}) because RELEASE_WINDOW_OVERRIDE names it", file=sys.stderr)
        else:
            refused.append(window)
    for window in refused:
        print(f"refusing: Beijing {now} on a weekday is inside a no-restart window: {window['name']} "
              f"{window['from']}-{window['to']} ({window['why']})", file=sys.stderr)
    return 3 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
