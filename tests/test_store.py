"""Unit tests for the durable store (persistent queues and durable subscriptions)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pubsub.broker.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "broker.db"
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_publish_routes_only_to_subscribers_of_the_topic(self):
        self.store.add_subscription("juan", "casa-jaro.announcements")
        self.store.add_subscription("jerome", "molo-hub.announcements")
        result = self.store.publish("manager", "m1", "casa-jaro.announcements", {"title": "x"})
        self.assertEqual(result.subscribers, ["juan"])
        self.assertEqual(self.store.pending_count("juan"), 1)
        self.assertEqual(self.store.pending_count("jerome"), 0)

    def test_subscription_is_idempotent(self):
        self.assertTrue(self.store.add_subscription("juan", "t"))
        self.assertFalse(self.store.add_subscription("juan", "t"))
        self.assertEqual(self.store.subscriptions_for("juan"), ["t"])

    def test_duplicate_publish_is_detected_and_not_requeued(self):
        self.store.add_subscription("juan", "t")
        first = self.store.publish("manager", "m1", "t", {"n": 1})
        again = self.store.publish("manager", "m1", "t", {"n": 1})
        self.assertFalse(first.duplicate)
        self.assertTrue(again.duplicate)
        self.assertEqual(first.seq, again.seq)
        self.assertEqual(self.store.pending_count("juan"), 1)

    def test_same_msg_id_from_different_publishers_is_not_a_duplicate(self):
        a = self.store.publish("manager", "m1", "t", {})
        b = self.store.publish("maintenance", "m1", "t", {})
        self.assertNotEqual(a.seq, b.seq)
        self.assertFalse(b.duplicate)

    def test_pending_is_fifo_and_ack_removes(self):
        self.store.add_subscription("juan", "t")
        seqs = [self.store.publish("p", f"m{i}", "t", {"i": i}).seq for i in range(5)]
        self.assertEqual([m["seq"] for m in self.store.pending("juan", 10)], seqs)
        self.assertTrue(self.store.ack("juan", seqs[0]))
        self.assertFalse(self.store.ack("juan", seqs[0]))  # second ACK is harmless
        self.assertEqual([m["seq"] for m in self.store.pending("juan", 10)], seqs[1:])
        self.assertEqual(len(self.store.pending("juan", 2)), 2)  # the window limit is respected

    def test_mark_sent_and_reset_in_flight(self):
        self.store.add_subscription("juan", "t")
        seq = self.store.publish("p", "m1", "t", {}).seq
        self.store.mark_sent("juan", seq)
        item = self.store.pending("juan", 1)[0]
        self.assertEqual(item["attempts"], 1)
        self.assertIsNotNone(item["last_sent"])
        self.assertEqual(self.store.reset_in_flight("juan"), 1)
        self.assertIsNone(self.store.pending("juan", 1)[0]["last_sent"])

    def test_unsubscribe_keeps_already_queued_messages(self):
        self.store.add_subscription("juan", "t")
        self.store.publish("p", "m1", "t", {})
        self.assertTrue(self.store.remove_subscription("juan", "t"))
        self.store.publish("p", "m2", "t", {})
        self.assertEqual(self.store.pending_count("juan"), 1)  # m1 is still owed, m2 never was

    def test_everything_survives_reopening_the_file(self):
        """Simulates a broker restart: a new Store on the same file sees the same queues."""
        self.store.add_subscription("juan", "t")
        seq = self.store.publish("p", "m1", "t", {"title": "Fire inspection"}).seq
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.subscriptions_for("juan"), ["t"])
        pending = self.store.pending("juan", 10)
        self.assertEqual([m["seq"] for m in pending], [seq])
        self.assertEqual(pending[0]["payload"], {"title": "Fire inspection"})
        self.assertTrue(self.store.publish("p", "m1", "t", {}).duplicate)  # duplicate detection survives too

    def test_queue_report(self):
        self.store.add_subscription("juan", "a")
        self.store.add_subscription("juan", "b")
        self.store.publish("p", "m1", "a", {})
        report = self.store.queue_report()
        self.assertEqual(report["juan"], {"topics": ["a", "b"], "pending": 1})


if __name__ == "__main__":
    unittest.main()
