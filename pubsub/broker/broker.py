"""
DormCare Live Alerts: the message broker.

    python -m pubsub.broker                    (defaults: 127.0.0.1:5555, data/broker.db)
    python -m pubsub.broker --help             (all options)

What the broker does
  * Accepts TCP connections from publishers and subscribers, one thread per connection (§7.4).
  * Keeps durable subscriptions and per-subscriber queues in SQLite (store.py, §6.4).
  * Routes every PUBLISH to the subscribers of its topic: topic-based filtering (§6.3).
  * Delivers at-least-once: a message stays in a subscriber's queue until that subscriber ACKs it,
    and is re-sent after ACK_TIMEOUT or as soon as the subscriber reconnects.
  * Detects failed clients: a closed or reset socket immediately, a silent one by heartbeat timeout.

The single delivery path
  Publishing never pushes a message straight to a subscriber's socket. It only adds the message to
  each subscriber's durable queue and wakes that subscriber's delivery thread. The delivery thread
  always sends from the queue, oldest first. So a reconnecting subscriber receives its backlog first
  and in order, and new messages simply join the end of the same queue, with no special cases.

Fault injection (for tests and the demo)
  --fault-skip-puback N   store the first N published messages but "lose" their PUBACK, so the
                          publisher's timeout-and-retry and the broker's duplicate detection can be shown.
"""
from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pubsub.common import config, console, protocol
from pubsub.common.protocol import ProtocolError
from pubsub.broker.registry import SessionRegistry
from pubsub.broker.store import Store


@dataclass
class BrokerSettings:
    host: str = config.HOST
    port: int = config.PORT
    db_path: Path = config.BROKER_DB
    ack_timeout: float = config.ACK_TIMEOUT
    heartbeat_timeout: float = config.HEARTBEAT_TIMEOUT
    connect_timeout: float = config.CONNECT_TIMEOUT
    window: int = config.DELIVERY_WINDOW
    quiet: bool = False             # hide per-message DELIVER / ACK lines
    fault_skip_puback: int = 0      # see module docstring


MAX_SEQ = 2 ** 63 - 1                     # largest SQLite INTEGER
LONGEST_TIME = -1.7976931348623157e308    # the float with the longest JSON form
MAX_PAYLOAD_DEPTH = 64                    # nested lists/objects allowed in a payload
STATUS_TOPICS_PER_CLIENT = 20             # STATUS lists at most this many topics per client
STATUS_PUBLISHERS = 100                   # and at most this many online publishers


def _deliver_frame(seq: int, topic: str, publisher: str, payload: object, published_at: float,
                   attempts: int) -> dict:
    return {"type": protocol.DELIVER, "seq": seq, "topic": topic, "publisher": publisher, "payload": payload,
            "published_at": published_at, "redelivery": attempts > 0, "attempt": attempts + 1}


def _check_deliverable(publisher: str, msg_id: str, topic: str, payload: object) -> None:
    """Refuse, before it is stored and confirmed, a message that could never be delivered.

    DELIVER has more fields than PUBLISH, and re-encoding can make a payload longer (1E15 becomes
    1000000000000000.0), so the size limit on the incoming frame is not enough. We encode the largest
    DELIVER frame this message could ever become. This is what makes "PUBACK means it will be delivered" true.
    """
    level, depth = [payload], 0
    while level:  # level by level rather than recursively, so this check cannot itself overflow the stack
        level = [child for node in level if isinstance(node, (dict, list))
                 for child in (node.values() if isinstance(node, dict) else node)]
        depth += 1
        if depth > MAX_PAYLOAD_DEPTH:
            raise ProtocolError(protocol.BAD_REQUEST, f"payload is nested more than {MAX_PAYLOAD_DEPTH} levels deep")
    largest = {**_deliver_frame(MAX_SEQ, topic, publisher, payload, LONGEST_TIME, MAX_SEQ), "redelivery": False}
    try:
        protocol.encode(largest)
        protocol.encode({"type": protocol.PUBACK, "msg_id": msg_id})
    except ProtocolError as err:
        # Not FRAME_TOO_LARGE: that code means "connection closed", and this connection is still fine.
        raise ProtocolError(protocol.BAD_REQUEST, f"message could not be delivered: {err.reason}") from None


