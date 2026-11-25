"""
DormCare Live Alerts: publisher client.

Interactive mode (used in the live demo):
    python -m pubsub.publisher --id manager-office
        list                show this publisher's scripted DormCare events
        send 1              publish scripted event 1     (send all: publish every event)
        pub <topic> <title> [| <body>]
                            publish your own message, e.g.
                            pub casa-jaro.announcements Water interruption | No water 8 AM - 2 PM
        status              show every subscriber's queue on the broker
        help, quit

One-shot mode:
    python -m pubsub.publisher --id manager-office --topic casa-jaro.announcements --title "..." --body "..."

Scripted mode (all of this publisher's scenario events, one every --delay seconds):
    python -m pubsub.publisher --id maintenance-desk --run-scenario --delay 2

Queue report (connects, prints the broker's STATUS, exits):
    python -m pubsub.publisher --id status-check --status

Reliability
  Every PUBLISH carries a msg_id that is unique for this publisher. The broker sends PUBACK only after
  the message is safely stored. If no PUBACK arrives (timeout or lost connection), the publisher
  reconnects and sends the SAME msg_id again; the broker recognises the duplicate and does not route
  it twice. Together with subscriber ACKs this gives at-least-once delivery end to end. After the last retry
  the publisher reports failure; it keeps no outbox.
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import uuid
from pathlib import Path

from pubsub.common import config, console, protocol
from pubsub.common.protocol import ProtocolError


class PublishFailed(Exception):
    pass


class Publisher:
    def __init__(self, client_id: str, host: str, port: int, puback_timeout: float = config.PUBACK_TIMEOUT,
                 retries: int = config.PUBLISH_RETRIES):
        self.client_id = client_id
        self.host, self.port = host, port
        self.puback_timeout = puback_timeout
        self.retries = retries
        self.sock: socket.socket | None = None
        self.reader: protocol.FrameReader | None = None
        self.send_lock = threading.Lock()
        self.broken = threading.Event()        # set by the heartbeat thread if the connection dies
        self._stop_heartbeat = threading.Event()

    # --- connection management ------------------------------------------------------------
    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.reader = protocol.FrameReader(self.sock)
        self.broken.clear()
        self._send({"type": protocol.CONNECT, "client_id": self.client_id, "role": protocol.PUBLISHER})
        self._wait_for(protocol.CONNACK, timeout=5)
        self._stop_heartbeat = threading.Event()
        threading.Thread(target=self._heartbeat, args=(self._stop_heartbeat,), daemon=True).start()
        console.log("CONNECTED", f"publisher {console.paint(self.client_id, 'bold')} -> broker "
                                 f"{self.host}:{self.port}", "green")

    def close(self, graceful: bool = True) -> None:
        self._stop_heartbeat.set()
        if self.sock is not None:
            if graceful:
                try:
                    self._send({"type": protocol.DISCONNECT})
                except OSError:
                    pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = self.reader = None

    def _heartbeat(self, stop: threading.Event) -> None:
        """PING every HEARTBEAT_INTERVAL so the broker does not drop us while we sit idle at the prompt."""
        while not stop.wait(config.HEARTBEAT_INTERVAL):
            try:
                self._send({"type": protocol.PING})
            except (OSError, AttributeError):
                self.broken.set()
                return

    def _send(self, message: dict) -> None:
        protocol.send(self.sock, message, self.send_lock)

    def _wait_for(self, mtype: str, timeout: float, msg_id: str | None = None) -> dict:
        """Read frames until one of type `mtype` (and matching msg_id, if given) arrives.
        PONGs and stale replies are skipped. Raises TimeoutError, ConnectionError or ProtocolError."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"no {mtype} within {timeout:.0f}s")
            self.sock.settimeout(min(remaining, 0.5))
            try:
                message = self.reader.read()
            except TimeoutError:
                continue
            if message is None:
                raise ConnectionError("the broker closed the connection")
            if message["type"] == protocol.ERROR:
                raise ProtocolError(message.get("code", "?"), message.get("reason", ""))
            if message["type"] == mtype and (msg_id is None or message.get("msg_id") == msg_id):
                return message

    # --- publishing ---------------------------------------------------------------------------
    def publish(self, topic: str, payload: dict) -> dict:
        """Publish one message and return the broker's PUBACK. Retries with the same msg_id on failure."""
        protocol.validate_topic(topic)
        msg_id = f"{self.client_id}-{uuid.uuid4().hex[:16]}"  # 64 random bits: a repeat would be dropped as a duplicate
        frame = {"type": protocol.PUBLISH, "msg_id": msg_id, "topic": topic, "payload": payload}
        delay = 0.5
        for attempt in range(1, self.retries + 2):
            try:
                if self.sock is None or self.broken.is_set():
                    self.close(graceful=False)
                    self.connect()
                self._send(frame)
                puback = self._wait_for(protocol.PUBACK, self.puback_timeout, msg_id=msg_id)
                self._report(puback, topic)
                return puback
            except ProtocolError:
                raise  # the broker rejected the request itself (bad topic, wrong role, ...): retrying won't help
            except (TimeoutError, OSError, ConnectionError) as exc:
                if attempt > self.retries:
                    break
                console.log("RETRY", f"{msg_id}: {exc}. Reconnecting and re-sending the SAME msg_id "
                                     f"(attempt {attempt + 1})", "yellow")
                self.close(graceful=False)
                time.sleep(delay)
                delay = min(delay * 2, config.RECONNECT_MAX_DELAY)
        raise PublishFailed(f"gave up on {msg_id} after {self.retries + 1} attempts")

    @staticmethod
    def _report(puback: dict, topic: str) -> None:
        if puback.get("duplicate"):
            console.log("PUBACK", f"#{puback['seq']}: broker already had this message (duplicate detected, "
                                  f"not routed twice)", "magenta")
        else:
            console.log("PUBACK", f"#{puback['seq']} stored by broker -> queued for {puback['routed_to']} "
                                  f"subscriber(s) of {console.paint(topic, 'cyan')}", "green")

    # --- monitoring ---------------------------------------------------------------------------
    def status(self) -> dict:
        """The broker's queue report. Reconnects once, because the first use of a connection that died with
        the broker is often how we find out that it died. Raises OSError if the broker cannot be reached."""
        for attempt in (1, 2):
            try:
                if self.sock is None or self.broken.is_set():
                    self.close(graceful=False)
                    self.connect()
                self._send({"type": protocol.STATUS})
                return self._wait_for(protocol.STATUS_REPLY, timeout=5)
            except OSError:
                self.close(graceful=False)
                if attempt == 2:
                    raise


