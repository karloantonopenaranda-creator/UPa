# UPa / DormCare — Parallel & Distributed Computing Concepts

November 25, 2026

## Overview

DormCare Live Alerts, the messaging layer behind the UPa website, is a broker-based publish-subscribe system. Each part runs as its own operating-system process and talks to the others only by sending messages over TCP.

- **Publishers** (the manager's office, the maintenance desk) send announcements, billing notices and maintenance updates.
- **The broker** (`pubsub/broker/`) receives every message, stores it in SQLite, and routes it to each subscriber of its topic.
- **Subscribers** (tenants such as Juan and Jerome, and the caretaker) receive only the topics they subscribed to.
- **The UPa website** (`pubsub/webapp/`) connects to the same broker, so in-app notifications travel through it too.

Messages are JSON lines over TCP (default port 5555). One message travels as **PUBLISH → PUBACK → DELIVER → ACK**.

![System architecture: two publishers send PUBLISH to the broker and get PUBACK; the broker, with a thread per connection, a delivery thread per online subscriber and a SQLite store, sends DELIVER to three subscribers and gets ACK; the website gateway connects to the broker and serves browser tabs over HTTP POST and a server-sent events stream](images/upa_architecture.png)

Publishers and subscribers never connect to each other. The broker stores each message in SQLite first, then delivers it to every subscriber of its topic.

## Q1: Concepts we used

The project uses 13 parallel and distributed computing concepts: 3 parallel (work happening at the same time inside one program), 8 distributed (separate programs cooperating over a network), and 2 that are both.

| # | Concept | Type | Part of the program |
| --- | --- | --- | --- |
| 1 | Independent processes running in parallel | Parallel + distributed | Broker, publishers, subscribers, website, dashboard |
| 2 | Message passing over TCP sockets | Distributed | Wire protocol (`pubsub/common/protocol.py`) |
| 3 | Publish-subscribe with topic-based filtering | Distributed | Broker routing (`broker.py`, `store.py`) |
| 4 | Multithreading | Parallel | Broker, clients, website gateway |
| 5 | Synchronization: locks and atomic transactions | Parallel | Broker store, session registry, every socket send |
| 6 | Producer-consumer coordination | Parallel | Broker delivery threads, website live stream |
| 7 | Persistent queues and durable subscriptions | Distributed | Broker store (SQLite) |
| 8 | Reliable at-least-once delivery | Distributed | Broker delivery loop, publisher retry, subscriber ACK |
| 9 | Duplicate detection (idempotent processing) | Distributed | Broker store, subscriber log, browser |
| 10 | Failure detection: heartbeats and timeouts | Distributed | Broker sessions, all clients |
| 11 | Fault tolerance and recovery | Distributed | Subscriber and publisher reconnect, session registry |
| 12 | Flow control: delivery window | Distributed | Broker delivery loop |
| 13 | Web gateway with server-sent events | Parallel + distributed | Website server (`pubsub/webapp/server.py`) |

The code comments tie several of these to the course textbook (Coulouris): publish-subscribe §6.3, persistent queues §6.4, time uncoupling §6.1, thread-per-connection §7.4 and TCP streams §4.2.4.

## Q2: Where each concept occurs

Each concept below names the part of the program it lives in, the exact code (file and line numbers), and what it does for UPa.

### 1. Independent processes running in parallel

**Part of the program:** the whole system. The broker, each publisher, each subscriber and the website are separate operating-system processes. `scripts/start_demo.bat` opens six of them at once (1 broker, 3 subscribers, 2 publishers), and the dashboard starts them for you.

**Code:** `pubsub/dashboard/server.py`, lines 104–118 (`ManagedProcess.start`)

```python
self.proc = subprocess.Popen([sys.executable, *args], cwd=config.PROJECT_ROOT, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", creationflags=flags)
# If this program is closed or crashes, the broker/subscriber it started must not linger unseen.
children.end_with_this_process(self.proc)
```

`start_demo` (lines 215–230) uses this to start the broker, wait until its port is open, then start every subscriber.

**What it does for UPa:** each part runs at the same time with its own memory and CPU time. One part can crash without taking the others down, which is what makes the crash-and-recover demos possible. Started with `--host 0.0.0.0` (see `pubsub/common/config.py`), the broker also accepts clients from other machines.

### 2. Message passing over TCP sockets

**Part of the program:** the wire protocol that the broker, publishers, subscribers and website all share. The processes share no memory, so every interaction is a message: 8 types a client sends (CONNECT, SUBSCRIBE, PUBLISH, ACK, PING, ...) and 8 the broker sends (CONNACK, PUBACK, DELIVER, PONG, ...).

**Code:** `pubsub/common/protocol.py`, lines 133–141 (`encode`) and 179–202 (`FrameReader.read`)

```python
# encode: one message = one line of JSON
data = (json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")

# FrameReader.read: rebuild whole messages from the byte stream
while True:
    newline = self.buffer.find(b"\n")
    if newline != -1:
        line = bytes(self.buffer[:newline])
        del self.buffer[:newline + 1]
        ...
        return decode(line)
    ...
    chunk = self.sock.recv(4096)
    if not chunk:
        return None  # orderly close by the peer (any partial frame is discarded)
    self.buffer.extend(chunk)
```

**What it does for UPa:** TCP delivers a stream of bytes, not separate messages, so one `recv()` can return half a message or several at once. Ending every message with a newline lets each side cut the stream back into whole messages, so a notification is never read half-finished or merged with the next one.

### 3. Publish-subscribe with topic-based filtering

**Part of the program:** the broker's routing step. Publishers send to a topic such as `casa-jaro.announcements` or `molo-hub.billing`. Subscribers register for the topics they want. In the website, each tenant subscribes to `tenant.<user>` and to their property's announcements.

**Code:** `pubsub/broker/store.py`, lines 142–153 (`Store.publish`), called from `pubsub/broker/broker.py` line 376 (`handle_publish`)

```python
# Topic-based filtering: exactly the clients subscribed to this topic name.
subscribers = [row["client_id"] for row in
               db.execute("SELECT client_id FROM subscriptions WHERE topic = ? ORDER BY client_id",
                          (topic,))]
cur = db.execute(
    "INSERT INTO messages (topic, publisher_id, pub_msg_id, payload, published_at, routed_to) "
    "VALUES (?, ?, ?, ?, ?, ?)", ...)
seq = cur.lastrowid
db.executemany("INSERT INTO deliveries (client_id, seq) VALUES (?, ?)",
               [(client_id, seq) for client_id in subscribers])
```

**What it does for UPa:** the manager's office sends a Casa Jaro notice once, without knowing who lives there, and only Casa Jaro tenants receive it. This is **space uncoupling**: publishers and subscribers never address each other. Because the message waits in each subscriber's queue, it is also **time uncoupling**: the two sides do not need to be online at the same time.

### 4. Multithreading

**Part of the program:** inside the broker, the clients and the website.

- The broker starts one thread per TCP connection, plus a delivery thread for each connected subscriber.
- Every client runs a background heartbeat thread.
- The website answers each browser request on its own thread.

**Code:** `pubsub/broker/broker.py`, lines 262–263 and 309–310

```python
# accept loop: one thread per TCP connection (thread-per-connection, Coulouris §7.4)
session = ClientSession(self, conn, address)
threading.Thread(target=session.run, name=f"conn-{address[1]}", daemon=True).start()

# after CONNECT: a second thread per subscriber that only sends its queued messages
threading.Thread(target=self.delivery_loop, args=(session,), name=f"deliver-{client_id}",
                 daemon=True).start()
```

Also: `pubsub/subscriber/subscriber.py` line 149 (heartbeat thread) and `pubsub/dashboard/server.py` lines 398–399 (`ThreadingHTTPServer`, used by the website).

**What it does for UPa:** the broker serves every tenant, publisher and browser at the same time. A slow or silent client blocks only its own thread. Reading a subscriber's ACKs and sending it new messages also run in parallel. Python threads overlap well here because the work is mostly waiting on sockets, not heavy computation.

### 5. Synchronization: locks and atomic transactions

**Part of the program:** wherever two threads touch the same thing. That means each socket (a subscriber's reader thread and its delivery thread both send on it), the broker's SQLite database, and the in-memory list of who is online.

**Code:** `pubsub/common/protocol.py`, lines 155–163, and `pubsub/broker/store.py`, lines 74 and 86–97

```python
# protocol.py: only one thread at a time may write to a socket
def send(sock, message, lock=None):
    """... otherwise two frames could be interleaved byte-by-byte and corrupt the stream."""
    data = encode(message)
    ...
    with lock:
        sock.sendall(data)

# store.py: every database change is one atomic transaction, behind a lock
self._lock = threading.RLock()
...
with self._lock:
    self._db.execute("BEGIN IMMEDIATE")
    try:
        yield self._db
    except BaseException:
        self._db.execute("ROLLBACK")
        raise
    else:
        self._db.execute("COMMIT")
```

Also: `pubsub/broker/registry.py`, lines 231–259 (a lock around the online-clients table) and `broker.py` line 120 (each session's `send_lock`).

**What it does for UPa:** the lock on each socket stops two messages from mixing byte by byte. The database lock stops threads from corrupting the store. Each transaction stores a message and its delivery rows for every subscriber all at once, so after a crash a message is either fully routed or not stored at all.

### 6. Producer-consumer coordination

**Part of the program:** between the broker's threads, and inside the website server. In the broker, the publisher's connection thread is the producer and each subscriber's delivery thread is a consumer. They meet at the subscriber's queue and signal with a `threading.Event`.

**Code:** `pubsub/broker/broker.py`, lines 400–404 (producer) and 423–452 (consumer)

```python
# producer (handle_publish): message is stored, now wake each online subscriber's delivery thread
for client_id in result.subscribers:
    subscriber = self.sessions.get(client_id)
    if subscriber is not None and subscriber.role == protocol.SUBSCRIBER:
        subscriber.wakeup.set()

# consumer (delivery_loop): send what is queued, then sleep until woken
while not session.closed.is_set():
    session.wakeup.clear()  # cleared BEFORE reading the queue, so no wake-up can be missed
    for item in self.store.pending(client_id, s.window):
        ...  # send the message
    session.wakeup.wait(timeout=min(1.0, s.ack_timeout / 2))
```

The website uses the same pattern with `queue.Queue`. A thread reading from the broker puts each message into one queue per open browser tab, and each tab's HTTP thread takes messages out and streams them (`pubsub/webapp/server.py`, lines 131–138 and 414–429).

**What it does for UPa:** a publisher gets its confirmation as soon as the message is stored, without waiting for delivery. Delivery threads sleep instead of polling, but wake the moment a message arrives. Clearing the event before reading the queue prevents a "lost wake-up" race, where a message arriving at the wrong instant could sit undelivered.

### 7. Persistent queues and durable subscriptions

**Part of the program:** the broker's store, one SQLite file (`data/broker.db`). It has three tables: `messages` (everything published), `subscriptions` (who wants which topic) and `deliveries` (one row per message still owed to a subscriber, which is that subscriber's queue).

**Code:** `pubsub/broker/store.py`, lines 52–58 (the queue table) and 81–82 (crash-safe journal)

```python
CREATE TABLE IF NOT EXISTS deliveries (
    client_id TEXT    NOT NULL,
    seq       INTEGER NOT NULL REFERENCES messages (seq),
    attempts  INTEGER NOT NULL DEFAULT 0,            -- how many times it has been sent
    last_sent REAL,                                  -- when it was last sent; NULL = not sent on this connection
    PRIMARY KEY (client_id, seq)                     -- per-subscriber FIFO order = ORDER BY seq
);
...
self._db.execute("PRAGMA journal_mode=WAL")
self._db.execute("PRAGMA synchronous=NORMAL")
```

On start-up the broker reports what it recovered (`broker.py`, lines 246–250: "N durable subscription(s) ... M message(s) waiting in queues").

**What it does for UPa:** a tenant who is offline still gets every notice when they come back (catch-up). Queues are rows on disk, not Python lists, so they survive the subscriber going offline and the broker process itself being killed. Subscriptions are remembered too, so a subscriber restarted without `--topics` keeps its topics.

### 8. Reliable at-least-once delivery

**Part of the program:** both hops of every message.

- **Publisher to broker:** the broker sends PUBACK only after the message is stored. With no PUBACK within 5 seconds, the publisher reconnects and re-sends, up to 5 retries.
- **Broker to subscriber:** a message stays queued until the subscriber ACKs it. Without an ACK within 5 seconds, it is sent again.

**Code:** `pubsub/broker/broker.py` lines 429–430, `pubsub/broker/store.py` line 176, `pubsub/subscriber/subscriber.py` lines 234–240, `pubsub/publisher/publisher.py` lines 131–132

```python
# broker (delivery_loop): re-send anything sent more than ack_timeout ago and still not ACKed
if item["last_sent"] is not None and now - item["last_sent"] < s.ack_timeout:
    continue  # sent recently; still waiting for its ACK

# store (ack): only an ACK removes a message from the subscriber's queue
cur = db.execute("DELETE FROM deliveries WHERE client_id = ? AND seq = ?", (client_id, seq))

# subscriber (on_deliver): record it as processed FIRST, then acknowledge
self.processed.add(seq, msg["published_at"])
...
if not self.no_ack:
    self.send({"type": protocol.ACK, "seq": seq})

# publisher (publish): wait for PUBACK; on timeout the loop reconnects and re-sends the SAME msg_id
self._send(frame)
puback = self._wait_for(protocol.PUBACK, self.puback_timeout, msg_id=msg_id)
```

When a subscriber reconnects, the broker re-sends its un-ACKed messages at once instead of waiting for the timeout (`store.py` lines 179–185, `reset_in_flight`). The browser follows the same rule and ACKs only after saving the alert (`app.js` line 1924).

**What it does for UPa:** no notice is lost if the network drops, the broker restarts, or a tenant's app crashes mid-message. The price is that a message can arrive twice, which concept 9 handles.

### 9. Duplicate detection (idempotent processing)

**Part of the program:** three places, one for each copy that a retry can create:

- **The broker** spots a publisher re-sending the same message.
- **Each subscriber** keeps an on-disk log of what it has already processed.
- **The browser** keeps the same kind of log for each user.

**Code:** `pubsub/broker/store.py` lines 41 and 137–140, `pubsub/subscriber/subscriber.py` lines 207–213, `app.js` lines 1892–1893

```python
# store.py: the broker recognises a retry by (publisher, msg_id) and does not route it again
UNIQUE (publisher_id, pub_msg_id)                -- detects a publisher re-sending the same message
...
existing = db.execute("SELECT seq, routed_to FROM messages WHERE publisher_id = ? AND pub_msg_id = ?",
                      (publisher_id, pub_msg_id)).fetchone()
if existing:
    return PublishResult(existing["seq"], [], existing["routed_to"], duplicate=True)

# subscriber.py: already processed -> ACK again so the broker stops re-sending, but do not process twice
if self.processed.seen(seq, msg["published_at"]):
    ...
    if not self.no_ack:
        self.send({"type": protocol.ACK, "seq": seq})
    return
```

```javascript
// app.js: the browser does the same check before showing an alert
const key = `${m.seq}-${m.published_at}`;
const isNew = !this.seen(me.client).includes(key);
```

**What it does for UPa:** at-least-once delivery plus duplicate detection means each notice is processed exactly once, as far as the tenant can tell. A message is identified by its sequence number plus its publish time, because sequence numbers restart at 1 with a fresh database. The subscriber's log is written atomically with `os.replace` (lines 75–77), so a crash never leaves it half-written.

### 10. Failure detection: heartbeats and timeouts

**Part of the program:** every connection, in both directions. Clients send a PING every 5 seconds. The broker presumes a client dead after 15 seconds of silence, which is 3 missed PINGs. A killed process is noticed at once, because its socket resets.

**Code:** `pubsub/common/config.py` lines 28–29, `pubsub/subscriber/subscriber.py` lines 100–106, `pubsub/broker/broker.py` lines 161–163, 171–174 and 190

```python
# config.py
HEARTBEAT_INTERVAL = 5.0    # clients send a PING this often
HEARTBEAT_TIMEOUT = 15.0    # the broker drops a client that has been silent this long (3 missed PINGs)

# subscriber.py: a background thread sends the heartbeat
while not stop.wait(config.HEARTBEAT_INTERVAL):
    try:
        self.send({"type": protocol.PING})
    except OSError:
        return

# broker.py: silence and crashes both end the session
self.sock.settimeout(self.broker.settings.heartbeat_timeout)
...
except TimeoutError:
    self.close(f"no {waiting_for} for {self.sock.gettimeout():.0f}s")
...
except OSError:
    # e.g. WinError 10054 "connection forcibly closed" when the client process is killed
    self.close("connection reset (client process ended abruptly)")
```

Clients watch the broker the same way: no reply for 15 seconds means the broker has failed (`subscriber.py` lines 157–161).

**What it does for UPa:** in a distributed system a crashed program and a slow one look alike, so the system needs a timeout to decide. The broker knows who is really online and starts queueing for anyone who is not. Clients notice a dead broker and start reconnecting (concept 11).

### 11. Fault tolerance and recovery

**Part of the program:** the clients' reconnect loops and the broker's session registry. When the broker goes away, subscribers retry after 1, 2, 4 and then 8 seconds (exponential back-off, capped at 8). The publisher and the website gateway do the same.

**Code:** `pubsub/subscriber/subscriber.py` lines 109–128, `pubsub/broker/broker.py` lines 290–294

```python
# subscriber.py (run): reconnect forever, doubling the wait each time
delay = 1.0
while True:
    try:
        had_connection = self.session()
        if had_connection:
            delay = 1.0
    except ConnectionRefusedError:
        console.log("WAITING", f"broker at {self.host}:{self.port} is not reachable", "yellow")
    ...
    time.sleep(delay)
    delay = min(delay * 2, config.RECONNECT_MAX_DELAY)

# broker.py (handle_connect): if one client id connects twice, the newer connection wins
replaced = self.sessions.attach(client_id, session)
if replaced is not None:
    replaced.send(protocol.error_frame(protocol.REPLACED, "another connection logged in with your client_id"))
    replaced.close("replaced by a newer connection with the same client_id")
```

Back-off also appears in `publisher.py` line 144 and `webapp/server.py` line 151. To prove recovery works, the code can inject faults on purpose:

- `--crash-after N` makes a subscriber exit before its ACK (`subscriber.py` lines 236–238).
- `--fault-skip-puback N` makes the broker drop PUBACKs (`broker.py` lines 394–395).
- The dashboard's **Crash** button hard-kills a process (`dashboard/server.py` lines 128–135).

**What it does for UPa:** the system heals itself without anyone restarting clients. Restart a crashed broker and every client reconnects and catches up on its own. Back-off avoids flooding a broker that is still starting. Session takeover handles a subscriber that restarts before the broker has noticed its old connection died.

### 12. Flow control: delivery window

**Part of the program:** the broker's delivery loop. At most 50 un-ACKed messages are in flight to one subscriber at a time, and each ACK frees a slot for the next one. Messages go out oldest first.

**Code:** `pubsub/common/config.py` line 37, `pubsub/broker/broker.py` lines 426 and 463, `pubsub/broker/store.py` lines 160–164

```python
# config.py
DELIVERY_WINDOW = 50         # most un-ACKed messages in flight to one subscriber at a time

# broker.py (delivery_loop): only the oldest `window` queued messages are eligible
for item in self.store.pending(client_id, s.window):

# store.py (pending): oldest first, at most `limit`
"SELECT d.seq, d.attempts, d.last_sent, m.topic, m.publisher_id, m.payload, m.published_at "
"FROM deliveries d JOIN messages m ON m.seq = d.seq "
"WHERE d.client_id = ? ORDER BY d.seq LIMIT ?",

# broker.py (handle_ack)
session.wakeup.set()  # a slot in the delivery window just opened
```

**What it does for UPa:** a tenant returning to a long backlog is not flooded with everything at once. The broker keeps sending only as fast as the subscriber confirms. Ordering by sequence number means catch-up messages arrive in the order they were published.

### 13. Web gateway with server-sent events

**Part of the program:** the website server, which bridges the browser and the broker. A browser cannot open raw TCP sockets, so the gateway speaks the broker protocol for it:

- Each website user (for example `web-tenant-u-juan`) is a durable subscriber, with its own broker connection on its own thread.
- Messages reach the page as **server-sent events**: one long-lived HTTP response that the server keeps writing to.

**Code:** `pubsub/webapp/server.py` lines 100–102, 409–410 and 435–437; `app.js` line 1848

```python
# server.py (WebSubscription.want): one broker session, on its own thread, per website user
if self.thread is None:
    self.thread = threading.Thread(target=self._run, name=f"web-{self.client_id}", daemon=True)
    self.thread.start()

# server.py (_stream / _event): push each message to the browser as it arrives
self.send_header("Content-Type", "text/event-stream; charset=utf-8")
...
def _event(self, event: dict) -> None:
    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
    self.wfile.flush()
```

```javascript
// app.js: the browser subscribes to the stream
this.source = new EventSource(`/broker/stream?${new URLSearchParams({ client: me.client, topics: me.topics.join(',') })}`);
```

**What it does for UPa:** bills, maintenance updates, announcements and chat appear in the app live, with no refresh. While a user is away the broker queues their messages. When they open the app, the queue is delivered and a toast says "Was waiting at the broker" (`app.js` line 1931). Several tabs for one user share a single broker session, kept open for 4 seconds after the last tab closes so a page reload does not drop it.

## Showing each concept in the live demo

The steps in [`UPa_Broker_Demo_Guide.md`](UPa_Broker_Demo_Guide.md) already show every concept. Name the concepts as you go:

| Demo step | What the audience sees | Concepts to name |
| --- | --- | --- |
| 1. Introduce the system | Broker, Juan, Jerome and caretaker all **Online** in the dashboard | 1 processes, 4 threads, 10 heartbeats |
| 2. Topic filtering: send **Fire safety inspection** | Only Juan receives it; the faucet update reaches Juan and the caretaker | 2 message passing, 3 publish-subscribe |
| 3. Offline and catch-up: crash Juan, publish 3 notices, start Juan | **3 waiting**, then three **Caught up** notices, in order | 7 persistent queues, 10 failure detection, 11 reconnect, 12 ordered delivery |
| 4. Delivery retry: **Juan crashes before ACK** | The notice is sent again; Juan shows **Duplicate ignored** | 8 at-least-once, 9 duplicate detection |
| 5. Broker recovery: crash and restart the broker | Clients reconnect; Jerome's waiting notice still arrives | 7 durable store, 11 fault tolerance, 5 atomic transactions |
| Website: owner offline, tenant sends a request, owner signs back in | The owner's alert arrives with "Was waiting at the broker" | 13 web gateway, 3 publish-subscribe, 7 persistent queues |

If asked how threads stay safe, point to concept 5. If asked how the publisher knows a message got through, point to PUBACK in concept 8.
