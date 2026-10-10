import struct
import unittest

from app.datasources.sources.tdx_mac_fields import (
    DEFAULT_BITMAP,
    MAC_FIELDS,
    MATCH,
    NO_REFERENCE,
    bitmap_for_bits,
    decode_dynamic_response,
    decode_dynamic_row,
    reconcile,
)


class MacFieldRegistryTests(unittest.TestCase):
    def test_registry_covers_wire_bitmap_and_unknowns_are_wire_named(self):
        self.assertEqual(len(MAC_FIELDS), 160)
        self.assertEqual(MAC_FIELDS[0].name, "pre_close")
        self.assertEqual(MAC_FIELDS[0x96].name, "change_at_1430")
        self.assertEqual(MAC_FIELDS[0x4F].name, "bit_0x4f")
        self.assertEqual(MAC_FIELDS[0x4F].canonical_key, None)
        self.assertEqual(MAC_FIELDS[0x05].unit, "lots")
        self.assertEqual(MAC_FIELDS[0x5C].canonical_key, "close_streak")
        self.assertNotIn("limits.ladder", MAC_FIELDS[0x5C].capability_ids)
        self.assertIsNone(MAC_FIELDS[0x5D].canonical_key)
        self.assertIsNone(MAC_FIELDS[0x5E].canonical_key)

    def test_dynamic_row_uses_bit_order_and_signed_formats(self):
        bitmap = bitmap_for_bits([0, 43, 5, 140])
        payload = struct.pack("<fIi i", 12.5, 17, 1, -4)
        values, end = decode_dynamic_row(payload, bitmap)
        self.assertEqual(end, 16)
        self.assertEqual(values["pre_close"], 12.5)
        self.assertEqual(values["flag_kcb"], 1)
        self.assertEqual(values["vol"], 17)
        self.assertEqual(values["bid_ask_diff"], -4)

    def test_dynamic_response_fixture(self):
        bitmap = bitmap_for_bits([0, 4])
        row = struct.pack("<H22s44sff", 0, b"000001\0", b"Ping An\0", 10.0, 10.5)
        body = bitmap + struct.pack("<IH", 1, 1) + row
        decoded = decode_dynamic_response(body)
        self.assertEqual(decoded[0]["symbol"], "000001")
        self.assertEqual(decoded[0]["close"], 10.5)

    def test_split_bitmap_fixture_keeps_bit_order(self):
        for bits in ([5], [92], [93], [94]):
            bitmap = bitmap_for_bits(bits)
            value = struct.pack("<i", -3) if bits == [92] else struct.pack("<I", 3)
            body = (
                bitmap
                + struct.pack("<IH", 1, 1)
                + struct.pack("<H22s44s", 0, b"000001\0", b"fixture\0")
                + value
            )
            row = decode_dynamic_response(body)[0]
            self.assertEqual(
                row[next(field.name for field in MAC_FIELDS if field.bit == bits[0])],
                -3 if bits == [92] else 3,
            )

    def test_truncated_row_fails_closed(self):
        with self.assertRaises(ValueError):
            decode_dynamic_row(b"\x00\x00\x00", DEFAULT_BITMAP)

    def test_reconcile(self):
        self.assertEqual(reconcile(10.0, 10.00001), MATCH)
        self.assertEqual(reconcile(10.0, 11.0), "MISMATCH")
        self.assertEqual(reconcile(None, 1), NO_REFERENCE)


if __name__ == "__main__":
    unittest.main()
