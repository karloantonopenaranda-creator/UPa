"""Readable, colour-coded terminal output. Used by every program so the live demo is easy to follow."""
from __future__ import annotations

import os
import sys
import threading
import time

_STYLES = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "blue": "\033[34m",
    "magenta": "\033[35m", "cyan": "\033[36m", "grey": "\033[90m",
}
_color_enabled = False


class _SafeStream:
    """Wraps stdout/stderr so that output that can no longer be written never crashes the program.

    Example: the dashboard or website starts the broker and reads its output through a pipe. If that parent
    program is closed while the broker keeps running, every write to the dead pipe raises an error. Without this
    wrapper the error escaped from log lines inside message handling and cut off every client's connection.
    Now the text is simply dropped and message handling carries on.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, text: str) -> int:
        try:
            return self._stream.write(text)
        except (OSError, ValueError):
            return len(text)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def setup(color: bool = True, title: str | None = None) -> None:
    """Prepare the terminal: UTF-8 output, line-by-line flushing, optional colours and window title."""
    global _color_enabled
    for stream in (sys.stdout, sys.stderr):
        try:
            # line_buffering makes output appear immediately even when it is piped (the tests read it).
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    if not isinstance(sys.stdout, _SafeStream):
        sys.stdout, sys.stderr = _SafeStream(sys.stdout), _SafeStream(sys.stderr)
    if color and sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        if os.name == "nt":
            os.system("")  # side effect: turns on ANSI colour codes in the Windows console
        _color_enabled = True
    if title and os.name == "nt" and sys.stdout.isatty():
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except (AttributeError, OSError):
            pass


def paint(text: str, *styles: str) -> str:
    """Wrap text in ANSI colour codes (no-op when colours are off)."""
    if not _color_enabled or not styles:
        return text
    return "".join(_STYLES[s] for s in styles) + text + _STYLES["reset"]


def clock() -> str:
    return time.strftime("%H:%M:%S")


_listeners: list = []


def add_listener(callback) -> None:
    """Also send every log line to callback(tag, message). Used by the web dashboard to show the
    lines printed by publishers that run inside it."""
    _listeners.append(callback)


_write_lock = threading.Lock()


def log(tag: str, message: str, *styles: str) -> None:
    """Print one log line:  12:01:33 PUBLISH    #7 casa-jaro.announcements ...

    The broker logs from several threads at once. print() writes the text and the newline separately, so two
    threads could interleave and glue lines together; one locked write per line prevents that."""
    line = f"{paint(clock(), 'grey')} {paint(f'{tag:<10}', *styles)} {message}\n"
    with _write_lock:
        sys.stdout.write(line)
        sys.stdout.flush()
    for callback in _listeners:
        callback(tag, message)


def indent(text: str, spaces: int = 20) -> str:
    """Indent every line of text, for multi-line message bodies under a log line."""
    pad = " " * spaces
    return "\n".join(pad + line for line in str(text).splitlines())
