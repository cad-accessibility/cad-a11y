#!/usr/bin/env python3
"""Accessible 3D viewer server.

Features:
- Receives render state from the viewer and renders with CADComparisonRenderer.
- Sends rendered output to a connected braille display using braille_display.py.
- Opens accessible-3d-viewer.html in the default browser at startup.

Input hardware is not handled here. The orientation cube, the slider, the Monarch
and the DotPad are all connected by the browser, to the window using them, so one
person's device moves that window and no other.
"""

from __future__ import annotations

import base64
import copy
import contextlib
import hashlib
import io
import json
import logging
import os
import queue as _queue_module
import re
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
import uuid

import numpy as np
from flask import Flask, Response, has_request_context, jsonify, redirect, request, send_file, stream_with_context
from werkzeug.utils import secure_filename
from flask_cors import CORS
from PIL import Image

from . import db, study, study_db
from .braille_display import (
    _pixels_to_braille_cells,
    _pixels_to_braille_cells_dotpad,
    _MONARCH_LINES,
    _MONARCH_COLS,
    _DOTPAD_LINES,
    _DOTPAD_COLS,
)
from .cad_comparison_lib import DEFAULT_SCREEN_SIZE, CADComparisonRenderer, RenderResult
from src.converter.render_low_res import dilate_mask, raised_ink_mask, save_binary_array_as_vector_pdf

logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app)
# Cap request bodies (uploads and /ingest); default 100 MB. Oversized requests
# are rejected with 413 before the handler runs.
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "100") or "100") * 1024 * 1024


if getattr(sys, "frozen", False):
    # In bundled mode, runtime assets are expected next to the executable.
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    # app/server.py lives one level below the project root.
    REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO_ROOT / "data" / "models"
# Tracked built-in models ship here, outside any Docker volume mount, and are
# copied into MODEL_DIR at startup by _seed_builtin_models(). Keeping the source
# outside the mount is what lets built-ins added later reach a server whose
# models volume already exists — Docker only seeds a named volume when empty.
BUILTIN_SOURCE_DIR = REPO_ROOT / "builtin_models"
RENDERS_DIR = REPO_ROOT / "data" / "renders"
STUDY_LOG_DIR = REPO_ROOT / "data" / "logs"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    # Write/unlink probe catches bind mounts that exist but are not writable.
    probe = path / f".cad_a11y_write_test_{uuid.uuid4().hex}"
    try:
        with probe.open("wb") as handle:
            handle.write(b"ok")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        with contextlib.suppress(Exception):
            probe.unlink(missing_ok=True)
        return False


# A writability probe touches the disk twice, and /health checks four directories
# on an endpoint that is unauthenticated by design and polled every 30 seconds by
# the container healthcheck. Cache briefly so traffic cannot turn a health check
# into disk load, while staying short enough that a mount going read-only is
# still reported within one healthcheck interval.
_WRITABILITY_CACHE_TTL = 10.0  # seconds
_writability_cache: dict[str, tuple[float, bool]] = {}
_writability_cache_lock = threading.Lock()


def _is_writable_directory_cached(path: Path) -> bool:
    key = str(path)
    now = time.monotonic()
    with _writability_cache_lock:
        cached = _writability_cache.get(key)
        if cached is not None and now - cached[0] < _WRITABILITY_CACHE_TTL:
            return cached[1]

    # Probed outside the lock: a hung filesystem should not block every other
    # caller, and a duplicated probe is harmless.
    writable = _is_writable_directory(path)
    with _writability_cache_lock:
        _writability_cache[key] = (now, writable)
    return writable


def _resolve_upload_dir() -> Path:
    """Resolve the directory uploads are written to.

    MODEL_DIR is deliberately NOT a candidate. Built-in vs. uploaded is decided
    by which directory a file sits in (see _is_builtin), so the two must never
    be the same directory. MODEL_DIR used to be the first candidate, which meant
    that merely making it writable — as the move to Docker named volumes did in
    #96 — silently collapsed the distinction and emptied the model dropdown (#102).
    """
    env_dir = os.getenv("UPLOAD_MODEL_DIR", "").strip()
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            REPO_ROOT / "data" / "uploads",
            Path("/tmp/cad-a11y/uploads"),
        ]
    )

    # Preserve order while removing duplicates.
    deduped_candidates = list(dict.fromkeys(candidates))
    for candidate in deduped_candidates:
        if _is_writable_directory(candidate):
            return candidate
    raise RuntimeError(
        "No writable upload directory found. Tried: "
        + ", ".join(str(c) for c in deduped_candidates)
        + ". Set UPLOAD_MODEL_DIR to a writable path."
    )


UPLOAD_DIR = _resolve_upload_dir()

# Structural invariant, not a preference. If uploads ever shared a directory with
# built-ins, every uploaded file would be classified public. Fail loudly at start
# rather than silently reshaping who can see what.
if UPLOAD_DIR.resolve() == MODEL_DIR.resolve():
    raise RuntimeError(
        f"UPLOAD_MODEL_DIR ({UPLOAD_DIR}) must not be the built-in model "
        f"directory ({MODEL_DIR}); uploads would be served to every visitor."
    )


def _seed_builtin_models() -> int:
    """Copy tracked built-ins into MODEL_DIR, skipping files already present.

    Idempotent and safe on every boot. Docker seeds a named volume from the image
    only while the volume is empty, so without this a built-in added to the image
    later would never appear on an existing deployment (#124).
    """
    if not BUILTIN_SOURCE_DIR.is_dir():
        return 0
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as error:
        _log(f"Could not create model directory {MODEL_DIR}: {error}", force=True)
        return 0

    copied = 0
    for source in sorted(BUILTIN_SOURCE_DIR.iterdir()):
        if not source.is_file() or source.name.startswith("."):
            continue
        target = MODEL_DIR / source.name
        if target.exists():
            continue
        try:
            shutil.copy2(source, target)
            copied += 1
        except Exception as error:
            _log(f"Could not seed built-in model {source.name}: {error}", force=True)
    if copied:
        _log(f"Seeded {copied} built-in model(s) into {MODEL_DIR}", force=True)
    return copied


def _resolve_braille_log_path() -> Path:
    """Resolve a writable path for the braille-send study log.

    Mirrors _resolve_upload_dir(): on managed servers the repo-local
    ``data/logs`` directory is frequently not writable by the app user
    (root-owned bind mount, non-root container user, redeploy resets
    ownership, etc.). Study telemetry must never dictate whether the app can
    serve renders, so fall back to a writable location instead of pinning to
    an unwritable one.
    """
    env_path = os.getenv("BRAILLE_LOG_PATH", "").strip()
    if env_path:
        candidate = Path(env_path)
        if _is_writable_directory(candidate.parent):
            return candidate
        # An explicit but unwritable BRAILLE_LOG_PATH should not wedge
        # rendering; fall through to the auto-detected writable dirs below.
    for directory in (STUDY_LOG_DIR, Path("/tmp/cad-a11y/logs")):
        if _is_writable_directory(directory):
            return directory / "braille_send_events.jsonl"
    return STUDY_LOG_DIR / "braille_send_events.jsonl"


BRAILLE_LOG_PATH = _resolve_braille_log_path()

_SESSION_COOKIE = "cad_session"
_SESSION_MAX_AGE = 365 * 24 * 3600  # 1 year
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Allowlist for event_type values accepted by POST /events/track.
_ALLOWED_EVENT_TYPES = frozenset(
    {
        "section_dwell",
        "keyboard_shortcut",
        "device_connect",
        "export",
        "model_select",
    }
)


def _validate_session_cookie(value: str | None) -> str | None:
    """Return the cookie value if it is a well-formed UUID, else None."""
    if value and _UUID_RE.match(value):
        return value
    return None


def _get_or_create_session_id() -> str:
    """Read cad_session cookie from the current request; generate a new UUID if absent/invalid."""
    existing = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE))
    return existing if existing else str(uuid.uuid4())


def _attach_session_cookie(response: Response, session_id: str) -> Response:
    is_https = (
        request.is_secure
        or request.environ.get("HTTP_X_FORWARDED_PROTO", "").lower() == "https"
    )
    response.set_cookie(
        _SESSION_COOKIE,
        session_id,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="Strict",
        secure=is_https,
    )
    return response


# ---------------------------------------------------------------------------
# Workshop participants. The calling tool sends the participant's first name with the
# STL; we key each participant on their (normalised) first name and give them a unique
# user id so their models and in-app actions can be linked together for the research.
# ---------------------------------------------------------------------------
def _normalize_name(raw: Any) -> str | None:
    """Normalise a participant first name into a stable, case- and spacing-insensitive
    lookup key (e.g. '  Alex ' and 'alex' both match). Returns None if empty."""
    if raw is None:
        return None
    key = " ".join(str(raw).strip().lower().split())
    return key or None


def _participant_for_name(first_name: str) -> str:
    """Return the workshop participant id (a unique session id) for this normalised
    first name, creating one the first time the name is seen and reusing it afterwards
    so a participant's iterations attach to the same record. Workshop participants are
    flagged so their in-app actions are recorded without the analytics consent dialog.

    First names are not unique, so two people sharing one first name share one record;
    the workshop mitigates that out of band (name tags, photos)."""
    existing = db.get_session_id_for_identifier(first_name)
    if existing:
        db.upsert_session(existing)  # refresh last_seen_at
        return existing
    user_id = str(uuid.uuid4())
    db.upsert_session(user_id)
    db.save_session_identifier(user_id, first_name, consent_given=False, is_workshop=True)
    return user_id


DEFAULT_RENDER_PARAMS: dict[str, Any] = {
    "view": "y-",
    "zoom": "0",
    "depth": 0,
    "renderMode": "Outline",
    "mode": "single",
    "move_camera_center": "none",
    "print_view": False,
}

# Keyed by model path, not by position in the discovered list. An upload or a
# delete renumbers that list, so an index-keyed entry silently came to mean a
# different model, which is why every one of them used to be thrown away on any
# change. Paths do not renumber, so only the model that actually changed is
# evicted and everyone else keeps a renderer that is still correct.
#
# Bounded, because nothing clears it wholesale any more: a long workshop would
# otherwise hold every mesh anyone had ever opened. Least recently used goes
# first; rebuilding one is a mesh load, not a correctness problem.
RENDERER_CACHE_MAX = int(os.getenv("RENDERER_CACHE_MAX", "24"))

