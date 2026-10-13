"""Read-only probe on the owner (W008: the completeness gate and the in-session agreement tolerance), for 2026-10-13.
The agreement rows record `seconds_apart` between the two captures' start times. The main document is often a cached
snapshot (its metadata gives the cache age and the upstream timestamp) and the TDX read returns some seconds after its
capture started, so this probe recomputes for every agreement row the gap between the two DATA times:
    main data time = main capture time - cache age;  TDX data time = when the TDX read returned
and gives the agreement distribution by that gap, over complete snapshots whose two data times lie in the same
continuous-trading half-day. Documents are read through the small `payload` column, agreement rows through JSON paths.
Run inside the owner's main service container (calling convention: W008 record, "Resume here")."""
import json, re, statistics
from datetime import datetime, timedelta, timezone
from app.database import Database
CN = timezone(timedelta(hours=8))
day = datetime(2026, 10, 13, tzinfo=CN)
db = Database(); db.open()
def q(sql, params):
    with db.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '20s'")
        return [dict(r) for r in c.execute(sql, params).fetchall()]
def hms(t): return t.astimezone(CN).strftime("%H:%M:%S")
def js(v): return v if isinstance(v, (dict, list)) or v is None else json.loads(v)
def phase(t):
    s = hms(t)
    return ("call auction" if s < "09:25:00" else "pre-open" if s < "09:30:00" else "am" if s <= "11:30:00"
            else "lunch" if s < "13:00:00" else "pm" if s <= "15:00:00" else "closed")
