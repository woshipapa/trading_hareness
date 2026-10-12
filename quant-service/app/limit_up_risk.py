"""Pure limit-up big-loss risk features and score (research-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from math import fsum
from typing import Any, Mapping
from zoneinfo import ZoneInfo

VERSION = "limit-up-risk-v1"
COEFFICIENTS_FILE_SHA256 = "29f01efb6a1f78372f49227bff83267617262af89db7e4c28e7a07c9a5692408"
# SHA-256: state/research/divergence_study_20261012/seal/fitted_coefs.json
COEFFICIENTS = {
    "rank_vr": 0.013579072948992335,
    "rank_flow": -0.11645549322296539,
    "rank_intraday": 0.12705396492255722,
    "rank_gap": 0.0696963591815818,
    "rank_seal_ratio": -0.19162589861558535,
    "rank_seal_minutes": -0.11676472440931669,
    "opened_minus_day_mean": -0.028410675174980395,
    "rank_boards": 0.18757755167541718,
    "rank_float_cap": -0.10006322974064638,
}
CN_TZ = ZoneInfo("Asia/Shanghai")


def symbol_for_code(code: Any) -> str:
    code = str(code).zfill(6)
    return f"{code}.SH" if code.startswith(("5", "6")) else f"{code}.SZ"


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for position, _value in ordered[index:end]:
            ranks[position] = rank / len(values) - 0.5
        index = end
    return ranks


def _percentiles(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for position, _value in ordered[index:end]:
            ranks[position] = rank / len(values) * 100
        index = end
    return ranks


def _seal_minutes(epoch: Any) -> int:
    local = _seal_time(epoch)
    minutes = local.hour * 60 + local.minute - 570
    return 0 if minutes <= 0 else minutes - 90 if local.hour >= 13 else minutes


def _seal_time(epoch: Any) -> datetime:
    return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone(CN_TZ)


def _limit_up(code: str, bar: Mapping[str, Any], limits: Mapping[str, Any]) -> float:
    limit = limits.get(symbol_for_code(code))
    if isinstance(limit, (tuple, list)):
        limit = limit[0]
    if limit:
        return float(limit)
    stored = bar.get("limit_up")
    if stored:
        return float(stored)
    return round(float(bar["pre_close"]) * (1.2 if code.startswith(("30", "68")) else 1.1), 2)


def score_day(
    lists: Mapping[str, list[list[Any]]], bars: Mapping[str, Mapping[str, Any]],
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    limits = limits or {}
    coverage = {key: 0 for key in (
        "malformed", "excluded_code", "duplicate", "missing_bar", "limit_ratio", "not_at_limit", "insufficient_history",
    )}
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for list_key in ("pid:1", "pid:2", "pid:3", "pid:4", "pid:5"):
        for row in lists.get(list_key, []):
            if not isinstance(row, list) or len(row) < 23:
                coverage["malformed"] += 1
                continue
            code = str(row[0]).zfill(6)
            if code.startswith(("4", "8")):
                coverage["excluded_code"] += 1
                continue
            symbol = symbol_for_code(code)
            if symbol in seen:
                coverage["duplicate"] += 1
                continue
            seen.add(symbol)
            bar = bars.get(symbol)
            if bar is None:
                coverage["missing_bar"] += 1
                continue
            limit_up = _limit_up(code, bar, limits)
            if round(limit_up / float(bar["pre_close"]) - 1, 2) < 0.08:
                coverage["limit_ratio"] += 1
                continue
            if float(bar["close"]) < limit_up - 0.005:
                coverage["not_at_limit"] += 1
                continue
            prior = list(bar["prior_amounts"])
            if len(prior) < 10:
                coverage["insufficient_history"] += 1
                continue
            turnover = float(bar["amount"])
            seal_time = _seal_time(row[4])
            candidates.append({
                "symbol": symbol, "name": row[1], "sector": row[5],
                "vr": turnover / (fsum(float(value) for value in prior[-20:]) / len(prior[-20:])),
                "gap": float(bar["open"]) / float(bar["pre_close"]) - 1,
                "intraday": float(bar["close"]) / float(bar["open"]) - 1,
                "flow": float(row[8]) / float(row[11]),
                "seal_ratio": float(row[6]) / float(row[11]),
                "seal_minutes": _seal_minutes(row[4]),
                "first_seal_time": seal_time.strftime("%H:%M:%S"),
                "opened": float(row[16]), "boards": float(row[15]), "float_cap": float(row[13]),
                "sealed_from_open": _seal_minutes(row[4]) == 0,
            })

    if not candidates:
        return {"rows": [], "coverage": coverage, "ranks_reliable": False}
    fields = ("vr", "flow", "intraday", "gap", "seal_ratio", "seal_minutes", "boards", "float_cap")
    ranks = {field: _average_ranks([item[field] for item in candidates]) for field in fields}
    opened_mean = fsum(item["opened"] for item in candidates) / len(candidates)
    scores: list[float] = []
    for index, item in enumerate(candidates):
        risk = (
            COEFFICIENTS["rank_vr"] * ranks["vr"][index]
            + COEFFICIENTS["rank_flow"] * ranks["flow"][index]
            + COEFFICIENTS["rank_intraday"] * ranks["intraday"][index]
            + COEFFICIENTS["rank_gap"] * ranks["gap"][index]
            + COEFFICIENTS["rank_seal_ratio"] * ranks["seal_ratio"][index]
            + COEFFICIENTS["rank_seal_minutes"] * ranks["seal_minutes"][index]
            + COEFFICIENTS["opened_minus_day_mean"] * (item["opened"] - opened_mean)
            + COEFFICIENTS["rank_boards"] * ranks["boards"][index]
            + COEFFICIENTS["rank_float_cap"] * ranks["float_cap"][index]
        )
        item["risk"] = risk
        scores.append(risk)
    for item, rank in zip(candidates, _percentiles(scores)):
        item["risk_rank"] = rank
    return {"rows": candidates, "coverage": coverage, "ranks_reliable": len(candidates) >= 20}


compute = score_day

__all__ = ["COEFFICIENTS", "COEFFICIENTS_FILE_SHA256", "VERSION", "compute", "score_day", "symbol_for_code"]
