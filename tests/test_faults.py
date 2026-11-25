"""
Failure tests (X). These are the experiments recorded in the Fault Test Report.

Every "crash" is a hard kill (TerminateProcess on Windows): the process gets no chance to clean up
or say goodbye, which is the realistic case a distributed system has to survive.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from pubsub.common import protocol
from tests.helpers import BrokerTestCase, RawClient, is_online, queue_depth

SUB, PUB = protocol.SUBSCRIBER, protocol.PUBLISHER
TOPIC = "casa-jaro.announcements"


class SubscriberFailureTests(BrokerTestCase):
    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def test_X1_offline_subscriber_catches_up_in_order(self):
        juan = self.client("tenant-juan", SUB, TOPIC)
        juan.close()  # disconnect without saying goodbye
        self.wait_until(lambda: not is_online(self.status(), "tenant-juan"), message="broker to notice")
        pub = self.publisher()
        sent = [pub.publish(TOPIC, f"while offline {i}")["seq"] for i in range(3)]
        self.assertEqual(queue_depth(self.status(), "tenant-juan"), 3)

        juan = self.client("tenant-juan", SUB)  # same id, no SUBSCRIBE: the subscription is durable
        self.assertTrue(juan.connack["resumed"])
        self.assertEqual(juan.connack["queued"], 3)
        self.assertEqual(juan.connack["subscriptions"], [TOPIC])
        got = juan.deliveries(3)
        self.assertEqual([m["seq"] for m in got], sent)
        self.assertEqual([m["payload"]["title"] for m in got], [f"while offline {i}" for i in range(3)])
        self.wait_until(lambda: queue_depth(self.status(), "tenant-juan") == 0, message="queue to drain")

    def test_X1b_real_subscriber_process_killed_then_restarted(self):
        state = str(self.workdir / "subs")
        proc, out = self.spawn_cli("-m", "pubsub.subscriber", "--id", "tenant-juan", "--topics", TOPIC,
                                   "--state-dir", state)
        out.wait_for("SUBSCRIBED")
        proc.kill()  # hard crash of the subscriber process
        self.wait_until(lambda: not is_online(self.status(), "tenant-juan"), message="crash detection")
        pub = self.publisher("manager-office")
        for i in range(3):
            pub.publish(TOPIC, f"Notice {i}")

        _, out2 = self.spawn_cli("-m", "pubsub.subscriber", "--id", "tenant-juan", "--state-dir", state)
        out2.wait_for("3 queued message(s) waiting")
        lines = out2.wait_for("CATCH-UP", count=3)
        self.assertEqual(len(lines), 3)
        for i in range(3):
            out2.wait_for(f"Notice {i}")
        self.wait_until(lambda: queue_depth(self.status(), "tenant-juan") == 0, message="ACKs")

    def test_X2_crash_after_processing_but_before_ack_is_redelivered_and_deduplicated(self):
        state = str(self.workdir / "subs")
        proc, out = self.spawn_cli("-m", "pubsub.subscriber", "--id", "tenant-juan", "--topics", TOPIC,
                                   "--state-dir", state, "--crash-after", "1", "--no-reconnect")
        out.wait_for("SUBSCRIBED")
        self.publisher().publish(TOPIC, "Water interruption")
        out.wait_for("exiting abruptly before ACKing")
        self.assertEqual(proc.wait(timeout=10), 3)
        self.assertEqual(queue_depth(self.status(), "tenant-juan"), 1, "un-ACKed message must stay queued")

        _, out2 = self.spawn_cli("-m", "pubsub.subscriber", "--id", "tenant-juan", "--state-dir", state)
        out2.wait_for("DUPLICATE")  # the broker re-sent it (at-least-once) ...
        self.assertEqual(out2.count("Water interruption"), 0, "... and it was NOT processed a second time")
        self.wait_until(lambda: queue_depth(self.status(), "tenant-juan") == 0, message="re-ACK")

    def test_X2b_unacked_message_is_resent_after_the_ack_timeout(self):
        juan = self.client("tenant-juan", SUB, TOPIC)
        self.publisher().publish(TOPIC, "please ack me")
        first = juan.deliveries(1, ack=False)[0]
        self.assertFalse(first["redelivery"])
        again = juan.expect(protocol.DELIVER, timeout=5)  # ack-timeout is 1 s in this test
        self.assertEqual(again["seq"], first["seq"])
        self.assertTrue(again["redelivery"])
        self.assertGreaterEqual(again["attempt"], 2)
        juan.ack(again["seq"])
        self.wait_until(lambda: queue_depth(self.status(), "tenant-juan") == 0, message="ACK")

    def test_X6_silent_client_is_dropped_by_heartbeat_timeout(self):
        self.broker.restart("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "2")
        juan = self.client("tenant-juan", SUB, TOPIC)
        started = time.time()
        with self.assertRaises(EOFError):  # we never PING, so the broker must give up on us
            while time.time() - started < 10:
                juan.recv(timeout=1)
        self.assertLess(time.time() - started, 6)
        self.broker.output.wait_for("no heartbeat")
        self.publisher().publish(TOPIC, "queued for the silent client")
        self.assertEqual(queue_depth(self.status(), "tenant-juan"), 1)

    def test_X7_same_client_id_twice_newest_connection_wins(self):
        old = self.client("tenant-juan", SUB, TOPIC)
        new = self.client("tenant-juan", SUB)
        self.assertEqual(old.expect(protocol.ERROR)["code"], protocol.REPLACED)
        with self.assertRaises(EOFError):
            while True:
                old.recv(timeout=3)
        self.publisher().publish(TOPIC, "for the new connection")
        self.assertEqual(new.deliveries(1)[0]["payload"]["title"], "for the new connection")

    def test_X10_one_thousand_messages_queued_while_offline(self):
        juan = self.client("tenant-juan", SUB, TOPIC)
        juan.close()
        self.wait_until(lambda: not is_online(self.status(), "tenant-juan"), message="offline")
        pub = self.publisher()
        for batch in range(20):
            ids = [f"x{batch}-{i}" for i in range(50)]
            for msg_id in ids:
                pub.send({"type": protocol.PUBLISH, "msg_id": msg_id, "topic": TOPIC, "payload": {"title": msg_id}})
            for msg_id in ids:
                pub.expect(protocol.PUBACK, msg_id=msg_id, timeout=10)
        juan = self.client("tenant-juan", SUB)
        self.assertEqual(juan.connack["queued"], 1000)
        got = juan.deliveries(1000, timeout=120)
        seqs = [m["seq"] for m in got]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 1000)


class BrokerFailureTests(BrokerTestCase):
    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def test_X3_broker_crash_keeps_subscriptions_and_queued_messages(self):
        jerome = self.client("tenant-jerome", SUB, "molo-hub.announcements")
        jerome.close()
        self.wait_until(lambda: not is_online(self.status(), "tenant-jerome"), message="offline")
        pub = self.publisher()
        sent = [pub.publish("molo-hub.announcements", f"before crash {i}")["seq"] for i in range(2)]

        self.broker.kill()  # hard crash of the broker with 2 messages queued
        self.broker.start()
        self.broker.output.wait_for("1 durable subscription(s)")
        self.broker.output.wait_for("2 message(s) waiting")

        jerome = self.client("tenant-jerome", SUB)
        self.assertEqual(jerome.connack["subscriptions"], ["molo-hub.announcements"])
        self.assertEqual([m["seq"] for m in jerome.deliveries(2)], sent)
        # New messages after the restart are routed using the recovered subscription.
        self.publisher("pub2").publish("molo-hub.announcements", "after restart")
        self.assertEqual(jerome.deliveries(1)[0]["payload"]["title"], "after restart")

    def test_X3b_subscriber_reconnects_by_itself_after_broker_restart(self):
        _, out = self.spawn_cli("-m", "pubsub.subscriber", "--id", "tenant-juan", "--topics", TOPIC,
                                "--state-dir", str(self.workdir / "subs"))
        out.wait_for("SUBSCRIBED")
        self.broker.restart()
        out.wait_for("CONNECTED", count=2, timeout=20)
        self.publisher().publish(TOPIC, "after the broker came back")
        out.wait_for("after the broker came back")

    def test_X4_lost_puback_publisher_retries_and_broker_deduplicates(self):
        self.broker.restart("--ack-timeout", "1", "--heartbeat-timeout", "10", "--fault-skip-puback", "1")
        juan = self.client("tenant-juan", SUB, TOPIC)
        proc, out = self.spawn_cli("-m", "pubsub.publisher", "--id", "manager-office", "--topic", TOPIC,
                                   "--title", "Rent due Oct 15", "--puback-timeout", "1")
        self.assertEqual(proc.wait(timeout=30), 0, out.text())
        self.assertIn("RETRY", out.text())
        self.assertIn("duplicate detected", out.text())
        self.broker.output.wait_for("PUBACK is being dropped")
        self.broker.output.wait_for("not routed again")
        # Juan may see the SAME message more than once (the broker re-sends it while this test is not yet
        # ACKing; that is at-least-once working). What must NOT happen is a second, separately routed copy:
        # every DELIVER must carry one and the same sequence number.
        seqs = set()
        deadline = time.time() + 2.5
        while time.time() < deadline:
            frame = juan.recv(timeout=0.5)
            if frame and frame["type"] == protocol.DELIVER:
                self.assertEqual(frame["payload"]["title"], "Rent due Oct 15")
                seqs.add(frame["seq"])
                juan.ack(frame["seq"])
        self.assertEqual(len(seqs), 1, f"the retried PUBLISH was routed twice: {seqs}")


class LostOutputTests(unittest.TestCase):
    """X11: the broker's log output goes nowhere (e.g. the program that started it and read its output has
    exited). Logging must never break message handling."""

    def test_X11_broker_keeps_working_after_its_output_stream_is_closed(self):
        import os
        import subprocess
        import sys
        import tempfile
        from tests.helpers import ROOT, free_port, wait_for_port
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            port = free_port()
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}
            proc = subprocess.Popen([sys.executable, "-m", "pubsub.broker", "--port", str(port), "--db",
                                     str(Path(tmp) / "broker.db")], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                wait_for_port(port)
                proc.stdout.close()  # nobody is reading the broker's output any more
                time.sleep(0.3)
                for attempt in range(2):  # every log line now fails to write; the broker must not care
                    sub = RawClient(port, f"tenant-{attempt}", SUB)
                    sub.subscribe(TOPIC)
                    pub = RawClient(port, f"pub-{attempt}", PUB)
                    seq = pub.publish(TOPIC, "still delivered")["seq"]
                    self.assertEqual(sub.deliveries(1)[0]["seq"], seq)
                    self.assertIn("clients", pub.status())
                    sub.close()
                    pub.close()
            finally:
                proc.kill()
                proc.wait(timeout=10)


class ProtocolFailureTests(BrokerTestCase):
    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def test_X5_malformed_frames_get_errors_and_the_broker_keeps_serving(self):
        bad = self.client("tenant-juan", SUB)
        bad.send_raw(b"this is not json\n")
        self.assertEqual(bad.expect(protocol.ERROR)["code"], protocol.BAD_FRAME)
        bad.send({"type": "TELEPORT"})
        self.assertEqual(bad.expect(protocol.ERROR)["code"], protocol.UNKNOWN_TYPE)
        bad.send({"type": protocol.SUBSCRIBE})
        self.assertEqual(bad.expect(protocol.ERROR)["code"], protocol.BAD_REQUEST)
        bad.send({"type": protocol.SUBSCRIBE, "topic": "Not A Topic"})
        self.assertEqual(bad.expect(protocol.ERROR)["code"], protocol.BAD_REQUEST)
        bad.send({"type": protocol.PING})
        bad.expect(protocol.PONG)  # same connection still works after all those errors

        bad.send_raw(b"x" * 70_000)  # one frame larger than the 64 KiB limit: connection is closed
        with self.assertRaises(EOFError):
            while True:
                frame = bad.recv(timeout=5)
                if frame is None:
                    self.fail("broker did not close an oversized connection")
        self.broker.output.wait_for("FRAME_TOO_LARGE")

        juan = self.client("tenant-juan2", SUB, TOPIC)  # other clients are unaffected
        self.publisher().publish(TOPIC, "still working")
        self.assertEqual(juan.deliveries(1)[0]["payload"]["title"], "still working")

    def test_first_frame_must_be_connect(self):
        raw = RawClient(self.broker.port, "x", SUB, connect=False)
        self._clients.append(raw)
        raw.send({"type": protocol.PING})
        self.assertEqual(raw.expect(protocol.ERROR)["code"], protocol.NOT_CONNECTED)

    def test_X8_publish_to_topic_without_subscribers(self):
        puback = self.publisher().publish("nobody.listens", "into the void")
        self.assertEqual(puback["routed_to"], 0)
        self.broker.output.wait_for("nobody subscribes to this topic")

    def test_X9_new_subscriber_gets_no_history(self):
        self.publisher().publish(TOPIC, "published before anyone subscribed")
        late = self.client("tenant-late", SUB, TOPIC)
        self.assertTrue(late.no_delivery(1.0))


if __name__ == "__main__":
    unittest.main()