# --- scenario and output helpers --------------------------------------------------------------
def load_events(path: Path, publisher_id: str) -> list[dict]:
    """This publisher's events from the DormCare scenario file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data.get("events", []) if e.get("publisher") == publisher_id]


def event_payload(event: dict) -> dict:
    return {k: v for k, v in event.items() if k not in ("publisher", "topic")}


def print_status(reply: dict) -> None:
    clients = reply.get("clients", [])
    print(console.paint(f"  {'SUBSCRIBER':<16} {'STATE':<8} {'QUEUED':>6}  TOPICS", "bold"))
    for c in clients:
        state = console.paint("online ", "green") if c["online"] else console.paint("offline", "yellow")
        queued = console.paint(f"{c['pending']:>6}", "bold", "yellow") if c["pending"] else f"{c['pending']:>6}"
        print(f"  {c['client_id']:<16} {state:<8} {queued}  {', '.join(c['topics']) or '-'}")
    if not clients:
        print("  (no subscribers yet)")
    if reply.get("publishers_online"):
        print(f"  publishers online: {', '.join(reply['publishers_online'])}")


HELP = """Commands:
  list                                   show your scripted DormCare events
  send <n> | send all                    publish scripted event n (or every event)
  pub <topic> <title> [| <body>]         publish your own message
  status                                 show every subscriber's queue on the broker
  help                                   show this help
  quit                                   disconnect and exit"""


def interactive(pub: Publisher, events: list[dict]) -> None:
    print(HELP)
    while True:
        try:
            line = input(console.paint(f"{pub.client_id}> ", "bold")).strip()
        except EOFError:
            return
        if not line:
            continue
        command, _, rest = line.partition(" ")
        command = command.lower()
        try:
            if command in ("quit", "exit"):
                return
            elif command == "help":
                print(HELP)
            elif command == "list":
                if not events:
                    print("  (no scripted events for this publisher id)")
                for i, e in enumerate(events, 1):
                    print(f"  {i:>2}. {console.paint(e['topic'], 'cyan'):<40} {e.get('title', '')}")
            elif command == "send":
                if rest.strip() == "all":
                    chosen = events
                elif int(rest) < 1:
                    raise IndexError(rest)  # otherwise "send 0" would mean events[-1], the last event
                else:
                    chosen = [events[int(rest) - 1]]
                for e in chosen:
                    pub.publish(e["topic"], event_payload(e))
            elif command == "pub":
                topic, _, text = rest.strip().partition(" ")
                title, _, body = text.partition("|")
                if not topic or not title.strip():
                    print("  usage: pub <topic> <title> [| <body>]")
                    continue
                pub.publish(topic, {"title": title.strip(), "body": body.strip()})
            elif command == "status":
                print_status(pub.status())
            else:
                print("  unknown command; type help")
        except (ValueError, IndexError):
            print(f"  choose an event number between 1 and {len(events)} (see: list)")
        except ProtocolError as err:
            console.log("REJECTED", f"{err.code}: {err.reason}", "red")
        except PublishFailed as exc:
            console.log("FAILED", str(exc), "red")
        except OSError as exc:
            console.log("OFFLINE", f"cannot reach the broker ({exc}); is it running?", "red")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pubsub.publisher", description="DormCare Live Alerts publisher.")
    parser.add_argument("--id", required=True, help="fixed client id, e.g. manager-office")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--scenario", type=Path, default=config.SCENARIO_FILE, help="scenario JSON file")
    parser.add_argument("--topic", help="one-shot mode: topic to publish to")
    parser.add_argument("--title", default="", help="one-shot mode: message title")
    parser.add_argument("--body", default="", help="one-shot mode: message body")
    parser.add_argument("--run-scenario", action="store_true", help="publish all of this publisher's events")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between scripted events")
    parser.add_argument("--status", action="store_true", help="print the broker's queue report and exit")
    parser.add_argument("--puback-timeout", type=float, default=config.PUBACK_TIMEOUT)
    parser.add_argument("--retries", type=int, default=config.PUBLISH_RETRIES)
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    try:
        protocol.validate_client_id(args.id)
        if args.topic:
            protocol.validate_topic(args.topic)
    except ProtocolError as err:
        parser.error(err.reason)

    console.setup(color=not args.no_color, title=f"PUB {args.id}")
    pub = Publisher(args.id, args.host, args.port, args.puback_timeout, args.retries)
    try:
        if args.status:
            pub.connect()
            print_status(pub.status())
        elif args.topic:
            pub.publish(args.topic, {"title": args.title or "(no title)", "body": args.body})
        elif args.run_scenario:
            events = load_events(args.scenario, args.id)
            if not events:
                console.log("ERROR", f"no events for publisher '{args.id}' in {args.scenario}", "red")
                return 1
            for i, event in enumerate(events):
                if i:
                    time.sleep(args.delay)
                pub.publish(event["topic"], event_payload(event))
        else:
            try:
                pub.connect()
            except OSError as exc:
                console.log("WAITING", f"broker not reachable yet ({exc}); will connect on first publish", "yellow")
            interactive(pub, load_events(args.scenario, args.id))
        return 0
    except ConnectionRefusedError:
        console.log("ERROR", f"cannot reach the broker at {args.host}:{args.port}. Is it running?", "red")
        return 1
    except (PublishFailed, ProtocolError, OSError) as exc:
        console.log("FAILED", str(exc), "red")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        pub.close()


if __name__ == "__main__":
    raise SystemExit(main())
