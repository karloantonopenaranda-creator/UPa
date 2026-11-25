"""
DormCare Live Alerts: web dashboard (the "mailroom" control room) for demonstrating the system.

    python -m pubsub.dashboard                 then open http://127.0.0.1:8080 (it opens automatically)
    python -m pubsub.dashboard --help          (ports, data folder, ...)

The dashboard does NOT replace or bypass the middleware. It:
  * launches the REAL broker and subscriber programs as separate processes, and hard-kills them when
    you press "Crash" (exactly like scripts\\crash.bat);
  * shows what those processes print, live, by reading their console output;
  * publishes through the real TCP protocol, as the manager-office and maintenance-desk publishers;
  * asks the broker for its STATUS once a second, to draw each subscriber's queue.
Everything shown in the browser therefore went through the broker over TCP.

Browser <-> dashboard: plain HTTP (standard library http.server). Live updates use server-sent events
(GET /events): one long-lived HTTP response to which the server appends a "data: {json}" line per event.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pubsub.common import children, config, console, protocol
from pubsub.common.protocol import ProtocolError
from pubsub.publisher.publisher import Publisher, PublishFailed

STATIC_DIR = Path(__file__).resolve().parent / "static"
MONITOR_ID = "dashboard-monitor"

# The broker and subscribers print fixed-width lines:  "HH:MM:SS TAG_______ message"  (see console.log)
LOG_LINE = re.compile(r"^(\d\d:\d\d:\d\d) (.{10}) (.*)$")
CONTINUATION = " " * 20
DELIVER_TEXT = re.compile(r"^#(\d+) (\S+) from (\S+)(?:\s+\(queued (\d+)s ago\))?")
PUBLISH_TEXT = re.compile(r'^#(\d+) (\S+) from (\S+)(?: "(.*)")?')
ROUTE_TARGET = re.compile(r"([A-Za-z0-9][\w.-]*) \((online|offline: queued)\)")
SUB_STATE_TAGS = {"CONNECTED", "OFFLINE", "RECONNECT", "WAITING", "FAULT", "STOPPED", "ERROR"}


class EventHub:
    """Fans dashboard events out to every open browser tab. Recent events are kept so a page that is
    (re)loaded can rebuild the same view."""

    REPLAYED = {"log", "message", "message_line", "route", "puback", "sub_state"}

    def __init__(self, history: int = 2000):
        self._lock = threading.Lock()
        self._clients: set[queue.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._ids = itertools.count(1)

    def emit(self, event_type: str, **data) -> None:
        with self._lock:
            event = {"type": event_type, "id": next(self._ids), "at": time.time(), **data}
            if event_type in self.REPLAYED:
                self._history.append(event)
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                self.unsubscribe(q)  # a stuck browser tab must not slow everyone else down

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self._lock:
            self._clients.add(q)
            return q, list(self._history)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


class ManagedProcess:
    """A real broker or subscriber process started by the dashboard; its output is parsed line by line."""

    def __init__(self, name: str, kind: str, hub: EventHub, on_line):
        self.name, self.kind, self.hub, self.on_line = name, kind, hub, on_line
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: list[str]) -> bool:
        with self._lock:
            if self.running:
                return False
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no extra console windows
            self.proc = subprocess.Popen([sys.executable, *args], cwd=config.PROJECT_ROOT, env=env,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                         errors="replace", creationflags=flags)
            # If this program is closed or crashes, the broker/subscriber it started must not linger unseen.
            children.end_with_this_process(self.proc)
            proc = self.proc
        self.hub.emit("process", name=self.name, kind=self.kind, state="running", pid=proc.pid)
        threading.Thread(target=self._pump, args=(proc,), name=f"out-{self.name}", daemon=True).start()
        return True

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in iter(proc.stdout.readline, ""):
            self.on_line(self.name, line.rstrip("\n"))
        code = proc.wait()
        proc.stdout.close()
        self.hub.emit("process", name=self.name, kind=self.kind, state="stopped", code=code)

    def crash(self) -> bool:
        """Hard kill (TerminateProcess on Windows): no clean-up code runs, no DISCONNECT is sent."""
        with self._lock:
            proc = self.proc
        if proc is None or proc.poll() is not None:
            return False
        proc.kill()
        proc.wait(timeout=10)
        return True


class Dashboard:
    def __init__(self, broker_port: int, data_dir: Path, scenario_file: Path = config.SCENARIO_FILE):
        self.broker_port = broker_port
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "broker.db"
        self.state_dir = self.data_dir / "subscribers"
        self.scenario = json.loads(Path(scenario_file).read_text(encoding="utf-8"))
        self.hub = EventHub()
        self.broker = ManagedProcess("broker", "broker", self.hub, self._on_broker_line)
        self.subscribers = {s["id"]: ManagedProcess(s["id"], "subscriber", self.hub, self._on_subscriber_line)
                            for s in self.scenario["subscribers"]}
        self.publishers: dict[str, Publisher] = {}
        self._publisher_locks = {p["id"]: threading.Lock() for p in self.scenario["publishers"]}
        self._acting_publisher = threading.local()
        self._keys = itertools.count(1)
        self._current_message: dict[str, str | None] = {}
        self._last_publish: dict | None = None
        self.last_status: dict | None = None
        self.stopping = threading.Event()
        console.add_listener(self._on_console)  # shows the in-process publishers' RETRY/PUBACK lines too
        threading.Thread(target=self._status_loop, name="status-monitor", daemon=True).start()

    # --- what the browser asks for ---------------------------------------------------------------
    def scenario_info(self) -> dict:
        events = {}
        for i, e in enumerate(self.scenario["events"]):
            events.setdefault(e["publisher"], []).append({"index": i, "topic": e["topic"], "title": e.get("title", ""),
                                                          "body": e.get("body", ""), "property": e.get("property", "")})
        return {"name": self.scenario.get("name"), "topics": self.scenario.get("topics", {}),
                "publishers": [{**p, "events": events.get(p["id"], [])} for p in self.scenario["publishers"]],
                "subscribers": self.scenario["subscribers"], "broker_port": self.broker_port}

    def snapshot(self) -> dict:
        return {"processes": {"broker": self.broker.running,
                              **{sid: p.running for sid, p in self.subscribers.items()}},
                "status": self.last_status, "external_broker": self.external_broker(),
                "broker_port": self.broker_port}

    def external_broker(self) -> bool:
        """True if something else (e.g. the command-line demo) already listens on the broker port."""
        return not self.broker.running and _port_open(self.broker_port)

    # --- process control ---------------------------------------------------------------------------
    def start_broker(self, fault_skip_puback: int = 0) -> dict:
        if self.external_broker():
            return {"ok": False, "error": f"Another broker is already running on port {self.broker_port}. "
                                          f"Close the command-line demo windows, or start the dashboard "
                                          f"with --broker-port 5600."}
        args = ["-m", "pubsub.broker", "--port", str(self.broker_port), "--db", str(self.db_path), "--no-color"]
        if fault_skip_puback:
            args += ["--fault-skip-puback", str(fault_skip_puback)]
        started = self.broker.start(args)
        return {"ok": True, "started": started}

    def crash_broker(self) -> dict:
        return {"ok": True, "crashed": self.broker.crash()}

    def start_subscriber(self, sid: str, crash_after: int = 0) -> dict:
        if sid not in self.subscribers:
            return {"ok": False, "error": f"unknown subscriber {sid}"}
        args = ["-m", "pubsub.subscriber", "--id", sid, "--port", str(self.broker_port),
                "--state-dir", str(self.state_dir), "--no-color"]
        if not self._broker_knows(sid):
            # First start: register the topics. After that the broker remembers them (durable subscriptions),
            # so restarts deliberately omit --topics.
            spec = next(s for s in self.scenario["subscribers"] if s["id"] == sid)
            args += ["--topics", ",".join(spec["topics"])]
        if crash_after:
            args += ["--crash-after", str(crash_after)]
        return {"ok": True, "started": self.subscribers[sid].start(args)}

    def crash_subscriber(self, sid: str) -> dict:
        if sid not in self.subscribers:
            return {"ok": False, "error": f"unknown subscriber {sid}"}
        return {"ok": True, "crashed": self.subscribers[sid].crash()}

    def start_demo(self) -> dict:
        """Start the broker, wait until it listens, then start every subscriber (in the background)."""
        result = self.start_broker()
        if not result["ok"]:
            return result

        def bring_up():
            for _ in range(60):
                if _port_open(self.broker_port):
                    break
                time.sleep(0.25)
            for sid in self.subscribers:
                self.start_subscriber(sid)
                time.sleep(0.15)
        threading.Thread(target=bring_up, daemon=True).start()
        return {"ok": True}

    def reset(self) -> dict:
        """Stop everything and delete the demo's queues and subscriber logs."""
        for proc in (*self.subscribers.values(), self.broker):
            proc.crash()
        for pid, pub in list(self.publishers.items()):
            with self._publisher_locks[pid]:
                pub.close(graceful=False)
        self.publishers.clear()
        time.sleep(0.3)
        for path in (self.db_path, Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if self.state_dir.is_dir():
            for f in self.state_dir.glob("*"):
                f.unlink(missing_ok=True)
        self.last_status = None
        self._current_message.clear()
        self.hub.clear()
        self.hub.emit("reset")
        return {"ok": True}

    def shutdown(self) -> None:
        self.stopping.set()
        for proc in (*self.subscribers.values(), self.broker):
            proc.crash()
        for pub in self.publishers.values():
            pub.close()

    # --- publishing (through the real protocol) ------------------------------------------------------
    def publish(self, publisher_id: str, topic: str, payload: dict) -> dict:
        if publisher_id not in self._publisher_locks:
            return {"ok": False, "error": f"unknown publisher {publisher_id}"}
        if not _port_open(self.broker_port):
            return self._publish_failed(publisher_id, "The broker is not running. Start it, then publish again.")
        with self._publisher_locks[publisher_id]:
            pub = self.publishers.get(publisher_id)
            if pub is None:
                pub = self.publishers[publisher_id] = Publisher(publisher_id, "127.0.0.1", self.broker_port,
                                                                puback_timeout=3, retries=2)
            self._acting_publisher.id = publisher_id
            try:
                ack = pub.publish(topic, payload)
            except (PublishFailed, OSError) as exc:
                return self._publish_failed(publisher_id, f"The broker did not confirm the message ({exc}). "
                                                          f"Check that it is running.")
            except ProtocolError as err:
                return self._publish_failed(publisher_id, f"The broker rejected the message: {err.reason}")
            finally:
                self._acting_publisher.id = None
        self.hub.emit("puback", publisher=publisher_id, ok=True, topic=topic, title=payload.get("title", ""),
                      seq=ack["seq"], routed_to=ack["routed_to"], duplicate=bool(ack.get("duplicate")))
        return {"ok": True, **ack}

    def _publish_failed(self, publisher_id: str, message: str) -> dict:
        pub = self.publishers.pop(publisher_id, None)
        if pub:
            pub.close(graceful=False)
        self.hub.emit("puback", publisher=publisher_id, ok=False, error=message)
        return {"ok": False, "error": message}

    def _on_console(self, tag: str, message: str) -> None:
        """console.log listener: forwards log lines printed by the in-process publishers."""
        pid = getattr(self._acting_publisher, "id", None)
        if pid:
            self.hub.emit("log", source=pid, time=console.clock(), tag=tag, text=message)

    # --- understanding the processes' output -----------------------------------------------------------
    def _on_broker_line(self, name: str, line: str) -> None:
        m = LOG_LINE.match(line)
        if m:
            time_, tag, text = m.group(1), m.group(2).strip(), m.group(3)
            self.hub.emit("log", source="broker", time=time_, tag=tag, text=text)
            pm = PUBLISH_TEXT.match(text) if tag == "PUBLISH" else None
            self._last_publish = ({"seq": int(pm.group(1)), "topic": pm.group(2), "publisher": pm.group(3),
                                   "title": pm.group(4) or ""} if pm else None)
        elif line.startswith(CONTINUATION):
            text = line.strip()
            self.hub.emit("log", source="broker", time="", tag="", text=text)
            if text.startswith("->") and self._last_publish:
                targets = [{"id": cid, "online": state == "online"} for cid, state in ROUTE_TARGET.findall(text)]
                self.hub.emit("route", **self._last_publish, targets=targets)
                self._last_publish = None
        elif line.strip():
            self.hub.emit("log", source="broker", time="", tag="ERROR", text=line.strip())

    def _on_subscriber_line(self, name: str, line: str) -> None:
        m = LOG_LINE.match(line)
        if not m:
            key = self._current_message.get(name)
            if key and line.startswith(CONTINUATION):
                self.hub.emit("message_line", subscriber=name, key=key, text=line.strip())
            return
        time_, tag, text = m.group(1), m.group(2).strip(), m.group(3)
        self._current_message[name] = None
        self.hub.emit("log", source=name, time=time_, tag=tag, text=text)
        if tag in ("LIVE", "CATCH-UP", "REDELIVERY"):
            dm = DELIVER_TEXT.match(text)
            if dm:
                key = f"m{next(self._keys)}"
                self._current_message[name] = key
                self.hub.emit("message", subscriber=name, key=key, label=tag, seq=int(dm.group(1)),
                              topic=dm.group(2), publisher=dm.group(3),
                              queued_s=int(dm.group(4)) if dm.group(4) else None, time=time_)
        elif tag == "DUPLICATE":
            dm = re.match(r"#(\d+)", text)
            self.hub.emit("message", subscriber=name, key=f"m{next(self._keys)}", label="DUPLICATE",
                          seq=int(dm.group(1)) if dm else None, topic="", publisher="", time=time_, note=text)
        elif tag in SUB_STATE_TAGS:
            self.hub.emit("sub_state", subscriber=name, tag=tag, text=text, time=time_)

    # --- broker STATUS, once a second -------------------------------------------------------------------
    def _broker_knows(self, sid: str) -> bool:
        clients = (self.last_status or {}).get("clients", [])
        return any(c["client_id"] == sid and c["topics"] for c in clients) and self.broker.running

    def _status_loop(self) -> None:
        sock = reader = None
        while not self.stopping.is_set():
            if not self.broker.running:
                if sock:
                    sock.close()
                    sock = reader = None
                if self.last_status is not None:
                    self.last_status = None
                    self.hub.emit("status", status=None)
                self.stopping.wait(0.5)
                continue
            try:
                if sock is None:
                    sock = socket.create_connection(("127.0.0.1", self.broker_port), timeout=2)
                    reader = protocol.FrameReader(sock)
                    protocol.send(sock, {"type": protocol.CONNECT, "client_id": MONITOR_ID, "role": protocol.PUBLISHER})
                    _read_until(reader, protocol.CONNACK)
                protocol.send(sock, {"type": protocol.STATUS})
                reply = _read_until(reader, protocol.STATUS_REPLY)
                status = {"clients": reply["clients"],
                          "publishers_online": [p for p in reply.get("publishers_online", []) if p != MONITOR_ID]}
                if status != self.last_status:
                    self.last_status = status
                    self.hub.emit("status", status=status)
            except (OSError, ProtocolError, ConnectionError):
                if sock:
                    sock.close()
                sock = reader = None
            self.stopping.wait(1.0)


def _read_until(reader: protocol.FrameReader, mtype: str) -> dict:
    while True:
        frame = reader.read()
        if frame is None:
            raise ConnectionError("broker closed the connection")
        if frame["type"] == mtype:
            return frame


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A browser tab that closes mid-request is normal; only report real errors."""
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "DormCareDashboard/1.0"
    CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}

    @property
    def dash(self) -> Dashboard:
        return self.server.dashboard  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # --- GET ---------------------------------------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file("index.html")
        if path.startswith("/static/"):
            return self._file(path[len("/static/"):])
        if path == "/api/scenario":
            return self._json(self.dash.scenario_info())
        if path == "/api/state":
            return self._json(self.dash.snapshot())
        if path == "/events":
            return self._events()
        self._json({"ok": False, "error": "not found"}, 404)

    # --- POST --------------------------------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except (ValueError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "request body must be JSON"}, 400)
        d = self.dash
        m = re.fullmatch(r"/api/subscribers/([\w.-]+)/(start|crash)", path)
        if path == "/api/demo/start":
            result = d.start_demo()
        elif path == "/api/broker/start":
            result = d.start_broker(int(body.get("fault_skip_puback") or 0))
        elif path == "/api/broker/crash":
            result = d.crash_broker()
        elif m and m.group(2) == "start":
            result = d.start_subscriber(m.group(1), int(body.get("crash_after") or 0))
        elif m:
            result = d.crash_subscriber(m.group(1))
        elif path == "/api/publish":
            result = self._publish(body)
        elif path == "/api/reset":
            result = d.reset()
        else:
            return self._json({"ok": False, "error": "not found"}, 404)
        self._json(result, 200 if result.get("ok") else 409)

    def _publish(self, body: dict) -> dict:
        d = self.dash
        publisher = body.get("publisher", "")
        if "event" in body:
            try:
                event = d.scenario["events"][int(body["event"])]
            except (ValueError, IndexError, TypeError):
                return {"ok": False, "error": "unknown scenario event"}
            payload = {k: v for k, v in event.items() if k not in ("publisher", "topic")}
            return d.publish(publisher or event["publisher"], event["topic"], payload)
        title = str(body.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "Write a title for the message."}
        return d.publish(publisher, str(body.get("topic", "")), {"title": title, "body": str(body.get("body", "")).strip()})

    # --- helpers --------------------------------------------------------------------------------------------
    def _json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            return self._json({"ok": False, "error": "not found"}, 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q, backlog = self.dash.hub.subscribe()
        try:
            self._send_event({"type": "snapshot", **self.dash.snapshot()})
            for event in backlog:
                self._send_event(event)
            while not self.dash.stopping.is_set():
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._send_event(event)
        except OSError:
            pass  # the browser tab was closed
        finally:
            self.dash.hub.unsubscribe(q)

    def _send_event(self, event: dict) -> None:
        self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pubsub.dashboard",
                                     description="Web dashboard for demonstrating DormCare Live Alerts.")
    parser.add_argument("--host", default="127.0.0.1", help="address for the web page (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="web page port (default 8080)")
    parser.add_argument("--broker-port", type=int, default=config.PORT, help=f"broker TCP port (default {config.PORT})")
    parser.add_argument("--data-dir", type=Path, default=config.DATA_DIR, help="folder for the broker database "
                                                                               "and subscriber logs")
    parser.add_argument("--no-browser", action="store_true", help="do not open the web page automatically")
    args = parser.parse_args(argv)

    console.setup(title="DormCare dashboard")
    dashboard = Dashboard(args.broker_port, args.data_dir)
    try:
        server = DashboardHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        dashboard.shutdown()
        raise SystemExit(f"Cannot open the dashboard on {args.host}:{args.port} ({exc}). "
                         f"Is it already running? Try --port 8081.")
    server.dashboard = dashboard  # type: ignore[attr-defined]
    url = f"http://{args.host}:{args.port}/"
    console.log("DASHBOARD", f"open {url}  (broker port {args.broker_port}; press Ctrl+C here to stop)", "bold", "green")
    if dashboard.external_broker():
        console.log("NOTE", f"a broker is already running on port {args.broker_port} (probably the command-line demo). "
                            f"Close it before pressing Start in the dashboard.", "yellow")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        console.log("STOPPED", "dashboard stopped; broker and subscribers it started were closed", "yellow")
    finally:
        dashboard.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
