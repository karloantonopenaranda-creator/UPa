"""F1: framing over a TCP byte stream, plus message validation (no broker needed)."""
from __future__ import annotations

import socket
import unittest

from pubsub.common import protocol
from pubsub.common.protocol import FrameReader, ProtocolError


class FramingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.a, self.b = socket.socketpair()
        self.b.settimeout(2)
        self.reader = FrameReader(self.b)

    def tearDown(self) -> None:
        self.a.close()
        self.b.close()

    def test_single_frame_round_trip(self):
        msg = {"type": "PUBLISH", "msg_id": "m1", "topic": "casa-jaro.announcements", "payload": {"title": "Hi"}}
        self.a.sendall(protocol.encode(msg))
        self.assertEqual(self.reader.read(), msg)

    def test_two_frames_in_one_send(self):
        self.a.sendall(protocol.encode({"type": "PING"}) + protocol.encode({"type": "STATUS"}))
        self.assertEqual(self.reader.read()["type"], "PING")
        self.assertEqual(self.reader.read()["type"], "STATUS")

    def test_frame_split_across_sends(self):
        data = protocol.encode({"type": "ACK", "seq": 42})
        self.a.sendall(data[:5])
        self.b.settimeout(0.2)
        with self.assertRaises(TimeoutError):  # incomplete: nothing returned yet...
            self.reader.read()
        self.a.sendall(data[5:])
        self.b.settimeout(2)
        self.assertEqual(self.reader.read(), {"type": "ACK", "seq": 42})  # ...and nothing was lost

    def test_multibyte_character_split_across_sends(self):
        data = protocol.encode({"type": "PUBLISH", "msg_id": "m", "topic": "t", "payload": {"title": "Rent ₱2,500"}})
        cut = data.index("₱".encode()) + 1  # cut in the MIDDLE of the 3-byte UTF-8 peso sign
        self.a.sendall(data[:cut])
        self.a.sendall(data[cut:])
        self.assertEqual(self.reader.read()["payload"]["title"], "Rent ₱2,500")

    def test_newline_inside_text_does_not_break_framing(self):
        msg = {"type": "PUBLISH", "msg_id": "m", "topic": "t", "payload": {"body": "line 1\nline 2"}}
        self.a.sendall(protocol.encode(msg))
        self.assertEqual(self.reader.read(), msg)

    def test_peer_close_returns_none(self):
        self.a.close()
        self.assertIsNone(self.reader.read())

    def test_malformed_frame_raises_but_stream_stays_usable(self):
        self.a.sendall(b"this is not json\n" + protocol.encode({"type": "PING"}))
        with self.assertRaises(ProtocolError) as ctx:
            self.reader.read()
        self.assertEqual(ctx.exception.code, protocol.BAD_FRAME)
        self.assertEqual(self.reader.read(), {"type": "PING"})

    def test_oversized_frame_rejected(self):
        reader = FrameReader(self.b, max_frame=100)
        self.a.sendall(b"x" * 300)
        with self.assertRaises(ProtocolError) as ctx:
            reader.read()
        self.assertEqual(ctx.exception.code, protocol.FRAME_TOO_LARGE)

    def test_encode_refuses_oversized_message(self):
        with self.assertRaises(ProtocolError):
            protocol.encode({"type": "PUBLISH", "payload": "x" * (protocol.MAX_FRAME_BYTES + 1)})


class ValidationTests(unittest.TestCase):
    def test_valid_topics(self):
        for topic in ("casa-jaro.announcements", "molo-hub.maintenance", "dormcare.verification", "alerts"):
            self.assertEqual(protocol.validate_topic(topic), topic)

    def test_invalid_topics(self):
        for topic in ("", "Casa.Jaro", "casa..jaro", ".casa", "casa.", "casa jaro", "casa.*", 7, "a" * 200):
            with self.assertRaises(ProtocolError, msg=repr(topic)):
                protocol.validate_topic(topic)

    def test_client_ids(self):
        protocol.validate_client_id("tenant-juan")
        for bad in ("", "has space", "-leading-dash", "x" * 65, None):
            with self.assertRaises(ProtocolError, msg=repr(bad)):
                protocol.validate_client_id(bad)

    def test_required_fields(self):
        with self.assertRaises(ProtocolError) as ctx:
            protocol.validate({"type": "PUBLISH", "topic": "t"})
        self.assertEqual(ctx.exception.code, protocol.BAD_REQUEST)
        self.assertIn("msg_id", ctx.exception.reason)

    def test_unknown_or_disallowed_type(self):
        with self.assertRaises(ProtocolError) as ctx:
            protocol.validate({"type": "TELEPORT"})
        self.assertEqual(ctx.exception.code, protocol.UNKNOWN_TYPE)
        with self.assertRaises(ProtocolError):  # DELIVER is a broker->client type; clients may not send it
            protocol.validate({"type": "DELIVER", "seq": 1, "topic": "t", "publisher": "p", "payload": {},
                               "published_at": 0, "redelivery": False}, protocol.CLIENT_TYPES)


if __name__ == "__main__":
    unittest.main()
