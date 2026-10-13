"""Read-only probe on the owner (W008 prerequisite 2 and the tolerance): on 2026-10-13, which minutes had an incomplete
TDX snapshot, how many rows each TDX document held and what its metadata says; then the agreement distribution over
complete snapshots by capture gap. Documents are read through the small `payload` column (rows, metadata) and the
agreement rows through JSON paths selected in SQL; no document body is selected.
Run inside the owner's main service container (calling convention: W008 record, "Resume here")."""
import json, statistics
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
print("probe ran at", datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S"), "Asia/Shanghai")
docs = {}
for provider, capability in (("fuyao_ths", "a_share_minute_cross_section"), ("tdx_public", "a_share_minute_cross_section_shadow")):
    docs[provider] = q("""SELECT effective_at, (payload->>'rows')::int n, payload->'metadata' metadata
                            FROM quant.raw_market_observations
                           WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at < %s
                           ORDER BY effective_at""", (provider, capability, day, day + timedelta(days=1)))
agreement = q("""SELECT effective_at, normalized->>'status' status, normalized->>'reference_observed_at' reference_at,
                        (normalized->'comparison'->>'symbols_in_both')::int both_n,
                        jsonb_array_length(normalized->'comparison'->'only_in_fuyao') only_main,
                        jsonb_array_length(normalized->'comparison'->'only_in_tdx') only_tdx,
                        (normalized->'comparison'->>'seconds_apart')::float gap,
                        (normalized->'comparison'->'price'->>'within_one_tick')::float tick,
                        (normalized->'comparison'->'price'->>'within_0_1_pct')::float p01,
                        (normalized->'comparison'->'price'->>'within_0_5_pct')::float p05,
                        (normalized->'comparison'->'price'->>'largest_relative_gap')::float largest,
                        normalized->'comparison'->'price'->>'largest_relative_gap_symbol' largest_symbol,
                        (normalized->'comparison'->'pct_change'->>'largest_absolute_gap')::float pct_gap,
                        (normalized->'comparison'->'volume'->>'within_1_pct')::float vol1,
                        (normalized->'comparison'->'volume'->>'median_ratio')::float vol_ratio,
                        (normalized->'comparison'->'turnover'->>'within_1_pct')::float amt1
                   FROM quant.raw_market_observations
                  WHERE provider_key='local_derived' AND capability='all_a_snapshot_source_agreement' AND symbol IS NULL
                    AND effective_at >= %s AND effective_at < %s ORDER BY effective_at""", (day, day + timedelta(days=1)))
tdx_rows = {r["effective_at"]: r for r in docs["tdx_public"]}
main_rows = sorted((r["effective_at"], r["n"]) for r in docs["fuyao_ths"])
def main_count_at(reference_at):
    if not reference_at:
        return None
    t = datetime.fromisoformat(reference_at)
    return next((n for at, n in main_rows if abs((at - t).total_seconds()) < 0.001), None)
print("[rows per document, by phase]")
def phase(t):
    s = t.astimezone(CN).strftime("%H:%M:%S")
    return "call auction 09:15-09:25" if s < "09:25:00" else "09:25-09:30" if s < "09:30:00" else "continuous"
for provider in ("fuyao_ths", "tdx_public"):
    for name in ("call auction 09:15-09:25", "09:25-09:30", "continuous"):
        counts = [r["n"] for r in docs[provider] if phase(r["effective_at"]) == name]
        if counts:
            print(f"  {provider} {name}: documents {len(counts)}  rows min {min(counts)}  median {statistics.median(counts)}  max {max(counts)}")
print("[every TDX document with fewer than 5,000 rows, and the agreement row of that minute]")
by_time = {r["effective_at"]: r for r in agreement}
for r in docs["tdx_public"]:
    if r["n"] < 5000:
        a = by_time.get(r["effective_at"]) or {}
        print(f"  {hms(r['effective_at'])} tdx rows {r['n']}  main rows {main_count_at(a.get('reference_at'))}  in both {a.get('both_n')}  only main {a.get('only_main')}  only tdx {a.get('only_tdx')}  gap {a.get('gap')}  status {a.get('status')}")
print("[agreement rows with fewer than 5,000 symbols in both]")
for a in agreement:
    if a["status"] == "compared" and (a["both_n"] or 0) < 5000:
        t = tdx_rows.get(a["effective_at"]) or {}
        print(f"  {hms(a['effective_at'])} in both {a['both_n']}  only main {a['only_main']}  only tdx {a['only_tdx']}  tdx rows {t.get('n')}  main rows {main_count_at(a['reference_at'])}  gap {a['gap']:.1f}")
print("[TDX document metadata]")
seen = {}
for r in docs["tdx_public"]:
    m = js(r["metadata"]) or {}
    shape = json.dumps({k: (v if k in ("provider", "capability", "source", "research_only", "live_effect") else type(v).__name__) for k, v in sorted(m.items())}, ensure_ascii=False)
    seen.setdefault(shape, []).append(r)
for shape, items in seen.items():
    print(f"  {len(items)} documents: {shape}")
warned = [(hms(r["effective_at"]), r["n"], (js(r["metadata"]) or {}).get("warnings")) for r in docs["tdx_public"] if (js(r["metadata"]) or {}).get("warnings")]
print("  documents with warnings:", len(warned))
for item in warned[:12]:
    print("   ", item[0], "rows", item[1], json.dumps(item[2], ensure_ascii=False)[:300])
small = [r for r in docs["tdx_public"] if r["n"] < 5000]
for r in small[:3] + small[-2:]:
    print("  metadata of the", hms(r["effective_at"]), f"document ({r['n']} rows):", json.dumps(js(r["metadata"]), ensure_ascii=False)[:400])
full = [r for r in docs["tdx_public"] if r["n"] >= 5000]
if full:
    print("  metadata of the", hms(full[-1]["effective_at"]), f"document ({full[-1]['n']} rows):", json.dumps(js(full[-1]["metadata"]), ensure_ascii=False)[:400])
print("[main-source document metadata: is the stored snapshot a cached one?]")
ages, statuses = [], {}
for r in docs["fuyao_ths"]:
    m = js(r["metadata"]) or {}
    statuses[m.get("status")] = statuses.get(m.get("status"), 0) + 1
    if isinstance(m.get("age_seconds"), (int, float)):
        ages.append(float(m["age_seconds"]))
print("  status counts:", statuses, "| age_seconds: median", statistics.median(ages) if ages else None, "max", max(ages) if ages else None, "| over 5 s:", sum(a > 5 for a in ages), "of", len(ages))
print("  keys:", sorted((js(docs["fuyao_ths"][-1]["metadata"]) or {}).keys()))
print("  last:", json.dumps(js(docs["fuyao_ths"][-1]["metadata"]), ensure_ascii=False)[:500])
print("[agreement over complete snapshots in continuous trading (both documents >= 5,000 rows), by capture gap]")
ok = [a for a in agreement if a["status"] == "compared" and (a["both_n"] or 0) >= 5000 and phase(a["effective_at"]) == "continuous"]
print("  compared rows", sum(a["status"] == "compared" for a in agreement), "| complete and in continuous trading", len(ok), "| no_reference", sum(a["status"] == "no_reference" for a in agreement))
def line(label, sub):
    if not sub:
        print(f"  {label}: n 0"); return
    def stats(key):
        v = sorted(a[key] for a in sub if a[key] is not None)
        return f"median {statistics.median(v):.4f} p10 {v[int(.1*len(v))]:.4f} min {v[0]:.4f}"
    print(f"  {label}: n {len(sub)} | within 0.5%: {stats('p05')} | within 0.1%: {stats('p01')} | within one tick: {stats('tick')} | volume within 1%: {stats('vol1')} | largest price gap max {max(a['largest'] for a in sub):.4f}")
for label, lo, hi in (("gap under 5 s", 0, 5), ("gap 5-10 s", 5, 10), ("gap 10-15 s", 10, 15), ("gap 15-30 s", 15, 30), ("gap 30-60 s", 30, 60), ("gap 60 s and more", 60, 999)):
    line(label, [a for a in ok if lo <= a["gap"] < hi])
line("gap under 10 s", [a for a in ok if a["gap"] < 10])
line("gap under 15 s", [a for a in ok if a["gap"] < 15])
line("all", ok)
for label, lo, hi in (("09:30-09:45", "09:30:00", "09:45:00"), ("09:45-11:30", "09:45:00", "11:31:00"), ("13:00 on", "13:00:00", "15:01:00")):
    line(f"gap under 10 s, {label}", [a for a in ok if a["gap"] < 10 and lo <= hms(a["effective_at"]) < hi])
print("  rows with gap under 10 s and within 0.5% below 0.97:", [(hms(a["effective_at"]), round(a["gap"], 1), round(a["p05"], 4)) for a in ok if a["gap"] < 10 and a["p05"] < 0.97])
print("  symbols: in both min", min(a["both_n"] for a in ok), "max", max(a["both_n"] for a in ok), "| only main max", max(a["only_main"] for a in ok), "| only tdx max", max(a["only_tdx"] for a in ok))
print("[the largest single-symbol price gaps of the day, continuous trading]")
for a in sorted((a for a in agreement if a["status"] == "compared" and a["largest"] is not None and phase(a["effective_at"]) == "continuous"), key=lambda a: -a["largest"])[:12]:
    print(f"  {hms(a['effective_at'])} {a['largest_symbol']} gap {a['largest']:.4f}  seconds apart {a['gap']:.1f}  reference {a['reference_at']}")