# Startup only warms this many models; the rest stay cold until the render path
# builds them on demand the first time someone actually opens them (same
# fallback that already covers a model a warmup pass failed on). Warming every
# model up front doesn't scale as the model count grows, and most of a large
# library may never be opened in a given deployment's lifetime. Uploads still
# enqueue for warmup immediately via enqueue_model_for_warmup, independent of
# this limit — it only bounds what start_model_warmup() does at boot.
STARTUP_WARMUP_LIMIT = max(0, int(os.getenv("STARTUP_WARMUP_LIMIT", "1")))
renderers_by_model: OrderedDict[str, CADComparisonRenderer] = OrderedDict()
models_lock = threading.Lock()
# Serialize all engine.render() calls, because matplotlib is not thread-safe:
# the converters below it drive the pyplot module globals and call a bare
# plt.close(). That is the whole reason. It used to also stand in for the camera
# pan state a render mutated on the shared renderer, which is gone: a render now
# takes its camera centre and grid as arguments and returns what it resolved.
# Do not put per-request state back on the renderer on the strength of this lock.
# It orders renders; it does not isolate them, and it is a global bottleneck
# rather than a concurrency mechanism.
render_lock = threading.Lock()
braille_log_lock = threading.Lock()
braille_send_sequence = 0
# Raised from 128 because this is now the only render cache, and because every
# window keys separately on its own camera centre, so N windows exploring the
# same model no longer share entries the way one window did. An entry is about
# 16 KB, so this ceiling is roughly 8 MB.
RENDER_QUANTIZED_CACHE_MAX = int(os.getenv("RENDER_QUANTIZED_CACHE_MAX", "512"))
quantized_render_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
quantized_render_cache_lock = threading.Lock()
PREVIEW_PAYLOAD_CACHE_MAX = int(os.getenv("PREVIEW_PAYLOAD_CACHE_MAX", "128"))
preview_payload_cache: OrderedDict[str, np.ndarray] = OrderedDict()
preview_payload_cache_lock = threading.Lock()

# SSE client registry — each connected browser tab gets its own queue.
_sse_clients: list[_queue_module.Queue] = []
_sse_clients_lock = threading.Lock()

# Quiet-by-default: set SERVER_VERBOSE=1 to see detailed logs.
QUIET_MODE = os.getenv("SERVER_VERBOSE", "0").lower() not in {"1", "true", "yes", "on"}


def _log(message: str, *, force: bool = False) -> None:
    if force or not QUIET_MODE:
        print(message)


def _push_sse(data: dict) -> None:
    """Broadcast a hardware-state event to all connected SSE clients."""
    message = f"data: {json.dumps(data)}\n\n"
    with _sse_clients_lock:
        stale = []
        for q in _sse_clients:
            try:
                q.put_nowait(message)
            except _queue_module.Full:
                stale.append(q)
        for q in stale:
            _sse_clients.remove(q)


def _renderer_stdio_guard():
    if QUIET_MODE:
        sink = io.StringIO()
        return contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink)
    return contextlib.nullcontext(), contextlib.nullcontext()


# The extensions a model file can carry, compared case-insensitively. Discovery
# below still globs a fixed set of spellings, so a file named .STL is not indexed
# and cannot be opened; _stem_is_taken uses this set anyway, so such a file still
# reserves its name and cannot be shadowed by an upload.
MODEL_SUFFIXES = frozenset({".stl", ".step"})


def _discover_models() -> list[Path]:
    patterns = ("*.stl", "*.step", "*.STEP")
    models: list[Path] = []
    search_dirs = list(dict.fromkeys([MODEL_DIR, UPLOAD_DIR]))
    for model_dir in search_dirs:
        for pattern in patterns:
            models.extend(sorted(model_dir.glob(pattern)))
    # Deduplicate while preserving order.
    return list(dict.fromkeys(models))


def _find_default_model() -> Path:
    models = _discover_models()
    if models:
        return models[0]
    raise FileNotFoundError(f"No .stl/.step model found in {MODEL_DIR}")


# Seed before the first discovery so a fresh MODEL_DIR (or a Docker volume that
# predates a newly added built-in) is populated before anything globs it.
_seed_builtin_models()

DEFAULT_MODEL = _find_default_model()
AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
MODEL_NAME_LIST = [model_path.stem for model_path in AVAILABLE_MODELS]

_MODEL_DIR_RESOLVED = MODEL_DIR.resolve()

# The /study endpoints live in their own module. They need two things from here —
# the current model list (to check the protocol's models are all present) and the
# repo root (to serve the two HTML pages). Passed as callables rather than
# imported, because MODEL_NAME_LIST is rebound whenever the model set changes,
# and because study.py importing server.py would be a cycle.
study.set_model_list_provider(lambda: MODEL_NAME_LIST)
study.set_repo_root_provider(lambda: REPO_ROOT)
app.register_blueprint(study.study_bp)


def _is_builtin(model_path: Path) -> bool:
    """True if this model ships with the app and is therefore public.

    Classification is by directory: built-ins live in MODEL_DIR, uploads in
    UPLOAD_DIR, and _resolve_upload_dir plus the startup guard above make it
    impossible for those to be the same place. This replaces a module-level
    BUILTIN_MODEL_STEMS that was computed once at import while the model list
    itself kept refreshing, so a model added at runtime was never classifiable.
    """
    try:
        return model_path.parent.resolve() == _MODEL_DIR_RESOLVED
    except OSError:
        return False


def _builtin_model_stems() -> list[str]:
    """Stems of the currently discovered built-in models."""
    return [p.stem for p in AVAILABLE_MODELS if _is_builtin(p)]


def _stem_is_taken(stem: str) -> bool:
    """True if any model in either directory already uses this stem.

    Built-ins and uploads live in different directories now, so two files can
    share a stem without ever colliding on a path. Stems have to stay unique:
    they are how a model is named to the client.
    """
    for directory in (MODEL_DIR, UPLOAD_DIR):
        for existing in directory.glob(f"{stem}.*"):
            if existing.suffix.lower() in MODEL_SUFFIXES:
                return True
    return False
_model_list_last_refresh: float = 0.0
_MODEL_LIST_REFRESH_INTERVAL = 2.0  # seconds


def _refresh_model_list_if_stale() -> None:
    """Refresh AVAILABLE_MODELS/MODEL_NAME_LIST from disk at most every 2 s."""
    global AVAILABLE_MODELS, MODEL_NAME_LIST, _model_list_last_refresh
    now = time.monotonic()
    if now - _model_list_last_refresh < _MODEL_LIST_REFRESH_INTERVAL:
        return
    with models_lock:
        if now - _model_list_last_refresh < _MODEL_LIST_REFRESH_INTERVAL:
            return  # another thread already refreshed
        AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
        MODEL_NAME_LIST = [p.stem for p in AVAILABLE_MODELS]
        _model_list_last_refresh = time.monotonic()


def _resolve_model_stem(raw_value: Any) -> str:
    """The model a request means, named rather than numbered.

    A model used to be addressed by its position in the discovered list. That
    list is rebuilt from disk on a timer and on every upload, so a number meant a
    different file after anyone added one: a window sitting on index 18 silently
    started rendering somebody else's model. This is the crossover the workshop
    reported.

    A name does not renumber. An unknown one falls back to the default model,
    never to whatever another window happens to be looking at, which is what the
    process-wide "current model" used to supply.

    Numeric values are still accepted, so a browser holding an older viewer.js
    keeps working until it reloads.
    """
    if raw_value is None:
        return DEFAULT_MODEL.stem

    text = str(raw_value).strip()
    if not text:
        return DEFAULT_MODEL.stem

    known = {path.stem for path in AVAILABLE_MODELS}
    if text in known:
        return text

    # Legacy: a position in the list. Ambiguous by nature, which is the whole
    # problem, but resolving it once here is better than rejecting the request.
    try:
        index = int(text)
    except ValueError:
        return DEFAULT_MODEL.stem
    if 0 <= index < len(AVAILABLE_MODELS):
        return AVAILABLE_MODELS[index].stem
    return DEFAULT_MODEL.stem


def _path_for_stem(model_stem: str) -> Path:
    """The file behind a model name, or the default if it has gone."""
    for path in AVAILABLE_MODELS:
        if path.stem == model_stem:
            return path
    return DEFAULT_MODEL


def get_or_create_renderer(model_stem: str | None = None) -> CADComparisonRenderer:
    stem = _resolve_model_stem(model_stem)
    with models_lock:
        return _renderer_for_path(_path_for_stem(stem))


def _renderer_for_path(model_path: Path) -> CADComparisonRenderer:
    """The renderer for this file, building it if nobody has yet.

    Caller holds models_lock. Construction happens inside it, which serialises a
    mesh load against the model list, but the alternative is two threads building
    the same renderer and one of them being discarded.
    """
    key = str(model_path)
    existing = renderers_by_model.get(key)
    if existing is not None:
        renderers_by_model.move_to_end(key)
        return existing

    _log(f"Initializing CAD renderer with: {model_path}")
    out_guard, err_guard = _renderer_stdio_guard()
    with out_guard, err_guard:
        # Slice-graph precompute is expensive and only feeds a mode the
        # simplified workshop viewer can't reach, so it's kicked off lazily
        # (CADComparisonRenderer._get_zoom_filtered_slice_profile) on first
        # actual use instead of unconditionally here.
        renderer = CADComparisonRenderer(str(model_path), str(model_path))

    renderers_by_model[key] = renderer
    while len(renderers_by_model) > max(1, RENDERER_CACHE_MAX):
        renderers_by_model.popitem(last=False)
    return renderer


def _forget_renderer(model_path: Path) -> None:
    """Drop one model's renderer and its precomputed slice data.

    Caller holds models_lock. The cache file goes too: it is keyed by the model's
    signature, so a deleted upload would otherwise leave megabytes behind that
    nothing can ever match again. A workshop's worth of uploads and deletions
    would fill the volume with data for models that no longer exist.
    """
    engine = renderers_by_model.pop(str(model_path), None)
    if engine is None:
        return
    # Tell the background precompute (if it is still running) that this model is
    # gone, so it does not write its cache file back after we unlink it here.
    engine._discarded = True
    with contextlib.suppress(Exception):
        Path(engine.cache_path).unlink(missing_ok=True)


