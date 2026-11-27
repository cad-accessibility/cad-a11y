"""The wait loop both Server-Sent Events streams share (#211)."""

from __future__ import annotations

import queue
from collections.abc import Iterator
from typing import Any

HEARTBEAT = ": heartbeat\n\n"  # keeps the connection alive through proxies


def messages(
    client_queue: queue.Queue[str],
    environ: dict[str, Any],
    *,
    heartbeat_seconds: float = 25.0,
    poll_seconds: float = 1.0,
) -> Iterator[str]:
    """Each queued message, and a heartbeat after heartbeat_seconds without one,
    until waitress says the client has gone.

    A stream holds one of waitress's threads. Waiting for the next message in
    short steps, and checking between them, frees it within a second of the page
    closing rather than when a heartbeat write next fails. Under a server that
    cannot say, the stream ends when a write fails, as before.
    """
    disconnected = environ.get("waitress.client_disconnected")
    waited = 0.0
    while not (disconnected and disconnected()):
        try:
            yield client_queue.get(timeout=poll_seconds)
            waited = 0.0
        except queue.Empty:
            waited += poll_seconds
            if waited >= heartbeat_seconds:
                waited = 0.0
                yield HEARTBEAT
