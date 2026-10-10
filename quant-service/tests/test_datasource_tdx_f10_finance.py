import inspect
import struct
import unittest
from unittest import mock

from app.datasources.sources import tdx_f10_finance as f10
from app.datasources.sources import tdx_protocol
from app.datasources.sources.tdx_fin_history import parse_report_file

# The wire bytes below are written out by hand from the documented layouts (pytdx get_finance_info /
# get_company_info_*), not packed with the parsers' own struct formats, so a wrong field order, offset or
# name in a parser fails here.

# 0x0010 reply for 000001.SZ: count 1, market 0, code, then float_shares (万股), province, industry,
# updated_date, ipo_date and 30 floats that run 1.0 .. 30.0 in the order total_shares .. reserved2
# (money in 千元, share capital in 万股).
FINANCE_BODY = bytes.fromhex(
    "0100" "00" "303030303031"      # count 1, market 0 (SZ), "000001"
    "0000c642"                      # float_shares = 99.0
    "0300" "0700"                   # province 3, industry 7
    "cf273501"                      # updated_date = 20260815
    "03cf2f01"                      # ipo_date = 19910403
    "0000803f"  # 1.0 -> total_shares
    "00000040"  # 2.0 -> state_shares
    "00004040"  # 3.0 -> sponsor_legal_shares
    "00008040"  # 4.0 -> legal_shares
    "0000a040"  # 5.0 -> b_shares
    "0000c040"  # 6.0 -> h_shares
    "0000e040"  # 7.0 -> eps
    "00000041"  # 8.0 -> total_assets
    "00001041"  # 9.0 -> current_assets
    "00002041"  # 10.0 -> fixed_assets
    "00003041"  # 11.0 -> intangible_assets
    "00004041"  # 12.0 -> shareholder_count
    "00005041"  # 13.0 -> current_liabilities
    "00006041"  # 14.0 -> long_term_liabilities
    "00007041"  # 15.0 -> capital_reserve
    "00008041"  # 16.0 -> parent_equity
    "00008841"  # 17.0 -> operating_revenue
    "00009041"  # 18.0 -> main_business_profit
    "00009841"  # 19.0 -> accounts_receivable
    "0000a041"  # 20.0 -> operating_profit
    "0000a841"  # 21.0 -> investment_income
    "0000b041"  # 22.0 -> net_cash_flow
    "0000b841"  # 23.0 -> total_cash_inflow
    "0000c041"  # 24.0 -> inventory
    "0000c841"  # 25.0 -> total_profit
    "0000d041"  # 26.0 -> after_tax_profit
    "0000d841"  # 27.0 -> net_profit
    "0000e041"  # 28.0 -> undistributed_profit
    "0000e841"  # 29.0 -> net_assets_per_share
    "0000f041"  # 30.0 -> reserved2
)
# The same reply for 600519.SH: only market and code differ.
FINANCE_BODY_SH = bytes.fromhex("0100" "01" "363030353139") + FINANCE_BODY[9:]

FINANCE_ITEMS = {
    "float_shares": 990000.0, "province": 3, "industry": 7, "updated_date": 20260815, "ipo_date": 19910403,
    "total_shares": 10000.0, "state_shares": 20000.0, "sponsor_legal_shares": 30000.0, "legal_shares": 40000.0,
    "b_shares": 50000.0, "h_shares": 60000.0, "eps": 7.0,
    "total_assets": 8000.0, "current_assets": 9000.0, "fixed_assets": 10000.0, "intangible_assets": 11000.0,
    "shareholder_count": 12.0, "current_liabilities": 13000.0, "long_term_liabilities": 14000.0,
    "capital_reserve": 15000.0, "parent_equity": 16000.0, "operating_revenue": 17000.0,
    "main_business_profit": 18000.0, "accounts_receivable": 19000.0, "operating_profit": 20000.0,
    "investment_income": 21000.0, "net_cash_flow": 22000.0, "total_cash_inflow": 23000.0, "inventory": 24000.0,
    "total_profit": 25000.0, "after_tax_profit": 26000.0, "net_profit": 27000.0, "undistributed_profit": 28000.0,
    "net_assets_per_share": 29.0, "reserved2": 30.0,
}
FINANCE_UNITS = {
    "float_shares": "股", "province": "code", "industry": "code", "updated_date": "YYYYMMDD", "ipo_date": "YYYYMMDD",
    "total_shares": "股", "state_shares": "股", "sponsor_legal_shares": "股", "legal_shares": "股",
    "b_shares": "股", "h_shares": "股", "eps": "元/股",
    "total_assets": "元", "current_assets": "元", "fixed_assets": "元", "intangible_assets": "元",
    "shareholder_count": "户", "current_liabilities": "元", "long_term_liabilities": "元", "capital_reserve": "元",
    "parent_equity": "元", "operating_revenue": "元", "main_business_profit": "元", "accounts_receivable": "元",
    "operating_profit": "元", "investment_income": "元", "net_cash_flow": "元", "total_cash_inflow": "元",
    "inventory": "元", "total_profit": "元", "after_tax_profit": "元", "net_profit": "元",
    "undistributed_profit": "元", "net_assets_per_share": "元/股", "reserved2": None,
}

