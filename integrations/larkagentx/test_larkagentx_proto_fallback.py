import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
	import bridge
	from proto_wire import tolerant_websocket_decode_with_meta
except ModuleNotFoundError as error:  # The workstation does not carry the supervisor venv.
	bridge = None
	tolerant_websocket_decode_with_meta = None
	BRIDGE_IMPORT_ERROR = error
else:
	BRIDGE_IMPORT_ERROR = None


def varint(value):
	out = bytearray()
	while value > 0x7F:
		out.append((value & 0x7F) | 0x80)
		value >>= 7
	out.append(value)
	return bytes(out)


def vfield(number, value):
	return varint((number << 3) | 0) + varint(value)


def bfield(number, value):
	return varint((number << 3) | 2) + varint(len(value)) + value


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class LarkAgentXProtoFallbackTests(unittest.TestCase):
	def test_recovers_message_when_known_field_has_new_wire_type(self):
		# createTime is normally a varint. Keep it length-delimited here to model
		# the schema drift that made the generated parser reject the old frame.
		entity = (
			bfield(1, b"synthetic-msg")
			+ vfield(2, 1)
			+ bfield(3, b"synthetic-user")
			+ bfield(4, b"not-a-varint")
			+ bfield(5, b"synthetic-content")
			+ bfield(10, b"7661209668907207659")
			+ vfield(46, 2)
		)
		entry = bfield(1, b"synthetic-key") + bfield(2, entity)
		push = bfield(1, entry)
		packet = bfield(1, b"synthetic-sid") + vfield(3, 6) + bfield(5, push)
		frame = bfield(8, packet)

		with patch.object(bridge.decoders, "decode_message_content", return_value=("synthetic", None)):
			meta, messages = bridge.tolerant_websocket_decode(frame)

		self.assertEqual(meta["cmd"], 6)
		self.assertEqual(len(messages), 1)
		self.assertEqual(messages[0]["msg_id"], "synthetic-msg")
		self.assertEqual(messages[0]["chat_id"], "7661209668907207659")

	def test_skips_unknown_protobuf_group_and_keeps_packet(self):
		entity = (
			bfield(1, b"group-msg")
			+ vfield(2, 1)
			+ bfield(3, b"synthetic-user")
			+ bfield(10, b"7661209668907207659")
			+ vfield(46, 2)
		)
		entry = bfield(1, b"entry") + bfield(2, entity)
		push = bfield(1, entry)
		# Field 90 is an unknown deprecated protobuf group.  It must not make
		# the tolerant envelope parser discard the valid packet that follows it.
		unknown_group = vfield((90 << 3) | 3, 0) + bfield(1, b"metadata") + vfield((90 << 3) | 4, 0)
		packet = bfield(1, b"group-sid") + unknown_group + vfield(3, 6) + bfield(5, push)
		frame = bfield(8, packet)

		with patch.object(bridge.decoders, "decode_message_content", return_value=("synthetic", None)):
			meta, messages = bridge.tolerant_websocket_decode(frame)

		self.assertEqual(meta["cmd"], 6)
		self.assertEqual(messages[0]["msg_id"], "group-msg")

	def test_records_unknown_fields_without_logging_payload(self):
		entity = (
			bfield(1, b"telemetry-msg")
			+ vfield(2, 1)
			+ bfield(3, b"synthetic-user")
			+ bfield(10, b"7661209668907207659")
			+ vfield(46, 2)
		)
		entry = bfield(1, b"entry") + bfield(2, entity)
		push = bfield(1, entry)
		# Field 77 is an extension in the packet envelope.  The value is
		# intentionally opaque; only its bounded fingerprint is exposed.
		packet = bfield(1, b"telemetry-sid") + bfield(77, b"opaque-metadata") + vfield(3, 6) + bfield(5, push)
		frame = bfield(8, packet)

		with patch.object(bridge.decoders, "decode_message_content", return_value=("synthetic", None)):
			meta, messages, telemetry = tolerant_websocket_decode_with_meta(frame)

		self.assertEqual(meta["cmd"], 6)
		self.assertEqual(messages[0]["msg_id"], "telemetry-msg")
		self.assertEqual(telemetry["unknown_fields"], {"packet:77:2": 1})
		self.assertEqual(len(telemetry["field_fingerprint"]), 16)
		self.assertNotIn("opaque-metadata", telemetry["field_fingerprint"])


if __name__ == "__main__":
	unittest.main()
