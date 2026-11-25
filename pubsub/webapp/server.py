"""
DormCare website + broker gateway: runs the existing DormCare web app on top of the publish-subscribe system.

    python -m pubsub.webapp            then open http://127.0.0.1:8000  (it opens automatically)
    python -m pubsub.webapp --help     (ports, data folder, ...)

This server does two things:
  1. serves the existing DormCare website (index.html, app.js, styles.css from the project folder);
  2. connects that website to the real broker. A browser cannot open raw TCP sockets, so this server is the
     website's bridge, speaking the normal protocol to the broker on the website's behalf:

        browser --POST /broker/publish-->  gateway  --PUBLISH------>  broker
        browser <--GET  /broker/stream---  gateway  <--DELIVER------  broker   (server-sent events)
        browser --POST /broker/ack------>  gateway  --ACK---------->  broker

Every DormCare user is a durable subscriber at the broker (the browser decides the topics; see app.js):

        web-manager-m1       manager.m1
        web-admin            dormcare.admin
        web-tenant-<user>    tenant.<user>                 personal alerts: bills, payments, tickets, join decisions
                             <property>.announcements      notices for the property the tenant lives in

While no browser tab is showing a user, the broker queues that user's messages; when their view opens, the
queue is delivered (catch-up). The browser ACKs each message only after saving it, so at-least-once delivery
holds all the way to the page. If no broker is listening, this server starts one.
"""
from __future__ import annotations

import argparse
import json
import queue
import socket
import threading
import time
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pubsub.common import config, console, protocol
from pubsub.common.protocol import ProtocolError
from pubsub.dashboard.server import DashboardHTTPServer, ManagedProcess, _port_open
from pubsub.publisher.publisher import Publisher, PublishFailed

# Only these files of the project folder are served: never the database, the code or anything else.
SITE_FILES = {
    "/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
    "/styles.css": "styles.css", "/marketplace.js": "marketplace.js",
    "/marketplace.css": "marketplace.css",
}
# Explicit public asset list: the gateway must never become a general project file server.
for _asset in ("base.css", "manrope.ttf", "nunito.ttf", "living-room.jpg", "studio.jpg",
               "bedroom.jpg", "ana.jpg", "miguel.jpg", "james.jpg"):
    SITE_FILES[f"/assets/marketplace/{_asset}"] = f"assets/marketplace/{_asset}"
for _asset in ("upa-wordmark.png", "upa-app-icon.png"):
    SITE_FILES[f"/assets/brand/{_asset}"] = f"assets/brand/{_asset}"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png", ".ttf": "font/ttf"}
ROLES = ("manager", "tenant", "admin")
GRACE_SECONDS = 4.0  # keep a user's broker session this long after their last browser tab goes away


class _Quiet:
    """ManagedProcess wants an event hub; the gateway has nothing to report there."""

    def emit(self, *args, **kwargs) -> None:
        pass


