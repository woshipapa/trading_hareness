"""Read dated persisted controls without discarding complementary valuation providers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .datasources.derived.daily_valuations import ratio

DAILY_BASIC_SQL = """SELECT DISTINCT ON (basic.symbol)
    basic.symbol AS ts_code,to_char(basic.trading_date,'YYYYMMDD') AS trade_date,
    basic.close,basic.turnover_rate,basic.volume_ratio,basic.pe,basic.pb,basic.total_share,
    basic.float_share,basic.total_mv,basic.circ_mv,basic.provider AS source_provider,
    basic.available_at AS source_available_at,basic.raw AS source_metadata
FROM quant.daily_fundamentals basic
JOIN quant.canonical_bars_daily bar ON bar.symbol=basic.symbol AND bar.trading_date=basic.trading_date
JOIN quant.universe_membership_history member ON member.symbol=basic.symbol
    AND member.universe_key='all_a' AND member.effective_from<=basic.trading_date
    AND (member.effective_to IS NULL OR member.effective_to>basic.trading_date)
WHERE basic.trading_date=%s AND bar.quality_status='fresh'
  AND basic.available_at<((basic.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
  AND bar.available_at<((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
ORDER BY basic.symbol,
  (basic.turnover_rate IS NOT NULL)::int+(basic.volume_ratio IS NOT NULL)::int+
  (basic.total_mv IS NOT NULL)::int+(basic.pe IS NOT NULL)::int+(basic.pb IS NOT NULL)::int DESC,
  basic.available_at DESC,basic.provider"""


def read_persisted_control_rows(connection: Any, api_name: str, trade_date: date,
                                *, as_of: datetime | None = None) -> dict[str, Any] | None:
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("repair as_of must be timezone-aware")
    if api_name == "daily_basic":
        statement, params = DAILY_BASIC_SQL, (trade_date,)
        if as_of is not None:
            # Explicit repaired reads may see late arrivals. Existing callers
            # keep the original session cutoff and cannot acquire hindsight.
            statement = statement.replace("basic.available_at<((basic.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')", "basic.available_at<=%s")
            statement = statement.replace("bar.available_at<((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')", "bar.available_at<=%s")
            params = (trade_date,as_of,as_of)
        rows = [dict(row) for row in connection.execute(statement, params).fetchall()]
        if not rows:
            return None
        providers = sorted({str(row["source_provider"]) for row in rows})
        fields = ("turnover_rate", "volume_ratio", "pe", "pb", "total_share", "float_share", "total_mv", "circ_mv")
        return {"rows": rows, "provider": providers[0] if len(providers) == 1 else "owner_persisted_multi_source",
                "providers": providers, "trade_date": str(trade_date), "source": "owner_persisted_daily_basic",
                "field_symbols": {field: sum(ratio(row.get(field)) is not None for row in rows) for field in fields},
                "evidence_time_basis": "repair_as_of" if as_of is not None else "session_end",
                "as_of": as_of.isoformat() if as_of is not None else None,
                "notice": "Each row retains its own provider; record count does not guarantee every daily-basic field."}
    if api_name not in {"stk_limit", "suspend_d"}:
        return None
    table, date_column, projection = {
        "stk_limit": ("quant.daily_trade_limits", "trading_date", "symbol AS ts_code,to_char(trading_date,'YYYYMMDD') AS trade_date,limit_up,limit_down"),
        "suspend_d": ("quant.security_suspensions", "suspend_date", "symbol AS ts_code,to_char(suspend_date,'YYYYMMDD') AS trade_date,suspend_reason,resume_date"),
    }[api_name]
    provider_row = connection.execute(
        f"""SELECT provider,count(DISTINCT symbol)::int AS symbols FROM {table}
        WHERE {date_column}=%s AND available_at<(({date_column}+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
        GROUP BY provider ORDER BY symbols DESC,provider LIMIT 1""", (trade_date,),
    ).fetchone()
    if not provider_row:
        return None
    provider = str(provider_row["provider"])
    rows = connection.execute(
        f"""SELECT {projection} FROM {table} WHERE {date_column}=%s AND provider=%s
        AND available_at<(({date_column}+1)::timestamp AT TIME ZONE 'Asia/Shanghai')""", (trade_date,provider),
    ).fetchall()
    return {"rows": [dict(row) for row in rows], "provider": provider,
            "trade_date": str(trade_date), "source": f"owner_persisted_{api_name}"}
