from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import unittest

from app.routers.longhu_reads import build_longhu_reads_router


def client(*, key: str = "peer-key", enabled: bool = True) -> TestClient:
    async def quotes(symbols, max_symbols):
        return ([{"ts_code": symbol, "price": 10.0} for symbol in symbols],
                {"status": "completed", "max_symbols": max_symbols})

    async def minutes(symbol):
        return [{"symbol": symbol, "time": "0930", "close": 10.0}]

    app = FastAPI()
    app.include_router(build_longhu_reads_router(
        configured=lambda: enabled, shared_read_key=lambda: key,
        quotes=quotes, minutes=minutes,
    ))
    return TestClient(app)


class LonghuReadsRouterTests(unittest.TestCase):
    def test_gateway_requires_its_separate_read_key(self):
        response = client().get("/licensed/longhu/quotes?symbols=600664.SH")
        self.assertEqual(response.status_code, 401)


    def test_gateway_returns_audited_cap_and_rows(self):
        response = client().get(
            "/licensed/longhu/quotes?symbols=600664.SH,600487.SH",
            headers={"X-Quant-Read-Key": "peer-key"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["physical_request_limit"], 300)
        self.assertEqual(payload["source_status"]["max_symbols"], 300)


    def test_gateway_rejects_more_than_300_symbols_before_provider_call(self):
        symbols = ",".join(f"{index:06d}.SZ" for index in range(301))
        response = client().get(
            f"/licensed/longhu/quotes?symbols={symbols}",
            headers={"X-Quant-Read-Key": "peer-key"},
        )
        self.assertEqual(response.status_code, 422)
