import struct
import unittest
import hashlib

from app.datasources.sources.tdx_fin_history import (
    ManifestEntry, gpcw_field_unit, manifest_changes, normalize_report_period,
    parse_gpcw_dat, parse_manifest, parse_tipinfo, ttm_from_cumulative, verify_manifest_entry,
)


class TdxFinancialHistoryTests(unittest.TestCase):
    def test_manifest_and_change_detection(self):
        old = parse_manifest("gpcw20241231.zip," + "a" * 32 + ",10\n")
        new = parse_manifest("gpcw20241231.zip," + "b" * 32 + ",11\ngpcw20251231.zip," + "c" * 32 + ",12\n")
        self.assertEqual(manifest_changes(old, new), {"added": ["gpcw20251231.zip"], "removed": [], "changed": ["gpcw20241231.zip"]})
        self.assertTrue(verify_manifest_entry(ManifestEntry("x", hashlib.md5(b"abc").hexdigest(), 3), b"abc"))
        self.assertFalse(verify_manifest_entry(ManifestEntry("x", hashlib.md5(b"abc").hexdigest(), 3), b"abd"))

    def test_gpcw_header_and_units(self):
        values = [0.0] * 242
        values[73] = 170_899_144_704.0
        values[95] = 86_228_148_224.0
        values[237] = 1_256_197_760.0
        payload = struct.pack("<hI H 3L", 1, 20241231, 1, 0, len(values) * 4, 0)
        payload += struct.pack("<6s1sL", b"600519", b"\0", struct.calcsize("<hI H 3L") + struct.calcsize("<6s1sL"))
        payload += struct.pack("<" + "f" * len(values), *values)
        row = parse_gpcw_dat(payload, filename="gpcw20241231.zip")[0]
        self.assertEqual(row["report_period"], "2024-12-31")
        self.assertEqual(row["field_count"], 242)
        self.assertEqual(row["fields"]["其中：营业收入"], values[73])
        self.assertEqual(row["fields"]["归属于母公司所有者的净利润"], values[95])
        self.assertEqual(gpcw_field_unit(238), "shares")

    def test_tipinfo_keeps_candidate_unpromoted(self):
        row = parse_tipinfo(b"0|000001|20260630|1.24|20260815|20240221|x\n")[0]
        self.assertEqual(row["announcement_date_candidate"], "20260815")
        self.assertNotIn("available_at", row)

    def test_period_and_ttm(self):
        self.assertEqual(normalize_report_period("gpcw20101231.zip"), "2010-12-31")
        self.assertEqual(ttm_from_cumulative(10, 4, 3), 11)


if __name__ == "__main__":
    unittest.main()
