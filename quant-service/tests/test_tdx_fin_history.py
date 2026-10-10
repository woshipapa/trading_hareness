import contextlib
import hashlib
import importlib.util
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.datasources.sources.tdx_fin_history import (
    ManifestEntry, TdxFinanceError, build_report_file_request, download_report_file, gpcw, gpcw_field_unit,
    manifest_changes, normalize_report_period, parse_gpcw_dat, parse_gpcw_zip, parse_manifest, parse_report_file,
    parse_tipinfo, ttm_from_cumulative, verify_manifest_entry,
)

VERIFY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-tdx-fin-history.py"

# One GPCW .dat written out byte by byte: a 20-byte header (kind, report_date 20241231, count 1, unknown,
# record_size 968 = 242 floats, reserved), one 11-byte index entry ("600519", flag, record offset 31) and the
# record.  Nearly every float is 0.0; the non-zero ones are col 1 = 2.5, col 6 = 15.5, col 74 = 1,000,000.0,
# col 96 = 250,000.0, col 238 = 8,000,000.0 and col 242 = 123,456.0.
GPCW_DAT = bytes.fromhex(
    "0100" "4fdb3401" "0100" "00000000" "c8030000" "00000000"
    "363030353139" "00" "1f000000"
    "00002040" + "00000000" * 4 + "00007841" + "00000000" * 67 + "00247449" + "00000000" * 21
    + "00247448" + "00000000" * 141 + "0024f44a" + "00000000" * 3 + "0020f147"
)


def gpcw_zip(dat=GPCW_DAT, member="gpcw20241231.dat"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, dat)
    return buffer.getvalue()


def manifest_line(filename, payload):
    return f"{filename},{hashlib.md5(payload).hexdigest()},{len(payload)}\n"


class ReportHost:
    """A TDX host serving one file over 0x06b9: every reply is the <I length of the slice asked for, then the slice."""

    def __init__(self, content, *, send=lambda chunk, size: chunk, limit=20):
        self.content, self.send, self.limit, self.requests = content, send, limit, []

    def _exchange(self, request):
        assert len(self.requests) < self.limit, "the downloader keeps asking"      # fail, do not hang
        offset, size = struct.unpack_from("<II", request, 12)            # a request: header, opcode, offset, size, name
        self.requests.append((request[20:120].rstrip(b"\0").decode("ascii"), offset, size))
        chunk = self.send(self.content[offset:offset + size], size)
        return len(chunk).to_bytes(4, "little") + chunk


