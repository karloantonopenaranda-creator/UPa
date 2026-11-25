"""
Durable storage for the broker: persistent queues and durable subscriptions (Coulouris §6.4).

Everything lives in one SQLite file with three tables:

    messages       every accepted PUBLISH, numbered by a broker-wide sequence number `seq`
    subscriptions  durable subscriptions, one row per (client_id, topic)
    deliveries     the per-subscriber queues: one row means "message `seq` is still owed to `client_id`"

A message is owed to a subscriber from the moment it is published until that subscriber ACKs it.
Because the queues are rows in a file rather than Python lists, they survive the subscriber
going offline AND the broker process being killed.

Topic-based filtering (§6.3) is a single indexed lookup inside the publish transaction:
    SELECT client_id FROM subscriptions WHERE topic = ?
The message row and one delivery row per matching subscriber are written in the SAME
transaction, so after a crash either all of them exist or none do.

Thread safety: the broker uses one thread per connection, and a SQLite connection must not be
used by two threads at the same time, so every method holds `self._lock`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,  -- broker-wide sequence number
    topic        TEXT    NOT NULL,
    publisher_id TEXT    NOT NULL,
    pub_msg_id   TEXT    NOT NULL,                   -- the publisher's own id for this message
    payload      TEXT    NOT NULL,                   -- JSON
    published_at REAL    NOT NULL,                   -- Unix time the broker accepted it
    routed_to    INTEGER NOT NULL,                   -- how many subscribers it was queued for
    UNIQUE (publisher_id, pub_msg_id)                -- detects a publisher re-sending the same message
);

CREATE TABLE IF NOT EXISTS subscriptions (
    topic      TEXT NOT NULL,
    client_id  TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (topic, client_id)                   -- makes "who subscribes to this topic?" an index lookup
);
CREATE INDEX IF NOT EXISTS subscriptions_by_client ON subscriptions (client_id);

CREATE TABLE IF NOT EXISTS deliveries (
    client_id TEXT    NOT NULL,
    seq       INTEGER NOT NULL REFERENCES messages (seq),
    attempts  INTEGER NOT NULL DEFAULT 0,            -- how many times it has been sent
    last_sent REAL,                                  -- when it was last sent; NULL = not sent on this connection
    PRIMARY KEY (client_id, seq)                     -- per-subscriber FIFO order = ORDER BY seq
);
"""