print("probe ran at", datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S"), "Asia/Shanghai")
docs = {}
for provider, capability in (("fuyao_ths", "a_share_minute_cross_section"), ("tdx_public", "a_share_minute_cross_section_shadow")):
    docs[provider] = q("""SELECT effective_at, (payload->>'rows')::int n, payload->'metadata' metadata
                            FROM quant.raw_market_observations
                           WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at < %s
                           ORDER BY effective_at""", (provider, capability, day, day + timedelta(days=1)))
    one = q("""SELECT octet_length(normalized::text) text_bytes, pg_column_size(normalized) stored_bytes FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at = %s""", (provider, capability, docs[provider][-1]["effective_at"]))[0]
    print(f"[{provider}] documents {len(docs[provider])}; the last one is {one['text_bytes']/1e6:.2f} MB as JSON text, {one['stored_bytes']/1e3:.0f} KB as stored")
main = {}
for r in docs["fuyao_ths"]:
    m = js(r["metadata"]) or {}
    age = float(m.get("age_seconds") or 0.0)
    main[r["effective_at"]] = {"n": r["n"], "status": m.get("status"), "age": age, "fetched": r["effective_at"] - timedelta(seconds=age),
                               "upstream": datetime.fromisoformat(m["upstream_observed_at"]) if m.get("upstream_observed_at") else None,
                               "total": m.get("total")}
tdx = {}
for r in docs["tdx_public"]:
    m = js(r["metadata"]) or {}
    no_trade = next((int(x.group(1)) for w in (m.get("warnings") or []) if (x := re.match(r"no_trade_rows=(\d+)", str(w)))), 0)
    tdx[r["effective_at"]] = {"n": r["n"], "no_trade": no_trade, "returned": datetime.fromisoformat(str(m["available_at"]).replace(" ", "T"))}
print("[completeness of the TDX read: priced rows / rows served (priced + no_trade_rows from the adapter's warning)]")
for name in ("call auction", "pre-open", "am", "pm"):
    items = [v for t, v in tdx.items() if phase(t) == name]
    if items:
        shares = sorted(v["n"] / (v["n"] + v["no_trade"]) if v["n"] + v["no_trade"] else 0.0 for v in items)
        served = sorted(v["n"] + v["no_trade"] for v in items)
        print(f"  {name}: documents {len(items)}  priced share min {shares[0]:.4f} median {statistics.median(shares):.4f} max {shares[-1]:.4f}  rows served min {served[0]} max {served[-1]}  priced rows min {min(v['n'] for v in items)}")
for name in ("call auction", "pre-open", "am", "pm"):
    items = [v for t, v in main.items() if phase(t) == name]
    if items:
        print(f"  main source, {name}: documents {len(items)}  rows min {min(v['n'] for v in items)} max {max(v['n'] for v in items)}  upstream total {sorted({v['total'] for v in items})}")
print("  read duration (capture start to read returned), seconds:", (lambda d: f"median {statistics.median(d):.2f} p90 {d[int(.9*len(d))]:.2f} max {d[-1]:.2f}")(sorted((v["returned"] - t).total_seconds() for t, v in tdx.items())))
agreement = q("""SELECT effective_at, normalized->>'status' status, normalized->>'reference_observed_at' reference_at,
                        (normalized->'comparison'->>'symbols_in_both')::int both_n,
                        (normalized->'comparison'->>'seconds_apart')::float recorded,
                        (normalized->'comparison'->'price'->>'within_one_tick')::float tick,
                        (normalized->'comparison'->'price'->>'within_0_1_pct')::float p01,
                        (normalized->'comparison'->'price'->>'within_0_5_pct')::float p05,
                        (normalized->'comparison'->'price'->>'largest_relative_gap')::float largest,
                        (normalized->'comparison'->'volume'->>'within_1_pct')::float vol1,
                        (normalized->'comparison'->'turnover'->>'within_1_pct')::float amt1
                   FROM quant.raw_market_observations
                  WHERE provider_key='local_derived' AND capability='all_a_snapshot_source_agreement' AND symbol IS NULL
                    AND effective_at >= %s AND effective_at < %s ORDER BY effective_at""", (day, day + timedelta(days=1)))
rows, skipped = [], {"not compared": 0, "reference document not found": 0, "incomplete": 0, "data times not in the same continuous half-day": 0}
for a in agreement:
    if a["status"] != "compared":
        skipped["not compared"] += 1; continue
    reference = main.get(datetime.fromisoformat(a["reference_at"]))
    shadow = tdx[a["effective_at"]]
    if reference is None:
        skipped["reference document not found"] += 1; continue
    served = shadow["n"] + shadow["no_trade"]
    if not served or shadow["n"] / served < 0.95 or shadow["n"] < 0.95 * reference["n"]:
        skipped["incomplete"] += 1; continue
    if phase(reference["fetched"]) != phase(shadow["returned"]) or phase(shadow["returned"]) not in ("am", "pm"):
        skipped["data times not in the same continuous half-day"] += 1; continue
    rows.append({**a, "gap": (shadow["returned"] - reference["fetched"]).total_seconds(),
                 "gap_upstream": (shadow["returned"] - reference["upstream"]).total_seconds() if reference["upstream"] else None,
                 "age": reference["age"], "cache": reference["status"]})
print("[agreement rows]", len(agreement), "| judged (complete, both data times in the same continuous half-day):", len(rows), "| left out:", skipped)
g = sorted(abs(r["gap"]) for r in rows); rec = sorted(r["recorded"] for r in rows)
print(f"  data gap, seconds: median {statistics.median(g):.1f} p90 {g[int(.9*len(g))]:.1f} max {g[-1]:.1f} | as recorded in the rows (capture starts): median {statistics.median(rec):.1f} p90 {rec[int(.9*len(rec))]:.1f} max {rec[-1]:.1f}")
print("  TDX data later than the main data in", sum(r["gap"] > 0 for r in rows), "of", len(rows), "rows | main document was a cached snapshot in", sum(r["cache"] == "cached" for r in rows))
up = sorted(r["gap_upstream"] - r["gap"] for r in rows if r["gap_upstream"] is not None)
print(f"  main fetch time minus its upstream timestamp, seconds: median {statistics.median(up):.1f} p90 {up[int(.9*len(up))]:.1f} max {up[-1]:.1f}")
def stats(sub, key):
    v = sorted(r[key] for r in sub if r[key] is not None)
    return f"median {statistics.median(v):.4f} p10 {v[int(.1*len(v))]:.4f} min {v[0]:.4f}"
def line(label, sub):
    if not sub:
        print(f"  {label}: n 0"); return
    print(f"  {label}: n {len(sub)} | within 0.5%: {stats(sub, 'p05')} | within 0.1%: {stats(sub, 'p01')} | one tick: {stats(sub, 'tick')} | volume within 1%: {stats(sub, 'vol1')} | turnover within 1%: {stats(sub, 'amt1')} | largest single gap max {max(r['largest'] for r in sub):.4f}")
print("[agreement by DATA gap]")
for label, lo, hi in (("under 5 s", 0, 5), ("5-10 s", 5, 10), ("10-15 s", 10, 15), ("15-20 s", 15, 20), ("20-30 s", 20, 30), ("30-45 s", 30, 45), ("45-60 s", 45, 60), ("60 s and more", 60, 9999)):
    line("data gap " + label, [r for r in rows if lo <= abs(r["gap"]) < hi])
line("data gap up to 15 s", [r for r in rows if abs(r["gap"]) <= 15])
line("data gap up to 20 s", [r for r in rows if abs(r["gap"]) <= 20])
line("any data gap", rows)
print("[share of judged minutes that satisfy 'at least X of the common symbols within 0.5%', by the largest data gap allowed]")
for limit in (5, 10, 15, 20, 30, 9999):
    sub = [r for r in rows if abs(r["gap"]) <= limit]
    if sub:
        print(f"  data gap <= {limit if limit < 9999 else 'any'} s: n {len(sub)} | " + " | ".join(f"X={x}: {sum(r['p05'] >= x for r in sub)}/{len(sub)} = {sum(r['p05'] >= x for r in sub)/len(sub):.3f}" for x in (0.90, 0.93, 0.95, 0.97, 0.98)))
print("[the same, excluding the first 15 minutes of each half-day (09:30-09:45, 13:00-13:15)]")
calm = [r for r in rows if not ("09:30:00" <= hms(r["effective_at"]) < "09:45:00" or "13:00:00" <= hms(r["effective_at"]) < "13:15:00")]
for limit in (5, 10, 15, 20, 30):
    sub = [r for r in calm if abs(r["gap"]) <= limit]
    if sub:
        print(f"  data gap <= {limit} s: n {len(sub)} | " + " | ".join(f"X={x}: {sum(r['p05'] >= x for r in sub)}/{len(sub)} = {sum(r['p05'] >= x for r in sub)/len(sub):.3f}" for x in (0.90, 0.93, 0.95, 0.97, 0.98)))
print("[judged minutes with data gap <= 15 s and fewer than 95% of symbols within 0.5%]")
for r in rows:
    if abs(r["gap"]) <= 15 and r["p05"] < 0.95:
        print(f"  {hms(r['effective_at'])} data gap {r['gap']:.1f} s (recorded {r['recorded']:.1f}, main {r['cache']} age {r['age']:.1f} s, against upstream stamp {r['gap_upstream']:.1f} s) within 0.5% {r['p05']:.4f} within 0.1% {r['p01']:.4f} volume within 1% {r['vol1']:.4f} largest {r['largest']:.4f}")
print("[by time of day, data gap <= 15 s]")
for label, lo, hi in (("09:30-09:45", "09:30:00", "09:45:00"), ("09:45-10:30", "09:45:00", "10:30:00"), ("10:30-11:30", "10:30:00", "11:31:00"), ("13:00-13:15", "13:00:00", "13:15:00"), ("13:15-15:00", "13:15:00", "15:01:00")):
    line(label, [r for r in rows if abs(r["gap"]) <= 15 and lo <= hms(r["effective_at"]) < hi])
