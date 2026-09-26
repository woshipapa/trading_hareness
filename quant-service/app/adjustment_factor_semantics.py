"""Point-in-time corporate-action factor semantics.

The owner publishes the factor contract as a machine-readable derived rule.
The important subtlety is that a missing ``raw.factor_semantics`` key is valid
for legacy Tushare rows: the provider itself supplies the cumulative-series
meaning.  Conversely, a superseded row is never priceable, even when its
provider and numeric value look valid.  Keep this predicate in one module so
SQL readers and Python projections cannot drift apart.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


ADJUSTMENT_STATES = ("complete", "pending", "retired", "absent")
COMPLETE_FACTOR_SEMANTICS = "corporate_action_cumulative"
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
    """Return the owner v2 semantic/provider predicate.

    This intentionally does *not* require the JSON key to be present.  Owner
    production has millions of valid Tushare rows without that key.  The
    separate ``persisted_factor_eligible_sql`` adds the positive-value guard;
    the superseded-row guard belongs here because every semantic read must
    exclude rows explicitly replaced by a later correction.
    """

    if not alias or not alias.replace("_", "").isalnum():
        raise ValueError("invalid SQL alias")
    return (
        f"({alias}.raw->>'superseded_at' IS NULL AND "
        # psycopg uses percent signs for placeholders; ``%%`` is sent to
        # PostgreSQL as the literal ``%`` in the LIKE pattern.
        f"(({alias}.provider LIKE 'tushare%%' "
        f"AND coalesce({alias}.raw->>'factor_semantics','') IN ('','corporate_action_cumulative')) "
        f"OR ({alias}.provider='longhu_qfq_derived' "
        f"AND coalesce({alias}.raw->>'factor_semantics','')='corporate_action_cumulative')))"
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
    return (
        f"({alias}.adj_factor>0 AND {persisted_factor_semantics_sql(alias)})"
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
    if source and not _provider_is_allowed(source):
        return "unknown"
    if _semantic_provider_match(source, explicit):
        return COMPLETE_FACTOR_SEMANTICS
    return explicit or "unknown"


def _provider_is_allowed(provider: str) -> bool:
    """Mirror ``provider LIKE 'tushare%'`` plus the owner-derived provider."""
    return provider.startswith("tushare") or provider == LONGHU_QFQ_FACTOR_PROVIDER


def _semantic_provider_match(provider: str, semantic: str) -> bool:
    if provider.startswith("tushare"):
        return semantic in {"", COMPLETE_FACTOR_SEMANTICS}
    return provider == LONGHU_QFQ_FACTOR_PROVIDER and semantic == COMPLETE_FACTOR_SEMANTICS


def factor_usable(row: Mapping[str, Any] | None, *, provider: str | None = None) -> bool:
    """Evaluate the owner's ``derived_rules.adjustment_factor_usable`` rule."""
    if not row or positive_decimal(row.get("adj_factor")) is None:
        return False
    raw = row.get("raw")
    if not isinstance(raw, Mapping):
        raw = {}
    superseded = raw.get("superseded_at")
    if superseded is None:
        superseded = row.get("superseded_at")
    if superseded is not None:
        return False
    source = str(row.get("provider") or provider or "").strip()
    semantic = str(row.get("factor_semantics") or raw.get("factor_semantics") or "").strip()
    return _semantic_provider_match(source, semantic)


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
    raw = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
    if raw.get("superseded_at") is not None or row.get("superseded_at") is not None:
        return "retired"
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
    return factor_usable(row, provider=provider)


def normalize_factor_row(row: Mapping[str, Any], *, provider: str) -> dict[str, Any]:
    """Validate/annotate a provider row without inventing a factor."""
    factor = positive_decimal(row.get("adj_factor"))
    if not _provider_is_allowed(provider):
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
    "COMPLETE_FACTOR_PROVIDERS", "adjustment_state", "factor_semantics", "factor_usable",
    "normalize_factor_row", "persisted_factor_eligible_sql", "persisted_factor_semantics_sql",
    "positive_decimal", "research_factor_eligible",
]
