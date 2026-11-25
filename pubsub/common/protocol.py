"""
Wire protocol shared by the broker, the publishers and the subscribers.

Framing
-------
TCP delivers a reliable, ordered *stream of bytes*, not separate messages (Coulouris §4.2.4).
One recv() call can return half a message, one message, or several at once. We therefore mark
message boundaries ourselves: every frame is ONE JSON object, encoded as UTF-8, followed by a
newline character. JSON escapes newlines inside strings, so a raw newline byte can only mean
"end of frame". Plain-text frames are easy to read in logs and can even be typed by hand.

    {"type":"PUBLISH","msg_id":"manager-office-1a2b3c4d","topic":"casa-jaro.announcements","payload":{...}}\\n

Message types
-------------
    Client -> broker                         Broker -> client
    CONNECT     client_id, role              CONNACK       client_id, resumed, queued, subscriptions
    SUBSCRIBE   topic                        SUBACK        topic, new
    UNSUBSCRIBE topic                        UNSUBACK      topic, removed
    PUBLISH     msg_id, topic, payload       PUBACK        msg_id, seq, routed_to, duplicate
    ACK         seq                          DELIVER       seq, topic, publisher, payload, published_at, redelivery
    PING                                     PONG
    STATUS                                   STATUS_REPLY  clients
    DISCONNECT                               ERROR         code, reason

Naming
------
    topic      lowercase words separated by dots, e.g. "casa-jaro.announcements"
    client_id  letters, digits, '.', '_' or '-', at most 64 characters, e.g. "tenant-juan"
"""
from __future__ import annotations

import json
import re
import socket
import threading

from pubsub.common.config import MAX_FRAME_BYTES

# --- Message types ----------------------------------------------------------
CONNECT, CONNACK = "CONNECT", "CONNACK"
SUBSCRIBE, SUBACK = "SUBSCRIBE", "SUBACK"
UNSUBSCRIBE, UNSUBACK = "UNSUBSCRIBE", "UNSUBACK"
PUBLISH, PUBACK = "PUBLISH", "PUBACK"
DELIVER, ACK = "DELIVER", "ACK"
PING, PONG = "PING", "PONG"
STATUS, STATUS_REPLY = "STATUS", "STATUS_REPLY"
DISCONNECT, ERROR = "DISCONNECT", "ERROR"

# Fields every frame of a given type must carry. Extra fields are allowed and ignored.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    # sent by clients
    CONNECT: ("client_id", "role"),
    SUBSCRIBE: ("topic",),
    UNSUBSCRIBE: ("topic",),
    PUBLISH: ("msg_id", "topic", "payload"),
    ACK: ("seq",),
    PING: (),
    STATUS: (),
    DISCONNECT: (),
    # sent by the broker
    CONNACK: ("client_id", "resumed", "queued"),
    SUBACK: ("topic",),
    UNSUBACK: ("topic",),
    PUBACK: ("msg_id", "seq", "routed_to"),
    DELIVER: ("seq", "topic", "publisher", "payload", "published_at", "redelivery"),
    PONG: (),
    STATUS_REPLY: ("clients",),
    ERROR: ("code", "reason"),
}
CLIENT_TYPES = frozenset({CONNECT, SUBSCRIBE, UNSUBSCRIBE, PUBLISH, ACK, PING, STATUS, DISCONNECT})
BROKER_TYPES = frozenset(REQUIRED_FIELDS) - CLIENT_TYPES

PUBLISHER, SUBSCRIBER = "publisher", "subscriber"
ROLES = (PUBLISHER, SUBSCRIBER)

# --- Error codes sent in ERROR frames ---------------------------------------
BAD_FRAME = "BAD_FRAME"              # not UTF-8 JSON, or not an object with a "type"
BAD_REQUEST = "BAD_REQUEST"          # missing or invalid field
UNKNOWN_TYPE = "UNKNOWN_TYPE"        # "type" is not a client message type
NOT_CONNECTED = "NOT_CONNECTED"      # first frame was not CONNECT
ROLE = "ROLE"                        # e.g. a subscriber tried to PUBLISH
FRAME_TOO_LARGE = "FRAME_TOO_LARGE"  # frame exceeds MAX_FRAME_BYTES (connection is closed)
REPLACED = "REPLACED"                # another connection logged in with the same client_id

# Always used with fullmatch(): with match(), "$" would also accept a name that ends in a newline.
_TOPIC_RE = re.compile(r"[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*")
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_REASON_CHARS = 300  # reasons quote the bad value, which could itself be almost a whole frame


