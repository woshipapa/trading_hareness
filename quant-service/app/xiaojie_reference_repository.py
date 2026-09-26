"""Session-scoped reference data for xiaojie leader-flow indicators.

Everything here is end-of-previous-session material - limit prices, the prior
bar, a 20-day high, MA5, sector membership - so it is read once per trading
date and reused by every 30-second scan rather than re-queried each time.

Point-in-time discipline: every read is bounded to sessions strictly before
the scan's own trading date.  Today's bars land in the same tables after the
close, and an intraday indicator that silently started using them would be
reading its own outcome.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .platform.strategy_data_needs import strategy_taxonomies
from .sector_membership_repository import point_in_time_membership_predicate, sector_group_predicate

_CHINA = ZoneInfo("Asia/Shanghai")

#: Sessions used for the breakout high and the recent-behaviour counters.
LOOKBACK_SESSIONS = 20
MA_SESSIONS = 5

#: The session's limit prices are a full-market cross-section of ~5,700 rows.
#: How a source pages that set is its own contract (see
#: ``app/datasources/sources/tushare_limits.py``); this module only checks the
#: result.

#: Any complete A-share cross-section spans both main exchanges. The check is
#: structural rather than a row count, so it does not drift as the market grows.
#:
#: It is needed because a terminal page is inferred from a page shorter than
#: the page size, and the provider returns rows ordered by exchange. On
#: 2026-09-18 the first page came back with 1,999 Shanghai rows against a
#: 2,000-row request, the loop read that as the end of the data, and the
#: session ran on a limit table holding one exchange - which silently makes
#: every Shenzhen name unable to register as sealed at its board.
TRADE_LIMIT_REQUIRED_EXCHANGES = frozenset({"SH", "SZ"})


def trade_limits(connection: Any, trading_date: date) -> dict[str, float]:
    """Upper limit price per symbol for the session being scanned.

    Limits are published for the session itself and are known before the open,
    so this is the one reference legitimately read for ``trading_date``.
    """
    rows = connection.execute(
        """SELECT DISTINCT ON (symbol) symbol, limit_up FROM quant.daily_trade_limits
            WHERE trading_date=%s AND limit_up IS NOT NULL
            ORDER BY symbol, provider""",
        (trading_date,),
    ).fetchall()
    return {str(row["symbol"]): float(row["limit_up"]) for row in rows}


async def ensure_session_trade_limits(
    trading_date: date, *,
    read_limits: Callable[[date], Awaitable[dict[str, float]]],
    fetch_limit_cross_section: Callable[[date], Awaitable[tuple[list[dict[str, Any]], str]]],
    persist_limits: Callable[[date, list[dict[str, Any]]], Awaitable[int]],
) -> dict[str, Any]:
    """Guarantee the session's limit prices exist before the first scan needs them.

    Limit prices are published for the session and are known before the open,
    but ``daily_trade_limits`` is only written by the post-close control sync -
    so intraday the table holds every session except the one being traded.
    Any board-state indicator would silently see no limits at all.  When the
    row set for the date is missing, it is fetched once through the
    ``limits.prices`` capability and persisted, after which every later scan
    reads it from the database.  How the source pages and proves completeness
    is the source adapter's contract; this guard only refuses a cross-section
    that does not span both exchanges.
    """
    existing = await read_limits(trading_date)
    if existing:
        return {"status": "already_present", "symbols": len(existing), "limits": existing}
    rows, provider_key = await fetch_limit_cross_section(trading_date)
    rows = [row for row in rows if str(row.get("ts_code") or "").strip()]
    if not rows:
        return {"status": "unavailable", "symbols": 0, "limits": {}}
    exchanges = {str(row["ts_code"]).strip().upper()[-2:] for row in rows}
    missing = TRADE_LIMIT_REQUIRED_EXCHANGES - exchanges
    if missing:
        # Left unpersisted on purpose: the table staying empty for the date is
        # what makes the next scan retry, where a partial would have been
        # cached for the whole session.
        return {
            "status": "incomplete", "symbols": 0, "limits": {},
            "reason": (f"limit cross-section covers only {sorted(exchanges)}; "
                       f"missing {sorted(missing)} after {len(rows)} rows"),
            "provider": provider_key,
        }
    stored = await persist_limits(trading_date, rows)
    limits = await read_limits(trading_date)
    return {"status": "fetched", "symbols": len(limits), "stored": stored,
            "provider": provider_key, "limits": limits}


def persist_trade_limit_rows(connection: Any, trading_date: date, rows: list[dict[str, Any]],
                             provider: str, available_at: datetime) -> int:
    """Upsert one session's published limit prices."""
    stored = 0
    for row in rows:
        symbol = str(row.get("ts_code") or "").upper()
        try:
            up_limit = float(row["up_limit"]) if row.get("up_limit") not in (None, "") else None
            down_limit = float(row["down_limit"]) if row.get("down_limit") not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if not symbol or up_limit is None:
            continue
        connection.execute(
            """INSERT INTO quant.daily_trade_limits(
                    symbol,trading_date,limit_up,limit_down,provider,available_at,raw)
               SELECT %s,%s,%s,%s,%s,%s,%s
                WHERE EXISTS(SELECT 1 FROM quant.instruments WHERE symbol=%s)
               ON CONFLICT(symbol,trading_date,provider) DO UPDATE SET
                 limit_up=EXCLUDED.limit_up,limit_down=EXCLUDED.limit_down,
                 available_at=EXCLUDED.available_at,raw=EXCLUDED.raw""",
            (symbol, trading_date, up_limit, down_limit, provider, available_at,
             Json(dict(row)), symbol),
        )
        stored += 1
    return stored


