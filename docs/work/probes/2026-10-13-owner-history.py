from app.database import Database
db = Database(); db.open()
def q(sql, params=None):
    with db.transaction() as c:
        c.execute("SET LOCAL statement_timeout = '90s'")
        return [dict(r) for r in c.execute(sql, params).fetchall()]
print("[bars by year]")
for r in q("""SELECT extract(year from trading_date)::int y, count(DISTINCT trading_date) days, count(*) n,
                     round(count(*)::numeric/count(DISTINCT trading_date)) per_day,
                     round(100.0*count(adj_factor)/count(*),1) adj_pct, round(100.0*count(limit_up)/count(*),1) limit_pct,
                     min(trading_date) first, max(trading_date) last
                FROM quant.canonical_bars_daily GROUP BY 1 ORDER BY 1"""):
    print(" ", r["y"], "days", r["days"], "rows", r["n"], "per_day", r["per_day"], "adj%", r["adj_pct"], "limit%", r["limit_pct"], r["first"], r["last"])
print("[calendar]", q("SELECT min(calendar_date) first, max(calendar_date) last, count(*) FILTER (WHERE is_open AND calendar_date BETWEEN '2023-09-01' AND current_date) open_since_202309 FROM quant.market_trade_calendar WHERE exchange='SSE'")[0])
print("[trade limits]", q("SELECT min(trading_date) first, max(trading_date) last, count(DISTINCT trading_date) days FROM quant.daily_trade_limits")[0])
print("[raw observations size]", q("SELECT reltuples::bigint n, pg_size_pretty(pg_total_relation_size(oid)) size FROM pg_class WHERE oid='quant.raw_market_observations'::regclass")[0])
caps = ["limit_performance_history", "limit_performance_live", "morning_bidding_history", "auction_pool_snapshot", "auction_curve_daily",
        "auction_bid_daily", "limit_up_risk_daily", "auction_open_desk_daily"]
print("[capabilities]")
for r in q("""SELECT provider_key, capability, min(effective_at)::date first, max(effective_at)::date last,
                     count(DISTINCT effective_at::date) days, count(*) n
                FROM quant.raw_market_observations WHERE capability = ANY(%s) GROUP BY 1,2 ORDER BY 2,1""", (caps,)):
    print(" ", r["provider_key"], r["capability"], r["first"], r["last"], "days", r["days"], "rows", r["n"])
print("[tables with minute/intraday/tick/flow data]")
for r in q("""SELECT relname, reltuples::bigint n, pg_size_pretty(pg_total_relation_size(oid)) size FROM pg_class
               WHERE relnamespace='quant'::regnamespace AND relkind IN ('r','p')
                 AND (relname ILIKE '%%minute%%' OR relname ILIKE '%%intraday%%' OR relname ILIKE '%%tick%%' OR relname ILIKE '%%flow%%' OR relname ILIKE '%%bars%%')
               ORDER BY pg_total_relation_size(oid) DESC LIMIT 16"""):
    print(" ", r["relname"], r["n"], r["size"])
