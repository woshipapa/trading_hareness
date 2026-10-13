"""Read-only probe on the owner: today's TDX whole-market shadow snapshots beside the main source, and their agreement.
Run inside the owner's main service container (see the README of docs/work for the calling convention)."""
import json, statistics
from datetime import datetime, timedelta, timezone
from app.database import Database
CN = timezone(timedelta(hours=8))
day = datetime(2026, 10, 13, tzinfo=CN)
start, end = day, day + timedelta(days=1)
db = Database(); db.open()
def q(sql, params):
    with db.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '60s'")
        return [dict(r) for r in c.execute(sql, params).fetchall()]
print("[whole-market minute documents today, by provider and capability]")
for r in q("""SELECT provider_key, capability, count(*) n, min(effective_at) first, max(effective_at) last
                FROM quant.raw_market_observations
               WHERE symbol IS NULL AND effective_at >= %s AND effective_at < %s AND capability LIKE 'a_share_minute_cross_section%%'
               GROUP BY 1,2 ORDER BY 2,1""", (start, end)):
    print(" ", r["provider_key"], r["capability"], "docs", r["n"], r["first"].astimezone(CN).strftime("%H:%M:%S"), "..", r["last"].astimezone(CN).strftime("%H:%M:%S"))
rows = q("""SELECT effective_at, normalized FROM quant.raw_market_observations
             WHERE provider_key='local_derived' AND capability='all_a_snapshot_source_agreement' AND symbol IS NULL
               AND effective_at >= %s AND effective_at < %s ORDER BY effective_at""", (start, end))
payloads = [r["normalized"] if isinstance(r["normalized"], dict) else json.loads(r["normalized"]) for r in rows]
compared = [p["comparison"] for p in payloads if p.get("status") == "compared"]
print("[agreement] rows", len(payloads), "compared", len(compared), "no_reference", sum(p.get("status") == "no_reference" for p in payloads))
if compared:
    print("  comparison keys:", sorted(compared[-1].keys()))
    print("  price keys:", sorted(compared[-1]["price"].keys()))
    def vals(*path):
        out = []
        for c in compared:
            v = c
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                out.append(float(v))
        return out
    for label, path in (("within one tick", ("price", "within_one_tick")), ("within 0.1%", ("price", "within_0_1_pct")), ("within 0.5%", ("price", "within_0_5_pct")),
                        ("volume ratio median", ("volume", "median_ratio")), ("turnover ratio median", ("turnover", "median_ratio")), ("seconds apart", ("seconds_apart",))):
        v = vals(*path)
        if v:
            print(f"  {label}: median {statistics.median(v):.4f}  min {min(v):.4f}  max {max(v):.4f}  n {len(v)}")
    last = compared[-1]
    print("  last comparison:", json.dumps({k: v for k, v in last.items() if not isinstance(v, dict)}, ensure_ascii=False)[:300])
    for k in ("symbols", "coverage", "counts"):
        if k in last:
            print(" ", k, json.dumps(last[k], ensure_ascii=False)[:300])
