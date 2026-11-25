"""
Shares the DormCare website on a public link, so any phone or laptop can open it with nothing to install.

    scripts\\start_online.bat          (or: python scripts/start_online.py [--port 8000] [--no-browser])

It uses the website if it is already running on port 8000, or starts it (python -m pubsub.webapp, which in turn
uses or starts the broker on port 5555). Then it starts the ngrok agent: ngrok makes an OUTGOING connection to the
ngrok service, which forwards every request for your account's fixed address (https://<name>.ngrok-free.app) to this
PC. Because the connection is outgoing, no firewall rule, Wi-Fi setting or IP address is needed, and the other device
can be on any network, even mobile data. Live alerts and chat keep working, because ngrok passes the website's
server-sent events through as they happen.

Only the website goes through the link; the broker's TCP port stays private to this PC. Visitors must sign in with
a demo account, but those accounts are written in app.js, so treat the link as public until this script is stopped
with Ctrl+C. On the free plan, visitors see an
ngrok notice page once and click Visit.

One-time setup on this PC:
    winget install --id 9MVS1J51GMK6 --source msstore      (ngrok from the Microsoft Store; the "Ngrok.Ngrok" winget
                                                            package is an old version that ngrok no longer accepts)
    a free account at https://dashboard.ngrok.com/signup, then:  ngrok config add-authtoken <your authtoken>
"""
from __future__ import annotations

import argparse
import collections
import http.client
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pubsub.common import children, console  # noqa: E402

INSTALL = "winget install --id 9MVS1J51GMK6 --source msstore"
# ngrok logs the public address as url=... on its "started tunnel" line. Its error messages contain https:// links
# too (the sign-up page, or the address that could not be started), so no other line is trusted.
TUNNEL_URL = re.compile(r'msg="started tunnel".*?\burl=(https://[^\s"]+)')
# ngrok's own explanation on an error line: err="..." (quotes inside it are escaped as \").
ERR_TEXT = re.compile(r'\berr="((?:[^"\\]|\\.)*)"')
# Direct connections only: a proxy configured in Windows must not answer for the website on this PC.
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def find_tunnel_url(line: str) -> str | None:
    """The public link in one line of ngrok's log, or None."""
    match = TUNNEL_URL.search(line)
    return match.group(1) if match else None