#: Sector taxonomies the session may draw its map from, best coverage wins and
#: ties break toward the earlier entry.
#:
#: Concept flow leads because it is the definition the strategy was written
#: against.  Longhu's industry taxonomy follows because it is the licensed
#: source that can actually answer at full breadth: concept membership is
#: filled one board at a time through a rate-limited Tushare route, while
#: Longhu returns all 104 industry boards through the owner gateway in
#: minutes.
#:
#: Coverage decides rather than mere presence.  On 2026-09-18 the concept
#: taxonomy held exactly one board - 278 symbols from an interrupted backfill -
#: against Longhu's 5,315, and "first non-empty" would have handed the strategy
#: the one-board map, leaving every pool member ``sector_core_unconfirmed``
#: just as an empty table did.  A partial sector map is not a smaller answer,
#: it is a wrong one.
#: Which taxonomies are candidates is this strategy's declared data need
#: (``app/platform/strategy_data_needs.py``), not a vendor list kept here.
SECTOR_TAXONOMY_PREFERENCE = strategy_taxonomies("xiaojie_leader_flow")


def membership_for_taxonomy(connection: Any, trading_date: date,
                            taxonomy_key: str) -> dict[str, set[str]]:
    """Point-in-time membership for exactly one taxonomy, sectors only.

    A qualification list is not a sector.  The concept taxonomy this falls back
    to carries 融资融券 with 3,915 members, and a name's move minus "everything
    margin-eligible" measures the market, not a sector rotation.
    """
    sector_only, sector_parameters = sector_group_predicate("member")
    membership_predicate = point_in_time_membership_predicate(
        "member", known_at_cutoff_sql="((%s::date + time '08:59:59') AT TIME ZONE 'Asia/Shanghai')",
    )
    rows = connection.execute(
        f"""SELECT symbol, sector_key FROM quant.sector_membership_history member
            WHERE taxonomy_key=%s AND {sector_only} AND {membership_predicate}""",
        (taxonomy_key, *sector_parameters, trading_date, trading_date, trading_date),
    ).fetchall()
    membership: dict[str, set[str]] = {}
    for row in rows:
        membership.setdefault(str(row["symbol"]), set()).add(str(row["sector_key"]))
    return membership


#: Below this a taxonomy is treated as not yet loaded rather than as a choice.
#: ths_concept_flow held 278 symbols for months while longhu_ths_industry held
#: 5,315, and preferring the wider map was the only sensible rule then.
MINIMUM_TAXONOMY_COVERAGE = 3_000


