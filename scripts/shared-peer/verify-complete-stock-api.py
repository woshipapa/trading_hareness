#!/usr/bin/env python3
"""Verify the complete peer stock-data gateway through its public contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def contains_owner_credential_field(value: Any) -> bool:
    """Detect credential-bearing keys at any depth without printing values."""
    if isinstance(value, dict):
        if any(str(key).lower() in {"token", "userid", "deviceid"} for key in value):
            return True
        return any(contains_owner_credential_field(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_owner_credential_field(item) for item in value)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:15682")
    parser.add_argument(
        "--env-file",
        default="/home/stockpeer/trading_hareness/deploy/shared-peer/.env",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="per-request timeout in seconds (the generic gateway may fan out pages)",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    env = read_env(Path(args.env_file))
    key = env.get("QUANT_SHARED_READ_API_KEY", "").strip()
    if not key:
        print(json.dumps({
            "passed": False,
            "error": "QUANT_SHARED_READ_API_KEY is missing or empty",
            "env_file": str(args.env_file),
        }, ensure_ascii=False, sort_keys=True))
        return 2
    opener = build_opener(ProxyHandler({}))

    def request(path: str, body: dict[str, Any] | None = None, *, auth: bool = True):
        headers = {"Accept": "application/json"}
        if auth:
            headers["X-Quant-Read-Key"] = key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        response = opener.open(
            Request(f"{base_url}{path}", data=data, headers=headers),
            timeout=max(1.0, args.timeout),
        )
        return response.status, json.load(response)

    try:
        health_status, health = request("/health", auth=False)
        try:
            request("/licensed/stock-api/catalog", auth=False)
            unauthorized_status = 200
        except HTTPError as error:
            unauthorized_status = error.code

        catalog_status, catalog = request("/licensed/stock-api/catalog")
        examples = catalog["documented_examples"]

        def pick(*, action: str | None = None, target: str | None = None):
            return next(
                row
                for row in examples
                if (action is None or row.get("action") == action)
                and (target is None or row["target"] == target)
            )

        checks = [
            ("quote", pick(action="GetStockPanKou")),
            ("breadth", pick(action="RiseFallAnalysis", target="longhu_market_wide")),
            ("public", pick(target="xuangubao")),
        ]
        results: list[dict[str, Any]] = []
        raw_results: list[dict[str, Any]] = []
        for name, example in checks:
            status, payload = request(
                "/licensed/stock-api/call",
                {
                    "target": example["target"],
                    "path": example["path"],
                    "params": example["params"],
                },
            )
            pages = payload.get("pages") if isinstance(payload, dict) else None
            page_payloads = [
                page.get("payload") for page in pages
                if isinstance(page, dict) and isinstance(page.get("payload"), dict)
            ] if isinstance(pages, list) else []
            raw_results.append(payload)
            results.append({
                "name": name,
                "status": status,
                "calls": payload.get("calls"),
                "pages": len(pages) if isinstance(pages, list) else 0,
                "nonempty_object_payload": any(bool(item) for item in page_payloads),
            })

        history = pick(action="GetKLineDay_W14")
        history_params = dict(history["params"])
        history_params["st"] = 301
        history_status, history_payload = request(
            "/licensed/stock-api/call",
            {
                "target": history["target"],
                "path": history["path"],
                "params": history_params,
            },
        )
        raw_results.append(history_payload)
        history_pages = history_payload.get("pages") if isinstance(history_payload, dict) else []
        history_sizes = [page.get("size") for page in history_pages if isinstance(page, dict)]
        results.append({
            "name": "paged_301",
            "status": history_status,
            "calls": history_payload.get("calls"),
            "sizes": history_sizes,
        })

        credential_fields_present = contains_owner_credential_field(raw_results)
        summary = {
            "health_status": health_status,
            "health_ok": health_status == 200 and isinstance(health, dict) and health.get("status") == "ok",
            "unauthorized_status": unauthorized_status,
            "catalog_status": catalog_status,
            "targets": len(catalog["targets"]),
            "operations": len(catalog["documented_operations"]),
            "examples": len(examples),
            "calls": results,
            "credential_fields_in_response": credential_fields_present,
        }
        passed = (
            summary["health_ok"]
            and unauthorized_status == 401
            and catalog_status == 200
            and summary["targets"] == 8
            and summary["examples"] == 89
            and all(row["status"] == 200 and row.get("nonempty_object_payload", False) for row in results[:3])
            and history_status == 200
            and history_payload.get("calls") == 2
            and history_sizes == [300, 1]
            and not credential_fields_present
        )
        summary["passed"] = passed
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if passed else 1
    except (HTTPError, URLError, KeyError, TypeError, ValueError, TimeoutError) as error:
        print(json.dumps({
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "base_url": base_url,
        }, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
