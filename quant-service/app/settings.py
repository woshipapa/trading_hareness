"""Every environment setting quant-research reads, and one way to read each kind.

Configuration was 140 ``os.getenv`` calls across 42 modules, each with its own
default, clamp and idea of "true"; nothing listed what an operator could set,
and two modules gave the same key different defaults without saying so. This
module is that list. A key read anywhere in ``app/`` must be declared here
(``tests/test_settings.py`` enforces it), and the accessors below - flag,
integer, number, text, csv - parse the way the rest of the code always meant
to: a garbage value falls back to the declared default instead of crashing,
and numbers are clamped to the bounds the caller states.

Tushare's TUSHARE_* keys are deliberately absent: Tushare was retired on
2026-10-08 and its remaining readers are being removed.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


@dataclass(frozen=True)
class Setting:
    name: str
    default: str | None          # as the environment would spell it; None means "unset"
    area: str
    note: str = ""
    secret: bool = False


def _settings(area: str, *rows: tuple) -> list[Setting]:
    return [Setting(row[0], row[1], area, *row[2:]) for row in rows]


SETTINGS: dict[str, Setting] = {item.name: item for item in [
    *_settings(
        "database",
        ("PGHOST", "postgres", "main database host (legacy_stock_brain_repository defaults to 127.0.0.1, the owner's Windows DB)"),
        ("PGPORT", "5432", "main database port (legacy_stock_brain_repository defaults to 55432)"),
        ("PGDATABASE", "n8n", "main database name (legacy_stock_brain_repository defaults to trading_hareness)"),
        ("PGUSER", "n8n", "main database user (legacy_stock_brain_repository defaults to quant_app)"),
        ("PGPASSWORD", "", "database password", True),
        ("QUANT_APPLICATION_NAME", "quant-research", "PostgreSQL application_name; batch tools set their own"),
        ("QUANT_ASYNC_READ_POOL_MIN_SIZE", "1", "async read pool lower bound"),
        ("QUANT_ASYNC_READ_POOL_MAX_SIZE", "16", "async read pool upper bound"),
        ("QUANT_LEGACY_SCHEMA_BOOTSTRAP", "false", "allow the legacy SCHEMA_SQL bootstrap instead of Alembic"),
        ("QUANT_SLOW_TRANSACTION_SECONDS", None, "slow-transaction threshold for the audit (default 5)"),
    ),
    *_settings(
        "runtime",
        ("QUANT_RUNTIME_PROFILE", "full", "full | intraday_edge | research: which loops this process runs"),
        ("QUANT_DATA_DIR", "/var/lib/quant", "local data root (spools, cursors, offline files)"),
        ("BACKGROUND_LOOP_LEASE_SECONDS", "120", "lease length for single-instance background loops"),
        ("POST_CLOSE_REFRESH_LEASE_SECONDS", "1800", "lease length for the post-close refresh"),
        ("QUANT_RUNTIME_MAX_MEMORY_RATIO", None, "memory ratio above which work is refused"),
        ("QUANT_RUNTIME_MIN_FREE_BYTES", None, "free-disk floor below which work is refused"),
        ("QUANT_RUNTIME_WARNING_FREE_BYTES", None, "free-disk level that raises a warning"),
        ("QUANT_PUBLIC_HTTP_PROXY", None, "proxy for public (non-licensed) market sources"),
        ("QUANT_DASHBOARD_PUBLIC_URL", None, "dashboard link placed in alerts"),
        ("APP_GIT_SHA", None, "release provenance, set by the release"),
        ("APP_RELEASE", None, "release label, set by the release"),
        ("APP_BUILD_CREATED_AT", None, "image build time, set by the release"),
        ("PEER_OWNER_CONTRACT_MODE", None, "strict on the owner: refuse writes outside the declared owner contract"),
        ("PEER_OWNER_CONTRACT_RECEIPT_PATH", None, "where the owner contract receipt is read from"),
    ),
    *_settings(
        "security",
        ("QUANT_WRITE_API_KEY", "", "shared full-scope write key (caller 'legacy')", True),
        ("QUANT_WRITE_API_KEYS", None, "named, path-scoped write keys: caller|prefixes|key;...", True),
        ("QUANT_SHARED_READ_API_KEY", "", "key for the licensed read proxy (X-Quant-Read-Key)", True),
        ("QUANT_SHARED_READ_API_BASE_URL", "", "owner read API base when this process reads through it"),
    ),
    *_settings(
        "providers",
        ("QUANT_PROVIDER_GLOBAL_RATE_LIMIT_MAX_WAIT_SECONDS", "5", "longest wait for a shared provider rate slot"),
        ("QUANT_LONGHU_CONFIG_PATH", None, "Longhu vendor configuration file"),
        ("QUANT_LONGHU_DIRECT_ENABLED", "false", "call the Longhu vendor directly instead of through the owner read API"),
        ("QUANT_LONGHU_FULL_MARKET_ENABLED", "false", "use Longhu for the full-market universe and daily close (true on the owner)"),
        ("QUANT_LONGHU_GATEWAY_WORKERS", None, "parallel Longhu gateway workers for full-market pulls"),
        ("LONGHU_MINUTE_BATCH_WORKERS", "4", "parallel Longhu minute-bar requests"),
        ("FUYAO_BULK_DUMP_DIR", "/var/lib/quant/fuyao-dumps", "where Fuyao bulk dumps are staged"),
        ("AKSHARE_ENABLED", "true", "allow AkShare supplements"),
        ("XINHUA_FINANCE_API_URL", None, "Xinhua Finance endpoint"),
        ("XINHUA_FINANCE_API_KEY", None, "Xinhua Finance key", True),
    ),
    *_settings(
        "market snapshots",
        ("MARKET_SNAPSHOT_LICENSED_PROVIDERS", "", "licensed snapshot providers, in order"),
        ("MARKET_SNAPSHOT_ENABLE_PUBLIC_FALLBACK", "true", "fall back to public quotes when licensed ones fail"),
        ("MARKET_SNAPSHOT_ENABLE_PUBLIC_BATCH", "false", "allow the batched public quote path"),
        ("MARKET_SNAPSHOT_PUBLIC_BATCH_SIZE", "80", "symbols per public batch request"),
        ("MARKET_SNAPSHOT_PUBLIC_CONCURRENCY", "2", "concurrent public batch requests"),
        ("MARKET_SNAPSHOT_MIN_COVERAGE", "0.95", "coverage below which a snapshot is not decision-grade"),
        ("MARKET_SNAPSHOT_MIN_UNIVERSE", "1000", "universe size below which a snapshot is refused"),
        ("QUANT_UNIVERSE", "", "explicit comma-separated symbols for legacy explicit-universe syncs"),
    ),
    *_settings(
        "intraday",
        ("INTRADAY_SCAN_INTERVAL_SECONDS", "0", "watchlist scan interval; below 30 disables the scan"),
        ("INTRADAY_WATCHLIST_MAX_SYMBOLS", None, "watchlist admission cap"),
        ("INTRADAY_ORDER_BOOK_ENABLED", "true", "capture order books for the watchlist"),
        ("INTRADAY_BOARD_CURVE_ENABLED", "true", "capture minute board-flow curves"),
        ("INTRADAY_BOARD_CURVE_RETENTION_DAYS", "60", "board-curve retention"),
        ("INTRADAY_BOARD_ROTATION_RETENTION_DAYS", "60", "board-rotation retention"),
        ("INTRADAY_BOARD_ROTATION_MIN_ABS_NET", "1.0", "smallest net flow that counts as a rotation"),
        ("INTRADAY_BOARD_ROTATION_MIN_DELTA", "2.0", "smallest same-source change that counts as a rotation"),
        ("INTRADAY_RULE_INPUT_RETENTION_DAYS", "90", "frozen rule-input retention (at least 60 for replay)"),
        ("INTRADAY_MINUTE_PROFILE_CAPTURE_ENABLED", "true", "save end-of-session minute profiles"),
        ("INTRADAY_MINUTE_PROFILE_RETENTION_DAYS", "90", "minute-profile retention, clamped to 20-365"),
        ("INTRADAY_MINUTE_PROFILE_MAX_SYMBOLS", "40", "symbols per minute-profile capture, clamped to 1-100"),
        ("WATCH_EVIDENCE_MIN_SECONDS", "30", "minimum spacing between watch-evidence writes"),
        ("AUCTION_PULSE_ENABLED", "true", "run the call-auction pulse"),
        ("AUCTION_PULSE_INTERVAL_SECONDS", "2", "auction pulse interval, clamped to 1-30"),
        ("AUCTION_PULSE_FEISHU_COOLDOWN_SECONDS", "30", "auction pulse alert cooldown, clamped to 5-300"),
        ("MARKET_EVENT_CAPTURE_ENABLED", "true", "capture market events"),
        ("ALL_A_LEVEL1_CAPTURE_ENABLED", "true", "capture all-A level-1 snapshots"),
        ("PUBLIC_EVIDENCE_CAPTURE_ENABLED", "true", "capture public evidence"),
        ("QUANT_CORE_INTRADAY_CAPTURE_AT_STOP", "false", "keep core intraday capture when storage is at its stop ratio"),
    ),
    *_settings(
        "post-close and research",
        ("STRATEGY_REVIEW_AUTOMATION_ENABLED", "true", "run the strategy-review loop"),
        ("POST_CLOSE_STRATEGY_AUTOMATION_ENABLED", "true", "run the post-close strategy loop"),
        ("TEN_DAY_LEADER_ROTATION_AUTOMATION_ENABLED", "true", "run the ten-day leader rotation loop"),
        ("DAILY_SUMMARY_AUTOMATION_ENABLED", "true", "build the daily strategy summary"),
        ("DAILY_SUMMARY_FEISHU_ENABLED", "true", "send the daily summary to Feishu"),
        ("POST_CLOSE_PUBLIC_ARCHIVE_ENABLED", "true", "archive public evidence after the close"),
        ("STORAGE_TIERING_MOVER_ENABLED", "true", "move cold data out of the hot database"),
        ("PEER_CLOSE_RESEARCH_ENABLED", "true", "run the peer close research loop"),
        ("THS_CONCEPT_MEMBER_BACKFILL_ENABLED", "true", "run the post-close Fuyao THS membership refresh loop"),
        ("THS_CONCEPT_MEMBER_BACKFILL_BATCH_SIZE", "25", "THS boards per Fuyao membership refresh batch"),
        ("ALL_BOARD_MEMBER_BACKFILL_ENABLED", "true", "run the all-board member backfill loop"),
        ("ALL_BOARD_MEMBER_BACKFILL_BATCH_SIZE", "10", "boards per backfill batch"),
        ("QUANT_PAPER_AUTO_EXECUTION_ENABLED", "false", "let paper decisions execute automatically (paper ledger only)"),
        ("TEACHER_REVIEW_ENABLED", "true", "run teacher-review settlement and plans"),
        ("TEACHER_REVIEW_MINUTE_EXTRA", "0", "extra minute-bar requests for teacher review, bounded by its maximum"),
        ("TEACHER_REVIEW_API", "http://127.0.0.1:8000", "base URL the teacher-review CLI calls"),
    ),
    *_settings(
        "storage and archives",
        ("QUANT_HOT_DATABASE_SOFT_BYTES", None, "hot database soft size"),
        ("QUANT_RESEARCH_STORAGE_SOFT_BYTES", None, "research storage soft size"),
        ("QUANT_RESEARCH_STORAGE_WARNING_RATIO", None, "research storage warning ratio"),
        ("QUANT_RESEARCH_STORAGE_STOP_RATIO", None, "research storage stop ratio"),
        ("QUANT_RAW_OVERFLOW_ARCHIVE_ENABLED", None, "hand raw overflow to the edge archive"),
        ("QUANT_RAW_OVERFLOW_CAPABILITIES", "", "raw capabilities eligible for overflow"),
        ("QUANT_RAW_OVERFLOW_HOT_WINDOW_HOURS", None, "how long raw rows stay hot before overflow"),
        ("QUANT_RAW_OVERFLOW_BATCH_ROWS", None, "rows per overflow batch"),
        ("QUANT_RAW_OVERFLOW_MAX_BATCH_BYTES", None, "bytes per overflow batch"),
        ("QUANT_RAW_OVERFLOW_MAX_QUEUE_BATCHES", None, "queued overflow batches before backpressure"),
        ("QUANT_RAW_OVERFLOW_MAX_SPOOL_BYTES", None, "local spool limit"),
        ("QUANT_RAW_OVERFLOW_WARNING_RATIO", None, "overflow warning ratio"),
        ("QUANT_RAW_OVERFLOW_STOP_RATIO", None, "overflow stop ratio"),
        ("QUANT_EDGE_EVIDENCE_CURSOR_PATH", "/var/lib/quant/edge-evidence-cursor.json", "edge evidence pull cursor"),
        ("QUANT_EDGE_EVIDENCE_PULL_STATUS_PATH", "/var/lib/quant/edge-evidence-pull-status.json", "edge evidence pull status"),
        ("QUANT_EDGE_LIVE_SESSION_ACCEPTANCE_PATH", "", "edge live-session acceptance file"),
    ),
    *_settings(
        "alerts and remote archive",
        ("QUANT_ALERT_WEBHOOK_URL", None, "generic alert webhook"),
        ("QUANT_ALERT_WEBHOOK_TOKEN", None, "token for the generic alert webhook", True),
        ("QUANT_ALERT_FEISHU_WEBHOOK_URL", None, "Feishu bot webhook for alerts", True),
        ("REMOTE_ANALYST_ARCHIVE_BASE_URL", "", "remote analyst archive base URL"),
        ("REMOTE_ANALYST_ARCHIVE_CA_FILE", "", "CA bundle for the remote archive"),
        ("REMOTE_ANALYST_SYNC_MAX_ITEMS", "100", "items per remote archive sync"),
        ("REMOTE_ANALYST_SYNC_MIN_INTERVAL_SECONDS", "15", "minimum spacing between syncs"),
        ("REMOTE_ANALYST_SYNC_REQUEST_INTERVAL_SECONDS", "2", "spacing between sync requests"),
    ),
]}

_TRUE = frozenset({"1", "true", "yes", "on"})


def _raw(name: str, environ: Mapping[str, str] | None) -> str | None:
    if name not in SETTINGS:
        raise KeyError(f"{name} is not declared in app/settings.py")
    env = os.environ if environ is None else environ
    value = env.get(name)
    return SETTINGS[name].default if value is None else value


def flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """1/true/yes/on (any case, surrounding space ignored) is true; anything else is false."""
    return str(_raw(name, environ) or "").strip().lower() in _TRUE


def text(name: str, environ: Mapping[str, str] | None = None) -> str:
    """The value with surrounding space removed; '' when unset and undeclared-by-default."""
    return str(_raw(name, environ) or "").strip()


def csv(name: str, environ: Mapping[str, str] | None = None) -> list[str]:
    return [item.strip() for item in text(name, environ).split(",") if item.strip()]


def _clamp(value, minimum, maximum):
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def integer(name: str, *, minimum: int | None = None, maximum: int | None = None,
            fallback: int | None = None, environ: Mapping[str, str] | None = None) -> int:
    """An int within [minimum, maximum]; an unparsable value gives ``fallback`` (default: the declared default)."""
    try:
        value = int(str(_raw(name, environ)).strip())
    except (TypeError, ValueError):
        if fallback is not None:
            return fallback
        value = int(str(SETTINGS[name].default))
    return _clamp(value, minimum, maximum)


def number(name: str, *, minimum: float | None = None, maximum: float | None = None,
           fallback: float | None = None, environ: Mapping[str, str] | None = None) -> float:
    """A float within [minimum, maximum]; an unparsable value gives ``fallback`` (default: the declared default)."""
    try:
        value = float(str(_raw(name, environ)).strip())
    except (TypeError, ValueError):
        if fallback is not None:
            return fallback
        value = float(str(SETTINGS[name].default))
    return _clamp(value, minimum, maximum)


def inventory() -> list[dict[str, object]]:
    """Every declared setting without its value - safe to print or serve."""
    return [{"name": item.name, "area": item.area, "default": None if item.secret else item.default,
             "secret": item.secret, "note": item.note} for item in SETTINGS.values()]


__all__ = ["SETTINGS", "Setting", "csv", "flag", "integer", "inventory", "number", "text"]


if __name__ == "__main__":  # pragma: no cover - operator listing
    for row in inventory():
        print(f"{row['area']:<26} {row['name']:<48} default={row['default']!s:<10} {row['note']}")
