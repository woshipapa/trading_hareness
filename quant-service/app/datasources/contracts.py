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
from typing import Final


#: Binding states, from strongest to weakest evidence.
LIVE_VERIFIED: Final = "live_verified"   # observed working from the owner runtime
DECLARED: Final = "declared"             # documented and probed, not yet session-proven
DORMANT: Final = "dormant"               # implemented but no scheduled caller
UNSUPPORTED: Final = "unsupported"       # the upstream refuses it (kept to re-probe)
RETIRED: Final = "retired"               # deliberately taken out of resolution
BINDING_STATES: Final = (LIVE_VERIFIED, DECLARED, DORMANT, UNSUPPORTED, RETIRED)
RESOLVABLE_STATES: Final = frozenset({LIVE_VERIFIED, DECLARED, DORMANT})

CATEGORIES: Final = (
    "quote", "bars", "ticks", "auction", "limits", "sector", "flow", "lhb", "attention",
    "news", "events", "fundamentals", "fund", "reference", "derived",
)
GRAINS: Final = ("realtime", "intraday", "daily", "event", "reference", "periodic")
SCOPES: Final = ("all_a", "watchlist", "per_symbol", "market", "board", "fund")
LICENSES: Final = (
    "licensed", "official_free_api", "public_web", "unofficial_protocol", "local_files", "aggregator", "derived",
)


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
class StrategyDataNeeds:
    strategy: str
    needs: tuple[CapabilityRequirement, ...] = field(default_factory=tuple)
    #: Vendor-specific references still embedded in the strategy code, kept
    #: visible until they are migrated to capability reads.
    legacy_couplings: tuple[str, ...] = ()


__all__ = [
    "BINDING_STATES", "Binding", "CATEGORIES", "Capability", "CapabilityRequirement", "DECLARED", "DORMANT",
    "DataSource", "GRAINS", "LICENSES", "LIVE_VERIFIED", "RESOLVABLE_STATES", "RETIRED", "SCOPES", "SourceLabel",
    "StrategyDataNeeds", "Taxonomy", "UNSUPPORTED",
]
