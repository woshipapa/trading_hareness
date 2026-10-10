import io
import struct
import unittest
import zipfile

from app.datasources.sources import tdx_files


class TdxFileParserTests(unittest.TestCase):
    def test_zip_members_stream_against_the_budget_and_corruption_is_typed(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("a.cfg", b"x" * 4096)
        self.assertEqual(tdx_files.parse_zhb_zip(buffer.getvalue())["a.cfg"], b"x" * 4096)
        with self.assertRaises(tdx_files.TdxFileError):
            tdx_files.parse_zhb_zip(buffer.getvalue(), max_uncompressed=1024)
        with self.assertRaises(tdx_files.TdxFileError):
            tdx_files.parse_zhb_zip(b"PK\x03\x04 not a zip archive")

    def test_request_layouts(self):
        meta = tdx_files.build_file_meta_request("block_gn.dat")
        self.assertEqual(meta[:12], bytes.fromhex("0c39186900012a002a00c502"))
        self.assertEqual(meta[12:24].rstrip(b"\0"), b"block_gn.dat")
        chunk = tdx_files.build_file_chunk_request("block_gn.dat", 17, 99)
        self.assertEqual(struct.unpack_from("<II", chunk, 12), (17, 99))
        report = tdx_files.build_report_file_request("zhb.zip", 30000)
        self.assertEqual(struct.unpack_from("<H", report, 10)[0], 0x06B9)
        self.assertEqual(struct.unpack_from("<II", report, 12), (30000, 0x7530))

    def test_block_file(self):
        data = bytearray(384) + struct.pack("<H", 1)
        data += "概念".encode("gbk").ljust(9, b"\x00") + struct.pack("<HH", 2, 5) + b"0000001" + b"1000002"
        data += b"\x00" * (400 * 7 - 14)
        self.assertEqual(tdx_files.parse_block_file(bytes(data)), [{
            "name": "概念", "type": 5, "members": ["0000001", "1000002"], "member_count": 2,
        }])

    def test_text_and_stats_preserve_unknown_columns(self):
        zs = tdx_files.parse_tdxzs("行业|880001|3|1|0|7|future\n".encode("gbk"))
        self.assertEqual(zs[0]["code"], "880001")
        self.assertEqual(zs[0]["fields"][-1], "future")
        sp = tdx_files.parse_spblock("#中证\n1000001\nignored\n".encode("gbk"))
        self.assertEqual(sp[0]["members"], ["1000001"])
        stat = tdx_files.parse_tdxstat(("0|000001|x|12.5|20261009|3|1.2|x|x|20|3.4|" + "|".join(["x"] * 20) + "\n").encode("gbk"))
        self.assertEqual(stat[0]["pe_ttm"], 12.5)
        self.assertEqual(len(stat[0]["fields"]), 31)
        stat2 = tdx_files.parse_tdxstat2(("0|000001|20261009|12|x|10|x|x|x|x|x|x|x|880001|x|x|9|20|10|x|x\n").encode("gbk"))
        self.assertEqual(stat2[0]["block_index"], "880001")
        self.assertEqual(stat2[0]["amount_10k_yuan"], 12.0)

    def test_zip_and_chunk_guards(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("nested/tdxzs.cfg", b"name|880001")
        self.assertEqual(tdx_files.parse_zhb_zip(output.getvalue())["tdxzs.cfg"], b"name|880001")
        with self.assertRaises(tdx_files.TdxFileError):
            tdx_files.parse_file_chunk(struct.pack("<I", 8) + b"short")
        self.assertEqual(tdx_files.parse_file_chunk(struct.pack("<I", 3) + b"abcTAIL"), b"abc")

    def test_bounded_client_downloads_block_and_report_chunks(self):
        class FakeClient:
            def __init__(self):
                self.requests = []

            def _exchange(self, request):
                self.requests.append(request)
                if request.startswith(bytes.fromhex("0c391869")):
                    return struct.pack("<I", 5)
                if request.startswith(bytes.fromhex("0c37186a")):
                    offset, size = struct.unpack_from("<II", request, 12)
                    payload = b"hello"[offset:offset + size]
                    return struct.pack("<I", len(payload)) + payload
                offset = struct.unpack_from("<I", request, 12)[0]
                payload = b"report"[offset:offset + 0x7530]
                return struct.pack("<I", len(payload)) + payload

        client = FakeClient()
        self.assertEqual(tdx_files.download(client, "block_gn.dat"), b"hello")
        self.assertEqual(tdx_files.download(client, "zhb.zip"), b"report")
        self.assertEqual(len(client.requests), 3)


if __name__ == "__main__":
    unittest.main()
