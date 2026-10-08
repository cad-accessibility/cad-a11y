"""The ``/studies/<slug>`` endpoints: a guided run of one study's protocol.

Two views share one session:

* ``GET /studies/<slug>`` -- what the participant uses. It is the ordinary
  viewer, with the model chooser removed (models load themselves, one per step)
  and a small study region added carrying the current step and an "I am ready to
  move on" button.
* ``GET /studies/<slug>/control`` -- what the experimenter uses. The script to
  read aloud, which printed model to hand over, the answer key for the current
  task, and the control that advances the step.

They stay in step over Server-Sent Events, so the experimenter can drive the
session from their own machine while the participant works on theirs, with
their own screen reader and braille display.

Which study, and whether it is served at all, comes from the registry. A study
that is not open has no participant page and accepts no session; a closed one
keeps its panel and exports so its data can be downloaded; anything else is a
404 at every address here.

Two halves, split by address
----------------------------
Everything the participant's page calls is at ``/studies/<slug>/...`` and
needs nothing but the session's join code. Everything the panel calls is under
``/studies/<slug>/control/...`` and needs the panel token: presented once at
sign-in, which sets a cookie scoped to that path, or sent as ``X-Study-Token``
by a script. The split is by address rather than by inspecting each request,
so a participant's page never receives the panel's state, even in a browser
that is signed in to the panel -- which is what happens when an experimenter
hands their own laptop over in a one-device session.

The token used to be optional and the panel open by default. Neither server
set it, so anyone who found the address could advance a live session, read the
answer key and download every log. Now a study without a token hash is not
served at all.

Several sessions at once
------------------------
One deployment serves the whole team, so two experimenters in different cities
can be running participants of the same study at the same moment. Every request
names the session it means: a participant's browser carries the key minted at
enrolment (``?s=KEY``), and a panel carries the session id it started. A panel
falls back to the single active session when there is exactly one; with more
than one active it refuses to guess. The SSE fan-out is scoped the same way.

The failure mode this prevents is worth remembering: with a single global
"active session", starting a second one abandoned the first mid-task,
re-pointed its participant's page at the new session's step, loaded a different
model onto their braille display, and logged their keypresses against the other
participant.

What is recorded, and what is not
---------------------------------
Interactions and their timings, keyed to the participant code: keypresses,
renders, step advances, model loads, readiness signals, device connections.
Questionnaires are asked aloud and recorded on the experimenter's own sheet.
See ``store``.

What the participant is never shown
-----------------------------------
Model names can give the answer away -- "lego_2x4" tells you the brick got
longer before you have felt anything. The participant payload carries the
model's stem, because a model has to be addressed by name to be addressed safely
at all (#123), plus a neutral label ("Second object") which is the only one of
the two the interface ever displays or announces. The task description, the
answer key and the experimenter's script are withheld outright, in
``_participant_state``, rather than by asking the front-end not to render them.
"""

from __future__ import annotations

import io
import json
import logging
import math
import queue as _queue_module
import sqlite3
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    g,
    jsonify,
    request,
    send_file,
    stream_with_context,
)

from . import export, protocol, registry, tokens
from .registry import StudyRuntime
from .store import idle_timeout_seconds, now
from .version import app_version

logger = logging.getLogger(__name__)

studies_bp = Blueprint("studies", __name__)

PANEL_COOKIE = "cad_study_panel"

# Set by server.py at registration; avoids importing server here (it imports us).
_model_list_provider: Callable[[], list[str]] | None = None
_repo_root_provider: Callable[[], Path] | None = None


def set_model_list_provider(provider: Callable[[], list[str]]) -> None:
    global _model_list_provider
    _model_list_provider = provider


def set_repo_root_provider(provider: Callable[[], Path]) -> None:
    global _repo_root_provider
    _repo_root_provider = provider


def _model_list() -> list[str]:
    if _model_list_provider is None:
        return []
    try:
        return list(_model_list_provider() or [])
    except Exception:  # noqa: BLE001 - an unreadable model list is an empty one
        return []


def _model_index(stem: str | None) -> int | None:
    if not stem:
        return None
    try:
        return _model_list().index(stem)
    except ValueError:
        return None


def _repo_root() -> Path:
    if _repo_root_provider is None:
        return Path(__file__).resolve().parent.parent.parent
    return _repo_root_provider()


# ---------------------------------------------------------------------------
# Which study
# ---------------------------------------------------------------------------


@studies_bp.url_value_preprocessor
def _pull_study(endpoint: str | None, values: dict[str, Any] | None) -> None:
    slug = (values or {}).pop("slug", None)
    g.study_runtime = registry.runtime(slug) if slug else None


@studies_bp.before_request
def _require_served_study():
    # Not served and never existed look the same from outside: a retired study's
    # addresses answer exactly as an unknown slug's do.
    if g.get("study_runtime") is None:
        return _not_found()
    return None


def _runtime() -> StudyRuntime:
    return g.study_runtime


def _not_found():
    return jsonify({"status": "error", "message": "Not found"}), 404


