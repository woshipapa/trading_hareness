"""Bounded transport and symbol helpers shared by the token-free public adapters.

Every public adapter in this package calls one upstream site with a fixed
allow-listed URL.  This module owns only the transport concerns they share:
one bounded retry for transient failures, JSONP unwrapping, network-health
bookkeeping and A-share code normalization.  It contains no scheduling and no
persistence, so an adapter stays a pure ``fetch -> normalize`` pair.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Mapping

import httpx

from ..http_clients import public_http_client
from ..http_retry import retry_delay_seconds
from ..network_health import network_state


BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, text/html, */*; q=0.01",
}

_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
_A_SHARE_CODE = re.compile(r"\d{6}")


class PublicSourceError(RuntimeError):
    """A concise, credential-free public-source failure."""


def _site(url: str) -> str:
    return f"public:{url.split('/', 3)[2] if '://' in url else 'unknown'}"


def unwrap_jsonp(text: str) -> Any:
    """Parse JSON, tolerating a ``callback(...)`` or ``var x = ...;`` wrapper."""
    body = text.strip()
    try:
        return json.loads(body)
    except ValueError:
        pass
    start, end = body.find("("), body.rfind(")")
    if 0 <= start < end:
        return json.loads(body[start + 1:end])
    if "=" in body:
        return json.loads(body.split("=", 1)[1].strip().rstrip(";"))
    raise PublicSourceError("public response is not JSON")


async def request(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    json_body: Any | None = None,
    data: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 15.0,
    attempts: int = 2,
) -> httpx.Response:
    """Issue one allow-listed request with a single bounded transient retry."""
    source = _site(url)
    last_error: Exception | None = None
    async with public_http_client() as client:
        for attempt in range(max(1, attempts)):
            response: httpx.Response | None = None
            try:
                response = await client.request(
                    method, url, params=dict(params or {}) or None, json=json_body,
                    data=dict(data or {}) or None, headers={**BROWSER_HEADERS, **dict(headers or {})},
                    timeout=timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                network_state.record_failure(source, str(error), transient=True)
                last_error = error
            else:
                if response.status_code in _TRANSIENT_STATUSES:
                    network_state.record_failure(source, f"HTTP {response.status_code}", transient=True)
                    last_error = PublicSourceError(f"HTTP {response.status_code}")
                elif response.status_code >= 400:
                    raise PublicSourceError(f"{source} answered HTTP {response.status_code}")
                else:
                    network_state.record_success(source)
                    return response
            if attempt < attempts - 1:
                await asyncio.sleep(retry_delay_seconds(response.headers if response is not None else None, 0.5))
    detail = type(last_error).__name__ if last_error is not None else "unknown"
    raise PublicSourceError(f"{source} request failed after bounded retry ({detail})") from last_error


async def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = await request(method, url, **kwargs)
    try:
        return response.json()
    except ValueError:
        return unwrap_jsonp(response.text)


async def request_text(method: str, url: str, **kwargs: Any) -> str:
    return (await request(method, url, **kwargs)).text


def ashare_symbol(code: Any, exchange_hint: Any = None) -> str | None:
    """Return ``NNNNNN.SH|SZ|BJ`` for an A-share *equity*, else ``None``.

    Funds, bonds, indices and boards are deliberately rejected: a news item
    tagged with an ETF or a board code must not create a stock instrument.
    ``exchange_hint`` accepts ``SH``/``SZ``/``BJ`` or the ``sh600000`` and
    ``SH600000`` prefixed forms various sites use.
    """
    text = str(code or "").strip().upper()
    hint = str(exchange_hint or "").strip().upper()
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"}:
        hint, text = text[:2], text[2:]
    if "." in text:
        text, _, suffix = text.partition(".")
        if suffix not in {"SH", "SZ", "BJ"}:
            # e.g. ``885431.TI`` is a THS index, ``161121.OF`` a fund.
            return None
        hint = suffix
    if not _A_SHARE_CODE.fullmatch(text):
        return None
    if text.startswith(("600", "601", "603", "605", "688", "689")):
        exchange = "SH"
    elif text.startswith(("000", "001", "002", "003", "300", "301", "302")):
        exchange = "SZ"
    elif text.startswith(("920", "43", "83", "87")):
        exchange = "BJ"
    else:
        return None
    if hint in {"SH", "SZ", "BJ"} and hint != exchange:
        return None
    return f"{text}.{exchange}"


def number(value: Any) -> float | None:
    """Parse a vendor number; blanks, dashes and NaN become ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "None", "null"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed == parsed else None


__all__ = [
    "BROWSER_HEADERS", "PublicSourceError", "ashare_symbol", "number", "request",
    "request_json", "request_text", "unwrap_jsonp",
]
