"""
Default settings shared by the broker and the clients.

Every value here can be overridden on the command line (see each program's --help), so the
tests can run a broker on a random port with short timeouts without editing this file.
"""
from __future__ import annotations

import os
from pathlib import Path

# The project root is the folder that contains the `pubsub` package.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Network ----------------------------------------------------------------
# 127.0.0.1 keeps the demo on one machine and avoids the Windows Firewall prompt.
# Start the broker with --host 0.0.0.0 to accept clients from other machines.
HOST = "127.0.0.1"
PORT = 5555

# --- Storage ----------------------------------------------------------------
DATA_DIR = Path(os.environ.get("DORMCARE_DATA_DIR", PROJECT_ROOT / "data"))
BROKER_DB = DATA_DIR / "broker.db"              # durable queues and subscriptions
SUBSCRIBER_STATE_DIR = DATA_DIR / "subscribers"  # each subscriber's "already processed" list
SCENARIO_FILE = PROJECT_ROOT / "pubsub" / "scenario" / "dormcare_scenario.json"

# --- Timing (seconds) -------------------------------------------------------
HEARTBEAT_INTERVAL = 5.0    # clients send a PING this often
HEARTBEAT_TIMEOUT = 15.0    # the broker drops a client that has been silent this long (3 missed PINGs)
ACK_TIMEOUT = 5.0           # the broker re-sends a DELIVER that has not been ACKed within this time
PUBACK_TIMEOUT = 5.0        # a publisher re-sends a PUBLISH that has not been PUBACKed within this time
CONNECT_TIMEOUT = 10.0      # a new connection must send CONNECT within this time
RECONNECT_MAX_DELAY = 8.0   # upper limit for the clients' exponential reconnect back-off

# --- Limits -----------------------------------------------------------------
MAX_FRAME_BYTES = 64 * 1024  # largest single protocol frame accepted
DELIVERY_WINDOW = 50         # most un-ACKed messages in flight to one subscriber at a time
PUBLISH_RETRIES = 5          # times a publisher re-sends one message before giving up