class WebSubscription:
    """One durable subscriber session at the broker on behalf of one DormCare user (e.g. web-tenant-u-juan).

    Several browser tabs may show the same user; they all listen to this one session. The session stays open
    while a tab is listening, plus a short grace period, so a page reload does not drop it. Messages that arrive
    while no tab is listening are simply not ACKed: the broker keeps them and sends them again later.
    """

    def __init__(self, gateway: "Gateway", client_id: str):
        self.gateway = gateway
        self.client_id = client_id
        self.topics: set[str] = set()
        self.listeners: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.ready: dict | None = None  # CONNACK details of the current session, for tabs that join later
        self.keep_until = 0.0
        self.thread: threading.Thread | None = None
        self.retry_delay = 1.0

    # --- used by the HTTP handlers -------------------------------------------------------------------
    def want(self, topics: list[str], keep_for: float = GRACE_SECONDS) -> None:
        """Make sure the session exists and is subscribed to `topics` (subscriptions are durable)."""
        with self.lock:
            new = [t for t in topics if t not in self.topics]
            self.topics.update(new)
            self.keep_until = max(self.keep_until, time.time() + keep_for)
            sock = self.sock
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name=f"web-{self.client_id}", daemon=True)
                self.thread.start()
        if sock is not None:
            for topic in new:
                self._send({"type": protocol.SUBSCRIBE, "topic": topic})

    def attach(self, listener: queue.Queue) -> dict | None:
        with self.lock:
            self.listeners.add(listener)
            return self.ready

    def detach(self, listener: queue.Queue) -> None:
        with self.lock:
            self.listeners.discard(listener)
            self.keep_until = max(self.keep_until, time.time() + GRACE_SECONDS)

    def ack(self, seq: int) -> bool:
        return self._send({"type": protocol.ACK, "seq": seq})

    # --- the session ----------------------------------------------------------------------------------
    def _send(self, message: dict) -> bool:
        sock = self.sock
        if sock is None:
            return False
        try:
            protocol.send(sock, message, self.send_lock)
            return True
        except OSError:
            return False

    def _broadcast(self, event: dict) -> None:
        with self.lock:
            listeners = list(self.listeners)
        for q in listeners:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def _wanted(self) -> bool:
        return bool(self.listeners) or time.time() < self.keep_until

    def _run(self) -> None:
        while True:
            with self.lock:
                if self.gateway.stopping.is_set() or not self._wanted():
                    self.thread = None
                    return
            if not self._session():
                self.gateway.stopping.wait(self.retry_delay)
                self.retry_delay = min(self.retry_delay * 2, config.RECONNECT_MAX_DELAY)

    def _session(self) -> bool:
        """One connection to the broker. Returns False if it failed and should be retried after a pause."""
        try:
            sock = socket.create_connection(("127.0.0.1", self.gateway.broker_port), timeout=3)
        except OSError:
            self._broadcast({"type": "offline", "reason": "The broker is not reachable."})
            return False
        reader = protocol.FrameReader(sock)
        try:
            protocol.send(sock, {"type": protocol.CONNECT, "client_id": self.client_id, "role": protocol.SUBSCRIBER})
            connack = reader.read()
            if connack is None or connack["type"] != protocol.CONNACK:
                raise ConnectionError("the broker refused the connection")
            with self.lock:
                topics = sorted(self.topics)
            for topic in topics:
                protocol.send(sock, {"type": protocol.SUBSCRIBE, "topic": topic})
            ready = {"type": "ready", "client": self.client_id, "queued": connack["queued"],
                     "resumed": connack["resumed"], "connected_at": time.time()}
            with self.lock:
                self.sock = sock
                self.ready = ready
                late = sorted(self.topics - set(topics))  # topics added while we were connecting
            for topic in late:
                self._send({"type": protocol.SUBSCRIBE, "topic": topic})
            self.retry_delay = 1.0
            self._broadcast(ready)

            sock.settimeout(1.0)
            last_ping = last_heard = time.time()
            while not self.gateway.stopping.is_set():
                with self.lock:
                    if not self._wanted():
                        break
                if time.time() - last_ping >= config.HEARTBEAT_INTERVAL:
                    self._send({"type": protocol.PING})
                    last_ping = time.time()
                try:
                    frame = reader.read()
                except TimeoutError:
                    if time.time() - last_heard > config.HEARTBEAT_TIMEOUT:
                        raise ConnectionError("the broker stopped answering") from None
                    continue
                if frame is None:
                    raise ConnectionError("the broker closed the connection")
                last_heard = time.time()
                if frame["type"] == protocol.DELIVER:
                    self._broadcast({"type": "deliver", **{k: frame[k] for k in
                                     ("seq", "topic", "publisher", "payload", "published_at", "redelivery")}})
                elif frame["type"] == protocol.ERROR and frame.get("code") == protocol.REPLACED:
                    raise ConnectionError("another connection took over this user")
            self._send({"type": protocol.DISCONNECT})  # nobody is viewing this user any more
            return True
        except (OSError, ConnectionError, ProtocolError) as exc:
            self._broadcast({"type": "offline", "reason": str(exc)})
            return False
        finally:
            with self.lock:
                self.sock = None
                self.ready = None
            try:
                sock.close()
            except OSError:
                pass


