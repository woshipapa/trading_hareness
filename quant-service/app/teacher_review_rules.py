"""Intraday evaluation of teacher-review plans inside the watchlist scan.

Pure and replayable: the only inputs are the frozen watch row (whose
``metadata.teacher_review`` carries the pre-session plan), the scan's merged
quote (with the raw Longhu row), the causal minute features, the peer context
and the scan clock.  Outputs are ordinary intraday signal candidates tagged
``policy_profile="teacher_review"``; they never size or route an order.

Only ``entry`` and ``invalid`` transitions produce candidates.  A persisting
condition is re-emitted every scan so the existing state machine confirms it
on the second observation and then de-duplicates it; the top-level
conditions intentionally omit price/volume keys so a moving price does not
re-alert the same unchanged setup.  A fresh alert therefore means the setup
lapsed for longer than the confirmation window and triggered again.

Scan-time approximations of the teacher's minute language (all ``I``):
分时放量 = minute volume multiple ≥ 2, or volume ratio ≥ 1.5 with a positive
5-minute return; 不能往下跌 = 5-minute return ≥ -0.3%; 竞价额 = cumulative
turnover observed before 09:31; 封板时成交 = cumulative turnover on the scan
that first sees a sealed bid-only book.

The minute-style values come from :class:`SnapshotTape` -- the list quotes
every scan already pulls (Longhu basket, Tencent batch, all-A snapshot),
kept per symbol -- so a plan needs no per-stock minute request.  Per-stock
minute features, when some other strategy fetched them, still take priority.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .teacher_review_playbooks import DEFAULTS, playbook_kind
from .teacher_review_plan import divergence_summary, limit_up_price

_CN_TZ = ZoneInfo("Asia/Shanghai")
MODEL_VERSION = "teacher-review-rules-v3"


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _yi(amount: float) -> str:
    return f"{amount / 1e8:g}亿"


def active_plan(watch: Mapping[str, Any], observed_at: datetime) -> dict[str, Any] | None:
    """Return the plan only on its own session date; anything else is inert."""
    metadata = watch.get("metadata") if isinstance(watch.get("metadata"), Mapping) else {}
    plan = metadata.get("teacher_review")
    if not isinstance(plan, Mapping) or plan.get("status", "active") != "active":
        return None
    if str(plan.get("session_date") or "") != observed_at.astimezone(_CN_TZ).date().isoformat():
        return None
    if playbook_kind(str(plan.get("playbook") or "")) == "record":
        return None
    return dict(plan)


#: Inputs each playbook's gating conditions need.  A missing one makes the
#: plan un-evaluable for that scan and is reported, never treated as a pass.
REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "relay_one_word": ("pre_close", "turnover_pct", "book"),
    "relay_acceleration": ("pre_close", "amount", "vwap"),
    "relay_expect_touch": ("pre_close", "vwap", "surge"),
    "relay_race": ("pre_close", "open", "vwap", "surge"),
    "relay_news_conditional": ("pre_close", "open", "vwap", "surge"),
    "relay_fast_seal": ("pre_close", "open", "amount", "book"),
    "trend_pullback_restart": ("pre_close", "volume_ratio", "vwap"),
    "leader_benchmark_pullback": ("low",),
    "sympathy_follow": ("pre_close", "vwap", "surge"),
    "ma5_reclaim_or_divergence": ("volume_ratio", "vwap", "not_falling", "low"),
    "trend_continuation": ("high",),
    "prior_high_breakout": ("volume_ratio", "vwap", "not_falling"),
    "platform_breakout": ("volume_ratio", "vwap", "not_falling"),
    "ma10_second_wave": ("high", "low", "vwap"),
    "double_bottom_platform": ("volume_ratio", "vwap", "not_falling"),
    "ma60_reclaim": ("amount",),
}


class SnapshotTape:
    """Minute-equivalent values rebuilt from each scan's list quotes.

    One sample per symbol per scan: price and cumulative volume (lots) with
    the volume's source.  Volumes are differenced only between samples of the
    same source, because providers use different units.  The tape keeps 31
    minutes, resets on a new exchange day and lives in process memory: after
    a restart it is empty and the plan falls back to the previous scan price
    and the quote's volume ratio until enough samples exist.
    """

    WINDOW = timedelta(minutes=31)
    RETURN_SECONDS = 300
    CURRENT_SECONDS = 60
    MIN_BASELINE_MINUTES = 5

    def __init__(self) -> None:
        self._day: date | None = None
        self._samples: dict[str, deque[tuple[datetime, float, float | None, str | None]]] = {}
        self._latest: dict[str, tuple[datetime, dict[str, Any]]] = {}

    def observe(self, symbol: str, observed_at: datetime, price: float | None,
                volume_lot: float | None, volume_source: str | None) -> None:
        if price is None or price <= 0:
            return
        day = observed_at.astimezone(_CN_TZ).date()
        if day != self._day:
            self._day, self._samples, self._latest = day, {}, {}
        ring = self._samples.setdefault(symbol, deque())
        if ring and observed_at <= ring[-1][0]:
            return
        ring.append((observed_at, float(price), volume_lot, volume_source))
        while observed_at - ring[0][0] > self.WINDOW:
            ring.popleft()

    def note_latest(self, symbol: str, observed_at: datetime, summary: Mapping[str, Any]) -> None:
        """Latest per-scan summary of a symbol, for plans that watch another plan's stock."""
        if observed_at.astimezone(_CN_TZ).date() != self._day:
            return
        self._latest[symbol] = (observed_at, dict(summary))

    def latest(self, symbol: str, observed_at: datetime, max_age_seconds: float = 120.0) -> dict[str, Any] | None:
        entry = self._latest.get(symbol) if self._day == observed_at.astimezone(_CN_TZ).date() else None
        if not entry or (observed_at - entry[0]).total_seconds() > max_age_seconds:
            return None
        return {**entry[1], "observed_at": entry[0].isoformat()}

    def features(self, symbol: str, observed_at: datetime) -> dict[str, Any]:
        if self._day != observed_at.astimezone(_CN_TZ).date():
            return {}
        ring = self._samples.get(symbol)
        if not ring:
            return {}
        latest_at, latest_price, latest_volume, source = ring[-1]
        result: dict[str, Any] = {"samples": len(ring), "span_seconds": int((latest_at - ring[0][0]).total_seconds())}
        anchor = None
        for sample in ring:
            if (latest_at - sample[0]).total_seconds() < self.RETURN_SECONDS:
                break
            anchor = sample
        if anchor is not None:
            result["return_5m_pct"] = round((latest_price / anchor[1] - 1) * 100, 4)
        same = [(at, volume) for at, _price, volume, src in ring if src == source and volume is not None]
        if latest_volume is None or len(same) < 2:
            return result
        start = None
        for at, volume in same:
            if (latest_at - at).total_seconds() < self.CURRENT_SECONDS:
                break
            start = (at, volume)
        if start is None or latest_volume < start[1]:
            return result
        current = (latest_volume - start[1]) * 60 / (latest_at - start[0]).total_seconds()
        # Per-minute volumes before the current window: last cumulative value
        # in each clock minute, differenced and spread over skipped minutes.
        closes: dict[datetime, float] = {}
        for at, volume in same:
            if at <= start[0]:
                closes[at.replace(second=0, microsecond=0)] = volume
        minutes = sorted(closes.items())
        per_minute = [
            (right - left) / ((later - earlier).total_seconds() / 60)
            for (earlier, left), (later, right) in zip(minutes, minutes[1:])
            if right >= left
        ]
        if len(per_minute) >= self.MIN_BASELINE_MINUTES:
            baseline = median(per_minute)
            if baseline > 0:
                result["minute_volume_multiple"] = round(current / baseline, 4)
        return result


