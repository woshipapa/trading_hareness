import io
import struct
import unittest
import zipfile
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

from app.datasources.sources import tdx_files, tdx_protocol, tdx_reference_files


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tdx_zhb_20261009"


def _block_file(name: str, block_type: int, members: list[str]) -> bytes:
    data = bytearray(384) + struct.pack("<H", 1)
    data += name.encode("ascii").ljust(9, b"\0") + struct.pack("<HH", len(members), block_type)
    data += b"".join(member.encode("ascii") for member in members)
    data += b"\0" * (400 * 7 - len(members) * 7)
    return bytes(data)


def _zip_files(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class _FakeClient:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.requests: list[bytes] = []

    def _exchange(self, request: bytes) -> bytes:
        self.requests.append(request)
        if request.startswith(bytes.fromhex("0c391869")):
            filename = request[12:52].split(b"\0", 1)[0].decode()
            return struct.pack("<I", len(self.files[filename]))
        if request.startswith(bytes.fromhex("0c37186a")):
            offset, size = struct.unpack_from("<II", request, 12)
            filename = request[12 + 8:12 + 108].split(b"\0", 1)[0].decode()
            content = self.files[filename][offset:offset + size]
            return struct.pack("<I", len(content)) + content
        offset = struct.unpack_from("<I", request, 12)[0]
        filename = request[20:120].split(b"\0", 1)[0].decode()
        content = self.files[filename][offset:offset + tdx_files.FILE_CHUNK_SIZE]
        return struct.pack("<I", len(content)) + content


class TdxReferenceFileAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls: list[dict] = []
        self.files = {
            name: (FIXTURE_DIR / name).read_bytes()
            for name in ("needini.dat", "hqrule.dat", "xgsg.cfg", "othersg.cfg", "tdxstat.cfg", "tdxstat2.cfg")
        }
        self.files.update({
            "tdxzs3.cfg": b"concept|880001|4|1|0|concept\nstyle|880002|5|1|0|style\n"
            b"region|880003|3|1|0|region\nindustry|880004|12|1|0|industry\n",
            "spblock.dat": b"#region\n2000001\n",
        })
        self.files["block_gn.dat"] = _block_file("concept", 4, ["000001"])
        self.files["block_fg.dat"] = _block_file("style", 5, ["600000"])
        self.files["block_zs.dat"] = _block_file("industry", 12, ["300001"])
        self.files["zhb.zip"] = _zip_files({
            name: content for name, content in self.files.items()
            if name not in {"block_gn.dat", "block_fg.dat", "block_zs.dat", "zhb.zip"}
        })

    async def _call(self, operation, **kwargs):
        self.calls.append(kwargs)
        return operation(_FakeClient(self.files)), "fake-host"

    async def test_statistics_use_row_date_and_one_zip_download(self):
        with patch.object(tdx_protocol, "call", self._call):
            valuation = await tdx_reference_files.fetch_valuation(symbols=("000001.SZ",))
            daily_basic = await tdx_reference_files.fetch_daily_basic(symbols=("000001.SZ",))
        self.assertEqual(valuation.rows[0]["effective_date"], "20261009")
        self.assertEqual(valuation.rows[0]["pe_ttm"], 5.18)
        self.assertEqual(daily_basic.rows[0]["effective_date"], "20261009")
        self.assertEqual(daily_basic.rows[0]["amount_10k_yuan"], 125971.71)
        self.assertEqual([call["handshake_profile"] for call in self.calls], ["login_one", "login_one"])

    async def test_membership_joins_taxonomies_and_sets_utc_known_at(self):
        with patch.object(tdx_protocol, "call", self._call):
            evidence = await tdx_reference_files.fetch_membership()
        rows = {row["sector_key"]: row for row in evidence.rows}
        self.assertEqual(rows["880001"]["taxonomy_key"], "tdx_files_concept")
        self.assertEqual(rows["880002"]["symbol"], "600000.SH")
        self.assertEqual(rows["880003"]["symbol"], "000001.BJ")
        self.assertEqual(rows["880004"]["symbol"], "300001.SZ")
        self.assertEqual(rows["880001"]["known_at"].tzinfo, timezone.utc)
        self.assertTrue(any("unmatched_boards=" in warning for warning in evidence.warnings))

    async def test_calendar_and_ipo_keep_declared_file_meaning(self):
        self.files["zhb.zip"] = _zip_files({
            "needini.dat": b"[Holiday]\nNUM=1\nY36=2026,1001,\n",
            "hqrule.dat": b"[RULE]\nSHGTDayMax=520\n",
            "xgsg.cfg": b"0|001381|20261019|11.20|x|x|x|x|x|x|x|x|x|x|Name|\n",
            "othersg.cfg": b"1|600000|123456|96000|9.16|x|x|1000|20261020|x|x|Bond\n",
        })
        with patch.object(tdx_protocol, "call", self._call):
            calendar = await tdx_reference_files.fetch_trade_calendar()
            ipo = await tdx_reference_files.fetch_ipo_calendar()
        self.assertEqual(calendar.rows, [{"exchange": "CN", "calendar_date": "2026-10-01", "is_open": False,
                                          "row_type": "holiday", "source_file": "needini.dat"}])
        self.assertTrue(any("open days are not fabricated" in warning for warning in calendar.warnings))
        self.assertEqual({row["source_file"] for row in ipo.rows}, {"xgsg.cfg", "othersg.cfg"})
        self.assertEqual(ipo.rows[0]["symbol"], "001381.SZ")


if __name__ == "__main__":
    unittest.main()