class Gateway:
    def __init__(self, broker_port: int, data_dir: Path, start_broker: bool = True):
        self.broker_port = broker_port
        self.data_dir = Path(data_dir)
        self.stopping = threading.Event()
        self.subscriptions: dict[str, WebSubscription] = {}
        self._subs_lock = threading.Lock()
        self.publishers: dict[str, Publisher] = {}
        self._pub_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.broker = ManagedProcess("broker", "broker", _Quiet(), self._broker_line) if start_broker else None

    # --- the broker process (only if none is running) ------------------------------------------------
    def broker_healthy(self) -> bool:
        """A real protocol check (CONNECT, PING, wait for PONG), not just "is the port open": a broker that is
        listening but cannot serve clients must not be reported as fine."""
        try:
            with socket.create_connection(("127.0.0.1", self.broker_port), timeout=2) as sock:
                sock.settimeout(2)
                reader = protocol.FrameReader(sock)
                protocol.send(sock, {"type": protocol.CONNECT, "client_id": "dormcare-web-health",
                                     "role": protocol.PUBLISHER})
                if (reader.read() or {}).get("type") != protocol.CONNACK:
                    return False
                protocol.send(sock, {"type": protocol.PING})
                while True:
                    frame = reader.read()
                    if frame is None:
                        return False
                    if frame["type"] == protocol.PONG:
                        protocol.send(sock, {"type": protocol.DISCONNECT})
                        return True
        except (OSError, ProtocolError):
            return False

    def ensure_broker(self) -> bool:
        if _port_open(self.broker_port):
            if self.broker_healthy():
                console.log("BROKER", f"using the broker already running on port {self.broker_port}", "green")
                return True
            console.log("BROKER", f"something is listening on port {self.broker_port} but does not answer like a "
                                  f"healthy broker. Stop it (scripts\\crash.bat broker) and start this website again.",
                        "red")
            return False
        if self.broker is None:
            console.log("BROKER", f"no broker on port {self.broker_port}; start one with: python -m pubsub.broker", "yellow")
            return False
        self.broker.start(["-m", "pubsub.broker", "--port", str(self.broker_port),
                           "--db", str(self.data_dir / "broker.db"), "--no-color"])
        for _ in range(60):
            if _port_open(self.broker_port):
                console.log("BROKER", f"started a broker on port {self.broker_port} (its log appears below)", "green")
                return True
            time.sleep(0.2)
        console.log("BROKER", "the broker did not start; see the messages above", "red")
        return False

    @staticmethod
    def _broker_line(name: str, line: str) -> None:
        print(f"  broker | {line}", flush=True)

    # --- operations used by the website --------------------------------------------------------------
    def subscription(self, client_id: str) -> WebSubscription:
        protocol.validate_client_id(client_id)
        with self._subs_lock:
            sub = self.subscriptions.get(client_id)
            if sub is None:
                sub = self.subscriptions[client_id] = WebSubscription(self, client_id)
            return sub

    def provision(self, items: list[dict]) -> dict:
        """Register every DormCare user's durable subscriptions, so messages queue for them even before their
        view has been opened."""
        for item in items:
            topics = [protocol.validate_topic(t) for t in item.get("topics", [])]
            # A short session is enough to register the subscriptions; it then disconnects, so messages for
            # this user queue at the broker until their view is actually opened.
            self.subscription(str(item.get("client", ""))).want(topics, keep_for=1.0)
        return {"ok": True, "count": len(items)}

    def publish(self, role: str, topic: str, payload: object) -> dict:
        if role not in ROLES:
            return {"ok": False, "error": f"role must be one of {', '.join(ROLES)}"}
        protocol.validate_topic(topic)
        if not isinstance(payload, dict):
            return {"ok": False, "error": "payload must be a JSON object"}
        if not _port_open(self.broker_port):
            return {"ok": False, "error": "The broker is not running."}
        publisher_id = f"dormcare-web-{role}"
        with self._pub_locks[publisher_id]:
            pub = self.publishers.get(publisher_id)
            if pub is None:
                pub = self.publishers[publisher_id] = Publisher(publisher_id, "127.0.0.1", self.broker_port,
                                                                puback_timeout=3, retries=2)
            try:
                ack = pub.publish(topic, payload)
            except (PublishFailed, OSError) as exc:
                self.publishers.pop(publisher_id, None)
                pub.close(graceful=False)
                return {"ok": False, "error": f"The broker did not confirm the message ({exc})."}
        return {"ok": True, "seq": ack["seq"], "routed_to": ack["routed_to"], "duplicate": bool(ack.get("duplicate"))}

    def ack(self, client_id: str, seq: int) -> dict:
        sub = self.subscriptions.get(client_id)
        return {"ok": bool(sub and sub.ack(int(seq)))}

    def info(self) -> dict:
        return {"ok": True, "broker": self.broker_healthy(), "broker_port": self.broker_port}

    def shutdown(self) -> None:
        self.stopping.set()
        for pub in self.publishers.values():
            pub.close()
        if self.broker is not None:
            self.broker.crash()


