"""Read-only extraction for the leader-tactic analysis: every stored close reading (replayed or live) settled with the
real daily bars, for the leader family of tactics.  One JSON line per session on stdout; progress on stderr."""
import json, sys, time
from datetime import date, timedelta
from app import tactic_trial_runtime as rt
from app import tactic_trial_repository as repo
from app.tactic_trials import TACTICS, evaluate_tactic
from app.t1_settlement import SettlementBar, _limits
from app.database import Database

FAMILY = [t for t in TACTICS if t.slot == "close" and any(
    word in t.key for word in ("leader", "tier", "kawei", "front_row", "top_board", "relay_2_3", "ladder_env"))]
print(json.dumps({"family": [t.key for t in FAMILY]}), flush=True)
START, END = date(2023, 9, 1), date(2026, 10, 13)

def num(v):
    try: return None if v is None else float(v)
    except Exception: return None

database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '60s'")
    days = [r["calendar_date"] for r in c.execute(
        "SELECT calendar_date FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open AND calendar_date BETWEEN %s AND %s ORDER BY 1",
        (START, END)).fetchall()]
started = time.time()
for index, day in enumerate(days):
    with database.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '60s'")
        reading = rt._stored(c, rt.CLOSE_CAPABILITY, day)
        if not reading or not reading.get("rows"):
            continue
        pairs = [(r["symbol"], day) for r in reading["rows"]]
        bars = {}
        for bar in repo.read_exit_bars(c, pairs):
            bars.setdefault(str(bar["symbol"]), []).append(bar)
    settled = rt._settle_reading(reading, bars)
    per = {r["symbol"]: r["outcomes"] for r in settled["rows"]}
    def next_bar(symbol):
        for bar in bars.get(symbol, []):
            if bar["trading_date"] > day:
                return bar
        return None
    def pack(row):
        o = per.get(row["symbol"], {}); ent = o.get("entries") or {}
        s1 = next_bar(row["symbol"])
        limit_up = None
        if s1:
            lu, _ld = _limits(SettlementBar.from_row(s1), row["symbol"])   # stored limits, else the board band around the pre-close
            limit_up = num(lu)
        s1d = None
        if s1:
            op, hi, lo, cl = num(s1["open"]), num(s1["high"]), num(s1["low"]), num(s1["close"])
            at_limit_open = limit_up is not None and op is not None and op >= limit_up - 0.005
            s1d = {"open": op, "high": hi, "low": lo, "close": cl, "pre": num(s1.get("pre_close")), "limit_up": limit_up,
                   "open_at_limit": at_limit_open,
                   "one_word": bool(at_limit_open and lo is not None and lo >= limit_up - 0.005),
                   "reopened": bool(at_limit_open and lo is not None and lo < limit_up - 0.005)}
        return {"symbol": row["symbol"], "sector": row.get("sector"), "boards": row.get("boards"), "theme_count": row.get("theme_count"),
                "first_seal_time": row.get("first_seal_time"), "one_word_today": row.get("one_word"),
                "held_close": num((ent.get("held", {}).get("close") or {}).get("net_return")),
                "held_gap_rule": num((ent.get("held", {}).get("gap_rule") or {}).get("net_return")),
                "buy_open_close": num((ent.get("buy_open", {}).get("close") or {}).get("net_return")),
                "buy_open_gap_rule": num((ent.get("buy_open", {}).get("gap_rule") or {}).get("net_return")),
                "buy_open_reason": (ent.get("buy_open", {}).get("close") or {}).get("reason"),
                "seal": o.get("seal"), "big_loss": o.get("big_loss"), "s1": s1d}
    rows = {row["symbol"]: pack(row) for row in reading["rows"]}
    tactics = {}
    for t in FAMILY:
        picked = [row["symbol"] for row in reading["rows"] if evaluate_tactic(t, row, reading["day"]) is True]
        if picked:
            tactics[t.key] = picked
    print(json.dumps({"day": day.isoformat(), "rows": rows, "tactics": tactics}, ensure_ascii=False, default=str), flush=True)
    if index % 50 == 0:
        print(f"{index}/{len(days)} {time.time()-started:.0f}s", file=sys.stderr, flush=True)
print(json.dumps({"done": True}), flush=True)
