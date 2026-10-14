"""B002/B078 apply (operator repair, authorised by the user 2026-10-14): fill 2023-08-01..2023-12-29's missing adjustment
factors from the pre_close chain, anchored on each stock's earliest stored factor.  f(t) = f(t-1) * close(t-1)/pre_close(t).

Set based: one transaction per session, four statements (raw evidence, factors, bars, run record), never an instrument
upsert, never a row that already has a factor.  Resumable (every statement skips what is there).  After each session it
counts what it wrote and stops on the first mismatch.   Dry run by default; --apply writes.
Run inside the owner's quant-research container:  python - [--apply] [FROM [TO]] < factors_fill_2023_sb.py
"""
import hashlib, json, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from app.database import Database

APPLY = "--apply" in sys.argv
_dates = [a for a in sys.argv[1:] if not a.startswith("--")]
FROM = _dates[0] if _dates else "0000-00-00"
TO = _dates[1] if len(_dates) > 1 else (_dates[0] if _dates else "9999-99-99")
START, END = "2023-08-01", "2023-12-29"
PROVIDER, SEMANTICS = "longhu_qfq_derived", "corporate_action_cumulative"
METHOD = "pre_close_chain_v1"
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '120s'")
    symbols = [r["symbol"] for r in c.execute(
        """SELECT DISTINCT symbol FROM quant.canonical_bars_daily
            WHERE trading_date BETWEEN %s AND %s AND symbol ~ '^[0-9]{6}\\.(SH|SZ|BJ)$' ORDER BY 1""", (START, END)).fetchall()]
stats = Counter(); to_write = defaultdict(list); started = time.time()
for i in range(0, len(symbols), 400):
    with database.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '120s'")
        rows = c.execute("""SELECT symbol, trading_date, close, pre_close, adj_factor FROM quant.canonical_bars_daily
                             WHERE trading_date BETWEEN %s AND %s AND symbol = ANY(%s) ORDER BY symbol, trading_date""",
                         (START, END, symbols[i:i + 400])).fetchall()
    by = defaultdict(list)
    for r in rows: by[r["symbol"]].append(r)
    for sym, s in by.items():
        anchor = next((k for k, r in enumerate(s) if r["adj_factor"] is not None and float(r["adj_factor"]) > 0), None)
        if anchor is None:
            stats["symbols_without_anchor"] += 1; continue
        f = [None] * len(s); f[anchor] = float(s[anchor]["adj_factor"])
        for k in range(anchor + 1, len(s)):
            if not (s[k]["pre_close"] and s[k - 1]["close"]): break
            f[k] = f[k - 1] * float(s[k - 1]["close"]) / float(s[k]["pre_close"])
        for k in range(anchor - 1, -1, -1):
            if f[k + 1] is None or not (s[k + 1]["pre_close"] and s[k]["close"]): break
            f[k] = f[k + 1] * float(s[k + 1]["pre_close"]) / float(s[k]["close"])
        for k, r in enumerate(s):
            if f[k] is not None and r["adj_factor"] is None and FROM <= str(r["trading_date"]) <= TO:
                to_write[r["trading_date"]].append((sym, r["trading_date"], round(f[k], 6), str(s[anchor]["trading_date"]), str(s[anchor]["adj_factor"])))
print(f"computed {sum(len(v) for v in to_write.values())} rows over {len(to_write)} sessions in {time.time()-started:.0f}s", flush=True)
if not APPLY:
    print("DRY RUN"); raise SystemExit(0)

