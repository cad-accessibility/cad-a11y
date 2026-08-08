"""The ``/study`` endpoints: a guided run of the study protocol.

Two views share one session:

* ``GET /study`` -- what the participant uses. It is the ordinary viewer, with the
  model chooser removed (models load themselves, one per step) and a small study
  region added carrying the current step and an "I am ready to move on" button.
* ``GET /study/control`` -- what the experimenter uses. Script to read aloud, which
  printed model to hand over, the answer key for the current pair, and the control
  that advances the step.

They stay in step over Server-Sent Events, so the experimenter can drive the
session from their own machine while the participant works on theirs -- which is
what the protocol asks for, since the participant uses their own screen reader and
braille display and the experimenter takes notes on a second device.

Several sessions at once
------------------------
One deployment serves the whole team, so two experimenters in different cities
can be running participants at the same moment. Every request therefore names
the session it means: a participant's browser carries the key minted at
enrolment (``/study?s=KEY``), and a panel carries the session id it started.
Both fall back to the single active session when there is exactly one, so the
ordinary case needs no key and nothing to type; with more than one active they
refuse to guess. The SSE fan-out is scoped the same way.

This was not always so, and the failure mode is worth remembering: with a single
global "active session", starting a second one abandoned the first mid-task,
re-pointed its participant's page at the new session's step, loaded a different
model onto their braille display, and logged their keypresses against the other
participant.

What is recorded, and what is not
---------------------------------
Interactions and their timings, keyed to the participant code: keypresses,
renders, step advances, model loads, readiness signals, device connections. The
questionnaires and the response tracking sheet are not here -- they are asked
verbally and recorded on the experimenter's own sheet. See ``study_db``.

What the participant is never shown
-----------------------------------
Model names give the answer away -- "lego_2x4" tells you the brick got longer
before you have felt anything. The participant payload carries the model's stem,
because a model has to be addressed by name to be addressed safely at all
(#123), plus a neutral label ("Second object") which is the only one of the two
the interface ever displays or announces. The pair description, the answer key
and the experimenter's script are withheld outright, here in
``_participant_state``, rather than by asking the front-end not to render them.

Authentication
--------------
Anything that changes or reveals the session requires a token
(``STUDY_CONTROL_TOKEN``, or one generated and logged at startup). The participant
view needs no token: a participant should not have to type a secret, and
everything it can do is either advisory (the ready button) or its own interaction
logging.
"""

from __future__ import annotations

import json
import logging
import os
import queue as _queue_module
import secrets
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context

from . import study_db, study_protocol

logger = logging.getLogger(__name__)

study_bp = Blueprint("study", __name__)

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
    except Exception:
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
        return Path(__file__).resolve().parent.parent
    return _repo_root_provider()


# ---------------------------------------------------------------------------
# Access
#
# The control panel is open by default. Running a session should be: open the
# app, start. Nothing to find in a log, nothing to paste into a URL.
#
# Setting STUDY_CONTROL_TOKEN turns a gate back on, for a deployment that wants
# one. It is off unless that variable is set, and the whole mechanism is one
# comparison -- there is deliberately no generated secret, no token file and no
# startup banner to go hunting through.
#
# What that leaves open on a public deployment: anyone with the URL can advance a
# live session, read the answer key and download a participant's interaction log.
# The destructive controls confirm before acting, which stops a misclick but not
# a determined stranger.
# ---------------------------------------------------------------------------


def control_token() -> str:
    """The configured gate, or "" when the panel is open."""
    return os.getenv("STUDY_CONTROL_TOKEN", "").strip()


def _provided_token() -> str:
    return (request.headers.get("X-Study-Token") or request.args.get("token") or "").strip()


def _token_valid() -> bool:
    configured = control_token()
    if not configured:
        return True
    provided = _provided_token()
    # compare_digest so a wrong token cannot be found a character at a time by
    # timing the response.
    return bool(provided) and secrets.compare_digest(provided, configured)


