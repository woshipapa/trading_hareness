"""Point-in-time corporate-action factor semantics.

The owner now derives the cumulative series from Longhu 前复权 K-lines and
stores it as ``provider='longhu_qfq_derived'``.  Tushare rows remain accepted
as explicitly licensed checkpoint evidence on the peer.  Owner production
uses ``corporate_action_cumulative`` in ``raw.factor_semantics``; the older
``cumulative_tushare`` label remains accepted for historical rows.
A same-day identity value (usually ``1``) is useful for a close-control
payload but is not a historical corporate-action factor and must never be
promoted into the research price path.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


ADJUSTMENT_STATES = ("complete", "pending", "retired", "absent")
COMPLETE_FACTOR_SEMANTICS = "cumulative_tushare"
IDENTITY_FACTOR_SEMANTICS = "same_day_identity_only"
TUSHARE_FACTOR_PROVIDERS = frozenset({
    "tushare", "tushare_primary", "tushare_super_get", "tushare_super_sdk",
    "tushare_super", "tushare_backup",
})
LONGHU_QFQ_FACTOR_PROVIDER = "longhu_qfq_derived"
LONGHU_FACTOR_PROVIDERS = frozenset({LONGHU_QFQ_FACTOR_PROVIDER})
# Keep one explicit allow-list.  In particular, ``longhuvip_composite`` and
# other front-adjusted quote providers are not cumulative-factor evidence.
COMPLETE_FACTOR_PROVIDERS = TUSHARE_FACTOR_PROVIDERS | LONGHU_FACTOR_PROVIDERS


def persisted_factor_semantics_sql(alias: str = "factor") -> str:
    """SQL predicate for the owner's actual factor schema.

    Owner production stores semantic metadata inside ``raw`` JSON rather than
    materializing ``factor_semantics``/``adjustment_state`` columns.  The
    Longhu close task identifies its cumulative series by method; legacy
    Tushare checkpoints retain the historical cumulative label.
    """

    if not alias or not alias.replace("_", "").isalnum():
        raise ValueError("invalid SQL alias")
    return (
        f"(({alias}.raw->>'factor_semantics') IN ('corporate_action_cumulative','cumulative_tushare','cumulative','longhu_qfq_derived') "
        f"OR ({alias}.provider='longhu_qfq_derived' "
        f"AND {alias}.raw->>'method'='longhu_cq_preclose_qfq_v2'))"
    )


def persisted_factor_eligible_sql(alias: str = "factor") -> str:
    """Return the complete-factor predicate for owner persisted rows.

    Keep the positive-value and provider checks beside the raw JSON semantic
    check so read paths cannot accidentally treat an arbitrary positive
    observation as a research factor.  This is a SQL fragment only; it never
    performs a query or writes to the owner database.
    """
    if not alias or not alias.replace("_", "").isalnum():
        raise ValueError("invalid SQL alias")
    providers = ",".join(repr(provider) for provider in sorted(COMPLETE_FACTOR_PROVIDERS))
    return (
        f"({alias}.adj_factor>0 AND {alias}.provider IN ({providers}) "
        f"AND {persisted_factor_semantics_sql(alias)})"
    )


def positive_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def factor_semantics(row: Mapping[str, Any] | None, *, provider: str | None = None) -> str:
    """Return a conservative semantic label for a persisted factor row."""
    if not row:
        return "absent"
    raw = row.get("raw")
    if not isinstance(raw, Mapping):
        raw = {}
    explicit = str(row.get("factor_semantics") or raw.get("factor_semantics") or "").strip()
    if explicit == IDENTITY_FACTOR_SEMANTICS:
        return IDENTITY_FACTOR_SEMANTICS
    source = str(row.get("provider") or provider or "").strip()
    if source and source not in COMPLETE_FACTOR_PROVIDERS:
        # Do not let a caller-supplied ``cumulative_tushare`` label override
        # the provider allow-list.  Unknown provenance is audit evidence only.
        return "unknown"
    method = str(raw.get("method") or "").strip()
    if source == LONGHU_QFQ_FACTOR_PROVIDER and method == "longhu_cq_preclose_qfq_v2":
        return COMPLETE_FACTOR_SEMANTICS
    if explicit in {"corporate_action_cumulative", "cumulative", "cumulative_tushare"}:
        return COMPLETE_FACTOR_SEMANTICS
    return explicit or COMPLETE_FACTOR_SEMANTICS


def adjustment_state(row: Mapping[str, Any] | None, *, provider: str | None = None) -> str:
    """Classify one row; missing rows are ``absent`` and identity rows retired."""
    if not row:
        return "absent"
    raw = row.get("raw")
    if not isinstance(raw, Mapping):
        raw = {}
    explicit = str(row.get("adjustment_state") or raw.get("adjustment_state") or "").strip()
    if explicit in {"pending", "retired", "absent"}:
        return explicit
    semantics = factor_semantics(row, provider=provider)
    if semantics == IDENTITY_FACTOR_SEMANTICS:
        return "retired"
    if semantics == COMPLETE_FACTOR_SEMANTICS:
        return "complete" if positive_decimal(row.get("adj_factor")) is not None else "pending"
    # A positive value from an unlicensed/unknown source remains audit
    # evidence, but cannot silently enter a research price series.
    return "retired" if positive_decimal(row.get("adj_factor")) is not None else "pending"


def research_factor_eligible(row: Mapping[str, Any] | None, *, provider: str | None = None) -> bool:
    """Whether a row may enter an adjusted research-price calculation."""
    return (
        adjustment_state(row, provider=provider) == "complete"
        and factor_semantics(row, provider=provider) == COMPLETE_FACTOR_SEMANTICS
        and positive_decimal(row.get("adj_factor") if row else None) is not None
    )


def normalize_factor_row(row: Mapping[str, Any], *, provider: str) -> dict[str, Any]:
    """Validate/annotate a provider row without inventing a factor."""
    factor = positive_decimal(row.get("adj_factor"))
    if provider not in COMPLETE_FACTOR_PROVIDERS:
        raise ValueError(f"provider {provider!r} is not licensed for cumulative adjustment factors")
    if factor is None:
        raise ValueError("adj_factor must be a positive cumulative factor")
    return {
        **dict(row),
        "adj_factor": factor,
        "provider": provider,
        "factor_semantics": COMPLETE_FACTOR_SEMANTICS,
        "adjustment_state": "complete",
    }


__all__ = [
    "ADJUSTMENT_STATES", "COMPLETE_FACTOR_SEMANTICS", "IDENTITY_FACTOR_SEMANTICS",
    "TUSHARE_FACTOR_PROVIDERS", "LONGHU_QFQ_FACTOR_PROVIDER", "LONGHU_FACTOR_PROVIDERS",
    "COMPLETE_FACTOR_PROVIDERS", "adjustment_state", "factor_semantics",
    "normalize_factor_row", "persisted_factor_eligible_sql", "persisted_factor_semantics_sql",
    "positive_decimal", "research_factor_eligible",
]