def require_open(view):
    """Participant pages and anything that starts or moves a session: an open
    study only. A closed study answers 404 here, the same as one that is not
    served, because to a participant it is gone."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _runtime().is_open:
            return _not_found()
        return view(*args, **kwargs)

    return wrapper


NO_DATA_MESSAGE = "This server has no data for this study."


def require_data(view):
    """Anything the panel reads from the study's database. A closed study's
    database is only ever read, so when this server does not have it there is
    nothing to read, and saying so is the answer: it used to be created empty,
    and the exports then handed over an empty study with a correct checksum
    (#258 review). An open study's database is made when the server starts."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _runtime().store.db_path.is_file():
            return jsonify({"status": "error", "message": NO_DATA_MESSAGE}), 404
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Access to the panel
# ---------------------------------------------------------------------------


def _authorised() -> bool:
    runtime = _runtime()
    header = (request.headers.get("X-Study-Token") or "").strip()
    if header and tokens.verify(header, runtime.study.token_hash):
        return True
    return runtime.panel_session_valid(request.cookies.get(PANEL_COOKIE))


def require_panel(view):
    """The panel's routes: a signed-in panel, or the token in a header.

    A POST must also be JSON. The cookie is SameSite=Strict, so another site
    cannot make a browser send it; requiring a JSON body is the second lock on
    the same door, since a plain cross-site form cannot send one.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _authorised():
            return jsonify(
                {
                    "status": "error",
                    "signed_in": False,
                    "message": "Sign in to the panel with this study's token.",
                }
            ), 401
        if request.method == "POST" and not request.is_json:
            return jsonify({"status": "error", "message": "Send JSON."}), 415
        return view(*args, **kwargs)

    return wrapper


def _panel_cookie_path() -> str:
    return f"/studies/{_runtime().slug}/control"


def _request_is_https() -> bool:
    # Behind the department's proxy the app itself is plain http, so the
    # proxy's word for it is what says whether the browser is on https.
    return request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


@studies_bp.route("/studies/<slug>/control/sign-in", methods=["POST"])
def study_sign_in():
    runtime = _runtime()
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    if not tokens.verify(token, runtime.study.token_hash):
        return jsonify(
            {"status": "error", "message": "That is not this study's panel token."}
        ), 403
    response = jsonify({"status": "success"})
    response.set_cookie(
        PANEL_COOKIE,
        runtime.start_panel_session(),
        max_age=registry.PANEL_SESSION_SECONDS,
        path=_panel_cookie_path(),
        httponly=True,
        samesite="Strict",
        secure=_request_is_https(),
    )
    return response


@studies_bp.route("/studies/<slug>/control/signed-in", methods=["GET"])
def study_signed_in():
    """Whether this browser is signed in, as a 200 either way.

    The panel asks this before anything else. Asking for its config instead
    and reading the 401 works too, but a browser logs every 401 as a console
    error, and a fresh panel would then look broken to anyone with the console
    open, and to the accessibility check in CI, which fails on any.
    """
    return jsonify({"signed_in": _authorised(), "status": _runtime().served_as.value}), 200


@studies_bp.route("/studies/<slug>/control/sign-out", methods=["POST"])
def study_sign_out():
    _runtime().end_panel_session(request.cookies.get(PANEL_COOKIE))
    response = jsonify({"status": "success"})
    response.delete_cookie(PANEL_COOKIE, path=_panel_cookie_path())
    return response


# ---------------------------------------------------------------------------
# Live sync
#
# One queue per connected browser. Every state change broadcasts the whole state
# rather than a delta: a client that misses a message (reconnect, sleep, proxy
# hiccup) is corrected by the next one instead of drifting, and there is no
# ordering to get wrong.
# ---------------------------------------------------------------------------


def _push(runtime: StudyRuntime, role: str, study_session_id: int, payload: dict[str, Any]) -> None:
    message = f"data: {json.dumps(payload)}\n\n"
    with runtime.subscribers_lock:
        targets = [
            s
            for s in runtime.subscribers
            # Scoped to one session. Without this every step change reached every
            # connected browser, so advancing one session moved another
            # participant's page -- and loaded a different model onto their
            # braille display mid-exploration.
            if s.get("role") == role and s.get("study_session_id") == study_session_id
        ]
    for subscriber in targets:
        try:
            subscriber["queue"].put_nowait(message)
        except _queue_module.Full:
            # A client that cannot keep up gets the next broadcast instead; the
            # payload is a full state, so nothing is lost by dropping this one.
            pass


def _broadcast(runtime: StudyRuntime, session: dict[str, Any] | None) -> None:
    """Push the current state to everyone attached to *this* session."""
    if not session:
        return
    session_id = int(session["id"])
    _push(runtime, "experimenter", session_id, _experimenter_state(runtime, session))
    _push(runtime, "participant", session_id, _participant_state(runtime, session))


def _attached_participants(runtime: StudyRuntime, study_session_id: int | None) -> int:
    if study_session_id is None:
        return 0
    with runtime.subscribers_lock:
        return sum(
            1
            for s in runtime.subscribers
            if s.get("role") != "experimenter" and s.get("study_session_id") == study_session_id
        )


# ---------------------------------------------------------------------------
# Session resolution
#
# Several sessions can run at once, so "the active session" is not a thing any
# request can ask for. Every request says which session it means:
#
#   participant  a key, minted at enrolment, carried in the page's URL
#   experimenter a study_session_id, which the panel holds from the moment it
#                started the session
# ---------------------------------------------------------------------------


class SessionAmbiguous(Exception):
    """More than one session is active and the request did not say which."""

    def __init__(self, count: int) -> None:
        super().__init__(f"{count} sessions are active")
        self.count = count


def _participant_key() -> str:
    return (
        request.args.get("s")
        or request.headers.get("X-Study-Key")
        or ((request.get_json(silent=True) or {}).get("participant_key") if request.is_json else None)
        or ""
    ).strip().upper()


def _resolve_participant_session(runtime: StudyRuntime) -> dict[str, Any] | None:
    """The session a participant's browser belongs to, by its join code.

    The code is always required, even when only one session is running. It used
    to fall back to "the single active session", which meant the procedure
    changed depending on how many sessions happened to be up -- and a page that
    attached that way held nothing of its own, so when its session ended it
    drifted onto the next one. One rule, every time: enter the code.
    """
    key = _participant_key()
    if not key:
        return None
    session = runtime.store.get_session_by_key(key)
    return session if session and session.get("status") == "active" else None


def _participant_session_for_display(runtime: StudyRuntime) -> dict[str, Any] | None:
    """The session this browser is bound to, whatever state it is in.

    The strict resolver above returns nothing once a session ends, which is right
    for logging -- a finished session accepts no more events. But the page needs
    to tell "your session is over" apart from "that code matched nothing", and
    with only the strict answer it showed the code prompt again to a participant
    who had just finished.
    """
    key = _participant_key()
    return runtime.store.get_session_by_key(key) if key else None


def _resolve_experimenter_session(runtime: StudyRuntime) -> dict[str, Any] | None:
    """The session a control panel is driving. Raises if ambiguous."""
    raw = (
        request.args.get("study_session_id")
        or request.headers.get("X-Study-Session")
        or ((request.get_json(silent=True) or {}).get("study_session_id") if request.is_json else None)
    )
    if raw not in (None, ""):
        try:
            named = runtime.store.get_study_session(int(raw))
        except (TypeError, ValueError):
            named = None
        if named:
            return named
        # A panel pointing at a session that no longer exists falls through to
        # the rules below rather than showing nothing, so a stale tab recovers
        # instead of sitting on an empty enrolment form beside a running session.

    active = runtime.store.list_active_sessions()
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise SessionAmbiguous(len(active))
    return None


def _ambiguous_response(error: SessionAmbiguous):
    return jsonify(
        {
            "status": "error",
            "ambiguous": True,
            "active_sessions": error.count,
            "message": (
                f"{error.count} study sessions are running. This request has to say "
                f"which one it means."
            ),
        }
    ), 409


def _steps_for(runtime: StudyRuntime, session: dict[str, Any] | None) -> list[dict[str, Any]]:
    return protocol.resolve_steps(runtime.study, (session or {}).get("task_order") or [])


def _clamped_index(session: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    if not steps:
        return 0
    return max(0, min(int(session.get("step_index") or 0), len(steps) - 1))


def _current_step(runtime: StudyRuntime, session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not session:
        return None
    steps = _steps_for(runtime, session)
    if not steps:
        return None
    return steps[_clamped_index(session, steps)]


# ---------------------------------------------------------------------------
# State payloads
# ---------------------------------------------------------------------------


def _participant_model(runtime: StudyRuntime, step: dict[str, Any] | None) -> dict[str, Any] | None:
    """The load instruction for the participant view: which model, and what to
    call it in front of the participant.

    ``stem`` is the real model name, because that is the only safe way to
    address a model. This used to send a position in the server's model list
    instead, so the name never reached the participant's browser at all -- but
    that list is rebuilt whenever anyone uploads a file, so a window holding a
    number silently began rendering a different model (#123). Correctness of
    *which object is under the participant's fingers* outranks the neatness of
    withholding a string they never see.

    What protects the participant is ``label``: it is deliberately generic, it
    comes from the study's ``model_labels``, and it is the only one of the two
    the interface ever displays or announces. See the study-mode branch in
    viewer.js, which keeps the stem out of the status bar and out of every
    announcement.
    """
    if not step or not step.get("model"):
        return None
    model = step["model"]
    stem = model.get("model")
    if not stem or _model_index(stem) is None:
        return None
    label = runtime.study.model_labels.get(str(model.get("version")), "Object")
    return {"stem": stem, "label": label}


def _participant_state(runtime: StudyRuntime, session: dict[str, Any] | None) -> dict[str, Any]:
    """What the participant's browser is allowed to know.

    Carries no task description, no answer key and no experimenter script --
    see the module docstring.
    """
    defaults = runtime.study.viewer_defaults
    if not session:
        # "No session" has two causes and the page has to tell them apart: a
        # browser that has not been given a code yet, and one whose code matches
        # nothing. Both used to answer with the same object, so a mistyped or
        # wiped-out code looked exactly like never having joined -- the page
        # showed the code prompt with no hint that what was entered was refused.
        return {
            "active": False,
            "unknown_code": bool(_participant_key()),
            "viewer_defaults": defaults,
        }
    steps = _steps_for(runtime, session)
    step = _current_step(runtime, session)
    model = _participant_model(runtime, step)
    return {
        "active": session.get("status") == "active",
        "study_session_id": session.get("id"),
        # Their own anonymous code. Not a leak -- it says nothing about the
        # models -- and on one machine the experimenter is reading this screen
        # and wants to note which session was just recorded.
        "participant_code": session.get("participant_code"),
        # Handed back so a browser that attached without one can bind itself to
        # this session and stay bound. Not a secret: it identifies the session
        # this participant is already in.
        "participant_key": session.get("participant_key"),
        "status": session.get("status"),
        "step_index": _clamped_index(session, steps),
        "step_count": len(steps),
        "mode": session.get("mode"),
        "step_id": (step or {}).get("id"),
        "part_id": (step or {}).get("part_id"),
        "part_title": (step or {}).get("part_title"),
        "title": (step or {}).get("title"),
        "text": (step or {}).get("participant_text"),
        "model": model,
        # A step whose object the server does not have. The page says so, rather
        # than leaving the last object on the display under text that tells the
        # participant to explore it (#258 review). That it is missing, never
        # which object it was.
        "model_unavailable": bool((step or {}).get("model")) and model is None,
        "viewer_defaults": defaults,
    }


def _experimenter_state(runtime: StudyRuntime, session: dict[str, Any] | None) -> dict[str, Any]:
    """Everything, for the control panel."""
    study = runtime.study
    store = runtime.store
    # Advisory: what the next participant will most likely be called, and which
    # tasks they are due. The real code comes from the id the database assigns
    # at enrolment, so two panels showing the same suggestion cannot collide.
    sequence_number = store.next_sequence_preview()
    base: dict[str, Any] = {
        "active": False,
        "study": study.slug,
        "study_status": runtime.served_as.value,
        "protocol_version": study.version,
        "logging": store.logging_health(),
        "attached_participants": 0,
        "suggested_participant_code": store.preview_next_code(),
        "suggested_task_order": protocol.assign_task_order(study, sequence_number),
        "next_sequence_number": sequence_number,
        "missing_models": _missing_models(runtime),
        # Every session currently running, so a panel that has just been opened
        # can join one instead of only being able to start another.
        "active_sessions": [
            {
                "study_session_id": other["id"],
                "participant_code": other["participant_code"],
                "participant_key": other["participant_key"],
                "session_number": other["session_number"],
                "step_index": other["step_index"],
                "started_at": other["started_at"],
                "is_current": bool(session and other["id"] == session.get("id")),
            }
            for other in store.list_active_sessions()
        ],
        "facilitator_prompts": study.facilitator_prompts,
        "strategy_prompts": study.strategy_prompts,
        "tasks": study.tasks,
        "design": protocol.design_preview(study),
    }
    if not session:
        return base

    steps = _steps_for(runtime, session)
    index = _clamped_index(session, steps)
    step = steps[index] if steps else None
    model = (step or {}).get("model") or {}
    session_id = int(session["id"])
    base.update(
        {
            "active": session.get("status") == "active",
            "study_session_id": session_id,
            "participant_code": session.get("participant_code"),
            "session_number": session.get("session_number"),
            "task_order": session.get("task_order"),
            "participant_id": session.get("participant_id"),
            "task_labels": [protocol.task_label(study, key) for key in session.get("task_order") or []],
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "step_started_at": session.get("step_started_at"),
            "log_path": session.get("log_path"),
            "mode": session.get("mode"),
            "participant_key": session.get("participant_key"),
            # The link this session's participant opens. With several sessions
            # running, the study's address alone cannot tell which one a browser
            # belongs to.
            "participant_path": (
                f"/studies/{study.slug}?s={session['participant_key']}"
                if session.get("participant_key")
                else f"/studies/{study.slug}"
            ),
            "attached_participants": _attached_participants(runtime, session_id),
            "step_index": index,
            "step_count": len(steps),
            "step": step,
            "steps": [
                {
                    "index": s["index"],
                    "id": s["id"],
                    "part_id": s.get("part_id"),
                    "part_title": s.get("part_title"),
                    "title": s.get("title"),
                }
                for s in steps
            ],
            # None, not False, on a step that loads nothing: "no model here" and
            # "the model this step needs is missing" are different situations, and
            # only the second is a problem worth warning about.
            "model_available": (
                _model_index(model.get("model")) is not None if model.get("model") else None
            ),
            "counts": store.session_counts(session_id),
            "participant_ready": runtime.ready_signals.get(session_id),
        }
    )
    return base


def _missing_models(runtime: StudyRuntime) -> list[str]:
    """Models the study uses that the server cannot currently serve.

    Checked at enrolment so a missing STL is a problem before the participant
    sits down, not when the step that needs it fails to put anything on the
    display.
    """
    models = set(_model_list())
    if not models:
        return []
    return [stem for stem in protocol.required_models(runtime.study) if stem not in models]


# ---------------------------------------------------------------------------
# Render hook, called from server.py
# ---------------------------------------------------------------------------


def record_render_for_request(params: dict[str, Any], *, model_stem: str, cache_hit: bool) -> None:
    """Record a /render call against a study session, if it came from one.

    The participant's viewer tags its render requests with ``X-Study`` (the
    study) and ``X-Study-Key`` (the session's join code). Recording here rather
    than in the client means a browser crash cannot lose the record of what was
    on the display, and it covers cache hits too -- a cached response still put
    a new image under the participant's fingers, so leaving those out would
    silently drop most of a fast arrow-key traversal.

    The join code rather than the session id, because ids are small sequential
    numbers: a render tagged with a guessed id used to be enough to write rows
    into someone else's session.

    Nothing in here may raise. It is called from inside ``render_view``'s try
    block, where an exception is turned into a 400 -- so a fault in the logging
    path would stop the participant's display updating at all. Study logging is
    important; it is not more important than the session continuing.
    """
    runtime = None
    try:
        slug = (request.headers.get("X-Study") or "").strip()
        runtime = registry.runtime(slug) if slug else None
        if runtime is None or not runtime.is_open:
            return
        _record_render(runtime, params, model_stem=model_stem, cache_hit=cache_hit)
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        if runtime is not None:
            runtime.store.note_external_failure(error)


def _record_render(
    runtime: StudyRuntime, params: dict[str, Any], *, model_stem: str, cache_hit: bool
) -> None:
    key = (request.headers.get("X-Study-Key") or "").strip().upper()
    if not key:
        return
    session = runtime.store.get_session_by_key(key)
    if not session or session.get("status") != "active":
        return

    orientation = params.get("orientation")
    runtime.store.record_render(
        int(session["id"]),
        # The stem the server actually rendered, not something re-derived from
        # the request: a model is addressed by name now precisely because a list
        # position means a different file after anyone uploads one (#123).
        model=model_stem or None,
        view=str(params.get("view") or "") or None,
        render_mode=str(params.get("renderMode") or "") or None,
        layout_mode=str(params.get("mode") or "") or None,
        depth=params.get("depth"),
        zoom=params.get("zoom"),
        input_source=str(params.get("input_source") or "") or None,
        cache_hit=cache_hit,
        orientation=orientation if isinstance(orientation, dict) else None,
        # Worked out under the store's lock, from the same read as the step
        # clock, so a render racing a step change is filed under one step with
        # that step's clock (#258 review).
        resolve_step=lambda current: _current_step(runtime, current),
        only_if_active=True,
        **_axis_fields(params),
    )


def _axis_fields(params: dict[str, Any]) -> dict[str, Any]:
    """The axis mode and where the cut was along its axis, as the viewer reported
    them with the render (#185), each checked against what it can be. Anything
    else is left blank rather than stored: a column is only worth grouping on if
    its values are the ones the viewer can actually send."""

    def pick(key: str, allowed: set[str]) -> str | None:
        value = params.get(key)
        return value if isinstance(value, str) and value in allowed else None

    def number(key: str) -> float | None:
        value = params.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) else None

    return {
        "axis_mode": pick("axis_mode", {"turn", "xyz"}),
        "cut_axis": pick("cut_axis", {"x", "y", "z"}),
        "cut_side": pick("cut_side", {"above", "below", "front", "back", "right", "left"}),
        "cut_percent": number("cut_percent"),
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@studies_bp.route("/studies/<slug>", methods=["GET"], strict_slashes=False)
@require_open
def study_participant_view():
    """The participant's view: the ordinary viewer in study mode.

    Same HTML as the viewer -- the interface a participant learns during
    onboarding is the interface they use for the tasks, which is the point of
    running studies on the real viewer rather than a special build of it.
    viewer.js keys off the path to hide the model chooser and show the study
    region.
    """
    return send_file(_repo_root() / "accessible-3d-viewer.html")


@studies_bp.route("/studies/<slug>/control", methods=["GET"], strict_slashes=False)
def study_control_view():
    """The panel page itself needs no token: it holds no data, and it is where
    the token is entered. Everything it loads after that does."""
    return send_file(_repo_root() / "study-control.html")


@studies_bp.route("/studies/<slug>/control/config", methods=["GET"])
@require_panel
def study_config():
    runtime = _runtime()
    payload = protocol.config_payload(runtime.study)
    payload["status"] = runtime.served_as.value
    payload["app_version"] = app_version()
    payload["missing_models"] = _missing_models(runtime)
    payload["logging"] = runtime.store.logging_health()
    # Whether there is anything to download. A closed study's database is never
    # created here, so on a server without it the panel says so up front.
    payload["has_data"] = runtime.store.db_path.is_file()
    return jsonify(payload), 200


# ---------------------------------------------------------------------------
# Closing sessions nobody closed
#
# A session ends when the experimenter presses End, or -- in a solo session --
# when the participant finishes the last step. Neither happens if the panel tab
# is simply closed, and on both servers that is what usually happened to the
# comparison study: of 14 sessions across staging and production, 12 were still
# 'active', one of them stuck at step 19 of 22 and one at step 0 for three days.
#
# So sessions are also closed by disuse. The sweep is lazy rather than a
# background thread: this server is a handful of requests an hour during a
# session and none between, so a thread would spend its life asleep, and a
# thread that dies takes the cleanup with it silently. Throttled, because
# otherwise every poll of the panel would re-scan.
# ---------------------------------------------------------------------------

# How long counts as gone is store.idle_timeout_seconds, twelve hours unless
# STUDY_SESSION_IDLE_HOURS says otherwise; the export reads the same number.
#
# Only while a study is open. A closed one answers none of the routes that run
# the sweep, and its database is only read, so a session it left active stays
# active; the archive puts those in long_incomplete.csv (#258 review).
_SWEEP_INTERVAL_SECONDS = 300


def sweep_idle_sessions(runtime: StudyRuntime, *, force: bool = False) -> list[dict[str, Any]]:
    """Close abandoned sessions, at most once every few minutes.

    Never raises: this runs at the top of ordinary requests, and a cleanup fault
    must not take down the panel or the participant's page with it.
    """
    with runtime.sweep_lock:
        now_ts = time.monotonic()
        if not force and now_ts - runtime.last_sweep_at < _SWEEP_INTERVAL_SECONDS:
            return []
        runtime.last_sweep_at = now_ts

    try:
        closed = runtime.store.close_idle_sessions(idle_timeout_seconds())
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        runtime.store.note_external_failure(error)
        return []
    for session in closed:
        logger.info(
            "study %s: closed abandoned session %s (%s), idle %ss",
            runtime.slug,
            session.get("id"),
            session.get("participant_code"),
            session.get("idle_seconds"),
        )
        # A panel or participant page still attached is told, rather than being
        # left showing a session the database no longer considers open.
        _broadcast(runtime, runtime.store.get_study_session(int(session["id"])))
    return closed


def task_set_status(runtime: StudyRuntime) -> dict[str, Any]:
    """Which task sets this round has already run, and which are still to do.

    A round is every row of the design run once. "Used" is not a stored flag --
    there is nothing to keep in step and nothing to reset -- it is derived from
    the completed sessions in the database: a set counts as used when it has
    been completed more often than the least-run set has. So the moment every
    set has been through once, they are all level again and the whole list
    comes back unstruck, which is the fresh round. Nobody has to notice the
    rollover or press anything to make it happen.

    Deriving it also means the two things that go wrong in a study do the right
    thing on their own. A session started and abandoned did not consume its set,
    because only completed sessions are counted. And an experimenter who has to
    deviate -- a print is missing, the participant saw one of these in a pilot --
    just picks a struck-through set; the counts absorb it, and the list keeps
    telling the truth about what has actually been run.

    ``in_progress`` is the one thing not derived from history: it is how many
    sessions are running on that set right now. Two panels open at once both see
    the same free sets, and without this they would both pick the first one.
    """
    # Before counting, not after: an abandoned session that still says it is
    # active is exactly what makes "running on it now" untrustworthy, and this
    # is the screen where that claim is acted on.
    sweep_idle_sessions(runtime)

    sets = protocol.task_sets(runtime.study)
    completed = runtime.store.count_task_orders("completed")

    running: dict[str, int] = {}
    for other in runtime.store.list_active_sessions():
        key = protocol.set_id(other.get("task_order") or [])
        running[key] = running.get(key, 0) + 1

    counts = [completed.get(entry["id"], 0) for entry in sets]
    # The least-run set defines the current round. Every set level with it is
    # still to do; anything above it has been used since the last rollover.
    floor = min(counts) if counts else 0
    return {
        "round": floor + 1,
        "sets_per_round": len(sets),
        "remaining": sum(1 for count in counts if count == floor),
        "sets": [
            {
                **entry,
                "completed_count": completed.get(entry["id"], 0),
                "used": completed.get(entry["id"], 0) > floor,
                "in_progress": running.get(entry["id"], 0),
            }
            for entry in sets
        ],
    }


@studies_bp.route("/studies/<slug>/control/sets", methods=["GET"])
@require_panel
@require_open
def study_sets():
    """The task sets the experimenter chooses from when starting a session."""
    return jsonify(task_set_status(_runtime())), 200


@studies_bp.route("/studies/<slug>/state", methods=["GET"])
@require_open
def study_state():
    """The participant's view of their session. Never the panel's, whoever
    asks: the panel has its own address for that."""
    runtime = _runtime()
    return jsonify(_participant_state(runtime, _participant_session_for_display(runtime))), 200


@studies_bp.route("/studies/<slug>/control/state", methods=["GET"])
@require_panel
@require_data
def study_control_state():
    runtime = _runtime()
    try:
        return jsonify(_experimenter_state(runtime, _resolve_experimenter_session(runtime))), 200
    except SessionAmbiguous as error:
        return _ambiguous_response(error)


@studies_bp.route("/studies/<slug>/control/session/start", methods=["POST"])
@require_panel
@require_open
def study_session_start():
    """Enroll a participant and begin a session.

    A participant code is minted automatically (P01, P02, ...) so every run gets a
    new id without the experimenter having to decide on one; they can still send
    their own, and a returning participant keeps their code and with it their
    place in the rotation.
    """
    runtime = _runtime()
    study = runtime.study
    data = request.get_json(silent=True) or {}

    # An empty code means "a new participant": the database assigns the id and
    # derives the label from it, so simultaneous enrolments cannot land on the
    # same one. A code that is given names an existing participant, or creates
    # one under that name.
    code = str(data.get("participant_code") or "").strip()
    if code and (len(code) > 32 or any(ch in code for ch in "/\\.:")):
        return jsonify({"status": "error", "message": "Invalid participant code"}), 400

    # `or 1` would be wrong here: it turns an explicit 0 into 1 and accepts an
    # invalid request as a valid one.
    raw_session_number = data.get("session_number")
    if raw_session_number in (None, ""):
        raw_session_number = 1
    try:
        session_number = int(raw_session_number)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "session_number must be a number"}), 400
    if session_number < 1:
        return jsonify({"status": "error", "message": "session_number must be 1 or more"}), 400

    # Checked in full before anything is written. The task order used to be
    # checked after the participant had been created, so a refused request left
    # a participant with no session and moved every later participant one place
    # along the rotation (#183).
    requested_order = data.get("task_order")
    task_order: list[str] | None = None
    if isinstance(requested_order, list) and requested_order:
        task_order = [str(key) for key in requested_order]
        # All or nothing. Unknown names used to be filtered out and the rest
        # accepted, so a request naming a task the protocol no longer has -- a
        # stale panel, a bookmarked call -- started a session one task short
        # instead of failing, and the shortfall was only visible as a task that
        # put nothing on the display.
        unknown = [key for key in task_order if key not in study.tasks]
        if unknown:
            return jsonify(
                {
                    "status": "error",
                    "message": f"task_order names unknown tasks: {', '.join(unknown)}",
                }
            ), 400
    elif study.tasks and not study.design:
        return jsonify({"status": "error", "message": "The study has no task sets to assign"}), 400

    # Tidy before enrolling, so a new session is not started alongside a row of
    # abandoned ones that will be reported to this experimenter as running.
    sweep_idle_sessions(runtime)

    # Sessions already running are left alone. Starting one used to abandon the
    # others, on the assumption that one deployment meant one session -- so a
    # second experimenter starting a session silently ended the first mid-task.
    try:
        participant, session = runtime.store.enroll(
            code=code or None,
            session_number=session_number,
            choose_task_order=lambda participant_id: (
                task_order
                if task_order is not None
                else protocol.assign_task_order(study, participant_id)
            ),
            protocol_version=study.version,
            protocol_hash=protocol.protocol_hash(study),
            app_version=app_version(),
        )
    except sqlite3.IntegrityError as error:
        # Said in the panel's words. The database's own text, "UNIQUE constraint
        # failed: ...", used to be shown to the experimenter as it was.
        logger.warning("study %s: session not started: %s", runtime.slug, error)
        if "session_number" in str(error):
            message = (
                f"{code or 'This participant'} already has a session {session_number}. "
                "Start it with the next session number."
            )
        else:
            message = "That participant code is already in use."
        return jsonify({"status": "error", "message": f"Could not start the session. {message}"}), 409
    except Exception as error:  # reported to the experimenter, not swallowed
        logger.exception("study %s: session not started", runtime.slug)
        return jsonify({"status": "error", "message": f"Could not start the session: {error}"}), 409

    runtime.store.record_event(
        int(session["id"]),
        "session_start",
        source="experimenter",
        event_data={
            "participant_id": participant.get("id"),
            "participant_code": session.get("participant_code"),
            "session_number": session_number,
            "task_order": session.get("task_order"),
            "protocol_version": study.version,
            "protocol_hash": session.get("protocol_hash"),
            "app_version": session.get("app_version"),
            "missing_models": _missing_models(runtime),
        },
        step_index=0,
    )
    runtime.ready_signals.pop(int(session["id"]), None)
    _broadcast(runtime, session)
    return jsonify({"status": "success", "state": _experimenter_state(runtime, session)}), 200


@studies_bp.route("/studies/<slug>/control/session/end", methods=["POST"])
@require_panel
@require_open
def study_session_end():
    runtime = _runtime()
    try:
        session = _resolve_experimenter_session(runtime)
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    if not session:
        return jsonify({"status": "error", "message": "No active session"}), 404
    data = request.get_json(silent=True) or {}
    steps = _steps_for(runtime, session)
    index = _clamped_index(session, steps)
    step = steps[index] if steps else None
    on_last_step = bool(steps) and index >= len(steps) - 1
    # Completed only from the last step. End used to record 'completed' at any
    # step, so a session stopped at step 3 of 22 went into long.csv and counted
    # as having used its task set (#258 review). Ended before the last step it
    # is abandoned: the set stays available, and the rows go to
    # long_incomplete.csv. A request may ask for 'abandoned' on the last step
    # too, and can never make an early end 'completed'.
    status = "completed" if on_last_step and data.get("status") != "abandoned" else "abandoned"
    reason = (
        "ended by the experimenter on the last step"
        if on_last_step
        else f"ended by the experimenter at step {index + 1} of {len(steps)}"
    )
    session_id = int(session["id"])
    ended = runtime.store.end_session(
        session_id,
        status=status,
        source="experimenter",
        event_data={"reason": reason},
        part_id=(step or {}).get("part_id"),
        step_id=(step or {}).get("id"),
        step_index=index,
    )
    runtime.ready_signals.pop(session_id, None)
    _broadcast(runtime, runtime.store.get_study_session(session_id))
    if not ended:
        # Said, not swallowed: ending a session that something else already
        # ended is how a second session_end used to get into the data.
        return jsonify(
            {"status": "error", "message": "That session has already ended."}
        ), 409
    return jsonify({"status": "success", "session_status": status}), 200


def _optional_index(value: Any) -> int | None:
    """A step index a page sent, or None when it sent none, or nonsense."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _advance(
    runtime: StudyRuntime, session: dict[str, Any], target: int, *, source: str
) -> dict[str, Any] | None:
    """Move a session to a step, recording it. Shared by the panel's Next button
    and by the participant's ready button in a single-device session, so both
    produce exactly the same record -- how a session was driven must not change
    what it logged."""
    store = runtime.store
    steps = _steps_for(runtime, session)
    if not steps:
        return None
    current = _clamped_index(session, steps)
    target = max(0, min(target, len(steps) - 1))

    session_id = int(session["id"])
    if target == current:
        # Nothing moved -- Next on the last step, Previous on the first. Recording
        # an advance from a step to itself puts a move in the log that never
        # happened, and "how many steps did this session take" then counts it.
        return store.get_study_session(session_id)
    step = steps[target]
    # One lock over the move and what it logs, so no render lands between the
    # step changing and the step_advance that says it did.
    with store.write_lock:
        store.set_step_index(session_id, target)
        store.record_event(
            session_id,
            "step_advance",
            source=source,
            event_data={
                "from_index": current,
                "from_step_id": steps[current]["id"],
                "to_index": target,
                "to_step_id": step["id"],
            },
            part_id=step.get("part_id"),
            step_id=step.get("id"),
            step_index=target,
        )
        if step.get("model"):
            store.record_event(
                session_id,
                "model_autoload",
                source="server",
                event_data={
                    "model": (step.get("model") or {}).get("model"),
                    "task_key": step.get("task_key"),
                    "version": (step.get("model") or {}).get("version"),
                    "available": _model_index((step.get("model") or {}).get("model")) is not None,
                },
                part_id=step.get("part_id"),
                step_id=step.get("id"),
                step_index=target,
            )
    runtime.ready_signals.pop(session_id, None)
    return store.get_study_session(session_id)


@studies_bp.route("/studies/<slug>/control/session/mode", methods=["POST"])
@require_panel
@require_open
def study_session_mode():
    """Switch a session between two machines and one.

    In 'solo' the experimenter and the participant share a laptop, so the panel
    is not reachable without taking the screen reader off the participant. The
    session then moves on when the participant presses "I am ready to move on"
    instead of waiting for a Next button nobody can get to.
    """
    runtime = _runtime()
    try:
        session = _resolve_experimenter_session(runtime)
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    if not session:
        return jsonify({"status": "error", "message": "No active session"}), 404

    data = request.get_json(silent=True) or {}
    mode = "solo" if str(data.get("mode")) == "solo" else "paired"
    session_id = int(session["id"])
    runtime.store.set_mode(session_id, mode)
    runtime.store.record_event(
        session_id,
        "session_mode",
        source="experimenter",
        event_data={"mode": mode},
        step_index=int(session.get("step_index") or 0),
    )
    refreshed = runtime.store.get_study_session(session_id)
    _broadcast(runtime, refreshed)
    return jsonify({"status": "success", "state": _experimenter_state(runtime, refreshed)}), 200


@studies_bp.route("/studies/<slug>/control/step/advance", methods=["POST"])
@require_panel
@require_open
def study_step_advance():
    """Move to another step. The experimenter drives this, never the participant.

    Accepts a direction or an absolute index, because going back matters: the
    protocol has no time limits, and an experimenter who advanced early needs to
    return without restarting the session.
    """
    runtime = _runtime()
    try:
        session = _resolve_experimenter_session(runtime)
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    if not session or session.get("status") != "active":
        return jsonify({"status": "error", "message": "No active session"}), 404

    steps = _steps_for(runtime, session)
    if not steps:
        return jsonify({"status": "error", "message": "Protocol has no steps"}), 500

    data = request.get_json(silent=True) or {}
    # The step the panel was showing when the press was made. Next and Back are
    # relative, so a double click, a held key or a second panel on the same
    # session each moved one step further than anyone meant, and the server
    # could not tell (#258 review). A press made against a step the session has
    # already left is refused, with the state as it now is.
    expected = data.get("from_index")
    if expected is not None:
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "from_index must be a number"}), 400
    requested = data.get("step_index")
    if requested is not None:
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "step_index must be a number"}), 400

    # Read, checked and moved under one lock, so two presses arriving together
    # cannot both pass the check against the same step.
    with runtime.store.write_lock:
        session = runtime.store.get_study_session(int(session["id"])) or session
        if session.get("status") != "active":
            return jsonify({"status": "error", "message": "No active session"}), 404
        current = _clamped_index(session, steps)
        if expected is not None and expected != current:
            return jsonify(
                {
                    "status": "error",
                    "message": f"Not moved: the session is on step {current + 1} now, not step {expected + 1}.",
                    "state": _experimenter_state(runtime, session),
                }
            ), 409
        if requested is not None:
            target = requested
        elif str(data.get("direction") or "next") == "previous":
            target = current - 1
        else:
            target = current + 1
        refreshed = _advance(runtime, session, target, source="experimenter")
    if refreshed is None:
        return jsonify({"status": "error", "message": "Protocol has no steps"}), 500
    _broadcast(runtime, refreshed)
    return jsonify(
        {
            "status": "success",
            "moved": int(refreshed.get("step_index") or 0) != current,
            "state": _experimenter_state(runtime, refreshed),
        }
    ), 200