class TdxFinancialHistoryTests(unittest.TestCase):
    def test_manifest_and_change_detection(self):
        old = parse_manifest("gpcw20241231.zip," + "a" * 32 + ",10\n")
        new = parse_manifest("gpcw20241231.zip," + "b" * 32 + ",11\ngpcw20251231.zip," + "c" * 32 + ",12\n")
        self.assertEqual(manifest_changes(old, new), {"added": ["gpcw20251231.zip"], "removed": [], "changed": ["gpcw20241231.zip"]})
        self.assertTrue(verify_manifest_entry(ManifestEntry("x", hashlib.md5(b"abc").hexdigest(), 3), b"abc"))
        self.assertFalse(verify_manifest_entry(ManifestEntry("x", hashlib.md5(b"abd").hexdigest(), 3), b"abc"))
        self.assertFalse(verify_manifest_entry(ManifestEntry("x", hashlib.md5(b"abc").hexdigest(), 2), b"abc"))

    def test_gpcw_record_fields_land_under_their_documented_names_and_units(self):
        rows = parse_gpcw_dat(GPCW_DAT, filename="gpcw20241231.zip")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["code"], row["report_date"], row["report_period"], row["field_count"]),
                         ("600519", 20241231, "2024-12-31", 242))
        self.assertEqual(len(row["raw_values"]), 242)
        self.assertEqual({name: value for name, value in row["fields"].items() if value}, {
            "基本每股收益": 2.5, "净资产收益率": 15.5, "其中：营业收入": 1_000_000.0,
            "归属于母公司所有者的净利润": 250_000.0, "总股本": 8_000_000.0, "股东人数(户)": 123_456.0})
        self.assertEqual({name: row["field_units"][name] for name in (
            "基本每股收益", "净资产收益率", "货币资金", "其中：营业收入", "总股本", "股东人数(户)", "col9", "col100")}, {
            "基本每股收益": "yuan/share", "净资产收益率": "ratio", "货币资金": "yuan", "其中：营业收入": "yuan",
            "总股本": "shares", "股东人数(户)": "count", "col9": None, "col100": None})
        self.assertEqual(len(row["field_units"]), 242)

    def test_the_period_falls_back_to_the_header_date_without_a_file_name(self):
        self.assertEqual(parse_gpcw_dat(GPCW_DAT)[0]["report_period"], "2024-12-31")

    def test_a_code_shorter_than_its_six_byte_field_ends_at_the_first_nul(self):
        dat = bytes.fromhex(
            "0100" "4fdb3401" "0100" "00000000" "10000000" "00000000"      # record size 16: four floats
            "303031000000" "00" "1f000000"                                  # "001" and three NULs
            "0000803f" "00000040" "00004040" "00008040")
        row = parse_gpcw_dat(dat)[0]
        self.assertEqual((row["code"], row["raw_values"]), ("001", (1.0, 2.0, 3.0, 4.0)))
        self.assertEqual(row["fields"], {"基本每股收益": 1.0, "扣除非经常性损益每股收益": 2.0, "每股未分配利润": 3.0, "每股净资产": 4.0})

    def test_tipinfo_fields_keep_their_positions_and_the_candidate_stays_unpromoted(self):
        row = parse_tipinfo(b"0|000001|20260630|1.24|20260815|20240221|x\n")[0]
        self.assertEqual(row, {
            "market": "0", "code": "000001", "report_period": "20260630", "eps": 1.24,
            "announcement_date_candidate": "20260815",
            "raw_fields": ["0", "000001", "20260630", "1.24", "20260815", "20240221", "x"]})
        self.assertNotIn("available_at", row)

    def test_report_period_comes_from_the_file_name(self):
        self.assertEqual(normalize_report_period("gpcw20101231.zip"), "2010-12-31")
        self.assertEqual(normalize_report_period("tdxfin/gpcw20250630.zip"), "2025-06-30")
        self.assertEqual(normalize_report_period(20241231), "2024-12-31")

    def test_ttm_at_2025_06_30_is_fy2024_plus_h1_2025_minus_h1_2024(self):
        # Year-to-date revenue by report period: Q1, H1 and Q3 accumulate and the series restarts each year.
        # FY2025 differs from FY2024 on purpose: taking it instead would give 1,450, not 1,150.
        ytd = {"2024-06-30": 450.0, "2024-12-31": 1000.0, "2025-06-30": 600.0, "2025-12-31": 1300.0}
        self.assertEqual(ttm_from_cumulative(ytd["2024-12-31"], ytd["2025-06-30"], ytd["2024-06-30"]), 1150.0)


