"""Confirm a drilled candidate against its own large-order flow.

The drill ranks a member by how much it leads its board, which is a price
statement.  This is the money statement: Longhu's ``GetStockDaDanTrendIncremental``
returns one cumulative large-order net figure per minute for a single symbol,
so the two can disagree - a name can lead its sector on price while large
orders leave it.

It costs one gateway call per symbol, which is why it runs on the delivery
shortlist rather than on every candidate the drill produces.

Research evidence, never an order.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: Minutes of the series used to judge the current push. Short enough that a
#: morning's flow does not mask an afternoon reversal.
RECENT_MINUTES = 15

#: A net figure inside this band is not evidence of anything either way.
MIN_ABS_NET = 1_000_000.0


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def parse_series(payload: Mapping[str, Any]) -> list[tuple[str, float]]:
    """Read the ``[["09:30", -1221521], ...]`` cumulative series.

    The vendor returns it under ``dadanjinge``. Malformed points are dropped
    rather than coerced: a zero standing in for an unreadable figure would read
    as balanced flow.
    """
    series: list[tuple[str, float]] = []
    for point in payload.get("dadanjinge") or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        minute, value = str(point[0] or "").strip(), _number(point[1])
        if minute and value is not None:
            series.append((minute, value))
    return series


def confirm(direction: str, series: Sequence[tuple[str, float]], *,
            recent_minutes: int = RECENT_MINUTES,
            min_abs_net: float = MIN_ABS_NET) -> dict[str, Any]:
    """Does the symbol's own large-order flow agree with its board's direction?

    The series is cumulative, so the push over the window is the difference
    between its ends - not the last value, which still carries the whole
    morning. Both the level and the push must agree: a name deep in cumulative
    outflow that ticked up for one minute has not turned.
    """
    if not series:
        return {"status": "unavailable", "reason": "no large-order series", "confirmed": False}
    window = list(series[-max(2, recent_minutes):])
    latest_minute, latest = window[-1]
    push = latest - window[0][1]
    if abs(latest) < min_abs_net and abs(push) < min_abs_net:
        return {
            "status": "inconclusive", "confirmed": False,
            "reason": "large-order flow is inside the noise band",
            "cumulative_net": latest, "window_push": push, "as_of": latest_minute,
        }
    wanted_positive = direction == "inflow"
    agrees = (latest > 0) == wanted_positive and (push > 0) == wanted_positive
    return {
        "status": "completed",
        "confirmed": bool(agrees),
        "reason": None if agrees else "large-order flow does not agree with the board direction",
        "cumulative_net": latest,
        "window_push": push,
        "window_minutes": len(window),
        "as_of": latest_minute,
        "source": "longhuvip:GetStockDaDanTrendIncremental",
    }


def request(symbol: str) -> dict[str, Any]:
    """The licensed call for one symbol's large-order minute series."""
    code = str(symbol).split(".")[0]
    return {
        "target": "longhu_quote", "path": "/w1/api/index.php",
        "params": {"a": "GetStockDaDanTrendIncremental", "c": "StockL2Data",
                   "StockID": code, "apiv": "w41", "Index": 0, "st": 300},
    }


def page_payload(response: Mapping[str, Any]) -> dict[str, Any]:
    """Unwrap the gateway's page envelope around one documented call."""
    for page in response.get("pages") or []:
        body = (page or {}).get("payload") or {}
        if isinstance(body, Mapping):
            return dict(body)
    return {}


def confirm_candidates(
    candidates: Iterable[Mapping[str, Any]],
    fetch: Any,
) -> list[dict[str, Any]]:
    """Attach a confirmation to each candidate; ``fetch`` makes one call.

    A failed call marks the candidate unconfirmed rather than dropping it, so
    a gateway problem is visible as unconfirmed evidence instead of a quietly
    shorter list.
    """
    confirmed: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        try:
            payload = page_payload(fetch(request(str(item.get("symbol") or ""))))
            item["large_order"] = confirm(str(item.get("direction") or ""), parse_series(payload))
        except Exception as error:  # noqa: BLE001 - one symbol must not end the pass
            item["large_order"] = {"status": "failed", "confirmed": False,
                                   "reason": f"{type(error).__name__}: {str(error)[:120]}"}
        confirmed.append(item)
    return confirmed


__all__ = [
    "MIN_ABS_NET", "RECENT_MINUTES", "confirm", "confirm_candidates",
    "page_payload", "parse_series", "request",
]