# 0x02cf reply: two sections of the one text file, each name 64 bytes (GBK) | filename 80 bytes | start | length.
CATEGORIES_BODY = bytes.fromhex(
    "0200"
    "d7eed0c2cce1cabe" + "00" * 56 + "3030303030312e747874" + "00" * 70 + "00000000" "1d000000"   # 最新提示, 0, 29
    + "b9abcbbeb8c5bff6" + "00" * 56 + "3030303030312e747874" + "00" * 70 + "1d000000" "38000000"  # 公司概况, 29, 56
)
# 0x02d0 replies by start offset: 10 prefix bytes, <H byte length, GBK text, then bytes beyond the length.
CONTENT_BODIES = {
    0: bytes.fromhex("0102030405060708090a" "0e00" "d7eed0c2cce1cabea3bab7d6baec" "7461696c"),   # 最新提示：分红
    29: bytes.fromhex("0102030405060708090a" "0e00" "b9abcbbeb8c5bff6a3bad2f8d0d0" "7461696c"),   # 公司概况：银行
}

FINANCE_REQUESTS = {
    "000001.SZ": "0c1f187600010b000b0010000100" "00" "303030303031",
    "600519.SH": "0c1f187600010b000b0010000100" "01" "363030353139",
}
CATEGORIES_REQUESTS = {
    "000001.SZ": "0c0f109b00010e000e00cf02" "0000" "303030303031" "00000000",
    "600519.SH": "0c0f109b00010e000e00cf02" "0100" "363030353139" "00000000",
}
# 12-byte header | market | code | 2 zero bytes | filename (80) | start | length | 4 zero bytes
CONTENT_REQUESTS = {
    ("000001.SZ", 0): "0c07109c000168006800d002" "0000" "303030303031" "0000" "3030303030312e747874" + "00" * 70
                      + "00000000" "1d000000" "00000000",
    ("000001.SZ", 29): "0c07109c000168006800d002" "0000" "303030303031" "0000" "3030303030312e747874" + "00" * 70
                       + "1d000000" "38000000" "00000000",
    ("600519.SH", 0): "0c07109c000168006800d002" "0100" "363030353139" "0000" "3030303030312e747874" + "00" * 70
                      + "00000000" "1d000000" "00000000",
    ("600519.SH", 29): "0c07109c000168006800d002" "0100" "363030353139" "0000" "3030303030312e747874" + "00" * 70
                       + "1d000000" "38000000" "00000000",
}


class F10Host:
    """What a TDX host does with the three F10 commands: answers by opcode and keeps every request."""

    def __init__(self, finance_body=FINANCE_BODY):
        self.finance_body = finance_body
        self.requests = []

    def _exchange(self, request):
        self.requests.append(request)
        opcode = struct.unpack_from("<H", request, 10)[0]
        if opcode == 0x0010:
            return self.finance_body
        if opcode == 0x02CF:
            return CATEGORIES_BODY
        if opcode == 0x02D0:
            return CONTENT_BODIES[struct.unpack_from("<I", request, 102)[0]]
        raise AssertionError(f"unexpected opcode {opcode:#06x}")


