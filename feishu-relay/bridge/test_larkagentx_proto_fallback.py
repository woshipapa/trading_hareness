import sys
import unittest
import random
import gzip
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
	import bridge
	from proto_wire import tolerant_websocket_decode, tolerant_websocket_decode_with_meta
	from proto_wire import decode_primary_websocket
	from larkx.proto import proto_pb2 as P
except ModuleNotFoundError as error:  # The workstation does not carry the supervisor venv.
	bridge = None
	tolerant_websocket_decode = None
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
			meta, messages = tolerant_websocket_decode(frame)

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
			meta, messages = tolerant_websocket_decode(frame)

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

	def test_isolates_one_bad_entry_and_keeps_later_message(self):
		bad_entity = bfield(1, b"bad-msg") + b"\x80"
		bad_entry = bfield(1, b"bad-entry") + bfield(2, bad_entity)
		good_entity = (
			bfield(1, b"good-msg")
			+ vfield(2, 1)
			+ bfield(3, b"synthetic-user")
			+ bfield(10, b"7661209668907207659")
			+ vfield(46, 2)
		)
		good_entry = bfield(1, b"good-entry") + bfield(2, good_entity)
		push = bfield(1, bad_entry) + bfield(1, good_entry)
		packet = bfield(1, b"partial-sid") + vfield(3, 6) + bfield(5, push)
		frame = bfield(8, packet)

		with patch.object(bridge.decoders, "decode_message_content", return_value=("synthetic", None)):
			meta, messages, telemetry = tolerant_websocket_decode_with_meta(frame)

		self.assertEqual(meta["cmd"], 6)
		self.assertEqual([message["msg_id"] for message in messages], ["good-msg"])
		self.assertTrue(telemetry["partial"])
		self.assertEqual(len(telemetry["entry_errors"]), 1)
		self.assertEqual(telemetry["entry_errors"][0]["layer"], "entity")

	def test_random_valid_extensions_do_not_break_message_recovery(self):
		for seed in range(64):
			rng = random.Random(seed)
			unknown_packet = bfield(rng.choice([70, 71, 72, 73]), f"packet-{seed}".encode())
			unknown_entity = vfield(rng.choice([70, 71, 72, 73]), rng.randrange(0, 1 << 20))
			entity = (
				bfield(1, f"fuzz-{seed}".encode())
				+ vfield(2, 1)
				+ bfield(3, b"synthetic-user")
				+ unknown_entity
				+ bfield(10, b"7661209668907207659")
				+ vfield(46, 2)
			)
			entry = bfield(1, b"entry") + bfield(2, entity)
			push = bfield(1, entry)
			packet = bfield(1, f"fuzz-sid-{seed}".encode()) + unknown_packet + vfield(3, 6) + bfield(5, push)
			frame = bfield(8, packet)

			with patch.object(bridge.decoders, "decode_message_content", return_value=("synthetic", None)):
				_, messages, telemetry = tolerant_websocket_decode_with_meta(frame)

			self.assertEqual([message["msg_id"] for message in messages], [f"fuzz-{seed}"])
			self.assertGreaterEqual(len(telemetry["unknown_fields"]), 2)

	def test_rejects_excessive_nested_groups(self):
		raw = b""
		for field in range(1, 18):
			raw += vfield((field << 3) | 3, 0)
		for field in range(17, 0, -1):
			raw += vfield((field << 3) | 4, 0)
		with self.assertRaises(ValueError):
			tolerant_websocket_decode_with_meta(raw)

	def test_decodes_gzip_payload_encoding(self):
		entity = bfield(1, b"gzip-msg") + vfield(2, 1) + bfield(3, b"synthetic-user") + bfield(10, b"7661209668907207659") + vfield(46, 2)
		entry = bfield(1, b"entry") + bfield(2, entity)
		push = bfield(1, entry)
		packet = P.Packet(sid="gzip-sid", cmd=6, payload=gzip.compress(push))
		frame = P.Frame(payloadEncoding="gzip", payload=packet.SerializeToString())
		with patch.object(bridge.decoders, "decode_message_content", return_value=("synthetic", None)):
			decoded_packet, messages = decode_primary_websocket(frame.SerializeToString())
		self.assertEqual(decoded_packet["cmd"], 6)
		self.assertEqual([message["msg_id"] for message in messages], ["gzip-msg"])

	def test_accepts_json_dispatch_control_frame_without_packet_parse(self):
		frame = P.Frame(
			payloadEncoding="json",
			payloadType="dispatch.command.DispatchCommandPayload",
			payload=json.dumps({"command": 400, "command_id": "opaque", "body": {}, "ext": {}}).encode(),
		)
		packet, messages = decode_primary_websocket(frame.SerializeToString())
		self.assertEqual(packet["transport"], "json")
		self.assertEqual(packet["json_command"], 400)
		self.assertEqual(messages, [])

	def test_recovers_card_json_from_nested_card_payload(self):
		card = {"schema": "2.0", "body": {"elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "cat card body"}}]}}
		# universalCardEntity is present in newer CardContent payloads while the
		# checked-in upstream descriptor only knows it as opaque bytes.
		card_content = P.CardContent(
			universalCardEntity=(b"opaque-prefix" + json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
		).SerializeToString()
		entity = (
			bfield(1, b"cat-card-msg")
			+ vfield(2, 14)
			+ bfield(3, b"synthetic-user")
			+ bfield(5, card_content)
			+ bfield(10, b"7684122107030031634")
			+ vfield(46, 2)
		)
		entry = bfield(1, b"entry") + bfield(2, entity)
		push = bfield(1, entry)
		packet = bfield(1, b"cat-card-sid") + vfield(3, 6) + bfield(5, push)
		frame = bfield(8, packet)

		with patch.object(bridge.decoders, "decode_message_content", return_value=("[卡片]", {})):
			_, messages, _ = tolerant_websocket_decode_with_meta(frame)

		self.assertEqual(messages[0]["msg_type_name"], "CARD")
		self.assertEqual(json.loads(messages[0]["content_data"]["jsonCard"]), card)
		self.assertIn("cat card body", messages[0]["content"])

	def test_primary_card_path_merges_recovered_card_json(self):
		card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": "primary card body"}]}}
		card_content = P.CardContent(
			universalCardEntity=json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
		).SerializeToString()
		entity = (
			bfield(1, b"primary-card-msg")
			+ vfield(2, 14)
			+ bfield(3, b"synthetic-user")
			+ bfield(5, card_content)
			+ bfield(10, b"7684122107030031634")
			+ vfield(46, 2)
		)
		entry = bfield(1, b"entry") + bfield(2, entity)
		push = bfield(1, entry)
		packet = P.Packet(sid="primary-card-sid", cmd=6, payload=push)
		frame = P.Frame(payload=packet.SerializeToString()).SerializeToString()

		with patch.object(bridge.decoders, "decode_push_messages", return_value=[{
			"msg_id": "primary-card-msg",
			"msg_type_name": "CARD",
			"content": "[卡片]",
			"content_data": {},
		}]):
			_, messages = decode_primary_websocket(frame)

		self.assertEqual(json.loads(messages[0]["content_data"]["jsonCard"]), card)
		self.assertIn("primary card body", messages[0]["content"])


if __name__ == "__main__":
	unittest.main()
