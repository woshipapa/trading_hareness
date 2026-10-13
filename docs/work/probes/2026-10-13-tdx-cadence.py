"""Read-only probe on the owner (W008 prerequisites 1 and 4): the cadence of the two whole-market capture loops on
2026-10-13, and what a day's documents weigh. It never selects a document body: only effective_at, available_at,
the small `payload` column and pg_column_size(normalized), which reads the stored size without detoasting.
Run inside the owner's main service container (calling convention: W008 record, "Resume here")."""
import statistics, time
from datetime import datetime, timedelta, timezone
from app.database import Database
CN = timezone(timedelta(hours=8))
day = datetime(2026, 10, 13, tzinfo=CN)
db = Database(); db.open()
def q(sql, params):
    with db.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '20s'")
        started = time.monotonic()
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
        return rows, time.monotonic() - started
def hm(t): return t.astimezone(CN).strftime("%H:%M")
def hms(t): return t.astimezone(CN).strftime("%H:%M:%S")
def session(t):
    s = hm(t)
    return "am" if s <= "11:30" else "pm"
now = datetime.now(CN)
print("probe ran at", now.strftime("%Y-%m-%d %H:%M:%S"), "Asia/Shanghai")
last_minute = min(now, day.replace(hour=15)).strftime("%H:%M")
want = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(60)
        if ("09:15" <= f"{h:02d}:{m:02d}" <= "11:30" or "13:00" <= f"{h:02d}:{m:02d}" <= "15:00") and f"{h:02d}:{m:02d}" < last_minute]
for provider, capability in (("fuyao_ths", "a_share_minute_cross_section"), ("tdx_public", "a_share_minute_cross_section_shadow")):
    rows, seconds = q("""SELECT effective_at, available_at, pg_column_size(normalized) stored_bytes, (payload->>'rows')::int n
                           FROM quant.raw_market_observations
                          WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at < %s
                          ORDER BY effective_at""", (provider, capability, day, day + timedelta(days=1)))
    print(f"[{provider} {capability}] documents {len(rows)}  (this query, no body: {seconds:.2f} s)")
    stored = [r["stored_bytes"] for r in rows]
    print(f"  stored size of normalized: total {sum(stored)/1e6:.1f} MB  median {statistics.median(stored)/1e3:.0f} KB  max {max(stored)/1e3:.0f} KB (compressed, as stored)")
    times = [r["effective_at"] for r in rows]
    print("  first", hms(times[0]), "last", hms(times[-1]), "| second of the minute: first", times[0].astimezone(CN).strftime("%S"), "last of morning",
          [t for t in times if session(t) == "am"][-1].astimezone(CN).strftime("%S"), "first of afternoon",
          ([t for t in times if session(t) == "pm"] or [times[-1]])[0].astimezone(CN).strftime("%S"), "last", times[-1].astimezone(CN).strftime("%S"))
    deltas = [((b - a).total_seconds(), a, b) for a, b in zip(times, times[1:]) if session(a) == session(b)]
    d = sorted(x[0] for x in deltas)
    def pct(p): return d[min(len(d) - 1, int(p * len(d)))]
    print(f"  seconds between consecutive captures (same half-day), n {len(d)}: min {d[0]:.1f}  p10 {pct(.1):.1f}  median {statistics.median(d):.2f}  p90 {pct(.9):.1f}  max {d[-1]:.1f}")
    buckets = {"under 60": 0, "60-62": 0, "62-64": 0, "64-66": 0, "66-70": 0, "70-90": 0, "90 and more": 0}
    for x in d:
        key = ("under 60" if x < 60 else "60-62" if x < 62 else "62-64" if x < 64 else "64-66" if x < 66 else "66-70" if x < 70 else "70-90" if x < 90 else "90 and more")
        buckets[key] += 1
    print("  distribution:", buckets)
    long = [(round(x, 1), hms(a), hms(b)) for x, a, b in deltas if x >= 90]
    print("  intervals of 90 s and more (a cycle that stored nothing):", long)
    lag = sorted((r["available_at"] - r["effective_at"]).total_seconds() for r in rows)
    print(f"  seconds from capture start (effective_at) to the insert (available_at): median {statistics.median(lag):.2f}  p90 {lag[int(.9*len(lag))]:.2f}  max {lag[-1]:.2f}")
    got = {hm(t) for t in times}
    missing = [m for m in want if m not in got]
    # a minute lost to drift: the neighbouring captures are one ordinary cycle apart and straddle the minute
    drift, other = [], []
    for m in missing:
        before = [t for t in times if hm(t) < m]
        after = [t for t in times if hm(t) > m]
        if before and after and (after[0] - before[-1]).total_seconds() < 90 and session(before[-1]) == session(after[0]):
            drift.append((m, hms(before[-1]), hms(after[0])))
        else:
            other.append((m, hms(before[-1]) if before else None, hms(after[0]) if after else None))
    print(f"  minutes without a document, of {len(want)} expected (09:15-11:30, 13:00-{last_minute} exclusive): {len(missing)}")
    print("    straddled by one ordinary cycle (drift):", len(drift), drift)
    print("    other (a failed cycle, or the edge of the session):", len(other), other)
    counts = [r["n"] for r in rows if r["n"] is not None]
    print("  rows per document: min", min(counts), "median", statistics.median(counts), "max", max(counts))
rows, seconds = q("""SELECT count(*) n, sum(pg_column_size(normalized)) stored FROM quant.raw_market_observations
                      WHERE provider_key='local_derived' AND capability='all_a_snapshot_source_agreement' AND symbol IS NULL
                        AND effective_at >= %s AND effective_at < %s""", (day, day + timedelta(days=1)))
print(f"[agreement rows] {rows[0]['n']} rows, stored {rows[0]['stored']/1e3:.0f} KB in total (query {seconds:.2f} s)")