def _best_membership(connection: Any, trading_date: date) -> tuple[str | None, dict[str, set[str]]]:
    """The session's sector map: the declared order, skipping any not yet loaded.

    Selection used to be by coverage alone, which was right while only one
    taxonomy held anything.  Once both were populated it began choosing between
    maps that are not interchangeable: for 2026-09-21 ths_concept_flow covered
    5,569 names at 23.6 memberships each against longhu_ths_industry's 5,315 at
    1.2.  A 254-name lead would have swapped "the stock's industry" for
    twenty-three concepts, and leader-flow's whole question - is this name
    leading its sector - has no answer when the name has twenty-three sectors.

    So the declared order carries the intent and coverage only rejects a
    taxonomy nothing has filled in yet.  A session where none clears the floor
    still gets the widest available rather than nothing at all.
    """
    widest_key: str | None = None
    widest: dict[str, set[str]] = {}
    for candidate in SECTOR_TAXONOMY_PREFERENCE:
        membership = membership_for_taxonomy(connection, trading_date, candidate)
        if len(membership) >= MINIMUM_TAXONOMY_COVERAGE:
            return candidate, membership
        if len(membership) > len(widest):
            widest_key, widest = candidate, membership
    return widest_key, widest


def sector_membership(connection: Any, trading_date: date,
                      taxonomy_key: str | None = None) -> dict[str, set[str]]:
    """The session's sector map, from whichever taxonomy covers the most names.

    An explicit ``taxonomy_key`` pins the read to that one taxonomy and does
    not consider any other, so a caller that needs a specific vendor's
    definition still gets exactly it - or nothing.
    """
    if taxonomy_key is not None:
        return membership_for_taxonomy(connection, trading_date, taxonomy_key)
    return _best_membership(connection, trading_date)[1]


def sector_membership_taxonomy(connection: Any, trading_date: date) -> str | None:
    """Name the taxonomy the session's membership actually came from.

    Recorded alongside the membership so a later review of a candidate can see
    which vendor's sector definition confirmed it, rather than inferring it.
    """
    return _best_membership(connection, trading_date)[0]


def candidate_references(connection: Any, trading_date: date) -> dict[str, dict[str, Any]]:
    """Prior-session bar, 20-day high, MA5 and stagnation counters per symbol.

    ``days_without_new_high`` and ``days_without_rise`` are counted over the
    completed sessions before ``trading_date`` so the exit rules that read them
    are answerable from the first scan of the day rather than only at the close.
    """
    rows = connection.execute(
        """WITH recent AS (
              SELECT symbol, trading_date, open, high, low, close, pre_close, limit_up, volume,
                     row_number() OVER (PARTITION BY symbol ORDER BY trading_date DESC) AS rn
                FROM quant.canonical_bars_daily
               WHERE trading_date < %s AND trading_date >= %s - INTERVAL '90 days'
                 AND volume > 0 AND NOT coalesce(is_suspended, false)
           ), prior AS (
              SELECT symbol, open, high, low, close, pre_close, limit_up
                FROM recent WHERE rn = 1
           ), window_stats AS (
              SELECT symbol, max(high) AS high_20d, min(low) AS low_20d,
                     avg(close) FILTER (WHERE rn <= %s) AS ma5,
                     avg(close) AS ma20,
                     avg(volume) FILTER (WHERE rn <= %s) * 100 AS mean_volume_5d,
                     max(close) FILTER (WHERE rn = 10) AS close_10_sessions_ago
                FROM recent WHERE rn <= %s GROUP BY symbol
           ), no_new_high AS (
              SELECT symbol, count(*) AS days FROM recent r
               WHERE rn <= %s
                 AND NOT EXISTS (
                   SELECT 1 FROM recent p
                    WHERE p.symbol = r.symbol AND p.rn < r.rn AND p.high > r.high)
                 AND rn > 1
               GROUP BY symbol
           ), no_rise AS (
              SELECT symbol, count(*) AS days FROM recent
               WHERE rn <= %s AND pre_close IS NOT NULL AND close <= pre_close
               GROUP BY symbol
           )
           SELECT prior.symbol, prior.open, prior.high, prior.low, prior.close,
                  prior.pre_close, prior.limit_up,
                  window_stats.high_20d, window_stats.low_20d, window_stats.ma5,
                  window_stats.ma20, window_stats.mean_volume_5d,
                  window_stats.close_10_sessions_ago,
                  coalesce(no_new_high.days, 0) AS days_without_new_high,
                  coalesce(no_rise.days, 0) AS days_without_rise
             FROM prior
             LEFT JOIN window_stats USING (symbol)
             LEFT JOIN no_new_high USING (symbol)
             LEFT JOIN no_rise USING (symbol)""",
        (trading_date, trading_date, MA_SESSIONS, MA_SESSIONS, LOOKBACK_SESSIONS,
         LOOKBACK_SESSIONS, MA_SESSIONS),
    ).fetchall()
    references: dict[str, dict[str, Any]] = {}
    for row in rows:
        references[str(row["symbol"])] = {
            "prior_bar": {
                "open": float(row["open"]) if row["open"] is not None else None,
                "high": float(row["high"]) if row["high"] is not None else None,
                "low": float(row["low"]) if row["low"] is not None else None,
                "close": float(row["close"]) if row["close"] is not None else None,
                "pre_close": float(row["pre_close"]) if row["pre_close"] is not None else None,
                "limit_up": float(row["limit_up"]) if row["limit_up"] is not None else None,
            },
            "high_20d": float(row["high_20d"]) if row["high_20d"] is not None else None,
            "low_20d": float(row["low_20d"]) if row["low_20d"] is not None else None,
            "ma5": float(row["ma5"]) if row["ma5"] is not None else None,
            "ma20": float(row["ma20"]) if row["ma20"] is not None else None,
            "mean_volume_5d": float(row["mean_volume_5d"]) if row["mean_volume_5d"] is not None else None,
            "close_10_sessions_ago": (float(row["close_10_sessions_ago"])
                                      if row["close_10_sessions_ago"] is not None else None),
            "days_without_new_high": int(row["days_without_new_high"] or 0),
            "days_without_rise": int(row["days_without_rise"] or 0),
        }
    return references