@studies_bp.route("/studies/<slug>/step/ready", methods=["POST"])
@require_open
def study_step_ready():
    """The participant's "I am ready to move on" signal.

    Advisory in a paired session: it notifies the experimenter and is logged,
    and it does not advance anything. The protocol is experimenter-paced with no
    time limits, and a button that jumped the session forward would turn a stray
    keypress into lost data.
    """
    runtime = _runtime()
    store = runtime.store
    session = _resolve_participant_session(runtime)
    if not session:
        return jsonify({"status": "error", "message": "No active session"}), 404
    data = request.get_json(silent=True) or {}
    step = _current_step(runtime, session)
    session_id = int(session["id"])
    viewer_state = data.get("viewer_state")
    store.record_event(
        session_id,
        "participant_ready",
        source="participant",
        event_data={},
        part_id=(step or {}).get("part_id"),
        step_id=(step or {}).get("id"),
        step_index=(step or {}).get("index"),
        client_id=str(data.get("client_id") or "") or None,
        client_time=str(data.get("client_time") or "") or None,
        viewer_state=viewer_state if isinstance(viewer_state, dict) else None,
    )
    if session.get("mode") == "solo":
        # One laptop: the participant saying they are ready is the only signal
        # available, so it is what moves the session on. The readiness event
        # above is still recorded first, so the log reads the same as a session
        # run from a panel -- the difference is who pressed the button, not what
        # was written down.
        steps = _steps_for(runtime, session)
        expected = _optional_index(data.get("from_index"))
        with store.write_lock:
            # Read again under the lock, and checked against the step the page
            # was on: a second press for a step already left must not move the
            # session on a second time (#258 review).
            session = store.get_study_session(session_id) or session
            current = _clamped_index(session, steps)
            if session.get("status") != "active" or (expected is not None and expected != current):
                return jsonify({"status": "success", "advanced": False, "finished": False}), 200

            if current >= len(steps) - 1:
                # The last step, and no panel to close the session from. Without
                # this the session simply stopped: everything was recorded, but it
                # stayed open with no session_end and no completed_at, and the page
                # told the participant to keep exploring until an experimenter moved
                # them on.
                store.end_session(
                    session_id,
                    status="completed",
                    source="participant",
                    event_data={"reason": "finished the last step"},
                    part_id=(step or {}).get("part_id"),
                    step_id=(step or {}).get("id"),
                    step_index=current,
                )
                refreshed = None
            else:
                refreshed = _advance(runtime, session, current + 1, source="participant")
        if refreshed is None:
            runtime.ready_signals.pop(session_id, None)
            _broadcast(runtime, store.get_study_session(session_id))
            return jsonify({"status": "success", "advanced": False, "finished": True}), 200
        _broadcast(runtime, refreshed)
        return jsonify({"status": "success", "advanced": True, "finished": False}), 200

    runtime.ready_signals[session_id] = {
        "step_id": (step or {}).get("id"),
        "step_index": (step or {}).get("index"),
        "at": now(),
    }
    _push(runtime, "experimenter", session_id, _experimenter_state(runtime, store.get_study_session(session_id)))
    return jsonify({"status": "success", "advanced": False, "finished": False}), 200