# Models are processed up front rather than when someone first opens them, so
# nobody pays the mesh load and the slice precompute by being the first to look.
# One worker, one model at a time: the work is CPU-bound and a pool would simply
# take the GIL away from request handling for longer.
_warmup_queue: _queue_module.Queue = _queue_module.Queue()
_warmup_state_lock = threading.Lock()
_warmup_state: dict[str, Any] = {"total": 0, "processed": 0, "current": None, "started": False}


def _warmup_snapshot() -> dict[str, Any]:
    """What the warm-up has got through, for /health."""
    with _warmup_state_lock:
        state_copy = dict(_warmup_state)
    state_copy["pending"] = max(0, state_copy["total"] - state_copy["processed"])
    state_copy["complete"] = state_copy["started"] and state_copy["pending"] == 0
    return state_copy


def enqueue_model_for_warmup(model_path: Path) -> None:
    """Process this model soon, in the background.

    Called for every model at startup and for each new upload, so a model is
    ready before anyone asks for it rather than at the cost of whoever asks
    first.
    """
    with _warmup_state_lock:
        _warmup_state["total"] += 1
    _warmup_queue.put(Path(model_path))


def _warm_one_model(model_path: Path) -> None:
    """Load a model and make sure its slice data exists.

    Skipped cheaply when a previous run already cached it: the renderer's own
    loader checks the signature, so an unchanged model costs a file read.
    """
    with models_lock:
        engine = _renderer_for_path(model_path)
    engine.start_background_slice_precompute()
    # Wait, so the queue really is one model at a time. Without this the worker
    # would start every model's precompute thread at once, which is the pool this
    # is meant not to be.
    engine._precompute_done.wait()


def _warmup_worker() -> None:
    while True:
        model_path = _warmup_queue.get()
        try:
            with _warmup_state_lock:
                _warmup_state["current"] = model_path.stem
            out_guard, err_guard = _renderer_stdio_guard()
            with out_guard, err_guard:
                _warm_one_model(model_path)
        except Exception as error:
            # One bad model must not stop the rest being processed, and must not
            # stop the server: it will simply be built on demand like before.
            _log(f"Warm-up failed for {model_path.name}: {error}", force=True)
        finally:
            with _warmup_state_lock:
                _warmup_state["processed"] += 1
                _warmup_state["current"] = None
            _warmup_queue.task_done()


def start_model_warmup() -> None:
    """Start the worker and hand it up to STARTUP_WARMUP_LIMIT models on disk.

    The rest are left cold; the render path builds one the first time someone
    actually asks for it, same as a model that failed warmup already did.
    """
    with _warmup_state_lock:
        if _warmup_state["started"]:
            return
        _warmup_state["started"] = True

    threading.Thread(target=_warmup_worker, name="cad-model-warmup", daemon=True).start()
    with models_lock:
        models = list(AVAILABLE_MODELS)

    to_warm = models[:STARTUP_WARMUP_LIMIT]

    # A fixed cache size smaller than the warmup batch would otherwise have
    # warmup evict its own earlier work before anyone's even hit the server —
    # raising it here, once, at startup, so warming a batch at boot actually
    # keeps that batch warm. A later upload that pushes the count higher still
    # can trigger ordinary LRU eviction, same as before this existed.
    global RENDERER_CACHE_MAX
    if len(to_warm) > RENDERER_CACHE_MAX:
        _log(
            f"Raising RENDERER_CACHE_MAX from {RENDERER_CACHE_MAX} to {len(to_warm)} "
            "so warming the startup batch doesn't evict its own work.",
            force=True,
        )
        RENDERER_CACHE_MAX = len(to_warm)

    for model_path in to_warm:
        enqueue_model_for_warmup(model_path)
    _log(
        f"Warming {len(to_warm)} of {len(models)} model(s) in the background at startup; "
        "the rest build on demand when first requested.",
        force=True,
    )


def _ensure_minimum_feature_thickness(mask: np.ndarray) -> np.ndarray:
    """Dilate raised content by one pixel if nothing in the render is two pixels
    thick, so a degenerate view (e.g. a flat model seen edge-on) doesn't come out
    as an all-but-invisible scattering of isolated pixels on the physical display.

    Only fires when the render as a whole is uniformly hairline; a normal render
    with crisp 1px edges alongside thicker filled or curved regions is left
    untouched, so this cannot re-thicken the single-pixel lines the majority
    threshold produces. Two pixels thick means two set pixels side by side, so
    the test is a pair of shifted comparisons rather than a per-row/column scan.
    """
    if not mask.any():
        return mask

    two_thick = (mask[1:, :] & mask[:-1, :]).any() or (mask[:, 1:] & mask[:, :-1]).any()
    if two_thick:
        return mask

    return dilate_mask(mask)


def _to_braille_payload(rendered_rgba: np.ndarray) -> np.ndarray:
    # Convert renderer output to braille payload using a single deterministic
    # rule for all modes: majority coverage is raised.
    #
    # The high-res render is downsampled to display resolution with an area-
    # average filter (resize_local_mean), so a pixel's value reflects the
    # fraction of its physical footprint actually covered by ink, not just
    # whether it was touched at all. A thin line straddling a pixel boundary
    # therefore leaves BOTH neighboring pixels partially gray. Any-non-white
    # ("< 255") marks both of them raised regardless of how thin the source
    # line is; a true majority rule (more than half covered) raises only the one
    # that a physical display pin's footprint would actually justify.
    #
    # Majority alone would drop sub-pixel features outright, so faint ink with no
    # majority pixel beside it is kept too (see raised_ink_mask, which the
    # outline detection shares so the two can't disagree on the model boundary).
    channel = rendered_rgba[:, :, 0].astype(np.uint8, copy=False)
    raised = raised_ink_mask(channel)
    raised = _ensure_minimum_feature_thickness(raised)
    return np.where(raised, 255, 0).astype(np.uint8)


def _payload_stats(payload: np.ndarray) -> dict[str, Any]:
    total_cells = int(payload.size)
    raised_cells = int(np.count_nonzero(payload > 0))
    payload_bytes = payload.astype(np.uint8, copy=False).tobytes()
    return {
        "shape": list(payload.shape),
        "dtype": str(payload.dtype),
        "total_cells": total_cells,
        "raised_cells": raised_cells,
        "raised_ratio": float(raised_cells / total_cells) if total_cells else 0.0,
        "sum": int(np.sum(payload, dtype=np.int64)),
        "min": int(np.min(payload)) if total_cells else 0,
        "max": int(np.max(payload)) if total_cells else 0,
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }


def _collect_request_context() -> dict[str, Any]:
    if not has_request_context():
        return {}
    return {
        "endpoint": request.path,
        "method": request.method,
        "remote_addr": request.remote_addr,
        "user_agent": request.user_agent.string,
    }


def _write_braille_event(event: dict[str, Any]) -> None:
    # Study telemetry is strictly best-effort: the actual device send happens
    # browser-side (Web HID / Web BLE), so a failure to append this research
    # log must never turn an otherwise-successful render into an HTTP 400.
    # Swallow and warn (e.g. read-only log dir on a managed server) rather
    # than propagating into the /render exception handler.
    try:
        BRAILLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, sort_keys=True, ensure_ascii=False)
        with braille_log_lock:
            with BRAILLE_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as error:
        _log(f"Braille event logging skipped (write failed): {error}", force=True)


def _next_braille_send_sequence() -> int:
    global braille_send_sequence
    with braille_log_lock:
        braille_send_sequence += 1
        return braille_send_sequence


def _target_grid(params: dict[str, Any]) -> tuple[int, int] | None:
    """The display grid the client named, or None if it named none.

    One reader for `target_pixel_width`/`target_pixel_height`, so the render, the
    payload sent to the display and both previews cannot disagree about the size
    they are describing.

    Always a (width, height) tuple or None, never a list: it is a fixed pair that
    nothing should append to, and it ends up inside a cache key. A tuple and a
    list of the same numbers serialise identically through json.dumps, so mixing
    them could not split the cache, but a single stated convention beats relying
    on that.

    None means the caller named no size and should use the renderer's own grid,
    DEFAULT_SCREEN_SIZE. Every consumer guards for it rather than substituting a
    size the caller did not ask for.
    """
    width = params.get("target_pixel_width")
    height = params.get("target_pixel_height")
    if width is None or height is None:
        return None
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _make_hifi_preview(
    params: dict[str, Any], model_stem: str, preview_width: int = 800, *, use_cache: bool = True
) -> tuple[str, list[int]]:
    """Render at high resolution and return (base64_png, [height, width]).

    Return strict binary black-on-white preview (no grayscale).

    The shape follows the grid the client named, so this preview describes the
    same display as the tactile one beside it. It used to take its aspect from
    the renderer's default screen size, which meant that with a DotPad attached
    the tactile preview reported the device while this one silently kept showing
    the 96x40 default (#52).
    """
    grid = _target_grid(params)
    if grid is None:
        grid = DEFAULT_SCREEN_SIZE
    w0, h0 = grid
    hifi_h = max(1, int(round(preview_width * h0 / w0)))

    payload = _get_braille_payload_at_size(
        params,
        model_stem=model_stem,
        pixel_width=preview_width,
        pixel_height=hifi_h,
        use_cache=use_cache,
    )
    # Preview is visual black-on-white while payload semantics are raised=255.
    preview_bw = np.where(payload > 0, 0, 255).astype(np.uint8)
    return _img_to_base64_png(preview_bw), list(preview_bw.shape)