class Handler(BaseHTTPRequestHandler):
    server_version = "DormCareWeb/1.0"

    @property
    def gw(self) -> Gateway:
        return self.server.gateway  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in SITE_FILES:
            return self._file(config.PROJECT_ROOT / SITE_FILES[url.path])
        if url.path == "/favicon.ico":  # the DormCare site has no icon; answer quietly instead of 404
            self.send_response(204)
            self.end_headers()
            return
        if url.path == "/broker/info":
            return self._json(self.gw.info())
        if url.path == "/broker/stream":
            q = parse_qs(url.query)
            client = (q.get("client") or [""])[0]
            topics = [t for t in (q.get("topics") or [""])[0].split(",") if t]
            try:
                sub = self.gw.subscription(client)
                sub.want([protocol.validate_topic(t) for t in topics])
            except ProtocolError as err:
                return self._json({"ok": False, "error": err.reason}, 400)
            return self._stream(sub)
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except (ValueError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "request body must be JSON"}, 400)
        try:
            if path == "/broker/publish":
                result = self.gw.publish(str(body.get("role", "")), str(body.get("topic", "")), body.get("payload"))
            elif path == "/broker/ack":
                result = self.gw.ack(str(body.get("client", "")), int(body.get("seq", 0)))
            elif path == "/broker/provision":
                result = self.gw.provision(list(body.get("subscriptions", [])))
            else:
                return self._json({"ok": False, "error": "not found"}, 404)
        except ProtocolError as err:
            result = {"ok": False, "error": err.reason}
        except (TypeError, ValueError) as exc:
            result = {"ok": False, "error": f"bad request ({exc})"}
        self._json(result, 200 if result.get("ok") else 409)

    # --- helpers ----------------------------------------------------------------------------------------
    def _json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, sub: WebSubscription) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        listener: queue.Queue = queue.Queue(maxsize=2000)
        ready = sub.attach(listener)
        try:
            # attached_at lets the page tell "published while you were away" (catch-up) from "live".
            self._event({"type": "hello", "client": sub.client_id, "topics": sorted(sub.topics),
                         "attached_at": time.time()})
            if ready:
                self._event(ready)
            while not self.gw.stopping.is_set():
                try:
                    event = listener.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._event(event)
        except OSError:
            pass  # the tab was closed or navigated away
        finally:
            sub.detach(listener)

    def _event(self, event: dict) -> None:
        self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pubsub.webapp",
                                     description="Run the DormCare website on top of the publish-subscribe broker.")
    parser.add_argument("--host", default="127.0.0.1", help="address for the website (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="website port (default 8000)")
    parser.add_argument("--broker-port", type=int, default=config.PORT, help=f"broker port (default {config.PORT})")
    parser.add_argument("--data-dir", type=Path, default=config.DATA_DIR,
                        help="folder for the broker database if this server starts the broker")
    parser.add_argument("--no-broker", action="store_true", help="never start a broker; use one that is running")
    parser.add_argument("--no-browser", action="store_true", help="do not open the website automatically")
    args = parser.parse_args(argv)

    console.setup(title="DormCare website")
    gateway = Gateway(args.broker_port, args.data_dir, start_broker=not args.no_broker)
    gateway.ensure_broker()
    try:
        server = DashboardHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        gateway.shutdown()
        raise SystemExit(f"Cannot open the website on {args.host}:{args.port} ({exc}). Try --port 8001.")
    server.gateway = gateway  # type: ignore[attr-defined]
    url = f"http://{args.host}:{args.port}/"
    console.log("WEBSITE", f"DormCare is running at {url}  (Ctrl+C here to stop)", "bold", "green")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        console.log("STOPPED", "website stopped", "yellow")
    finally:
        gateway.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