@studies_bp.route("/studies/<slug>/step/back", methods=["POST"])
@require_open
def study_step_back():
    """The participant's own "go back a step" command, solo sessions only.

    In solo mode there is no separate panel to press Previous on, so the same
    exception the ready button makes for moving forward applies here: the person
    driving this browser is the only person running the session. A paired
    session refuses this -- the panel's token is what stops a participant
    rewinding the protocol themselves, and that must hold here too even though
    this route takes no token at all.
    """
    runtime = _runtime()
    session = _resolve_participant_session(runtime)
    if not session or session.get("mode") != "solo":
        return jsonify({"status": "error", "message": "No active solo session"}), 404

    steps = _steps_for(runtime, session)
    if not steps:
        return jsonify({"status": "error", "message": "Protocol has no steps"}), 500

    data = request.get_json(silent=True) or {}
    expected = _optional_index(data.get("from_index"))
    with runtime.store.write_lock:
        # As the ready button's: read again, and refused for a step already left.
        session = runtime.store.get_study_session(int(session["id"])) or session
        current = _clamped_index(session, steps)
        if session.get("status") != "active" or (expected is not None and expected != current):
            return jsonify({"status": "success", "moved": False, "reason": "moved_on"}), 200
        if current == 0:
            # Said back to the page, which tells the participant: B on the first
            # step used to do nothing at all (#258 review).
            return jsonify({"status": "success", "moved": False, "reason": "first_step"}), 200
        refreshed = _advance(runtime, session, current - 1, source="participant")
    if refreshed is None:
        return jsonify({"status": "error", "message": "Protocol has no steps"}), 500
    _broadcast(runtime, refreshed)
    return jsonify({"status": "success", "moved": True}), 200