written_total = 0
for day in sorted(to_write):
    items = to_write[day]
    now = datetime.now(timezone.utc)
    request_key = hashlib.sha256(json.dumps({"capability": "adj_factor_pre_close_chain_v1", "trade_date": str(day),
                                             "symbols": [x[0] for x in items]}, sort_keys=True).encode()).hexdigest()
    day8 = day.strftime("%Y%m%d")
    raws = [{"ts_code": sym, "trade_date": day8, "adj_factor": repr(fac), "step": 1.0, "method": METHOD,
             "basis": "pre_close_chain_from_earliest_stored_factor",
             "anchor": {"date": anchor_date, "kind": "stored_factor", "provider": PROVIDER, "adj_factor": anchor_factor},
             "operator": "claude session 2026-10-14, authorised by the user"} for sym, _d, fac, anchor_date, anchor_factor in items]
    record_keys = [f"ts_code={r['ts_code']}|trade_date={day8}" for r in raws]
    texts = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in raws]
    hashes = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
    factor_raws = [json.dumps({**r, "factor_semantics": SEMANTICS, "adjustment_state": "complete"}, ensure_ascii=False) for r in raws]
    syms = [x[0] for x in items]; dates = [day] * len(items); facs = [x[2] for x in items]
    with database.transaction() as c:
        c.execute("SET LOCAL lock_timeout = '30s'"); c.execute("SET LOCAL statement_timeout = '120s'")
        c.execute("""INSERT INTO quant.tushare_raw_records(provider_key,api_name,request_key,record_index,record_key,content_sha256,row_data,available_at)
                     SELECT %s,'adj_factor',%s,t.i-1,t.k,t.h,t.r::jsonb,%s
                       FROM unnest(%s::text[], %s::text[], %s::text[]) WITH ORDINALITY AS t(k,h,r,i)
                     ON CONFLICT(provider_key,api_name,record_key,content_sha256) DO NOTHING""",
                  (PROVIDER, request_key + ":adj_factor", now, record_keys, hashes, texts))
        c.execute("""INSERT INTO quant.daily_adjustment_factors(symbol,trading_date,adj_factor,provider,available_at,raw)
                     SELECT t.s,t.d,t.f,%s,%s,t.r::jsonb FROM unnest(%s::text[], %s::date[], %s::numeric[], %s::text[]) AS t(s,d,f,r)
                     ON CONFLICT(symbol,trading_date,provider) DO NOTHING""",
                  (PROVIDER, now, syms, dates, facs, factor_raws))
        c.execute("""UPDATE quant.canonical_bars_daily b SET adj_factor=t.f, canonicalized_at=now()
                       FROM unnest(%s::text[], %s::date[], %s::numeric[]) AS t(s,d,f)
                      WHERE b.symbol=t.s AND b.trading_date=t.d AND b.adj_factor IS NULL""", (syms, dates, facs))
        have = c.execute("""SELECT count(*) AS n FROM quant.daily_adjustment_factors WHERE provider=%s AND trading_date=%s
                             AND symbol = ANY(%s)""", (PROVIDER, day, syms)).fetchone()["n"]
        null_left = c.execute("""SELECT count(*) AS n FROM quant.canonical_bars_daily WHERE trading_date=%s AND symbol = ANY(%s)
                                  AND adj_factor IS NULL""", (day, syms)).fetchone()["n"]
        c.execute("""INSERT INTO quant.fetch_runs(provider_key,capability,trade_date,request_key,status,attempt_count,started_at,finished_at,row_count,metadata)
                     VALUES(%s,'adj_factor_pre_close_chain',%s,%s,'completed',1,now(),now(),%s,%s::jsonb)
                     ON CONFLICT(request_key) DO UPDATE SET status='completed',finished_at=now(),row_count=EXCLUDED.row_count""",
                  (PROVIDER, day, request_key, len(items), json.dumps({"method": METHOD, "operator": "claude session 2026-10-14"})))
        if have < len(items) or null_left:
            print(f"MISMATCH {day}: planned {len(items)}, factors present {have}, bars still without factor {null_left}", flush=True)
            raise SystemExit(3)      # leaves the transaction to roll back
    written_total += len(items)
    print(f"{day}: {len(items)} rows (factors present {have}, bars without factor {null_left})", flush=True)
print("WRITTEN rows planned:", written_total)