class TdxFinanceErrorTests(unittest.TestCase):
    def test_manifest_rejects_every_malformed_form(self):
        md5 = "a" * 32
        for label, text in {
            "too few columns": f"gpcw20241231.zip,{md5}",
            "too many columns": f"gpcw20241231.zip,{md5},10,x",
            "file name": f"gpcw2024.zip,{md5},10",
            "short md5": f"gpcw20241231.zip,{'a' * 31},10",
            "non-hex md5": f"gpcw20241231.zip,{'g' * 32},10",
            "size not a number": f"gpcw20241231.zip,{md5},ten",
            "negative size": f"gpcw20241231.zip,{md5},-1",
            "size over the cap": f"gpcw20241231.zip,{md5},{64 * 1024 * 1024 + 1}",
            "empty": "",
            "blank lines only": "\n  \n",
            "non-ASCII byte": f"gpcw20241231.zip,{md5},10\u00e9",
        }.items():
            with self.subTest(label), self.assertRaises(TdxFinanceError):
                parse_manifest(text)
        with self.assertRaises(TdxFinanceError):
            parse_manifest(b"gpcw20241231.zip," + b"a" * 32 + b",10\xff")

    def test_manifest_accepts_crlf_blank_lines_and_either_md5_case(self):
        text = f"gpcw20241231.zip,{'A' * 32},10\r\n\r\ngpcw20250630.zip, {'b' * 32} ,{64 * 1024 * 1024}\r\n"
        self.assertEqual(parse_manifest(text), [ManifestEntry("gpcw20241231.zip", "a" * 32, 10),
                                                ManifestEntry("gpcw20250630.zip", "b" * 32, 64 * 1024 * 1024)])
        self.assertEqual(parse_manifest(text.encode("ascii")), parse_manifest(text))

    def test_gpcw_dat_rejects_a_short_header_and_a_record_size_that_is_not_floats(self):
        header = "0100" "4fdb3401" "0100" "00000000" "{}" "00000000"
        for label, data in {
            "short header": GPCW_DAT[:19],
            "record size 0": bytes.fromhex(header.format("00000000")),
            "record size 6": bytes.fromhex(header.format("06000000")),
        }.items():
            with self.subTest(label), self.assertRaises(TdxFinanceError):
                parse_gpcw_dat(data)

    def test_report_period_rejects_what_is_not_a_date(self):
        for value in ("gpcw2024.zip", "", "gpcw20241331.zip", "gpcw20240230.zip", 2024):
            with self.subTest(value=value), self.assertRaises(TdxFinanceError):
                normalize_report_period(value)

    def test_vendor_columns_have_no_unit_and_keep_their_raw_value(self):
        self.assertEqual({col: gpcw_field_unit(col) for col in (1, 6, 8, 74, 96, 238, 242)}, {
            1: "yuan/share", 6: "ratio", 8: "yuan", 74: "yuan", 96: "yuan", 238: "shares", 242: "count"})
        for col in (9, 243, 314, 584):
            self.assertIsNone(gpcw_field_unit(col), col)
        # A record of 314 floats: all zero but col 314, a vendor column.
        record_314 = bytes.fromhex(
            "0100" "4fdb3401" "0100" "00000000" "e8040000" "00000000" "363030353139" "00" "1f000000"
            + "00000000" * 313 + "0000e040")
        row = parse_gpcw_dat(record_314)[0]
        self.assertEqual((row["fields"]["col314"], row["field_units"]["col314"]), (7.0, None))
        self.assertIsNone(row["field_units"]["col9"])
        self.assertEqual(row["field_units"]["总股本"], "shares")


class GpcwZipTests(unittest.TestCase):
    def test_a_zip_with_one_dat_member_gives_its_rows_for_the_named_period(self):
        rows = parse_gpcw_zip(gpcw_zip(), filename="gpcw20250630.zip")
        self.assertEqual([(row["code"], row["report_period"], row["field_count"]) for row in rows],
                         [("600519", "2025-06-30", 242)])

    def test_bytes_that_are_not_a_zip_raise_the_typed_error(self):
        with self.assertRaisesRegex(TdxFinanceError, "GPCW ZIP format error"):
            parse_gpcw_zip(b"PK-this-is-not-a-zip")

    def test_a_member_that_fails_its_crc_raises_the_typed_error(self):
        data = bytearray(gpcw_zip())
        data[data.index(b"PK\x03\x04") + 14] ^= 0xFF        # the stored CRC-32 in the local header
        central = data.index(b"PK\x01\x02") + 16
        data[central] ^= 0xFF                                # and in the central directory
        with self.assertRaisesRegex(TdxFinanceError, "GPCW ZIP format error"):
            parse_gpcw_zip(bytes(data))

    def test_a_corrupt_deflate_stream_raises_the_typed_error(self):
        data = bytearray(gpcw_zip())
        data[30 + len("gpcw20241231.dat")] |= 0x06           # first deflate block type 11 is invalid
        with self.assertRaisesRegex(TdxFinanceError, "GPCW ZIP format error"):
            parse_gpcw_zip(bytes(data))

    def test_a_zip_needs_exactly_one_dat_member(self):
        for label, members in {
            "none": {},
            "only a text member": {"gpcw20241231.txt": b"x"},
            "two data members": {"a.dat": GPCW_DAT, "b.dat": GPCW_DAT},
        }.items():
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.subTest(label), self.assertRaisesRegex(TdxFinanceError, "exactly one .dat member"):
                parse_gpcw_zip(buffer.getvalue(), filename="gpcw20241231.zip")

    def test_size_caps_apply_to_the_zip_and_to_the_member(self):
        with self.assertRaisesRegex(TdxFinanceError, "member exceeds size cap"):
            parse_gpcw_zip(gpcw_zip(), max_uncompressed=len(GPCW_DAT) - 1)
        self.assertEqual(len(parse_gpcw_zip(gpcw_zip(), max_uncompressed=len(GPCW_DAT))), 1)
        payload = gpcw_zip()
        with mock.patch("app.datasources.sources.tdx_fin_history.MAX_DOWNLOAD_BYTES", len(payload) - 1), \
                self.assertRaisesRegex(TdxFinanceError, "exceeds download cap"):
            parse_gpcw_zip(payload)
        with mock.patch("app.datasources.sources.tdx_fin_history.MAX_DOWNLOAD_BYTES", len(payload)):
            self.assertEqual(len(parse_gpcw_zip(payload)), 1)


