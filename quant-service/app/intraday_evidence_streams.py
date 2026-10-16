"""Fixed destination and idempotency contracts for delayed evidence streams."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceStream:
    name: str
    target_table: str
    idempotency_key: tuple[str, ...]
    delayed: bool
    status: str


EVIDENCE_STREAMS: dict[str, EvidenceStream] = {
    "raw_market_observations": EvidenceStream(
        "raw_market_observations", "quant.raw_market_observations",
        ("provider_key", "capability", "market", "symbol", "effective_at", "payload_sha256"),
        True, "implemented",
    ),
    "intraday_quote_observations": EvidenceStream(
        "intraday_quote_observations", "quant.intraday_quote_observations",
        ("symbol", "source_name", "observed_at"), True, "adapter_ready_requires_scan_fk",
    ),
    "intraday_board_flow_snapshots": EvidenceStream(
        "intraday_board_flow_snapshots", "quant.intraday_board_flow_snapshots",
        ("snapshot_minute",), True, "adapter_ready",
    ),
    "intraday_minute_sessions": EvidenceStream(
        "intraday_minute_sessions", "quant.intraday_minute_sessions",
        ("symbol", "trading_date", "minute_bucket", "source_name"), True, "adapter_ready",
    ),
}


def stream_contract(name: str) -> EvidenceStream:
    try:
        return EVIDENCE_STREAMS[name]
    except KeyError as error:
        raise ValueError(f"unknown evidence target stream: {name}") from error


__all__ = ["EVIDENCE_STREAMS", "EvidenceStream", "stream_contract"]
