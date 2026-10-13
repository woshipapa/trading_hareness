"""Peak memory and time of the evening summary on a synthetic history of 760 sessions x 100 rows, both slots.
phase 'digest': build the digests of the whole history once and write them as JSON lines (what the replay stores).
phase 'fold':   read them back line by line, fold, and build the summary from the last day (what every evening does).
Run each phase as its own process under /usr/bin/time -l."""
import json
import random
import sys
import time
from datetime import date, timedelta

from app.tactic_conditions import CONDITIONS
from app.tactic_exits import EXITS
from app.tactic_trials import History, PairHistory, daily_summary, day_digest, day_pairs

random.seed(7)
PATH = sys.argv[2]
STOCK = {slot: [c.key for c in CONDITIONS if c.level == "stock" and c.slot == slot] for slot in ("close", "auction")}
DAY = [c.key for c in CONDITIONS if c.level == "day" and c.slot == "close"]


def outcome():
    entries = {entry: {exit_.key: {"status": "settled", "raw_return": round(random.gauss(0.002, 0.04), 6),
                                   "net_return": round(random.gauss(0.001, 0.04), 6)} for exit_ in EXITS}
               for entry in ("held", "buy_open")}
    return {"entries": entries, "seal": random.random() < 0.3, "big_loss": random.random() < 0.1,
            "open_to_close": round(random.gauss(0, 0.03), 6), "s1_high": round(random.random() * 0.08, 6),
            "s3_high": round(random.random() * 0.15, 6), "s1_low": -round(random.random() * 0.06, 6)}


def day(day_index, slot):
    rows = []
    for index in range(100):
        flags = [key for key in STOCK[slot] if random.random() < 0.2]
        row = {"symbol": f"{index:06d}.SZ", "name": f"S{index}", "boards": 1 + (index % 4), "board_bucket": str(min(3, 1 + index % 4)),
               "gap_bucket": random.choice(["<0", "0-5", "5+"]), "flags": flags, "unknown": []}
        if slot == "auction":
            row["prev"] = {"flags": [key for key in STOCK["close"] if random.random() < 0.2], "unknown": []}
        rows.append(row)
    return {"trade_date": (date(2023, 9, 1) + timedelta(days=day_index)).isoformat(), "slot": slot,
            "day": {"flags": [key for key in DAY if random.random() < 0.3], "unknown": []},
            "rows": rows, "outcomes": {row["symbol"]: outcome() for row in rows}}


started = time.time()
if sys.argv[1] == "digest":
    sizes = {"digest": 0, "pairs": 0}
    with open(PATH, "w", encoding="utf-8") as handle:
        for index in range(760):
            for slot in ("close", "auction"):
                item = day(index, slot)
                digest, pairs = json.dumps(day_digest(item)), json.dumps(day_pairs(item))
                sizes["digest"] += len(digest); sizes["pairs"] += len(pairs)
                handle.write(digest + "\n" + pairs + "\n")
                if index == 759:
                    with open(PATH + f".last.{slot}", "w", encoding="utf-8") as last:
                        json.dump(item, last)
    print(f"digest phase: {time.time() - started:.1f}s; mean digest {sizes['digest'] / 1520 / 1024:.1f} KB, mean pairs document {sizes['pairs'] / 1520 / 1024:.1f} KB")
else:
    history, pairs = History(), PairHistory()
    with open(PATH, encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            item = json.loads(line)
            if "pairs" in item:
                if number >= (760 - 250) * 4:     # the pairs of the last 250 sessions only, as the runtime does
                    pairs.add(item)
            else:
                history.add(item)
    latest = [json.load(open(PATH + f".last.{slot}", encoding="utf-8")) for slot in ("close", "auction")]
    summary = daily_summary(history, pairs, latest, None)
    print(f"fold phase: {time.time() - started:.1f}s; summary {len(json.dumps(summary, ensure_ascii=False)) / 1024:.0f} KB, {len(summary['lines'])} lines")
