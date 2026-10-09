"""Keep the market radar beside the all-A capture, and read its day back.

Every persisted all-A cross-section is handed to :meth:`MarketRadarRuntime.observe`,
which adds one radar point and stores it as a timed observation
(``local_derived`` / ``market_radar``) - no table of its own.  The band
membership a day has built survives a restart: the first point of a day
rebuilds it from that day's stored points, whose ``entered`` lists name each
stock the moment it first went beyond a band.

The read side returns a day's points and lines them up with the market's
main net inflow, which a price snapshot does not carry: it is the sum of the
industry boards in the board-flow capture, and which vendor's definition
that is (Eastmoney's f62, or Longhu's when the licensed industry flow is up)
is reported beside it.  Research evidence, never an order.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .market_radar import CN_TZ, RADAR_VERSION, RadarState, radar_point

PROVIDER_KEY = "local_derived"
CAPABILITY = "market_radar"
#: Industry taxonomies whose boards partition the market, so their sum is its main net flow.
INDUSTRY_TAXONOMIES = ("eastmoney_industry", "longhu_ths_industry")
#: Board-flow values are stored in 100 million CNY.
BOARD_FLOW_UNIT = 100_000_000.0
#: How often a day still missing its limit prices asks for them again.
LIMIT_RETRY_SECONDS = 300.0


def _day_bounds(trade_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trade_date, time(0, 0), CN_TZ)
    return start, start + timedelta(days=1)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def stored_points(connection: Any, trade_date: date) -> list[dict[str, Any]]:
    start, end = _day_bounds(trade_date)
    rows = connection.execute(
        """SELECT effective_at,normalized FROM quant.raw_market_observations
            WHERE capability=%s AND symbol IS NULL AND provider_key=%s
              AND effective_at>=%s AND effective_at<%s
            ORDER BY effective_at""",
        (CAPABILITY, PROVIDER_KEY, start, end),
    ).fetchall()
    return [_payload(dict(row)["normalized"]) for row in rows]


def session_limits(connection: Any, trade_date: date) -> dict[str, tuple[float | None, float | None]]:
    rows = connection.execute(
        """SELECT DISTINCT ON (symbol) symbol,limit_up,limit_down FROM quant.daily_trade_limits
            WHERE trading_date=%s AND limit_up IS NOT NULL ORDER BY symbol,provider""",
        (trade_date,),
    ).fetchall()
    return {str(row["symbol"]): (float(row["limit_up"]), float(row["limit_down"]) if row["limit_down"] is not None else None)
            for row in (dict(item) for item in rows)}


def main_net_series(connection: Any, trade_date: date) -> list[dict[str, Any]]:
    """The market's main net inflow per board-flow snapshot: the sum of its industry boards."""
    start, end = _day_bounds(trade_date)
    rows = connection.execute(
        """SELECT snapshot_minute,observed_at,status,payload FROM quant.intraday_board_flow_snapshots
            WHERE observed_at>=%s AND observed_at<%s ORDER BY snapshot_minute""",
        (start, end),
    ).fetchall()
    series = []
    for row in (dict(item) for item in rows):
        payload = _payload(row["payload"])
        values = [item.get("net_inflow") for item in payload.get("items") or []
                  if isinstance(item, Mapping) and item.get("taxonomy_key") in INDUSTRY_TAXONOMIES]
        numbers = [float(value) for value in values if isinstance(value, (int, float))]
        if not numbers:
            continue
        series.append({
            "observed_at": row["observed_at"].isoformat() if hasattr(row["observed_at"], "isoformat") else row["observed_at"],
            "main_net": round(sum(numbers) * BOARD_FLOW_UNIT, 2), "boards": len(numbers),
            "source": (payload.get("providers") or {}).get("industry"), "status": row["status"],
        })
    return series


def radar_day(connection: Any, trade_date: date, *, include_entered: bool = False) -> dict[str, Any]:
    points = stored_points(connection, trade_date)
    if not include_entered:
        points = [{key: value for key, value in point.items() if key != "entered"} for point in points]
    return {
        "trade_date": trade_date.isoformat(), "radar_version": RADAR_VERSION, "points": points,
        "main_net": main_net_series(connection, trade_date),
        "units": {"turnover": "cny", "main_net": "cny"},
        "definitions": {
            "cum_up/cum_down": "当日曾达到 +阈值/-阈值 的股票（按首次触及归一侧，只进不出）的当日累计成交额",
            "now_up/now_down": "此刻仍在阈值之外的股票的当日累计成交额",
            "middle": "全池总额减去两侧累计（轧差）；now_middle 为即时口径",
            "limit": "以当日公布涨跌停价为阈值的同一组带",
            "auction": "09:25 撮合前只有虚拟价：报告各阈值外的股票数，不计入当日集合",
            "main_net": "行业板块主力净额之和（来源见 source：eastmoney_free 为东财 f62，longhuvip 为开盘啦口径）",
        },
        "research_only": True, "live_effect": "none",
    }


@dataclass(frozen=True)
class MarketRadarDependencies:
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    persist_timed_observations: Callable[[str, str, list[dict[str, Any]]], int]


class MarketRadarRuntime:
    """One radar point per persisted all-A cross-section."""

    def __init__(self, deps: MarketRadarDependencies) -> None:
        self.deps = deps
        self.state: RadarState | None = None
        self.limits: dict[str, tuple[float | None, float | None]] = {}
        self._limits_read_at: float | None = None

    def _read(self, action: Callable[[Any, date], Any], trade_date: date) -> Callable[[], Any]:
        def read() -> Any:
            with self.deps.database.transaction() as connection:
                return action(connection, trade_date)
        return read

    async def observe(self, observed_at: datetime, rows: list[dict[str, Any]]) -> dict[str, Any]:
        trade_date = observed_at.astimezone(CN_TZ).date()
        if self.state is None or self.state.trading_date != trade_date:
            state = RadarState(trade_date)
            for point in await self.deps.run_database(self._read(stored_points, trade_date), timeout_seconds=30):
                state.restore(point)
            self.state, self.limits, self._limits_read_at = state, {}, None
        now = observed_at.timestamp()
        if not self.limits and (self._limits_read_at is None or now - self._limits_read_at >= LIMIT_RETRY_SECONDS):
            self.limits = await self.deps.run_database(self._read(session_limits, trade_date), timeout_seconds=30)
            self._limits_read_at = now
        point = radar_point(rows, self.state, observed_at=observed_at, limits=self.limits or None)
        stored = await self.deps.run_database(
            self.deps.persist_timed_observations, PROVIDER_KEY, CAPABILITY,
            [{**point, "effective_at": observed_at.isoformat(),
              "available_at": datetime.now(timezone.utc).isoformat()}],
            timeout_seconds=30,
        )
        return {"status": "stored" if stored else "not_stored", "phase": point["phase"],
                "limit_band": bool(self.limits)}


__all__ = [
    "BOARD_FLOW_UNIT", "CAPABILITY", "INDUSTRY_TAXONOMIES", "MarketRadarDependencies", "MarketRadarRuntime",
    "PROVIDER_KEY", "main_net_series", "radar_day", "session_limits", "stored_points",
]