class ProtocolError(Exception):
    """A frame broke the protocol. `code` is one of the error codes above."""

    def __init__(self, code: str, reason: str):
        reason = reason[:MAX_REASON_CHARS]
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


# --- Validation -------------------------------------------------------------
def validate_topic(topic: object) -> str:
    if not isinstance(topic, str) or len(topic) > 128 or not _TOPIC_RE.fullmatch(topic):
        raise ProtocolError(BAD_REQUEST, f"invalid topic {topic!r}: use lowercase words separated "
                                         f"by dots, e.g. casa-jaro.announcements")
    return topic


def validate_client_id(client_id: object) -> str:
    if not isinstance(client_id, str) or not _CLIENT_ID_RE.fullmatch(client_id):
        raise ProtocolError(BAD_REQUEST, f"invalid client_id {client_id!r}: use 1-64 letters, digits, "
                                         f"'.', '_' or '-'")
    return client_id


def validate(message: dict, allowed_types: frozenset[str] | None = None) -> dict:
    """Check the frame's type is known (and allowed here) and that its required fields are present."""
    mtype = message["type"]
    if mtype not in REQUIRED_FIELDS or (allowed_types is not None and mtype not in allowed_types):
        raise ProtocolError(UNKNOWN_TYPE, f"unknown message type {mtype!r}")
    missing = [field for field in REQUIRED_FIELDS[mtype] if field not in message]
    if missing:
        raise ProtocolError(BAD_REQUEST, f"{mtype} is missing field(s): {', '.join(missing)}")
    return message


def error_frame(code: str, reason: str) -> dict:
    return {"type": ERROR, "code": code, "reason": reason}


# --- Encoding / decoding ----------------------------------------------------
def encode(message: dict) -> bytes:
    """Dictionary -> one newline-terminated UTF-8 JSON frame."""
    try:
        data = (json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (ValueError, RecursionError) as exc:  # infinity, a lone surrogate, or nesting that is too deep
        raise ProtocolError(BAD_REQUEST, f"cannot be sent as UTF-8 JSON ({exc})") from None
    if len(data) > MAX_FRAME_BYTES:
        raise ProtocolError(FRAME_TOO_LARGE, f"frame is {len(data)} bytes; the limit is {MAX_FRAME_BYTES}")
    return data


def decode(line: bytes) -> dict:
    """One frame (without its newline) -> dictionary. Raises ProtocolError if it is not valid."""
    try:
        message = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError) as exc:  # bad UTF-8 or JSON, a huge integer, extreme nesting
        raise ProtocolError(BAD_FRAME, f"frame is not valid UTF-8 JSON ({exc})") from None
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError(BAD_FRAME, "a frame must be a JSON object with a string 'type' field")
    return message


def send(sock: socket.socket, message: dict, lock: threading.Lock | None = None) -> None:
    """Encode and send one frame. Pass the connection's send lock when several threads share a socket,
    otherwise two frames could be interleaved byte-by-byte and corrupt the stream."""
    data = encode(message)
    if lock is None:
        sock.sendall(data)
    else:
        with lock:
            sock.sendall(data)


class FrameReader:
    """Rebuilds frames from a TCP byte stream.

    Bytes are buffered until a newline arrives, so a frame split across several recv() calls,
    or several frames arriving in one recv(), are both handled. A multi-byte UTF-8 character
    split across two reads is fine too, because we only decode complete lines.
    """

    def __init__(self, sock: socket.socket, max_frame: int = MAX_FRAME_BYTES):
        self.sock = sock
        self.max_frame = max_frame
        self.buffer = bytearray()

    def read(self) -> dict | None:
        """Return the next frame, or None when the peer has closed the connection.

        If the socket has a timeout and no complete frame arrives in time, TimeoutError is raised;
        any partial frame stays buffered, so read() can simply be called again.
        Raises ProtocolError for a malformed frame (the stream itself is still usable afterwards)
        or an oversized one (the caller should close the connection).
        """
        while True:
            newline = self.buffer.find(b"\n")
            if newline != -1:
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                if len(line) > self.max_frame:
                    raise ProtocolError(FRAME_TOO_LARGE, f"frame exceeds {self.max_frame} bytes")
                if not line.strip():
                    continue  # ignore blank lines, e.g. an extra Enter when typing by hand
                return decode(line)
            if len(self.buffer) > self.max_frame:
                raise ProtocolError(FRAME_TOO_LARGE, f"frame exceeds {self.max_frame} bytes")
            chunk = self.sock.recv(4096)
            if not chunk:
                return None  # orderly close by the peer (any partial frame is discarded)
            self.buffer.extend(chunk)
