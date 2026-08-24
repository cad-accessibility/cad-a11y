"""The one place interaction data leaves this process.

Every write that could later say what somebody did with the viewer goes through
a *recorder*. There are two of them and they are chosen once, not consulted at
each call site:

``PersistentRecorder``
    Holds the handles -- the analytics database, the study database, the braille
    event log, the session cookie -- and writes.

``NullRecorder``
    Holds nothing. Its methods take the same arguments and return the same
    shapes, and it has no database module, no file path and no cookie name to
    reach for. It is not a filter over a real sink; there is no real sink behind
    it to filter.

Why the sink is swapped rather than the call sites guarded
----------------------------------------------------------
This exists for the Andrew Heiskell Braille and Talking Book Library demo, where
recording anything at all is not permitted, and where the study's IRB approval
does not reach. A scattered ``if not demo`` is one forgotten check away from a
data-collection incident at a venue that forbade it, and the forgotten check is
invisible until afterwards. Swapping the transport inverts that: a call site
someone adds next month and forgets to guard still calls ``current()``, still
gets the ``NullRecorder`` on the demo path, and still writes nothing. The way to
break the guarantee is to import ``db`` or ``study_db`` directly from a request
handler, which is a visible thing to do and is what
``tests/test_demo_no_recording.py`` looks for.

How the recorder is chosen
--------------------------
Two levels, both fail-closed, checked in this order:

1. **Per request.** A request that came from the demo page is bound to a
   ``NullRecorder`` in ``server.before_request``, before any handler runs. The
   demo page tags every outgoing request from a single shim installed ahead of
   the application's own code (``static/js/demo-bootstrap.js``), so a fetch
   added later inherits the tag the same way a call site inherits ``current()``.
   Requests under the ``/demo`` path prefix are demo requests whatever they
   carry.

2. **Per process.** With ``CAD_A11Y_DEMO=1`` in the environment the process
   recorder is a ``NullRecorder`` from import time, so *every* request records
   nothing regardless of what it carries or claims, and ``server.py`` does not
   register the study routes at all. This is the level the venue actually runs
   at: there is then no code path, tagged or untagged, that can write, because
   the object that knows how to write is never constructed.

What is deliberately still allowed
----------------------------------
* In-memory state. The viewer has to know its own depth and render mode. Nothing
  here touches that; the rule is only that nothing crosses the process boundary.
* Crash diagnostics with no interaction content -- see ``note_crash``. A stack
  trace is useful when the app dies in front of a room. The model name, the
  command history and the participant's uploads are not part of one, and this
  module is where that stays true.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from flask import Response, g, has_app_context, has_request_context


# The header the demo page's network shim stamps on every request it makes. A
# request carrying it is a demo request; see _bootstrap in demo-bootstrap.js.
DEMO_HEADER = "X-CAD-Demo"

# Everything served under this prefix is the demo, whatever headers survive a
# proxy. The prefix check is the part that keeps working if the shim does not.
DEMO_PATH_PREFIX = "/demo"

_TRUTHY = {"1", "true", "yes", "on"}


def demo_only_process() -> bool:
    """True when this whole process is a demo station and records nothing.

    Read from the environment on every call rather than cached, so a test can set
    it, and so the answer shown in ``/health`` and in the page banner is the live
    one rather than one fixed at import.
    """
    return os.getenv("CAD_A11Y_DEMO", "0").strip().lower() in _TRUTHY


class NullRecorder:
    """Records nothing, and has nothing to record with.

    Every method is a no-op returning the same shape its persistent counterpart
    returns. ``writes`` counts the calls that *would* have written, purely so a
    test can assert the demo path reached the recorder and still produced no
    output; nothing is derived from it and it never leaves memory.
    """

    records = False
    name = "null"

    def __init__(self) -> None:
        self.writes = 0
        self._lock = threading.Lock()

    def _seen(self) -> None:
        with self._lock:
            self.writes += 1

    # -- interaction data ---------------------------------------------------
    def render(self, **_kwargs: Any) -> None:
        self._seen()

    def page_event(self, *_args: Any, **_kwargs: Any) -> None:
        self._seen()

    def braille_event(self, _event: dict[str, Any]) -> None:
        self._seen()

    def study_render(self, *_args: Any, **_kwargs: Any) -> None:
        self._seen()

    def print_render(self, *_args: Any, **_kwargs: Any) -> None:
        self._seen()

    # -- identity -----------------------------------------------------------
    def touch_session(self, *_args: Any, **_kwargs: Any) -> None:
        self._seen()

    def identify_session(self, *_args: Any, **_kwargs: Any) -> None:
        self._seen()

    def register_model(self, *_args: Any, **_kwargs: Any) -> None:
        self._seen()

    def forget_model(self, *_args: Any, **_kwargs: Any) -> bool:
        self._seen()
        return False

    def attach_session_cookie(self, response: Response, _session_id: str) -> Response:
        """Returns the response untouched: no cookie, so nothing survives the tab."""
        self._seen()
        return response


class PersistentRecorder:
    """Writes to the real sinks.

    The sink modules are injected rather than imported, so this file has no
    import edge to ``db`` or ``study_db`` and ``server.py`` stays the only place
    that wires them together.
    """

    records = True
    name = "persistent"

    def __init__(
        self,
        *,
        analytics: Any,
        braille_writer: Callable[[dict[str, Any]], None],
        study_render_writer: Callable[..., None],
        print_writer: Callable[..., None],
        cookie_writer: Callable[[Response, str], Response],
    ) -> None:
        self._analytics = analytics
        self._braille_writer = braille_writer
        self._study_render_writer = study_render_writer
        self._print_writer = print_writer
        self._cookie_writer = cookie_writer

    # -- interaction data ---------------------------------------------------
    def render(self, **kwargs: Any) -> None:
        self._analytics.record_render(**kwargs)

    def page_event(self, session_id: str | None, event_type: str, event_data: Any) -> None:
        self._analytics.record_page_event(session_id, event_type, event_data)

    def braille_event(self, event: dict[str, Any]) -> None:
        self._braille_writer(event)

    def study_render(self, params: dict[str, Any], *, model_stem: str, cache_hit: bool) -> None:
        self._study_render_writer(params, model_stem=model_stem, cache_hit=cache_hit)

    def print_render(self, params: dict[str, Any], result: Any, image: Any) -> None:
        self._print_writer(params, result, image)

    # -- identity -----------------------------------------------------------
    def touch_session(self, session_id: str) -> None:
        self._analytics.upsert_session(session_id)

    def identify_session(
        self, session_id: str, email: str | None, *, consent: bool, is_workshop: bool = False
    ) -> None:
        self._analytics.save_session_identifier(
            session_id, email, consent_given=consent, is_workshop=is_workshop
        )

    def register_model(
        self,
        session_id: str,
        filename: str,
        original_name: str,
        file_size: int,
        sha256: str,
    ) -> None:
        self._analytics.register_model(session_id, filename, original_name, file_size, sha256)

    def forget_model(self, session_id: str, filename: str) -> bool:
        return bool(self._analytics.mark_model_deleted(session_id, filename))

    def attach_session_cookie(self, response: Response, session_id: str) -> Response:
        return self._cookie_writer(response, session_id)


# ---------------------------------------------------------------------------
# The injection point
# ---------------------------------------------------------------------------

# Starts null. server.py installs the persistent recorder at import, and only if
# this is not a demo-only process. Starting null rather than starting persistent
# means an import ordering mistake loses telemetry rather than leaking it.
_process_recorder: NullRecorder | PersistentRecorder = NullRecorder()

# The single NullRecorder handed to every demo request, so a test can read one
# counter rather than chasing per-request objects.
_null_recorder = NullRecorder()


def install(recorder: NullRecorder | PersistentRecorder) -> None:
    """Set the process-wide recorder. Called once, from server.py's import."""
    global _process_recorder
    _process_recorder = recorder


