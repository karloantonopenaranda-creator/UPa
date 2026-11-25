"""
Regression tests (R) for defects found in the pre-submission audit of 22 November 2026.

Each test failed against the code as audited and passes with the fix. They use the real broker and, where
the defect was in a client, the real client programs.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from pubsub.broker.store import Store
from pubsub.common import protocol
from pubsub.publisher.publisher import Publisher
from tests.helpers import ROOT, BrokerTestCase, OutputCollector, RawClient, queue_depth

SUB, PUB = protocol.SUBSCRIBER, protocol.PUBLISHER
TOPIC = "casa-jaro.announcements"
LIMIT = 64 * 1024


def publish_frame(payload_json: str, msg_id: str = "m1") -> bytes:
    return f'{{"type":"PUBLISH","msg_id":"{msg_id}","topic":"{TOPIC}","payload":{payload_json}}}\n'.encode()


class UndeliverableMessageTests(BrokerTestCase):
    """R1: a message the broker confirms must be one it can deliver."""

    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def assert_refused_and_queue_still_flows(self, frame: bytes) -> None:
        self.assertLessEqual(len(frame), LIMIT, "the test frame must itself be within the inbound limit")
        sub = self.client("tenant-juan", SUB, TOPIC)
        pub = self.publisher()
        pub.send_raw(frame)
        reply = pub.recv()
        self.assertEqual(reply["type"], protocol.ERROR, f"undeliverable message was accepted: {reply}")
        self.assertEqual(queue_depth(self.status(), "tenant-juan"), 0)
        pub.publish(TOPIC, "next message")
        self.assertEqual(sub.deliveries(1)[0]["payload"]["title"], "next message")

    def test_R1a_near_limit_message_is_refused_before_puback(self):
        empty = len(publish_frame('{"body":""}'))
        self.assert_refused_and_queue_still_flows(publish_frame('{"body":"%s"}' % ("x" * (LIMIT - 10 - empty))))

    def test_R1b_payload_that_grows_when_re_encoded_is_refused(self):
        self.assert_refused_and_queue_still_flows(publish_frame("[" + ",".join(["1E15"] * 4000) + "]"))

    def test_R1c_too_deeply_nested_payload_is_refused(self):
        self.assert_refused_and_queue_still_flows(publish_frame("[" * 200 + "]" * 200))

    def test_R1d_undeliverable_message_already_in_the_database_does_not_block_the_queue(self):
        self.broker.kill()
        store = Store(self.broker.db)
        store.add_subscription("tenant-juan", TOPIC)
        store.publish("old", "bad", TOPIC, {"body": "x" * (LIMIT + 100)})
        store.publish("old", "good", TOPIC, {"title": "behind the bad one"})
        store.close()
        self.broker.start()
        sub = self.client("tenant-juan", SUB)
        self.assertEqual(sub.deliveries(1)[0]["payload"]["title"], "behind the bad one")


class DeduplicationTests(BrokerTestCase):
    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def test_R2_new_message_is_not_mistaken_for_a_duplicate_after_the_database_is_reset(self):
        _, out = self.spawn_cli("-m", "pubsub.subscriber", "--id", "tenant-juan", "--topics", TOPIC,
                                "--state-dir", str(self.workdir / "subs"))
        out.wait_for("SUBSCRIBED")
        self.publisher().publish(TOPIC, "before the reset")
        out.wait_for("before the reset")
        self.broker.kill()
        self.broker.db = self.workdir / "fresh.db"  # sequence numbers start again at 1
        self.broker.start()
        out.wait_for("SUBSCRIBED", count=2, timeout=20)
        self.assertEqual(self.publisher().publish(TOPIC, "after the reset")["seq"], 1)
        out.wait_for("after the reset")

    def test_R3_message_ids_differ_beyond_the_first_32_random_bits(self):
        self.client("tenant-juan", SUB, TOPIC)
        pub = Publisher("manager-office", "127.0.0.1", self.broker.port, retries=0)
        self.addCleanup(pub.close)
        uuids = [SimpleNamespace(hex="aaaaaaaa" + "1" * 24), SimpleNamespace(hex="aaaaaaaa" + "2" * 24)]
        with mock.patch("pubsub.publisher.publisher.uuid.uuid4", side_effect=uuids), \
                mock.patch("pubsub.publisher.publisher.console.log"):
            pub.publish(TOPIC, {"title": "one"})
            second = pub.publish(TOPIC, {"title": "two"})
        self.assertFalse(second["duplicate"], "a different message was discarded as a duplicate")


class ClientProgramTests(BrokerTestCase):
    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def interactive_publisher(self) -> tuple[subprocess.Popen, OutputCollector]:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "pubsub.publisher", "--id", "manager-office", "--no-color",
                                 "--port", str(self.broker.port)],
                                cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        out = OutputCollector(proc.stdout)
        self._procs.append((proc, out))
        self.addCleanup(proc.stdin.close)
        out.wait_for("CONNECTED")
        return proc, out

    def test_R4_status_right_after_a_broker_restart_reconnects(self):
        proc, out = self.interactive_publisher()
        self.broker.restart()
        proc.stdin.write("status\n")
        proc.stdin.flush()
        out.wait_for("no subscribers yet", timeout=15)
        self.assertIsNone(proc.poll(), "the interactive publisher exited")

    def test_R4b_status_while_the_broker_is_down_does_not_end_the_publisher(self):
        proc, out = self.interactive_publisher()
        self.broker.kill()
        proc.stdin.write("status\nhelp\n")
        proc.stdin.flush()
        out.wait_for("show this help", count=2, timeout=20)
        self.assertIsNone(proc.poll(), "the interactive publisher exited")

    def test_R4c_status_option_against_a_listener_that_hangs_up_fails_cleanly(self):
        with socket.socket() as fake:
            fake.bind(("127.0.0.1", 0))
            fake.listen()
            proc = subprocess.Popen([sys.executable, "-m", "pubsub.publisher", "--id", "status-check", "--status",
                                     "--port", str(fake.getsockname()[1])],
                                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            fake.settimeout(15)
            fake.accept()[0].close()
            output, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", output)

    def test_R5_send_rejects_zero_and_negative_event_numbers(self):
        proc, out = self.interactive_publisher()
        proc.stdin.write("send 0\nsend -1\nsend 1\n")
        proc.stdin.flush()
        out.wait_for("PUBACK")
        time.sleep(0.5)
        self.assertEqual(out.count("PUBACK"), 1)
        self.assertIn("casa-jaro.announcements", [line for line in out.lines if "PUBACK" in line][0])

    def test_R6_replaced_subscriber_stops_instead_of_taking_the_id_back(self):
        args = ("-m", "pubsub.subscriber", "--id", "tenant-juan", "--state-dir", str(self.workdir / "subs"))
        old, old_out = self.spawn_cli(*args, "--topics", TOPIC)
        old_out.wait_for("SUBSCRIBED")
        new, new_out = self.spawn_cli(*args)
        new_out.wait_for("CONNECTED")
        try:
            old.wait(timeout=6)
        except subprocess.TimeoutExpired:
            self.fail("the replaced subscriber kept reconnecting")
        self.assertIsNone(new.poll())
        self.assertEqual(new_out.count("CONNECTED"), 1)


class LargeReplyTests(BrokerTestCase):
    broker_args = ("--quiet", "--heartbeat-timeout", "10")

    def setUp(self) -> None:
        super().setUp()
        self.broker.kill()
        store = Store(self.broker.db)
        with store._transaction() as db:  # one transaction: 540 separate ones would make the test slow
            db.executemany("INSERT INTO subscriptions (topic, client_id, created_at) VALUES (?, 'many', 0)",
                           [(f"x{i}" + "a" * 120,) for i in range(540)])
        store.close()
        self.broker.start()

    def test_R7a_client_with_very_many_subscriptions_can_still_connect(self):
        client = self.client("many", SUB)
        self.assertTrue(client.connack["resumed"])
        self.assertTrue(client.connack["truncated"])
        self.assertTrue(client.connack["subscriptions"])

    def test_R7b_status_still_answers_with_very_many_subscriptions(self):
        pub = self.publisher()
        pub.send({"type": protocol.STATUS})
        reply = pub.recv()
        self.assertEqual(reply["type"], protocol.STATUS_REPLY)
        self.assertEqual(reply["clients"][0]["client_id"], "many")
        self.assertTrue(reply["truncated"])


class InputValidationTests(BrokerTestCase):
    broker_args = ("--quiet", "--heartbeat-timeout", "10")

    def assert_error_and_connection_kept(self, role: str, frame: bytes) -> None:
        client = self.client("probe-client", role)
        client.send_raw(frame)
        try:
            self.assertEqual(client.expect(protocol.ERROR)["type"], protocol.ERROR)
            client.send({"type": protocol.PING})
            client.expect(protocol.PONG)
        except (EOFError, ConnectionError) as exc:
            self.fail(f"connection was dropped instead of answered with ERROR ({exc})")

    def test_R8a_topic_with_trailing_newline_is_refused(self):
        client = self.client("tenant-juan", SUB)
        client.send({"type": protocol.SUBSCRIBE, "topic": TOPIC + "\n"})
        reply = client.recv()
        self.assertEqual(reply["type"], protocol.ERROR)

    def test_R8b_client_id_with_trailing_newline_is_refused(self):
        raw = RawClient(self.broker.port, "x", SUB, connect=False)
        self._clients.append(raw)
        raw.send({"type": protocol.CONNECT, "client_id": "tenant-juan\n", "role": SUB})
        self.assertEqual(raw.recv()["type"], protocol.ERROR)

    def test_R9a_lone_surrogate_in_payload(self):
        self.assert_error_and_connection_kept(PUB, publish_frame('"\\ud800"'))

    def test_R9b_lone_surrogate_in_message_id(self):
        self.assert_error_and_connection_kept(PUB, publish_frame("{}", msg_id="\\ud800"))

    def test_R9c_extremely_nested_json(self):
        self.assert_error_and_connection_kept(PUB, b'{"type":"PING","x":' + b"[" * 30000 + b"]" * 30000 + b"}\n")

    def test_R9d_integer_too_long_to_parse(self):
        self.assert_error_and_connection_kept(PUB, b'{"type":"PING","x":' + b"9" * 5000 + b"}\n")

    def test_R9e_ack_sequence_number_out_of_range(self):
        self.assert_error_and_connection_kept(SUB, json.dumps({"type": "ACK", "seq": 2 ** 70}).encode() + b"\n")

    def test_R9f_non_finite_number_in_payload(self):
        self.assert_error_and_connection_kept(PUB, publish_frame('{"amount":1e400}'))

    def test_R9g_invalid_value_too_long_to_echo_back(self):
        # each DEL character is 1 byte on the way in and 5 bytes ("\\x7f") when echoed back in the error reason
        frame = json.dumps({"type": "SUBSCRIBE", "topic": "\x7f" * 30000}, ensure_ascii=False).encode() + b"\n"
        self.assert_error_and_connection_kept(SUB, frame)


if __name__ == "__main__":
    unittest.main()
