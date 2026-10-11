"""Is each session indicator present, fresh, continuous, plausible and agreed?

One check per question, each with its value, threshold and verdict. An
indicator is ``decision_eligible`` only when every one of its checks is
``ok``. A ``warn`` is reported but still withholds eligibility, and ``pending``
means the indicator is not due yet at this time of day.

The checks are deliberately ones that would have caught real faults:
- **The main net's plausibility.** A market net beyond a quarter of the whole
  pool's turnover is a unit error. This is the CNY/亿 mix that Longhu industry
  rows carried until 2026-10-09.
- **The limit detail's three-way count.** It sets the Longhu review against
  选股宝's pool and against the radar's closing limit band.
- **The radar's continuity.** It is the largest gap between points inside
  continuous trading, not merely the latest point.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import derived_daily_readings
from .broad_etf_flow import FLOW_CAPABILITY as ETF_FLOW_CAPABILITY, MIN_CODES as ETF_MIN_CODES
from .indicator_registry import BY_KEY, INTRADAY_MINUTE, PREVIOUS_SESSION, SESSION_OPEN
from .limit_detail_read_model import limit_detail_day
from .market_radar_runtime import latest_main_net, latest_point
from .market_temperature_runtime import CAPABILITY as TEMPERATURE_CAPABILITY
from .market_timing import CAPABILITY as TIMING_CAPABILITY
from .owner_storage import tiered_sql_builder
from .stock_money_flow_sync import stored_flow_symbols

CN_TZ = ZoneInfo("Asia/Shanghai")
OK, WARN, FAIL, PENDING, MISSING = "ok", "warn", "fail", "pending", "missing"
#: Worst first, for a status built from several.
SEVERITY = (FAIL, MISSING, PENDING, WARN, OK)
#: The radar is fed once a minute; three missed captures is a warning, ten a failure.
FRESH_SECONDS, STALE_SECONDS = 180, 600
#: A-share common stocks number about 5,100-5,400; below this the cross-section is partial.
MIN_POOL, WARN_POOL = 4500, 3000
#: The largest market main net that is plausible, as a share of the pool's turnover.
MAX_MAIN_NET_SHARE = 0.25
#: The close pipeline's indicators are due by this time on the session.
POST_CLOSE_DUE = time(17, 30)
MINUTE_DUE = time(9, 26)
#: The session's limit prices are written by the first 小杰 scan after the open.
SESSION_OPEN_DUE = time(9, 35)
CONTINUOUS = ((time(9, 31), time(11, 30)), (time(13, 1), time(15, 0)))


def _check(name: str, status: str, value: Any = None, threshold: Any = None, detail: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "value": value, "threshold": threshold, "detail": detail}


def _grade(value: float | None, ok_at: float, warn_at: float, *, lower_is_better: bool = False) -> str:
    if value is None:
        return MISSING
    if lower_is_better:
        return OK if value <= ok_at else WARN if value <= warn_at else FAIL
    return OK if value >= ok_at else WARN if value >= warn_at else FAIL


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), CN_TZ)
    return start, start + timedelta(days=1)


def _in_continuous(moment: datetime) -> bool:
    clock = moment.astimezone(CN_TZ).time()
    return any(start <= clock <= end for start, end in CONTINUOUS)


def _due(trade_date: date, now: datetime, availability: str) -> bool:
    local = now.astimezone(CN_TZ)
    if availability == PREVIOUS_SESSION or local.date() > trade_date:
        return True
    if local.date() < trade_date:
        return False
    due = {INTRADAY_MINUTE: MINUTE_DUE, SESSION_OPEN: SESSION_OPEN_DUE}.get(availability, POST_CLOSE_DUE)
    return local.time() >= due


def _one(connection: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    row = connection.execute(sql, params).fetchone()
    return dict(row) if row else {}


def radar_checks(connection: Any, trade_date: date, now: datetime) -> list[dict[str, Any]]:
    start, end = _day_bounds(trade_date)
    summary = _one(connection, """
        WITH points AS (
            SELECT effective_at,(effective_at AT TIME ZONE 'Asia/Shanghai')::time AS clock
              FROM quant.raw_market_observations
             WHERE capability='market_radar' AND provider_key='local_derived' AND symbol IS NULL
               AND effective_at>=%s AND effective_at<%s),
        continuous AS (
            SELECT effective_at - lag(effective_at) OVER (PARTITION BY clock<'12:00' ORDER BY effective_at) AS gap
              FROM points WHERE clock BETWEEN '09:31' AND '11:30' OR clock BETWEEN '13:01' AND '15:00')
        SELECT (SELECT count(*) FROM points)::int AS points,(SELECT max(effective_at) FROM points) AS last_at,
               (SELECT extract(epoch FROM max(gap)) FROM continuous) AS max_gap_seconds""", (start, end))
    if not summary.get("points"):
        return [_check("present", MISSING, 0, ">0", "该日没有雷达点")]
    checks = [_check("present", OK, summary["points"], ">0")]
    if now.astimezone(CN_TZ).date() == trade_date and _in_continuous(now):
        age = (now - summary["last_at"]).total_seconds()
        checks.append(_check("fresh", _grade(age, FRESH_SECONDS, STALE_SECONDS, lower_is_better=True),
                             round(age), f"<={FRESH_SECONDS}s", "最新雷达点距今秒数"))
    gap = summary.get("max_gap_seconds")
    if gap is None:
        # Fewer than two points inside continuous trading (just after the open, or
        # a restart): continuity cannot be judged yet, which is not the same as missing.
        checks.append(_check("continuity", WARN, None, f"<={FRESH_SECONDS}s", "连续竞价内不足两个点，尚不能判断间隔"))
    else:
        checks.append(_check("continuity", _grade(float(gap), FRESH_SECONDS, STALE_SECONDS, lower_is_better=True),
                             round(float(gap)), f"<={FRESH_SECONDS}s", "连续竞价内相邻两点的最大间隔"))
    point = latest_point(connection, trade_date) or {}
    pool = (point.get("pool") or {}).get("count")
    checks.append(_check("coverage", _grade(pool, MIN_POOL, WARN_POOL), pool, f">={MIN_POOL}", "最新一点计入的股票数"))
    if point.get("bands") is not None:
        checks.append(_check("limit_band", OK if "limit" in point["bands"] else WARN, "limit" in point["bands"], True,
                             "当日涨跌停价已读到，涨跌停带可用"))
    checks.extend(longhu_mood_checks(connection, trade_date, point))
    return checks


MOOD_CAPABILITY = "longhu:longhu_market_wide:MoodNumCount"


def longhu_mood_checks(connection: Any, trade_date: date, point: dict[str, Any]) -> list[dict[str, Any]]:
    """The radar's closing breadth and limit counts against 开盘啦's own tally, archived after the close.

    The two count the same market but not by one rulebook (suspensions, new
    listings, timing), so a few percent apart is agreement. Only a point from
    the closing auction is compared, because the archived tally is the closing one.
    """
    row = _one(connection, """SELECT normalized FROM quant.raw_market_observations
                               WHERE capability=%s AND provider_key='longhuvip' AND normalized->>'exchange_date'=%s
                               ORDER BY available_at DESC LIMIT 1""", (MOOD_CAPABILITY, trade_date.isoformat()))
    mood = (((row.get("normalized") or {}).get("payload") or {}).get("list") or {}) if row else {}
    observed = point.get("observed_at")
    if not mood or not observed or datetime.fromisoformat(observed).astimezone(CN_TZ).time() < time(14, 57):
        return []
    breadth = point.get("breadth") or {}
    limit_band = (point.get("bands") or {}).get("limit") or {}
    pairs = (("rising_vs_longhu", breadth.get("up"), mood.get("SZJS"), 0.05, 0.15),
             ("falling_vs_longhu", breadth.get("down"), mood.get("XDJS"), 0.05, 0.15),
             ("limit_up_vs_longhu", (limit_band.get("now_up") or {}).get("count"), mood.get("ZTJS"), None, None),
             ("limit_down_vs_longhu", (limit_band.get("now_down") or {}).get("count"), mood.get("DTJS"), None, None))
    checks = []
    for name, ours, theirs, ok_at, warn_at in pairs:
        if ours is None or theirs is None:
            continue
        ours, theirs = int(ours), int(theirs)
        if ok_at is None:   # small counts: a few names apart is agreement
            gap = abs(ours - theirs)
            status = OK if gap <= max(3, theirs * 0.1) else WARN if gap <= max(6, theirs * 0.25) else FAIL
            value = gap
        else:
            value = round(abs(ours - theirs) / max(theirs, 1), 4)
            status = _grade(value, ok_at, warn_at, lower_is_better=True)
        checks.append(_check(name, status, value, "相对差" if ok_at else "只数差", f"雷达 {ours} vs 开盘啦 {theirs}"))
    return checks


#: A minute document past this size means the columnar encoding stopped working.
MAX_DOCUMENT_BYTES = 1_500_000


def minute_document_checks(connection: Any, trade_date: date, now: datetime) -> list[dict[str, Any]]:
    start, end = _day_bounds(trade_date)
    summary = _one(connection, """
        WITH documents AS (
            SELECT effective_at,(effective_at AT TIME ZONE 'Asia/Shanghai')::time AS clock,
                   pg_column_size(normalized) AS bytes
              FROM quant.raw_market_observations
             WHERE capability='a_share_minute_cross_section' AND symbol IS NULL AND provider_key='fuyao_ths'
               AND effective_at>=%s AND effective_at<%s),
        continuous AS (
            SELECT effective_at - lag(effective_at) OVER (PARTITION BY clock<'12:00' ORDER BY effective_at) AS gap
              FROM documents WHERE clock BETWEEN '09:31' AND '11:30' OR clock BETWEEN '13:01' AND '15:00')
        SELECT (SELECT count(*) FROM documents)::int AS documents, (SELECT max(bytes) FROM documents) AS max_bytes,
               (SELECT max(effective_at) FROM documents) AS last_at,
               (SELECT extract(epoch FROM max(gap)) FROM continuous) AS max_gap_seconds""", (start, end))
    if not summary.get("documents"):
        return [_check("present", MISSING, 0, ">0", "该日没有分钟文档（LEVEL1_STORAGE=per_symbol，或采集未运行）")]
    checks = [_check("present", OK, summary["documents"], ">0")]
    gap = summary.get("max_gap_seconds")
    checks.append(_check("continuity", WARN if gap is None else _grade(float(gap), FRESH_SECONDS, STALE_SECONDS,
                                                                        lower_is_better=True),
                         None if gap is None else round(float(gap)), f"<={FRESH_SECONDS}s", "连续竞价内相邻两份文档的最大间隔"))
    size = summary.get("max_bytes")
    checks.append(_check("size", _grade(float(size or 0), MAX_DOCUMENT_BYTES, MAX_DOCUMENT_BYTES * 2, lower_is_better=True),
                         size, f"<={MAX_DOCUMENT_BYTES}", "单份文档的存储字节（压缩后）"))
    return checks


def main_net_checks(connection: Any, trade_date: date, now: datetime) -> list[dict[str, Any]]:
    flow = latest_main_net(connection, trade_date)
    if not flow:
        return [_check("present", MISSING, 0, ">0", "该日没有带行业板块的资金流快照")]
    checks = [_check("present", OK, flow["boards"], ">0", f"来源 {flow.get('source')}（{flow.get('upstream')}）")]
    observed = datetime.fromisoformat(flow["observed_at"]) if isinstance(flow["observed_at"], str) else flow["observed_at"]
    if now.astimezone(CN_TZ).date() == trade_date and _in_continuous(now):
        age = (now - observed).total_seconds()
        checks.append(_check("fresh", _grade(age, 300, 900, lower_is_better=True), round(age), "<=300s"))
    checks.append(_check("boards", _grade(flow["boards"], 80, 50), flow["boards"], ">=80", "参与合计的行业板块数"))
    pool = ((latest_point(connection, trade_date) or {}).get("pool") or {}).get("turnover")
    share = abs(flow["main_net"]) / pool if pool else None
    checks.append(_check("plausible", _grade(share, MAX_MAIN_NET_SHARE, MAX_MAIN_NET_SHARE, lower_is_better=True),
                         None if share is None else round(share, 4), f"<={MAX_MAIN_NET_SHARE}",
                         "|主力净额|/全池成交额；超过即单位或口径错误"))
    return checks


def limit_detail_checks(connection: Any, trade_date: date, now: datetime) -> list[dict[str, Any]]:
    day = limit_detail_day(connection, trade_date)
    coverage = day["coverage"]
    longhu, xuangubao, both = coverage["longhu_review"], coverage["xuangubao_limit_up"], coverage["both"]
    if not longhu and not xuangubao:
        return [_check("present", MISSING, 0, ">0", "涨停复盘与选股宝涨停池都未归档")]
    checks = [_check("present", OK if longhu else WARN, longhu, ">0", "开盘啦涨停复盘逐股行数")]
    if not xuangubao:
        checks.append(_check("cross_source", WARN, 0, ">=0.8", "选股宝涨停池未归档，无法交叉核对"))
    else:
        overlap = both / max(longhu, xuangubao)
        checks.append(_check("cross_source", _grade(overlap, 0.8, 0.5), round(overlap, 4), ">=0.8", "两源共同覆盖的占比"))
    shared = [stock for stock in day["stocks"] if len(stock["sources"]) == 2]
    gaps = [stock["agreement"]["first_seal_gap_seconds"] for stock in shared
            if stock["agreement"]["first_seal_gap_seconds"] is not None]
    if gaps:
        close = sum(gap <= 60 for gap in gaps) / len(gaps)
        checks.append(_check("seal_time_agreement", _grade(close, 0.8, 0.5), round(close, 4), ">=0.8",
                             "两源首封时间相差不超过 60 秒的占比"))
    boards = [stock["agreement"]["board_count_equal"] for stock in shared if stock["agreement"]["board_count_equal"] is not None]
    if boards:
        equal = sum(boards) / len(boards)
        checks.append(_check("board_count_agreement", _grade(equal, 0.9, 0.7), round(equal, 4), ">=0.9", "两源连板数一致的占比"))
    sealed_now = (((latest_point(connection, trade_date) or {}).get("bands") or {}).get("limit") or {}).get("now_up", {}).get("count")
    if sealed_now is not None and longhu:
        gap = abs(sealed_now - longhu) / max(longhu, 1)
        checks.append(_check("radar_limit_count", _grade(gap, 0.1, 0.25, lower_is_better=True), round(gap, 4), "<=0.1",
                             f"复盘 {longhu} 只 vs 雷达收盘涨停带 {sealed_now} 只的相对差"))
    return checks


def _stored_reading(connection: Any, capability: str, day: date) -> dict[str, Any] | None:
    readings = derived_daily_readings.newest(connection, capability, day, day)
    return readings[-1] if readings else None


def temperature_checks(connection: Any, trade_date: date, _now: datetime) -> list[dict[str, Any]]:
    reading = _stored_reading(connection, TEMPERATURE_CAPABILITY, trade_date)
    if reading is None or reading.get("temperature") is None:
        return [_check("present", MISSING, None, "stored", "当日情绪温度未落库（盘后阶段 market_temperature）")]
    scored = sum(value is not None for value in (reading.get("scores") or {}).values())
    return [_check("present", OK, reading["temperature"], "stored"),
            _check("components", _grade(scored, 8, 6), scored, ">=8", "参与平均的分项数（共 8 项）")]


def broad_etf_flow_checks(connection: Any, trade_date: date, _now: datetime) -> list[dict[str, Any]]:
    reading = _stored_reading(connection, ETF_FLOW_CAPABILITY, trade_date)
    if reading is None or reading.get("ratio") is None:
        return [_check("present", MISSING, None, "stored", "当日宽基 ETF 放量比未落库（盘后阶段 broad_etf_flow）")]
    checks = [_check("present", OK, reading["ratio"], "stored"),
              _check("coverage", _grade(reading.get("codes"), 12, ETF_MIN_CODES), reading.get("codes"), ">=12",
                     "参与计算的宽基 ETF 数")]
    if reading.get("amount_estimated"):
        checks.append(_check("exact_turnover", WARN, True, "false", "有 ETF 的成交额由腾讯成交量估算（扶摇未取到）"))
    return checks


def timing_checks(connection: Any, trade_date: date, _now: datetime) -> list[dict[str, Any]]:
    reading = _stored_reading(connection, TIMING_CAPABILITY, trade_date)
    if reading is None:
        return [_check("present", MISSING, None, "stored", "当日金/银指状态未落库（盘后阶段 market_timing）")]
    return [_check("present", OK, reading.get("state"), "stored"),
            _check("source", OK if reading.get("source") == "fuyao_ths" else WARN, reading.get("source"), "fuyao_ths",
                   "腾讯是整段备用源")]


def _count_check(connection: Any, sql: str, params: tuple[Any, ...], ok_at: int, warn_at: int, detail: str) -> list[dict[str, Any]]:
    value = int(_one(connection, sql, params).get("n") or 0)
    if not value:
        return [_check("present", MISSING, 0, f">={ok_at}", detail)]
    return [_check("present", OK, value, ">0", detail), _check("coverage", _grade(value, ok_at, warn_at), value, f">={ok_at}", detail)]


def previous_session(connection: Any, trade_date: date) -> date | None:
    return _one(connection, "SELECT max(as_of_date) AS day FROM quant.strategy_daily_candidates WHERE as_of_date<%s",
                (trade_date,)).get("day")


def _session_checks(key: str) -> Callable[[Any, date, datetime], list[dict[str, Any]]]:
    def concept_strength(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        return _count_check(connection, """SELECT count(*)::int AS n FROM quant.sector_market_observations
                                            WHERE taxonomy_key='fuyao_ths_concept_limit_strength' AND trading_date=%s""",
                            (day,), 20, 5, "有涨停成员的概念数")

    def concept_flow(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        return _count_check(connection, """SELECT count(*)::int AS n FROM quant.sector_market_observations
                                            WHERE taxonomy_key='eastmoney_concept' AND trading_date=%s""",
                            (day,), 200, 50, "收盘窗口概念板块数")

    def industry_flow(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        return _count_check(connection, """SELECT count(*)::int AS n FROM quant.sector_market_observations
                                            WHERE taxonomy_key='longhu_ths_industry' AND trading_date=%s""",
                            (day,), 90, 50, "开盘啦行业板块数")

    def money_flow(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        value = stored_flow_symbols(connection, day)
        if not value:
            return [_check("present", MISSING, 0, f">={MIN_POOL}", "个股主力净额覆盖股票数")]
        return [_check("present", OK, value, ">0"), _check("coverage", _grade(value, MIN_POOL, WARN_POOL), value, f">={MIN_POOL}")]

    def limit_prices(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        tiered = tiered_sql_builder(connection)
        return _count_check(connection, tiered("""SELECT count(DISTINCT symbol)::int AS n FROM quant.daily_trade_limits
                                                   WHERE trading_date=%s AND limit_up IS NOT NULL"""),
                            (day,), MIN_POOL, WARN_POOL, "有涨跌停价的股票数")

    def ledger(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        as_of = previous_session(connection, day)
        if as_of is None:
            return [_check("present", MISSING, 0, ">0", "台账没有该日之前的候选")]
        return _count_check(connection, """SELECT count(DISTINCT strategy_key)::int AS n FROM quant.strategy_daily_candidates
                                            WHERE as_of_date=%s""", (as_of,), 8, 4, f"{as_of} 有候选的策略线数")

    def lhb(connection: Any, day: date, _now: datetime) -> list[dict[str, Any]]:
        as_of = previous_session(connection, day)
        if as_of is None:
            return [_check("present", MISSING, 0, ">0", "无前一交易日")]
        start, end = _day_bounds(as_of)
        return _count_check(connection, """SELECT count(DISTINCT symbol)::int AS n FROM quant.market_events
                                            WHERE event_type='lhb_ths' AND source='fuyao_ths'
                                              AND occurred_at>=%s AND occurred_at<%s""",
                            (start, end), 20, 1, f"{as_of} 上榜股票数")

    return {"board.concept_strength": concept_strength, "board.concept_flow": concept_flow,
            "board.industry_flow": industry_flow, "stock.money_flow": money_flow, "stock.limit_prices": limit_prices,
            "strategy.ledger": ledger, "events.lhb": lhb}[key]


CHECKS: dict[str, Callable[[Any, date, datetime], list[dict[str, Any]]]] = {
    "market.radar": radar_checks, "market.main_net": main_net_checks, "limits.detail": limit_detail_checks,
    "market.minute_documents": minute_document_checks, "market.temperature": temperature_checks,
    "market.broad_etf_flow": broad_etf_flow_checks, "market.timing": timing_checks,
    **{key: _session_checks(key) for key in ("board.concept_strength", "board.concept_flow", "board.industry_flow",
                                             "stock.money_flow", "stock.limit_prices", "strategy.ledger", "events.lhb")},
}
#: Derived indicators are as healthy as their inputs.
DERIVED = {"market.direction_gate": ("market.radar", "market.main_net"),
           "market.temperature_intraday": ("market.minute_documents", "stock.limit_prices")}


def indicator_status(connection: Any, key: str, trade_date: date, now: datetime) -> dict[str, Any]:
    indicator = BY_KEY[key]
    if key in DERIVED:
        inputs = [indicator_status(connection, item, trade_date, now) for item in DERIVED[key]]
        # As healthy as its least healthy input: a warning there is a warning here, not a failure.
        status = min((item["status"] for item in inputs), key=SEVERITY.index)
        checks = [_check(f"input:{item['key']}", item["status"]) for item in inputs]
    elif not _due(trade_date, now, indicator.availability):
        status, checks = PENDING, [_check("due", PENDING, None, indicator.availability, "尚未到该指标应产出的时点")]
    else:
        checks = CHECKS[key](connection, trade_date, now)
        statuses = {check["status"] for check in checks}
        status = FAIL if FAIL in statuses else MISSING if MISSING in statuses else WARN if WARN in statuses else OK
    return {"key": key, "label": indicator.label, "availability": indicator.availability, "status": status,
            "decision_eligible": status == OK, "checks": checks}


def indicator_health(connection: Any, trade_date: date, now: datetime, keys: list[str] | None = None) -> dict[str, Any]:
    wanted = keys or list(BY_KEY)
    unknown = [key for key in wanted if key not in BY_KEY]
    indicators = [indicator_status(connection, key, trade_date, now) for key in wanted if key in BY_KEY]
    counts: dict[str, int] = {}
    for item in indicators:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "trade_date": trade_date.isoformat(), "observed_at": now.isoformat(), "indicators": indicators,
        "summary": counts, "unknown_keys": unknown,
        "decision_eligible_keys": [item["key"] for item in indicators if item["decision_eligible"]],
        "rule": "所有检查均为 ok 才可作为决策输入；warn/fail/missing/pending 一律不可",
        "research_only": True, "live_effect": "none",
    }


__all__ = ["CHECKS", "DERIVED", "indicator_health", "indicator_status"]