def process_recorder() -> NullRecorder | PersistentRecorder:
    return _process_recorder


def null_recorder() -> NullRecorder:
    return _null_recorder


def request_is_demo(request: Any) -> bool:
    """Whether this request came from the demo page.

    Either half is enough. The path prefix holds when a proxy strips the header;
    the header holds for the shared endpoints -- ``/render``, ``/models`` -- that
    the demo page reaches at their ordinary addresses.
    """
    path = str(getattr(request, "path", "") or "")
    if path == DEMO_PATH_PREFIX or path.startswith(DEMO_PATH_PREFIX + "/"):
        return True
    header = request.headers.get(DEMO_HEADER, "") if hasattr(request, "headers") else ""
    return str(header).strip().lower() in _TRUTHY


def bind_null() -> None:
    """Bind this request to the null recorder. Called from before_request."""
    if has_app_context():
        g._cad_recorder = _null_recorder


def current() -> NullRecorder | PersistentRecorder:
    """The recorder every write must go through.

    A request bound to the null recorder wins over whatever the process is doing,
    so a demo client is silent even on a server that is recording for others.
    """
    if has_request_context() or has_app_context():
        bound = g.get("_cad_recorder", None) if hasattr(g, "get") else None
        if bound is not None:
            return bound
    return _process_recorder


def recording_here() -> bool:
    """Whether the current context would write anything. Reported by /health and
    by /demo/status, so the person running the event can check it from the UI
    rather than from the source."""
    return bool(current().records)


# ---------------------------------------------------------------------------
# Crash diagnostics
# ---------------------------------------------------------------------------

# Where a crash note goes when one is written at all. Deliberately not under
# data/logs: it is not session data and must not be mistaken for it.
def _crash_log_path() -> Path | None:
    raw = os.getenv("CAD_A11Y_CRASH_LOG", "").strip()
    return Path(raw) if raw else None


# Keys that may appear in a crash note. Anything else is dropped rather than
# redacted, because a redaction list has to be updated when a caller adds a
# field and an allowlist does not.
_CRASH_ALLOWED_KEYS = frozenset({"where", "exc_type", "traceback", "python", "platform"})

# Substrings that must never appear in a crash note's text. A traceback can carry
# a model path in a frame's local variables under some formatters; the note is
# dropped entirely rather than shipped partly scrubbed.
_CRASH_FORBIDDEN = ("model_stem", "current_model", "participant", "event_data", "viewer_state")


def note_crash(where: str, error: BaseException, *, traceback_text: str = "") -> dict[str, Any] | None:
    """Record that the app fell over, and nothing about what anyone was doing.

    Allowed: where in the code it happened, the exception's class name, the
    formatted traceback. Not allowed, and structurally absent rather than
    filtered out later: the model, the command history, the request body, the
    participant, the upload. If the traceback text mentions any of those it is
    dropped whole -- a crash note is a convenience, and a partial one is not
    worth the risk of carrying interaction content off a machine at a venue that
    forbade it.

    Returns the note (also written to CAD_A11Y_CRASH_LOG if that is set), or None
    if there was nothing safe to say.
    """
    note = {
        "where": str(where),
        "exc_type": type(error).__name__,
        "traceback": str(traceback_text or ""),
    }
    note = {k: v for k, v in note.items() if k in _CRASH_ALLOWED_KEYS}
    blob = json.dumps(note, ensure_ascii=False)
    if any(term in blob for term in _CRASH_FORBIDDEN):
        return None

    path = _crash_log_path()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(blob + "\n")
        except OSError:
            pass
    return note