def require_token(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _token_valid():
            return jsonify(
                {
                    "status": "error",
                    "message": (
                        "This deployment sets STUDY_CONTROL_TOKEN, so the panel needs it."
                    ),
                }
            ), 403
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Live sync
#
# One queue per connected browser. Every state change broadcasts the whole state
# rather than a delta: a client that misses a message (reconnect, sleep, proxy
# hiccup) is corrected by the next one instead of drifting, and there is no
# ordering to get wrong.
# ---------------------------------------------------------------------------

_subscribers: list[dict[str, Any]] = []
_subscribers_lock = threading.Lock()

# Transient, per-step readiness signal. Kept in memory rather than in the session
# row because it is a notification about the current step, not session state, and
# it must not survive a step change. The durable record is the logged event.
_ready_signals: dict[int, dict[str, Any]] = {}


def _push(role: str, study_session_id: int, payload: dict[str, Any]) -> None:
    message = f"data: {json.dumps(payload)}\n\n"
    with _subscribers_lock:
        targets = [
            s
            for s in _subscribers
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


def _broadcast(session: dict[str, Any] | None) -> None:
    """Push the current state to everyone attached to *this* session."""
    if not session:
        return
    session_id = int(session["id"])
    _push("experimenter", session_id, _experimenter_state(session))
    _push("participant", session_id, _participant_state(session))


def _attached_participants(study_session_id: int | None) -> int:
    if study_session_id is None:
        return 0
    with _subscribers_lock:
        return sum(
            1
            for s in _subscribers
            if s.get("role") != "experimenter" and s.get("study_session_id") == study_session_id
        )


# ---------------------------------------------------------------------------
# State payloads
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Session resolution
#
# Several sessions can run at once -- two experimenters in different cities
# share one deployment -- so "the active session" is not a thing any request can
# ask for. Every request says which session it means:
#
#   participant  a key, minted at enrolment, carried in the /study URL
#   experimenter a study_session_id, which the panel holds from the moment it
#                started the session
#
# Both fall back to the single active session when there is exactly one, which
# keeps the common case frictionless: one experimenter, one participant, a plain
# /study link and nothing to type. With more than one active they refuse to
# guess, because guessing is what logged one participant's keypresses against
# another and loaded a model onto the wrong braille display mid-task.
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


def _resolve_participant_session() -> dict[str, Any] | None:
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
    session = study_db.get_session_by_key(key)
    return session if session and session.get("status") == "active" else None


def _resolve_experimenter_session() -> dict[str, Any] | None:
    """The session a control panel is driving. Raises if ambiguous."""
    raw = (
        request.args.get("study_session_id")
        or request.headers.get("X-Study-Session")
        or ((request.get_json(silent=True) or {}).get("study_session_id") if request.is_json else None)
    )
    if raw not in (None, ""):
        try:
            named = study_db.get_study_session(int(raw))
        except (TypeError, ValueError):
            named = None
        if named:
            return named
        # A panel pointing at a session that no longer exists falls through to
        # the rules below rather than showing nothing, so a stale tab recovers
        # instead of sitting on an empty enrolment form beside a running session.

    active = study_db.list_active_sessions()
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise SessionAmbiguous(len(active))
    return None


def _is_panel_request() -> bool:
    """Whether this request is the control panel rather than a participant.

    The token used to answer this: only the panel had one. With the panel open by
    default that no longer distinguishes anything, so the request says which it
    is by what it carries -- a panel names a session id, a participant carries a
    join code. Neither means participant, because the participant payload is the
    one that withholds the answer key, and the safe default when we cannot tell
    is to reveal less.
    """
    if _participant_key():
        return False
    named = (
        request.args.get("study_session_id")
        or request.headers.get("X-Study-Session")
        or ((request.get_json(silent=True) or {}).get("study_session_id") if request.is_json else None)
    )
    if named not in (None, ""):
        return True
    # A deployment that has turned the gate on still identifies its panel the
    # old way, by presenting the token.
    return bool(control_token()) and _token_valid()


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


def _steps_for(session: dict[str, Any] | None) -> list[dict[str, Any]]:
    return study_protocol.resolve_steps((session or {}).get("task_order") or [])


def _clamped_index(session: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    if not steps:
        return 0
    return max(0, min(int(session.get("step_index") or 0), len(steps) - 1))


def _current_step(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not session:
        return None
    steps = _steps_for(session)
    if not steps:
        return None
    return steps[_clamped_index(session, steps)]


def _participant_model(step: dict[str, Any] | None) -> dict[str, Any] | None:
    """The load instruction for the participant view: which model, and what to
    call it in front of the participant.

    ``stem`` is the real model name, because that is now the only safe way to
    address a model. This used to send a position in the server's model list
    instead, so the name never reached the participant's browser at all -- but
    that list is rebuilt whenever anyone uploads a file, so a window holding a
    number silently began rendering a different model (#123). Correctness of
    *which object is under the participant's fingers* outranks the neatness of
    withholding a string they never see.

    What protects the participant is ``label``: it is deliberately generic, and
    it is the only one of the two the interface ever displays or announces.
    Reading out "2x4 Lego brick" would answer the question they are being asked
    to work out by touch. See the study-mode branch in viewer.js, which keeps the
    stem out of the status bar and out of every announcement.
    """
    if not step or not step.get("model"):
        return None
    model = step["model"]
    stem = model.get("model")
    if not stem or _model_index(stem) is None:
        return None
    version = model.get("version")
    label = {"a": "First object", "b": "Second object"}.get(str(version), "Practice model")
    return {"stem": stem, "label": label}


def _participant_state(session: dict[str, Any] | None) -> dict[str, Any]:
    """What the participant's browser is allowed to know.

    Carries no model name, no pair description, no answer key and no experimenter
    script -- see the module docstring.
    """
    protocol = study_protocol.load_protocol()
    defaults = protocol.get("viewer_defaults")
    if not session:
        return {"active": False, "viewer_defaults": defaults}
    steps = _steps_for(session)
    step = _current_step(session)
    return {
        "active": session.get("status") == "active",
        "study_session_id": session.get("id"),
        # Handed back so a browser that attached without one can bind itself to
        # this session and stay bound. Without that, a page opened during a
        # single-session run holds no key, and when that session ends and the
        # next begins it silently joins the new one -- logging one participant's
        # keypresses into another participant's record. Not a secret: it
        # identifies the session this participant is already in.
        "participant_key": session.get("participant_key"),
        "status": session.get("status"),
        "step_index": _clamped_index(session, steps),
        "step_count": len(steps),
        "step_id": (step or {}).get("id"),
        "part_id": (step or {}).get("part_id"),
        "part_title": (step or {}).get("part_title"),
        "title": (step or {}).get("title"),
        "text": (step or {}).get("participant_text"),
        "model": _participant_model(step),
        "viewer_defaults": defaults,
    }


def _experimenter_state(session: dict[str, Any] | None) -> dict[str, Any]:
    """Everything, for the token-authenticated panel."""
    protocol = study_protocol.load_protocol()
    # Advisory: what the next participant will most likely be called, and which
    # pairs they are due. The real code comes from the id the database assigns at
    # enrolment, so two panels showing the same suggestion cannot collide.
    suggested_code = study_db.preview_next_code()
    sequence_number = study_db.next_sequence_preview()
    base: dict[str, Any] = {
        "active": False,
        "protocol_version": protocol.get("version"),
        "logging": study_db.logging_health(),
        "attached_participants": 0,
        "suggested_participant_code": suggested_code,
        "suggested_task_order": study_protocol.assign_task_order(sequence_number),
        "next_sequence_number": sequence_number,
        "missing_models": _missing_models(),
        # Every session currently running, so a panel that has just been opened
        # can join one instead of only being able to start another. Opening the
        # panel used to be a dead end when a session was already in progress:
        # with two running it could not resolve one at all, and the only control
        # on screen was "Start session".
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
            for other in study_db.list_active_sessions()
        ],
        "facilitator_prompts": protocol.get("facilitator_prompts"),
        "strategy_prompts": protocol.get("strategy_prompts"),
        "model_pairs": protocol.get("model_pairs"),
        "latin_square": study_protocol.latin_square_preview(),
    }
    if not session:
        return base

    steps = _steps_for(session)
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
            "task_labels": [
                (protocol.get("model_pairs", {}).get(key) or {}).get("label", key)
                for key in session.get("task_order") or []
            ],
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "step_started_at": session.get("step_started_at"),
            "log_path": session.get("log_path"),
            # The link this session's participant opens. With several sessions
            # running, a plain /study cannot tell which one a browser belongs to.
            "participant_key": session.get("participant_key"),
            "participant_path": (
                f"/study?s={session['participant_key']}" if session.get("participant_key") else "/study"
            ),
            "attached_participants": _attached_participants(int(session["id"])),
            "step_index": index,
            "step_count": len(steps),
            "step": step,
            "steps": [
                {
                    "index": s["index"],
                    "id": s["id"],
                    "part_id": s["part_id"],
                    "part_title": s["part_title"],
                    "title": s["title"],
                }
                for s in steps
            ],
            # None, not False, on a step that loads nothing: "no model here" and
            # "the model this step needs is missing" are different situations, and
            # only the second is a problem worth warning about.
            "model_available": (
                _model_index(model.get("model")) is not None if model.get("model") else None
            ),
            "counts": study_db.session_counts(session_id),
            "participant_ready": _ready_signals.get(session_id),
        }
    )
    return base


def _missing_models() -> list[str]:
    """Protocol models the server cannot currently serve.

    Checked at enrollment so a missing STL is a problem before the participant sits
    down, not when the step that needs it fails to put anything on the display.
    """
    models = set(_model_list())
    if not models:
        return []
    return [stem for stem in study_protocol.required_models() if stem not in models]


# ---------------------------------------------------------------------------
# Render hook, called from server.py
# ---------------------------------------------------------------------------

def record_render_for_request(params: dict[str, Any], *, model_stem: str, cache_hit: bool) -> None:
    """Record a /render call against the active study session, if it came from one.

    The participant's viewer tags its render requests with ``X-Study-Session``.
    Recording here rather than in the client means a browser crash cannot lose the
    record of what was on the display, and it covers cache hits too -- a cached
    response still put a new image under the participant's fingers, so leaving
    those out would silently drop most of a fast arrow-key traversal.

    Nothing in here may raise. It is called from inside ``render_view``'s try
    block, where an exception is turned into a 400 -- so a fault in the logging
    path would stop the participant's display updating at all. Study logging is
    important; it is not more important than the session continuing.
    """
    try:
        _record_render_for_request(params, model_stem=model_stem, cache_hit=cache_hit)
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        study_db.note_external_failure(error)


def _record_render_for_request(params: dict[str, Any], *, model_stem: str, cache_hit: bool) -> None:
    raw_id = (request.headers.get("X-Study-Session") or "").strip()
    if not raw_id:
        return
    try:
        study_session_id = int(raw_id)
    except ValueError:
        return
    session = study_db.get_study_session(study_session_id)
    if not session or session.get("status") != "active":
        return

    step = _current_step(session)
    orientation = params.get("orientation")
    study_db.record_render(
        study_session_id,
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
        part_id=(step or {}).get("part_id"),
        step_id=(step or {}).get("id"),
        step_index=(step or {}).get("index"),
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@study_bp.route("/study", methods=["GET"])
def study_participant_view():
    """The participant's view: the ordinary viewer in study mode.

    Same HTML as /viewer -- the interface a participant learns during onboarding is
    the interface they use for the tasks, which is the point of running the study
    on the real viewer rather than a special build of it. viewer.js keys off the
    path to hide the model chooser and show the study region.
    """
    return send_file(_repo_root() / "accessible-3d-viewer.html")


@study_bp.route("/study/control", methods=["GET"])
@require_token
def study_control_view():
    return send_file(_repo_root() / "study-control.html")


@study_bp.route("/study/config", methods=["GET"])
@require_token
def study_config():
    payload = study_protocol.config_payload()
    payload["missing_models"] = _missing_models()
    payload["logging"] = study_db.logging_health()
    return jsonify(payload), 200


@study_bp.route("/study/state", methods=["GET"])
def study_state():
    """Current state. Returns the experimenter view when a valid token is present
    and the participant view otherwise, so the participant page can call the same
    endpoint without being handed the answer key."""
    try:
        if _is_panel_request():
            return jsonify(_experimenter_state(_resolve_experimenter_session())), 200
        return jsonify(_participant_state(_resolve_participant_session())), 200
    except SessionAmbiguous as error:
        return _ambiguous_response(error)


@study_bp.route("/study/session/start", methods=["POST"])
@require_token
def study_session_start():
    """Enroll a participant and begin a session.

    A participant code is minted automatically (P01, P02, ...) so every run gets a
    new id without the experimenter having to decide on one; they can still send
    their own, and a returning participant keeps their code and with it their
    Latin-square assignment.
    """
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

    participant = study_db.create_participant(code or None)
    code = str(participant["code"])

    protocol = study_protocol.load_protocol()
    known_pairs = set(protocol.get("model_pairs", {}))
    requested_order = data.get("task_order")
    if isinstance(requested_order, list) and requested_order:
        task_order = [str(key) for key in requested_order if str(key) in known_pairs]
    else:
        task_order = study_protocol.assign_task_order(int(participant["id"]))
    if not task_order:
        return jsonify({"status": "error", "message": "task_order names no known model pairs"}), 400

    # Sessions already running are left alone. This used to abandon them, on the
    # assumption that one deployment meant one session -- so a second
    # experimenter starting a session silently ended the first mid-task, and its
    # participant's keypresses began landing on the new session.
    try:
        session = study_db.create_session(
            participant_id=int(participant["id"]),
            participant_code=code,
            session_number=session_number,
            task_order=task_order,
            protocol_version=str(protocol.get("version") or ""),
        )
    except Exception as error:  # noqa: BLE001 - reported to the experimenter, not swallowed
        return jsonify({"status": "error", "message": f"Could not start session: {error}"}), 409

    study_db.record_event(
        int(session["id"]),
        "session_start",
        source="experimenter",
        event_data={
            "participant_id": participant["id"],
            "participant_code": code,
            "session_number": session_number,
            "task_order": task_order,
            "protocol_version": protocol.get("version"),
            "missing_models": _missing_models(),
        },
        step_index=0,
    )
    _ready_signals.pop(int(session["id"]), None)
    _broadcast(session)
    return jsonify({"status": "success", "state": _experimenter_state(session)}), 200


@study_bp.route("/study/session/end", methods=["POST"])
@require_token
def study_session_end():
    try:
        session = _resolve_experimenter_session()
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    if not session:
        return jsonify({"status": "error", "message": "No active session"}), 404
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "completed")
    if status not in ("completed", "abandoned"):
        status = "completed"
    study_db.record_event(
        int(session["id"]),
        "session_end",
        source="experimenter",
        event_data={"status": status},
        step_index=int(session.get("step_index") or 0),
    )
    study_db.complete_session(int(session["id"]), status=status)
    _ready_signals.pop(int(session["id"]), None)
    _broadcast(study_db.get_study_session(int(session["id"])))
    return jsonify({"status": "success"}), 200


@study_bp.route("/study/step/advance", methods=["POST"])
@require_token
def study_step_advance():
    """Move to another step. The experimenter drives this, never the participant.

    Accepts a direction or an absolute index, because going back matters: the
    protocol has no time limits, and an experimenter who advanced early needs to
    return without restarting the session.
    """
    try:
        session = _resolve_experimenter_session()
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    if not session or session.get("status") != "active":
        return jsonify({"status": "error", "message": "No active session"}), 404

    steps = _steps_for(session)
    if not steps:
        return jsonify({"status": "error", "message": "Protocol has no steps"}), 500

    data = request.get_json(silent=True) or {}
    current = _clamped_index(session, steps)
    if data.get("step_index") is not None:
        try:
            target = int(data["step_index"])
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "step_index must be a number"}), 400
    elif str(data.get("direction") or "next") == "previous":
        target = current - 1
    else:
        target = current + 1
    target = max(0, min(target, len(steps) - 1))

    session_id = int(session["id"])
    study_db.set_step_index(session_id, target)
    step = steps[target]
    study_db.record_event(
        session_id,
        "step_advance",
        source="experimenter",
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
        # Logged separately from the advance: "which model was on the display" is
        # a question the analysis asks directly, and a step can be revisited.
        study_db.record_event(
            session_id,
            "model_autoload",
            source="server",
            event_data={
                "model": (step.get("model") or {}).get("model"),
                "pair_key": step.get("pair_key"),
                "version": (step.get("model") or {}).get("version"),
                "available": _model_index((step.get("model") or {}).get("model")) is not None,
            },
            part_id=step.get("part_id"),
            step_id=step.get("id"),
            step_index=target,
        )
    # The readiness light belongs to the step it was raised on.
    _ready_signals.pop(session_id, None)
    refreshed = study_db.get_study_session(session_id)
    _broadcast(refreshed)
    return jsonify({"status": "success", "state": _experimenter_state(refreshed)}), 200


@study_bp.route("/study/step/ready", methods=["POST"])
def study_step_ready():
    """The participant's "I am ready to move on" signal.

    Advisory by design: it notifies the experimenter and is logged, and it does not
    advance anything. The protocol is experimenter-paced with no time limits, and a
    button that jumped the session forward would turn a stray keypress into lost
    data.
    """
    try:
        session = _resolve_participant_session()
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    if not session:
        return jsonify({"status": "error", "message": "No active session"}), 404
    data = request.get_json(silent=True) or {}
    step = _current_step(session)
    session_id = int(session["id"])
    viewer_state = data.get("viewer_state")
    study_db.record_event(
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
    _ready_signals[session_id] = {
        "step_id": (step or {}).get("id"),
        "step_index": (step or {}).get("index"),
        "at": study_db.now(),
    }
    _push("experimenter", session_id, _experimenter_state(study_db.get_study_session(session_id)))
    return jsonify({"status": "success"}), 200


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


@study_bp.route("/study/event", methods=["POST"])
def study_event():
    """Record one participant-side interaction.

    This is the half of the record the server cannot see: which key was pressed, as
    opposed to which render it happened to produce; what the screen reader was
    told; when a device connected or dropped. Every event carries the viewer state
    at that moment, so the JSONL line is self-contained.
    """
    try:
        session = _resolve_participant_session()
    except SessionAmbiguous as error:
        # Refuse rather than pick. Guessing here is what logged one participant's
        # interactions against another participant's session.
        return _ambiguous_response(error)
    if not session:
        return jsonify({"status": "ignored", "message": "No active session"}), 200

    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in _ALLOWED_PARTICIPANT_EVENTS:
        return jsonify({"status": "error", "message": f"Unknown event_type '{event_type}'"}), 400

    event_data = data.get("event_data")
    if event_data is not None and not isinstance(event_data, dict):
        return jsonify({"status": "error", "message": "event_data must be an object"}), 400

    step = _current_step(session)
    viewer_state = data.get("viewer_state")
    study_db.record_event(
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


@study_bp.route("/study/stream", methods=["GET"])
def study_stream():
    """Server-Sent Events keeping both views in step.

    The experimenter and the participant are usually on different machines, so
    there has to be a push channel; polling would mean a step change landing up to
    a poll interval late, under the participant's fingers, with no explanation.
    """
    is_experimenter = _is_panel_request()
    role = "experimenter" if is_experimenter else "participant"

    # Resolved once, at subscribe time: a stream belongs to the session the
    # client named when it connected. Without this every broadcast reached every
    # browser, so advancing one session moved another participant's page.
    try:
        session = (
            _resolve_experimenter_session() if is_experimenter else _resolve_participant_session()
        )
    except SessionAmbiguous as error:
        return _ambiguous_response(error)
    session_id = int(session["id"]) if session else None

    def generate():
        client_queue: _queue_module.Queue[str] = _queue_module.Queue(maxsize=32)
        subscriber = {"queue": client_queue, "role": role, "study_session_id": session_id}
        with _subscribers_lock:
            _subscribers.append(subscriber)
        try:
            initial = (
                _experimenter_state(session) if role == "experimenter" else _participant_state(session)
            )
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    yield client_queue.get(timeout=25)
                except _queue_module.Empty:
                    yield ": heartbeat\n\n"  # keep the connection alive through proxies
        finally:
            with _subscribers_lock:
                if subscriber in _subscribers:
                    _subscribers.remove(subscriber)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@study_bp.route("/study/sessions", methods=["GET"])
@require_token
def study_sessions():
    return jsonify({"sessions": study_db.list_sessions()}), 200


@study_bp.route("/study/sessions/<int:study_session_id>/export", methods=["GET"])
@require_token
def study_session_export(study_session_id: int):
    """Everything recorded for one session as a single JSON document, so analysis
    does not start with getting a SQLite file off a Docker volume."""
    payload = study_db.export_session(study_session_id)
    if not payload:
        return jsonify({"status": "error", "message": "No such session"}), 404
    return jsonify(payload), 200
