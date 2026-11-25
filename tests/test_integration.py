"""
Functional (F) and integration (I) tests against a real broker process.

Test IDs match the Fault Test Report and the design document.
"""
from __future__ import annotations

import json
import time
import unittest
import uuid

from pubsub.common import config, protocol
from tests.helpers import BrokerTestCase, RawClient, queue_depth

SUB, PUB = protocol.SUBSCRIBER, protocol.PUBLISHER


class RoutingTests(BrokerTestCase):
    """Topic-based routing (Coulouris §6.3)."""

    broker_args = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "10")

    def test_F2_subscriber_receives_message_on_its_topic(self):
        juan = self.client("tenant-juan", SUB, "casa-jaro.announcements")
        puback = self.publisher("manager-office").publish("casa-jaro.announcements", "Fire inspection")
        self.assertEqual(puback["routed_to"], 1)
        [msg] = juan.deliveries(1)
        self.assertEqual(msg["topic"], "casa-jaro.announcements")
        self.assertEqual(msg["payload"]["title"], "Fire inspection")
        self.assertEqual(msg["publisher"], "manager-office")
        self.assertFalse(msg["redelivery"])
        self.assertTrue(juan.no_delivery(0.5), "message delivered more than once")

    def test_F3_message_on_other_topic_is_not_delivered(self):
        jerome = self.client("tenant-jerome", SUB, "molo-hub.announcements")
        puback = self.publisher().publish("casa-jaro.announcements", "Casa Jaro only")
        self.assertEqual(puback["routed_to"], 0)
        self.assertTrue(jerome.no_delivery(1.0), "topic filtering failed: got a message for another topic")

    def test_F4_fan_out_to_every_subscriber_of_a_topic(self):
        subs = [self.client(f"tenant-{i}", SUB, "casa-jaro.announcements") for i in range(3)]
        self.assertEqual(self.publisher().publish("casa-jaro.announcements", "To all")["routed_to"], 3)
        for sub in subs:
            self.assertEqual(sub.deliveries(1)[0]["payload"]["title"], "To all")

    def test_F5_one_subscriber_many_topics(self):
        juan = self.client("tenant-juan", SUB, "casa-jaro.announcements", "casa-jaro.maintenance")
        pub = self.publisher()
        pub.publish("casa-jaro.announcements", "notice")
        pub.publish("casa-jaro.maintenance", "repair")
        pub.publish("molo-hub.maintenance", "not for juan")
        got = juan.deliveries(2)
        self.assertEqual([m["topic"] for m in got], ["casa-jaro.announcements", "casa-jaro.maintenance"])
        self.assertTrue(juan.no_delivery(0.8))

    def test_F6_unsubscribe_stops_delivery(self):
        juan = self.client("tenant-juan", SUB, "casa-jaro.announcements")
        juan.send({"type": protocol.UNSUBSCRIBE, "topic": "casa-jaro.announcements"})
        self.assertTrue(juan.expect(protocol.UNSUBACK)["removed"])
        self.assertEqual(self.publisher().publish("casa-jaro.announcements", "after unsubscribe")["routed_to"], 0)
        self.assertTrue(juan.no_delivery(1.0))

    def test_F7_two_publishers_same_topic_each_in_order(self):
        sub = self.client("caretaker", SUB, "casa-jaro.maintenance")
        office, desk = self.publisher("manager-office"), self.publisher("maintenance-desk")
        for i in range(5):
            office.publish("casa-jaro.maintenance", f"office-{i}")
            desk.publish("casa-jaro.maintenance", f"desk-{i}")
        got = sub.deliveries(10)
        office_titles = [m["payload"]["title"] for m in got if m["publisher"] == "manager-office"]
        desk_titles = [m["payload"]["title"] for m in got if m["publisher"] == "maintenance-desk"]
        self.assertEqual(office_titles, [f"office-{i}" for i in range(5)])
        self.assertEqual(desk_titles, [f"desk-{i}" for i in range(5)])
        seqs = [m["seq"] for m in got]
        self.assertEqual(seqs, sorted(seqs), "subscriber must receive messages in broker sequence order")

    def test_F8_puback_is_sent_only_after_the_message_is_stored(self):
        """Kill the broker the instant the PUBACK arrives; after a restart the message must still be there."""
        juan = self.client("tenant-juan", SUB, "casa-jaro.announcements")
        juan.close()
        puback = self.publisher().publish("casa-jaro.announcements", "acknowledged means stored")
        self.broker.kill()
        self.broker.start()
        juan = self.client("tenant-juan", SUB)
        self.assertEqual(juan.connack["queued"], 1)
        [msg] = juan.deliveries(1)
        self.assertEqual(msg["seq"], puback["seq"])

    def test_roles_are_enforced(self):
        sub = self.client("tenant-juan", SUB)
        sub.send({"type": protocol.PUBLISH, "msg_id": "x", "topic": "t", "payload": {}})
        self.assertEqual(sub.expect(protocol.ERROR)["code"], protocol.ROLE)
        pub = self.publisher()
        pub.send({"type": protocol.SUBSCRIBE, "topic": "t"})
        self.assertEqual(pub.expect(protocol.ERROR)["code"], protocol.ROLE)

    def test_status_reports_queue_depth_and_presence(self):
        juan = self.client("tenant-juan", SUB, "casa-jaro.announcements")
        status = self.status()
        self.assertTrue(any(c["client_id"] == "tenant-juan" and c["online"] for c in status["clients"]))
        juan.close()
        self.wait_until(lambda: not any(c["online"] for c in self.status()["clients"]), message="juan offline")
        self.publisher().publish("casa-jaro.announcements", "queued")
        self.assertEqual(queue_depth(self.status(), "tenant-juan"), 1)


