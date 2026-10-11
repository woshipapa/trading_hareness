import struct
import unittest

from app.datasources.sources import tdx_local_extra as extra


class TdxLocalExtraTests(unittest.TestCase):
    def test_gbbq_encrypt_decrypt_and_parse(self):
        clear = struct.pack("<B7sIBffff", 1, b"600000\0", 20261009, 1, 1.25, 9.5, 2.0, 3.0)
        encrypted = extra.encrypt_gbbq_bytes(clear)
        self.assertEqual(extra.decrypt_gbbq_bytes(encrypted), clear)
        row = extra.parse_gbbq_bytes(encrypted)[0]
        self.assertEqual(row["code"], "600000")
        self.assertEqual(row["event_date"], "2026-10-09")
        self.assertEqual(row["category_name"], "除权除息")
        self.assertEqual(row["cash_dividend"], 1.25)

    def test_metadata_text_and_taxonomy(self):
        data = "1|600000|T001|||X020\n".encode("gbk")
        self.assertEqual(extra.parse_tdxhy_cfg_bytes(data)[0]["tdx_industry_code"], "T001")
        self.assertEqual(extra.parse_tdxhy_cfg_bytes(data)[0]["sw_industry_code"], "X020")
        incon = "######制造业\nT001|制造业\n".encode("gbk")
        taxonomy = extra.parse_incon_dat_bytes(incon)
        self.assertEqual(taxonomy, [{"section": "制造业", "code": "T001", "name": "制造业"}])
        joined = extra.map_tdxhy_to_incon(extra.parse_tdxhy_cfg_bytes(data), taxonomy)[0]
        self.assertEqual(joined["tdx_industry_name"], "制造业")
        self.assertIsNone(joined["sw_industry_name"])

    def test_tnf_dbf_extended_and_user_board(self):
        tnf = bytearray(50 + 314)
        tnf[50:56] = b"600000"
        tnf[73:81] = "浦发银行".encode("gbk")
        self.assertEqual(extra.parse_tnf_bytes(bytes(tnf))[0]["code"], "600000")

        fields = bytearray(32)
        fields[:11] = b"CODE\0\0\0\0\0\0\0"
        fields[11] = ord("C")
        fields[16] = 6
        dbf = bytearray(32)
        struct.pack_into("<IHH", dbf, 4, 1, 65, 7)
        dbf.extend(fields)
        dbf.extend(b"\r")
        dbf.extend(b" \x36\x30\x30\x30\x30\x30")
        parsed = extra.parse_base_dbf_bytes(bytes(dbf), encoding="ascii")
        self.assertEqual(parsed["fields"][0]["name"], "CODE")
        self.assertEqual(parsed["records"][0]["CODE"], "600000")

        day = struct.pack("<IffffIIf", 20261009, 12.3, 13.0, 12.0, 12.5, 100, 99, 12.4)
        parsed_day = extra.parse_extended_daily_bytes(day, "CF")
        self.assertEqual(parsed_day[0]["close"], 12.5)
        self.assertEqual(parsed_day[0]["amount"], 100)
        self.assertEqual(parsed_day[0]["settlement"], 12.4)
        self.assertEqual(extra.parse_block_members_bytes(b"1600000\n0000001\n"), ["600000", "000001"])
        self.assertEqual(extra.parse_zxg_bytes(b"1600000\n1#000001\n"), [{"market": "1", "code": "600000"}, {"market": "1", "code": "000001"}])
        cfg = b"Growth".ljust(50, b"\0") + b"123".ljust(70, b"\0")
        self.assertEqual(extra.parse_blocknew_cfg_bytes(cfg)[0]["file_id"], "123")

    def test_malformed_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            extra.decrypt_gbbq_bytes(struct.pack("<I", 1) + b"short")
        with self.assertRaises(ValueError):
            extra.parse_tnf_bytes(b"short")
        with self.assertRaises(ValueError):
            extra.parse_base_dbf_bytes(b"short")


if __name__ == "__main__":
    unittest.main()
