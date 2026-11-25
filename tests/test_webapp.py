"""
The DormCare website running on the broker (pubsub.webapp): its gateway API, driven the way app.js drives it.

A real broker runs as its own process; the website server is started with --no-broker and connects to it.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from pubsub.common import protocol
from tests.helpers import BrokerProcess, RawClient, free_port, queue_depth, spawn, wait_for_port
from tests.test_dashboard import EventStream

JUAN = "web-tenant-u-juan"
JUAN_TOPICS = ["tenant.u-juan", "casa-jaro.announcements"]


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.broker = BrokerProcess(Path(self._tmp.name), "--quiet").start()
        self.port = free_port()
        self.proc, self.output = spawn(["-m", "pubsub.webapp", "--port", str(self.port), "--broker-port",
                                        str(self.broker.port), "--no-broker", "--no-browser"])
        wait_for_port(self.port)
        self.streams: list[EventStream] = []
        self.clients: list[RawClient] = []

    def tearDown(self) -> None:
        for s in self.streams:
            s.close()
        for c in self.clients:
            c.close()
        self.proc.kill()
        self.proc.wait(timeout=10)
        self.output.close()
        self.broker.kill()
        self._tmp.cleanup()

    # --- helpers --------------------------------------------------------------------------------------
    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        status, data = self.request("POST", path, body)
        return status, json.loads(data or b"{}")

    def stream(self, client: str, topics: list[str]) -> EventStream:
        s = EventStream(self.port, f"/broker/stream?client={client}&topics={','.join(topics)}")
        self.streams.append(s)
        return s

    def status(self) -> dict:
        probe = RawClient(self.broker.port, "probe", protocol.PUBLISHER)
        try:
            return probe.status()
        finally:
            probe.close()

    def wait_depth(self, client: str, depth: int, timeout: float = 10) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if queue_depth(self.status(), client) == depth:
                return
            time.sleep(0.2)
        self.fail(f"{client} queue never reached {depth}")

    def provision(self, *items: tuple[str, list[str]]) -> None:
        status, _ = self.post("/broker/provision", {"subscriptions": [{"client": c, "topics": t} for c, t in items]})
        self.assertEqual(status, 200)
        deadline = time.time() + 10
        while time.time() < deadline:  # registration happens on a short background session
            known = {c["client_id"]: set(c["topics"]) for c in self.status()["clients"]}
            if all(set(t) <= known.get(c, set()) for c, t in items):
                return
            time.sleep(0.2)
        self.fail("subscriptions were not registered at the broker")

    # --- tests ----------------------------------------------------------------------------------------
    def test_serves_the_dormcare_site_and_nothing_else(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"UPa", page)
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/styles.css")[0], 200)
        for public in ("/marketplace.js", "/marketplace.css", "/assets/marketplace/base.css",
                       "/assets/marketplace/manrope.ttf", "/assets/marketplace/living-room.jpg",
                       "/assets/brand/upa-wordmark.png", "/assets/brand/upa-app-icon.png"):
            self.assertEqual(self.request("GET", public)[0], 200, public)
        self.assertEqual(self.request("GET", "/favicon.ico")[0], 204)
        for private in ("/data/broker.db", "/pubsub/broker/store.py", "/README.md", "/../app.js",
                        "/PRODUCT.md", "/assets/marketplace/../../app.js"):
            self.assertEqual(self.request("GET", private)[0], 404, private)
        status, info = self.request("GET", "/broker/info")
        self.assertEqual(json.loads(info), {"ok": True, "broker": True, "broker_port": self.broker.port})

    def test_website_announcement_reaches_web_and_command_line_subscribers(self):
        self.provision((JUAN, JUAN_TOPICS))
        cli = RawClient(self.broker.port, "tenant-juan", protocol.SUBSCRIBER)  # the command-line subscriber
        self.clients.append(cli)
        cli.subscribe("casa-jaro.announcements")

        payload = {"kind": "announcement", "id": "a1", "prop": "p1", "title": "Generator test on Sunday",
                   "body": "9 to 10 AM.", "text": "New announcement: Generator test on Sunday", "to": "notices"}
        status, ack = self.post("/broker/publish", {"role": "manager", "topic": "casa-jaro.announcements",
                                                    "payload": payload})
        self.assertEqual((status, ack["routed_to"]), (200, 2))  # the website's Juan and the command-line Juan
        self.assertEqual(cli.deliveries(1)[0]["payload"]["title"], "Generator test on Sunday")

        # Juan's view opens later: the message waited for him at the broker (catch-up).
        events = self.stream(JUAN, JUAN_TOPICS)
        hello = events.wait_for(lambda e: e["type"] == "hello")
        events.wait_for(lambda e: e["type"] == "ready")
        msg = events.wait_for(lambda e: e["type"] == "deliver")
        self.assertEqual((msg["seq"], msg["payload"]["id"]), (ack["seq"], "a1"))
        self.assertLess(msg["published_at"], hello["attached_at"])  # app.js labels it "waited at the broker"
        self.assertEqual(queue_depth(self.status(), JUAN), 1)        # not removed until the browser ACKs
        self.assertTrue(self.post("/broker/ack", {"client": JUAN, "seq": msg["seq"]})[1]["ok"])
        self.wait_depth(JUAN, 0)

    def test_personal_alerts_reach_only_their_tenant(self):
        self.provision((JUAN, JUAN_TOPICS), ("web-tenant-u-ana", ["tenant.u-ana"]))
        status, ack = self.post("/broker/publish", {"role": "manager", "topic": "tenant.u-juan",
                                                    "payload": {"kind": "notification", "text": "Payment verified"}})
        self.assertEqual((status, ack["routed_to"]), (200, 1))
        clients = self.status()
        self.assertEqual((queue_depth(clients, JUAN), queue_depth(clients, "web-tenant-u-ana")), (1, 0))

    def test_chat_message_reaches_both_people_in_the_conversation_and_nobody_else(self):
        self.provision(("web-manager-m1", ["manager.m1", "chat.u-juan", "chat.u-ana"]),
                       (JUAN, ["tenant.u-juan", "chat.u-juan"]),
                       ("web-tenant-u-ana", ["tenant.u-ana", "chat.u-ana"]))
        status, ack = self.post("/broker/publish", {"role": "tenant", "topic": "chat.u-juan", "payload": {
            "kind": "chat", "id": "c1", "thread": "u-juan", "from": "tenant", "name": "Juan dela Cruz",
            "text": "Is the Wi-Fi down on the 2nd floor?"}})
        # The manager gets it, and so does Juan's own subscription (that copy confirms "sent through the broker").
        self.assertEqual((status, ack["routed_to"]), (200, 2))
        clients = self.status()
        depths = {c: queue_depth(clients, c) for c in ("web-manager-m1", JUAN, "web-tenant-u-ana")}
        self.assertEqual(depths, {"web-manager-m1": 1, JUAN: 1, "web-tenant-u-ana": 0})
        events = self.stream("web-manager-m1", ["manager.m1", "chat.u-juan", "chat.u-ana"])
        msg = events.wait_for(lambda e: e["type"] == "deliver")
        self.assertEqual((msg["topic"], msg["payload"]["text"]), ("chat.u-juan", "Is the Wi-Fi down on the 2nd floor?"))

    def test_live_delivery_while_the_view_is_open(self):
        events = self.stream("web-manager-m1", ["manager.m1"])
        hello = events.wait_for(lambda e: e["type"] == "hello")
        events.wait_for(lambda e: e["type"] == "ready")
        _, ack = self.post("/broker/publish", {"role": "tenant", "topic": "manager.m1", "payload": {
            "kind": "notification", "text": "New ticket from Juan dela Cruz: Shower drain clogged", "to": "maintenance"}})
        msg = events.wait_for(lambda e: e["type"] == "deliver" and e["seq"] == ack["seq"])
        self.assertGreater(msg["published_at"], hello["attached_at"])  # app.js labels it "live"
        self.assertEqual(msg["payload"]["to"], "maintenance")

    def test_bad_requests_get_clear_errors(self):
        status, data = self.post("/broker/publish", {"role": "landlord", "topic": "manager.m1", "payload": {}})
        self.assertEqual(status, 409)
        self.assertIn("role must be one of", data["error"])
        status, data = self.post("/broker/publish", {"role": "manager", "topic": "Not A Topic", "payload": {}})
        self.assertEqual(status, 409)
        self.assertIn("invalid topic", data["error"])
        self.assertEqual(self.post("/broker/provision", {"subscriptions": [{"client": "bad id!", "topics": []}]})[0], 409)
        self.assertEqual(self.request("GET", "/broker/stream?client=bad%20id&topics=x")[0], 400)
        self.assertEqual(self.post("/broker/nothing", {})[0], 404)


class BrokerHealthTests(unittest.TestCase):
    """Regressions for a real incident: a broker left running after its website window was closed could no
    longer serve clients, yet the website reported it as reachable."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.port, self.broker_port = free_port(), free_port()
        self.procs = []

    def tearDown(self) -> None:
        for proc, out in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            out.close()
        self._tmp.cleanup()

    def start_website(self, *extra: str):
        proc, out = spawn(["-m", "pubsub.webapp", "--port", str(self.port), "--broker-port", str(self.broker_port),
                           "--data-dir", self._tmp.name, "--no-browser", *extra])
        self.procs.append((proc, out))
        wait_for_port(self.port, timeout=20)
        return proc, out

    @staticmethod
    def port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            return False

    @unittest.skipUnless(os.name == "nt", "child processes are tied to their parent with a Windows job object")
    def test_broker_started_by_the_website_ends_when_the_website_is_closed(self):
        proc, _ = self.start_website()
        wait_for_port(self.broker_port, timeout=20)  # the website started its own broker
        proc.kill()  # like closing the website's window: no clean-up code runs
        deadline = time.time() + 10
        while time.time() < deadline and self.port_open(self.broker_port):
            time.sleep(0.2)
        self.assertFalse(self.port_open(self.broker_port), "the broker kept running after its website was closed")

    def test_a_listening_but_broken_broker_is_reported_as_not_reachable(self):
        fake = socket.socket()  # accepts connections, then hangs up: the port is open, but nothing serves clients
        fake.bind(("127.0.0.1", self.broker_port))
        fake.listen()
        stop = threading.Event()

        def hang_up():
            fake.settimeout(0.2)
            while not stop.is_set():
                try:
                    conn, _ = fake.accept()
                    conn.close()
                except OSError:
                    pass
        threading.Thread(target=hang_up, daemon=True).start()
        try:
            _, out = self.start_website("--no-broker")
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("GET", "/broker/info")
            info = json.loads(conn.getresponse().read())
            conn.close()
            self.assertFalse(info["broker"])
            out.wait_for("does not answer like a healthy broker")
        finally:
            stop.set()
            fake.close()


if __name__ == "__main__":
    unittest.main()