def _render_and_send(
    params: dict[str, Any], *, source: str, model_stem: str,
    render_size: tuple[int, int] | None = None,
) -> tuple[np.ndarray, list[float] | None, np.ndarray, RenderResult]:
    engine = get_or_create_renderer(model_stem)
    out_guard, err_guard = _renderer_stdio_guard()
    grid = None
    if render_size is not None:
        grid = [max(1, int(render_size[0])), max(1, int(render_size[1]))]
    with render_lock:
        with out_guard, err_guard:
            result = engine.render(params, screen_size=grid)
    rendered = result.image

    braille_payload = _to_braille_payload(rendered)
    sequence = _next_braille_send_sequence()
    event: dict[str, Any] = {
        "event": "braille_send",
        "sequence": sequence,
        "source": source,
        "input_source": params.get("input_source", "unknown"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": str(_path_for_stem(model_stem)),
        "model_stem": model_stem,
        "params": {
            "view": params.get("view"),
            "zoom": params.get("zoom"),
            "depth": params.get("depth"),
            "renderMode": params.get("renderMode"),
            "mode": params.get("mode"),
            "move_camera_center": params.get("move_camera_center"),
            "print_view": params.get("print_view"),
        },
        "render_shape": list(rendered.shape),
        "payload": _payload_stats(braille_payload),
        "request": _collect_request_context(),
    }

    # Device sends are handled browser-side (Web HID / Web BLE).
    event.update({"status": "success", "send_duration_ms": 0.0})
    _write_braille_event(event)

    bbox = getattr(engine, "bbox", None)
    return rendered, bbox, braille_payload, result


def _save_print_if_requested(params: dict[str, Any], result: RenderResult, img_data: np.ndarray) -> None:
    if not params.get("print_view"):
        return

    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    next_index = 0
    for file_path in RENDERS_DIR.glob("print_*.pdf"):
        parts = file_path.stem.split("_")
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[1])
            next_index = max(next_index, idx + 1)
        except ValueError:
            continue

    stem = (
        f"print_{next_index}_"
        f"{result.render_mode}_"
        f"{result.cut_depth}_"
        f"{result.view_axis}_"
        f"{np.array(result.framing_bounds).tolist()}"
    )
    pdf_path = RENDERS_DIR / f"{stem}.pdf"
    npy_path = RENDERS_DIR / f"{stem}.npy"
    save_binary_array_as_vector_pdf(img_data, str(pdf_path))
    with npy_path.open("wb") as handle:
        np.save(handle, img_data)


def _img_to_base64_png(img_array: np.ndarray) -> str:
    image = Image.fromarray(img_array.astype("uint8"))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _img_to_png_bytes(img_array: np.ndarray) -> io.BytesIO:
    """Return an in-memory PNG file (BytesIO) for send_file."""
    image = Image.fromarray(img_array.astype("uint8"))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _prepare_render_params(data: dict[str, Any] | None) -> tuple[dict[str, Any], int, bool, str]:
    """Merge incoming request data with defaults and return (params, model_stem, is_pan, fingerprint)."""
    if data is None:
        data = {}
    merged = dict(DEFAULT_RENDER_PARAMS)
    merged.update({k: v for k, v in data.items() if v is not None})

    # Normalise view to lowercase string.
    merged["view"] = str(merged.get("view", "")).lower()

    orientation = merged.get("orientation")
    if isinstance(orientation, dict):
        def _vec3(value: Any) -> list[float] | None:
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                return None
            out: list[float] = []
            for component in value:
                try:
                    out.append(float(component))
                except (TypeError, ValueError):
                    return None
            return out

        forward = _vec3(orientation.get("forward"))
        up = _vec3(orientation.get("up"))
        right = _vec3(orientation.get("right"))
        if forward is not None and up is not None and right is not None:
            merged["orientation"] = {
                "scheme": str(orientation.get("scheme", "basis-v1")),
                "forward": forward,
                "up": up,
                "right": right,
            }
        else:
            merged["orientation"] = None
    else:
        merged["orientation"] = None

    camera_center = merged.get("camera_center")
    if isinstance(camera_center, (list, tuple)) and len(camera_center) == 2:
        try:
            merged["camera_center"] = [float(camera_center[0]), float(camera_center[1])]
        except (TypeError, ValueError):
            merged["camera_center"] = None
    else:
        merged["camera_center"] = None

    world_camera_center = merged.get("world_camera_center")
    if isinstance(world_camera_center, (list, tuple)) and len(world_camera_center) == 3:
        try:
            merged["world_camera_center"] = [
                float(world_camera_center[0]),
                float(world_camera_center[1]),
                float(world_camera_center[2]),
            ]
        except (TypeError, ValueError):
            merged["world_camera_center"] = None
    else:
        merged["world_camera_center"] = None

    model_stem = _resolve_model_stem(data.get("model", data.get("current_model")))

    # Camera moves are computed relative to the supplied camera_center, so they
    # must bypass cache lookup; otherwise a move request can hit a cached image
    # for the pre-move center and appear to do nothing.
    is_pan_request = str(merged.get("move_camera_center", "none")).lower() != "none"

    # Fingerprint for caching (excludes transient fields).
    fp_keys = ("view", "zoom", "depth", "renderMode", "projectionMode", "mode", "current_model",
               "orientation",
               "camera_center",
               "world_camera_center",
               "compose_scrollbar", "compose_cursor", "cursor_col", "cursor_row", "cursor_state", "compose_slicegraph", "show_view_info_box",
               "output_device", "slicegraph_locked", "slicegraph_view", "slicegraph_depth", "slicegraph_mode",
               "shape", "superpositionMode",
               # The frame is drawn at this size, so two requests that differ only
               # here are different renders. Omitting it meant connecting a display
               # returned the previous size's frame from cache, and the preview then
               # reported that stale size against the new display's name.
               "target_pixel_width", "target_pixel_height")
    fp_dict = {k: merged.get(k) for k in fp_keys}
    fp_dict["model_stem"] = model_stem
    fingerprint = hashlib.sha256(json.dumps(fp_dict, sort_keys=True).encode()).hexdigest()

    return merged, model_stem, is_pan_request, fingerprint


def _build_quantized_render_key(params: dict[str, Any], model_stem: str) -> str:
    """Build a stable coarse key for near-identical interactive requests."""
    quantized = {
        "model_stem": model_stem,
        "view": str(params.get("view", "")).lower(),
        "orientation": params.get("orientation"),
        "camera_center": params.get("camera_center"),
        "world_camera_center": params.get("world_camera_center"),
        "depth": round(float(params.get("depth", 0)), 0),
        "zoom": round(float(params.get("zoom", 0.0)), 2),
        "renderMode": str(params.get("renderMode", "")).lower(),
        "projectionMode": str(params.get("projectionMode", "orthographic")).lower(),
        "mode": str(params.get("mode", "single")).lower(),
        "compose_scrollbar": bool(params.get("compose_scrollbar", False)),
        "compose_cursor": bool(params.get("compose_cursor", False)), # compose cursor parameters added to quantized key
        "cursor_col": int(params.get("cursor_col", 0)),
        "cursor_row": int(params.get("cursor_row", 0)),
        "cursor_state": str(params.get("cursor_state", "none")).lower(),
        "compose_slicegraph": bool(params.get("compose_slicegraph", False)),
        "slicegraph_locked": bool(params.get("slicegraph_locked", False)),
        "slicegraph_view": str(params.get("slicegraph_view", "")).lower(),
        "slicegraph_depth": round(float(params.get("slicegraph_depth", 0)), 0),
        "slicegraph_mode": str(params.get("slicegraph_mode", "difference")).lower(),
        # Same reason as the exact fingerprint: this decides the size drawn.
        "target_grid": _target_grid(params),
        # Drawn onto the image, so leaving it out meant the checkbox was ignored
        # whenever the render came from cache, even within one window.
        "show_view_info_box": bool(params.get("show_view_info_box", False)),
        # Decides whether the response carries monarch_cells_hex, so a cached
        # answer made for another device arrived without the cells the Monarch
        # needs. Narrower than it looks, since target_grid usually differs too,
        # but not reliably: nothing stops two devices sharing a grid.
        "output_device": str(params.get("output_device", "")).strip().lower(),
        # Neither of these is reachable from the viewer today, and "shape" cannot
        # change the picture while a renderer is built from one file passed twice.
        # Keyed anyway: a key that is right only because of how the caller happens
        # to behave is a trap for whoever changes the caller.
        "shape": str(params.get("shape", "after")).lower(),
        "superpositionMode": str(params.get("superpositionMode", "outline")).lower(),
    }
    return hashlib.sha256(json.dumps(quantized, sort_keys=True).encode()).hexdigest()


def _get_quantized_cached_response(cache_key: str) -> dict[str, Any] | None:
    with quantized_render_cache_lock:
        cached = quantized_render_cache.get(cache_key)
        if cached is None:
            return None
        quantized_render_cache.move_to_end(cache_key)
        return copy.deepcopy(cached)


def _set_quantized_cached_response(cache_key: str, response: dict[str, Any]) -> None:
    with quantized_render_cache_lock:
        quantized_render_cache[cache_key] = copy.deepcopy(response)
        quantized_render_cache.move_to_end(cache_key)
        while len(quantized_render_cache) > max(1, RENDER_QUANTIZED_CACHE_MAX):
            quantized_render_cache.popitem(last=False)


def _build_preview_payload_cache_key(
    params: dict[str, Any], model_stem: str, pixel_width: int, pixel_height: int
) -> str:
    fp_keys = (
        "view",
        "zoom",
        "depth",
        "renderMode",
        "projectionMode",
        "mode",
        "orientation",
        "camera_center",
        "world_camera_center",
        "compose_scrollbar",
        "compose_cursor", # compose cursor parameters added to preview payload cache key
        "cursor_col",
        "cursor_row",
        "cursor_state",
        "compose_slicegraph",
        "show_view_info_box",
        "slicegraph_locked",
        "slicegraph_view",
        "slicegraph_depth",
        "slicegraph_mode",
        "shape",
        "superpositionMode",
    )
    fp_dict = {k: params.get(k) for k in fp_keys}
    fp_dict["model_stem"] = model_stem
    fp_dict["pixel_width"] = int(pixel_width)
    fp_dict["pixel_height"] = int(pixel_height)
    return hashlib.sha256(json.dumps(fp_dict, sort_keys=True).encode()).hexdigest()


def _get_preview_payload_cached(cache_key: str) -> np.ndarray | None:
    with preview_payload_cache_lock:
        cached = preview_payload_cache.get(cache_key)
        if cached is None:
            return None
        preview_payload_cache.move_to_end(cache_key)
        return cached.copy()