class ScenarioTests(BrokerTestCase):
    """I1 and I2: the DormCare scenario end to end, and a burst of traffic."""

    # A long ACK timeout, because I1 reads the subscribers one after another: on a busy machine the last one ACKs
    # late, and the broker would (correctly) re-send, which I1 would count as an extra message. X2b tests re-sending.
    broker_args = ("--quiet", "--ack-timeout", "30", "--heartbeat-timeout", "10")

    def test_I1_full_dormcare_scenario(self):
        scenario = json.loads(config.SCENARIO_FILE.read_text(encoding="utf-8"))
        subscribers = {s["id"]: self.client(s["id"], SUB, *s["topics"]) for s in scenario["subscribers"]}
        publishers = {p["id"]: self.publisher(p["id"]) for p in scenario["publishers"]}
        self.assertGreaterEqual(len(publishers), 2)
        self.assertGreaterEqual(len(subscribers), 3)

        for event in scenario["events"]:
            payload = {k: v for k, v in event.items() if k not in ("publisher", "topic")}
            publishers[event["publisher"]].publish(event["topic"], msg_id=uuid.uuid4().hex, **payload)

        for spec in scenario["subscribers"]:
            expected = [e["title"] for e in scenario["events"] if e["topic"] in spec["topics"]]
            got = subscribers[spec["id"]].deliveries(len(expected))
            self.assertEqual([m["payload"]["title"] for m in got], expected, spec["id"])
            self.assertTrue(subscribers[spec["id"]].no_delivery(0.3), f"{spec['id']} received extra messages")

    def test_I2_burst_of_500_messages_no_loss_no_corruption_in_order(self):
        sub = self.client("tenant-juan", SUB, "casa-jaro.announcements")
        pub = self.publisher()
        for batch in range(10):  # 10 x 50, pipelined: send 50 PUBLISH frames, then read 50 PUBACKs
            ids = [f"b{batch}-{i}" for i in range(50)]
            for msg_id in ids:
                pub.send({"type": protocol.PUBLISH, "msg_id": msg_id, "topic": "casa-jaro.announcements",
                          "payload": {"title": msg_id, "body": "₱" * 20}})
            for msg_id in ids:
                pub.expect(protocol.PUBACK, msg_id=msg_id, timeout=10)
        got = sub.deliveries(500, timeout=60)
        seqs = [m["seq"] for m in got]
        self.assertEqual(len(set(seqs)), 500)
        self.assertEqual(seqs, sorted(seqs))
        self.assertTrue(all(m["payload"]["body"] == "₱" * 20 for m in got))


if __name__ == "__main__":
    unittest.main()
