"""The data-source layer: every upstream behind one capability vocabulary.

Layout::

    contracts.py   Capability / DataSource / Binding types (no I/O)
    catalog.py     every source, capability and binding, with dated status
    resolver.py    capability -> rows, in catalog order, with provenance
    bindings.py    binds this package's adapters to their capabilities
    http.py        bounded public transport + A-share code normalization
    sources/       one module per upstream (fetch + normalize only)
    derived/       self-computed indicators (pure functions)
    collectors/    cadence/archive orchestration with injected sinks
    storage.py     the few reads collectors need
    runtime.py     deps + leased loops, shared by in-process and standalone
    __main__.py    ``python -m app.datasources catalog|validate|collect``

Rules, enforced by ``tests/test_datasource_boundaries.py``: nothing here
imports strategy, rule, router or composition-root code; strategies reach
data through capabilities (catalog / resolver / stored evidence), never by
importing ``app.datasources.sources``.
"""

from .catalog import CAPABILITIES, SOURCES, bindings_for, catalog_document, evidence_locations, validate_catalog
from .contracts import (
    Binding, BindingSpec, CanonicalSchema, Capability, CapabilityEvidence, CapabilityRequest, CapabilityRequirement,
    DataSource, FieldSpec, PURPOSES, QUALITY_STATUSES, QualityReceipt, StrategyDataNeeds,
)
from .resolver import CapabilityResolver, CapabilityResult, CapabilityUnavailable

__all__ = [
    "Binding", "BindingSpec", "CanonicalSchema", "CAPABILITIES", "Capability", "CapabilityEvidence", "CapabilityRequest", "CapabilityRequirement",
    "CapabilityResolver", "CapabilityResult", "CapabilityUnavailable", "DataSource", "PURPOSES", "QUALITY_STATUSES",
    "FieldSpec", "QualityReceipt", "SOURCES", "StrategyDataNeeds", "bindings_for", "catalog_document", "evidence_locations",
    "validate_catalog",
]