#: Completed sessions searched for a 潜龙 marker K and the MA-convergence low.
QIANLONG_MARKER_LOOKBACK = 10
#: A marker K closes above its prior 20-session box on this multiple of the
#: prior five sessions' mean volume - the same bar as an intraday breakout.
QIANLONG_MARKER_VOLUME_RATIO = 1.5
#: Sessions behind the overhead-pressure high (前高/套牢盘).
PRESSURE_LOOKBACK_SESSIONS = 60
#: Sessions a name needs before "no marker K found" is a real absence rather
#: than a short history: the deepest marker needs its own 20-session box.
QIANLONG_MIN_HISTORY_SESSIONS = QIANLONG_MARKER_LOOKBACK + 20
#: A fundamentals row older than this no longer describes the session.
FUNDAMENTAL_MAX_AGE_DAYS = 20


def qianlong_references(connection: Any, trading_date: date) -> dict[str, dict[str, Any]]:
    """Per-symbol daily inputs for the 潜龙 contract and overheat check.

    All from completed sessions before ``trading_date``:

    * ``ma_spread_min_10d_pct`` - the tightest MA5/MA10/MA20 spread over the
      last ten sessions (均线收敛 before the move, not after it);
    * the most recent marker K in that window - a close above the prior
      20-session box on 1.5x the prior five sessions' volume - with its
      sessions-ago, box top and box range;
    * ``close_5_sessions_before`` for the pre-signal five-session run-up;
    * ``high_60d`` for the overhead pressure (前高) check;
    * ``fundamental_pe`` - the latest PE a provider published before the
      session, which Longhu reports negative for a loss-maker.
    """
    rows = connection.execute(
        """WITH bars AS (
              SELECT symbol, trading_date, high, low, close, volume,
                     row_number() OVER (PARTITION BY symbol ORDER BY trading_date DESC) AS rn,
                     count(*) OVER w20 AS n20,
                     avg(close) OVER (PARTITION BY symbol ORDER BY trading_date
                                      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5,
                     avg(close) OVER (PARTITION BY symbol ORDER BY trading_date
                                      ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) AS ma10,
                     avg(close) OVER w20 AS ma20,
                     max(high) OVER box AS box_high, min(low) OVER box AS box_low, count(*) OVER box AS box_n,
                     avg(volume) OVER (PARTITION BY symbol ORDER BY trading_date
                                       ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS prior_volume
                FROM quant.canonical_bars_daily
               WHERE trading_date < %s AND trading_date >= %s - INTERVAL '150 days'
                 AND volume > 0 AND NOT coalesce(is_suspended, false)
              WINDOW w20 AS (PARTITION BY symbol ORDER BY trading_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                     box AS (PARTITION BY symbol ORDER BY trading_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
           ), summary AS (
              SELECT symbol,
                     count(*) AS sessions,
                     max(close) FILTER (WHERE rn = 6) AS close_5_sessions_before,
                     max(ma10) FILTER (WHERE rn = 1) AS ma10,
                     max(high) FILTER (WHERE rn <= %s) AS high_60d,
                     min((greatest(ma5, ma10, ma20) - least(ma5, ma10, ma20)) / nullif(least(ma5, ma10, ma20), 0) * 100)
                       FILTER (WHERE rn <= %s AND n20 = 20) AS ma_spread_min_10d_pct
                FROM bars GROUP BY symbol
           ), markers AS (
              SELECT DISTINCT ON (symbol) symbol, rn AS marker_k_sessions_ago, box_high AS marker_box_top,
                     (box_high - box_low) / nullif(box_low, 0) * 100 AS marker_box_range_pct
                FROM bars
               WHERE rn <= %s AND box_n = 20 AND prior_volume > 0
                 AND close > box_high AND volume >= %s * prior_volume
               ORDER BY symbol, rn
           ), fundamentals AS (
              SELECT DISTINCT ON (symbol) symbol, pe, trading_date AS fundamental_date, provider AS fundamental_provider
                FROM quant.daily_fundamentals
               WHERE trading_date < %s AND trading_date >= %s - make_interval(days => %s) AND pe IS NOT NULL
               ORDER BY symbol, trading_date DESC, provider
           )
           SELECT summary.*, markers.marker_k_sessions_ago, markers.marker_box_top, markers.marker_box_range_pct,
                  fundamentals.pe AS fundamental_pe, fundamentals.fundamental_date, fundamentals.fundamental_provider
             FROM summary
             LEFT JOIN markers USING (symbol)
             LEFT JOIN fundamentals USING (symbol)""",
        (trading_date, trading_date, PRESSURE_LOOKBACK_SESSIONS, QIANLONG_MARKER_LOOKBACK,
         QIANLONG_MARKER_LOOKBACK, QIANLONG_MARKER_VOLUME_RATIO,
         trading_date, trading_date, FUNDAMENTAL_MAX_AGE_DAYS),
    ).fetchall()

    def number(value: Any) -> float | None:
        return float(value) if value is not None else None

    references: dict[str, dict[str, Any]] = {}
    for row in rows:
        references[str(row["symbol"])] = {
            "daily_history_complete": int(row["sessions"] or 0) >= QIANLONG_MIN_HISTORY_SESSIONS,
            "close_5_sessions_before": number(row["close_5_sessions_before"]),
            "ma10": number(row["ma10"]),
            "high_60d": number(row["high_60d"]),
            "ma_spread_min_10d_pct": number(row["ma_spread_min_10d_pct"]),
            "marker_k_sessions_ago": int(row["marker_k_sessions_ago"]) if row["marker_k_sessions_ago"] is not None else None,
            "marker_box_top": number(row["marker_box_top"]),
            "marker_box_range_pct": number(row["marker_box_range_pct"]),
            "fundamental": {
                "pe": number(row["fundamental_pe"]),
                "trading_date": row["fundamental_date"].isoformat() if row["fundamental_date"] else None,
                "provider": row["fundamental_provider"],
            },
        }
    return references


