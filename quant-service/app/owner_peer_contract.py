"""Read and validate the owner's machine-readable peer contract.

The peer must not infer an owner schema from a prose handoff.  This module is
deliberately read-only: it calls the owner's gateway, validates only the
envelope that gateway publishes, and stores a small local receipt so a future
blocking check can prove that the contract was observed at least once.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests


LOGGER = logging.getLogger(__name__)
DEFAULT_MODE = "report_only"
DEFAULT_RECEIPT_PATH = "/var/lib/quant/owner-peer-contract.json"
# These are the two owner projections every peer research read path actually
# consumes.  This is intentionally much smaller than the owner's 200+ readable
# relations and contains no proposed semantic/cold columns.
REQUIRED_OBJECT_COLUMNS = {
    "quant.canonical_bars_daily": ("symbol", "trading_date", "adj_factor", "available_at", "quality_status"),
    "quant.daily_adjustment_factors": ("symbol", "trading_date", "adj_factor", "provider", "available_at", "raw"),
}


class OwnerPeerContractError(RuntimeError):
    """Raised when a blocking owner-contract check cannot pass."""


def contract_mode(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = str(env.get("PEER_OWNER_CONTRACT_MODE", DEFAULT_MODE)).strip().lower()
    if value in {"off", "disabled"}:
        return "off"
    if value in {"block", "blocking", "enforce"}:
        return "block"
    return DEFAULT_MODE


def receipt_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(str(env.get("PEER_OWNER_CONTRACT_RECEIPT_PATH", DEFAULT_RECEIPT_PATH)))


def contract_url(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    base = str(env.get("QUANT_SHARED_READ_API_BASE_URL", "")).strip().rstrip("/")
    return f"{base}/api/v1/peer/contract" if base else ""


def validate_contract(payload: Any) -> list[str]:
    """Return contract-envelope issues without asserting undocumented columns."""
    issues: list[str] = []
    if not isinstance(payload, Mapping):
        return ["payload_not_object"]
    if not str(payload.get("alembic_head") or "").strip():
        issues.append("missing_alembic_head")
    objects = payload.get("objects")
    if not isinstance(objects, list) or not objects:
        issues.append("missing_objects")
    else:
        seen_objects: dict[str, Mapping[str, Any]] = {}
        for index, obj in enumerate(objects):
            if not isinstance(obj, Mapping):
                issues.append(f"objects[{index}]_not_object")
                continue
            if not str(obj.get("name") or "").startswith("quant."):
                issues.append(f"objects[{index}]_missing_quant_name")
            if not isinstance(obj.get("columns"), list):
                issues.append(f"objects[{index}]_missing_columns")
            else:
                seen_objects[str(obj.get("name"))] = obj
        for name, required_columns in REQUIRED_OBJECT_COLUMNS.items():
            obj = seen_objects.get(name)
            if obj is None:
                issues.append(f"missing_required_object:{name}")
                continue
            actual = obj.get("columns") or []
            names = {
                str(column.get("name") or column.get("column_name")) if isinstance(column, Mapping) else str(column)
                for column in actual
            }
            for column in required_columns:
                if column not in names:
                    issues.append(f"{name}:missing_required_column:{column}")
    cold = payload.get("cold_tier")
    if not isinstance(cold, Mapping) or not isinstance(cold.get("tables"), list):
        issues.append("missing_cold_tier")
    enums = payload.get("enumerations")
    if not isinstance(enums, Mapping) or not isinstance(enums.get("factor_semantics"), list):
        issues.append("missing_factor_semantics_enumeration")
    for key in ("not_provided", "endpoints", "rules"):
        if key in payload and not isinstance(payload[key], list):
            issues.append(f"{key}_not_list")
    return issues


def fetch_contract(*, environ: Mapping[str, str] | None = None, timeout_seconds: float = 10.0) -> dict[str, Any]:
    """Fetch the live owner contract using the shared read-only key."""
    env = os.environ if environ is None else environ
    url = contract_url(env)
    key = str(env.get("QUANT_SHARED_READ_API_KEY", "")).strip()
    if not url or not key:
        raise OwnerPeerContractError("owner peer contract URL/key is not configured")
    try:
        response = requests.get(
            url,
            headers={"X-Quant-Read-Key": key},
            timeout=max(1.0, float(timeout_seconds)),
        )
        response.raise_for_status()
        payload = response.json()
    except (OSError, requests.RequestException, ValueError) as error:
        raise OwnerPeerContractError(f"owner peer contract request failed: {type(error).__name__}") from error
    issues = validate_contract(payload)
    if issues:
        raise OwnerPeerContractError("owner peer contract is invalid: " + ", ".join(issues))
    return dict(payload)


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "status": "passed",
        "passed_at": datetime.now(timezone.utc).isoformat(),
        "alembic_head": payload.get("alembic_head"),
        "object_names": sorted(
            str(obj.get("name")) for obj in payload.get("objects", [])
            if isinstance(obj, Mapping) and obj.get("name")
        ),
    }
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _receipt_passed(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            receipt = json.load(handle)
        return receipt.get("status") == "passed" and bool(receipt.get("passed_at"))
    except (OSError, ValueError, AttributeError):
        return False


def verify_owner_peer_contract(*, required: bool, environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Report or enforce the live contract, never inventing owner schema.

    ``report_only`` is the safe rollout mode.  ``block`` only becomes a real
    startup gate after a prior successful report receipt exists; this prevents
    a never-tested assertion from taking both peer containers down.
    """
    if not required:
        return None
    env = os.environ if environ is None else environ
    mode = contract_mode(env)
    if mode == "off":
        LOGGER.warning("owner peer contract check disabled by PEER_OWNER_CONTRACT_MODE")
        return None
    try:
        payload = fetch_contract(environ=env)
    except OwnerPeerContractError as error:
        if mode == "block":
            raise
        LOGGER.error("owner peer contract report failed: %s", error)
        return None
    path = receipt_path(env)
    had_receipt = _receipt_passed(path)
    try:
        _write_receipt(path, payload)
    except OSError as error:
        # Report-only mode must not turn an unwritable optional receipt volume
        # into the same startup outage this contract was introduced to avoid.
        LOGGER.error("could not persist owner peer contract receipt path=%s error=%s", path, error)
        if mode == "block" and not had_receipt:
            LOGGER.warning("owner peer contract passed but no prior receipt exists; keeping report-only behavior")
    LOGGER.info(
        "owner peer contract passed report-only=%s alembic_head=%s objects=%s cold_tables=%s",
        mode != "block" or not had_receipt,
        payload.get("alembic_head"),
        len(payload.get("objects", [])),
        len((payload.get("cold_tier") or {}).get("tables", [])),
    )
    if mode == "block" and not had_receipt:
        LOGGER.warning("owner peer contract has passed once now; blocking will begin on the next restart")
    return payload


__all__ = [
    "OwnerPeerContractError", "contract_mode", "contract_url", "fetch_contract",
    "receipt_path", "validate_contract", "verify_owner_peer_contract",
]
