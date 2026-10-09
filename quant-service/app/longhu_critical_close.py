"""Repair held-symbol daily gaps independently of aggregate market coverage."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from .longhu_market_sync import PROVIDER_KEY, build_control_rows, merge_cross_section
from .longhu_settled_quotes import fetch


def missing_holdings(connection: Any, day: date) -> list[dict[str, Any]]:
    boundary=datetime.combine(day+timedelta(days=1),time(),ZoneInfo('Asia/Shanghai'))
    return [dict(r) for r in connection.execute(
        """WITH latest AS (
             SELECT DISTINCT ON(account_key) snapshot_id,observed_at
               FROM quant.broker_portfolio_snapshots
              WHERE verification='verified_exact' AND observed_at<%s
              ORDER BY account_key,observed_at DESC,snapshot_id DESC)
           SELECT DISTINCT p.symbol,i.name FROM latest s
             JOIN quant.broker_position_snapshots p USING(snapshot_id)
             JOIN quant.instruments i ON i.symbol=p.symbol
            WHERE p.quantity>0 AND s.observed_at>=%s
              AND NOT EXISTS(SELECT 1 FROM quant.canonical_bars_daily b
                WHERE b.symbol=p.symbol AND b.trading_date=%s AND b.close>0)
            ORDER BY p.symbol""",(boundary,boundary-timedelta(days=10),day)).fetchall()]


async def repair(day: date, *, db: Any, source_factory: Any, run_public_blocking: Any,
                 run_database_blocking: Any, persist_rows: Any) -> dict[str, Any]:
    def missing():
        with db.transaction() as c:
            return missing_holdings(c,day)
    rows=await run_database_blocking(missing)
    if not rows:
        return {'status':'unchanged','required_missing':[],'repaired':0}
    required=[r['symbol'] for r in rows]
    try:
        source=source_factory()
        quote_source=source if hasattr(source,'raw_call') else source._source
        quotes,health=await run_public_blocking(fetch,quote_source,required,day,workers=4,timeout_seconds=120)
        names={r['symbol']:r.get('name') or r['symbol'] for r in rows}
        quotes=[{**r,'name':names.get(r['ts_code'],r['ts_code'])} for r in quotes]
        merged=merge_cross_section(day,{},quotes)
        received={r['ts_code'] for r in merged.daily_rows}
        unresolved=sorted(set(required)-received)
        observed=datetime.now(timezone.utc)
        def persist():
            with db.transaction() as c:
                # Price-only evidence: no invented valuation, flow or factors.
                key='critical-held-close:'+str(day)+':'+','.join(required)
                normalized=persist_rows(c,'daily',key,merged.daily_rows,PROVIDER_KEY,observed)
                limits=build_control_rows(merged.daily_rows)['stk_limit']
                if limits:
                    persist_rows(c,'stk_limit',key,limits,PROVIDER_KEY,observed)
                return normalized
        count=await run_database_blocking(persist) if merged.daily_rows else 0
        return {'status':'blocked' if unresolved else 'completed','required_missing':required,
                'unresolved':unresolved,'repaired':count,'source_health':health,
                'reason':'held_symbol_daily_gap' if unresolved else None}
    except Exception as error:
        return {'status':'blocked','required_missing':required,'unresolved':required,'repaired':0,
                'reason':f'{type(error).__name__}: {str(error)[:300]}'}