#: The licensed board-flow point the xiaojie scan reads sector return and net
#: inflow from, captured once a minute by the board-curve loop.
BOARD_FLOW_TAXONOMY = "longhu_ths_industry"


def latest_board_flow(connection: Any, trading_date: date, until: datetime) -> dict[str, Any]:
    """The session's newest stored board-flow cross-section at or before ``until``.

    Only boards of ``BOARD_FLOW_TAXONOMY`` are kept: they share their keys with
    the session's sector membership, which the public concept boards do not.
    """
    session_start = datetime.combine(trading_date, datetime.min.time(), tzinfo=_CHINA)
    row = connection.execute(
        """SELECT observed_at, payload->'providers' AS providers, payload->'items' AS items
             FROM quant.intraday_board_flow_snapshots
            WHERE observed_at >= %s AND observed_at <= %s AND status IN ('completed','partial')
            ORDER BY observed_at DESC LIMIT 1""",
        (session_start, until),
    ).fetchone()
    if row is None:
        return {"status": "missing", "taxonomy": BOARD_FLOW_TAXONOMY, "boards": {}}
    boards = {
        str(item["sector_key"]): {"label": item.get("label"), "change_pct": item.get("change_pct"),
                                  "net_inflow": item.get("net_inflow"), "amount": item.get("amount")}
        for item in (row["items"] or [])
        if isinstance(item, dict) and item.get("taxonomy_key") == BOARD_FLOW_TAXONOMY and item.get("sector_key")
    }
    return {"status": "stored" if boards else "missing", "taxonomy": BOARD_FLOW_TAXONOMY,
            "observed_at": row["observed_at"], "provider": (row["providers"] or {}).get("industry"),
            "boards": boards}