@dataclass
class PublishResult:
    seq: int                # broker sequence number given to the message
    subscribers: list[str]  # clients it was queued for (empty when it was a duplicate)
    routed_to: int          # number of subscribers it was queued for
    duplicate: bool         # True if this (publisher, msg_id) was already stored


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # isolation_level=None: we issue BEGIN/COMMIT ourselves (see _transaction).
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # WAL + synchronous=NORMAL: every committed transaction survives the broker PROCESS crashing
        # (what we test and demo). Only a power cut could lose the last few commits;
        # synchronous=FULL would close that gap at the cost of a disk flush per message.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)

    @contextmanager
    def _transaction(self):
        """Run a block atomically: all of its writes are committed together or not at all."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- Subscriptions ------------------------------------------------------
    def add_subscription(self, client_id: str, topic: str) -> bool:
        """Register a durable subscription. Returns False if it already existed."""
        with self._transaction() as db:
            cur = db.execute("INSERT OR IGNORE INTO subscriptions (topic, client_id, created_at) VALUES (?, ?, ?)",
                             (topic, client_id, time.time()))
            return cur.rowcount == 1

    def remove_subscription(self, client_id: str, topic: str) -> bool:
        """Remove a subscription. Messages already queued for this client stay queued (they were
        published while the subscription existed, so we still owe them). Returns False if absent."""
        with self._transaction() as db:
            cur = db.execute("DELETE FROM subscriptions WHERE topic = ? AND client_id = ?", (topic, client_id))
            return cur.rowcount == 1

    def subscriptions_for(self, client_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT topic FROM subscriptions WHERE client_id = ? ORDER BY topic",
                                    (client_id,)).fetchall()
        return [row["topic"] for row in rows]

    def subscriber_count(self) -> int:
        """Number of distinct clients holding at least one subscription."""
        with self._lock:
            return self._db.execute("SELECT COUNT(DISTINCT client_id) FROM subscriptions").fetchone()[0]

    # --- Publishing (routing) ------------------------------------------------
    def publish(self, publisher_id: str, pub_msg_id: str, topic: str, payload: object) -> PublishResult:
        """Store a message and queue it for every current subscriber of `topic`, in one transaction.

        If the same publisher already sent a message with this msg_id (a retry after a lost PUBACK),
        nothing new is stored and the original sequence number is returned with duplicate=True.
        """
        with self._transaction() as db:
            existing = db.execute("SELECT seq, routed_to FROM messages WHERE publisher_id = ? AND pub_msg_id = ?",
                                  (publisher_id, pub_msg_id)).fetchone()
            if existing:
                return PublishResult(existing["seq"], [], existing["routed_to"], duplicate=True)

            # Topic-based filtering: exactly the clients subscribed to this topic name.
            subscribers = [row["client_id"] for row in
                           db.execute("SELECT client_id FROM subscriptions WHERE topic = ? ORDER BY client_id",
                                      (topic,))]
            cur = db.execute(
                "INSERT INTO messages (topic, publisher_id, pub_msg_id, payload, published_at, routed_to) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (topic, publisher_id, pub_msg_id, json.dumps(payload, ensure_ascii=False), time.time(),
                 len(subscribers)))
            seq = cur.lastrowid
            db.executemany("INSERT INTO deliveries (client_id, seq) VALUES (?, ?)",
                           [(client_id, seq) for client_id in subscribers])
            return PublishResult(seq, subscribers, len(subscribers), duplicate=False)

    # --- Per-subscriber queues -----------------------------------------------
    def pending(self, client_id: str, limit: int) -> list[dict]:
        """The oldest `limit` messages still owed to `client_id`, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT d.seq, d.attempts, d.last_sent, m.topic, m.publisher_id, m.payload, m.published_at "
                "FROM deliveries d JOIN messages m ON m.seq = d.seq "
                "WHERE d.client_id = ? ORDER BY d.seq LIMIT ?",
                (client_id, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def mark_sent(self, client_id: str, seq: int) -> None:
        with self._transaction() as db:
            db.execute("UPDATE deliveries SET attempts = attempts + 1, last_sent = ? WHERE client_id = ? AND seq = ?",
                       (time.time(), client_id, seq))

    def ack(self, client_id: str, seq: int) -> bool:
        """The subscriber has processed `seq`: remove it from its queue. Returns False if it was not queued
        (for example a second ACK for a message that was delivered twice)."""
        with self._transaction() as db:
            cur = db.execute("DELETE FROM deliveries WHERE client_id = ? AND seq = ?", (client_id, seq))
            return cur.rowcount == 1

    def reset_in_flight(self, client_id: str) -> int:
        """Called when a subscriber (re)connects: anything sent on an earlier connection but never ACKed
        must be sent again now, without waiting for the ACK timeout. Returns how many messages that is."""
        with self._transaction() as db:
            cur = db.execute("UPDATE deliveries SET last_sent = NULL WHERE client_id = ? AND last_sent IS NOT NULL",
                             (client_id,))
            return cur.rowcount

    def pending_count(self, client_id: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM deliveries WHERE client_id = ?", (client_id,)).fetchone()[0]

    # --- Reporting -------------------------------------------------------------
    def queue_report(self) -> dict[str, dict]:
        """{client_id: {"topics": [...], "pending": n}} for every client with a subscription or a queue."""
        report: dict[str, dict] = {}
        with self._lock:
            for row in self._db.execute("SELECT client_id, topic FROM subscriptions ORDER BY client_id, topic"):
                report.setdefault(row["client_id"], {"topics": [], "pending": 0})["topics"].append(row["topic"])
            for row in self._db.execute("SELECT client_id, COUNT(*) AS n FROM deliveries GROUP BY client_id"):
                report.setdefault(row["client_id"], {"topics": [], "pending": 0})["pending"] = row["n"]
        return report

    def stats(self) -> dict[str, int]:
        with self._lock:
            one = lambda sql: self._db.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "messages": one("SELECT COUNT(*) FROM messages"),
                "subscriptions": one("SELECT COUNT(*) FROM subscriptions"),
                "subscribers": one("SELECT COUNT(DISTINCT client_id) FROM subscriptions"),
                "pending": one("SELECT COUNT(*) FROM deliveries"),
            }
