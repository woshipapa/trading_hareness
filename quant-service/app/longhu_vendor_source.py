"""LonghuVIP full-market evidence transport.

This module owns network and vendor-contract concerns only.  It deliberately
does not know about PostgreSQL.  Every vendor list request is hard capped at
300 records, matching the purchased account's verified physical request
limit; larger logical reads are paginated by the caller here.

``main_net`` is the vendor's order-size-classified field 13.  It is not
institution identity and it is not Level-2 cancellation/order-book evidence.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo

import requests

from .licensed_stock_api import execute as execute_licensed_stock_api


MAX_PAGE_SIZE = 300
MAX_TENCENT_BATCH_SIZE = 80
FLOW_CONVENTION = "longhuvip_zs_stocklist_main_net_field13"
DEFAULT_CONFIG_PATH = Path.home() / ".stock-brain" / "longhu_vendor.json"
USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 14; V2178A Build/UP1A.231005.007)"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def direct_access_enabled() -> bool:
    """Whether this process is the licensed owner-side vendor adapter."""
    return os.getenv("QUANT_LONGHU_DIRECT_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def market_today(now: datetime | None = None) -> date:
    """Return today's exchange date independently of the host timezone."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(MARKET_TIMEZONE).date()


def safe_page_size(value: int) -> int:
    requested = int(value)
    if requested <= 0:
        raise ValueError("Longhu vendor page size must be positive")
    return min(requested, MAX_PAGE_SIZE)


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_stock_symbol(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", raw):
        return raw
    digits = "".join(character for character in raw if character.isdigit())[-6:]
    if len(digits) != 6 or digits in {"399001", "399006"}:
        return None
    if digits in {"000001", "000688"} and raw.lower().startswith("sh"):
        return None
    if digits.startswith("6"):
        return f"{digits}.SH"
    if digits.startswith(("0", "3")):
        return f"{digits}.SZ"
    if digits.startswith(("4", "8", "9")):
        return f"{digits}.BJ"
    return None


def _stock_code(value: Any) -> str | None:
    symbol = normalize_stock_symbol(value)
    return symbol.split(".", 1)[0] if symbol else None


def parse_stock_snapshot_payload(payload: Mapping[str, Any], symbol: str) -> dict[str, Any] | None:
    """Normalize one ``GetStockPanKou`` response for the live watch pipeline.

    The vendor response exposes an exchange timestamp.  Keeping that timestamp
    separate from our receipt time lets the existing freshness gate reject a
    delayed response instead of treating a successful HTTP request as fresh.
    """
    normalized = normalize_stock_symbol(symbol)
    code = _stock_code(symbol)
    real = payload.get("real") if isinstance(payload.get("real"), Mapping) else {}
    price = _number(real.get("last_px"))
    if not normalized or not code or _stock_code(payload.get("code")) != code or price is None or price <= 0:
        return None
    day = "".join(character for character in str(payload.get("day") or "") if character.isdigit())[:8]
    quote_time = "".join(character for character in str(real.get("time") or "") if character.isdigit())[:6]
    order_book = parse_longhu_order_book(payload.get("weituo"))
    return {
        "ts_code": normalized,
        "name": str(payload.get("name") or normalized),
        "price": price,
        "pre_close": _number(payload.get("preclose_px")),
        "open": _number(real.get("open_px")),
        "high": _number(real.get("high_px")),
        "low": _number(real.get("low_px")),
        "pct_change": _number(real.get("px_change_rate")),
        "volume": _number(real.get("total_amount")),
        "amount": _number(real.get("total_turnover")),
        "turnover_rate": _number(real.get("turnover_ratio")),
        "volume_ratio": _number(real.get("vol_ratio")),
        "amplitude": _number(real.get("amplitude")),
        "pe_ttm": _number(real.get("TTMPeRate")),
        "pb": _number(real.get("dyn_pb_rate")),
        "trade_date": day or None,
        "trade_time": f"{day}{quote_time}" if len(day) == 8 and len(quote_time) == 6 else None,
        "order_book": order_book,
        "raw": {"provider": "longhuvip", "action": "GetStockPanKou"},
    }


def parse_longhu_order_book(value: Any) -> dict[str, Any] | None:
    """Normalize Longhu's ``weituo`` ten-level book without inventing trades.

    Longhu uses ``b1..b10``/``s1..s10`` for price and size.  Empty sides are
    retained because a one-sided limit book is meaningful evidence.  The
    result is intentionally source-labelled and remains research evidence;
    it is not treated as exchange-level order-cancellation data.
    """
    if not isinstance(value, Mapping):
        return None

    def levels(prefix: str) -> list[dict[str, float]]:
        result: list[dict[str, float]] = []
        for index in range(1, 11):
            raw_level = value.get(f"{prefix}{index}")
            if not isinstance(raw_level, (list, tuple)) or len(raw_level) < 2:
                continue
            price = _number(raw_level[0])
            size = _number(raw_level[1])
            if price is None or price <= 0 or size is None or size < 0:
                continue
            result.append({"price": price, "size": size})
        return result

    bids, asks = levels("b"), levels("s")
    if not bids and not asks:
        return None
    side = "bid_only" if bids and not asks else "ask_only" if asks and not bids else "two_sided"
    return {
        "bids": bids,
        "asks": asks,
        "book_side": side,
        "one_sided_book": side != "two_sided",
        "seal_volume_lot": bids[0]["size"] if side == "bid_only" else asks[0]["size"] if side == "ask_only" else None,
        "total_bid_lot": _number(value.get("totalb")),
        "total_ask_lot": _number(value.get("totals")),
        "source": "longhuvip:GetStockPanKou",
    }


def _valid_trade_date(value: Any) -> str | None:
    """Return an exchange date only when the vendor supplied a real YYYYMMDD."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    candidate = digits[:8]
    if len(candidate) != 8:
        return None
    try:
        datetime.strptime(candidate, "%Y%m%d")
    except ValueError:
        return None
    return candidate


def _minute_clock(value: Any) -> tuple[str | None, str | None]:
    """Extract HHMM and (when embedded) YYYYMMDD from a vendor time token."""
    text = str(value or "").strip()
    compact = "".join(character for character in text if character.isdigit())
    embedded_date = _valid_trade_date(compact[:8]) if len(compact) >= 8 else None
    if embedded_date and len(compact) >= 12:
        clock = compact[8:12]
    else:
        match = re.search(r"(?<!\d)([01]\d|2[0-3])[:]?([0-5]\d)(?!\d)", text)
        clock = f"{match.group(1)}{match.group(2)}" if match else None
    return clock if clock and re.fullmatch(r"\d{4}", clock) else None, embedded_date


def parse_stock_minute_payload(
    payload: Mapping[str, Any], symbol: str, *, require_trade_date: bool = False,
) -> list[dict[str, Any]]:
    """Normalize per-minute Longhu rows without inventing Level-2 semantics.

    ``GetStockTrendIncremental`` commonly returns ``HH:MM`` only.  Such rows
    remain useful for offline shape inspection, but the live source methods
    request ``require_trade_date=True`` so a previous-session response cannot
    silently enter the current-session feature path.
    """
    normalized = normalize_stock_symbol(symbol)
    if not normalized:
        return []
    payload_date = None
    for key in ("trade_date", "trading_date", "day", "date"):
        payload_date = _valid_trade_date(payload.get(key))
        if payload_date:
            break
    rows: list[dict[str, Any]] = []
    cumulative_volume = 0.0
    for raw in payload.get("trend") or []:
        if not isinstance(raw, list) or len(raw) < 4:
            continue
        minute, embedded_date = _minute_clock(raw[0])
        trade_date = embedded_date or payload_date
        price = _number(raw[1])
        volume_lot = max(0.0, _number(raw[3]) or 0.0)
        if minute is None or price is None or price <= 0:
            continue
        cumulative_volume += volume_lot
        average_price = _number(raw[2])
        rows.append({
            "symbol": normalized,
            "time": minute,
            "trade_date": trade_date,
            "trade_time": (
                f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} "
                f"{minute[:2]}:{minute[2:]}:00"
            ) if trade_date else None,
            "close": price,
            "vwap": average_price,
            "volume_lot": volume_lot,
            "vol": volume_lot,
            "amount": round((average_price or price) * volume_lot * 100, 4),
            "cumulative_volume_lot": cumulative_volume,
            "cumulative_segment": 0 if minute <= "1130" else 1,
            "is_complete": True,
            "source": "longhuvip:GetStockTrendIncremental",
        })
    if rows:
        rows[-1]["is_complete"] = False
    if require_trade_date and rows and any(not row.get("trade_date") for row in rows):
        raise RuntimeError("Longhu minute rows missing exchange date")
    if require_trade_date and not rows:
        raise RuntimeError("Longhu minute returned no dated rows")
    return rows


def current_session_minute_rows(
    rows: Iterable[Mapping[str, Any]], *, observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fail closed unless every Longhu row belongs to the exchange session.

    The vendor's trend endpoint can return a perfectly valid-looking prior
    session when its cache lags.  It must never be relabelled as today.  The
    caller may pass its scan timestamp for deterministic replay tests.
    """
    expected = market_today(observed_at)
    expected_text = expected.strftime("%Y%m%d")
    materialized = [dict(row) for row in rows if isinstance(row, Mapping)]
    if not materialized:
        return []
    dates = {str(row.get("trade_date") or "") for row in materialized}
    if dates != {expected_text}:
        raise RuntimeError("Longhu minute rows are stale or span multiple exchange dates")
    return materialized


@dataclass(frozen=True)
class LonghuVendorConfig:
    token: str
    user_id: str
    device_id: str
    version: str = "5.20.0.2"
    ranking_device_id: str = "20ad85ca-becb-3bed-b3d4-30032a0f5923"
    page_size: int = MAX_PAGE_SIZE
    plate_page_size: int = 60
    timeout_seconds: float = 20.0
    retries: int = 3
    workers: int = 12

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "LonghuVendorConfig":
        result = cls(
            token=str(payload.get("token") or "").strip(),
            user_id=str(payload.get("user_id") or "").strip(),
            device_id=str(payload.get("device_id") or "").strip(),
            version=str(payload.get("version") or "5.20.0.2").strip(),
            ranking_device_id=str(payload.get("ranking_device_id") or cls.ranking_device_id),
            page_size=safe_page_size(int(payload.get("page_size") or MAX_PAGE_SIZE)),
            plate_page_size=safe_page_size(int(payload.get("plate_page_size") or 60)),
            timeout_seconds=float(payload.get("timeout_seconds") or 20.0),
            retries=max(1, int(payload.get("retries") or 3)),
            workers=max(1, min(24, int(payload.get("workers") or 12))),
        )
        if not result.token or not result.user_id or not result.device_id or not result.version:
            raise ValueError("Longhu token, user_id, device_id and version are required")
        return result

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LonghuVendorConfig":
        resolved = Path(path or os.getenv("QUANT_LONGHU_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
        return cls.from_mapping(json.loads(resolved.read_text(encoding="utf-8")))


def configured(path: str | Path | None = None) -> bool:
    """Return whether this runtime has a permitted Longhu transport.

    Workstations consume the owner's normalized gateway. A local vendor
    credential file is ignored unless this process is explicitly running on
    the licensed owner host with direct access enabled.
    """
    if os.getenv("QUANT_SHARED_READ_API_BASE_URL", "").strip() and os.getenv(
        "QUANT_SHARED_READ_API_KEY", ""
    ).strip():
        return True
    if not direct_access_enabled():
        return False
    try:
        LonghuVendorConfig.load(path)
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


class LonghuIntradaySource(Protocol):
    """Small contract shared by the local licensed and remote gateway clients."""

    def watch_quotes(
        self, symbols: Iterable[str], *, max_symbols: int = MAX_PAGE_SIZE,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...

    def stock_quote(self, symbol: str) -> dict[str, Any]: ...

    def stock_minutes(self, symbol: str) -> list[dict[str, Any]]: ...

    def raw_call(self, request: Mapping[str, Any]) -> dict[str, Any]: ...


class SharedLonghuReadSource:
    """Read normalized licensed evidence through the owner's gateway.

    The upstream Longhu credential never leaves the owner's machine. A peer
    only receives normalized rows and cannot widen one logical request beyond
    the same 300-symbol ceiling enforced by the local adapter.
    """

    def __init__(
        self, base_url: str | None = None, read_key: str | None = None,
        *, timeout_seconds: float = 30.0, stock_api_timeout_seconds: float = 600.0,
    ) -> None:
        self.base_url = str(base_url or os.getenv("QUANT_SHARED_READ_API_BASE_URL") or "").rstrip("/")
        self.read_key = str(read_key or os.getenv("QUANT_SHARED_READ_API_KEY") or "").strip()
        if not self.base_url or not self.read_key:
            raise ValueError("shared Longhu base URL and read key are required")
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        # The generic stock API may fan one logical request into multiple
        # physical calls. Keep its transport budget aligned with the public
        # 600-second call boundary instead of the 30-second compatibility GET.
        self.stock_api_timeout_seconds = max(self.timeout_seconds, float(stock_api_timeout_seconds))
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({"X-Quant-Read-Key": self.read_key, "Accept": "application/json"})

    def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self._session.get(
            f"{self.base_url}{path}", params=dict(params or {}), timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("shared Longhu gateway response must be an object")
        return payload

    def _post(self, path: str, *, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            f"{self.base_url}{path}", json=dict(payload), timeout=self.stock_api_timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise TypeError("shared stock API response must be an object")
        return result

    @staticmethod
    def _single_payload(result: Mapping[str, Any], *, action: str) -> dict[str, Any]:
        """Extract one documented-call payload without hiding partial pages.

        The gateway deliberately returns the raw response under ``pages``.  A
        single-security quote/minute call must produce exactly one page; if the
        owner returns a malformed or multi-page response, fail closed instead
        of silently parsing only the first page.
        """
        if not isinstance(result, Mapping):
            raise TypeError(f"shared Longhu {action} response envelope must be an object")
        pages = result.get("pages")
        if not isinstance(pages, list) or len(pages) != 1:
            raise RuntimeError(f"shared Longhu {action} returned an unexpected page count")
        page = pages[0]
        payload = page.get("payload") if isinstance(page, Mapping) else None
        if not isinstance(payload, dict):
            raise TypeError(f"shared Longhu {action} payload must be an object")
        errcode = payload.get("errcode")
        if errcode not in (None, "", 0, "0"):
            raise RuntimeError(f"Longhu {action} returned errcode={errcode}")
        return payload

    def _call_single(self, *, target: str, action: str, controller: str,
                     params: Mapping[str, Any]) -> dict[str, Any]:
        request = {
            "target": target,
            "path": "/w1/api/index.php",
            "params": {"a": action, "c": controller, **dict(params)},
        }
        return self._single_payload(self.raw_call(request), action=action)

    def watch_quotes(
        self, symbols: Iterable[str], *, max_symbols: int = MAX_PAGE_SIZE,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        limit = max(1, min(MAX_PAGE_SIZE, int(max_symbols)))
        ordered = list(dict.fromkeys(
            symbol for value in symbols if (symbol := normalize_stock_symbol(value)) is not None
        ))
        selected = ordered[:limit]
        if not selected:
            return [], {
                "status": "completed", "requested": 0, "selected": 0, "received": 0,
                "truncated": False, "max_symbols": limit, "errors": [],
                "source": "shared-longhu-gateway",
            }
        payload = self._get("/licensed/longhu/quotes", params={"symbols": ",".join(selected)})
        rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
        status = payload.get("source_status") if isinstance(payload.get("source_status"), dict) else {}
        return rows, {
            **status,
            "requested": len(ordered),
            "selected": len(selected),
            "received": len(rows),
            "truncated": len(ordered) > len(selected),
            "max_symbols": limit,
            "transport": "shared_gateway",
        }

    def stock_quote(self, symbol: str) -> dict[str, Any]:
        """Fetch one quote through the complete owner-gateway contract.

        Unlike the normalized batch route, this preserves the documented
        ``GetStockPanKou`` request/response path and its exchange timestamp.
        """
        code = _stock_code(symbol)
        if not code:
            raise ValueError(f"unsupported Longhu stock symbol: {symbol}")
        payload = self._call_single(
            target="longhu_quote", action="GetStockPanKou", controller="StockL2Data",
            params={"apiv": "w41", "StockID": code},
        )
        parsed = parse_stock_snapshot_payload(payload, symbol)
        if parsed is None:
            raise RuntimeError(f"Longhu quote missing or mismatched for {code}")
        return parsed

    def stock_minutes(self, symbol: str) -> list[dict[str, Any]]:
        normalized = normalize_stock_symbol(symbol)
        if not normalized:
            raise ValueError(f"unsupported Longhu stock symbol: {symbol}")
        code = _stock_code(normalized)
        assert code is not None
        payload = self._call_single(
            target="longhu_quote", action="GetStockTrendIncremental", controller="StockL2Data",
            params={"apiv": "w41", "Type": 1, "StockID": code},
        )
        rows = parse_stock_minute_payload(payload, normalized, require_trade_date=True)
        if not rows:
            raise RuntimeError(f"shared Longhu minute returned no rows for {normalized}")
        return rows

    def raw_call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Forward the complete documented call contract to the owner gateway."""
        return self._post("/licensed/stock-api/call", payload=request)


def intraday_source() -> LonghuIntradaySource:
    """Return the shared gateway, or an explicitly owner-enabled source.

    Direct vendor access is opt-in for the licensed owner service and is not a
    fallback for a workstation whose SSH/shared gateway is unavailable.
    """
    if os.getenv("QUANT_SHARED_READ_API_BASE_URL", "").strip() and os.getenv(
        "QUANT_SHARED_READ_API_KEY", ""
    ).strip():
        return SharedLonghuReadSource()
    if direct_access_enabled():
        return LonghuVendorSource()
    raise ValueError(
        "Longhu direct access is disabled; configure QUANT_SHARED_READ_API_BASE_URL "
        "and QUANT_SHARED_READ_API_KEY for the SSH-forwarded gateway"
    )


def parse_industry_stock_row(row: Any, trade_date: date, plate_id: str) -> dict[str, Any] | None:
    if not isinstance(row, list) or len(row) < 26:
        return None
    symbol = normalize_stock_symbol(row[0])
    close, main_net = _number(row[5]), _number(row[13])
    if not symbol or close is None or close <= 0 or main_net is None:
        return None
    return {
        "symbol": symbol,
        "trade_date": trade_date.strftime("%Y%m%d"),
        "name": str(row[1] or symbol),
        "close": close,
        "pct_chg": _number(row[6]),
        "amount": _number(row[7]),
        "main_net": main_net,
        "volume_ratio": _number(row[21]),
        "turnover_rate": _number(row[25]),
        "total_mv": _number(row[37]) if len(row) > 37 else None,
        "circ_mv": _number(row[38]) if len(row) > 38 else None,
        "pb": _number(row[53]) if len(row) > 53 else None,
        "pe": _number(row[61]) if len(row) > 61 else None,
        "plate_id": str(plate_id),
        "flow_convention": FLOW_CONVENTION,
        "raw": {"vendor_row": row, "plate_id": str(plate_id), "row_length": len(row)},
    }


def parse_tencent_quote_text(text: str, requested: Mapping[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in re.finditer(r'v_([a-z0-9]+)="([^"]*)";', text, re.IGNORECASE):
        request_key, fields = match.group(1).lower(), match.group(2).split("~")
        symbol = requested.get(request_key)
        if not symbol or len(fields) <= 37:
            continue
        price, pre_close = _number(fields[3]), _number(fields[4])
        timestamp = str(fields[30] or "")
        if price is None or price <= 0 or pre_close is None or len(timestamp) < 8:
            continue
        result.append({
            "ts_code": symbol,
            "name": str(fields[1] or symbol),
            "trade_date": timestamp[:8],
            "open": _number(fields[5]),
            "high": _number(fields[33]),
            "low": _number(fields[34]),
            "close": price,
            "pre_close": pre_close,
            "vol": _number(fields[6]),
            # Tencent field 37 is 10k CNY.  Store daily amount in CNY for this
            # composite provider; the source label prevents unit ambiguity.
            "amount": (_number(fields[37]) * 10_000 if _number(fields[37]) is not None else None),
            "pct_chg": _number(fields[32]),
        })
    return result


class LonghuVendorSource:
    def __init__(self, config: LonghuVendorConfig | None = None) -> None:
        self.config = config or LonghuVendorConfig.load()
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,*/*"})

    def _credentials(self) -> dict[str, Any]:
        return {
            "PhoneOSNew": 1,
            "DeviceID": self.config.device_id,
            "VerSion": self.config.version,
            "Token": self.config.token,
            "UserID": self.config.user_id,
        }

    def _json(self, url: str, params: Mapping[str, Any], *, authenticate: bool = True) -> dict[str, Any]:
        merged = self._credentials() if authenticate else {"PhoneOSNew": 1}
        merged.update({key: value for key, value in params.items() if value is not None})
        if "st" in merged:
            merged["st"] = safe_page_size(int(merged["st"]))
        error: Exception | None = None
        for attempt in range(self.config.retries):
            try:
                response = self._session.get(url, params=merged, timeout=self.config.timeout_seconds)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("Longhu top-level response must be an object")
                if payload.get("errcode") is not None and str(payload.get("errcode")) != "0":
                    raise RuntimeError(f"Longhu errcode={payload.get('errcode')} action={merged.get('a')}")
                return payload
            except Exception as caught:  # upstream transport has heterogeneous errors
                error = caught
                if attempt + 1 < self.config.retries:
                    time.sleep(0.35 * (attempt + 1))
        assert error is not None
        raise error

    def raw_call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Call any documented stock endpoint through the owner-held identity."""
        batch = request.get("batch") if isinstance(request.get("batch"), Mapping) else {}
        return execute_licensed_stock_api(
            session=self._session,
            config=self.config,
            target_key=str(request.get("target") or ""),
            path=str(request["path"]) if request.get("path") is not None else None,
            params=request.get("params") if isinstance(request.get("params"), Mapping) else {},
            batch_param=str(batch.get("param")) if batch.get("param") else None,
            batch_values=batch.get("values") if isinstance(batch.get("values"), list) else (),
            batch_separator=str(batch.get("separator") or ","),
        )

    def stock_quote(self, symbol: str) -> dict[str, Any]:
        """Fetch one exchange-timestamped quote from the licensed endpoint."""
        code = _stock_code(symbol)
        if not code:
            raise ValueError(f"unsupported Longhu stock symbol: {symbol}")
        payload = self._single_raw_call_payload(
            {
                "target": "longhu_quote",
                "path": "/w1/api/index.php",
                "params": {"a": "GetStockPanKou", "c": "StockL2Data", "apiv": "w41", "StockID": code},
            },
            action="GetStockPanKou",
        )
        parsed = parse_stock_snapshot_payload(payload, symbol)
        if parsed is None:
            raise RuntimeError(f"Longhu quote missing or mismatched for {code}")
        return parsed

    def watch_quotes(self, symbols: Iterable[str], *, max_symbols: int = MAX_PAGE_SIZE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Fetch a bounded explicit watch basket; never widen to an all-A scan."""
        limit = max(1, min(MAX_PAGE_SIZE, int(max_symbols)))
        ordered = list(dict.fromkeys(
            symbol for value in symbols if (symbol := normalize_stock_symbol(value)) is not None
        ))
        selected = ordered[:limit]
        rows: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(
            max_workers=min(self.config.workers, len(selected) or 1), thread_name_prefix="longhu-watch",
        ) as pool:
            futures = {pool.submit(self.stock_quote, symbol): symbol for symbol in selected}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    rows[symbol] = future.result()
                except Exception as error:  # one symbol must not abort the basket
                    errors.append(f"{symbol}:{type(error).__name__}:{error}")
        return [rows[symbol] for symbol in selected if symbol in rows], {
            "status": "completed" if len(rows) == len(selected) else "partial" if rows else "failed",
            "requested": len(ordered),
            "selected": len(selected),
            "received": len(rows),
            "truncated": len(ordered) > len(selected),
            "max_symbols": limit,
            "errors": errors[:20],
            "source": "longhuvip:GetStockPanKou",
        }

    def stock_minutes(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch the current session minute path for one explicit security."""
        code = _stock_code(symbol)
        if not code:
            raise ValueError(f"unsupported Longhu stock symbol: {symbol}")
        payload = self._single_raw_call_payload(
            {
                "target": "longhu_quote",
                "path": "/w1/api/index.php",
                "params": {
                    "a": "GetStockTrendIncremental", "c": "StockL2Data", "apiv": "w41",
                    "Type": 1, "StockID": code,
                },
            },
            action="GetStockTrendIncremental",
        )
        rows = parse_stock_minute_payload(payload, symbol, require_trade_date=True)
        if not rows:
            raise RuntimeError(f"Longhu minute returned no rows for {code}")
        return rows

    def _single_raw_call_payload(self, request: Mapping[str, Any], *, action: str) -> dict[str, Any]:
        """Extract one page from the same generic contract used by peers."""
        result = self.raw_call(request)
        if not isinstance(result, Mapping):
            raise TypeError(f"Longhu {action} response envelope must be an object")
        pages = result.get("pages")
        if not isinstance(pages, list) or len(pages) != 1:
            raise RuntimeError(f"Longhu {action} returned an unexpected page count")
        page = pages[0]
        payload = page.get("payload") if isinstance(page, Mapping) else None
        if not isinstance(payload, dict):
            raise TypeError(f"Longhu {action} payload must be an object")
        errcode = payload.get("errcode")
        if errcode not in (None, "", 0, "0"):
            raise RuntimeError(f"Longhu {action} returned errcode={errcode}")
        return payload

    def industry_plate_catalog(self) -> list[dict[str, Any]]:
        url = "https://apphq.longhuvip.com/w1/api/index.php"
        offset, result, seen = 0, [], set()
        for _ in range(10):
            payload = self._json(url, {
                "Order": 1, "a": "RealRankingInfo", "st": self.config.plate_page_size,
                "apiv": "w26", "Type": 1, "c": "ZhiShuRanking",
                "DeviceID": self.config.ranking_device_id, "Index": offset, "ZSType": 4,
            }, authenticate=False)
            rows = payload.get("list") or []
            for row in rows:
                plate = str(row[0] if isinstance(row, list) and row else "").strip()
                if plate and plate not in seen:
                    seen.add(plate)
                    result.append({
                        "sector_key": plate,
                        "label": str(row[1] if len(row) > 1 else plate),
                        "strength": _number(row[2]) if len(row) > 2 else None,
                        "change_pct": _number(row[3]) if len(row) > 3 else None,
                        "speed": _number(row[4]) if len(row) > 4 else None,
                        "amount": _number(row[5]) if len(row) > 5 else None,
                        "net_inflow": _number(row[6]) if len(row) > 6 else None,
                        "volume_ratio": _number(row[9]) if len(row) > 9 else None,
                        "taxonomy_key": "longhu_ths_industry",
                    })
            total = int(_number(payload.get("Count")) or 0)
            if not rows or len(rows) < self.config.plate_page_size or total and len(result) >= total:
                break
            offset += len(rows)
        if len(result) < 90:
            raise RuntimeError(f"Longhu industry coverage too small: {len(result)}")
        return result

    def industry_plates(self) -> list[str]:
        return [row["sector_key"] for row in self.industry_plate_catalog()]

    def plate_day(self, plate_id: str, trade_date: date, *, live: bool) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(10):
            host = ("https://apphwshhq.longhuvip.com/w1/api/index.php" if live
                    else "https://apphis.longhuvip.com/w1/api/index.php")
            params: dict[str, Any] = {
                "Order": 1, "a": "ZhiShuStockList_W8", "st": self.config.page_size,
                "c": "ZhiShuRanking", "old": 1, "IsZZ": 0, "Index": offset,
                "REnd": 1500, "apiv": "w41", "Type": 6, "IsKZZType": 0,
                "PlateID": plate_id, "TSZB_Type": 0, "filterType": 0,
            }
            params["RStart" if live else "Date"] = "0925" if live else trade_date.isoformat()
            payload = self._json(host, params)
            rows = payload.get("list") or []
            for row in rows:
                parsed = parse_industry_stock_row(row, trade_date, plate_id)
                if parsed and parsed["symbol"] not in seen:
                    seen.add(parsed["symbol"])
                    result.append(parsed)
            total = int(_number(payload.get("Count")) or 0)
            if not rows or len(rows) < self.config.page_size or total and len(result) >= total:
                break
            offset += len(rows)
        return result

    def full_market_vendor_rows(
        self, trade_date: date, *, plate_ids: Iterable[str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        plates = list(plate_ids) if plate_ids is not None else self.industry_plates()
        # Provider selection is exchange-date based.  ``date.today()`` follows
        # the container timezone (which may be UTC or the operator's laptop),
        # so use the single market-clock contract used by the rest of the
        # service.  This prevents a just-after-midnight UTC request from
        # accidentally using the live endpoint for the prior Shanghai session.
        today = market_today()
        live = trade_date == today
        by_symbol: dict[str, dict[str, Any]] = {}
        conflicts: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        successful = 0
        with ThreadPoolExecutor(max_workers=min(self.config.workers, len(plates)), thread_name_prefix="longhu-market") as pool:
            futures = {pool.submit(self.plate_day, plate, trade_date, live=live): plate for plate in plates}
            for future in as_completed(futures):
                plate = futures[future]
                try:
                    rows = future.result()
                    successful += 1
                    for row in rows:
                        existing = by_symbol.get(row["symbol"])
                        if existing and existing["main_net"] != row["main_net"]:
                            conflicts.append({"symbol": row["symbol"], "plates": [existing["plate_id"], plate]})
                            continue
                        by_symbol[row["symbol"]] = row
                except Exception as error:
                    errors.append({"plate_id": plate, "error": f"{type(error).__name__}: {error}"})
        health = {
            "plates": len(plates), "successful_plates": successful,
            "plate_coverage": successful / len(plates) if plates else 0.0,
            "symbols": len(by_symbol), "errors": errors[:20],
            "duplicate_conflicts": conflicts[:20], "physical_page_limit": MAX_PAGE_SIZE,
        }
        return by_symbol, health

    @staticmethod
    def _tencent_key(symbol: str) -> str:
        code, exchange = symbol.split(".")
        return ("sh" if exchange == "SH" else "sz" if exchange == "SZ" else "bj") + code

    def tencent_quotes(self, symbols: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ordered = sorted(set(symbols))
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for start in range(0, len(ordered), MAX_TENCENT_BATCH_SIZE):
            batch = ordered[start:start + MAX_TENCENT_BATCH_SIZE]
            requested = {self._tencent_key(symbol): symbol for symbol in batch}
            try:
                response = self._session.get(
                    "https://qt.gtimg.cn/q=" + ",".join(requested),
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
                rows.extend(parse_tencent_quote_text(response.content.decode("gb18030", errors="replace"), requested))
            except Exception as error:
                errors.append(f"batch={start // MAX_TENCENT_BATCH_SIZE}:{type(error).__name__}:{error}")
        return rows, {
            "requested": len(ordered), "received": len(rows),
            "coverage": len(rows) / len(ordered) if ordered else 0.0,
            "batch_size": MAX_TENCENT_BATCH_SIZE, "errors": errors[:20],
        }

    def fetch_full_market_evidence(self, trade_date: date) -> dict[str, Any]:
        catalog = self.industry_plate_catalog()
        vendor, vendor_health = self.full_market_vendor_rows(
            trade_date, plate_ids=[row["sector_key"] for row in catalog],
        )
        quotes, quote_health = self.tencent_quotes(vendor)
        members_by_plate: dict[str, list[dict[str, Any]]] = {}
        for row in vendor.values():
            members_by_plate.setdefault(str(row["plate_id"]), []).append(row)
        board_rows: list[dict[str, Any]] = []
        for board in catalog:
            members = members_by_plate.get(board["sector_key"], [])
            leaders = sorted(
                members,
                key=lambda row: (float(row.get("main_net") or 0), float(row.get("pct_chg") or 0)),
                reverse=True,
            )[:10]
            board_rows.append({
                **board, "mapped_members": len(members), "quoted_members": len(members),
                "top_stocks": [{
                    "symbol": row["symbol"], "name": row["name"],
                    "pct_change": row.get("pct_chg"), "net_inflow": row.get("main_net"),
                } for row in leaders],
                "source": "longhuvip:RealRankingInfo+ZhiShuStockList_W8",
            })
        return {
            "trade_date": trade_date, "vendor_rows": vendor, "quote_rows": quotes,
            "board_rows": board_rows,
            "health": {"longhu": vendor_health, "tencent": quote_health},
        }


__all__ = [
    "DEFAULT_CONFIG_PATH", "FLOW_CONVENTION", "LonghuIntradaySource", "LonghuVendorConfig",
    "LonghuVendorSource", "SharedLonghuReadSource", "intraday_source",
    "MAX_PAGE_SIZE", "MAX_TENCENT_BATCH_SIZE", "configured", "normalize_stock_symbol",
    "current_session_minute_rows", "parse_industry_stock_row", "parse_stock_minute_payload", "parse_stock_snapshot_payload",
    "parse_tencent_quote_text", "safe_page_size", "market_today", "direct_access_enabled",
]