class ReportFileDownloadTests(unittest.TestCase):
    FILE = bytes(range(256)) * 274           # 70,144 bytes

    def test_the_request_is_the_documented_0x06b9_frame(self):
        self.assertEqual(
            build_report_file_request("tdxfin/gpcw.txt", 30000, 8574).hex(),
            "0c1234000000" "6e00" "6e00" "b906" "30750000" "7e210000" "74647866696e2f677063772e747874" + "00" * 85)

    def test_a_reply_is_its_declared_length_and_a_zero_length_is_the_end(self):
        self.assertEqual(parse_report_file(bytes.fromhex("03000000" "616263" "6a756e6b")), b"abc")
        self.assertEqual(parse_report_file(bytes.fromhex("00000000")), b"")
        for label, body in {"no length": bytes.fromhex("030000"), "chunk cut short": bytes.fromhex("05000000" "616263")}.items():
            with self.subTest(label), self.assertRaises(TdxFinanceError):
                parse_report_file(body)

    def test_the_whole_file_is_fetched_in_requests_for_30000_bytes(self):
        host = ReportHost(self.FILE)
        self.assertEqual(download_report_file(host, "tdxfin/x.zip", len(self.FILE)), self.FILE)
        self.assertEqual([(offset, size) for _, offset, size in host.requests],
                         [(0, 30000), (30000, 30000), (60000, 30000)])
        self.assertEqual({name for name, _, _ in host.requests}, {"tdxfin/x.zip"})

    def test_a_server_that_returns_shorter_chunks_is_followed_from_where_it_stopped(self):
        host = ReportHost(self.FILE, send=lambda chunk, size: chunk[:20000])
        self.assertEqual(download_report_file(host, "tdxfin/x.zip", len(self.FILE)), self.FILE)
        self.assertEqual([offset for _, offset, _ in host.requests], [0, 20000, 40000, 60000])

    def test_a_file_over_the_cap_is_refused_before_anything_is_downloaded(self):
        host = ReportHost(bytes(102_400))
        with self.assertRaisesRegex(TdxFinanceError, "exceeds the 60000-byte cap"):
            download_report_file(host, "tdxfin/x.zip", 102_400, max_bytes=60_000)
        self.assertEqual(host.requests, [])
        # Exactly at the cap is allowed, and the whole file comes back, not 60,000 bytes of a longer one.
        self.assertEqual(len(download_report_file(ReportHost(bytes(60_000)), "tdxfin/x.zip", 60_000, max_bytes=60_000)), 60_000)

    def test_a_file_that_ends_before_its_advertised_size_raises(self):
        host = ReportHost(self.FILE[:50_000])
        with self.assertRaisesRegex(TdxFinanceError, "ended at byte 50000 of 70144"):
            download_report_file(host, "tdxfin/x.zip", len(self.FILE))
        self.assertEqual(len(host.requests), 3)

    def test_a_file_longer_than_its_advertised_size_raises(self):
        host = ReportHost(self.FILE)
        with self.assertRaisesRegex(TdxFinanceError, "runs past its advertised 65000 bytes"):
            download_report_file(host, "tdxfin/x.zip", 65_000)

    def test_a_reply_shorter_than_its_declared_length_raises(self):
        host = ReportHost(self.FILE)
        host._exchange = lambda request: bytes.fromhex("10270000") + b"x" * 100       # declares 10,000, carries 100
        with self.assertRaisesRegex(TdxFinanceError, "shorter than its declared length 10000"):
            download_report_file(host, "tdxfin/x.zip", len(self.FILE))

    def test_an_empty_file_is_empty_without_a_request(self):
        host = ReportHost(b"")
        self.assertEqual(download_report_file(host, "tdxfin/x.zip", 0), b"")
        self.assertEqual(host.requests, [])


