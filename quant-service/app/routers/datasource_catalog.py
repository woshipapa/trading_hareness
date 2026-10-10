"""Read-only catalog routes for the data-source capability directory."""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..datasources import catalog


class BindingSpecResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    params: dict[str, Any]
    field_map: dict[str, Any]
    unit_factors: dict[str, Any]
    paging: Any | None = None
    max_batch: int | None = None
    limits: dict[str, Any]
    time_semantics: str
    handshake_profile: str | None = None
    agreement: dict[str, Any] = Field(default_factory=dict)


class BindingResponse(BaseModel):
    source: str
    capability: str
    priority: int
    status: str
    store: str | None = None
    adapter: str | None = None
    history: str
    limits: str
    notes: str
    decision_eligible: bool
    spec: BindingSpecResponse | None = None


class TaxonomyResponse(BaseModel):
    key: str
    source: str
    kind: str
    status: str
    preference: int
    notes: str


class CapabilityResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    category: str
    label: str
    grain: str
    scope: str
    fields: list[str]
    schema_: list[dict[str, Any]] = Field(alias="schema")
    time_semantics: str
    description: str
    bindings: list[BindingResponse]
    evidence_locations: list[dict[str, Any]] = Field(default_factory=list)


class DataSourceResponse(BaseModel):
    key: str
    label: str
    upstream: str
    license: str
    protocol: str
    cost: str
    module: str
    credential_env: list[str]
    risks: str
    deploy: str
    capabilities: list[str]


class CatalogResponse(BaseModel):
    version: str
    sources: list[DataSourceResponse]
    capabilities: list[CapabilityResponse]
    taxonomies: list[TaxonomyResponse]
    retired: list[dict[str, Any]]
    non_sector_groups: dict[str, Any]


class CapabilityDetailResponse(CapabilityResponse):
    evidence_locations: list[dict[str, Any]]


def _binding(item: Any) -> dict[str, Any]:
    payload = {
        "source": item.source,
        "capability": item.capability,
        "priority": item.priority,
        "status": item.status,
        "store": item.store,
        "adapter": item.adapter,
        "history": item.history,
        "limits": item.limits,
        "notes": item.notes,
        "decision_eligible": item.decision_eligible,
        "spec": None,
    }
    if item.spec is not None:
        # Every BindingSpec field, so a field added to the contract appears here without a code change.
        payload["spec"] = dataclasses.asdict(item.spec)
    return payload


def _capability(item: Any, bindings: list[Any]) -> dict[str, Any]:
    return {
        "key": item.key,
        "category": item.category,
        "label": item.label,
        "grain": item.grain,
        "scope": item.scope,
        "fields": list(item.fields),
        "schema": [field.__dict__ for field in (item.schema.fields if item.schema else ())],
        "time_semantics": item.time_semantics,
        "description": item.description,
        "bindings": [_binding(binding) for binding in bindings],
        "evidence_locations": catalog.evidence_locations(item.key),
    }


def _document(*, source: str | None, category: str | None, status: str | None) -> dict[str, Any]:
    raw = catalog.catalog_document()
    bindings = catalog.BINDINGS
    if source:
        bindings = tuple(item for item in bindings if item.source == source)
    if status:
        bindings = tuple(item for item in bindings if item.status == status)
    allowed = {item.capability for item in bindings}
    if category:
        allowed &= {key for key, item in catalog.CAPABILITIES.items() if item.category == category}
    raw["capabilities"] = [
        _capability(item, [binding for binding in bindings if binding.capability == item.key])
        for item in sorted(catalog.CAPABILITIES.values(), key=lambda value: value.key)
        if item.key in allowed
    ]
    raw["sources"] = [
        {**source_item, "capabilities": [key for key in source_item["capabilities"] if key in allowed]}
        for source_item in raw["sources"]
        if not source or source_item["key"] == source
    ]
    raw["taxonomies"] = [item.__dict__ for item in sorted(catalog.TAXONOMIES.values(), key=lambda value: value.key)
                          if (not source or item.source == source) and (not status or item.status == status)]
    return raw


def build_datasource_catalog_router() -> APIRouter:
    router = APIRouter(tags=["datasource-catalog"])

    @router.get("/api/v1/datasources/catalog", response_model=CatalogResponse)
    async def datasource_catalog(
        source: str | None = Query(default=None),
        category: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return _document(source=source, category=category, status=status)

    @router.get("/api/v1/datasources/capabilities/{capability}", response_model=CapabilityDetailResponse)
    async def datasource_capability(capability: str) -> dict[str, Any]:
        item = catalog.CAPABILITIES.get(capability)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown datasource capability")
        bindings = [binding for binding in catalog.BINDINGS if binding.capability == capability]
        return {
            **_capability(item, bindings),
            "evidence_locations": catalog.evidence_locations(capability),
        }

    return router


__all__ = ["build_datasource_catalog_router"]
