"""Export the stored daily sentiment temperature (decision 0013) from the owner to a local file (read-only).

Two requests to the owner read path http://127.0.0.1:15682 (SSH forward; not the local :5681 service), 400 sessions
each, the route's maximum. Output: state/research/vshape_20261013/owner/temperature_daily.json (machine-local).
Run: /Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-temperature-export.py
"""
import json
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "state/research/vshape_20261013/owner/temperature_daily.json"
CN = ZoneInfo("Asia/Shanghai")


def read(end):
    url = "http://127.0.0.1:15682/api/v1/market/temperature/daily?days=400" + (f"&end={end}" if end else "")
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


first = read(None)
key = next(name for name, value in first.items() if isinstance(value, list) and value and isinstance(value[0], dict))
rows = list(first[key])
day_key = next(name for name in ("trade_date", "date", "trading_date") if name in rows[0])
older = read((date.fromisoformat(str(rows[0][day_key])[:10]) - timedelta(days=1)).isoformat())[key]
rows = older + rows
OUT.write_text(json.dumps({"exported_at": datetime.now(CN).isoformat(), "source": "owner /api/v1/market/temperature/daily",
                           "other_fields": {name: value for name, value in first.items() if name != key}, "rows": rows},
                          ensure_ascii=False), encoding="utf-8")
print(f"observed {datetime.now(CN):%Y-%m-%d %H:%M:%S} CST | list key {key!r} | day key {day_key!r} | rows {len(rows)} | {rows[0][day_key]} .. {rows[-1][day_key]}")
print("fields of a row:", sorted(rows[-1]))
print("last row:", json.dumps(rows[-1], ensure_ascii=False, default=str)[:700])
print("other fields:", json.dumps({name: value for name, value in first.items() if name != key}, ensure_ascii=False, default=str)[:400])
cold = [row for row in rows if isinstance(row.get("temperature"), (int, float)) and row["temperature"] <= 40]
print(f"rows with a temperature: {sum(1 for row in rows if isinstance(row.get('temperature'), (int, float)))} | at or under 40: {len(cold)}")
