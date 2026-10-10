#!/usr/bin/env python3
"""Log when tipinfo.dat rows appear in zhb.zip during a day and look up the cninfo announcement time of each.

Every --interval minutes zhb.zip is downloaded and the (security, report period, column-4 date) rows of tipinfo.dat
that the poll before did not have are logged with the poll time (the first poll is the baseline: nothing is new
yet). After the last of --polls the app's cninfo reader is asked, for every new row with a YYYYMMDD date and a
quarter-end period, for the announcements of the security from one day before that date to one day after it. It queries by code and dates, not
by period, so each announcement carries whether its title names the period (<year>年 plus the report name); a row
whose lookup failed keeps the error for a manual check. The times of cninfo are what its list gives (a date may be
all it holds); all times are written with the Asia/Shanghai offset. Read-only.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_files, tdx_protocol, tdx_zhb_extras  # noqa: E402
from app.free_market_providers import FreeProviderError, cninfo_announcements  # noqa: E402
from app.public_provider_rate_limits import configured_rate_limit  # noqa: E402

CN_TZ = ZoneInfo("Asia/Shanghai")
#: The report name that follows "<year>年" in the title of the periodic report of a period end (MMDD).
REPORT_NAMES = {"0331": "第一季度报告", "0630": "半年度报告", "0930": "第三季度报告", "1231": "年度报告"}
DATE = re.compile(r"20\d{6}")
LOOKUP_DAYS = 1


def now():
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def poll():
    """The distinct (symbol, period, column-4 date) rows of tipinfo.dat now, and the host label that served them."""
    members, host = tdx_protocol.call_sync(
        lambda client: tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip")))
    rows = set()
    for row in tdx_zhb_extras.parse_tipinfo(members["tipinfo.dat"]):
        market, code, period, _eps, date, *_ = row["raw_fields"]     # by position: the parser's name for column 4 changes
        rows.add((tdx_protocol.symbol(int(market), code), period, date))
    return rows, host


async def look_up(rows, appeared):
    """Per row, the cninfo announcements around its date; one request at a time, at the reader's rate."""
    found = []
    for symbol, period, date in rows:
        day = datetime.strptime(date, "%Y%m%d").date()
        entry = {"symbol": symbol, "period": period, "date": date, "appeared_between": appeared[symbol, period, date]}
        try:
            announcements = await cninfo_announcements(
                symbol, day - timedelta(days=LOOKUP_DAYS), day + timedelta(days=LOOKUP_DAYS), max_pages=1)
        except FreeProviderError as error:
            entry["lookup_error"] = str(error)
        else:
            title = f"{period[:4]}年{REPORT_NAMES[period[4:]]}"
            entry["announcements"] = [
                {"title": item["title"], "matches_period": title in item["title"],
                 "published_at": datetime.fromisoformat(item["published_at"]).astimezone(CN_TZ).isoformat()}
                for item in announcements]
        found.append(entry)
        await asyncio.sleep(60 / configured_rate_limit("cninfo_free"))
    return found


def emit(payload, output):
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interval", type=float, default=10.0, help="minutes between the starts of two polls")
    parser.add_argument("--polls", type=int, required=True)
    parser.add_argument("--output", type=Path, help="write the JSON here instead of stdout; rewritten after every poll")
    args = parser.parse_args(argv)
    polls, appeared = [], {}
    payload = {"schema": "tdx-disclosure-timing-v1", "interval_minutes": args.interval, "polls": polls,
               "lookup": {"source": "cninfo through app.free_market_providers.cninfo_announcements",
                          "window_days_either_side_of_the_date": LOOKUP_DAYS, "time_zone": "Asia/Shanghai"}}
    previous, previous_at = None, None
    began = time.monotonic()
    for number in range(args.polls):
        time.sleep(max(0.0, began + number * args.interval * 60 - time.monotonic()))
        rows, host = poll()
        polled_at = now()
        new = None if previous is None else sorted(rows - previous)
        for row in new or ():
            appeared.setdefault(row, [previous_at, polled_at])
        polls.append({"polled_at": polled_at, "host": host, "rows": len(rows),
                      "new": None if new is None else [dict(zip(("symbol", "period", "date"), row)) for row in new]})
        previous, previous_at = rows, polled_at
        print(f"poll {number + 1}/{args.polls} {polled_at} {host}: {len(rows)} rows, "
              f"{len(new) if new is not None else 'no'} new", file=sys.stderr)
        if args.output:
            emit(payload, args.output)
    dated = [(symbol, period, date) for symbol, period, date in sorted(appeared)
             if DATE.fullmatch(date) and period[4:] in REPORT_NAMES]
    payload["lookups"] = asyncio.run(look_up(dated, appeared))
    emit(payload, args.output)
    failed = sum("lookup_error" in entry for entry in payload["lookups"])
    print(f"{len(appeared)} new rows, {len(dated)} looked up on cninfo, {failed} lookups failed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