def _fit(frame: dict, key: str) -> dict:
    """Shorten the list frame[key] until the frame is within the size limit, and flag it as truncated."""
    while True:
        try:
            protocol.encode(frame)
            return frame
        except ProtocolError:
            if not frame[key]:
                raise
            frame = {**frame, key: frame[key][:len(frame[key]) // 2], "truncated": True}


def _title(payload: object) -> str:
    """Short description of a payload for log lines."""
    if isinstance(payload, dict) and payload.get("title"):
        text = str(payload["title"])
        return f'"{text[:60]}{"..." if len(text) > 60 else ""}"'
    return ""


class ClientSession:
    """One TCP connection from a publisher or subscriber. `run()` executes in its own thread."""

    def __init__(self, broker: "Broker", sock: socket.socket, address: tuple):
        self.broker = broker
        self.sock = sock
        self.address = f"{address[0]}:{address[1]}"
        self.client_id: str | None = None
        self.role: str | None = None
        self.reader = protocol.FrameReader(sock)
        self.send_lock = threading.Lock()   # the reader thread and the delivery thread both send
        self.closed = threading.Event()
        self.wakeup = threading.Event()     # set when this subscriber has new messages queued
        self.close_reason = "connection closed by client"
        self._close_lock = threading.Lock()

    # --- sending / closing ---------------------------------------------------
    def send(self, message: dict) -> bool:
        """Send one frame; on failure close the session. Returns True if it was sent."""
        if self.closed.is_set():
            return False
        try:
            protocol.send(self.sock, message, self.send_lock)
            return True
        except OSError:
            self.close("connection lost while sending")
            return False

    def close(self, reason: str) -> None:
        with self._close_lock:
            if self.closed.is_set():
                return
            self.close_reason = reason
            self.closed.set()
        self.wakeup.set()  # let the delivery thread notice and exit
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    # --- receiving -------------------------------------------------------------
    def _read_frame(self, waiting_for: str) -> dict | None:
        """Read and validate the next client frame. Returns None when the connection should end."""
        while not self.closed.is_set():
            try:
                message = self.reader.read()
                if message is None:
                    self.close("connection closed by client")
                    return None
                return protocol.validate(message, protocol.CLIENT_TYPES)
            except TimeoutError:
                self.close(f"no {waiting_for} for {self.sock.gettimeout():.0f}s")
                return None
            except ProtocolError as err:
                self.broker.log_error(self, err)
                self.send(protocol.error_frame(err.code, err.reason))
                if err.code == protocol.FRAME_TOO_LARGE:
                    self.close("frame too large")
                    return None
                # Malformed frame: the newline framing is still intact, so skip it and carry on.
            except OSError:
                # e.g. WinError 10054 "connection forcibly closed" when the client process is killed
                self.close("connection reset (client process ended abruptly)")
                return None
        return None

    def run(self) -> None:
        try:
            self.sock.settimeout(self.broker.settings.connect_timeout)
            first = self._read_frame("CONNECT")
            if first is None:
                return
            if first["type"] != protocol.CONNECT:
                self.send(protocol.error_frame(protocol.NOT_CONNECTED, "the first frame must be CONNECT"))
                self.close("did not start with CONNECT")
                return
            if not self.broker.handle_connect(self, first):
                return
            # From now on, a client that stays silent longer than the heartbeat timeout is presumed dead.
            self.sock.settimeout(self.broker.settings.heartbeat_timeout)
            while not self.closed.is_set():
                message = self._read_frame("heartbeat")
                if message is None:
                    break
                self.broker.dispatch(self, message)
        finally:
            self.close(self.close_reason)
            self.broker.on_session_end(self)


class Broker:
    def __init__(self, settings: BrokerSettings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.sessions = SessionRegistry()
        self._running = threading.Event()
        self._skip_pubacks = settings.fault_skip_puback
        self._fault_lock = threading.Lock()
        self._handlers = {
            protocol.SUBSCRIBE: self.handle_subscribe,
            protocol.UNSUBSCRIBE: self.handle_unsubscribe,
            protocol.PUBLISH: self.handle_publish,
            protocol.ACK: self.handle_ack,
            protocol.PING: self.handle_ping,
            protocol.STATUS: self.handle_status,
            protocol.DISCONNECT: self.handle_disconnect,
            protocol.CONNECT: self.handle_second_connect,
        }

    # --- logging ---------------------------------------------------------------
    def verbose(self, tag: str, message: str, *styles: str) -> None:
        if not self.settings.quiet:
            console.log(tag, message, *styles)

    def log_error(self, session: ClientSession, err: ProtocolError) -> None:
        console.log("ERROR", f"{session.client_id or session.address}: {err.code}: {err.reason}", "red")

    # --- accept loop -------------------------------------------------------------
    def serve_forever(self) -> None:
        s = self.settings
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            # On Windows SO_REUSEADDR would let a SECOND broker bind the same port; forbid that instead.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((s.host, s.port))
        except OSError as exc:
            listener.close()
            raise SystemExit(f"Cannot listen on {s.host}:{s.port} ({exc}). Is another broker already running?")
        listener.listen()
        # accept() is given a 1-second timeout so Ctrl+C is noticed promptly on Windows.
        listener.settimeout(1.0)

        stats = self.store.stats()
        console.log("LISTEN", f"Broker listening on {s.host}:{s.port}  (store: {s.db_path})", "bold", "green")
        console.log("RECOVER", f"{stats['subscriptions']} durable subscription(s) for {stats['subscribers']} "
                               f"client(s), {stats['pending']} message(s) waiting in queues, "
                               f"{stats['messages']} message(s) in the log", "green")
        self._running.set()
        try:
            while self._running.is_set():
                try:
                    conn, address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if not self._running.is_set():
                        break
                    raise
                session = ClientSession(self, conn, address)
                threading.Thread(target=session.run, name=f"conn-{address[1]}", daemon=True).start()
        except KeyboardInterrupt:
            console.log("SHUTDOWN", "Ctrl+C received, closing all connections", "yellow")
        finally:
            self._running.clear()
            listener.close()
            for session in self.sessions.all():
                session.close("broker shutting down")
            time.sleep(0.2)
            self.store.close()

    def stop(self) -> None:
        self._running.clear()

    # --- connection lifecycle --------------------------------------------------------
    def handle_connect(self, session: ClientSession, msg: dict) -> bool:
        try:
            client_id = protocol.validate_client_id(msg["client_id"])
            if msg["role"] not in protocol.ROLES:
                raise ProtocolError(protocol.BAD_REQUEST, f"role must be one of {protocol.ROLES}")
        except ProtocolError as err:
            self.log_error(session, err)
            session.send(protocol.error_frame(err.code, err.reason))
            session.close("invalid CONNECT")
            return False

        session.client_id, session.role = client_id, msg["role"]
        replaced = self.sessions.attach(client_id, session)
        if replaced is not None:
            replaced.send(protocol.error_frame(protocol.REPLACED, "another connection logged in with your client_id"))
            replaced.close("replaced by a newer connection with the same client_id")
            console.log("REPLACED", f"{client_id}: older connection {replaced.address} closed, newer one wins", "yellow")

        topics = self.store.subscriptions_for(client_id)
        queued = self.store.pending_count(client_id)
        if session.role == protocol.SUBSCRIBER:
            self.store.reset_in_flight(client_id)  # re-send anything that was never ACKed, right away
        session.send(_fit({"type": protocol.CONNACK, "client_id": client_id, "role": session.role,
                           "resumed": bool(topics or queued), "queued": queued, "subscriptions": topics},
                          "subscriptions"))

        if session.role == protocol.SUBSCRIBER:
            detail = (f"resumed session, {len(topics)} durable subscription(s), {queued} message(s) waiting"
                      if topics or queued else "new session")
            console.log("ONLINE", f"subscriber {console.paint(client_id, 'bold')} from {session.address}: {detail}",
                        "green")
            threading.Thread(target=self.delivery_loop, args=(session,), name=f"deliver-{client_id}",
                             daemon=True).start()
        else:
            console.log("ONLINE", f"publisher {console.paint(client_id, 'bold')} from {session.address}", "green")
        return True

    def on_session_end(self, session: ClientSession) -> None:
        if session.client_id is None:
            return  # never completed CONNECT
        if not self.sessions.detach(session.client_id, session):
            return  # this connection had already been replaced by a newer one
        if session.role == protocol.SUBSCRIBER:
            waiting = self.store.pending_count(session.client_id)
            console.log("OFFLINE", f"subscriber {console.paint(session.client_id, 'bold')}: {session.close_reason}. "
                                   f"{waiting} message(s) waiting; new messages will be queued until it reconnects",
                        "yellow")
        else:
            console.log("OFFLINE", f"publisher {session.client_id}: {session.close_reason}", "yellow")

    def dispatch(self, session: ClientSession, msg: dict) -> None:
        try:
            self._handlers[msg["type"]](session, msg)
        except ProtocolError as err:
            self.log_error(session, err)
            session.send(protocol.error_frame(err.code, err.reason))

    @staticmethod
    def _require_role(session: ClientSession, role: str, action: str) -> None:
        if session.role != role:
            raise ProtocolError(protocol.ROLE, f"only a {role} may {action} (you connected as a {session.role})")

    def handle_second_connect(self, session: ClientSession, msg: dict) -> None:
        raise ProtocolError(protocol.BAD_REQUEST, "already connected; CONNECT may only be sent once")

    def handle_disconnect(self, session: ClientSession, msg: dict) -> None:
        session.close("client disconnected gracefully")

    def handle_ping(self, session: ClientSession, msg: dict) -> None:
        session.send({"type": protocol.PONG})

    # --- subscriptions -------------------------------------------------------------------
    def handle_subscribe(self, session: ClientSession, msg: dict) -> None:
        self._require_role(session, protocol.SUBSCRIBER, "subscribe")
        topic = protocol.validate_topic(msg["topic"])
        new = self.store.add_subscription(session.client_id, topic)
        session.send({"type": protocol.SUBACK, "topic": topic, "new": new})
        console.log("SUBSCRIBE", f"{session.client_id} -> {console.paint(topic, 'cyan')}"
                                 f"{'' if new else '  (already subscribed: durable subscription kept)'}", "blue")

    def handle_unsubscribe(self, session: ClientSession, msg: dict) -> None:
        self._require_role(session, protocol.SUBSCRIBER, "unsubscribe")
        topic = protocol.validate_topic(msg["topic"])
        removed = self.store.remove_subscription(session.client_id, topic)
        session.send({"type": protocol.UNSUBACK, "topic": topic, "removed": removed})
        console.log("UNSUBSCR", f"{session.client_id} -x {topic}{'' if removed else '  (was not subscribed)'}", "blue")

    # --- publishing and routing -------------------------------------------------------------
    def handle_publish(self, session: ClientSession, msg: dict) -> None:
        self._require_role(session, protocol.PUBLISHER, "publish")
        topic = protocol.validate_topic(msg["topic"])
        msg_id = msg["msg_id"]
        if not isinstance(msg_id, str) or not 0 < len(msg_id) <= 128:
            raise ProtocolError(protocol.BAD_REQUEST, "msg_id must be a non-empty string of at most 128 characters")

        _check_deliverable(session.client_id, msg_id, topic, msg["payload"])

        # 1. Store the message and queue it for every subscriber of the topic (one atomic transaction).
        result = self.store.publish(session.client_id, msg_id, topic, msg["payload"])

        if result.duplicate:
            console.log("DUPLICATE", f"{session.client_id} re-sent {msg_id}; already stored as #{result.seq}, "
                                     f"not routed again", "magenta")
        else:
            total = self.store.subscriber_count()
            if result.subscribers:
                online = self.sessions.online()
                who = ", ".join(f"{c} ({'online' if online.get(c) == protocol.SUBSCRIBER else 'offline: queued'})"
                                for c in result.subscribers)
                route = f"{len(result.subscribers)} of {total} subscriber(s): {who}"
            else:
                route = f"0 of {total} subscriber(s): nobody subscribes to this topic (message logged only)"
            console.log("PUBLISH", f"#{result.seq} {console.paint(topic, 'cyan')} from {session.client_id} "
                                   f"{_title(msg['payload'])}\n{' ' * 20}-> {route}", "bold", "magenta")

        # 2. Only now, with the message safely stored, confirm it to the publisher.
        if self._should_skip_puback():
            console.log("FAULT", f"injected fault: #{result.seq} is stored but its PUBACK is being dropped", "red")
        else:
            session.send({"type": protocol.PUBACK, "msg_id": msg_id, "seq": result.seq,
                          "routed_to": result.routed_to, "duplicate": result.duplicate})

        # 3. Wake the delivery thread of every subscriber that is online; offline ones keep it queued.
        for client_id in result.subscribers:
            subscriber = self.sessions.get(client_id)
            if subscriber is not None and subscriber.role == protocol.SUBSCRIBER:
                subscriber.wakeup.set()

    def _should_skip_puback(self) -> bool:
        with self._fault_lock:
            if self._skip_pubacks > 0:
                self._skip_pubacks -= 1
                return True
            return False

    # --- delivery -----------------------------------------------------------------------------
    def delivery_loop(self, session: ClientSession) -> None:
        """Send queued messages to one connected subscriber, oldest first (the single delivery path).

        Only the oldest `window` queued messages are eligible at any time, which bounds how many are
        in flight. A message that was sent but not ACKed within ack_timeout is sent again.
        """
        s = self.settings
        client_id = session.client_id
        unsendable: set[int] = set()
        while not session.closed.is_set():
            session.wakeup.clear()  # cleared BEFORE reading the queue, so no wake-up can be missed
            now = time.time()
            for item in self.store.pending(client_id, s.window):
                if session.closed.is_set():
                    return
                if item["last_sent"] is not None and now - item["last_sent"] < s.ack_timeout:
                    continue  # sent recently; still waiting for its ACK
                redelivery = item["attempts"] > 0
                frame = _deliver_frame(item["seq"], item["topic"], item["publisher_id"], item["payload"],
                                       item["published_at"], item["attempts"])
                try:
                    if not session.send(frame):
                        return
                except ProtocolError as err:
                    # handle_publish refuses such messages now, but a database from an older version may hold one.
                    # It stays queued, and must not stop the messages behind it from being delivered.
                    if item["seq"] not in unsendable:
                        unsendable.add(item["seq"])
                        console.log("ERROR", f"#{item['seq']} cannot be sent to {client_id}, skipping it: {err.reason}",
                                    "red")
                    continue
                self.store.mark_sent(client_id, item["seq"])
                if redelivery:
                    console.log("REDELIVER", f"#{item['seq']} -> {client_id} (attempt {item['attempts'] + 1}: "
                                             f"earlier copy was never ACKed)", "yellow")
                else:
                    self.verbose("DELIVER", f"#{item['seq']} -> {client_id}", "grey")
            # Sleep until something new is queued, or briefly, to check ACK timeouts.
            session.wakeup.wait(timeout=min(1.0, s.ack_timeout / 2))

    def handle_ack(self, session: ClientSession, msg: dict) -> None:
        self._require_role(session, protocol.SUBSCRIBER, "ACK")
        seq = msg["seq"]
        if type(seq) is not int or not 0 < seq <= MAX_SEQ:
            raise ProtocolError(protocol.BAD_REQUEST, "seq must be a positive 64-bit integer")
        if self.store.ack(session.client_id, seq):
            if not self.settings.quiet:
                left = self.store.pending_count(session.client_id)
                console.log("ACK", f"#{seq} <- {session.client_id} (removed from its queue; {left} left)", "grey")
            session.wakeup.set()  # a slot in the delivery window just opened
        else:
            self.verbose("ACK", f"#{seq} <- {session.client_id} (already ACKed earlier: duplicate ACK ignored)", "grey")

    # --- monitoring -----------------------------------------------------------------------------
    def handle_status(self, session: ClientSession, msg: dict) -> None:
        report = self.store.queue_report()
        online = self.sessions.online()
        clients = [{"client_id": client_id, "online": online.get(client_id) == protocol.SUBSCRIBER,
                    "topics": info["topics"][:STATUS_TOPICS_PER_CLIENT], "pending": info["pending"]}
                   for client_id, info in sorted(report.items())]
        publishers = sorted(c for c, role in online.items() if role == protocol.PUBLISHER)
        reply = {"type": protocol.STATUS_REPLY, "clients": clients, "publishers_online": publishers[:STATUS_PUBLISHERS]}
        if (len(publishers) > STATUS_PUBLISHERS
                or any(len(info["topics"]) > STATUS_TOPICS_PER_CLIENT for info in report.values())):
            reply["truncated"] = True
        session.send(_fit(reply, "clients"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pubsub.broker",
                                     description="DormCare Live Alerts publish-subscribe broker.")
    parser.add_argument("--host", default=config.HOST, help=f"address to listen on (default {config.HOST})")
    parser.add_argument("--port", type=int, default=config.PORT, help=f"TCP port (default {config.PORT})")
    parser.add_argument("--db", type=Path, default=config.BROKER_DB, help="SQLite file for durable queues")
    parser.add_argument("--ack-timeout", type=float, default=config.ACK_TIMEOUT,
                        help="seconds before an un-ACKed message is re-sent")
    parser.add_argument("--heartbeat-timeout", type=float, default=config.HEARTBEAT_TIMEOUT,
                        help="seconds of silence before a client is presumed dead")
    parser.add_argument("--window", type=int, default=config.DELIVERY_WINDOW,
                        help="maximum un-ACKed messages in flight per subscriber")
    parser.add_argument("--quiet", action="store_true", help="hide per-message DELIVER/ACK lines")
    parser.add_argument("--no-color", action="store_true", help="plain output without colours")
    parser.add_argument("--fault-skip-puback", type=int, default=0, metavar="N",
                        help="FAULT INJECTION: store the first N messages but drop their PUBACK")
    args = parser.parse_args(argv)

    console.setup(color=not args.no_color, title="BROKER")
    settings = BrokerSettings(host=args.host, port=args.port, db_path=args.db, ack_timeout=args.ack_timeout,
                              heartbeat_timeout=args.heartbeat_timeout, window=args.window, quiet=args.quiet,
                              fault_skip_puback=args.fault_skip_puback)
    Broker(settings).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