def market_volume_baseline(connection: Any, trading_date: date,
                           sessions: int = MA_SESSIONS) -> float | None:
    """Mean total market volume, in shares, over the recent completed sessions.

    ``canonical_bars_daily.volume`` is in 手, converted here so the baseline is
    directly comparable with the all-A snapshot's share counts.
    """
    # Index series live in the same table and carry aggregate volumes: on
    # 2026-08-25 000001.SH, 399001.SZ, 000300.SH and 399006.SZ alone summed to
    # 84.45 of the table's 189.37 billion shares, against 104.83 billion for
    # the listed names.  Including them nearly doubled the baseline and made a
    # normal session read as half its usual volume.  They carry an instruments
    # row but no listing date, which is the discriminator used here - a listed
    # security has one, an index does not.
    row = connection.execute(
        """SELECT avg(total) * 100 AS baseline FROM (
             SELECT b.trading_date, sum(b.volume) AS total
               FROM quant.canonical_bars_daily b
               JOIN quant.instruments i ON i.symbol = b.symbol
              WHERE i.list_date IS NOT NULL
                AND b.trading_date < %s AND b.volume > 0
                AND NOT coalesce(b.is_suspended, false)
              GROUP BY b.trading_date ORDER BY b.trading_date DESC LIMIT %s) recent""",
        (trading_date, sessions),
    ).fetchone()
    return float(row["baseline"]) if row and row["baseline"] is not None else None


def instrument_names(connection: Any) -> dict[str, str]:
    """Symbol to its Chinese short name.

    A live snapshot carries no name, so an alert built from one identifies a
    stock by code alone - readable to the pipeline, not to the person holding
    the phone. This rides along with the rest of the session reference, which
    is cached per trading date, so naming costs one query a day.
    """
    rows = connection.execute(
        "SELECT symbol, name FROM quant.instruments WHERE name IS NOT NULL AND name <> ''"
    ).fetchall()
    return {row["symbol"]: row["name"] for row in rows}


def load_session_reference(connection: Any, trading_date: date) -> dict[str, Any]:
    """One call for everything a session's indicator construction needs."""
    references = candidate_references(connection, trading_date)
    for symbol, extra in qianlong_references(connection, trading_date).items():
        references.setdefault(symbol, {}).update(extra)
    return {
        "trading_date": trading_date,
        "limits": trade_limits(connection, trading_date),
        "membership": sector_membership(connection, trading_date),
        "membership_taxonomy": sector_membership_taxonomy(connection, trading_date),
        "references": references,
        "market_volume_baseline": market_volume_baseline(connection, trading_date),
        "names": instrument_names(connection),
    }


__all__ = [
    "BOARD_FLOW_TAXONOMY", "LOOKBACK_SESSIONS", "MA_SESSIONS", "MINIMUM_TAXONOMY_COVERAGE",
    "QIANLONG_MARKER_LOOKBACK", "QIANLONG_MARKER_VOLUME_RATIO", "SECTOR_TAXONOMY_PREFERENCE",
    "candidate_references", "ensure_session_trade_limits", "latest_board_flow", "qianlong_references",
    "instrument_names", "load_session_reference", "membership_for_taxonomy",
    "persist_trade_limit_rows", "market_volume_baseline", "sector_membership",
    "sector_membership_taxonomy", "trade_limits",
]