#: Teacher sector names -> limit-up reason keywords (Fuyao limit-up pool ``limit_up_reason``).
SECTOR_PATTERNS: dict[str, str] = {
    "大金融": r"券商|证券|保险|期货|金融|银行|信托|支付|数字货币",
    "房地产": r"地产|房地产|物业|楼市|住房|城中村|城市更新|REITs|保障房",
}


def count_sector_limit_ups(rows: list[Mapping[str, Any]], sectors: list[str]) -> dict[str, dict[str, Any]]:
    """Limit-up count per teacher sector from one pool snapshot (reason keyword match)."""
    result: dict[str, dict[str, Any]] = {}
    for sector in sectors:
        pattern = re.compile(SECTOR_PATTERNS.get(sector) or re.escape(sector))
        names = sorted({str(row.get("name") or row.get("symbol")) for row in rows
                        if pattern.search(str(row.get("limit_up_reason") or ""))})
        result[sector] = {"count": len(names), "names": names[:12]}
    return result


class TeacherMarketBook:
    """Same-day market context the teacher rules read: 09:25 auction and sector limit-up counts.

    The scan refreshes it from the data plane (Fuyao auction snapshot, the
    stored limit-up pool); process memory, reset per exchange day.
    """

    SECTOR_MAX_AGE_SECONDS = 600.0

    def __init__(self) -> None:
        self._day: date | None = None
        self._auction: dict[str, dict[str, Any]] = {}
        self._sectors: dict[str, dict[str, Any]] = {}
        self._sectors_at: datetime | None = None
        self.sector_refreshed_at: datetime | None = None

    def _roll(self, observed_at: datetime) -> None:
        day = observed_at.astimezone(_CN_TZ).date()
        if day != self._day:
            self._day, self._auction, self._sectors = day, {}, {}
            self._sectors_at = self.sector_refreshed_at = None

    def auction_missing(self, symbols: list[str], observed_at: datetime) -> list[str]:
        self._roll(observed_at)
        return [symbol for symbol in symbols if not (self._auction.get(symbol) or {}).get("final")]

    def store_auction(self, symbol: str, observed_at: datetime, row: Mapping[str, Any]) -> None:
        self._roll(observed_at)
        self._auction[symbol] = dict(row)

    def auction(self, symbol: str, observed_at: datetime) -> dict[str, Any] | None:
        self._roll(observed_at)
        return self._auction.get(symbol)

    def store_sectors(self, observed_at: datetime, snapshot_at: datetime | None,
                      counts: Mapping[str, Mapping[str, Any]]) -> None:
        self._roll(observed_at)
        self._sectors, self._sectors_at, self.sector_refreshed_at = dict(counts), snapshot_at, observed_at

    def sector_counts(self, observed_at: datetime) -> dict[str, int]:
        self._roll(observed_at)
        if self._sectors_at is None or (observed_at - self._sectors_at).total_seconds() > self.SECTOR_MAX_AGE_SECONDS:
            return {}
        return {sector: int(value.get("count") or 0) for sector, value in self._sectors.items()}


