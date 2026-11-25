"""
Session registry: which clients are connected RIGHT NOW, and on which connection.

The durable store answers "who is subscribed to what?", and that survives restarts.
This registry answers "is tenant-juan online, and which connection is his?". It lives only in
memory and starts empty every time the broker starts, because after a restart nobody is connected.

Policy for duplicate client ids: if a second connection logs in with a client_id that is already
connected (for example a subscriber restarted before the broker noticed the old connection died),
the NEWER connection wins and the broker closes the older one.
"""
from __future__ import annotations

import threading
from typing import Any


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}
        self._lock = threading.Lock()

    def attach(self, client_id: str, session: Any) -> Any | None:
        """Record `session` as the live connection for `client_id`. Returns the connection it replaced, if any."""
        with self._lock:
            previous = self._sessions.get(client_id)
            self._sessions[client_id] = session
            return previous if previous is not session else None

    def detach(self, client_id: str, session: Any) -> bool:
        """Forget `session`, but only if it is still the current one (a newer connection may have replaced it)."""
        with self._lock:
            if self._sessions.get(client_id) is session:
                del self._sessions[client_id]
                return True
            return False

    def get(self, client_id: str) -> Any | None:
        with self._lock:
            return self._sessions.get(client_id)

    def online(self) -> dict[str, str]:
        """{client_id: role} for every connected client."""
        with self._lock:
            return {client_id: session.role for client_id, session in self._sessions.items()}

    def all(self) -> list[Any]:
        with self._lock:
            return list(self._sessions.values())