def _set_preview_payload_cached(cache_key: str, payload: np.ndarray) -> None:
    with preview_payload_cache_lock:
        preview_payload_cache[cache_key] = payload.copy()
        preview_payload_cache.move_to_end(cache_key)
        while len(preview_payload_cache) > max(1, PREVIEW_PAYLOAD_CACHE_MAX):
            preview_payload_cache.popitem(last=False)


def _render_braille_payload_at_size(
    params: dict[str, Any], *, model_stem: str, pixel_width: int, pixel_height: int
) -> np.ndarray:
    engine = get_or_create_renderer(model_stem)
    out_guard, err_guard = _renderer_stdio_guard()
    with render_lock:
        with out_guard, err_guard:
            result = engine.render(
                params, screen_size=[max(1, int(pixel_width)), max(1, int(pixel_height))]
            )
    return _to_braille_payload(result.image)


def _get_braille_payload_at_size(
    params: dict[str, Any], *, model_stem: str, pixel_width: int, pixel_height: int, use_cache: bool = True
) -> np.ndarray:
    if not use_cache:
        return _render_braille_payload_at_size(
            params,
            model_stem=model_stem,
            pixel_width=pixel_width,
            pixel_height=pixel_height,
        )

    cache_key = _build_preview_payload_cache_key(
        params,
        model_stem=model_stem,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
    )
    cached = _get_preview_payload_cached(cache_key)
    if cached is not None:
        return cached

    payload = _render_braille_payload_at_size(
        params,
        model_stem=model_stem,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
    )
    _set_preview_payload_cached(cache_key, payload)
    return payload


