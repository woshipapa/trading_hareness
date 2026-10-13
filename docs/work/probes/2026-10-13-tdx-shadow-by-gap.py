"""Read-only probe on the owner: TDX against the main snapshot by the time gap between the two captures, and each source's missing minutes."""
import json, statistics
from datetime import datetime, timedelta, timezone
from app.database import Database
CN = timezone(timedelta(hours=8))
day = datetime(2026, 10, 13, tzinfo=CN)
db = Database(); db.open()
def q(sql, params):
    with db.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '60s'")
        return [dict(r) for r in c.execute(sql, params).fetchall()]
rows = q("""SELECT effective_at, normalized FROM quant.raw_market_observations
             WHERE provider_key='local_derived' AND capability='all_a_snapshot_source_agreement' AND symbol IS NULL
               AND effective_at >= %s AND effective_at < %s ORDER BY effective_at""", (day, day + timedelta(days=1)))
items = []
for r in rows:
    p = r["normalized"] if isinstance(r["normalized"], dict) else json.loads(r["normalized"])
    if p.get("status") != "compared":
        continue
    t = r["effective_at"].astimezone(CN)
    c = p["comparison"]
    items.append((t, c["seconds_apart"], c["price"].get("within_one_tick"), c["price"].get("within_0_5_pct"), c["symbols_in_both"], len(c.get("only_in_tdx") or []), len(c.get("only_in_fuyao") or []), c["price"].get("largest_relative_gap")))
trading = [i for i in items if (i[0].strftime("%H:%M") >= "09:30" and i[0].strftime("%H:%M") < "11:30") or i[0].strftime("%H:%M") >= "13:00"]
print("compared", len(items), "in continuous trading", len(trading))
for label, lo, hi in (("under 5 s", 0, 5), ("5-15 s", 5, 15), ("15-30 s", 15, 30), ("30 s and more", 30, 999)):
    sub = [i for i in trading if lo <= i[1] < hi and i[2] is not None]
    if sub:
        print(f"  gap {label}: n {len(sub)}  within one tick median {statistics.median(i[2] for i in sub):.3f} min {min(i[2] for i in sub):.3f}  within 0.5% median {statistics.median(i[3] for i in sub):.3f} min {min(i[3] for i in sub):.3f}")
print("  symbols in both: min", min(i[4] for i in items), "max", max(i[4] for i in items), "| only in TDX max", max(i[5] for i in items), "| only in main max", max(i[6] for i in items))
gaps = sorted((i[7], i[0].strftime("%H:%M")) for i in trading if i[7] is not None)[-3:]
print("  largest relative price gaps:", [(round(g, 4), t) for g, t in gaps])
for provider, capability in (("fuyao_ths", "a_share_minute_cross_section"), ("tdx_public", "a_share_minute_cross_section_shadow")):
    got = {r["effective_at"].astimezone(CN).strftime("%H:%M") for r in q(
        "SELECT effective_at FROM quant.raw_market_observations WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at < %s",
        (provider, capability, day, day + timedelta(days=1)))}
    want = [f"{h:02d}:{m:02d}" for h in range(9, 14) for m in range(60) if "09:15" <= f"{h:02d}:{m:02d}" <= "11:30" or "13:00" <= f"{h:02d}:{m:02d}" <= "13:36"]
    missing = [t for t in want if t not in got]
    print(f"{provider}: minutes with a document {len(got)}; missing of {len(want)} expected (09:15-11:30, 13:00-13:36): {len(missing)} {missing[:14]}")