# Events the participant's viewer may report. An allowlist, so a stray or
# malicious client cannot fill the study database with arbitrary event types.
_ALLOWED_PARTICIPANT_EVENTS = frozenset(
    {
        "keyboard",
        "ui_action",
        "announcement",
        "device",
        "model_loaded",
        "page_load",
        "page_unload",
        "error",
    }
)


@studies_bp.route("/studies/<slug>/event", methods=["POST"])
@require_open
def study_event():
    """Record one participant-side interaction.

    This is the half of the record the server cannot see: which key was pressed, as
    opposed to which render it happened to produce; what the screen reader was
    told; when a device connected or dropped. Every event carries the viewer state
    at that moment, so the JSONL line is self-contained.
    """
    runtime = _runtime()
    session = _resolve_participant_session(runtime)
    if not session:
        return jsonify({"status": "ignored", "message": "No active session"}), 200

    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in _ALLOWED_PARTICIPANT_EVENTS:
        return jsonify({"status": "error", "message": f"Unknown event_type '{event_type}'"}), 400

    event_data = data.get("event_data")
    if event_data is not None and not isinstance(event_data, dict):
        return jsonify({"status": "error", "message": "event_data must be an object"}), 400

    step = _current_step(runtime, session)
    viewer_state = data.get("viewer_state")
    runtime.store.record_event(
        int(session["id"]),
        event_type,
        source="participant",
        event_data=event_data or {},
        part_id=(step or {}).get("part_id"),
        step_id=(step or {}).get("id"),
        step_index=(step or {}).get("index"),
        client_id=str(data.get("client_id") or "") or None,
        client_time=str(data.get("client_time") or "") or None,
        viewer_state=viewer_state if isinstance(viewer_state, dict) else None,
    )
    return jsonify({"status": "success"}), 200