class FakeSocket:
    """The TCP connection of a host: every request frame is answered with a 16-byte header and the body."""

    def __init__(self, host):
        self.host, self.pending = host, b""

    def sendall(self, request):
        opcode = struct.unpack_from("<H", request, 10)[0]
        body = b"" if opcode == 0x000D else self.host._exchange(request)     # 0x000d is the login_one setup
        self.pending += bytes.fromhex("00000000" "00000000" "00000000") + len(body).to_bytes(2, "little") * 2 + body

    def recv(self, size):
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk

    def close(self):
        pass


class TdxF10Fixtures(unittest.TestCase):
    def test_builders_match_wire_layouts(self):
        for symbol, market, code in (("000001.SZ", 0, "000001"), ("600519.SH", 1, "600519")):
            with self.subTest(symbol=symbol):
                self.assertEqual(f10.build_finance_info_request(market, code).hex(), FINANCE_REQUESTS[symbol])
                self.assertEqual(f10.build_company_categories_request(market, code).hex(), CATEGORIES_REQUESTS[symbol])
                for start, length in ((0, 29), (29, 56)):
                    self.assertEqual(
                        f10.build_company_content_request(market, code, "000001.txt", start, length).hex(),
                        CONTENT_REQUESTS[(symbol, start)])

    def test_finance_summary_fields_keep_their_names_scales_and_units(self):
        row = f10.parse_finance_info(FINANCE_BODY)
        self.assertEqual(row, {
            "count": 1, "market": 0, "code": "000001", **FINANCE_ITEMS, "field_units": FINANCE_UNITS,
            "units": {"shares": "股", "amounts": "元", "per_share": "元/股", "eps": "元/股",
                      "finance_raw_money": "千元", "finance_raw_shares": "万股"}})
        self.assertEqual(f10.parse_finance_info(FINANCE_BODY_SH)["market"], 1)
        self.assertEqual(f10.parse_finance_info(FINANCE_BODY_SH)["code"], "600519")

    def test_finance_summary_rejects_a_reply_that_is_too_short(self):
        for length in (0, 8, len(FINANCE_BODY) - 1):
            with self.subTest(length=length), self.assertRaises(tdx_protocol.TdxProtocolError):
                f10.parse_finance_info(FINANCE_BODY[:length])

    def test_categories_are_read_in_order_with_their_text_ranges(self):
        self.assertEqual(f10.parse_company_categories(CATEGORIES_BODY), [
            {"name": "最新提示", "filename": "000001.txt", "start": 0, "length": 29},
            {"name": "公司概况", "filename": "000001.txt", "start": 29, "length": 56}])
        self.assertEqual(f10.parse_company_categories(bytes.fromhex("0000")), [])

    def test_categories_reject_a_short_or_truncated_reply(self):
        for body in (b"", b"\x02", CATEGORIES_BODY[:-1], CATEGORIES_BODY[:2 + 152]):
            with self.subTest(length=len(body)), self.assertRaises(tdx_protocol.TdxProtocolError):
                f10.parse_company_categories(body)

    def test_content_is_the_gbk_text_after_the_prefix_and_length(self):
        self.assertEqual(f10.parse_company_content(CONTENT_BODIES[0]), "最新提示：分红")
        self.assertEqual(f10.parse_company_content(CONTENT_BODIES[29]), "公司概况：银行")
        self.assertEqual(f10.parse_company_content(bytes.fromhex("0102030405060708090a" "0000")), "")
        # A byte that is not GBK stays visible as U+FFFD instead of vanishing.
        self.assertEqual(f10.parse_company_content(bytes.fromhex("0102030405060708090a" "0100" "ff")), "\ufffd")

    def test_content_rejects_a_short_or_truncated_reply(self):
        for body in (b"", CONTENT_BODIES[0][:11], CONTENT_BODIES[0][:12 + 13]):
            with self.subTest(length=len(body)), self.assertRaises(tdx_protocol.TdxProtocolError):
                f10.parse_company_content(body)

    def test_report_file_chunk_is_its_declared_length(self):
        self.assertEqual(parse_report_file(bytes.fromhex("03000000" "616263" "6a756e6b")), (3, b"abc"))


