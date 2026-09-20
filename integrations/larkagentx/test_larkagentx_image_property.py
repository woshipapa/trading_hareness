import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from larkagentx_image_property import extract_rich_text_image_resource


def varint(value):
	result = bytearray()
	while value > 0x7F:
		result.append((value & 0x7F) | 0x80)
		value >>= 7
	result.append(value)
	return bytes(result)


def field(number, value):
	return varint(number << 3 | 2) + varint(len(value)) + value


class RichTextImagePropertyTests(unittest.TestCase):
	def test_extracts_image_key_and_gcm_pair_from_nested_property(self):
		image_key = b"img_v3_0215h_example-image-key"
		key = bytes(range(32))
		iv = bytes(range(12))
		cipher = field(1, key) + field(2, iv) + field(3, b"")
		property_bytes = field(2, image_key) + field(17, field(3, field(2, cipher)))

		self.assertEqual(
			extract_rich_text_image_resource(property_bytes, "element-1"),
			{
				"image_id": image_key.decode(),
				"key_hex": key.hex(),
				"iv_hex": iv.hex(),
				"source_id": "element-1",
			},
		)

	def test_rejects_unrelated_opaque_property(self):
		self.assertEqual(extract_rich_text_image_resource(field(1, b"ordinary text")), {})


if __name__ == "__main__":
	unittest.main()
