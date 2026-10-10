"""Contracts of the data-source layer: sources, capabilities and bindings.

The vocabulary strategies compose is the *capability* ("limits.limit_up_pool",
"quote.all_a_snapshot") -- never a vendor.  A *source* is one upstream (a
site, a licensed gateway, a local file tree).  A *binding* says that one
source serves one capability, at what priority, with what verified status,
history depth and limits, and where its evidence is stored.

These are declarations only: no I/O, no imports from the rest of the
application, so the catalog can be read by agents, the API, the deploy tools
and tests without starting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Mapping


#: Binding states, from strongest to weakest evidence.
LIVE_VERIFIED: Final = "live_verified"   # observed working from the owner runtime
DECLARED: Final = "declared"             # documented and probed, not yet session-proven
DORMANT: Final = "dormant"               # implemented but no scheduled caller
UNSUPPORTED: Final = "unsupported"       # the upstream refuses it (kept to re-probe)
RETIRED: Final = "retired"               # deliberately taken out of resolution
BINDING_STATES: Final = (LIVE_VERIFIED, DECLARED, DORMANT, UNSUPPORTED, RETIRED)
RESOLVABLE_STATES: Final = frozenset({LIVE_VERIFIED, DECLARED, DORMANT})
PURPOSES: Final = ("research", "replay", "shadow")
QUALITY_STATUSES: Final = ("complete", "partial", "empty", "stale", "invalid", "conflicted")

CATEGORIES: Final = (
    "quote", "bars", "ticks", "auction", "limits", "sector", "flow", "lhb", "attention",
    "news", "events", "fundamentals", "fund", "reference", "derived", "context",
)
GRAINS: Final = ("realtime", "intraday", "daily", "event", "reference", "periodic")
SCOPES: Final = ("all_a", "watchlist", "per_symbol", "market", "board", "fund")
LICENSES: Final = (
    "licensed", "official_free_api", "public_web", "unofficial_protocol", "local_files", "aggregator", "derived",
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    unit: str | None = None
    dtype: str = "any"
    nullable: bool = True
    note: str = ""


@dataclass(frozen=True)
class CanonicalSchema:
    """Canonical fields for one capability; ``fields`` remains a compatibility view."""

    fields: tuple[FieldSpec, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)


@dataclass(frozen=True)
class BindingSpec:
    """Source-specific contract kept separate from the canonical capability."""

    params: Mapping[str, Any] = field(default_factory=dict)
    field_map: Mapping[str, str] = field(default_factory=dict)
    unit_factors: Mapping[str, float] = field(default_factory=dict)
    paging: Any | None = None
    max_batch: int | None = None
    limits: Mapping[str, Any] = field(default_factory=dict)
    time_semantics: str = ""
    handshake_profile: str | None = None
    #: What this binding must agree with before it is promoted, per canonical field (the names ``field_map``
    #: produces, after ``unit_factors``), e.g.
    #: ``{"close": {"reference": "tencent_free", "key": ["symbol", "bar_time"], "rel_tol": 0.0001}}``.
    #: ``reference`` is the source whose binding of the same capability is asked with the same parameters; when that
    #: binding's catalog adapter is only a module, ``reference_adapter`` (``app/<module path>.py:<function>``) names
    #: the function that is read instead, ``reference_params`` ({the function's keyword: the name of a parameter of
    #: this binding}; every parameter under its own name when absent) says what it is given from the check's
    #: parameters and ``reference_fixed`` ({keyword: value}) adds what is constant.
    #: ``key`` names the fields that identify a row (``symbol`` and ``trade_date`` besides the capability's own
    #: fields).  ``scripts/tdx-promote.py check`` reads every row of both sides, joins them on the key and compares the
    #: field on every common row: it agrees when each is within ``rel_tol`` / ``abs_tol`` (the ``math.isclose``
    #: tolerances; one that is missing counts as 0) and the common rows are at least ``min_coverage`` (default 0.95)
    #: of this binding's rows.  An empty mapping means the binding is promoted without a cross-source comparison.
    agreement: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Capability:
    """One interface a strategy can ask for, independent of who serves it."""

    key: str
    category: str
    label: str
    grain: str
    scope: str
    #: Key output fields with units, e.g. ``"volume:shares"``.
    fields: tuple[str, ...]
    #: Point-in-time rule: what ``effective_at`` and ``available_at`` mean.
    time_semantics: str
    description: str = ""
    schema: CanonicalSchema | None = None


@dataclass(frozen=True)
class DataSource:
    """One upstream and its operational facts (never its credential values)."""

    key: str
    label: str
    upstream: str
    license: str
    protocol: str
    cost: str
    module: str
    #: Environment variable *names* holding the credential, if any.
    credential_env: tuple[str, ...] = ()
    risks: str = ""
    deploy: str = "in_process"


@dataclass(frozen=True)
class Binding:
    """``source`` serves ``capability``; lower ``priority`` is tried first."""

    source: str
    capability: str
    priority: int
    status: str
    #: Where the evidence lands: ``table`` or ``table:column=value``.
    store: str | None = None
    #: The implementing function, for traceability (``module:function``).
    adapter: str | None = None
    history: str = ""
    limits: str = ""
    notes: str = ""
    decision_eligible: bool = False
    spec: BindingSpec | None = None


@dataclass(frozen=True)
class SourceLabel:
    """A provenance label stored on evidence rows (``price_source`` etc.).

    Rules test *properties* of a label -- is the quote exchange-timestamped,
    may this flow field feed a rule -- instead of comparing vendor strings.
    """

    label: str
    source: str
    capability: str
    exchange_timestamped: bool = False
    rule_usable_flow: bool = False
    notes: str = ""


@dataclass(frozen=True)
class Taxonomy:
    """One stored sector-membership taxonomy and how far it is trusted."""

    key: str
    source: str
    kind: str
    status: str
    #: Tie-break order among taxonomies of equal coverage (lower first).
    preference: int
    notes: str = ""


@dataclass(frozen=True)
class CapabilityRequirement:
    """What a strategy needs, stated without naming a vendor."""

    capability: str
    required: bool = True
    purpose: str = ""
    #: For ``sector.membership``: the taxonomies this strategy was calibrated
    #: on, in preference order.  A strategy parameter kept as data here, never
    #: a vendor string in the strategy's code.
    taxonomies: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityRequest:
    """Runtime policy for one capability read.

    The catalog describes what a source *can* provide.  This request describes
    what the current consumer is allowed to accept.  Keeping the policy here
    prevents a research fallback from silently becoming a shadow decision
    input while preserving the existing vendor-free call shape.
    """

    capability: str
    purpose: str = "research"
    as_of: Any | None = None
    min_rows: int = 1
    required_fields: tuple[str, ...] = ()
    min_coverage: float | None = None
    max_age_seconds: float | None = None
    require_live_verified: bool = False
    require_decision_eligible: bool = False
    allow_empty: bool = False

    def __post_init__(self) -> None:
        if self.purpose not in PURPOSES:
            raise ValueError(f"unknown capability request purpose: {self.purpose}")
        if self.min_rows < 0:
            raise ValueError("min_rows must be non-negative")
        if self.min_coverage is not None and not 0 <= self.min_coverage <= 1:
            raise ValueError("min_coverage must be between 0 and 1")
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")


@dataclass(frozen=True)
class QualityReceipt:
    """Small, serializable quality record attached to every resolver result."""

    status: str
    row_count: int | None
    coverage: float | None
    effective_at_min: Any | None
    effective_at_max: Any | None
    available_at_min: Any | None
    available_at_max: Any | None
    response_hash: str | None
    warnings: tuple[str, ...] = ()
    schema: str = "canonical"


@dataclass(frozen=True)
class CapabilityEvidence:
    """Optional adapter return envelope.

    Existing adapters may continue returning rows.  New adapters can return
    this envelope to provide source clocks and coverage without changing the
    resolver API again.
    """

    rows: Any
    coverage: float | None = None
    effective_at_min: Any | None = None
    effective_at_max: Any | None = None
    available_at_min: Any | None = None
    available_at_max: Any | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyDataNeeds:
    strategy: str
    needs: tuple[CapabilityRequirement, ...] = field(default_factory=tuple)
    #: Vendor-specific references still embedded in the strategy code, kept
    #: visible until they are migrated to capability reads.
    legacy_couplings: tuple[str, ...] = ()


__all__ = [
    "BINDING_STATES", "Binding", "BindingSpec", "CATEGORIES", "CanonicalSchema", "Capability", "CapabilityEvidence", "CapabilityRequest",
    "CapabilityRequirement", "DECLARED", "DORMANT", "DataSource", "GRAINS", "LICENSES", "LIVE_VERIFIED",
    "PURPOSES", "QUALITY_STATUSES", "QualityReceipt", "RESOLVABLE_STATES", "RETIRED", "SCOPES", "SourceLabel",
    "StrategyDataNeeds", "FieldSpec", "Taxonomy", "UNSUPPORTED",
]