def find_ngrok() -> str | None:
    """The ngrok program: on PATH, or where the Microsoft Store or winget puts it (PATH only updates in new windows)."""
    found = shutil.which("ngrok")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for candidate in (local / "Microsoft" / "WindowsApps" / "ngrok.exe", local / "Microsoft" / "WinGet" / "Links" / "ngrok.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def ngrok_error(lines: list[str]) -> str | None:
    """ngrok's own message from its last error line, with its escaped line breaks turned into spaces."""
    for line in reversed(lines):
        if "lvl=eror" in line or "lvl=crit" in line:
            match = ERR_TEXT.search(line)
            if match:
                text = re.sub(r"(\\[rn])+", " ", match.group(1).replace('\\"', '"'))
                return " ".join(text.split())
    return None


def explain_failure(lines: list[str]) -> str:
    """Plain advice for why ngrok gave no link, judged from its last log lines."""
    text = "\n".join(lines)
    if "ERR_NGROK_121" in text:
        return ("This ngrok is too old: ngrok's service no longer accepts it. Update it with:  ngrok update\n"
                f"or install the current version with:  {INSTALL}\nThen run this script again.")
    if "ERR_NGROK_4018" in text:
        return ("ngrok needs your free account's authtoken on this PC (a one-time step):\n"
                "  1. Sign up or log in at https://dashboard.ngrok.com/signup\n"
                "  2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken\n"
                "  3. In a new window run:  ngrok config add-authtoken <your authtoken>\n"
                "Then run this script again.")
    if "ERR_NGROK_334" in text or "already online" in text:
        return ("Your ngrok address is already online, probably because this script is already running in another "
                "window. Use that window, or close it and try again.")
    said = ngrok_error(lines)
    if said:
        return f"ngrok could not start the link. ngrok said: {said}"
    return "ngrok could not start the link. Is this PC online?"


def website_running(base_url: str, timeout: float = 2.0) -> bool:
    """True only if the DormCare website answers at base_url. Its /broker/info reply is JSON with "ok": true;
    any other program on that address (an HTML page, a non-web server, nothing at all) counts as False."""
    request = urllib.request.Request(f"{base_url}/broker/info", headers={"User-Agent": "DormCare start_online"})
    try:
        with _http.open(request, timeout=timeout) as reply:
            return json.loads(reply.read(4096)).get("ok") is True
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return False


def wait_until_reachable(base_url: str, timeout: float) -> bool:
    """Ask website_running about once a second until it says yes (True) or `timeout` seconds pass (False)."""
    deadline = time.monotonic() + timeout
    while not website_running(base_url):
        if time.monotonic() >= deadline:
            return False
        time.sleep(1.0)
    return True


def open_tunnel(command: list[str], timeout: float = 30.0) -> tuple[subprocess.Popen, str | None]:
    """Start the tunnel program and return it with the public link it prints, or with None if it exits or
    `timeout` seconds pass first. Its output keeps being read in the background, because a full pipe would
    freeze it. Error lines after the link are shown; the last 10 lines are kept in proc.recent for error reports."""
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    proc.recent = collections.deque(maxlen=10)
    links: queue.Queue = queue.Queue()

    def read_output() -> None:
        link = None
        with proc.stdout:  # closed when the program ends
            for line in proc.stdout:
                line = line.rstrip()
                proc.recent.append(line)
                if link is None:
                    link = find_tunnel_url(line)
                    if link:
                        links.put(link)
                elif "lvl=eror" in line or "lvl=crit" in line:
                    console.log("TUNNEL", line, "yellow")
        links.put(None)  # the program ended (only read if no link came first)

    threading.Thread(target=read_output, daemon=True).start()
    try:
        return proc, links.get(timeout=timeout)
    except queue.Empty:
        return proc, None


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def announce(link: str, ready: bool) -> None:
    line = "=" * 72
    print(f"\n{line}")
    print(console.paint("  DormCare is online. Share this link:", "bold", "green"))
    print(f"\n      {console.paint(link, 'bold')}\n")
    print(f"  Manager sign-in: {link}/?role=manager")
    print(f"  Tenant sign-in:  {link}/?role=tenant")
    if os.name == "nt":
        try:
            subprocess.run(["clip"], input=link, text=True, check=False)
            print("  The link is copied to the clipboard.")
        except OSError:
            pass
    print("  Visitors see an ngrok notice page once: tap Visit.")
    if not ready:
        print(console.paint("  ngrok has not confirmed the link yet: it may take a minute to start working.", "yellow"))
    print(console.paint("  Anyone with this link can use the site until you press Ctrl+C.", "yellow"))
    print(f"{line}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/start_online.py",
                                     description="Share the DormCare website on a public ngrok link.")
    parser.add_argument("--port", type=int, default=8000, help="website port (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the link in this PC's browser")
    args = parser.parse_args(argv)
    console.setup(title="DormCare online link")

    ngrok = find_ngrok()
    if ngrok is None:
        print(f"ngrok is not installed. Install it once with:\n\n    {INSTALL}\n\n"
              "then open a new window and run this again.")
        return 1

    local = f"http://127.0.0.1:{args.port}"  # 127.0.0.1, not localhost: the website listens on IPv4 only
    started: list[subprocess.Popen] = []  # what this script started, and stops again at the end
    try:
        if website_running(local):
            console.log("WEBSITE", f"using the DormCare website already running on port {args.port}", "green")
        elif port_in_use(args.port):
            console.log("ERROR", f"port {args.port} is used by another program, not the DormCare website. "
                                 f"Close that program, or choose another port with --port.", "red")
            return 1
        else:
            console.log("WEBSITE", f"starting the DormCare website on port {args.port}", "green")
            site = subprocess.Popen([sys.executable, "-m", "pubsub.webapp", "--port", str(args.port), "--no-browser"],
                                    cwd=ROOT)
            children.end_with_this_process(site)
            started.append(site)
            if not wait_until_reachable(local, 15):
                console.log("ERROR", "the website did not start; see the messages above", "red")
                return 1

        console.log("TUNNEL", "starting ngrok (a few seconds)...", "cyan")
        tunnel, link = open_tunnel([ngrok, "http", local, "--log", "stdout", "--log-format", "logfmt"])
        children.end_with_this_process(tunnel)
        started.append(tunnel)
        if link is None:
            console.log("ERROR", explain_failure(list(tunnel.recent)), "red")
            return 1

        announce(link, wait_until_reachable(link, 30))
        if not args.no_browser:
            webbrowser.open(link)
        while tunnel.poll() is None:  # a short sleep loop, so Ctrl+C is noticed straight away
            time.sleep(0.5)
        console.log("ERROR", "ngrok stopped, so the link no longer works. " + explain_failure(list(tunnel.recent)),
                    "red")
        return 1
    except KeyboardInterrupt:
        console.log("STOPPED", "the link no longer works", "yellow")
        return 0
    finally:
        for proc in reversed(started):
            stop(proc)


if __name__ == "__main__":
    raise SystemExit(main())
