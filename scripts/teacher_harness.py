"""The video harness as the teacher cycle uses it: one declared port.

The teacher cycle orchestrates the video harness, a separate repository: owner
health, pack building with its agent, chip evidence, the method library, and
two supplementary checks. Each call site used to put the harness on sys.path
and import what it needed, so nothing said which harness functions this
repository depends on, and a renamed one surfaced as an ImportError halfway
through a nightly run.

CONTRACT is that list. ``load`` imports one harness module and checks its
declared names; ``contract_problems`` checks them all up front. The trading
calendar no longer comes from the harness: it was exchange_calendars' XSHG all
along, so it is read here directly.
"""
from __future__ import annotations

import datetime as dt
import importlib
import os
import pathlib
import sys
from types import ModuleType

HARNESS_DIR = pathlib.Path(os.environ.get("VIDEO_HARNESS_DIR", "/Users/papa/codebase/video_understanding_harness"))

# harness module -> the names this repository calls
CONTRACT: dict[str, tuple[str, ...]] = {
    "owner_universe": ("owner_health",),
    "agent_dispatch": ("_load_provider",),
    "teacher_pack_agent": ("build_pack",),
    "chip_distribution": ("build_chip_evidence",),
    "teacher_method_library": ("build", "markdown"),
    "teacher_board": ("dropped_bound_stocks",),
    "teacher_owner_overlap": ("compare",),
}


class HarnessUnavailable(RuntimeError):
    """The harness checkout is missing, or no longer provides a declared name."""


def _put_first_on_path(directory: pathlib.Path) -> None:
    if not directory.is_dir():
        raise HarnessUnavailable(f"video harness not found at {directory} (set VIDEO_HARNESS_DIR)")
    entry = str(directory)
    if sys.path[:1] != [entry]:
        while entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)


def load(module: str, directory: pathlib.Path | None = None) -> ModuleType:
    """Import one declared harness module and check the names this repository uses."""
    names = CONTRACT.get(module)
    if names is None:
        raise KeyError(f"{module} is not in the declared harness contract")
    _put_first_on_path(directory or HARNESS_DIR)
    try:
        loaded = importlib.import_module(module)
    except ImportError as error:
        raise HarnessUnavailable(f"harness module {module} cannot be imported: {error}") from error
    missing = [name for name in names if not hasattr(loaded, name)]
    if missing:
        raise HarnessUnavailable(f"harness module {module} no longer provides {', '.join(missing)}")
    return loaded


def contract_problems(directory: pathlib.Path | None = None) -> list[str]:
    """Every way the harness checkout falls short of CONTRACT; empty when it is complete."""
    problems = []
    for module in CONTRACT:
        try:
            load(module, directory)
        except HarnessUnavailable as error:
            problems.append(str(error))
    return problems


def is_trading_day(day: dt.date) -> bool:
    """Shanghai exchange sessions (XSHG); plain weekdays when the calendar is unavailable."""
    try:
        import exchange_calendars as xc  # noqa: PLC0415 - heavy, imported only when asked
        return bool(xc.get_calendar("XSHG").is_session(day.isoformat()))
    except Exception:  # noqa: BLE001 - not installed, or outside the calendar's range
        return day.weekday() < 5