class TdxF10Adapters(unittest.IsolatedAsyncioTestCase):
    def test_bound_adapters_are_async(self):
        self.assertTrue(inspect.iscoroutinefunction(f10.fetch_financial_summary))
        self.assertTrue(inspect.iscoroutinefunction(f10.fetch_company_profile))

    def patched_call(self, host):
        async def call(operation, **kwargs):
            self.assertEqual(kwargs, {"handshake_profile": "login_one"})
            return operation(host), "h:7709/login_one"
        return mock.patch.object(f10.tdx_protocol, "call", call)

    async def test_financial_summary_requests_its_own_market_and_names_the_answering_host(self):
        for requested, body in (("000001.SZ", FINANCE_BODY), ("600519.SH", FINANCE_BODY_SH), ("000001.sz", FINANCE_BODY)):
            with self.subTest(symbol=requested):
                host = F10Host(body)
                with self.patched_call(host):
                    evidence = await f10.fetch_financial_summary(symbol=requested)
                canonical = requested.upper()
                self.assertEqual([request.hex() for request in host.requests], [FINANCE_REQUESTS[canonical]])
                # updated_date moves after disclosure (delta-1 D4): it is neither the report period nor available_at.
                self.assertEqual(evidence.rows, [{"symbol": canonical, "report_period": None,
                                                  "statement_items": FINANCE_ITEMS, "field_units": FINANCE_UNITS}])
                self.assertEqual(evidence.warnings, ("tdx_host=h:7709/login_one",))

    async def test_company_profile_requests_each_section_for_its_own_market(self):
        for requested in ("000001.SZ", "600519.SH", "600519.sh"):
            with self.subTest(symbol=requested):
                host = F10Host()
                with self.patched_call(host):
                    evidence = await f10.fetch_company_profile(symbol=requested)
                canonical = requested.upper()
                self.assertEqual([request.hex() for request in host.requests], [
                    CATEGORIES_REQUESTS[canonical], CONTENT_REQUESTS[(canonical, 0)], CONTENT_REQUESTS[(canonical, 29)]])
                self.assertEqual(evidence.rows, [
                    {"symbol": canonical, "category": "最新提示", "filename": "000001.txt", "content": "最新提示：分红"},
                    {"symbol": canonical, "category": "公司概况", "filename": "000001.txt", "content": "公司概况：银行"}])
                self.assertEqual(evidence.warnings, ("tdx_host=h:7709/login_one",))

    async def test_a_bad_symbol_fails_before_any_connection(self):
        opened = mock.AsyncMock()
        with mock.patch.object(f10.tdx_protocol, "call", opened):
            for symbol in ("600519", "60051X.SH", "6005.SH", "600519.XX", "600519.SH.SH"):
                for adapter in (f10.fetch_financial_summary, f10.fetch_company_profile):
                    with self.subTest(symbol=symbol, adapter=adapter.__name__), self.assertRaises(ValueError):
                        await adapter(symbol=symbol)
        opened.assert_not_called()

    async def test_adapters_run_over_the_real_transport_with_a_plain_client(self):
        # call_sync builds a plain TdxClient; this is the path where an adapter that needs a client subclass breaks.
        host = F10Host()
        with mock.patch.object(tdx_protocol, "configured_hosts", return_value=(("h", 7709),)), \
                mock.patch.object(tdx_protocol.socket, "create_connection", lambda *args, **kwargs: FakeSocket(host)):
            summary = await f10.fetch_financial_summary(symbol="000001.SZ")
            profile = await f10.fetch_company_profile(symbol="000001.SZ")
        self.assertEqual(summary.rows[0]["statement_items"], FINANCE_ITEMS)
        self.assertEqual([row["content"] for row in profile.rows], ["最新提示：分红", "公司概况：银行"])
        self.assertEqual(summary.warnings, ("tdx_host=h:7709/login_one",))
        self.assertEqual(len(host.requests), 4)       # the setup frame is answered by FakeSocket itself


if __name__ == "__main__":
    unittest.main()
