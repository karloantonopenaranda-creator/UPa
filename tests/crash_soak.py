"""
Randomized crash test of the delivery guarantee (not part of the unit test run; about 2 to 3 minutes).

    python -m tests.crash_soak              seeds 1809, 7 and 42
    python -m tests.crash_soak 5 6          other seeds

In each run four publishers send 500 messages each to five topics while six subscribers keep disconnecting,
sometimes after processing a message and before ACKing it, and the broker is hard-killed five times. At the end
every acknowledged message must have reached every subscriber of its topic, in order, and nobody else.
"""
from __future__ import annotations

import random
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from pubsub.common import protocol
from tests.helpers import BrokerProcess, RawClient, queue_depth

PUBLISHERS, MESSAGES, BROKER_KILLS = 4, 500, 5
LOST_CONNECTION = (OSError, EOFError, AssertionError)  # RawClient raises AssertionError on a timeout


def run(seed: int) -> bool:
    rng = random.Random(seed)
    topics = [f"t{i}" for i in range(5)]
    interests = {f"s{i}": rng.sample(topics, rng.randint(1, 4)) for i in range(6)}
    received: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    confirmed: list[tuple[str, str]] = []
    events: Counter[str] = Counter()
    lock, stop = threading.Lock(), threading.Event()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        broker = BrokerProcess(Path(tmp), "--quiet", "--ack-timeout", "1").start()
        for client_id, wanted in interests.items():
            client = RawClient(broker.port, client_id, protocol.SUBSCRIBER)
            for topic in wanted:
                client.subscribe(topic)
            client.close()

        def subscriber(client_id: str, seed_: int) -> None:
            r = random.Random(seed_)
            while not stop.is_set():
                client = None
                try:
                    client = RawClient(broker.port, client_id, protocol.SUBSCRIBER)
                    until = time.time() + r.uniform(0.3, 2)
                    while time.time() < until and not stop.is_set():
                        frame = client.recv(timeout=0.3)
                        if frame and frame["type"] == protocol.DELIVER:
                            with lock:
                                received[client_id].append((frame["seq"], frame["topic"], frame["payload"]["title"]))
                            if r.random() < 0.02:
                                break  # gone after processing, before the ACK
                            client.ack(frame["seq"])
                except LOST_CONNECTION:
                    time.sleep(0.2)
                finally:
                    if client:
                        client.close()
                        with lock:
                            events["subscriber disconnects"] += 1
                time.sleep(r.uniform(0, 0.3))

        def publisher(client_id: str, seed_: int) -> None:
            r, client = random.Random(seed_), None
            for i in range(MESSAGES):
                name, topic = f"{client_id}-{i}", r.choice(topics)
                give_up = time.time() + 60
                while time.time() < give_up:
                    try:
                        client = client or RawClient(broker.port, client_id, protocol.PUBLISHER)
                        client.publish(topic, name, msg_id=name)
                        with lock:
                            confirmed.append((name, topic))
                        break
                    except LOST_CONNECTION:
                        if client:
                            client.close()
                        client = None
                        with lock:
                            events["publisher retries"] += 1
                        time.sleep(0.2)
                time.sleep(0.012)
            if client:
                client.close()

        readers = [threading.Thread(target=subscriber, args=(c, seed + i)) for i, c in enumerate(interests)]
        writers = [threading.Thread(target=publisher, args=(f"p{i}", seed + 100 + i)) for i in range(PUBLISHERS)]
        for thread in readers + writers:
            thread.start()
        for _ in range(BROKER_KILLS):
            time.sleep(2)
            broker.kill()
            events["broker kills"] += 1
            time.sleep(0.3)
            broker.start()
        for thread in writers:
            thread.join()

        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                probe = RawClient(broker.port, "soak-probe", protocol.PUBLISHER)
                status = probe.status()
                probe.close()
                if not any(queue_depth(status, c) for c in interests):
                    break
            except LOST_CONNECTION:
                pass
            time.sleep(1)
        stop.set()
        for thread in readers:
            thread.join()
        broker.kill()

    expected = lost = misrouted = duplicates = disordered = 0
    for client_id, wanted in interests.items():
        want = {name for name, topic in confirmed if topic in wanted}
        got = received[client_id]
        names = {name for _, _, name in got}
        expected += len(want)
        lost += len(want - names)
        misrouted += sum(topic not in wanted for _, topic, _ in got)
        duplicates += len(got) - len(names)
        first_arrivals = list(dict.fromkeys(seq for seq, _, _ in got))
        disordered += first_arrivals != sorted(first_arrivals)

    complete = len(confirmed) == PUBLISHERS * MESSAGES
    passed = complete and not (lost or misrouted or disordered)
    print(f"seed {seed}: {'PASS' if passed else 'FAIL'}  confirmed={len(confirmed)} expected_deliveries={expected} "
          f"lost={lost} misrouted={misrouted} subscribers_out_of_order={disordered} duplicates={duplicates} "
          + " ".join(f"{name.replace(' ', '_')}={count}" for name, count in sorted(events.items())), flush=True)
    return passed


if __name__ == "__main__":
    seeds = [int(arg) for arg in sys.argv[1:]] or [1809, 7, 42]
    results = [run(seed) for seed in seeds]
    print(f"{sum(results)} of {len(results)} runs passed")
    sys.exit(0 if all(results) else 1)