class GpcwPeriodTests(unittest.TestCase):
    def entry_and_host(self, filename="gpcw20241231.zip"):
        payload = gpcw_zip()
        return ManifestEntry(filename, hashlib.md5(payload).hexdigest(), len(payload)), ReportHost(payload)

    def test_the_period_zip_is_downloaded_checked_and_parsed(self):
        entry, host = self.entry_and_host()
        rows = gpcw(host, "gpcw20241231.zip", entry)
        self.assertEqual([(row["code"], row["report_period"]) for row in rows], [("600519", "2024-12-31")])
        self.assertEqual({name for name, _, _ in host.requests}, {"tdxfin/gpcw20241231.zip"})

    def test_a_download_that_does_not_match_its_manifest_md5_is_rejected(self):
        entry, host = self.entry_and_host()
        tampered = ManifestEntry(entry.filename, "0" * 32, entry.size)
        with self.assertRaisesRegex(TdxFinanceError, "manifest mismatch for gpcw20241231.zip"):
            gpcw(host, "gpcw20241231.zip", tampered)

    def test_the_manifest_entry_is_required_and_must_be_the_files_own(self):
        entry, host = self.entry_and_host("gpcw20250630.zip")
        with self.assertRaisesRegex(TdxFinanceError, "not the entry of gpcw20241231.zip"):
            gpcw(host, "gpcw20241231.zip", entry)
        self.assertEqual(host.requests, [])
        with self.assertRaises(TypeError):
            gpcw(host, "gpcw20241231.zip")


class VerifyCacheScriptTests(unittest.TestCase):
    """scripts/verify-tdx-fin-history.py reads a local cache: a bad period is reported and skipped."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("verify_tdx_fin_history", VERIFY_SCRIPT)
        cls.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.script)

    def run_script(self, manifest_text, cache_files):
        with tempfile.TemporaryDirectory() as root:
            manifest = Path(root) / "gpcw.txt"
            manifest.write_text(manifest_text, encoding="ascii")
            cache = Path(root) / "cache"
            cache.mkdir()
            for name, payload in cache_files.items():
                (cache / name).write_bytes(payload)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch("sys.argv", ["verify", str(manifest), str(cache)]), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.script.main()
        return code, out.getvalue(), err.getvalue()

    def test_a_period_that_cannot_be_read_is_skipped_and_the_next_one_is_verified(self):
        good, bad_zip, other = gpcw_zip(), b"PK-this-is-not-a-zip", gpcw_zip(member="gpcw20250630.dat")
        manifest = (manifest_line("gpcw20240630.zip", bad_zip) + manifest_line("gpcw20241231.zip", good)
                    + "gpcw20250331.zip," + "0" * 32 + f",{len(other)}\n" + manifest_line("gpcw20250630.zip", other)
                    + manifest_line("gpcw20250930.zip", b"never cached"))
        code, out, err = self.run_script(manifest, {
            "gpcw20240630.zip": bad_zip, "gpcw20241231.zip": good, "gpcw20250331.zip": other,
            "gpcw20250630.zip": other, "gpcw20250930.zip": b""})
        self.assertEqual(code, 0)
        self.assertIn("gpcw20241231.zip: rows=1 fields=242\n", out)
        self.assertIn("verified 2 periods", out)
        self.assertIn("gpcw20240630.zip: unusable: GPCW ZIP format error", err)
        self.assertIn("gpcw20250331.zip: manifest MD5/size mismatch", err)

    def test_nothing_usable_fails_and_a_bad_manifest_is_reported(self):
        bad_zip = b"PK-this-is-not-a-zip"
        code, out, err = self.run_script(manifest_line("gpcw20240630.zip", bad_zip), {"gpcw20240630.zip": bad_zip})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no usable GPCW rows", err)
        code, _, err = self.run_script("not,a,manifest\n", {})
        self.assertEqual(code, 2)
        self.assertIn("manifest unusable: invalid GPCW manifest row", err)


if __name__ == "__main__":
    unittest.main()
