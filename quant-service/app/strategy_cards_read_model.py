"""Strategy cards: each strategy line's picks for a session, followed live.

A finer, reverse-engineered version of a vendor's 十路战法 panel. Its rules
cannot be recovered from a screenshot, but its fields can. The large
percentage on every card is the return from the open: 尚水智能 1.0824/0.9712-1
= +11.45%, 传艺科技 +4.84%, 泰诺麦博 +9.05%, each matching its card to
rounding. So each card here is one of our own lines from the daily candidate
ledger, with the same fields made explicit:

- **picks**: the ledger's candidates as of the previous session, with rank,
  native score and its scale, and liquidity screen;
- **per pick, for the session**:
  - the 09:25 auction match's change and turnover;
  - open, latest and return since open;
  - whether it sits at a limit;
  - the entry signals that fired today (出票) and the model that fired them;
- **themes**: the pick's 同花顺 concepts, strongest first by the previous
  session's concept limit strength, and the reason the 开盘啦 review gave if
  it sealed that session;
- **events**: the kinds of evidence held for it. These are a scheduled
  disclosure today, guidance or an express report in the last three days, a
  龙虎榜 seat, and yesterday's limit-up. They are counted, not rated.
- **resonance (共振)**: how many lines picked the same stock;
- **direction gate (总方向)**: from the market radar's latest point. The
  definition is ours and is returned with the gate.
- **leaderboard**: for each line over its last 5 and 20 ledger days, the mean
  next-session open-to-close return of its top picks and the share that rose.
  A pick that opened at its limit could not be bought, so it is counted
  apart.

Research evidence only: nothing here is an order, and no line gains live effect.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .indicator_health import indicator_status
from . import minute_cross_section
from .limit_detail_read_model import limit_detail_day
from .market_radar_runtime import latest_main_net, latest_point
from .owner_storage import tiered_sql_builder
from .platform.strategy_registry import STRATEGY_CONTRACTS
from .sector_membership_repository import point_in_time_membership_predicate, sector_group_predicate
from .strategy_daily_candidate_ledger import candidate_direction
from .xiaojie_indicators import snapshot_fields

CN_TZ = ZoneInfo("Asia/Shanghai")
CARDS_VERSION = "strategy-cards-v1"
#: Ledger line -> (card name, registered contract, style).
LINES: dict[str, tuple[str, str | None, str]] = {
    "post_close_base_ready": ("底部蓄势·就绪", "post_close_base_candidates", "低吸"),
    "post_close_base_forming": ("底部蓄势·成形", "post_close_base_candidates", "低吸"),
    "post_close_fresh_start": ("新起点", "post_close_base_candidates", "启动"),
    "post_close_limit_pattern": ("涨停分时形态", "post_close_limit_lift_pattern", "接力"),
    "ten_day_leader_rotation": ("十日龙头轮动", "ten_day_leader_rotation_shadow", "龙头"),
    "limit_linkage": ("涨停联动", None, "联动"),
    "board_stock_mining_inflow": ("板块资金·流入", "board_flow_drill", "资金"),
    "board_stock_mining_outflow": ("板块资金·流出（回避）", "board_flow_drill", "回避"),
    "daily_recommendation": ("每日推荐", None, "综合"),
    "teacher_review_relay": ("老师复盘·接力", "teacher_review_playbooks", "接力"),
    "teacher_review_trend": ("老师复盘·趋势", "teacher_review_playbooks", "趋势"),
    "xiaojie_leader_flow": ("小杰龙头资金", "xiaojie_leader_flow", "龙头"),
    "launch_radar": ("启动雷达", "launch_radar", "启动"),
}
BENCHMARK = "000001.SH"
SNAPSHOT_CAPABILITY = "a_share_prices_snapshot"
AUCTION_FROM, AUCTION_TO = time(9, 25), time(9, 30)
TICKET_STATES = ("confirmed", "alerted")
WINDOWS = (5, 20)
#: Direction gate: now-band turnover ratio beyond which one side leads.
GATE_RATIO = 1.2
LIMIT_TOLERANCE = 0.005


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), CN_TZ)
    return start, start + timedelta(days=1)


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round((numerator / denominator - 1) * 100, 4)


def _rows(connection: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def picks_as_of(connection: Any, trade_date: date) -> date | None:
    row = connection.execute(
        "SELECT max(as_of_date) AS as_of FROM quant.strategy_daily_candidates WHERE as_of_date<%s", (trade_date,),
    ).fetchone()
    return dict(row)["as_of"] if row else None


def ledger_picks(connection: Any, as_of: date) -> list[dict[str, Any]]:
    return _rows(connection, """
        SELECT strategy_key,symbol,rank,raw_score,score_scale,liquidity_eligible,liquidity_flags,evidence
          FROM quant.strategy_daily_candidates WHERE as_of_date=%s
         ORDER BY strategy_key,rank NULLS LAST,raw_score DESC NULLS LAST,symbol""", (as_of,))


def _document_quotes(connection: Any, trade_date: date, symbols: list[str]) -> dict[str, dict[str, Any]] | None:
    """The same fields from the minute documents (decision 0009); None for a day stored only per symbol."""
    latest = minute_cross_section.latest(connection, trade_date)
    if latest is None:
        return None
    quotes: dict[str, dict[str, Any]] = {}
    for symbol, row in minute_cross_section.rows_for(latest[1], symbols).items():
        quotes[symbol] = {**snapshot_fields(row), "observed_at": latest[0].isoformat()}
    auction = minute_cross_section.first_between(connection, datetime.combine(trade_date, AUCTION_FROM, CN_TZ),
                                                 datetime.combine(trade_date, AUCTION_TO, CN_TZ))
    if auction is not None:
        for symbol, row in minute_cross_section.rows_for(auction[1], symbols).items():
            fields = snapshot_fields(row)
            quotes.setdefault(symbol, {})["auction"] = {
                "pct_change": fields["pct_change"], "turnover": fields["turnover"], "observed_at": auction[0].isoformat()}
    return quotes


def session_quotes(connection: Any, trade_date: date, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Latest and 09:25-match snapshot fields per symbol for the session."""
    from_documents = _document_quotes(connection, trade_date, symbols)
    if from_documents is not None:
        return from_documents
    start, end = _day_bounds(trade_date)
    auction_from = datetime.combine(trade_date, AUCTION_FROM, CN_TZ)
    auction_to = datetime.combine(trade_date, AUCTION_TO, CN_TZ)
    # One index probe per symbol (capability, symbol, effective_at): the day
    # holds a snapshot a minute for every A share.
    query = """SELECT wanted.symbol,observation.effective_at,observation.normalized
                 FROM unnest(%s::text[]) AS wanted(symbol)
                 JOIN LATERAL (SELECT effective_at,normalized FROM quant.raw_market_observations
                                WHERE capability=%s AND symbol=wanted.symbol AND effective_at>=%s AND effective_at<%s
                                ORDER BY effective_at {order} LIMIT 1) observation ON true"""
    latest = _rows(connection, query.format(order="DESC"), (symbols, SNAPSHOT_CAPABILITY, start, end))
    auction = _rows(connection, query.format(order="ASC"), (symbols, SNAPSHOT_CAPABILITY, auction_from, auction_to))
    quotes: dict[str, dict[str, Any]] = {}
    for row in latest:
        fields = snapshot_fields(_json(row["normalized"]) or {})
        quotes[row["symbol"]] = {**fields, "observed_at": row["effective_at"].isoformat()}
    for row in auction:
        fields = snapshot_fields(_json(row["normalized"]) or {})
        quotes.setdefault(row["symbol"], {})["auction"] = {
            "pct_change": fields["pct_change"], "turnover": fields["turnover"],
            "observed_at": row["effective_at"].isoformat()}
    return quotes


