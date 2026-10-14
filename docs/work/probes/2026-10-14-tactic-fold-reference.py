"""Run at b04be9b9 (the code before the fold): the history-dependent results on the deterministic days.
Usage: PYTHONPATH=<quant-service of b04be9b9, with tests/tactic_trial_days.py beside app/> python make_golden.py <out.json>"""
import json, sys
from tactic_trial_days import fingerprint, record_outcomes, rounded, settled_days
from app.tactic_trials import TACTICS, build_pairs, build_records, build_sell_points, build_switch_records

days = settled_days()
records = build_records(days)
kept = {tactic.key: {outcome: value for outcome, value in records[tactic.key]["outcomes"].items()
                     if record_outcomes(tactic) is None or outcome in record_outcomes(tactic)} for tactic in TACTICS}
sell = build_sell_points(days)
result = {
    "source": "quant-service/app/tactic_trials.py at b04be9b9 on tests/tactic_trial_days.settled_days(); made by "
              "docs/work/probes/2026-10-14-tactic-fold-reference.py",
    "days": len(days),
    "primary": {tactic.key: {"label": records[tactic.key]["label"], "q": records[tactic.key]["q"],
                             **records[tactic.key]["outcomes"][tactic.primary]} for tactic in TACTICS},
    "records_sha256": fingerprint(kept),
    "picking_combinations": sum(1 for tactic in TACTICS if not tactic.key.startswith("only.")
                                and any(records[tactic.key]["outcomes"][tactic.primary][period]["picks"] for period in ("replay", "forward"))),
    "switch_records": build_switch_records(days),
    "sell_cumulative_all": {slot: sell[slot]["cumulative"]["all"] for slot in sell},
    "sell_points_sha256": fingerprint(sell),
    "pairs": build_pairs(days),
}
json.dump(rounded(result, 12), open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, sort_keys=True, indent=1)
print("written", len(days), "days; combinations with picks:", result["picking_combinations"],
      "; labels:", sorted({value["label"] for value in result["primary"].values()}))
