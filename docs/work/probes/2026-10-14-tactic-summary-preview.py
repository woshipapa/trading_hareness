"""W006: the daily summary the evening run would store, computed now for the newest session with readings, without
storing anything (summarise with apply=False; the summary is caught on its way out). Read-only.
Run inside the owner's quant-research container, stdin = this file."""
import json, sys
from datetime import date

from app import tactic_trial_runtime as runtime
from app.database import Database

DAY = date(2026, 10, 13)
caught = {}
original = runtime.daily_summary


def catching(*args, **kwargs):
    caught["summary"] = original(*args, **kwargs)
    return caught["summary"]


runtime.daily_summary = catching
database = Database()
database.open()
result = runtime.summarise(database, DAY, apply=False)
summary = caught["summary"]
print(json.dumps({"result": result["summary"], "status": result["status"]}, ensure_ascii=False))
print("=== lines")
for line in summary["lines"]:
    print(line)
print("=== records (combinations)")
for key, record in summary["records"].items():
    print(json.dumps({"key": key, **record}, ensure_ascii=False))
