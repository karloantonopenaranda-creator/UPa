"""
Helpers shared by the integration and fault tests.

The tests exercise the REAL programs: the broker (and sometimes the publisher/subscriber CLIs) run as
separate processes talking over TCP on 127.0.0.1, exactly as in the demo. A process "crash" is
simulated with Popen.kill(), which on Windows calls TerminateProcess(): no cleanup code runs and no
DISCONNECT is sent, just like pulling the plug on that process.
"""
from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from pubsub.common import protocol

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listening on port {port} after {timeout}s")


class OutputCollector:
    """Reads a child process's output on a background thread so tests can wait for specific lines."""

    def __init__(self, stream):
        self.lines: list[str] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._stream = stream
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Call after the process has exited: finish reading, then close the pipe."""
        self._thread.join(timeout=5)
        try:
            self._stream.close()
        except OSError:
            pass

    def _pump(self, stream) -> None:
        for line in iter(stream.readline, ""):
            self.lines.append(line.rstrip("\n"))
            self._queue.put(line)

    def wait_for(self, text: str, timeout: float = 10.0, count: int = 1) -> list[str]:
        """Block until `count` output lines (in total, including earlier ones) contain `text`."""
        deadline = time.time() + timeout
        while True:
            matches = [line for line in self.lines if text in line]
            if len(matches) >= count:
                return matches
            remaining = deadline - time.time()
            if remaining <= 0:
                raise AssertionError(f"timed out waiting for {count}x {text!r}; output so far:\n" +
                                     "\n".join(self.lines[-40:]))
            try:
                self._queue.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                pass

    def count(self, text: str) -> int:
        return sum(text in line for line in self.lines)

    def text(self) -> str:
        return "\n".join(self.lines)


def spawn(args: list[str]) -> tuple[subprocess.Popen, OutputCollector]:
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    proc = subprocess.Popen([PYTHON, *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    return proc, OutputCollector(proc.stdout)


class BrokerProcess:
    """A broker running as a separate process on a free port, with its own database file."""

    def __init__(self, workdir: Path, *extra: str):
        self.port = free_port()
        self.db = Path(workdir) / "broker.db"
        self.extra = list(extra)
        self.proc: subprocess.Popen | None = None
        self.output: OutputCollector | None = None

    def start(self) -> "BrokerProcess":
        self.proc, self.output = spawn(["-m", "pubsub.broker", "--port", str(self.port), "--db", str(self.db),
                                        "--no-color", *self.extra])
        wait_for_port(self.port)
        return self

    def kill(self) -> None:
        """Hard crash: TerminateProcess on Windows, SIGKILL elsewhere."""
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        if self.output:
            self.output.close()

    def restart(self, *extra: str) -> "BrokerProcess":
        self.kill()
        if extra:
            self.extra = list(extra)
        return self.start()


class RawClient:
    """A minimal protocol client used by the tests to send exact frames and inspect exact replies."""

    def __init__(self, port: int, client_id: str, role: str, connect: bool = True):
        self.client_id = client_id
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.reader = protocol.FrameReader(self.sock)
        self.inbox: list[dict] = []  # frames received while waiting for something else
        self.connack: dict | None = None
        if connect:
            self.send({"type": protocol.CONNECT, "client_id": client_id, "role": role})
            self.connack = self.expect(protocol.CONNACK)

    def send(self, message: dict) -> None:
        protocol.send(self.sock, message)

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def recv(self, timeout: float = 5.0) -> dict | None:
        """Next frame (from the inbox first). Returns None on timeout; raises EOFError if the broker closed us."""
        if self.inbox:
            return self.inbox.pop(0)
        self.sock.settimeout(timeout)
        try:
            frame = self.reader.read()
        except TimeoutError:
            return None
        except ConnectionError as exc:
            raise EOFError(str(exc)) from None
        if frame is None:
            raise EOFError("broker closed the connection")
        return frame

    def expect(self, mtype: str, timeout: float = 5.0, **match) -> dict:
        """Wait for a frame of `mtype` whose fields equal `match`; other frames are kept for later."""
        for i, frame in enumerate(self.inbox):
            if frame["type"] == mtype and all(frame.get(k) == v for k, v in match.items()):
                return self.inbox.pop(i)
        deadline = time.time() + timeout
        stash = []
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError(f"{self.client_id}: no {mtype} {match or ''} within {timeout}s")
                self.sock.settimeout(remaining)
                try:
                    frame = self.reader.read()
                except TimeoutError:
                    continue
                if frame is None:
                    raise EOFError(f"{self.client_id}: broker closed the connection while waiting for {mtype}")
                if frame["type"] == mtype and all(frame.get(k) == v for k, v in match.items()):
                    return frame
                if frame["type"] != protocol.PONG:
                    stash.append(frame)
        finally:
            self.inbox.extend(stash)

    def subscribe(self, topic: str) -> dict:
        self.send({"type": protocol.SUBSCRIBE, "topic": topic})
        return self.expect(protocol.SUBACK, topic=topic)

    def publish(self, topic: str, title: str = "test", msg_id: str | None = None, **extra) -> dict:
        msg_id = msg_id or f"{self.client_id}-{uuid.uuid4().hex[:8]}"
        self.send({"type": protocol.PUBLISH, "msg_id": msg_id, "topic": topic, "payload": {"title": title, **extra}})
        return self.expect(protocol.PUBACK, msg_id=msg_id)

    def deliveries(self, n: int, timeout: float = 10.0, ack: bool = True) -> list[dict]:
        """Collect n DELIVER frames, ACKing each one unless ack=False."""
        got = []
        deadline = time.time() + timeout
        while len(got) < n:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise AssertionError(f"{self.client_id}: got {len(got)} of {n} deliveries")
            frame = self.expect(protocol.DELIVER, timeout=remaining)
            got.append(frame)
            if ack:
                self.ack(frame["seq"])
        return got

    def no_delivery(self, wait: float = 1.0) -> bool:
        """True if no DELIVER arrives within `wait` seconds."""
        try:
            self.expect(protocol.DELIVER, timeout=wait)
            return False
        except AssertionError:
            return True

    def ack(self, seq: int) -> None:
        self.send({"type": protocol.ACK, "seq": seq})

    def status(self) -> dict:
        self.send({"type": protocol.STATUS})
        return self.expect(protocol.STATUS_REPLY)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def queue_depth(status: dict, client_id: str) -> int:
    for client in status["clients"]:
        if client["client_id"] == client_id:
            return client["pending"]
    return 0


def is_online(status: dict, client_id: str) -> bool:
    return any(c["client_id"] == client_id and c["online"] for c in status["clients"])


class BrokerTestCase(unittest.TestCase):
    """Starts a fresh broker (own port, own database) for every test and cleans everything up afterwards."""

    broker_args: tuple[str, ...] = ("--quiet", "--ack-timeout", "1", "--heartbeat-timeout", "3")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.workdir = Path(self._tmp.name)
        self.broker = BrokerProcess(self.workdir, *self.broker_args).start()
        self._clients: list[RawClient] = []
        self._procs: list[tuple[subprocess.Popen, OutputCollector]] = []

    def tearDown(self) -> None:
        for client in self._clients:
            client.close()
        for proc, out in self._procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            out.close()
        self.broker.kill()
        self._tmp.cleanup()

    def client(self, client_id: str, role: str = protocol.SUBSCRIBER, *topics: str) -> RawClient:
        c = RawClient(self.broker.port, client_id, role)
        self._clients.append(c)
        for topic in topics:
            c.subscribe(topic)
        return c

    def publisher(self, client_id: str = "pub") -> RawClient:
        return self.client(client_id, protocol.PUBLISHER)

    def spawn_cli(self, *args: str) -> tuple[subprocess.Popen, OutputCollector]:
        proc, out = spawn([*args, "--port", str(self.broker.port), "--no-color"])
        self._procs.append((proc, out))
        return proc, out

    def status(self) -> dict:
        probe = RawClient(self.broker.port, f"probe-{uuid.uuid4().hex[:6]}", protocol.PUBLISHER)
        try:
            return probe.status()
        finally:
            probe.close()

    def wait_until(self, predicate, timeout: float = 10.0, message: str = "condition") -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail(f"timed out waiting for {message}")
