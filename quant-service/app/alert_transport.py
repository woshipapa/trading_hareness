"""Bounded opt-in delivery adapter for human-review notifications.

Three transports, tried in order, because a recommendation nobody receives is
the same as no recommendation.  A transport that is configured but failing
falls through to the next rather than ending the attempt: on 2026-09-17 the
peer had only the adapter route named and no URL for it, so every alert it
would have produced resolved to "disabled" and was never delivered anywhere.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .http_clients import alert_http_client
from .feishu_direct_alert import direct_feishu_alert_configured, post_direct_feishu_alert_text
from .tushare_providers import safe_error_detail


async def post_feishu_bot_webhook_text(text: str) -> dict[str, Any]:
    """Post to a Feishu custom-bot hook, which needs no app membership.

    The hook answers 200 with a body even when it rejects the message - a
    missing keyword or a bad signature appears only in ``code`` - so the body
    is what decides, not the status line.
    """
    webhook_url = (os.getenv("QUANT_ALERT_FEISHU_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return {"status": "disabled", "reason": "Feishu bot webhook is not configured"}
    try:
        async with alert_http_client() as client:
            response = await client.post(
                webhook_url, json={"msg_type": "text", "content": {"text": str(text)}},
            )
            response.raise_for_status()
            payload = response.json()
            if int(payload.get("code") or 0) != 0:
                raise ValueError(f"Feishu bot webhook rejected the message: {str(payload.get('msg') or '')[:200]}")
            return {"status": "sent", "transport": "feishu_bot_webhook", "response": payload}
    except (httpx.HTTPError, ValueError, TypeError) as error:
        return {"status": "failed", "transport": "feishu_bot_webhook",
                "error": safe_error_detail(str(error), 500)}


async def post_adapter_webhook_text(text: str) -> dict[str, Any]:
    """Post to the local n8n adapter route."""
    webhook_url = (os.getenv("QUANT_ALERT_WEBHOOK_URL") or "").strip()
    webhook_token = (os.getenv("QUANT_ALERT_WEBHOOK_TOKEN") or "").strip()
    if not webhook_url or not webhook_token:
        return {"status": "disabled", "reason": "alert webhook or token is not configured"}
    try:
        async with alert_http_client() as client:
            response = await client.post(
                webhook_url,
                headers={"X-Quant-Alert-Token": webhook_token},
                json={"text": text},
            )
            response.raise_for_status()
            return {"status": "sent", "transport": "adapter_webhook", "response": response.json()}
    except (httpx.HTTPError, ValueError) as error:
        return {"status": "failed", "transport": "adapter_webhook", "error": safe_error_detail(str(error), 500)}


async def post_feishu_alert_text(text: str) -> dict[str, Any]:
    """Deliver through the first transport that accepts the message."""
    attempts: list[dict[str, Any]] = []
    for transport in (
        post_feishu_bot_webhook_text,
        (lambda value: post_direct_feishu_alert_text(value)) if direct_feishu_alert_configured() else None,
        post_adapter_webhook_text,
    ):
        if transport is None:
            continue
        outcome = await transport(text)
        if outcome.get("status") == "sent":
            return {**outcome, "attempts": attempts} if attempts else outcome
        attempts.append(outcome)
    # Every transport declining is not the same as every transport failing: the
    # caller's outbox retries a failure and stops retrying a disabled channel.
    failed = [item for item in attempts if item.get("status") == "failed"]
    if failed:
        return {"status": "failed", "error": failed[0].get("error"), "attempts": attempts}
    return {"status": "disabled", "reason": "no alert transport is configured", "attempts": attempts}