def _render_response(params: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Render, send to braille display, and build JSON response dict."""
    _refresh_model_list_if_stale()
    model_stem = _resolve_model_stem(params.get("model", params.get("current_model")))
    # A client that names a target pixel size used to cost two full renders per
    # interaction: one at the default grid, whose payload only fed telemetry, and
    # a second at the device size that actually reached the display. Render once
    # at the size the client asked for and let it serve both.
    #
    # The Monarch used to be excluded here, on the grounds that its cell packing
    # assumes the default grid. It does, and it still gets it: a Monarch is 48
    # cells by 10 lines and a braille cell is 2x4 pixels, so its size *is* 96x40.
    # Excluding it was also counterproductive, because leaving render_size unset
    # is precisely what sent it down the second-render path it was meant to avoid.
    render_size = _target_grid(params)

    rendered, bbox, braille_payload, render_result = _render_and_send(
        params, source=source, model_stem=model_stem, render_size=render_size
    )

    # What the previews show is the payload that reaches the display, so the two
    # cannot describe different things. There is no second render to reconcile:
    # the frame was drawn at the requested size in the first place.
    preview_payload = braille_payload

    if render_size is not None:
        # Seed the shared cache so the follow-up /render/dotpad-hex request for
        # the same size is a lookup rather than another render.
        _set_preview_payload_cached(
            _build_preview_payload_cache_key(
                params,
                model_stem=model_stem,
                pixel_width=render_size[0],
                pixel_height=render_size[1],
            ),
            braille_payload,
        )
    session_id = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE)) if has_request_context() else None
    db.record_render(
        session_id=session_id,
        view=str(params.get("view", "")),
        render_mode=str(params.get("renderMode", "")),
        depth=float(params.get("depth", 0)),
        zoom=float(params.get("zoom", 0)),
        layout_mode=str(params.get("mode", "single")),
        input_source=source,
    )

    _save_print_if_requested(params, render_result, rendered)

    response: dict[str, Any] = {
        "status": "success",
        "image_base64": _img_to_base64_png(preview_payload),
        "image_shape": list(preview_payload.shape),
        "model_list": MODEL_NAME_LIST,
        # Where this render ended up looking, so the window that asked can send
        # it back next time. This is what keeps a pan inside the window that
        # made it: the renderer no longer remembers, and must not.
        "camera_center": render_result.camera_center,
    }
    if bbox is not None:
        response["bbox"] = bbox
    # Only meaningful for a request that actually asked for the slice graph:
    # slicegraph_ready otherwise carries whatever a previous request left it at,
    # which would misreport for one that wasn't building a graph. The render just
    # done cached this model's engine, so reading the flag is a lookup, not a build.
    if params.get("compose_slicegraph"):
        with models_lock:
            engine = renderers_by_model.get(str(_path_for_stem(model_stem)))
        if engine is not None and not getattr(engine, "slicegraph_ready", True):
            response["slicegraph_ready"] = False
    if str(params.get("output_device", "")).strip().lower() == "monarch_hid":
        cells = _pixels_to_braille_cells(braille_payload, lines=_MONARCH_LINES, cols=_MONARCH_COLS)
        response["monarch_cells_hex"] = cells.hex()
    return response


def initialize_default_braille_render() -> None:
    _log("Preparing initial render...", force=True)

    try:
        merged_params, model_stem, _is_pan_request, _fingerprint = _prepare_render_params(dict(DEFAULT_RENDER_PARAMS))
        rendered, _, _, _ = _render_and_send(merged_params, source="startup", model_stem=model_stem)
        _log(f"Initial render ready: shape={tuple(rendered.shape)}", force=True)
    except Exception as error:
        _log(f"Initial render failed: {error}", force=True)


def open_viewer_in_browser() -> None:
    try:
        webbrowser.open("http://localhost:6969/viewer", new=1)
        _log("Opened viewer: http://localhost:6969/viewer", force=True)
    except Exception as error:
        _log(f"Could not open viewer in browser: {error}", force=True)


@app.route("/viewer", methods=["GET"])
def serve_viewer():
    """Serve the main HTML viewer.

    No session cookie or DB row is created here. Under GDPR/ePrivacy even an
    anonymous persistent identifier requires prior consent, so the session is
    established only once the user answers the consent dialog (POST /session/identify).
    """
    return send_file(REPO_ROOT / "accessible-3d-viewer.html")


def _render_workshop_entry(notice: bool = False) -> Response:
    """Serve the accessible first-name entry page. No JS required; screen-reader/braille
    friendly. The participant types their first name and we normalise it on submit."""
    notice_html = (
        '<p class="workshop-notice" role="alert">We could not find a model for that '
        "name yet. Please check your first name and try again.</p>"
        if notice
        else ""
    )
    html = (REPO_ROOT / "workshop-entry.html").read_text(encoding="utf-8")
    return Response(html.replace("<!--NOTICE-->", notice_html), mimetype="text/html")


@app.route("/workshop", methods=["GET"])
def workshop():
    """Simplified workshop entry point.

    ``?model=<stem>``  serve the viewer (viewer.js renders it in simplified mode).
    ``?name=<first>``  resolve the participant's first name to their latest model and
                       redirect into the pre-loaded viewer, attaching their cookie.
    (no params)        the accessible first-name entry page.
    """
    if request.args.get("model"):
        return send_file(REPO_ROOT / "accessible-3d-viewer.html")

    name_param = request.args.get("name")
    raw_name = (name_param or "").strip()
    if not raw_name:
        # A name that is present but blank (e.g. only spaces) means the participant
        # submitted something unusable, so tell them rather than silently re-rendering
        # an empty form with no explanation for a screen reader to announce.
        return _render_workshop_entry(notice=name_param is not None)

    normalized_name = _normalize_name(raw_name)
    if normalized_name:
        filename = db.get_latest_model_for_identifier(normalized_name)
        if filename:
            _refresh_model_list_if_stale()
            stem = Path(filename).stem
            if stem in MODEL_NAME_LIST:
                # Attach the participant's session cookie so their in-app actions
                # (renders, key presses) log against them, then open their model.
                resp = redirect(f"/workshop?model={quote(stem)}", code=302)
                user_id = db.get_session_id_for_identifier(normalized_name)
                if user_id:
                    _attach_session_cookie(resp, user_id)
                return resp
    return _render_workshop_entry(notice=True)


@app.route("/ingest-test", methods=["GET"])
def ingest_test():
    """Serve the static ingest test harness so it can be reached in a browser while the
    app is running (e.g. in Docker): navigate here, enter a participant name, and send
    the bundled sample STL to /ingest to exercise the whole workshop flow."""
    return send_file(REPO_ROOT / "examples" / "ingest-test.html")


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "status": "running",
            "message": "Accessible 3D Viewer server",
            "endpoints": {
                "/render": "POST - Render CAD view with parameters",
                "/render/fit-view": "POST - Render with the model framed to fit the display",
                "/models": "GET - List available models",
                "/upload": "POST - Upload an STL or STEP model file",
                "/ingest": "POST - Ingest an STL from an external tool; optional first_name, returns a workshop_url + user_id",
                "/workshop": "GET - Simplified viewer; ?model= pre-loads, ?name= resolves a participant's first name",
                "/ingest-test": "GET - Static harness to send the sample STL to /ingest from a browser",
                "/get_data": "GET - Optional cube/slider state",
                "/render/dotpad-hex": "POST - Get render as DotPad hex string for Web SDK",
                "/viewer": "GET - Serve the HTML viewer (required for DotPad Web SDK)",
                "/study": "GET - Participant view for a study session; models load per protocol step",
                "/study/control": "GET - Experimenter control panel (requires ?token=)",
                "/session/me": "GET - Return current session metadata",
                "/session/identify": "POST - Store email/consent for current session",
                "/session/models": "GET - List uploaded models for current session",
                "/models/<filename>": "DELETE - Delete an uploaded model",
                "/events/track": "POST - Record a client-side interaction event",
                "/health": "GET - Deployment self-check: storage layout, writability, database",
            },
        }
    )


@app.route("/health", methods=["GET"])
def health():
    """Report whether this deployment is configured and working correctly.

    Exists so the servers can be checked without shell access to them, which we
    do not have. Everything here is something that has actually broken in
    production: the database the app could not open during the 2026-07-22
    outage, and the storage layout that made uploads public and emptied the
    model list (#102).

    Deliberately reports counts and booleans only, never paths, filenames or
    model names, so it is safe to expose on a public deployment.
    """
    _refresh_model_list_if_stale()

    # Cached: this endpoint is unauthenticated by design and polled every 30s by
    # the container healthcheck, and each probe writes and unlinks a file.
    #
    # "logs" checks BRAILLE_LOG_PATH.parent — the directory the app actually
    # resolved to and is using — not the hardcoded STUDY_LOG_DIR. The two can
    # differ: _resolve_braille_log_path() already falls back to /tmp/cad-a11y/logs
    # on a host where data/logs is not writable by the app user (root-owned bind
    # mount, non-root container user, redeploy resets ownership, etc.), the same
    # class of problem UPLOAD_DIR's own resolution (_resolve_upload_dir) already
    # handles for uploads. Checking the un-resolved path here reported a
    # deployment as unhealthy for a condition the app itself had already
    # recovered from.
    writable = {
        "models": _is_writable_directory_cached(MODEL_DIR),
        "uploads": _is_writable_directory_cached(UPLOAD_DIR),
        "renders": _is_writable_directory_cached(RENDERS_DIR),
        "logs": _is_writable_directory_cached(BRAILLE_LOG_PATH.parent),
    }

    # Opening the file is the thing that failed in the 2026-07-22 outage, and it
    # is a separate question from whether the schema has been created: init_db()
    # runs from main(), so a database can be perfectly openable and still empty.
    # Conflating the two reports a healthy deployment as broken.
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as conn:
            conn.execute("SELECT 1").fetchone()
            initialised = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
            ).fetchone()
        database = "ok" if initialised else "uninitialised"
    except Exception:
        database = "error"

    shipped = 0
    if BUILTIN_SOURCE_DIR.is_dir():
        shipped = sum(
            1 for p in BUILTIN_SOURCE_DIR.iterdir() if p.is_file() and not p.name.startswith(".")
        )
    public_models = len(_builtin_model_stems())

    storage_separated = UPLOAD_DIR.resolve() != MODEL_DIR.resolve()
    checks = {
        # The invariant the built-in/upload distinction rests on (#102).
        "storage_separated": storage_separated,
        "builtin_models_shipped": shipped,
        "public_models": public_models,
        # Files public despite not shipping with the app: left over from before
        # uploads were separated. Not an error, but they are visible to everyone.
        "unexpected_public_models": max(0, public_models - shipped),
        "writable": writable,
        "database": database,
        # Reported so a slow first start reads as work in progress rather than a
        # hang. Deliberately not part of `healthy`: a server that is still
        # warming answers renders perfectly well, just without the head start.
        "warmup": _warmup_snapshot(),
    }

    healthy = (
        storage_separated
        and all(writable.values())
        # "uninitialised" still means the file opened, which is what the outage
        # broke. The schema appears as soon as the server's own startup runs.
        and database in ("ok", "uninitialised")
        and shipped > 0
        and public_models >= shipped
    )
    return jsonify({"status": "ok" if healthy else "degraded", "checks": checks}), (
        200 if healthy else 503
    )


@app.route("/render", methods=["POST"])
def render_view():
    try:
        t0 = time.perf_counter()
        _refresh_model_list_if_stale()
        merged_params, model_stem, is_pan_request, fingerprint = _prepare_render_params(request.get_json(silent=True))
        quantized_cache_key = _build_quantized_render_key(merged_params, model_stem)

        # A slice-graph response is not a pure function of these params: the
        # precompute it plots finishes in the background, independent of any
        # request, so identical params legitimately produce a different (correct)
        # response once that finishes. A response cached while precompute was
        # still pending would then be served forever, even once the real graph
        # was ready, because nothing here knows precompute state changed on its
        # own. Skip the cache entirely for these; the render is cheap once
        # precompute is done, so this only costs the redundant plot.
        skip_cache = bool(merged_params.get("compose_slicegraph"))

        if not skip_cache and not is_pan_request and merged_params.get("print_view") is not True:
            cached_response = _get_quantized_cached_response(quantized_cache_key)
            if cached_response is not None:
                cached_response["model_list"] = MODEL_NAME_LIST
                debug = dict(cached_response.get("debug", {}))
                debug.update(
                    {
                        "phase1_exact_cache_hit": False,
                        "phase1_quantized_cache_hit": True,
                        "phase1_total_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                    }
                )
                cached_response["debug"] = debug
                # Recorded on the cache path too: a cached response still put a
                # new image under the participant's fingers, and dropping those
                # would lose most of a fast arrow-key traversal from the record.
                study.record_render_for_request(merged_params, model_stem=model_stem, cache_hit=True)
                return jsonify(cached_response), 200

        response = _render_response(merged_params, source="http_render")
        debug = dict(response.get("debug", {}))
        debug.update(
            {
                "phase1_exact_cache_hit": False,
                "phase1_quantized_cache_hit": False,
                "phase1_total_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }
        )
        response["debug"] = debug
        study.record_render_for_request(merged_params, model_stem=model_stem, cache_hit=False)

        # A pan is not cacheable in either direction. "move left" means move from
        # wherever this window already was, and neither key carries that starting
        # point or the verb, so the same key describes two different pictures.
        # Reads were already guarded; writes were not, so a pan stored its result
        # under the unpanned key and the next window to ask got it. The request
        # after a pan carries the new centre explicitly and caches correctly.
        cacheable = not skip_cache and not is_pan_request and merged_params.get("print_view") is not True
        if cacheable:
            _set_quantized_cached_response(quantized_cache_key, response)
        return jsonify(response), 200
    except Exception as error:
        _log(f"Error rendering: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/render/status", methods=["POST"])
def render_status():
    """Per-model async-work status — today, whether slice-graph precompute has
    finished. A cheap, render_lock-free check, deliberately separate from
    /render itself (unlike /health, this is per-model, so it can't live there:
    /health is a fixed, unauthenticated, model-name-free deployment check
    polled by infra, not something with per-viewer-session context).

    The client polls this while waiting for a locked slice graph's first real
    render (see viewer.js's scheduleSliceGraphStatusCheck). Polling /render
    itself for this would repeatedly re-run the same, non-trivial
    matplotlib/shapely-based render() under render_lock — real CPU competing
    with the very background precompute thread the poll is waiting on, which
    can starve it indefinitely under constrained CPU. This only reads a flag
    already set by the background thread; it never renders and never blocks
    on render_lock, so it cannot compete with the thing it's checking on.

    Looks the renderer up directly rather than through get_or_create_renderer,
    so calling this can't itself construct a renderer (and thus can't be the
    thing that kicks off a mesh load) for a model nobody has actually rendered
    yet — "not ready" is the correct answer for that case anyway.
    """
    data = request.get_json(silent=True) or {}
    model_stem = _resolve_model_stem(data.get("model", data.get("current_model")))
    with models_lock:
        engine = renderers_by_model.get(str(_path_for_stem(model_stem)))
    slice_graphs_ready = bool(engine is not None and getattr(engine, "_slice_graphs_ready", False))
    return jsonify({"slice_graphs_ready": slice_graphs_ready}), 200


@app.route("/render/fit-view", methods=["POST"])
def fit_render_view():
    data = request.get_json(silent=True) or {}
    merged_params, model_stem, _, _ = _prepare_render_params(data)
    engine = get_or_create_renderer(model_stem)
    fit = engine.compute_fit_view(merged_params, screen_size=_target_grid(merged_params))
    return jsonify({"status": "success", **fit}), 200

@app.route("/models", methods=["GET"])
def models_endpoint():
    """List the models on disk.

    Read only. Selecting a model is not a server-side action: a render names the
    model it wants, so there is nothing here to set. The POST branch that used to
    write a process-wide "current model" is gone, since one window choosing a
    model would change what every other window rendered next.
    """
    global AVAILABLE_MODELS, MODEL_NAME_LIST

    with models_lock:
        AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
        MODEL_NAME_LIST = [p.stem for p in AVAILABLE_MODELS]
    return jsonify(
        {
            "status": "success",
            "model_list": MODEL_NAME_LIST,
            "model_paths": [str(model) for model in AVAILABLE_MODELS],
        }
    ), 200


_ALLOWED_EXTENSIONS = {".stl", ".step"}
_MAX_UPLOAD_SESSION_ID_LEN = 128

# Tracks uploaded model paths by browser-tab session id so they can be cleaned up
# when the page closes. Values are absolute path strings under UPLOAD_DIR.
uploaded_models_by_session: dict[str, set[str]] = {}
uploaded_models_lock = threading.Lock()


def _sanitize_upload_session_id(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    if len(value) > _MAX_UPLOAD_SESSION_ID_LEN:
        value = value[:_MAX_UPLOAD_SESSION_ID_LEN]
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(ch not in allowed for ch in value):
        return None
    return value


def _register_uploaded_model(session_id: str | None, model_path: Path) -> None:
    if not session_id:
        return
    resolved_upload_root = UPLOAD_DIR.resolve()
    resolved_model_path = model_path.resolve()
    try:
        resolved_model_path.relative_to(resolved_upload_root)
    except ValueError:
        return
    with uploaded_models_lock:
        session_models = uploaded_models_by_session.setdefault(session_id, set())
        session_models.add(str(resolved_model_path))


def _cleanup_uploaded_models_for_session(session_id: str | None) -> dict[str, Any]:
    if not session_id:
        return {"deleted": [], "errors": []}

    with uploaded_models_lock:
        tracked_models = uploaded_models_by_session.pop(session_id, set())

    if not tracked_models:
        return {"deleted": [], "errors": []}

    deleted: list[str] = []
    errors: list[str] = []
    upload_root = UPLOAD_DIR.resolve()
    for model_str in tracked_models:
        model_path = Path(model_str)
        try:
            resolved_model_path = model_path.resolve()
            resolved_model_path.relative_to(upload_root)
            if resolved_model_path.exists() and resolved_model_path.is_file():
                resolved_model_path.unlink()
                deleted.append(str(resolved_model_path))
        except Exception as exc:
            errors.append(f"{model_path}: {exc}")

    # Refresh in-memory model list and invalidate caches after cleanup.
    global AVAILABLE_MODELS, MODEL_NAME_LIST
    with models_lock:
        AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
        MODEL_NAME_LIST = [p.stem for p in AVAILABLE_MODELS]
        # Only the files this tab actually removed. Dropping every renderer made
        # one visitor closing a tab reload every mesh for everyone still working.
        for removed in deleted:
            _forget_renderer(Path(removed))


    with quantized_render_cache_lock:
        quantized_render_cache.clear()
    with preview_payload_cache_lock:
        preview_payload_cache.clear()

    return {"deleted": deleted, "errors": errors}


def _save_and_index_stl(
    save_fn: Callable[[Path], Any],
    requested_name: str,
    *,
    session_id: str | None = None,
    original_name: str | None = None,
    public: bool = False,
) -> tuple[str, Path, int]:
    """Persist an uploaded STL/STEP file and refresh the in-memory model list.

    ``save_fn(dest)`` writes the bytes to the chosen path (e.g. ``FileStorage.save``
    or ``dest.write_bytes``). Shared by /upload and /ingest so both apply the
    identical sanitisation, collision-rename, DB registration and cache
    invalidation under the same locks.

    ``public=True`` writes into MODEL_DIR instead of UPLOAD_DIR, making the file a
    built-in visible to everyone. Only /ingest uses it: an ingested model has no
    browser session to own it, so under the upload rules it would be visible to
    nobody and the workshop flow would break. /ingest is slated for removal, and
    this carve-out goes with it.

    Returns ``(filename, dest_path, new_index)``. Raises ``ValueError`` for a
    missing name or unsupported extension; save/registration errors propagate.
    """
    global AVAILABLE_MODELS, MODEL_NAME_LIST

    filename = secure_filename(requested_name or "")
    if not filename:
        raise ValueError("No file selected")
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{suffix}'. Use .stl or .step")

    target_dir = MODEL_DIR if public else UPLOAD_DIR
    # Uniqueness is checked on the stem across BOTH directories, not just on
    # whether the destination path is free. Models are addressed by stem on the
    # wire, and the client tells built-ins apart from uploads by stem too, so an
    # upload allowed to reuse a built-in's stem would both collide and be shown
    # to every visitor. Before the storage split a plain dest.exists() sufficed,
    # because everything lived in one directory.
    # Claimed under models_lock, because the check and the write have to be one
    # step. Two uploads of the same name could otherwise both look free, both
    # rename to the same stem, and end up as two models claiming one name.
    target_dir.mkdir(parents=True, exist_ok=True)
    with models_lock:
        dest = target_dir / filename
        if dest.exists() or _stem_is_taken(Path(filename).stem):
            stem = Path(filename).stem
            filename = f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
            dest = target_dir / filename
        # Reserve the name before releasing the lock, so a second upload arriving
        # now sees it taken. The real bytes overwrite this immediately below.
        dest.touch()

    save_fn(dest)

    if session_id:
        try:
            db.register_model(
                session_id,
                filename,
                original_name or filename,
                dest.stat().st_size,
                _sha256_file(dest),
            )
        except Exception as err:
            _log(f"register_model failed for {filename}: {err}")

    with models_lock:
        AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
        MODEL_NAME_LIST = [p.stem for p in AVAILABLE_MODELS]
        # Nothing to evict: this path is new, and a name collision is renamed
        # rather than overwritten, so no cached renderer can be stale. Renderers
        # are keyed by path, so the list reordering underneath them is harmless.
        new_index = next((i for i, p in enumerate(AVAILABLE_MODELS) if p == dest), 0)

    # Processed in the background, so whoever uploaded it is not also the one who
    # waits for its first render.
    enqueue_model_for_warmup(dest)

    with quantized_render_cache_lock:
        quantized_render_cache.clear()
    with preview_payload_cache_lock:
        preview_payload_cache.clear()

    return filename, dest, new_index


@app.route("/upload", methods=["POST"])
def upload_model():
    """Accept an STL or STEP file upload and add it to the model list."""
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file field in request"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"status": "error", "message": "No file selected"}), 400

    upload_session_id = _sanitize_upload_session_id(
        request.form.get("upload_session_id") or request.headers.get("X-Upload-Session")
    )
    cookie_sid = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE))

    try:
        filename, dest, new_index = _save_and_index_stl(
            file.save,
            file.filename,
            session_id=cookie_sid,
            original_name=file.filename,
        )
    except ValueError as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    except Exception as error:
        return jsonify(
            {"status": "error", "message": f"Could not save file in '{UPLOAD_DIR}': {error}"}
        ), 500

    _register_uploaded_model(upload_session_id, dest)

    _log(f"Model uploaded: {filename}", force=True)
    return jsonify({
        "status": "success",
        "filename": filename,
        "model_list": MODEL_NAME_LIST,
        # The name is how a model is addressed. new_model_index is kept for one
        # release for anything still reading it; it is a position in a list that
        # the next upload renumbers.
        "model_stem": Path(filename).stem,
        "new_model_index": new_index,
    }), 200


@app.route("/ingest", methods=["POST"])
def ingest_model():
    """Receive an STL from an external tool, store it, and return a URL that opens it
    in the simplified /workshop viewer.

    Body: multipart form-data with a ``file`` field, or a raw STL body (name via
    ``?filename=`` / ``X-Filename``). The participant's first name is sent by the
    calling tool as ``first_name`` (form field, query parameter or ``X-First-Name``
    header). It is optional: when given, the model is stored under a workshop
    participant (a unique user id keyed on the first name) so it can be retrieved at a
    braille station and the participant's actions are logged; without one the ingest is
    anonymous and the returned workshop_url opens the model directly.
    """
    # Only touch request.files when the body actually is multipart: merely
    # accessing it makes Werkzeug parse the whole body as form data whenever
    # Content-Type looks like a form (multipart *or* x-www-form-urlencoded),
    # which drains the input stream. A raw-body caller that doesn't set an
    # explicit octet-stream content type (many HTTP clients default raw
    # POSTs to x-www-form-urlencoded) would then hit request.get_data()
    # below on an already-empty stream and get a bogus "No STL provided".
    is_multipart = (request.content_type or "").startswith("multipart/form-data")
    upload = request.files.get("file") if is_multipart else None
    if upload and upload.filename:
        save_fn = upload.save
        requested_name = upload.filename
    else:
        raw = request.get_data(cache=False)
        if not raw:
            return jsonify({"status": "error", "message": "No STL provided"}), 400
        requested_name = (
            request.args.get("filename") or request.headers.get("X-Filename") or "model.stl"
        )

        def save_fn(dest: Path) -> None:
            dest.write_bytes(raw)

    # The calling tool sends the participant's first name; we key the participant on it
    # and give them a unique user id. Without a name the ingest is anonymous (single
    # station: the returned workshop_url opens the model directly).
    first_name = (
        request.form.get("first_name")
        or request.args.get("first_name")
        or request.headers.get("X-First-Name")
    )
    normalized_name = _normalize_name(first_name)
    user_id = _participant_for_name(normalized_name) if normalized_name else None

    try:
        filename, dest, new_index = _save_and_index_stl(
            save_fn,
            requested_name,
            session_id=user_id,
            original_name=requested_name,
            # Ingested models stay public; see the `public` note on the helper.
            public=True,
        )
    except ValueError as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    except Exception as error:
        return jsonify(
            {"status": "error", "message": f"Could not save file in '{UPLOAD_DIR}': {error}"}
        ), 500

    stem = dest.stem
    base = (os.getenv("PUBLIC_BASE_URL") or request.host_url).rstrip("/")
    if normalized_name:
        # Open via the participant so the viewer sets their session cookie and loads
        # their latest model (this ingest); their in-app actions then log against them.
        workshop_path = f"/workshop?name={quote(first_name.strip())}"
    else:
        workshop_path = f"/workshop?model={quote(stem)}"
    workshop_url = f"{base}{workshop_path}"

    # Single-station conveniences are opt-in so a shared braille station is never
    # yanked away from the model it is currently showing. The host pop-up needs the
    # client to ask (open=1) *and* the host to allow it (INGEST_OPEN_ON_HOST), so a
    # remote caller cannot open windows on the server machine on its own.
    if request.args.get("open") == "1" or request.args.get("open_here") == "1":
        _push_sse({"load_model": stem})
        if os.getenv("INGEST_OPEN_ON_HOST", "0") == "1":
            try:
                webbrowser.open(f"http://localhost:6969{workshop_path}", new=1)
            except Exception as err:
                _log(f"INGEST_OPEN_ON_HOST failed: {err}")

    _log(
        f"Model ingested: {filename} → index {new_index} "
        f"(participant {normalized_name or 'anonymous'})",
        force=True,
    )

    # A plain browser form navigation gets redirected straight into the pre-loaded
    # viewer; fetch/XHR and other API clients get JSON.
    accepts_html = "text/html" in request.headers.get("Accept", "")
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if accepts_html and not is_xhr:
        return redirect(workshop_url, code=302)

    return jsonify({
        "status": "success",
        "filename": filename,
        "model_stem": stem,
        "new_model_index": new_index,
        "first_name": first_name.strip() if first_name else None,
        "user_id": user_id,
        "workshop_url": workshop_url,
        "workshop_entry_url": f"{base}/workshop",
    }), 200


@app.route("/session/me", methods=["GET"])
def session_me():
    session_id = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE))
    if not session_id:
        return jsonify({"session_id": None, "consent_given": None}), 200
    row = db.get_session(session_id)
    if not row:
        return jsonify({"session_id": None, "consent_given": None}), 200
    model_count = len(db.get_session_models(session_id))
    return jsonify(
        {
            "session_id": row["id"],
            "identifier": row["identifier"],
            "consent_given": row["consent_given"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "model_count": model_count,
        }
    ), 200


@app.route("/session/identify", methods=["POST"])
def session_identify():
    """Record the user's consent choice (and optional email), creating the session.

    This is the first point at which a session is persisted: /viewer deliberately
    does not, so no identifier is stored before the user answers the consent dialog.
    Email is validated before the row is created, so a rejected request leaves no
    orphan session behind. The response carries the persistent cad_session cookie.
    """
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    consent = data.get("consent", False)

    if email is not None:
        email = str(email).strip()
        if email and not _EMAIL_RE.match(email):
            return jsonify({"status": "error", "message": "Invalid email address"}), 400
        email = email or None

    session_id = _get_or_create_session_id()  # reuse a valid cookie or mint a new UUID
    db.upsert_session(session_id)
    db.save_session_identifier(session_id, email, bool(consent))

    response = jsonify({"status": "success"})
    _attach_session_cookie(response, session_id)
    return response, 200


@app.route("/session/models", methods=["GET"])
def session_models():
    session_id = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE))
    if not session_id:
        return jsonify({"models": []}), 200
    raw_models = db.get_session_models(session_id)
    # Cross-check each filename against disk; surface availability to the client.
    models = []
    for m in raw_models:
        on_disk = (UPLOAD_DIR / m["filename"]).exists()
        models.append({**m, "available": on_disk})
    return jsonify({"models": models}), 200


@app.route("/models/<filename>", methods=["DELETE"])
def delete_model(filename: str):
    session_id = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE))
    if not session_id:
        return jsonify({"status": "error", "message": "No active session"}), 400

    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return jsonify({"status": "error", "message": "Invalid filename"}), 400

    if not db.session_owns_model(session_id, safe_name):
        return jsonify({"status": "error", "message": "Model not found or already deleted"}), 404

    dest = UPLOAD_DIR / safe_name
    try:
        if dest.exists():
            dest.unlink()
    except Exception as err:
        return jsonify({"status": "error", "message": f"Could not remove file: {err}"}), 500

    db.mark_model_deleted(session_id, safe_name)

    global AVAILABLE_MODELS, MODEL_NAME_LIST
    with models_lock:
        AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
        MODEL_NAME_LIST = [p.stem for p in AVAILABLE_MODELS]
        _forget_renderer(dest)
    with quantized_render_cache_lock:
        quantized_render_cache.clear()
    with preview_payload_cache_lock:
        preview_payload_cache.clear()

    return jsonify({"status": "success", "filename": safe_name}), 200


@app.route("/events/track", methods=["POST"])
def track_event():
    """Record a client-side interaction event (section dwell, shortcut, device connect, etc.)."""
    session_id = _validate_session_cookie(request.cookies.get(_SESSION_COOKIE))
    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type", "")).strip()
    if event_type not in _ALLOWED_EVENT_TYPES:
        return jsonify({"status": "error", "message": f"Unknown event_type '{event_type}'"}), 400
    event_data = data.get("event_data")
    if event_data is not None and not isinstance(event_data, dict):
        return jsonify({"status": "error", "message": "event_data must be an object"}), 400
    db.record_page_event(session_id, event_type, event_data)
    return jsonify({"status": "success"}), 200


@app.route("/uploads/cleanup", methods=["POST"])
def cleanup_uploaded_models():
    payload = request.get_json(silent=True) or {}
    upload_session_id = _sanitize_upload_session_id(
        payload.get("upload_session_id")
        or request.form.get("upload_session_id")
        or request.args.get("upload_session_id")
        or request.headers.get("X-Upload-Session")
    )
    if not upload_session_id:
        return jsonify({"status": "error", "message": "Missing upload_session_id"}), 400

    result = _cleanup_uploaded_models_for_session(upload_session_id)
    return jsonify(
        {
            "status": "success",
            "deleted_count": len(result["deleted"]),
            "error_count": len(result["errors"]),
        }
    ), 200


@app.route("/events", methods=["GET"])
def sse_events():
    """Server-Sent Events stream for hardware state changes (WitMotion IMU, Slider).

    Replaces 1-second polling for hardware input — events are pushed immediately
    when device state changes, reducing perceived latency from ~1000 ms to ~10 ms.
    """
    def generate():
        client_queue = _queue_module.Queue(maxsize=20)
        with _sse_clients_lock:
            _sse_clients.append(client_queue)
        try:
            # Send current state immediately on connect so the client is in sync.
            with models_lock:
                initial = {
                    "model_list": MODEL_NAME_LIST,
                }
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    msg = client_queue.get(timeout=25)
                    yield msg
                except _queue_module.Empty:
                    yield ": heartbeat\n\n"  # Keep TCP alive through proxies
        finally:
            with _sse_clients_lock:
                if client_queue in _sse_clients:
                    _sse_clients.remove(client_queue)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/get_data", methods=["GET"])
def get_data():
    with models_lock:
        payload = {
            "status": "success",
            "model_list": MODEL_NAME_LIST,
            "builtin_model_stems": _builtin_model_stems(),
        }
    return jsonify(payload), 200


@app.route("/render/export-source", methods=["POST"])
def render_export_source():
    """Render a high-fidelity tactile source image for export workflows.

    This endpoint intentionally avoids sending anything to braille hardware.
    """
    try:
        params = request.get_json(silent=True) or {}
        merged_params = dict(DEFAULT_RENDER_PARAMS)
        merged_params.update(params)
        merged_params["view"] = str(merged_params.get("view", "")).lower()
        merged_params["print_view"] = False
        # The client names the model on this request too. It used to be ignored,
        # so an export handed back whatever model the server-wide value happened
        # to hold: you could export somebody else's model.
        export_stem = _resolve_model_stem(
            merged_params.get("model", merged_params.get("current_model"))
        )

        export_width = _coerce_positive_int(params.get("export_width", 1000), 1000)

        engine = get_or_create_renderer(export_stem)

        aspect_ratio = float(DEFAULT_SCREEN_SIZE[1]) / float(DEFAULT_SCREEN_SIZE[0])
        export_height = max(1, int(round(export_width * aspect_ratio)))
        with render_lock:
            out_guard, err_guard = _renderer_stdio_guard()
            with out_guard, err_guard:
                result = engine.render(
                    merged_params, screen_size=[export_width, export_height]
                )

        tactile_payload = _to_braille_payload(result.image)
        response = {
            "status": "success",
            "message": "Export source render complete",
            "image_shape": list(tactile_payload.shape),
            "image_base64": _img_to_base64_png(tactile_payload),
            "export_width": export_width,
            "export_height": export_height,
        }
        return jsonify(response), 200
    except Exception as error:
        _log(f"Error rendering export source: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/render/preview", methods=["POST"])
def render_preview():
    """Render only the high-fidelity browser preview.

    This endpoint avoids braille-device work so the main tactile render can
    return immediately and the preview can be fetched afterward.
    """
    try:
        _refresh_model_list_if_stale()
        merged_params, model_stem, is_pan_request, _fingerprint = _prepare_render_params(request.get_json(silent=True))
        preview_width = _coerce_positive_int(merged_params.get("preview_width", 800), 800)
        use_cache = (
            not is_pan_request
            and merged_params.get("print_view") is not True
            # See render_view(): a slice-graph output is time-dependent
            # (precompute finishes on its own), so it must not be cached.
            and not merged_params.get("compose_slicegraph")
        )
        preview_b64, preview_shape = _make_hifi_preview(
            merged_params,
            model_stem,
            preview_width=preview_width,
            use_cache=use_cache,
        )
        return jsonify(
            {
                "status": "success",
                "render_preview_base64": preview_b64,
                "render_preview_shape": preview_shape,
            }
        ), 200
    except Exception as error:
        _log(f"Error rendering preview: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/render/dotpad-hex", methods=["POST"])
def render_dotpad_hex():
    """Return the current render as a DotPad-compatible hex string.

    The hex string can be passed directly to the DotPad Web SDK's
    ``displayGraphicData(hexString)`` method.
    """
    try:
        params = request.get_json(silent=True) or {}
        merged_params, model_stem, is_pan_request, _fingerprint = _prepare_render_params(params)

        # Use device-reported cell grid if provided; fall back to DotPad 300A defaults.
        dotpad_cols = max(1, min(int(params.get("dotpad_cols", _DOTPAD_COLS)), 128))
        dotpad_rows = max(1, min(int(params.get("dotpad_rows", _DOTPAD_LINES)), 64))
        total_cells = dotpad_cols * dotpad_rows
        # Each braille cell is 2 px wide × 4 px tall.
        pixel_width  = dotpad_cols * 2
        pixel_height = dotpad_rows * 4

        use_cache = (
            not is_pan_request
            and merged_params.get("print_view") is not True
            # See render_view(): a slice-graph output is time-dependent
            # (precompute finishes on its own), so it must not be cached.
            and not merged_params.get("compose_slicegraph")
        )
        braille_payload = _get_braille_payload_at_size(
            merged_params,
            model_stem=model_stem,
            pixel_width=pixel_width,
            pixel_height=pixel_height,
            use_cache=use_cache,
        )
        cells = _pixels_to_braille_cells_dotpad(
            braille_payload, lines=dotpad_rows, cols=dotpad_cols,
        )
        cell_bytes = cells[:total_cells].ljust(total_cells, b"\x00")
        hex_string = cell_bytes.hex().upper()

        return jsonify({
            "status": "success",
            "dotpad_graphic_hex": hex_string,
            "cell_count": total_cells,
        }), 200
    except Exception as error:
        _log(f"Error rendering DotPad hex: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/static/js/<path:filename>", methods=["GET"])
def serve_static_js(filename):
    """Serve JavaScript files from the static/js directory."""
    response = send_file(REPO_ROOT / "static" / "js" / filename, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/static/css/<path:filename>", methods=["GET"])
def serve_static_css(filename):
    """Serve CSS files from the static/css directory."""
    response = send_file(REPO_ROOT / "static" / "css" / filename, mimetype="text/css")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def main() -> int:
    _log("Server starting on http://localhost:6969", force=True)
    _log(f"Model directory: {MODEL_DIR}", force=True)
    _log(f"Upload directory: {UPLOAD_DIR}", force=True)
    _log(f"Upload directory writable: {_is_writable_directory(UPLOAD_DIR)}", force=True)
    _log(f"Models found: {len(AVAILABLE_MODELS)}", force=True)
    _log("Endpoints: POST /render, GET /get_data", force=True)
    _log(f"Braille send logs: {BRAILLE_LOG_PATH}", force=True)
    if QUIET_MODE:
        _log("Output mode: quiet (set SERVER_VERBOSE=1 for debug logs)", force=True)

    db.init_db()
    # A separate database from the analytics one, so a study session that cannot
    # be re-run is never at the mercy of a change to product telemetry.
    study_db.init_db()
    _log(f"Study database: {study_db.DB_PATH}", force=True)
    _log("Study control panel: /study/control", force=True)
    initialize_default_braille_render()
    start_model_warmup()
    open_viewer_in_browser()

    _log("Ready.", force=True)
    # threaded=True lets /events (SSE) and /get_data respond concurrently while
    # a render is in progress; render_lock still serializes the renders themselves.
    app.run(debug=False, host="0.0.0.0", port=6969, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())