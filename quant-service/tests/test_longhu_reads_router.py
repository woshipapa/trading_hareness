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

    async def minutes_batch(symbols, deadline_seconds):
        batch_calls.append((list(symbols), deadline_seconds))
        rows = [{"time": f"{index:04d}", "close": 10.0, "volume_lot": 100, "trade_date": "20260922"}
                for index in range(rows_per_symbol)]
        return {symbol: (rows if not symbol.startswith("6") else "Longhu minute rows are stale")
                for symbol in symbols[:-1]} if len(symbols) > 1 else {symbols[0]: rows}

    app = FastAPI()
    app.include_router(build_longhu_reads_router(
        configured=lambda: enabled, shared_read_key=lambda: key,
        quotes=quotes, minutes=minutes, minutes_batch=minutes_batch,
    ))
    return TestClient(app)


batch_calls: list = []
rows_per_symbol = 2


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


class LonghuMinuteBatchRouteTests(unittest.TestCase):
    def setUp(self):
        global rows_per_symbol
        batch_calls.clear()
        rows_per_symbol = 2

    def test_batch_route_requires_the_read_key(self):
        self.assertEqual(client().get("/licensed/longhu/minutes?symbols=000001.SZ").status_code, 401)

    def test_batch_route_splits_rows_errors_and_unanswered_symbols(self):
        response = client().get(
            "/licensed/longhu/minutes?symbols=000001.sz,600000.SH,000002.SZ&deadline_seconds=4",
            headers={"X-Quant-Read-Key": "peer-key"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(batch_calls, [(["000001.SZ", "600000.SH", "000002.SZ"], 4.0)])
        self.assertEqual(list(payload["rows"]), ["000001.SZ"])
        self.assertEqual(payload["errors"], {"600000.SH": "Longhu minute rows are stale",
                                             "000002.SZ": "minute_batch_missing_symbol"})
        self.assertEqual((payload["requested"], payload["completed"]), (3, 1))

    def test_batch_route_caps_symbols_and_deadline(self):
        symbols = ",".join(f"{index:06d}.SZ" for index in range(301))
        headers = {"X-Quant-Read-Key": "peer-key"}
        self.assertEqual(client().get(f"/licensed/longhu/minutes?symbols={symbols}", headers=headers).status_code, 422)
        self.assertEqual(client().get("/licensed/longhu/minutes?symbols=000001.SZ&deadline_seconds=60",
                                      headers=headers).status_code, 422)
        self.assertEqual(batch_calls, [])

    def test_large_batches_are_gzipped_for_the_tunnel(self):
        global rows_per_symbol
        rows_per_symbol = 241
        symbols = ",".join(f"{index:06d}.SZ" for index in range(1, 40))
        response = client().get(f"/licensed/longhu/minutes?symbols={symbols}",
                                headers={"X-Quant-Read-Key": "peer-key", "Accept-Encoding": "gzip"})
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertEqual(len(response.json()["rows"]["000001.SZ"]), 241)