def _stream(runtime: StudyRuntime, role: str, session: dict[str, Any] | None) -> Response:
    """Server-Sent Events keeping both views in step.

    The experimenter and the participant are usually on different machines, so
    there has to be a push channel; polling would mean a step change landing up to
    a poll interval late, under the participant's fingers, with no explanation.

    A stream belongs to the session named when it connected. Without that every
    broadcast reached every browser, so advancing one session moved another
    participant's page.
    """
    session_id = int(session["id"]) if session else None

    def generate():
        client_queue: _queue_module.Queue[str] = _queue_module.Queue(maxsize=32)
        subscriber = {"queue": client_queue, "role": role, "study_session_id": session_id}
        with runtime.subscribers_lock:
            runtime.subscribers.append(subscriber)
        try:
            initial = (
                _experimenter_state(runtime, session)
                if role == "experimenter"
                else _participant_state(runtime, session)
            )
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    yield client_queue.get(timeout=25)
                except _queue_module.Empty:
                    yield ": heartbeat\n\n"  # keep the connection alive through proxies
        finally:
            with runtime.subscribers_lock:
                if subscriber in runtime.subscribers:
                    runtime.subscribers.remove(subscriber)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@studies_bp.route("/studies/<slug>/stream", methods=["GET"])
@require_open
def study_stream():
    runtime = _runtime()
    # The display resolver, not the strict one. A page reconnecting to a session
    # that has finished should be told it finished; the strict resolver returns
    # nothing for it, and this stream's opening message would then overwrite
    # "the session has ended" with "that code was not recognised".
    return _stream(runtime, "participant", _participant_session_for_display(runtime))


