"""Pure limit-up big-loss risk features and score (research-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from math import fsum
from statistics import mean, stdev
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .datasources.http import ashare_symbol

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


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for position, _value in ordered[index:end]:
            ranks[position] = rank / len(values)
        index = end
    return ranks


def _seal_minutes(epoch: Any) -> int:
    local = _seal_time(epoch)
    minutes = local.hour * 60 + local.minute - 570
    return 0 if minutes <= 0 else minutes - 90 if local.hour >= 13 else minutes


def _seal_time(epoch: Any) -> datetime:
    return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone(CN_TZ)


def _limit_up(code: str, bar: Mapping[str, Any]) -> float:
    stored = bar.get("limit_up")
    if stored:
        return float(stored)
    return round(float(bar["pre_close"]) * (1.2 if code.startswith(("30", "68")) else 1.1), 2)


def decode_limit_rows(
    lists: Mapping[str, list[list[Any]]], bars: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Decode valid limit-list rows before the scorer's history gate."""
    coverage = {key: 0 for key in (
        "malformed", "excluded_code", "duplicate", "missing_bar", "limit_ratio", "not_at_limit",
    )}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for list_key in ("pid:1", "pid:2", "pid:3", "pid:4", "pid:5"):
        for row in lists.get(list_key, []):
            if not isinstance(row, list) or len(row) < 23:
                coverage["malformed"] += 1
                continue
            code = str(row[0]).zfill(6)
            symbol = ashare_symbol(code)
            if symbol is None or symbol.endswith(".BJ"):
                coverage["excluded_code"] += 1
                continue
            if symbol in seen:
                coverage["duplicate"] += 1
                continue
            seen.add(symbol)
            bar = bars.get(symbol)
            if bar is None:
                coverage["missing_bar"] += 1
                continue
            limit_up = _limit_up(code, bar)
            if round(limit_up / float(bar["pre_close"]) - 1, 2) < 0.08:
                coverage["limit_ratio"] += 1
                continue
            if float(bar["close"]) < limit_up - 0.005:
                coverage["not_at_limit"] += 1
                continue
            rows.append({
                "symbol": symbol, "name": row[1], "sector": row[5], "first_seal_time": row[4],
                "seal_money": float(row[6]), "seal_max": float(row[7]), "main_net": float(row[8]),
                "turnover": float(row[11]), "float_cap": float(row[13]), "boards": float(row[15]),
                "reopened": float(row[16]), "bar": bar,
            })
    return {"rows": rows, "coverage": coverage}


def score_day(
    lists: Mapping[str, list[list[Any]]], bars: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    coverage = {key: 0 for key in (
        "malformed", "excluded_code", "duplicate", "missing_bar", "limit_ratio", "not_at_limit", "insufficient_history",
    )}
    decoded = decode_limit_rows(lists, bars)
    coverage.update(decoded["coverage"])
    candidates: list[dict[str, Any]] = []
    for decoded_row in decoded["rows"]:
        bar = decoded_row["bar"]
        prior = list(bar.get("prior_amounts") or [])
        if len(prior) < 10:
            coverage["insufficient_history"] += 1
            continue
        turnover = float(bar["amount"])
        candidates.append({
            "symbol": decoded_row["symbol"], "name": decoded_row["name"], "sector": decoded_row["sector"],
            "vr": turnover / (fsum(float(value) for value in prior[-20:]) / len(prior[-20:])),
            "gap": float(bar["open"]) / float(bar["pre_close"]) - 1,
            "intraday": float(bar["close"]) / float(bar["open"]) - 1,
            "flow": decoded_row["main_net"] / decoded_row["turnover"],
            "seal_ratio": decoded_row["seal_money"] / decoded_row["turnover"],
            "seal_minutes": _seal_minutes(decoded_row["first_seal_time"]),
            "first_seal_time": _seal_time(decoded_row["first_seal_time"]).strftime("%H:%M:%S"),
            "opened": decoded_row["reopened"], "boards": decoded_row["boards"],
            "float_cap": decoded_row["float_cap"],
            "sealed_from_open": _seal_minutes(decoded_row["first_seal_time"]) == 0,
        })

    if not candidates:
        return {"rows": [], "coverage": coverage, "ranks_reliable": False}
    fields = ("vr", "flow", "intraday", "gap", "seal_ratio", "seal_minutes", "boards", "float_cap")
    ranks = {field: [rank - 0.5 for rank in _ranks([item[field] for item in candidates])] for field in fields}
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
    for item, rank in zip(candidates, _ranks(scores)):
        item["risk_rank"] = rank * 100
    return {"rows": candidates, "coverage": coverage, "ranks_reliable": len(candidates) >= 20}


def fifth_comparison(rows: list[Mapping[str, Any]], next_day_returns: Mapping[str, float]) -> dict[str, Any] | None:
    outcomes = [(row, float(next_day_returns[row["symbol"]])) for row in rows if row["symbol"] in next_day_returns]
    if len(outcomes) < 20:
        return None
    outcomes.sort(key=lambda item: item[0]["risk"])
    fifth = len(outcomes) // 5

    def summary(group: list[tuple[Mapping[str, Any], float]]) -> dict[str, float]:
        returns = [item[1] for item in group]
        return {"big_loss_rate": sum(value <= -0.05 for value in returns) / len(returns),
                "mean_next_day_return": sum(returns) / len(returns)}

    return {"lowest": summary(outcomes[:fifth]), "highest": summary(outcomes[-fifth:])}


def recent_status(summaries: list[Mapping[str, Any]], limit: int = 120) -> dict[str, Any]:
    recent = summaries[-limit:]
    differences = [float(item["highest"]["big_loss_rate"]) - float(item["lowest"]["big_loss_rate"])
                   for item in recent]
    lowest = mean(float(item["lowest"]["big_loss_rate"]) for item in recent) if recent else None
    highest = mean(float(item["highest"]["big_loss_rate"]) for item in recent) if recent else None
    difference_points = (highest - lowest) * 100 if lowest is not None and highest is not None else None
    t_value = None
    if len(differences) >= 2:
        spread = stdev(differences)
        # Zero-variance daily differences do not occur in real data; no finite t-value is reported.
        t_value = mean(differences) / (spread / len(differences) ** 0.5) if spread else None
    status = "insufficient_history" if len(recent) < 60 else (
        "working" if difference_points >= 3 and t_value is not None and t_value >= 2 else "not_working_recently"
    )
    return {"sessions": len(recent), "lowest_big_loss_rate": lowest, "highest_big_loss_rate": highest,
            "difference_points": difference_points, "t_value": t_value, "status": status}


__all__ = [
    "COEFFICIENTS", "COEFFICIENTS_FILE_SHA256", "VERSION", "decode_limit_rows", "fifth_comparison",
    "recent_status", "score_day",
]