class PeriodDivergenceBook:
    """Today's 30/60-minute divergence per symbol, refreshed during the session.

    The teacher's "回踩到位出现 30/60 分钟两段底背离" usually completes
    intraday, so the pre-session result alone would miss it.  The scan
    refreshes the data-plane period K-line (history plus the forming bar) for
    the few symbols with a divergence plan at most once a minute; the rules
    read the latest result and fall back to the frozen pre-session one.
    """

    REFRESH_SECONDS = 60.0

    def __init__(self) -> None:
        self._day: date | None = None
        self._entries: dict[str, tuple[datetime, dict[str, Any]]] = {}

    def _roll(self, observed_at: datetime) -> date:
        day = observed_at.astimezone(_CN_TZ).date()
        if day != self._day:
            self._day, self._entries = day, {}
        return day

    def due(self, symbols: list[str], observed_at: datetime) -> list[str]:
        self._roll(observed_at)
        return [symbol for symbol in symbols
                if symbol not in self._entries
                or (observed_at - self._entries[symbol][0]).total_seconds() >= self.REFRESH_SECONDS]

    def store(self, symbol: str, observed_at: datetime, divergence: Mapping[str, Any]) -> None:
        self._roll(observed_at)
        self._entries[symbol] = (observed_at, dict(divergence))

    def get(self, symbol: str, observed_at: datetime) -> dict[str, Any] | None:
        if self._day != observed_at.astimezone(_CN_TZ).date():
            return None
        entry = self._entries.get(symbol)
        return {**entry[1], "refreshed_at": entry[0].isoformat()} if entry else None


def divergence_plan_symbols(watches: list[Mapping[str, Any]], observed_at: datetime) -> list[str]:
    """Watches whose plan for this session needs a 30/60-minute divergence."""
    result = []
    for watch in watches:
        plan = active_plan(watch, observed_at)
        if plan is not None and plan.get("playbook") == "ma5_reclaim_or_divergence":
            result.append(str(watch["symbol"]).upper())
    return result


def _divergence_text(divergence: Mapping[str, Any] | None) -> str:
    parts = []
    for period in ("30", "60"):
        value = (divergence or {}).get(period)
        if isinstance(value, bool):
            parts.append(f"{period}分:{'是' if value else '否'}")
        elif isinstance(value, Mapping):
            verdict = ("是" if value.get("found") else "否") if value.get("status") == "ok" else "数据不足"
            parts.append(f"{period}分:{verdict}({value.get('bars', 0)}根)")
    return " ".join(parts) or "—"


def _pick(*candidates: tuple[str, Any]) -> tuple[float | None, str | None]:
    for source, value in candidates:
        number = _num(value)
        if number is not None and number > 0:
            return number, source
    return None, None


def _tencent_field(row: Mapping[str, Any], index: int) -> Any:
    fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), list) else []
    return fields[index] if len(fields) > index else None


