"""
DormCare Live Alerts: subscriber client.

    python -m pubsub.subscriber --id tenant-juan --topics casa-jaro.announcements,casa-jaro.maintenance

What it does
  * Connects with a FIXED client id. The broker keys durable subscriptions and queues on that id,
    so messages published while this subscriber is offline wait for it (time uncoupling, §6.1).
  * Subscribes to the requested topics. Subscriptions are durable: after a restart you can omit
    --topics and the broker still remembers them.
  * Prints every message with a label:
        LIVE        published while we were connected
        CATCH-UP    published while we were offline and queued by the broker
        REDELIVERY  sent before, but we never ACKed it (e.g. we crashed before ACKing)
        DUPLICATE   already processed: recognised by (seq, published_at) and ignored
  * Processes a message, records its identity on disk, THEN sends the ACK. If we crash in
    between, the broker re-sends it (at-least-once), and the on-disk record stops us processing it twice.
  * Sends PING heartbeats and reconnects automatically with exponential back-off if the broker goes away.
  * Stops, without reconnecting, if a newer connection takes over its client id.

Fault-injection flags (tests and demo)
  --crash-after N   exit abruptly right after processing the N-th message, BEFORE its ACK is sent
  --no-ack          process messages but never ACK them (shows the broker re-sending after its timeout)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from collections import deque
from pathlib import Path

from pubsub.common import config, console, protocol
from pubsub.common.protocol import ProtocolError


class ProcessedLog:
    """Messages this subscriber has already processed, kept on disk so they survive a crash.

    This is what turns at-least-once delivery into "process each message once": a re-delivered message
    that is in this log is acknowledged again but not processed a second time.

    A message is identified by (seq, published_at), not by seq alone: the sequence starts again at 1 with a
    new broker database, and a new message #1 must not be mistaken for the old one. A re-delivery always
    carries the same two values, because both are read from the broker's stored copy.
    """

    def __init__(self, path: Path, keep: int = 2000):
        self.path = path
        self.keep = keep
        self._order: deque[tuple[int, float]] = deque()
        self._seen: set[tuple[int, float]] = set()
        if path.exists():
            try:
                for seq, published_at in json.loads(path.read_text(encoding="utf-8")).get("processed", []):
                    self._remember((int(seq), float(published_at)))
            except (ValueError, TypeError, AttributeError, OSError):
                pass  # a damaged or old-format file only means a possible duplicate; never a lost message

    def _remember(self, message: tuple[int, float]) -> None:
        self._order.append(message)
        self._seen.add(message)
        while len(self._order) > self.keep:
            self._seen.discard(self._order.popleft())

    def seen(self, seq: int, published_at: float) -> bool:
        return (seq, published_at) in self._seen

    def add(self, seq: int, published_at: float) -> None:
        self._remember((seq, published_at))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"processed": list(self._order)}), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: the file is either the old version or the new one


class Subscriber:
    def __init__(self, args: argparse.Namespace):
        self.client_id: str = args.id
        self.host, self.port = args.host, args.port
        self.add_topics = [t.strip() for t in (args.topics or "").split(",") if t.strip()]
        self.drop_topics = [t.strip() for t in (args.unsubscribe or "").split(",") if t.strip()]
        self.reconnect = not args.no_reconnect
        self.no_ack = args.no_ack
        self.crash_after = args.crash_after
        self.processed = ProcessedLog(Path(args.state_dir) / f"{self.client_id}.json")
        self.processed_this_run = 0
        self.sock: socket.socket | None = None
        self.send_lock = threading.Lock()
        self.connected_at = 0.0
        self.replaced = False

    # --- helpers -----------------------------------------------------------------
    def send(self, message: dict) -> None:
        protocol.send(self.sock, message, self.send_lock)

    def _heartbeat(self, stop: threading.Event) -> None:
        """Send a PING every HEARTBEAT_INTERVAL so the broker knows we are alive."""
        while not stop.wait(config.HEARTBEAT_INTERVAL):
            try:
                self.send({"type": protocol.PING})
            except OSError:
                return

    # --- main loop -----------------------------------------------------------------
    def run(self) -> int:
        delay = 1.0
        while True:
            try:
                had_connection = self.session()
                if had_connection:
                    delay = 1.0
            except ConnectionRefusedError:
                console.log("WAITING", f"broker at {self.host}:{self.port} is not reachable", "yellow")
            except (OSError, ProtocolError) as exc:
                console.log("ERROR", f"{exc}", "red")
            if self.replaced:
                console.log("STOPPED", f"another connection took over the id {self.client_id}; not reconnecting, "
                                       "otherwise the two would replace each other forever", "yellow")
                return 4
            if not self.reconnect:
                return 1
            console.log("RECONNECT", f"trying again in {delay:.0f}s", "yellow")
            time.sleep(delay)
            delay = min(delay * 2, config.RECONNECT_MAX_DELAY)

    def session(self) -> bool:
        """One connection to the broker. Returns when the connection is lost (True if CONNECT succeeded)."""
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        reader = protocol.FrameReader(self.sock)
        stop_heartbeat = threading.Event()
        try:
            self.send({"type": protocol.CONNECT, "client_id": self.client_id, "role": protocol.SUBSCRIBER})
            connack = reader.read()
            if connack is None:
                raise ConnectionError("broker closed the connection during CONNECT")
            if connack["type"] == protocol.ERROR:
                raise ProtocolError(connack["code"], connack["reason"])
            self.connected_at = time.time()
            self._print_connack(connack)

            for topic in self.add_topics:
                self.send({"type": protocol.SUBSCRIBE, "topic": topic})
            for topic in self.drop_topics:
                self.send({"type": protocol.UNSUBSCRIBE, "topic": topic})
            threading.Thread(target=self._heartbeat, args=(stop_heartbeat,), daemon=True).start()

            # A 1-second socket timeout keeps Ctrl+C responsive on Windows and lets us notice a silent broker.
            self.sock.settimeout(1.0)
            last_heard = time.time()
            while True:
                try:
                    message = reader.read()
                except TimeoutError:
                    if time.time() - last_heard > config.HEARTBEAT_TIMEOUT:
                        console.log("OFFLINE", "no reply from the broker for "
                                               f"{config.HEARTBEAT_TIMEOUT:.0f}s; assuming it has failed", "red")
                        return True
                    continue
                except OSError:
                    console.log("OFFLINE", "connection to the broker was reset", "red")
                    return True
                if message is None:
                    console.log("OFFLINE", "the broker closed the connection", "red")
                    return True
                last_heard = time.time()
                self.handle(message)
                if self.replaced:
                    return True
        finally:
            stop_heartbeat.set()
            try:
                self.sock.close()
            except OSError:
                pass

    def _print_connack(self, connack: dict) -> None:
        name = console.paint(self.client_id, "bold")
        if connack.get("resumed"):
            console.log("CONNECTED", f"as {name}: resumed durable session, {connack['queued']} queued message(s) "
                                     f"waiting", "green")
            if connack.get("subscriptions"):
                console.log("", "durable subscriptions: " + ", ".join(connack["subscriptions"]), "green")
        else:
            console.log("CONNECTED", f"as {name}: new session", "green")

    # --- incoming frames ---------------------------------------------------------------
    def handle(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == protocol.DELIVER:
            self.on_deliver(message)
        elif mtype == protocol.SUBACK:
            note = "" if message.get("new", True) else " (already subscribed)"
            console.log("SUBSCRIBED", f"{message['topic']}{note}", "blue")
        elif mtype == protocol.UNSUBACK:
            console.log("UNSUBSCR", message["topic"], "blue")
        elif mtype == protocol.ERROR:
            console.log("ERROR", f"{message.get('code')}: {message.get('reason')}", "red")
            self.replaced = message.get("code") == protocol.REPLACED
        # PONG needs no action: receiving anything at all proves the broker is alive.

    def on_deliver(self, msg: dict) -> None:
        seq = msg["seq"]
        if self.processed.seen(seq, msg["published_at"]):
            console.log("DUPLICATE", f"#{seq} was already processed: ignored"
                                     f"{'' if self.no_ack else ', ACK sent again so the broker stops re-sending'}",
                        "magenta")
            if not self.no_ack:
                self.send({"type": protocol.ACK, "seq": seq})
            return

        if msg["redelivery"]:
            label, style = "REDELIVERY", "yellow"
        elif msg["published_at"] < self.connected_at:
            label, style = "CATCH-UP", "cyan"
        else:
            label, style = "LIVE", "green"
        age = time.time() - msg["published_at"]
        waited = f"  (queued {age:.0f}s ago)" if label == "CATCH-UP" else ""
        console.log(label, f"#{seq} {console.paint(msg['topic'], 'cyan')} from {msg['publisher']}{waited}",
                    "bold", style)
        payload = msg["payload"]
        if isinstance(payload, dict) and "title" in payload:
            print(console.indent(console.paint(str(payload["title"]), "bold")))
            if payload.get("body"):
                print(console.indent(str(payload["body"])))
        else:
            print(console.indent(json.dumps(payload, ensure_ascii=False)))

        # "Processing" is displaying it. Record it on disk BEFORE acknowledging.
        self.processed.add(seq, msg["published_at"])
        self.processed_this_run += 1
        if self.crash_after and self.processed_this_run >= self.crash_after:
            console.log("FAULT", f"--crash-after {self.crash_after}: exiting abruptly before ACKing #{seq}", "red")
            os._exit(3)  # no cleanup, no DISCONNECT: behaves like a crash
        if not self.no_ack:
            self.send({"type": protocol.ACK, "seq": seq})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pubsub.subscriber",
                                     description="DormCare Live Alerts subscriber.")
    parser.add_argument("--id", required=True, help="fixed client id, e.g. tenant-juan")
    parser.add_argument("--topics", default="", help="comma-separated topics to subscribe to")
    parser.add_argument("--unsubscribe", default="", help="comma-separated topics to unsubscribe from")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--state-dir", default=str(config.SUBSCRIBER_STATE_DIR),
                        help="folder for this subscriber's processed-message log")
    parser.add_argument("--no-reconnect", action="store_true", help="exit instead of reconnecting")
    parser.add_argument("--no-ack", action="store_true", help="FAULT INJECTION: never send ACKs")
    parser.add_argument("--crash-after", type=int, default=0, metavar="N",
                        help="FAULT INJECTION: exit abruptly after processing N messages, before the last ACK")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    try:
        protocol.validate_client_id(args.id)
        for topic in filter(None, (t.strip() for t in (args.topics + "," + args.unsubscribe).split(","))):
            protocol.validate_topic(topic)
    except ProtocolError as err:
        parser.error(err.reason)

    console.setup(color=not args.no_color, title=f"SUB {args.id}")
    subscriber = Subscriber(args)
    try:
        return subscriber.run()
    except KeyboardInterrupt:
        try:
            subscriber.send({"type": protocol.DISCONNECT})
        except (OSError, AttributeError):
            pass
        console.log("STOPPED", "disconnected (Ctrl+C)", "yellow")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