def session_limits(connection: Any, day: date, symbols: list[str], tiered: Any) -> dict[str, tuple[float | None, float | None]]:
    rows = _rows(connection, tiered("""
        SELECT DISTINCT ON (symbol) symbol,limit_up,limit_down FROM quant.daily_trade_limits
         WHERE trading_date=%s AND symbol=ANY(%s) AND limit_up IS NOT NULL ORDER BY symbol,provider"""), (day, symbols))
    return {row["symbol"]: (_float(row["limit_up"]), _float(row["limit_down"])) for row in rows}


def entry_signals(connection: Any, trade_date: date, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    start, end = _day_bounds(trade_date)
    signals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(connection, """
            SELECT symbol,signal_key,state,score,observed_at FROM quant.intraday_signal_events
             WHERE symbol=ANY(%s) AND signal_type='entry' AND observed_at>=%s AND observed_at<%s
             ORDER BY observed_at""", (symbols, start, end)):
        signals[row["symbol"]].append({
            "model": str(row["signal_key"]).split(":", 2)[-1], "state": row["state"],
            "score": _float(row["score"]), "observed_at": row["observed_at"].isoformat()})
    return signals


def concept_themes(connection: Any, as_of: date, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Each symbol's 同花顺 concepts as known at ``as_of``, strongest that session first."""
    group_sql, group_params = sector_group_predicate("member")
    members = _rows(connection, f"""
        SELECT member.symbol,member.sector_key,coalesce(sector.label,member.sector_key) AS label
          FROM quant.sector_membership_history member
          LEFT JOIN quant.sectors sector ON sector.taxonomy_key=member.taxonomy_key AND sector.sector_key=member.sector_key
         WHERE member.taxonomy_key='fuyao_ths_concept' AND member.symbol=ANY(%s)
           AND {point_in_time_membership_predicate("member")} AND {group_sql}""",
        (symbols, as_of, as_of, as_of, *group_params))
    strength = {row["sector_key"]: int(row["strength"] or 0) for row in _rows(connection, """
        SELECT sector_key,jsonb_array_length(coalesce(raw->'limit_up_symbols','[]'::jsonb)) AS strength
          FROM quant.sector_market_observations
         WHERE taxonomy_key='fuyao_ths_concept_limit_strength' AND trading_date=%s""", (as_of,))}
    themes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in members:
        themes[row["symbol"]].append({"code": row["sector_key"], "label": row["label"],
                                      "limit_up_members": strength.get(row["sector_key"], 0)})
    for items in themes.values():
        items.sort(key=lambda item: (-item["limit_up_members"], item["label"]))
    return themes


def symbol_events(connection: Any, trade_date: date, as_of: date, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(connection, """
            SELECT DISTINCT symbol,period FROM quant.disclosure_schedule
             WHERE symbol=ANY(%s) AND coalesce(actual_date,modify_date,pre_date)=%s""", (symbols, trade_date)):
        events[row["symbol"]].append({"kind": "disclosure_due", "label": f"定期报告披露日（{row['period']}）"})
    since = trade_date - timedelta(days=3)
    for table, kind, label in (("earnings_forecasts", "earnings_forecast", "业绩预告"),
                               ("earnings_express", "earnings_express", "业绩快报")):
        for row in _rows(connection, f"""
                SELECT DISTINCT ON (symbol) symbol,ann_date FROM quant.{table}
                 WHERE symbol=ANY(%s) AND ann_date>=%s AND ann_date<=%s ORDER BY symbol,ann_date DESC""",
                (symbols, since, trade_date)):
            events[row["symbol"]].append({"kind": kind, "label": f"{label}（{row['ann_date']}）"})
    start, end = _day_bounds(as_of)
    for row in _rows(connection, """
            SELECT DISTINCT symbol FROM quant.market_events
             WHERE event_type='lhb_ths' AND source='fuyao_ths' AND symbol=ANY(%s)
               AND occurred_at>=%s AND occurred_at<%s""", (symbols, start, end)):
        events[row["symbol"]].append({"kind": "lhb", "label": f"龙虎榜（{as_of}）"})
    return events


def direction_gate(connection: Any, trade_date: date) -> dict[str, Any]:
    point, flow = latest_point(connection, trade_date), latest_main_net(connection, trade_date)
    band = ((point or {}).get("bands") or {}).get("2") or {}
    up = _float((band.get("now_up") or {}).get("turnover"))
    down = _float((band.get("now_down") or {}).get("turnover"))
    main_net = _float((flow or {}).get("main_net"))
    ratio = round(up / down, 4) if up is not None and down else None
    if ratio is None:
        label = "unknown"
    elif ratio >= GATE_RATIO and (main_net is None or main_net >= 0):
        label = "up"
    elif ratio <= 1 / GATE_RATIO and (main_net is None or main_net <= 0):
        label = "down"
    else:
        label = "mixed"
    return {
        "label": label, "now_up_turnover": up, "now_down_turnover": down, "up_down_ratio": ratio,
        "main_net": main_net, "main_net_source": (flow or {}).get("source"), "main_net_upstream": (flow or {}).get("upstream"),
        "radar_observed_at": (point or {}).get("observed_at"), "phase": (point or {}).get("phase"),
        "definition": (f"up：±2% 带“此刻在带外”的成交额 上/下 ≥ {GATE_RATIO} 且行业净额不为负；"
                       f"down：比值 ≤ 1/{GATE_RATIO} 且净额不为正；否则 mixed（我方定义，非原图口径）"),
    }


def leaderboard(connection: Any, as_of: date, per_line: int, tiered: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Each line's top picks over its last settled ledger days, scored on the next session's open-to-close.

    Only ledger days before ``as_of`` count: their next session is ``as_of``
    at the latest, so its bar exists, while ``as_of``'s own picks are the
    ones the cards are following live.
    """
    rows = _rows(connection, tiered("""
        WITH days AS (
            SELECT DISTINCT as_of_date FROM quant.strategy_daily_candidates
             WHERE as_of_date<%s ORDER BY as_of_date DESC LIMIT %s),
        sessions AS (
            SELECT days.as_of_date,(SELECT min(b.trading_date) FROM quant.canonical_bars_daily b
                                     WHERE b.symbol=%s AND b.trading_date>days.as_of_date) AS next_session
              FROM days),
        ranked AS (
            SELECT c.strategy_key,c.as_of_date,c.symbol,
                   row_number() OVER (PARTITION BY c.strategy_key,c.as_of_date
                                      ORDER BY c.rank NULLS LAST,c.raw_score DESC NULLS LAST,c.symbol) AS pick_rank
              FROM quant.strategy_daily_candidates c JOIN days USING (as_of_date))
        SELECT ranked.strategy_key,ranked.as_of_date,ranked.symbol,sessions.next_session,bar.open,bar.close,
               (SELECT max(limits.limit_up) FROM quant.daily_trade_limits limits
                 WHERE limits.symbol=ranked.symbol AND limits.trading_date=sessions.next_session) AS limit_up
          FROM ranked JOIN sessions USING (as_of_date)
          LEFT JOIN quant.canonical_bars_daily bar
                 ON bar.symbol=ranked.symbol AND bar.trading_date=sessions.next_session AND bar.quality_status='fresh'
         WHERE ranked.pick_rank<=%s"""), (as_of, max(WINDOWS), BENCHMARK, per_line))
    days = sorted({row["as_of_date"] for row in rows}, reverse=True)
    board: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for window in WINDOWS:
        in_window = set(days[:window])
        by_line: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["as_of_date"] in in_window:
                by_line[row["strategy_key"]].append(row)
        for line, items in by_line.items():
            sign = candidate_direction(line, None)
            returns, unbuyable, missing = [], 0, 0
            for item in items:
                open_, close, limit_up = _float(item["open"]), _float(item["close"]), _float(item["limit_up"])
                if open_ is None or close is None or not open_:
                    missing += 1
                elif limit_up is not None and open_ >= limit_up - LIMIT_TOLERANCE:
                    unbuyable += 1
                else:
                    returns.append(sign * (close / open_ - 1) * 100)
            board[line][str(window)] = {
                "days": len({item["as_of_date"] for item in items}), "picks": len(items), "scored": len(returns),
                "mean_open_to_close_pct": round(sum(returns) / len(returns), 4) if returns else None,
                "rose_share": round(sum(value > 0 for value in returns) / len(returns), 4) if returns else None,
                "opened_at_limit": unbuyable, "without_bar": missing,
            }
    return board


def _pick(row: Mapping[str, Any], quote: Mapping[str, Any] | None, limits: tuple[float | None, float | None] | None,
          *, names: Mapping[str, str], resonance: Mapping[str, list[str]], signals: Mapping[str, list[dict[str, Any]]],
          themes: Mapping[str, list[dict[str, Any]]], events: Mapping[str, list[dict[str, Any]]],
          review: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    symbol = row["symbol"]
    quote = quote or {}
    price, open_, prev_close = quote.get("price"), quote.get("open"), quote.get("prev_close")
    up, down = limits or (None, None)
    sealed = review.get(symbol)
    own_events = list(events.get(symbol, []))
    if sealed:
        own_events.append({"kind": "limit_up", "label": f"昨日涨停：{sealed.get('theme') or sealed.get('reason') or ''}".rstrip("：")})
    fired = signals.get(symbol, [])
    return {
        "symbol": symbol, "name": names.get(symbol), "rank": row["rank"], "score": _float(row["raw_score"]),
        "score_scale": row["score_scale"], "liquidity_eligible": row["liquidity_eligible"],
        "auction": quote.get("auction"),
        "open_pct": _pct(open_, prev_close), "latest_pct": quote.get("pct_change"),
        "since_open_pct": _pct(price, open_), "price": price, "observed_at": quote.get("observed_at"),
        "at_limit_up": price is not None and up is not None and price >= up - LIMIT_TOLERANCE,
        "at_limit_down": price is not None and down is not None and price <= down + LIMIT_TOLERANCE,
        "opened_at_limit_up": open_ is not None and up is not None and open_ >= up - LIMIT_TOLERANCE,
        "ticket": any(item["state"] in TICKET_STATES for item in fired), "entry_signals": fired,
        "themes": themes.get(symbol, [])[:3],
        "limit_review": {key: sealed.get(key) for key in ("theme", "reason", "board_count", "first_limit_up_at")} if sealed else None,
        "events": own_events, "event_count": len({item["kind"] for item in own_events}),
        "resonance": len(resonance.get(symbol, [])), "resonance_lines": resonance.get(symbol, []),
        "evidence": _json(row["evidence"]),
    }


def _card_summary(picks: list[dict[str, Any]], direction: int) -> dict[str, Any]:
    tradable = [pick for pick in picks if pick["since_open_pct"] is not None and not pick["opened_at_limit_up"]]
    returns = [direction * pick["since_open_pct"] for pick in tradable]
    best = max(tradable, key=lambda pick: direction * pick["since_open_pct"], default=None)
    return {
        "picks": len(picks), "quoted": sum(pick["price"] is not None for pick in picks), "scored": len(returns),
        "mean_since_open_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "rose_share": round(sum(value > 0 for value in returns) / len(returns), 4) if returns else None,
        "tickets": sum(pick["ticket"] for pick in picks),
        "best": {"symbol": best["symbol"], "name": best["name"], "since_open_pct": best["since_open_pct"]} if best else None,
    }


#: The indicators each card field rests on, and the session each is checked for.
FIELD_INPUTS: dict[str, tuple[str, str]] = {
    "since_open/auction/latest": ("market.radar", "trade_date"),
    "limit flags": ("stock.limit_prices", "trade_date"),
    "direction_gate": ("market.direction_gate", "trade_date"),
    "picks": ("strategy.ledger", "trade_date"),
    "themes": ("board.concept_strength", "as_of"),
    "events": ("events.lhb", "trade_date"),
}


def data_health(connection: Any, trade_date: date, as_of: date, now: datetime) -> dict[str, Any]:
    """Whether the indicators behind each card field may be used for a decision now."""
    fields = {}
    for field, (key, session) in FIELD_INPUTS.items():
        status = indicator_status(connection, key, as_of if session == "as_of" else trade_date, now)
        fields[field] = {"indicator": key, "status": status["status"], "decision_eligible": status["decision_eligible"]}
    return {"fields": fields, "all_eligible": all(item["decision_eligible"] for item in fields.values())}


def strategy_cards(connection: Any, trade_date: date, *, per_line: int = 10, now: datetime | None = None) -> dict[str, Any]:
    as_of = picks_as_of(connection, trade_date)
    base = {"trade_date": trade_date.isoformat(), "cards_version": CARDS_VERSION,
            "research_only": True, "live_effect": "none"}
    if as_of is None:
        return {**base, "status": "missing", "reason": "no ledger candidates before the session", "cards": []}
    tiered = tiered_sql_builder(connection)
    ledger = ledger_picks(connection, as_of)
    resonance: dict[str, list[str]] = defaultdict(list)
    by_line: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ledger:
        resonance[row["symbol"]].append(row["strategy_key"])
        if len(by_line[row["strategy_key"]]) < per_line:
            by_line[row["strategy_key"]].append(row)
    shown = sorted({row["symbol"] for rows in by_line.values() for row in rows})
    names = {row["symbol"]: row["name"] for row in _rows(
        connection, "SELECT symbol,name FROM quant.instruments WHERE symbol=ANY(%s)", (shown,))}
    quotes = session_quotes(connection, trade_date, shown)
    limits = session_limits(connection, trade_date, shown, tiered)
    context = {
        "names": names, "resonance": resonance, "signals": entry_signals(connection, trade_date, shown),
        "themes": concept_themes(connection, as_of, shown),
        "events": symbol_events(connection, trade_date, as_of, shown),
        "review": {stock["symbol"]: stock for stock in limit_detail_day(connection, as_of)["stocks"]},
    }
    board = leaderboard(connection, as_of, per_line, tiered)
    cards = []
    for line in sorted(by_line, key=lambda key: (list(LINES).index(key) if key in LINES else len(LINES), key)):
        name, contract_key, style = LINES.get(line, (line, None, ""))
        contract = STRATEGY_CONTRACTS.get(contract_key) if contract_key else None
        direction = candidate_direction(line, None)
        picks = [_pick(row, quotes.get(row["symbol"]), limits.get(row["symbol"]), **context) for row in by_line[line]]
        cards.append({
            "strategy_key": line, "name": name, "style": style, "direction": "short" if direction < 0 else "long",
            "contract_key": contract_key, "model_version": contract.model_version if contract else None,
            "maturity": contract.maturity if contract else "ledger_only",
            "candidates": sum(row["strategy_key"] == line for row in ledger),
            "summary": _card_summary(picks, direction), "picks": picks, "leaderboard": board.get(line, {}),
        })
    ranking = {str(window): sorted(
        ({"strategy_key": card["strategy_key"], "name": card["name"], **card["leaderboard"][str(window)]}
         for card in cards if card["leaderboard"].get(str(window), {}).get("mean_open_to_close_pct") is not None),
        key=lambda item: -item["mean_open_to_close_pct"]) for window in WINDOWS}
    regime = connection.execute(
        "SELECT regime_label,model_version FROM quant.market_regime_daily WHERE trading_date=%s", (as_of,)).fetchone()
    return {
        **base, "status": "completed" if cards else "missing", "picks_as_of": as_of.isoformat(),
        "per_line": per_line, "direction_gate": direction_gate(connection, trade_date),
        "data_health": data_health(connection, trade_date, as_of, now or datetime.now(CN_TZ)),
        "previous_close_regime": dict(regime) if regime else None,
        "cards": cards, "leaderboard": ranking,
        "definitions": {
            "since_open_pct": "现价/今日开盘价-1（原图大号百分比的口径）；开盘即涨停的票买不进，不计入卡片均值",
            "ticket": "今日该股出现 confirmed/alerted 的 entry 信号（model 为触发它的模型版本）",
            "resonance": "前一交易日台账中选中该股的策略线条数",
            "event_count": "我方掌握的事件种类数：今日定期报告披露、近三日业绩预告/快报、昨日龙虎榜、昨日涨停（不是原图的星级）",
            "themes": "同花顺概念（Fuyao 成分，按前一交易日概念涨停强度排序），剔除融资融券等资格类分组",
            "leaderboard": "各线最近 5/20 个台账日的前 N 名，次日开盘买入、收盘计的收益均值与上涨占比；流出线按做空方向计",
        },
    }


__all__ = ["CARDS_VERSION", "FIELD_INPUTS", "LINES", "data_health", "direction_gate", "leaderboard", "strategy_cards"]