@studies_bp.route("/studies/<slug>/control/stream", methods=["GET"])
@require_panel
@require_data
def study_control_stream():
    runtime = _runtime()
    try:
        session = _resolve_experimenter_session(runtime)
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    return _stream(runtime, "experimenter", session)


# ---------------------------------------------------------------------------
# Getting the data out. Open and closed studies both: closing a study is what
# makes these the way its data leaves the server, since nobody on the team has
# shell access to one.
# ---------------------------------------------------------------------------


@studies_bp.route("/studies/<slug>/control/sessions", methods=["GET"])
@require_panel
@require_data
def study_sessions():
    return jsonify({"sessions": _runtime().store.list_sessions()}), 200


@studies_bp.route("/studies/<slug>/control/export/sessions/<int:study_session_id>.json", methods=["GET"])
@require_panel
@require_data
def study_session_export(study_session_id: int):
    """Everything recorded for one session as a single JSON document, whatever
    its status. The right thing to use when the question is about one session
    rather than the analysis set."""
    payload = _runtime().store.export_session(study_session_id)
    if not payload:
        return jsonify({"status": "error", "message": "No such session"}), 404
    return jsonify(payload), 200


@studies_bp.route("/studies/<slug>/control/export/long.csv", methods=["GET"])
@require_panel
@require_data
def study_export_long_csv():
    """Every completed session as one long-format CSV, one row per interaction.

    Completed sessions only, and that is not a filter the caller can turn off. An
    active session is still being written to, so including it would make the file
    depend on the moment it was asked for; an abandoned one stopped partway with
    nothing recording why. Deciding either is usable is a judgement about a
    specific participant, and it belongs to whoever is doing the analysis, not to
    a default in an endpoint. ``?session=<id>`` narrows the file to one session,
    still only if that session is completed.

    Cells a spreadsheet would run as a formula are escaped; see ``export``.
    """
    store = _runtime().store
    raw_session = (request.args.get("session") or "").strip()
    study_session_id: int | None = None
    if raw_session:
        try:
            study_session_id = int(raw_session)
        except ValueError:
            return jsonify({"status": "error", "message": "session must be a session id"}), 400
        session = store.get_study_session(study_session_id)
        if not session:
            return jsonify({"status": "error", "message": "No such session"}), 404
        if session.get("status") != "completed":
            # 409 rather than 404: the session is real, and saying so is what tells
            # the experimenter the difference between a typo and a session that
            # nobody finished. And where its rows are.
            return jsonify(
                {
                    "status": "error",
                    "message": (
                        f"Session {study_session_id} is {session.get('status')}, and long.csv "
                        "covers completed sessions only. Its rows are in long_incomplete.csv in "
                        "the archive once nothing is writing to it, and in this session's JSON "
                        "export at any time."
                    ),
                }
            ), 409

    buffer = io.StringIO()
    try:
        export.write_long_csv(buffer, store.export_long_rows(study_session_id))
    except Exception as error:  # said, rather than a file missing a session
        logger.exception("study %s: long.csv failed", _runtime().slug)
        return jsonify(
            {
                "status": "error",
                "message": (
                    f"A session could not be read, so long.csv was not made: {type(error).__name__}: "
                    f"{error}. The archive lists which session in checks.json."
                ),
            }
        ), 500
    slug = _runtime().slug
    filename = (
        f"{slug}_long_session_{study_session_id}.csv" if study_session_id else f"{slug}_long.csv"
    )
    return Response(
        buffer.getvalue(),
        status=200,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@studies_bp.route("/studies/<slug>/control/export/checks.json", methods=["GET"])
@require_panel
@require_data
def study_export_checks():
    runtime = _runtime()
    return jsonify(export.run_checks(runtime.store, study=runtime.study)), 200


@studies_bp.route("/studies/<slug>/control/export/archive.zip", methods=["GET"])
@require_panel
@require_data
def study_export_archive():
    """Everything, as one file: a consistent copy of the database, every
    session log, the long CSV with its codebook, the data checks, and a manifest
    with a checksum for each. What the IRB's storage location should receive."""
    runtime = _runtime()
    archive = export.build_archive(runtime.study, runtime.store.db_path, runtime.store.log_dir)
    filename = f"{runtime.slug}_{export.timestamp_for_filename()}.zip"
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )
