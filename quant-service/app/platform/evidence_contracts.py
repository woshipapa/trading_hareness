"""Typed provenance contracts for normalized market evidence.

Provider payloads may look similar while having fundamentally different
coverage and decision semantics.  These small contracts make that distinction
explicit before data reaches a strategy or a dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final


@dataclass(frozen=True)
class EvidenceContract:
    """Stable meaning of one normalized evidence stream."""

    key: str
    provider_key: str
    capability: str
    scope: str
    cross_sectional: bool
    decision_eligible: bool
    semantics: str
    schema_version: str = "market-evidence-v1"

    def materialize(self, status: dict[str, Any] | None = None, /, **extra: Any) -> dict[str, Any]:
        """Attach non-negotiable provenance without dropping runtime evidence."""
        observed = dict(status or {})
        observed.update(extra)
        observed.update({
            "evidence_contract": self.key,
            "evidence_schema_version": self.schema_version,
            "provider_key": self.provider_key,
            "capability": self.capability,
            "scope": self.scope,
            "cross_sectional": self.cross_sectional,
            "decision_eligible": self.decision_eligible,
            "semantics": self.semantics,
        })
        return observed


EVIDENCE_CONTRACTS: Final[dict[str, EvidenceContract]] = {
    "fuyao_all_a_snapshot": EvidenceContract(
        key="fuyao_all_a_snapshot", provider_key="fuyao_ths", capability="a_share_prices_snapshot",
        scope="all_a_cross_section", cross_sectional=True, decision_eligible=False,
        semantics="all_a_price_volume_turnover_snapshot_no_main_flow",
    ),
    # The minute the main source failed and the TDX snapshot filled.  Like the main snapshot its
    # price is no decision quote; whether its source may back a flow is the catalog binding's
    # ``decision_eligible``, which the snapshot carries itself and the scan reports beside this.
    "tdx_public_all_a_snapshot": EvidenceContract(
        key="tdx_public_all_a_snapshot", provider_key="tdx_public", capability="all_a_snapshot",
        scope="all_a_cross_section", cross_sectional=True, decision_eligible=False,
        semantics="all_a_price_volume_turnover_snapshot_no_main_flow",
    ),
    "tencent_watch_quote": EvidenceContract(
        key="tencent_watch_quote", provider_key="tencent_free", capability="order_book_quote",
        scope="explicit_watchlist_only", cross_sectional=False, decision_eligible=True,
        semantics="same_scan_batched_watch_price_with_exchange_timestamp",
    ),
    "longhuvip_watch_quote": EvidenceContract(
        key="longhuvip_watch_quote", provider_key="longhuvip", capability="stock_quote",
        scope="explicit_watchlist_only", cross_sectional=False, decision_eligible=True,
        semantics="licensed_same_scan_watch_price_with_exchange_timestamp_not_level2_order_cancellation",
    ),
    "sina_watch_quote": EvidenceContract(
        key="sina_watch_quote", provider_key="sina_free", capability="realtime_quote",
        scope="explicit_watchlist_only", cross_sectional=False, decision_eligible=False,
        semantics="bounded_watch_price_fallback_without_decision_timestamp_contract",
    ),
    # Derived from the same licensed all-A snapshot the price cross-section
    # comes from, so unlike the bounded Eastmoney basket it carries genuine
    # cross-sectional scope.  It deliberately covers only the two fields that
    # are arithmetic definitions; main_net_inflow stays Eastmoney's alone.
    "fuyao_ths_derived_watch_flow": EvidenceContract(
        key="fuyao_ths_derived_watch_flow", provider_key="fuyao_ths", capability="a_share_prices_snapshot",
        scope="all_a_cross_section", cross_sectional=True, decision_eligible=False,
        semantics="volume_ratio_and_turnover_rate_derived_from_licensed_snapshot_volume_and_local_float_shares",
    ),
    "tdx_public_derived_watch_flow": EvidenceContract(
        key="tdx_public_derived_watch_flow", provider_key="tdx_public", capability="all_a_snapshot",
        scope="all_a_cross_section", cross_sectional=True, decision_eligible=False,
        semantics="volume_ratio_and_turnover_rate_derived_from_fallback_snapshot_volume_and_local_float_shares",
    ),
    "eastmoney_watch_flow": EvidenceContract(
        key="eastmoney_watch_flow", provider_key="eastmoney_free", capability="watchlist_flow_quote",
        scope="explicit_watchlist_only", cross_sectional=False, decision_eligible=False,
        semantics="watchlist_public_flow_proxy_not_exchange_order_flow",
    ),
}


#: The contracts of the all-A snapshot and of the flow derived from it, by the provider that
#: served the snapshot.  A status that names no provider (no snapshot) is the main source's.
_MAIN_ALL_A_PROVIDER: Final = "fuyao_ths"
_ALL_A_CONTRACTS: Final[dict[str, tuple[str, str]]] = {
    _MAIN_ALL_A_PROVIDER: ("fuyao_all_a_snapshot", "fuyao_ths_derived_watch_flow"),
    "tdx_public": ("tdx_public_all_a_snapshot", "tdx_public_derived_watch_flow"),
}


def all_a_snapshot_provider(status: dict[str, Any] | None) -> str:
    """The provider a snapshot status says served it; the main source when it names none."""
    return str((status or {}).get("provider_key") or _MAIN_ALL_A_PROVIDER)


def all_a_snapshot_contract_keys(status: dict[str, Any] | None) -> tuple[str, str]:
    """(snapshot contract, derived-flow contract) for the provider that served ``status``.

    A provider with no contract of its own raises: an unreviewed stream is never materialized
    under the main source's name.
    """
    provider = all_a_snapshot_provider(status)
    try:
        return _ALL_A_CONTRACTS[provider]
    except KeyError as error:
        raise ValueError(f"no all-A snapshot evidence contract for provider: {provider}") from error


def evidence_contract(key: str) -> EvidenceContract:
    """Return one declared contract and fail early on an unreviewed stream."""
    try:
        return EVIDENCE_CONTRACTS[key]
    except KeyError as error:
        raise ValueError(f"unknown evidence contract: {key}") from error


def materialize_evidence_status(key: str, status: dict[str, Any] | None = None, /, **extra: Any) -> dict[str, Any]:
    """Attach a contract to a secret-free runtime source status."""
    return evidence_contract(key).materialize(status, **extra)


def evidence_contract_catalog() -> list[dict[str, Any]]:
    """Return a deterministic, secret-free catalog for APIs and agents."""
    return [
        {
            "key": contract.key,
            "provider_key": contract.provider_key,
            "capability": contract.capability,
            "scope": contract.scope,
            "cross_sectional": contract.cross_sectional,
            "decision_eligible": contract.decision_eligible,
            "semantics": contract.semantics,
            "schema_version": contract.schema_version,
        }
        for contract in sorted(EVIDENCE_CONTRACTS.values(), key=lambda item: item.key)
    ]


__all__ = [
    "EVIDENCE_CONTRACTS", "EvidenceContract", "all_a_snapshot_contract_keys", "all_a_snapshot_provider",
    "evidence_contract", "evidence_contract_catalog",
    "materialize_evidence_status",
]
