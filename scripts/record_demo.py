"""
Runs the demonstration storyline with the real programs and records every process's output in docs/evidence/.

    python scripts/record_demo.py          (about 45 seconds; uses a free port and a temporary database)

The log excerpts in the Fault Test Report come from a run of this script.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pubsub.common import protocol  # noqa: E402
from tests.helpers import OutputCollector, RawClient, free_port, wait_for_port  # noqa: E402

EVIDENCE = ROOT / "docs" / "evidence"
TOPICS = {
    "tenant-juan": "casa-jaro.announcements,casa-jaro.maintenance,casa-jaro.billing",
    "tenant-jerome": "molo-hub.announcements,molo-hub.billing",
    "caretaker": "casa-jaro.maintenance,molo-hub.maintenance",
}


class Recorder:
    def __init__(self, workdir: Path):
        self.workdir, self.port = workdir, free_port()
        self.steps: list[str] = []
        self.runs: list[tuple[str, str, subprocess.Popen, OutputCollector]] = []
        self.latest: dict[str, tuple[subprocess.Popen, OutputCollector]] = {}

    def step(self, text: str) -> None:
        self.steps.append(f"{time.strftime('%H:%M:%S')}  STEP  {text}")

    def start(self, name: str, label: str, *args: str) -> OutputCollector:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
        proc = subprocess.Popen([sys.executable, *args, "--port", str(self.port), "--no-color"], cwd=ROOT, env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8")
        out = OutputCollector(proc.stdout)
        self.runs.append((name, label, proc, out))
        self.latest[name] = (proc, out)
        return out

    def broker(self, label: str = "run", *extra: str) -> OutputCollector:
        out = self.start("broker", label, "-m", "pubsub.broker", "--db", str(self.workdir / "broker.db"), *extra)
        wait_for_port(self.port)
        return out

    def subscriber(self, name: str, label: str = "run", *extra: str) -> OutputCollector:
        out = self.start(name, label, "-m", "pubsub.subscriber", "--id", name,
                         "--state-dir", str(self.workdir / "subscribers"), *extra)
        out.wait_for("CONNECTED")
        return out

    def type(self, name: str, line: str, expect: str) -> None:
        proc, out = self.latest[name]
        count = out.count(expect)
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        out.wait_for(expect, timeout=30, count=count + 1)
        time.sleep(0.3)

    def kill(self, name: str) -> None:
        proc, out = self.latest[name]
        proc.kill()
        proc.wait(timeout=10)
        time.sleep(0.5)

    def run(self) -> None:
        self.step("A. Start the broker")
        broker = self.broker()
        self.step("B. Start subscribers tenant-juan, tenant-jerome, caretaker")
        for name, topics in TOPICS.items():
            self.subscriber(name, "run", "--topics", topics).wait_for("SUBSCRIBED", count=topics.count(",") + 1)
        juan, jerome = self.latest["tenant-juan"][1], self.latest["tenant-jerome"][1]
        self.step("C. Start interactive publishers manager-office and maintenance-desk")
        for name in ("manager-office", "maintenance-desk"):
            self.start(name, "run", "-m", "pubsub.publisher", "--id", name).wait_for("CONNECTED")
        self.type("manager-office", "list", "Pest control")

        self.step("D. TOPIC FILTERING: manager-office sends event 1 (casa-jaro.announcements): only Juan should get it")
        self.type("manager-office", "send 1", "PUBACK")
        self.step("E. manager-office sends event 2 (molo-hub.announcements): only Jerome should get it")
        self.type("manager-office", "send 2", "PUBACK")
        jerome.wait_for("LIVE")
        self.step("F. maintenance-desk sends event 1 (casa-jaro.maintenance): Juan AND caretaker, not Jerome")
        self.type("maintenance-desk", "send 1", "PUBACK")
        juan.wait_for("LIVE", count=2)

        self.step("G. X1 SUBSCRIBER CRASH: hard-kill tenant-juan")
        self.kill("tenant-juan")
        broker.wait_for("OFFLINE")
        self.step("H. Publish 3 messages for Juan while he is offline")
        self.type("manager-office", "send 3", "PUBACK")
        self.type("manager-office", "send 4", "PUBACK")
        self.type("maintenance-desk", "send 2", "PUBACK")
        self.step("I. status: Juan should be offline with 3 queued")
        self.type("manager-office", "status", "publishers online")
        self.step("J. Restart tenant-juan WITHOUT --topics: durable subscriptions + catch-up of 3 messages")
        self.subscriber("tenant-juan", "restarted after crash (no --topics)").wait_for("CATCH-UP", count=3)
        time.sleep(0.5)
        self.type("manager-office", "status", "publishers online")

        self.step("K. X2 CRASH BEFORE ACK: restart Juan with --crash-after 1, publish event 7")
        self.kill("tenant-juan")
        juan = self.subscriber("tenant-juan", "--crash-after 1", "--crash-after", "1")
        self.type("manager-office", "send 7", "PUBACK")
        juan.wait_for("exiting abruptly")
        time.sleep(0.5)
        self.type("manager-office", "status", "publishers online")
        self.step("L. Restart Juan normally: broker re-sends the un-ACKed message, Juan suppresses the duplicate")
        juan = self.subscriber("tenant-juan", "restarted after crash-before-ACK")
        juan.wait_for("DUPLICATE")

        self.step("M. X3 BROKER CRASH: crash Jerome, queue 2 messages for him, then hard-kill the broker")
        self.kill("tenant-jerome")
        self.type("manager-office", "send 5", "PUBACK")
        self.type("manager-office", "pub molo-hub.announcements Quiet hours reminder | Quiet hours are 10 PM to 6 AM.",
                  "PUBACK")
        self.kill("broker")
        self.step("N. Restart the broker on the same database: subscriptions and queues recovered")
        self.broker("restarted after crash")
        juan.wait_for("CONNECTED", count=2, timeout=20)
        self.latest["caretaker"][1].wait_for("CONNECTED", count=2, timeout=20)
        self.step("O. Restart Jerome: he catches up on the 2 messages queued before the broker crash")
        self.subscriber("tenant-jerome", "restarted after broker crash").wait_for("CATCH-UP", count=2)
        self.step("P. Publishers reconnect automatically on their next publish")
        self.type("maintenance-desk", "send 3", "PUBACK")
        juan.wait_for("LIVE")

        self.step("Q. X4 LOST PUBACK: restart broker with --fault-skip-puback 1; one-shot publish with 2 s PUBACK timeout")
        self.kill("broker")
        broker = self.broker("restarted with --fault-skip-puback 1", "--fault-skip-puback", "1")
        juan.wait_for("CONNECTED", count=3, timeout=20)
        once = self.start("one-shot-publisher", "run", "-m", "pubsub.publisher", "--id", "manager-office-2",
                          "--topic", "casa-jaro.billing", "--title", "Reminder: rent due tomorrow",
                          "--puback-timeout", "2")
        once.wait_for("duplicate detected", timeout=30)

        self.step("R. X5 MALFORMED INPUT: a raw client sends garbage, an unknown type and a bad topic")
        raw = RawClient(self.port, "bad-client", protocol.SUBSCRIBER)
        replies = [raw.connack]
        raw.send_raw(b"this is not json\n")
        replies.append(raw.expect(protocol.ERROR))
        raw.send({"type": "TELEPORT"})
        replies.append(raw.expect(protocol.ERROR))
        raw.send({"type": protocol.SUBSCRIBE, "topic": "Not A Topic!"})
        replies.append(raw.expect(protocol.ERROR))
        raw.send({"type": protocol.PING})
        replies.append(raw.expect(protocol.PONG))
        self.steps.append("raw client received:")
        self.steps += [protocol.encode(reply).decode().rstrip() for reply in replies]
        self.step("S. X6 SILENT CLIENT: the raw client now sends nothing; the broker should drop it after 15 s")
        broker.wait_for("no heartbeat", timeout=25)
        raw.close()

    def finish(self) -> None:
        for _, _, proc, out in reversed(self.runs):  # subscribers before the broker
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            out.close()
            proc.stdin.close()

    def write(self) -> None:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        files = {"00_steps": self.steps}
        for name, label, _, out in self.runs:
            lines = files.setdefault(name, [])
            lines += ([""] if lines else []) + [f"===== {name}: {label} =====", *out.lines]
        for name, lines in files.items():
            text = "\n".join(lines).replace(str(self.workdir), "<temp-dir>") + "\n"  # no personal folder names
            (EVIDENCE / f"demo_{name}.txt").write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dormcare-demo-", ignore_cleanup_errors=True) as tmp:
        recorder = Recorder(Path(tmp))
        try:
            recorder.run()
        finally:
            recorder.finish()
        recorder.write()  # only a completed run replaces the saved evidence
    print(f"Recorded the demonstration in {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