def scan_features(symbol: str, quote: Mapping[str, Any], minute: Mapping[str, Any] | None,
                  observed_at: datetime, name: str = "",
                  previous_quote: Mapping[str, Any] | None = None,
                  tape: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Collect every value the playbooks read, from whichever scan source has it.

    Order: licensed watch quote (Longhu) -> Tencent watch depth quote -> all-A
    snapshot row -> minute features -> arithmetic from the merged quote.
    ``sources`` records where each value came from; ``missing`` lists what no
    source could supply.
    """
    raw = quote.get("raw") if isinstance(quote.get("raw"), Mapping) else {}
    longhu = raw.get("longhu_watch_quote") if isinstance(raw.get("longhu_watch_quote"), Mapping) else {}
    tencent = raw.get("watch_quote") if isinstance(raw.get("watch_quote"), Mapping) else {}
    unfresh = raw.get("longhu_watch_quote_unfresh") if isinstance(raw.get("longhu_watch_quote_unfresh"), Mapping) else {}
    minute = minute if isinstance(minute, Mapping) else {}
    price = float(quote["price"])
    pct_quote = _num(quote.get("pct_change"))
    sources: dict[str, str] = {}

    def take(key: str, *candidates: tuple[str, Any]) -> float | None:
        value, source = _pick(*candidates)
        if source:
            sources[key] = source
        return value

    # pre-close and open are fixed for the session, so an unfresh licensed row is as good as a fresh one.
    pre_close = take("pre_close", ("longhu", longhu.get("pre_close")), ("longhu_unfresh", unfresh.get("pre_close")),
                     ("tencent", tencent.get("pre_close")),
                     ("all_a", raw.get("pre_close")), ("all_a", raw.get("preClose")),
                     ("derived_pct", round(price / (1 + pct_quote / 100), 4) if pct_quote is not None else None))
    opened = take("open", ("longhu", longhu.get("open")), ("longhu_unfresh", unfresh.get("open")),
                  ("tencent", _tencent_field(tencent, 5)), ("all_a", raw.get("open")))
    high = take("high", ("longhu", longhu.get("high")), ("tencent", _tencent_field(tencent, 33)),
                ("all_a", raw.get("high")), ("minute", minute.get("session_high_price")),
                ("longhu_unfresh", unfresh.get("high")))
    low = take("low", ("longhu", longhu.get("low")), ("tencent", _tencent_field(tencent, 34)),
               ("all_a", raw.get("low")), ("minute", minute.get("session_low_price")),
               ("longhu_unfresh", unfresh.get("low")))
    high = max(value for value in (high, price) if value is not None)
    low = min(value for value in (low, price) if value is not None)
    amount = take("amount", ("quote", quote.get("amount")), ("longhu", longhu.get("amount")),
                  ("tencent", tencent.get("cumulative_amount")), ("all_a", raw.get("amount")),
                  ("longhu_unfresh", unfresh.get("amount")))
    volume_lot = take("volume_lot", ("longhu", longhu.get("volume")), ("tencent", tencent.get("cumulative_volume_lot")),
                      ("longhu_unfresh", unfresh.get("volume")),
                      ("quote_shares", (_num(quote.get("volume")) or 0) / 100 or None))
    vwap = take("vwap", ("minute", minute.get("vwap")),
                ("derived_amount_volume", amount / (volume_lot * 100) if amount and volume_lot else None))
    book = longhu.get("order_book") if isinstance(longhu.get("order_book"), Mapping) else None
    if book:
        sources["book"] = "longhu"
    elif tencent.get("book_side"):
        book, sources["book"] = {"book_side": tencent.get("book_side")}, "tencent"
    elif isinstance(unfresh.get("order_book"), Mapping):
        book, sources["book"] = unfresh["order_book"], "longhu_unfresh"
    limit = limit_up_price(pre_close, symbol, name) if pre_close else None
    at_limit = bool(limit) and price >= limit - 0.005
    sealed = at_limit and (book or {}).get("book_side") == "bid_only" if book else None
    volume_ratio = take("volume_ratio", ("quote", quote.get("volume_ratio")), ("longhu_unfresh", unfresh.get("volume_ratio")))
    turnover = take("turnover_pct", ("quote", quote.get("turnover_rate")), ("longhu_unfresh", unfresh.get("turnover_rate")))
    tape = tape or {}

    def first(key: str) -> tuple[float | None, str | None]:
        return next(((value, source) for source, value in (("minute", _num(minute.get(key))),
                                                           ("snapshot_tape", _num(tape.get(key))))
                     if value is not None), (None, None))

    multiple, multiple_source = first("minute_volume_multiple")
    return_5m, return_source = first("return_5m_pct")
    previous_price = _num((previous_quote or {}).get("price"))
    if multiple is not None:
        surge, sources["surge"] = multiple >= DEFAULTS["minute_volume_multiple_min"] or (
            (volume_ratio or 0) >= DEFAULTS["vol_ratio_min"] and (return_5m or 0) > 0), multiple_source
    elif volume_ratio is not None:
        surge, sources["surge"] = volume_ratio >= DEFAULTS["vol_ratio_min"] and (vwap is None or price >= vwap), "volume_ratio"
    else:
        surge = None
    if return_5m is not None:
        not_falling, sources["not_falling"] = return_5m >= DEFAULTS["not_falling_return_5m_min"], return_source
    elif previous_price:
        not_falling, sources["not_falling"] = price >= previous_price * 0.997, "previous_scan"
    else:
        not_falling = None
    clock = observed_at.astimezone(_CN_TZ).strftime("%H:%M")
    features = {
        "price": price, "pre_close": pre_close,
        "pct": round((price / pre_close - 1) * 100, 2) if pre_close else pct_quote,
        "open": opened, "high": high, "low": low, "amount": amount, "volume_lot": volume_lot,
        "turnover_pct": turnover, "volume_ratio": volume_ratio,
        "vwap": round(vwap, 4) if vwap else None, "above_vwap": None if vwap is None else price >= vwap,
        "limit_up_price": limit, "sealed": bool(sealed), "book": sources.get("book"),
        "seal_verified_by_book": book is not None,
        "touched_limit": bool(limit) and high >= limit - 0.005,
        "open_gap_pct": round((opened / pre_close - 1) * 100, 2) if opened and pre_close else None,
        "surge": surge, "not_falling": not_falling,
        "auction_amount": amount if clock < DEFAULTS["auction_proxy_until"] else None,
        "clock": clock, "session_elapsed_min": _elapsed_minutes(observed_at), "sources": sources,
    }
    return features


def missing_inputs(playbook: str, features: Mapping[str, Any]) -> list[str]:
    return [key for key in REQUIRED_INPUTS.get(playbook, ()) if features.get(key) is None]


def _elapsed_minutes(observed_at: datetime) -> int:
    local = observed_at.astimezone(_CN_TZ)
    minutes = local.hour * 60 + local.minute
    return max(0, min(minutes - 570, 120)) + max(0, min(minutes - 780, 120))


def _sig(name: str, value: Any, ok: bool | None, src: str = "", *, gating: bool = True) -> dict[str, Any]:
    return {"name": name, "value": value, "pass": ok, "src": src, "gating": gating}


def _breakout(f: dict[str, Any], level: float | None, label: str) -> list[dict[str, Any]]:
    return [
        _sig(f"价格>{label}", f"{f['price']} vs {level}", level is not None and f["price"] > level),
        _sig(f"量比≥{DEFAULTS['vol_ratio_min']:g}（带量）", f["volume_ratio"], (f["volume_ratio"] or 0) >= DEFAULTS["vol_ratio_min"], "I"),
        _sig("均价上方（不能往下跌）", f"{f['price']} vs {f['vwap']}", bool(f["above_vwap"]), "I"),
        _sig("5分钟不下跌", f["not_falling"], bool(f["not_falling"]), "I"),
    ]


def _session_share(profile: Mapping[str, Any] | None, clock: str, elapsed_min: int) -> float:
    """Share of the day's volume normally traded by ``clock``.

    ``profile`` maps 30-minute bar ends ("10:00" … "15:00") to the stock's own
    median cumulative share; linear inside a bar.  Without one, time share.
    """
    if not profile:
        return max(elapsed_min, 1) / 240
    points = [("09:30", 0.0)] + sorted((str(key), float(value)) for key, value in profile.items())
    def minutes(stamp: str) -> int:
        hour, minute = int(stamp[:2]), int(stamp[3:5])
        total = hour * 60 + minute
        return total - 570 if total <= 690 else 120 + max(0, total - 780)
    now = minutes(clock) if clock >= "09:30" else 0
    for (left, share_left), (right, share_right) in zip(points, points[1:]):
        start, end = minutes(left), minutes(right)
        if now <= end:
            fraction = 0.0 if end == start else max(0.0, (now - start) / (end - start))
            return max(share_left + fraction * (share_right - share_left), 0.02)
    return 1.0


def _live_ma(x: Mapping[str, Any], n: int | None, price: float, frozen: Any = None) -> float | None:
    """Today's MA_n from the frozen prefix (sum of the previous n-1 closes); frozen level for older plans."""
    prefix = (x.get("ma_prefix") or {}).get(str(n)) if n else None
    if prefix is not None:
        return round((float(prefix) + price) / int(n), 4)
    value = _num(frozen)
    return value


def _close_breach(f: Mapping[str, Any], breach: bool, label: str, sig: list[dict[str, Any]]) -> bool:
    """A "收盘跌破" invalidation: only from ``close_confirm_from``; earlier it is a warning line."""
    if not breach:
        return False
    if f["clock"] >= DEFAULTS["close_confirm_from"]:
        return True
    sig.append(_sig(f"盘中{label}（{DEFAULTS['close_confirm_from']} 后按收盘确认）", f["price"], False, "T", gating=False))
    return False


def evaluate(plan: Mapping[str, Any], f: dict[str, Any], peer_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return ``{"action": entry|watch|invalid, "path": ..., "signals": [...]}`` for one snapshot."""
    pb, p = str(plan["playbook"]), plan.get("params") or {}
    x = plan.get("extra") or {}
    sig: list[dict[str, Any]] = []
    invalid = False
    path = None
    if pb == "relay_one_word":
        auction = f["auction_amount"]
        seal = (f.get("auction") or {}).get("seal_amount")
        sig += [_sig(f"竞价成交额≥{_yi(p['auction_amount_min'])}（{f['sources'].get('auction_amount', '—')}）", auction,
                     None if auction is None else auction >= p["auction_amount_min"], "T"),
                _sig("竞价封单（未匹配买量×价，参考）", None if seal is None else f"{seal / 1e8:.2f}亿", None if seal is None else seal > 0,
                     "D", gating=False),
                _sig(f"换手≤{p['turnover_max_pct']:g}%", f["turnover_pct"], (f["turnover_pct"] or 0) <= p["turnover_max_pct"], "T"),
                _sig("封板中", f["sealed"], f["sealed"]),
                _sig(f"最高>前高{p['prior_high']}", f["high"], f["high"] > p["prior_high"], "D", gating=False)]
        invalid = not f["sealed"] and (f["above_vwap"] is False or f["price"] < (f["pre_close"] or 0))
    elif pb == "relay_acceleration":
        amount = f["amount"] or 0
        low, high = p["entry_amount_range"]
        sig += [_sig(f"买点窗口：成交{_yi(low)}~{_yi(high)}且涨幅≥{p['entry_pct_min']:g}%且均价上方",
                     f"{amount / 1e8:.2f}亿/{f['pct']}%",
                     low <= amount <= high * 1.1 and (f["pct"] or 0) >= p["entry_pct_min"] and bool(f["above_vwap"]), "T"),
                _sig(f"加速：封板时成交≤{_yi(p['seal_amount_max'])}", f"{amount / 1e8:.2f}亿/封板={f['sealed']}",
                     f["sealed"] and amount <= p["seal_amount_max"], "T", gating=False)]
        invalid = (amount > p["fail_amount"] and not f["sealed"]) or (f["above_vwap"] is False and not f["sealed"])
        if f["sealed"] and amount <= p["seal_amount_max"]:
            path = "sealed"
    elif pb == "relay_expect_touch":
        sig += [_sig(f"涨幅≥{p['entry_pct_min']:g}% + 分时放量 + 均价上方", f"{f['pct']}%/{f['surge']}/{f['above_vwap']}",
                     (f["pct"] or 0) >= p["entry_pct_min"] and bool(f["surge"]) and bool(f["above_vwap"]), "I"),
                _sig("触及涨停（顶一次）", f["high"], f["touched_limit"], "T", gating=False)]
        invalid = f["price"] < (f["pre_close"] or 0)
    elif pb in ("relay_race", "relay_news_conditional"):
        sector = p.get("sector")
        sector_count = (f.get("sector_counts") or {}).get(sector) if sector else None
        minimum = p.get("sector_min_limit_ups")
        if pb == "relay_news_conditional":
            sig.append(_sig(f"{sector}板块涨停≥{minimum}（板块形成才做）", sector_count,
                            None if sector_count is None else sector_count >= minimum, "T"))
        elif sector and minimum is not None:
            sig.append(_sig(f"{sector}板块涨停≥{minimum}（板块未退潮）", sector_count,
                            None if sector_count is None else sector_count >= minimum, "D", gating=False))
        gap = f["open_gap_pct"]
        sig += [_sig("竞价不低开", gap, gap is None or gap >= 0, "I"),
                _sig("分时放量或已封板", f"{f['surge']}/{f['sealed']}", bool(f["surge"]) or f["sealed"], "I"),
                _sig("均价上方", f["above_vwap"], f["above_vwap"], "I"),
                _sig("封板中", f["sealed"], f["sealed"], gating=False)]
        invalid = (gap is not None and gap < 0 and f["above_vwap"] is False) or f["price"] < (f["pre_close"] or 0)
        if pb == "relay_race" and sector_count is not None and minimum is not None:
            invalid = invalid or (sector_count < minimum and f["clock"] >= DEFAULTS["sector_check_from"])
    elif pb == "relay_fast_seal":
        gap, amount = f["open_gap_pct"], f["amount"] or 0
        sig += [_sig("竞价不低开", gap, gap is None or gap >= 0, "I"),
                _sig(f"封板时成交≤{_yi(p['seal_amount_max'])}", f"{amount / 1e8:.2f}亿/封板={f['sealed']}",
                     f["sealed"] and amount <= p["seal_amount_max"], "I")]
        invalid = (amount >= p["fail_amount"] and not f["sealed"]) or f["price"] < (f["pre_close"] or 0)
    elif pb == "trend_pullback_restart":
        restart = (f["volume_ratio"] or 0) >= DEFAULTS["vol_ratio_min"] and (f["pct"] or 0) >= 3 and bool(f["above_vwap"])
        sig += [_sig("未涨停追高（<9.5%）", f["pct"], (f["pct"] or 0) < 9.5, "T"),
                _sig("前一日已回调缩量", x.get("pulled_back"), bool(x.get("pulled_back")), "I"),
                _sig("再起：量比≥1.5、涨幅≥3%、均价上方", f"{f['volume_ratio']}/{f['pct']}%", restart, "I")]
        ma10 = _live_ma(x, 10, f["price"], x.get("ma10")) or 0
        invalid = _close_breach(f, f["price"] < ma10, f"跌破当日MA10 {ma10}", sig)
    elif pb == "leader_benchmark_pullback":
        level = _live_ma(x, x.get("pullback_ma"), f["price"], x.get("pullback_level")) or 0
        floor = _live_ma(x, x.get("trend_floor_ma"), f["price"], x.get("floor_level")) or 0
        sig += [_sig(f"回踩当日MA{x.get('pullback_ma', '')}", f"{f['low']} vs {level}", f["low"] <= level * 1.01, "D"),
                _sig(f"守当日MA{x.get('trend_floor_ma', '')}", f"{f['price']} vs {floor}", f["price"] >= floor, "D")]
        invalid = _close_breach(f, f["price"] < floor, f"跌破当日MA{x.get('trend_floor_ma', '')} {floor}", sig)
    elif pb == "sympathy_follow":
        leader = f.get("leader_latest")
        strong_pct, weak_pct = float(p["leader_strong_pct"]), float(p["leader_weak_pct"])
        if isinstance(leader, Mapping):
            leader_pct = _num(leader.get("pct"))
            strong = bool(leader.get("sealed")) or ((leader_pct or 0) >= strong_pct and bool(leader.get("above_vwap")))
            weak = leader_pct is not None and leader_pct <= weak_pct
            shown = f"{leader.get('pct')}%/封板={leader.get('sealed')}"
        else:
            strong = weak = None
            shown = None
        sig += [_sig(f"{p.get('leader_name') or '龙头'}强（封板或涨幅≥{strong_pct:g}%且均价上方）", shown, strong, "D"),
                _sig("分时放量 + 均价上方", f"{f['surge']}/{f['above_vwap']}", bool(f["surge"]) and bool(f["above_vwap"]), "I")]
        invalid = bool(weak) or (strong is False and f["price"] < (f["pre_close"] or 0))
    elif pb == "ma5_reclaim_or_divergence":
        short_mas = [value for value in (_live_ma(x, 5, f["price"]), _live_ma(x, 10, f["price"])) if value]
        a_level = max(short_mas) if short_mas else x.get("a_level")
        path_a = _breakout(f, a_level, "当日短均线压制")
        zone = x.get("zone") or [0, 0]
        in_zone = zone[0] <= f["low"] <= zone[1]
        sig += path_a + [_sig("B：进入回踩区", f"{f['low']} in {zone}", in_zone, "T/I", gating=False)]
        live = f.get("divergence_live")
        frozen_found, frozen_judgeable = x.get("divergence_found"), x.get("divergence_judgeable")
        if isinstance(live, Mapping):
            found, judgeable = divergence_summary({key: live.get(key) for key in ("30", "60")})
            label, shown = "B：30/60分钟两段底背离（盘中）", _divergence_text(live)
        else:
            found = bool(frozen_found)
            judgeable = True if frozen_judgeable is None else bool(frozen_judgeable) or found
            label, shown = "B：30/60分钟两段底背离（盘前）", _divergence_text(x.get("divergence"))
        sig.append(_sig(label, shown, found if (judgeable or found) else None, "T", gating=False))
        if x.get("invalid_ma"):
            line = _live_ma(x, x["invalid_ma"], f["price"]) or 0
            line = round(line * (1 - float(x.get("invalid_buffer_pct", 3.0)) / 100), 3)
        else:
            line = float(p["invalid_below"])
        invalid = _close_breach(f, f["price"] < line and not found, f"跌破 {line} 且无背离", sig)
        a_ok = all(item["pass"] for item in path_a)
        b_ok = in_zone and found and bool(f["above_vwap"])
        path = "A" if a_ok else "B" if b_ok else None
        return {"action": "invalid" if invalid else "entry" if path else "watch", "path": path, "signals": sig}
    elif pb == "trend_continuation":
        hold = _live_ma(x, x.get("hold_ma"), f["price"], x.get("hold_level")) or 0
        floor = _live_ma(x, x.get("floor_ma"), f["price"], x.get("floor_level")) or 0
        sig += [_sig(f"持有：价格≥当日MA{x.get('hold_ma', '')}", f"{f['price']} vs {hold}", f["price"] >= hold, "D"),
                _sig(f"延续：价格>前高{p['prior_high']}", f["high"], f["high"] > p["prior_high"], "D")]
        invalid = _close_breach(f, f["price"] < floor, f"跌破当日MA{x.get('floor_ma', '')} {floor}", sig)
    elif pb == "prior_high_breakout":
        sig += _breakout(f, p["prior_high"], "前高")
        floor = _live_ma(x, x.get("floor_ma"), f["price"], x.get("floor_level")) or 0
        invalid = _close_breach(f, f["price"] < floor, f"跌破当日MA{x.get('floor_ma', '')} {floor}", sig)
    elif pb == "platform_breakout":
        # 老师：追高是很难的，加自选等回调、走平台；仍在创新高时没有平台可突破。
        sig.append(_sig("已形成平台（高点后整理≥1天）", x.get("days_since_peak"), int(x.get("days_since_peak") or 0) >= 1, "T"))
        sig += _breakout(f, x.get("platform_upper"), "平台上沿")
        floor_ma = int(x.get("floor_ma") or 10)
        ma_floor = _live_ma(x, floor_ma, f["price"], x.get(f"ma{floor_ma}") or x.get("ma10"))
        floor = min(x.get("platform_lower") or 0, ma_floor or x.get("platform_lower") or 0)
        invalid = _close_breach(f, f["price"] < floor, f"跌破 平台下沿/当日MA{floor_ma} {round(floor, 3)}", sig)
    elif pb == "ma10_second_wave":
        ma10 = _live_ma(x, 10, f["price"], x.get("ma10")) or 0
        touched = bool(x.get("touched_ma10")) or f["low"] <= ma10 * (1 + float(p["touch_tol_pct"]) / 100)
        mid = (f["high"] + f["low"]) / 2
        drift = (mid / x["prior_mid"] - 1) * 100 if x.get("prior_mid") else 0.0
        sig += [_sig("已回到10日线", touched, touched, "T"),
                _sig(f"重心不再下移（≥-{DEFAULTS['center_flat_pct']:g}%）", round(drift, 2), drift >= -DEFAULTS["center_flat_pct"], "I"),
                _sig("均价上方", f["above_vwap"], f["above_vwap"], "I")]
        invalid = _close_breach(f, f["price"] < ma10 * 0.97, f"跌破当日MA10×0.97 {round(ma10 * 0.97, 3)}", sig)
    elif pb == "double_bottom_platform":
        sig += [_sig("颈线上方", f"{f['price']} vs {p['neckline']}", f["price"] >= p["neckline"], "D")]
        sig += _breakout(f, p["platform_high"], "平台高点")
        invalid = _close_breach(f, f["price"] < p["neckline"], f"跌回颈线 {p['neckline']}", sig)
    elif pb == "ma60_reclaim":
        pace = _session_share(p.get("volume_profile"), f["clock"], f["session_elapsed_min"])
        projected = (f["amount"] or 0) / pace
        ma60, amt20 = _live_ma(x, 60, f["price"], x.get("ma60")) or 0, x.get("amt20") or 0
        sig += [_sig("价格>当日MA60", f"{f['price']} vs {ma60}", f["price"] > ma60, "T"),
                _sig(f"全天成交（按本股分时量能曲线外推）≥{p['amount_mult']:g}×20日均额", round(projected / 1e8, 1),
                     projected >= float(p["amount_mult"]) * amt20, str(p.get("amount_mult_src") or "I")[:1])]
        # 老师：收盘连续2日低于 MA60 才算失败；昨日已在 MA60 下方时，今日收盘仍明显在下方即确认。
        invalid = _close_breach(f, f["price"] < ma60 * 0.97 and bool(x.get("prev_close_below_ma60", True)),
                                f"收盘第2日低于当日MA60×0.97 {round(ma60 * 0.97, 2)}", sig)
    gating = [item for item in sig if item["gating"]]
    entry = bool(gating) and all(item["pass"] for item in gating)
    return {"action": "invalid" if invalid else "entry" if entry else "watch", "path": path, "signals": sig}


def teacher_review_signals(
    watch: Mapping[str, Any], quote: Mapping[str, Any] | None, minute_features: Mapping[str, Any] | None,
    peer_context: Mapping[str, Any] | None, observed_at: datetime,
    previous_quote: Mapping[str, Any] | None = None, *, tape: SnapshotTape | None = None,
    divergence_book: PeriodDivergenceBook | None = None, market_book: TeacherMarketBook | None = None,
) -> list[dict[str, Any]]:
    """Scan hook: entry/invalidation candidates plus an explicit data gap.

    Entry and invalidation carry ``independent_confirmation`` so the first
    scan that satisfies them is confirmed and delivered at once; the shared
    state machine still de-duplicates an unchanged, continuing setup.  A
    missing input is reported as its own candidate after it persists for two
    scans (ordinary confirmation), so the operator learns why a plan is silent.
    """
    plan = active_plan(watch, observed_at)
    if plan is None:
        return []
    symbol = str(watch["symbol"]).upper()
    playbook = str(plan["playbook"])
    base = {
        "symbol": symbol, "hard": False, "policy_profile": "teacher_review", "strategy_version": MODEL_VERSION,
        "risk_flags": ["teacher_review_research", "manual_review_required", "no_automatic_order"],
    }
    review_base = {
        "pack_id": plan.get("pack_id"), "analyst_id": plan.get("analyst_id"), "review_date": plan.get("review_date"),
        "session_date": plan.get("session_date"), "playbook": playbook, "kind": playbook_kind(playbook),
        "stance": plan.get("stance"), "group": plan.get("group"), "name": plan.get("name"),
        "teacher": plan.get("teacher"), "invalidation": plan.get("invalidation"),
        "evidence_times": plan.get("evidence_times"), "model_version": MODEL_VERSION,
    }
    if not quote or _num(quote.get("price")) is None:
        return [{**base, "signal_key": f"{symbol}:data_issue:teacher_review", "signal_type": "data_issue",
                 "severity": "warning", "score": 0,
                 "conditions": {"setup": "teacher_review_data_missing",
                                "teacher_review": {**review_base, "missing": ["price"], "action": "data_missing"}}}]
    name = str(plan.get("name") or "")
    features = scan_features(symbol, quote, minute_features, observed_at, name, previous_quote)
    if tape is not None:
        tape.observe(symbol, observed_at, features["price"], features.get("volume_lot"),
                     features["sources"].get("volume_lot"))
        tape_view = tape.features(symbol, observed_at)
        if tape_view:
            features = scan_features(symbol, quote, minute_features, observed_at, name, previous_quote, tape_view)
            features["tape"] = tape_view
    if tape is not None:
        tape.note_latest(symbol, observed_at, {key: features.get(key) for key in ("price", "pct", "sealed", "above_vwap")})
        if playbook == "sympathy_follow":
            leader = str((plan.get("extra") or {}).get("leader_ts_code") or "")
            features["leader_latest"] = tape.latest(leader, observed_at) if leader else None
    if market_book is not None:
        auction = market_book.auction(symbol, observed_at)
        if auction and auction.get("amount") is not None:
            features["auction"] = auction
            features["auction_amount"] = auction["amount"]
            features["sources"]["auction_amount"] = str(auction.get("source") or "auction_snapshot")
        features["sector_counts"] = market_book.sector_counts(observed_at)
    if features.get("auction_amount") is not None and "auction_amount" not in features["sources"]:
        features["sources"]["auction_amount"] = "proxy:cum_amount_before_0931"
    if divergence_book is not None and playbook == "ma5_reclaim_or_divergence":
        live = divergence_book.get(symbol, observed_at)
        if live:
            features["divergence_live"] = live
    missing = missing_inputs(playbook, features)
    result = evaluate(plan, features, peer_context)
    if missing and result["action"] == "entry":
        result = {**result, "action": "watch"}
    signals: list[dict[str, Any]] = []
    feature_view = {key: features[key] for key in (
        "price", "pct", "pre_close", "open", "high", "low", "amount", "turnover_pct", "volume_ratio", "vwap",
        "limit_up_price", "sealed", "seal_verified_by_book", "touched_limit", "open_gap_pct", "surge",
        "not_falling", "auction_amount", "clock", "sources")}
    for key in ("tape", "divergence_live", "auction", "sector_counts", "leader_latest"):
        if features.get(key):
            feature_view[key] = features[key]
    if result["action"] in {"entry", "invalid"}:
        invalid = result["action"] == "invalid"
        suffix = f":{result['path']}" if result.get("path") else ""
        signals.append({
            **base,
            "signal_key": (f"{symbol}:watch:teacher_review_invalid:{playbook}" if invalid
                           else f"{symbol}:entry:teacher_review:{playbook}{suffix}"),
            "signal_type": "watch" if invalid else "entry",
            "severity": "warning", "score": 50 if invalid else 65,
            "independent_confirmation": True,
            "conditions": {
                "setup": "teacher_review_invalidated" if invalid else "teacher_review_entry",
                "teacher_review": {**review_base, "path": result.get("path"), "action": result["action"],
                                   "signals": result["signals"], "features": feature_view, "missing": missing},
            },
        })
    if missing:
        signals.append({
            **base, "signal_key": f"{symbol}:data_issue:teacher_review", "signal_type": "data_issue",
            "severity": "warning", "score": 0,
            "conditions": {"setup": "teacher_review_data_missing",
                           "teacher_review": {**review_base, "action": "data_missing", "missing": missing,
                                              "features": feature_view}},
        })
    return signals


_INPUT_LABELS = {
    "price": "现价", "pre_close": "昨收", "open": "开盘价", "high": "最高", "low": "最低", "amount": "成交额",
    "turnover_pct": "换手", "volume_ratio": "量比", "vwap": "分时均价", "surge": "分时量能", "not_falling": "5分钟走势",
    "book": "封板盘口",
}


def teacher_review_alert_lines(signal: Mapping[str, Any]) -> list[str]:
    """Feishu body lines for a teacher-review candidate (rendered by intraday_alert_text)."""
    review = (signal.get("conditions") or {}).get("teacher_review") or {}
    features = review.get("features") or {}
    if review.get("action") == "data_missing":
        return [f"老师复盘：{review.get('analyst_id')} {review.get('review_date')}｜打法 {review.get('playbook')}",
                "条件无法判定，缺少数据：" + "、".join(_INPUT_LABELS.get(key, key) for key in review.get("missing") or []),
                "已按数据面现有接口依次尝试 Longhu/腾讯/全A快照/分钟线；请检查对应数据源。"]
    lines = [
        f"老师复盘：{review.get('analyst_id')} {review.get('review_date')}｜{review.get('group') or ''}｜打法 {review.get('playbook')}"
        + (f"（路径 {review['path']}）" if review.get("path") else ""),
        f"老师原话：{str(review.get('teacher') or '')[:160]}",
        f"现价 {features.get('price', '—')}｜涨跌 {features.get('pct', '—')}%｜成交 "
        f"{round((features.get('amount') or 0) / 1e8, 2)}亿｜量比 {features.get('volume_ratio', '—')}｜"
        f"换手 {features.get('turnover_pct', '—')}%｜均价 {features.get('vwap', '—')}｜封板 {features.get('sealed')}",
    ]
    for item in review.get("signals") or []:
        mark = "✔" if item.get("pass") else ("✘" if item.get("pass") is False else "?")
        lines.append(f"{mark} {item.get('name')}：{item.get('value')}" + ("" if item.get("gating", True) else "（参考）"))
    if review.get("action") == "invalid" and review.get("invalidation"):
        lines.append(f"失效条件：{review['invalidation']}")
    if review.get("evidence_times"):
        lines.append(f"视频时间码：{'、'.join(review['evidence_times'][:4])}")
    return lines


__all__ = ["MODEL_VERSION", "PeriodDivergenceBook", "SECTOR_PATTERNS", "SnapshotTape", "TeacherMarketBook", "active_plan",
           "count_sector_limit_ups", "divergence_plan_symbols", "evaluate",
           "scan_features", "teacher_review_alert_lines", "teacher_review_signals"]
