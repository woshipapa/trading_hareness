import re
import struct
import unittest
from pathlib import Path

from app.datasources.sources.tdx_mac import DEFAULT_BITMAP
from app.datasources.sources.tdx_mac_fields import (
    MAC_FIELDS,
    MATCH,
    NO_REFERENCE,
    bitmap_for_bits,
    decode_dynamic_response,
    decode_dynamic_row,
    reconcile,
)

ROUTE_NOTE = Path(__file__).resolve().parents[2] / "docs" / "archive" / "tdx-route-mac-fields.md"
#: Closed as UNKNOWN by delta-3 Q4 (flow) and Q5 (limit fields).
UNKNOWN_BITS = (0x16, 0x1D, 0x59, 0x5D, 0x5E, *range(0x6C, 0x73), 0x74, 0x75, 0x76, 0x7A)


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

    def test_the_bits_the_quote_and_limit_adapters_read_keep_their_gotdx_identity(self):
        # fetch_watch_snapshot and fetch_limit_prices read these by name; a swapped limit pair would flip up and down.
        expected = {0x04: ("close", "float32"), 0x05: ("vol", "uint32"), 0x06: ("vol_ratio", "float32"),
                    0x07: ("amount", "float32"), 0x13: ("server_update_date", "uint32"),
                    0x14: ("server_update_time", "uint32"), 0x1B: ("turnover", "float32"),
                    0x20: ("buy_price_limit", "float32"), 0x21: ("sell_price_limit", "float32")}
        for bit, identity in expected.items():
            self.assertEqual((MAC_FIELDS[bit].name, MAC_FIELDS[bit].format), identity, hex(bit))
        self.assertEqual((MAC_FIELDS[0x20].canonical_key, MAC_FIELDS[0x21].canonical_key), ("limit_up", "limit_down"))

    def test_the_iopv_bits_feed_fund_iopv_not_the_published_nav(self):
        self.assertEqual([(MAC_FIELDS[bit].name, MAC_FIELDS[bit].format, MAC_FIELDS[bit].capability_ids) for bit in (0x24, 0x27)],
                         [("pre_iopv", "float32", ("fund.iopv",)), ("iopv", "float32", ("fund.iopv",))])

    def test_index_bits_are_named_as_gotdx_names_them(self):
        # gotdx mac_board_members_dynamic.go:113,116 -- 0x37 index_metric (float32), 0x3a non_index_flag (uint32)
        self.assertEqual((MAC_FIELDS[0x37].name, MAC_FIELDS[0x37].format), ("index_metric", "float32"))
        self.assertEqual((MAC_FIELDS[0x3A].name, MAC_FIELDS[0x3A].format), ("non_index_flag", "uint32"))
        values, _ = decode_dynamic_row(struct.pack("<I", 3), bitmap_for_bits([0x3A]))
        self.assertEqual(values, {"non_index_flag": 3})

    def test_unknown_fields_keep_their_wire_name_and_have_no_key_or_capability(self):
        for bit in UNKNOWN_BITS:
            field = MAC_FIELDS[bit]
            self.assertEqual(field.name, f"bit_0x{bit:02x}")
            self.assertIsNone(field.canonical_key, field.name)
            self.assertEqual((field.unit, field.confidence, field.capability_ids, field.reconciliation),
                             ("", "unknown", (), NO_REFERENCE), field.name)

    def test_a_field_without_a_canonical_key_binds_to_no_capability(self):
        for field in MAC_FIELDS:
            if field.canonical_key is None:
                self.assertEqual(field.capability_ids, (), field.name)

    def test_the_provider_main_flow_pair_is_defined_as_the_providers(self):
        for bit in (0x38, 0x6B):
            field = MAC_FIELDS[bit]
            self.assertEqual((field.canonical_key, field.unit), ("main_net_amount", "yuan"))
            self.assertEqual(field.note, "provider-defined main_in - main_out, yuan")
        self.assertEqual(ROUTE_NOTE.read_text().count("provider-defined main_in - main_out, yuan"), 2)

    def test_limit_up_days_and_the_sampled_intraday_changes_carry_the_delta_3_q5_statuses(self):
        days = MAC_FIELDS[0x58]
        self.assertEqual((days.name, days.canonical_key, days.reconciliation), ("annual_limit_up_days", "annual_limit_up_days", MATCH))
        self.assertNotIn(days.confidence, ("plausible", "unknown", "unverified"))
        self.assertIn("calendar year", days.note)
        for bit, slot in zip(range(0x90, 0x97), ("1000", "1030", "1100", "1130", "1330", "1400", "1430")):
            field = MAC_FIELDS[bit]
            self.assertEqual((field.name, field.canonical_key, field.reconciliation),
                             (f"change_at_{slot}", f"change_at_{slot}", MATCH))
            self.assertIn("sampled", field.note)

    def test_every_row_of_the_route_note_table_agrees_with_the_registry(self):
        checked = 0
        for line in ROUTE_NOTE.read_text().splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 5 or not re.fullmatch(r"[0-9a-f]{2}(-[0-9a-f]{2})?", cells[0]):
                continue
            low, _, high = cells[0].partition("-")
            fields = [MAC_FIELDS[bit] for bit in range(int(low, 16), int(high or low, 16) + 1)]
            named = {name.strip() for name in cells[1].split(",")}
            self.assertLessEqual(named, {field.name for field in fields}, cells[0])
            self.assertEqual(set(re.findall(r"`([^`]+)`", cells[4])),
                             {capability for field in fields for capability in field.capability_ids}, cells[0])
            claims_match = "MATCH" in cells[3].replace("NO_REFERENCE", "")
            self.assertEqual(claims_match, any(field.reconciliation == MATCH for field in fields), cells[0])
            checked += 1
        self.assertGreater(checked, 25)

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
