"""
The web dashboard, driven through its HTTP API exactly as the browser drives it.

The dashboard starts the real broker and subscriber processes, so these tests also check that what the
browser is told (server-sent events and /api/state) matches what really happened in the system.
"""
from __future__ import annotations

import http.client
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import free_port, spawn, wait_for_port


class EventStream:
    """Reads server-sent events (e.g. the dashboard's GET /events) on a background thread."""

    def __init__(self, port: int, path: str = "/events"):
        self.events: queue.Queue = queue.Queue()
        self.seen: list[dict] = []
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        self.conn.request("GET", path)
        self.response = self.conn.getresponse()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            for raw in self.response:
                line = raw.decode("utf-8").strip()
                if line.startswith("data: "):
                    self.events.put(json.loads(line[6:]))
        except (OSError, ValueError):
            pass

    def wait_for(self, predicate, timeout: float = 15.0) -> dict:
        """Return the first event (already received or still to come) that matches. Events from different
        processes can arrive in any order, so nothing is thrown away."""
        for event in self.seen:
            if predicate(event):
                return event
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise AssertionError("expected dashboard event did not arrive")
            try:
                event = self.events.get(timeout=remaining)
            except queue.Empty:
                continue
            self.seen.append(event)
            if predicate(event):
                return event

    def close(self) -> None:
        self.conn.close()


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.port, self.broker_port = free_port(), free_port()
        self.proc, self.output = spawn(["-m", "pubsub.dashboard", "--port", str(self.port), "--broker-port",
                                        str(self.broker_port), "--data-dir", str(Path(self._tmp.name)), "--no-browser"])
        wait_for_port(self.port)
        self.stream = EventStream(self.port)

    def tearDown(self) -> None:
        try:
            self.post("/api/reset")  # stops the broker and subscribers the dashboard started
        finally:
            self.stream.close()
            self.proc.kill()
            self.proc.wait(timeout=10)
            self.output.close()
            self._tmp.cleanup()

    def post(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", path, body=json.dumps(body or {}), headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def get(self, path: str) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path)
        data = json.loads(conn.getresponse().read())
        conn.close()
        return data

    def wait_state(self, predicate, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.get("/api/state")
            if predicate(state):
                return state
            time.sleep(0.3)
        self.fail(f"dashboard state never matched; last: {state}")

    @staticmethod
    def client(state: dict, client_id: str) -> dict:
        return next((c for c in (state.get("status") or {}).get("clients", []) if c["client_id"] == client_id), {})

    def test_page_and_scenario_are_served(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/")
        page = conn.getresponse().read().decode("utf-8")
        conn.close()
        self.assertIn("DormCare Live Alerts", page)
        scenario = self.get("/api/scenario")
        self.assertEqual({p["id"] for p in scenario["publishers"]}, {"manager-office", "maintenance-desk"})
        self.assertGreaterEqual(len(scenario["subscribers"]), 3)
        self.stream.wait_for(lambda e: e["type"] == "snapshot")

    def test_demo_filtering_crash_and_catch_up_through_the_dashboard(self):
        status, _ = self.post("/api/demo/start")
        self.assertEqual(status, 200)
        self.wait_state(lambda s: sum(c["online"] for c in (s.get("status") or {}).get("clients", [])) >= 4)

        # Topic filtering: the Casa Jaro fire inspection notice (scenario event 0) reaches only tenant-juan.
        status, ack = self.post("/api/publish", {"publisher": "manager-office", "event": 0})
        self.assertEqual((status, ack["routed_to"]), (200, 1))
        msg = self.stream.wait_for(lambda e: e["type"] == "message" and e["subscriber"] == "tenant-juan")
        self.assertEqual((msg["label"], msg["seq"]), ("LIVE", ack["seq"]))
        title = self.stream.wait_for(lambda e: e["type"] == "message_line" and e["key"] == msg["key"])
        self.assertIn("Fire safety inspection", title["text"])
        route = self.stream.wait_for(lambda e: e["type"] == "route" and e["seq"] == ack["seq"])
        self.assertEqual(route["targets"], [{"id": "tenant-juan", "online": True}])

        # Crash Juan (a real hard kill), publish while he is offline, then start him again: catch-up.
        self.assertTrue(self.post("/api/subscribers/tenant-juan/crash")[1]["crashed"])
        self.wait_state(lambda s: not self.client(s, "tenant-juan").get("online", True))
        _, ack2 = self.post("/api/publish", {"publisher": "manager-office", "topic": "casa-jaro.announcements",
                                             "title": "Generator test on Sunday", "body": "From 9 to 10 AM."})
        self.wait_state(lambda s: self.client(s, "tenant-juan").get("pending") == 1)
        self.post("/api/subscribers/tenant-juan/start")
        caught = self.stream.wait_for(lambda e: e["type"] == "message" and e["subscriber"] == "tenant-juan"
                                      and e["seq"] == ack2["seq"])
        self.assertEqual(caught["label"], "CATCH-UP")
        self.wait_state(lambda s: self.client(s, "tenant-juan").get("pending") == 0
                        and self.client(s, "tenant-juan").get("online"))

    def test_clear_errors_for_bad_requests(self):
        status, data = self.post("/api/publish", {"publisher": "manager-office", "topic": "casa-jaro.announcements",
                                                  "title": "Broker is off"})
        self.assertEqual(status, 409)
        self.assertIn("broker is not running", data["error"])
        status, data = self.post("/api/publish", {"publisher": "manager-office", "topic": "casa-jaro.announcements",
                                                  "title": "   "})
        self.assertEqual((status, data["error"]), (409, "Write a title for the message."))
        self.assertEqual(self.post("/api/subscribers/nobody/start")[0], 409)
        self.assertEqual(self.post("/api/no-such-endpoint")[0], 404)


if __name__ == "__main__":
    unittest.main()
