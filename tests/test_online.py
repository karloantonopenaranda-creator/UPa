"""
The internet sharing script (O): scripts/start_online.py puts the DormCare website behind a public ngrok link.

These tests never contact ngrok. A small Python program stands in for the ngrok agent and prints lines copied from
real ngrok 3.39 output, and a local HTTP server stands in for the website.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests.helpers import free_port

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("start_online", ROOT / "scripts" / "start_online.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

LINK = "https://quiet-river-maple.ngrok-free.app"
# Lines in the format `ngrok http ... --log stdout --log-format logfmt` prints.
NGROK_STARTED = (f't=2026-11-24T15:50:57+0800 lvl=info msg="started tunnel" obj=tunnels name=command_line '
                 f'addr=http://127.0.0.1:8000 url={LINK}')
NGROK_WEB = 't=2026-11-24T15:50:56+0800 lvl=info msg="starting web service" obj=web addr=127.0.0.1:4040 allow_hosts=[]'
NGROK_NO_TOKEN = ('t=2026-11-24T15:50:57+0800 lvl=crit msg="command failed" err="authentication failed: This ngrok '
                  'session is not authenticated. ngrok requires an account and a valid credential to start a session.'
                  '\\n\\nSign up for an account: https://dashboard.ngrok.com/signup\\nGet your credential: '
                  'https://dashboard.ngrok.com/get-started/your-authtoken\\r\\n\\r\\nERR_NGROK_4018\\r\\n"')
# ERR_NGROK_121 and ERR_NGROK_105 lines use the wording of ngrok's error documentation (not captured live).
NGROK_TOO_OLD = ('t=2026-11-24T15:50:57+0800 lvl=crit msg="command failed" err="Your ngrok-agent version \\"3.3.1\\" is too '
                 'old. The minimum supported agent version for your account is \\"3.20.0\\". Please update to a newer '
                 'version with `ngrok update`.\\r\\n\\r\\nERR_NGROK_121\\r\\n"')
NGROK_BAD_TOKEN = ('t=2026-11-24T15:50:57+0800 lvl=eror msg="failed to reconnect session" obj=tunnels.session '
                   'err="authentication failed: The authtoken you specified is properly formed, but it is invalid.'
                   '\\r\\n\\r\\nERR_NGROK_105\\r\\n"')
NGROK_IN_USE = ('t=2026-11-24T15:50:57+0800 lvl=eror msg="session closing" obj=tunnels.session err="failed to start '
                f'tunnel: The endpoint \'{LINK}\' is already online.\\r\\n\\r\\nERR_NGROK_334\\r\\n"')


class FakeServer:
    """A local web server on a free port. `dormcare=True` answers /broker/info like pubsub.webapp does;
    `dormcare=False` answers with an HTML page, like some other program that happens to use the port."""

    def __init__(self, dormcare: bool) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if dormcare and self.path == "/broker/info":
                    body, kind = json.dumps({"ok": True, "broker": True, "broker_port": 5555}).encode(), "application/json"
                else:
                    body, kind = b"<!doctype html><title>Some other program</title>", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class OnlineLinkTests(unittest.TestCase):
    def serve(self, dormcare: bool) -> str:
        server = FakeServer(dormcare)
        self.addCleanup(server.close)
        return server.url

    def test_O1_link_is_read_from_the_started_tunnel_line(self):
        self.assertEqual(mod.find_tunnel_url(NGROK_STARTED), LINK)

    def test_O2_links_in_error_and_other_lines_are_not_the_public_link(self):
        self.assertIsNone(mod.find_tunnel_url(NGROK_NO_TOKEN))  # contains https://dashboard.ngrok.com/...
        self.assertIsNone(mod.find_tunnel_url(NGROK_IN_USE))  # contains the link, but the tunnel did not start
        self.assertIsNone(mod.find_tunnel_url(NGROK_WEB))

    def test_O3_only_a_dormcare_website_counts_as_running(self):
        self.assertTrue(mod.website_running(self.serve(dormcare=True)))
        self.assertFalse(mod.website_running(self.serve(dormcare=False)))
        self.assertFalse(mod.website_running(f"http://127.0.0.1:{free_port()}"))

    def test_O4_tunnel_that_fails_returns_no_link_quickly(self):
        fake = [sys.executable, "-c", f"print({NGROK_NO_TOKEN!r}); raise SystemExit(1)"]
        start = time.monotonic()
        proc, url = mod.open_tunnel(fake, timeout=10)
        self.assertIsNone(url)
        self.assertLess(time.monotonic() - start, 5)  # returns when the program exits, not at the timeout
        proc.wait(5)
        self.assertIn("ERR_NGROK_4018", "\n".join(proc.recent))  # kept for the error report

    def test_O4b_tunnel_link_is_returned_while_process_keeps_running(self):
        fake = [sys.executable, "-c", f"import time; print({NGROK_WEB!r}); print({NGROK_STARTED!r}, flush=True); "
                                      f"time.sleep(30)"]
        proc, url = mod.open_tunnel(fake, timeout=10)
        try:
            self.assertEqual(url, LINK)
            self.assertIsNone(proc.poll())  # the tunnel keeps running after the link is found
        finally:
            proc.kill()
            proc.wait(5)

    def test_O5_reachability_wait(self):
        self.assertTrue(mod.wait_until_reachable(self.serve(dormcare=True), 3))
        start = time.monotonic()
        self.assertFalse(mod.wait_until_reachable(f"http://127.0.0.1:{free_port()}", 2))
        self.assertGreaterEqual(time.monotonic() - start, 1.5)  # it really waited
        self.assertLess(time.monotonic() - start, 4)  # and gave up near the timeout

    def test_O6_missing_ngrok_prints_install_command(self):
        with mock.patch.object(mod, "find_ngrok", return_value=None), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mod.main(["--no-browser"]), 1)
        self.assertIn("winget install --id 9MVS1J51GMK6 --source msstore", out.getvalue())

    def test_O7_failures_are_explained_in_plain_words(self):
        no_token = mod.explain_failure([NGROK_WEB, NGROK_NO_TOKEN])
        self.assertIn("ngrok config add-authtoken", no_token)
        self.assertIn("https://dashboard.ngrok.com/signup", no_token)
        in_use = mod.explain_failure([NGROK_IN_USE])
        self.assertIn("already", in_use)
        self.assertNotIn("add-authtoken", in_use)
        other = mod.explain_failure(["t=2026-11-24T15:50:57+0800 lvl=eror msg=\"dial tcp: no such host\""])
        self.assertIn("online", other)
        self.assertNotIn("add-authtoken", other)

    def test_O8_an_old_ngrok_is_told_to_update(self):
        advice = mod.explain_failure([NGROK_WEB, NGROK_TOO_OLD])
        self.assertIn("ngrok update", advice)
        self.assertNotIn("Is this PC online", advice)

    def test_O9_other_ngrok_errors_are_shown_in_ngroks_own_words(self):
        advice = mod.explain_failure([NGROK_WEB, NGROK_BAD_TOKEN])
        self.assertIn("The authtoken you specified is properly formed, but it is invalid.", advice)
        self.assertIn("ERR_NGROK_105", advice)
        self.assertNotIn("\\r\\n", advice)  # ngrok's escaped line breaks are not shown raw


if __name__ == "__main__":
    unittest.main()
