"""The post-close stage receipts of one trade date, from the owner's automation-runs route (read-only).

Run: /Users/papa/.venvs/svc/bin/python -I docs/work/probes/2026-10-13-post-close-stages.py [YYYY-MM-DD]
Target: the owner read path http://127.0.0.1:15682 (SSH forward), not the local :5681 service.
"""
import json
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(CN).date().isoformat()
with urllib.request.urlopen("http://127.0.0.1:15682/api/v1/automation/runs?task_key=post_close_refresh.stage&limit=100", timeout=60) as response:
    items = json.load(response)["items"]
print(f"observed {datetime.now(CN):%Y-%m-%d %H:%M:%S} CST | receipts returned {len(items)} | trade date {day}")
latest = {}
for item in items:
    if str(item.get("as_of_date"))[:10] != day:
        continue
    stage = (item.get("input_summary") or {}).get("stage") or item.get("run_key")
    if stage not in latest:
        latest[stage] = item
for stage, item in sorted(latest.items(), key=lambda pair: str(pair[1].get("started_at"))):
    started = datetime.fromisoformat(item["started_at"]).astimezone(CN) if item.get("started_at") else None
    finished = datetime.fromisoformat(item["finished_at"]).astimezone(CN) if item.get("finished_at") else None
    seconds = f"{(finished - started).total_seconds():.0f}s" if started and finished else "-"
    output = item.get("output_summary") or {}
    detail = {key: output[key] for key in ("status", "reason", "written", "rows", "stored", "missing", "blocked_by") if key in output}
    if isinstance(detail.get("missing"), list):
        detail["missing"] = len(detail["missing"])
    print(f"  {started:%H:%M:%S} {seconds:>5} {item.get('status'):<10} {stage:<34} {json.dumps(detail, ensure_ascii=False, default=str)[:150]}"
          + (f" | {item.get('error_class')}: {str(item.get('error_message'))[:90]}" if item.get("error_class") else ""))
print(f"stages with a receipt for {day}: {len(latest)}")
