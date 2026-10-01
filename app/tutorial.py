"""The first-run tutorial's server side.

The tutorial itself runs in the browser (static/js/tutorial.js). What it needs
from here is four things it cannot work out alone:

* ``GET /tutorial/lessons``: the lesson text with the key names for the display
  the person said they have, plus the practice mug's landmarks.
* ``POST /tutorial/test-pattern``: a frame and a raised block in one corner,
  already packed for the Monarch and the DotPad, so the first thing under a new
  user's fingers is something they can check in seconds.
* ``POST /tutorial/locate``: where the mug's handle and body sit on the pins for
  the render the viewer just asked for, so a lesson can tell whether the cursor
  is on the handle or the handle is centred.
* ``GET /tutorial/prints.zip``: the mug and ten sliced plaques to 3D print.

None of them writes anything, to disk or to any recorder, and none of them
knows who is asking. That is why the blueprint is registered on every server,
a demo station included, and why it is not in server._CLOSED_TO_DEMO.

This module does not import app.server. The server runs as ``python -m
app.server``, so importing it from here would load a second copy of it with
its own Flask app and its own renderers. The few server functions the
endpoints need are handed over through configure() instead, the same way
app.study is given its model list.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import itertools
import json
import math
import re
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import Any

import numpy as np
import shapely
import trimesh
from flask import Blueprint, jsonify, request, send_file
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from trimesh.exchange.stl import load_stl

from .braille_display import _pixels_to_braille_cells, _pixels_to_braille_cells_dotpad

# The renderer's own framing and cutting functions are imported where /locate
# uses them rather than here. scripts/build_tutorial_mug.py imports the
# geometry helpers below, and loading the renderer brings in the CAD kernels,
# which in this environment abort the interpreter as it exits.

# Renders the tutorial asks for carry this header. The server skips the usage
# analytics row for them, since a lesson's scripted poses would otherwise read
# as somebody exploring the mug; see server._render_response.
TUTORIAL_HEADER = "X-CAD-Tutorial"

TUTORIAL_MODEL_STEM = "tutorial_mug"
LANDMARKS_PATH = Path(__file__).resolve().with_name("tutorial_mug.landmarks.json")

# What a "tutorial" event sent to /events/track may say. Coarse on purpose: how
# far people get and where they stop, never which keys they pressed.
EVENT_ACTIONS = frozenset(
    {"loaded", "lesson_completed", "lesson_skipped", "exited", "completed", "redo", "resumed"}
)
_LESSON_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")

DISPLAYS = ("monarch", "dotpad", "none")

# The test pattern: a one-pin frame round the whole grid and a 3x3 raised block
# whose outer corner sits 3 pins in from the named corner, so two lowered pins
# separate it from the frame and it cannot be mistaken for part of it.
PATTERN_CORNERS = ("tl", "tr", "bl", "br")
PATTERN_INSET = 3
PATTERN_BLOCK = 3
PATTERN_MIN_SIZE = 12
PATTERN_MAX_SIZE = 200

PLAQUE_THICKNESS_MM = 3.0
PLAQUE_PERCENTS = tuple(range(5, 100, 10))

# How far outside the body's wall a point has to be before it counts as the
# handle. The wall is faceted, so its own vertices wander a little either side
# of the radius measured for it.
HANDLE_MARGIN_MM = 0.5

# The slice graph is drawn over the bottom of the frame: ten rows of graph and
# a divider row above them (CADComparisonRenderer.render, graph_height_px).
_SLICE_GRAPH_ROWS = 11

tutorial_bp = Blueprint("tutorial", __name__)


# ---------------------------------------------------------------------------
# What the server hands over
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ServerHooks:
    prepare_render_params: Callable[[dict[str, Any] | None], tuple[dict[str, Any], str, bool, str]]
    renderer_for_stem: Callable[[str], Any]
    model_path_for_stem: Callable[[str], Path]
    target_grid: Callable[[dict[str, Any]], tuple[int, int] | None]
    builtin_dir: Callable[[], Path]


_hooks: _ServerHooks | None = None


def configure(
    *,
    prepare_render_params: Callable[[dict[str, Any] | None], tuple[dict[str, Any], str, bool, str]],
    renderer_for_stem: Callable[[str], Any],
    model_path_for_stem: Callable[[str], Path],
    target_grid: Callable[[dict[str, Any]], tuple[int, int] | None],
    builtin_dir: Callable[[], Path],
) -> None:
    """Called once by app.server, before any request, with the functions the
    render path itself uses. /tutorial/locate has to read a render request
    exactly as /render reads it, so it borrows the reader rather than keeping a
    copy of it here."""
    global _hooks
    _hooks = _ServerHooks(
        prepare_render_params=prepare_render_params,
        renderer_for_stem=renderer_for_stem,
        model_path_for_stem=model_path_for_stem,
        target_grid=target_grid,
        builtin_dir=builtin_dir,
    )


def _server() -> _ServerHooks:
    if _hooks is None:
        raise RuntimeError("app.tutorial.configure() was not called by the server")
    return _hooks


def is_tutorial_request(req: Any) -> bool:
    """Whether this request came from a tutorial render."""
    headers = getattr(req, "headers", None)
    if headers is None:
        return False
    return str(headers.get(TUTORIAL_HEADER, "")).strip() == "1"


def clean_event_data(event_data: Any) -> dict[str, Any] | None:
    """The part of a "tutorial" analytics event that may be stored, or None if
    it is not one. Only the action and the lesson id survive, so a client that
    sends more cannot widen what is recorded."""
    if not isinstance(event_data, dict):
        return None
    action = event_data.get("action")
    if action not in EVENT_ACTIONS:
        return None
    lesson = event_data.get("lesson")
    if lesson is not None and not (isinstance(lesson, str) and _LESSON_ID_RE.match(lesson)):
        return None
    return {"action": action, "lesson": lesson}


# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------

_landmarks_lock = threading.Lock()
_landmarks_cache: dict[str, Any] | None = None


def load_landmarks() -> dict[str, Any]:
    """The parsed landmarks file, read once. A copy each time, so a caller that
    adds to it cannot change what the next request sees."""
    global _landmarks_cache
    with _landmarks_lock:
        if _landmarks_cache is None:
            _landmarks_cache = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
        return json.loads(json.dumps(_landmarks_cache))


# ---------------------------------------------------------------------------
# Geometry shared with scripts/build_tutorial_mug.py
# ---------------------------------------------------------------------------

def load_mesh_as_renderer_does(path: Path) -> trimesh.Trimesh:
    """An STL read with the reader CADComparisonRenderer uses, so what is
    measured here is the mesh the viewer draws. Its repairs are left out: they
    change nothing on a closed mesh like the mug."""
    with Path(path).open("rb") as handle:
        loaded = load_stl(handle)
    return trimesh.Trimesh(loaded["vertices"], loaded["faces"], loaded["face_normals"])


def region_polygons(region: Any) -> list[Polygon]:
    """The separate pieces of a section, largest first."""
    if region is None or region.is_empty:
        return []
    if isinstance(region, Polygon):
        pieces = [region]
    elif isinstance(region, MultiPolygon):
        pieces = list(region.geoms)
    else:
        pieces = [g for g in getattr(region, "geoms", []) if isinstance(g, Polygon)]
    return sorted((p for p in pieces if not p.is_empty), key=lambda p: -p.area)


def section_region(mesh: Any, axis: int, value: float) -> Any:
    """The cut through a mesh where coordinate ``axis`` equals ``value``, as a
    shapely (Multi)Polygon in the other two coordinates (in x, y, z order), or
    None where the plane misses it.

    Built from the section's closed loops by even-odd filling rather than with
    trimesh's polygons_full, which needs rtree to nest the loops, and rtree is
    not in this environment. XOR of the loops nests them the same way: a loop
    inside another cuts a hole, and a loop inside that fills it again.
    """
    normal = np.zeros(3)
    normal[axis] = 1.0
    origin = np.zeros(3)
    origin[axis] = float(value)
    section = mesh.section(plane_origin=origin, plane_normal=normal)
    if section is None:
        return None
    keep = [a for a in range(3) if a != axis]
    loops = []
    for loop in section.discrete:
        if len(loop) < 4:
            continue
        polygon = Polygon(np.asarray(loop)[:, keep])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.area > 0:
            loops.append(polygon)
    if not loops:
        return None
    return reduce(lambda a, b: a.symmetric_difference(b), loops)


def handle_mask(points_mm: np.ndarray, profile: dict[str, list[float]]) -> np.ndarray:
    """Which points, in the tutorial mug's own millimetres, are handle rather
    than body.

    The body is a surface of revolution about the Z axis and the handle is
    everything outside it on the -Y side. So a point is handle when it lies on
    that side and further from the axis than the body's outer wall at its
    height. ``profile`` is that wall's radius against height, measured by the
    build script and stored in the landmarks file.
    """
    points = np.asarray(points_mm, dtype=float).reshape(-1, 3)
    wall = np.interp(points[:, 2], profile["z_mm"], profile["r_mm"])
    radius = np.hypot(points[:, 0], points[:, 1])
    return (points[:, 1] < 0.0) & (radius > wall + HANDLE_MARGIN_MM)


# ---------------------------------------------------------------------------
# Test pattern
# ---------------------------------------------------------------------------

def pattern_pixels(corner: str, width: int, height: int) -> np.ndarray:
    """The pattern as a display payload: 255 raised, 0 lowered, one row per
    pin row, the same convention the render path hands the encoders."""
    pixels = np.zeros((height, width), dtype=np.uint8)
    pixels[0, :] = 255
    pixels[-1, :] = 255
    pixels[:, 0] = 255
    pixels[:, -1] = 255
    top = PATTERN_INSET if corner in ("tl", "tr") else height - 1 - PATTERN_INSET - (PATTERN_BLOCK - 1)
    left = PATTERN_INSET if corner in ("tl", "bl") else width - 1 - PATTERN_INSET - (PATTERN_BLOCK - 1)
    pixels[top:top + PATTERN_BLOCK, left:left + PATTERN_BLOCK] = 255
    return pixels


@tutorial_bp.route("/tutorial/test-pattern", methods=["POST"])
def tutorial_test_pattern():
    data = request.get_json(silent=True) or {}
    corner = str(data.get("corner", "")).strip().lower()
    if corner not in PATTERN_CORNERS:
        return jsonify({"status": "error", "message": "corner must be one of tl, tr, bl, br"}), 400
    try:
        width = int(data.get("width"))
        height = int(data.get("height"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "width and height must be whole numbers"}), 400
    # A braille cell is 2 pins wide and 4 tall, and both displays take whole
    # cells, so a size that is not whole cells describes no real display.
    if width % 2 or height % 4:
        return jsonify({"status": "error", "message": "width must be whole cells (2 pins) and height whole lines (4 pins)"}), 400
    if not (PATTERN_MIN_SIZE <= width <= PATTERN_MAX_SIZE and PATTERN_MIN_SIZE <= height <= PATTERN_MAX_SIZE):
        return jsonify({"status": "error", "message": f"width and height must be {PATTERN_MIN_SIZE} to {PATTERN_MAX_SIZE}"}), 400

    pixels = pattern_pixels(corner, width, height)
    lines, cols = height // 4, width // 2
    # Packed exactly as the render path packs a frame: lowercase for the
    # Monarch, as _render_response does, and uppercase and padded to the full
    # cell count for the DotPad, as /render/dotpad-hex does.
    monarch_hex = _pixels_to_braille_cells(pixels, lines=lines, cols=cols).hex()
    total_cells = lines * cols
    dotpad_cells = _pixels_to_braille_cells_dotpad(pixels, lines=lines, cols=cols)
    dotpad_hex = dotpad_cells[:total_cells].ljust(total_cells, b"\x00").hex().upper()
    return jsonify(
        {
            "status": "success",
            "corner": corner,
            "width": width,
            "height": height,
            "monarch_cells_hex": monarch_hex,
            "dotpad_graphic_hex": dotpad_hex,
        }
    ), 200


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

@tutorial_bp.route("/tutorial/lessons", methods=["GET"])
def tutorial_lessons():
    display = str(request.args.get("display") or "none").strip().lower()
    if display not in DISPLAYS:
        return jsonify({"status": "error", "message": "display must be monarch, dotpad or none"}), 400
    # Looked up rather than imported at the top, so the blueprint still loads if
    # the lessons module is missing, and checked with find_spec rather than by
    # catching ImportError, so a real error inside that module is not reported
    # as the module being absent.
    if importlib.util.find_spec(f"{__package__}.tutorial_lessons") is None:
        return jsonify({"status": "error", "message": "The tutorial lessons are not installed."}), 503
    from . import tutorial_lessons as lessons_module

    payload = dict(lessons_module.lessons_payload(display))
    payload["landmarks"] = load_landmarks()
    payload["model"] = TUTORIAL_MODEL_STEM
    return jsonify(payload), 200


# ---------------------------------------------------------------------------
# Locate: where the landmarks are on the pins
# ---------------------------------------------------------------------------

_hash_lock = threading.Lock()
_hash_cache: dict[tuple[str, int, int], str] = {}


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _hash_lock:
        cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with _hash_lock:
        _hash_cache[key] = digest
    return digest


def _framing(engine: Any, params: dict[str, Any], grid: tuple[int, int] | None) -> dict[str, Any]:
    """The window a render of these params frames, and the pins it draws into.

    This follows CADComparisonRenderer.render() step for step for the single
    view: the zoom clamp, the scrollbar that is dropped at zoom 0 and in the
    slice graph, the drawable area it leaves, the view's window at this
    orientation, the camera centre sent or the view's own, the one pan step,
    and compute_imposed_zoom_limits. It cannot call render() for this without
    drawing a frame, so tests/test_tutorial_server.py checks the result against
    the framing_bounds a real render returns, for several poses, so the two
    cannot drift apart unnoticed.
    """
    from .cad_comparison_lib import (
        DEFAULT_SCREEN_SIZE,
        apply_pan_step,
        compute_imposed_zoom_limits,
    )

    screen_w, screen_h = grid if grid is not None else DEFAULT_SCREEN_SIZE
    zoom_level = max(0.0, min(10.0, float(params.get("zoom", 0.0))))
    mode = str(params.get("mode", "single")).lower()

    compose_scrollbar = bool(params["compose_scrollbar"]) if "compose_scrollbar" in params else False
    if mode == "slice-graph" or zoom_level == 0.0:
        compose_scrollbar = False
    draw_w, draw_h = (max(1, screen_w - 2), max(1, screen_h - 2)) if compose_scrollbar else (screen_w, screen_h)

    view_name = engine._map_view_name(params.get("view", "Top"))
    view_index = engine._get_view_index(view_name)
    orientation = params.get("orientation")
    view_limits = engine._limits_for_orientation(orientation, view_index, view_name)

    requested = params.get("camera_center")
    if isinstance(requested, (list, tuple)) and len(requested) == 2:
        centre = [float(requested[0]), float(requested[1])]
    else:
        centre = engine._default_camera_center(view_limits)

    zoom_scale = 1.0 / (zoom_level + 1.0)
    min_dim = min(draw_w, draw_h)
    if min_dim > 0 and engine.longest_3d_dim > 0:
        horizontal = engine.longest_3d_dim * draw_w / min_dim
        vertical = engine.longest_3d_dim * draw_h / min_dim
    else:
        horizontal = abs(view_limits[0][1] - view_limits[0][0])
        vertical = abs(view_limits[1][1] - view_limits[1][0])
    centre = apply_pan_step(centre, params.get("move_camera_center", "none"), 0.25 * zoom_scale, horizontal, vertical)
    x_lim, y_lim = compute_imposed_zoom_limits(
        horizontal, vertical, centre[0], centre[1], zoom_level, draw_w, draw_h
    )

    # The slice graph goes over the bottom rows whenever it is composed, which
    # is the slice-graph layout and also the "]" overlay in the single one.
    # render() reads the flag only when compose_scrollbar is present at all.
    graph_drawn = "compose_scrollbar" in params and bool(params.get("compose_slicegraph"))
    model_rows = min(draw_h, screen_h - _SLICE_GRAPH_ROWS) if graph_drawn else draw_h

    return {
        "view_name": view_name,
        "orientation": orientation,
        "limits": ([float(x_lim[0]), float(x_lim[1])], [float(y_lim[0]), float(y_lim[1])]),
        "draw": (draw_w, draw_h),
        "screen": (screen_w, screen_h),
        "model_rows": max(0, model_rows),
        "graph_rows": draw_h - max(0, model_rows),
    }


_extent_lock = threading.Lock()
_extent_cache: OrderedDict[tuple, dict[str, tuple[float, float, float, float] | None]] = OrderedDict()
_EXTENT_CACHE_MAX = 64


def _orientation_key(orientation: Any) -> tuple:
    if not isinstance(orientation, dict):
        return ()
    return tuple(
        tuple(round(float(c), 6) for c in (orientation.get(name) or ()))
        for name in ("forward", "up", "right")
    )


def _landmark_extents(
    engine: Any, model_key: tuple, view_name: str, orientation: Any, depth_percent: float,
    cut_only: bool, shape_index: int, profile: dict[str, list[float]],
) -> dict[str, tuple[float, float, float, float] | None]:
    """Where the handle and the body are drawn, in the renderer's projected
    plane, for this view, orientation, depth and render mode.

    The geometry comes from the same calls the render makes: get_cut_faces,
    which is the Cut mode's section, or the depth peeling that every other
    mode draws the remaining half of. Each vertex is then taken back to the
    mug's own millimetres and sorted into handle or body with handle_mask.
    Only the extents are kept, and only for this model, so the cache is small.
    """
    from src.converter.plane_intersection_utils import (
        depth_peeling_single_depth_with_bbox,
    )
    from src.converter.single_view_stl import (
        _get_view_basis,
        get_cut_faces,
        project_vertices,
    )

    key = (model_key, view_name, _orientation_key(orientation), round(float(depth_percent), 4), cut_only, shape_index)
    with _extent_lock:
        cached = _extent_cache.get(key)
        if cached is not None:
            _extent_cache.move_to_end(key)
            return cached

    shape = engine.shapes[shape_index]
    # render() hands get_single_view 1 - depth / 100; see compute_fit_view.
    cut_depth = 1.0 - float(depth_percent) / 100.0
    if cut_only:
        drawn = get_cut_faces(shape, view_name, cut_depth, engine.bbox, orientation_basis=orientation)
    else:
        normal = _get_view_basis(view_name, orientation_basis=orientation)[2]
        drawn, _origin = depth_peeling_single_depth_with_bbox(copy(shape), normal, depth=cut_depth, bbox=engine.bbox)

    extents: dict[str, tuple[float, float, float, float] | None] = {"handle": None, "body": None}
    if drawn is not None and len(drawn.faces) > 0:
        vertices = np.asarray(drawn.vertices)[np.unique(np.asarray(drawn.faces).reshape(-1))]
        # to_render_space maps v to s*v + c*(1 - s); this is its inverse.
        scale = float(engine.model_scale) or 1.0
        in_mm = (vertices - engine.model_offset * (1.0 - scale)) / scale
        is_handle = handle_mask(in_mm, profile)
        projected = project_vertices(vertices, view_name, orientation_basis=orientation)
        for name, selector in (("handle", is_handle), ("body", ~is_handle)):
            points = projected[selector]
            if len(points):
                extents[name] = (
                    float(points[:, 0].min()), float(points[:, 0].max()),
                    float(points[:, 1].min()), float(points[:, 1].max()),
                )

    with _extent_lock:
        _extent_cache[key] = extents
        _extent_cache.move_to_end(key)
        while len(_extent_cache) > _EXTENT_CACHE_MAX:
            _extent_cache.popitem(last=False)
    return extents


# Keeps a value that is a pixel boundary up to rounding from spilling into the
# pixel beyond it.
_EDGE_EPSILON = 1e-9


def _pixel_box(extent: tuple[float, float, float, float], framing: dict[str, Any]) -> list[int]:
    """[col0, row0, col1, row1], inclusive, of every pin the extent touches.

    The same mapping the renderer draws with and origin_on_display reads back:
    the window's left edge is column 0, its top edge is row 0, and the drawable
    area is divided evenly between them. The box is not clipped to the display,
    so a landmark that is partly or wholly off it keeps its true position.
    """
    (x_lo, x_hi), (y_lo, y_hi) = framing["limits"]
    draw_w, draw_h = framing["draw"]
    x_min, x_max, y_min, y_max = extent
    per_col = draw_w / (x_hi - x_lo)
    per_row = draw_h / (y_hi - y_lo)
    col0 = math.floor((x_min - x_lo) * per_col + _EDGE_EPSILON)
    col1 = math.ceil((x_max - x_lo) * per_col - _EDGE_EPSILON) - 1
    row0 = math.floor((y_hi - y_max) * per_row + _EDGE_EPSILON)
    row1 = math.ceil((y_hi - y_min) * per_row - _EDGE_EPSILON) - 1
    return [col0, row0, max(col0, col1), max(row0, row1)]


def _describe_box(box: list[int], framing: dict[str, Any]) -> dict[str, Any]:
    """Whether a box is on the pins, and which part of the display it is on.

    "left", "right", "top" or "bottom" when the middle of its visible part is
    outside the middle third of the display that way (the larger offset wins),
    "centre" when it is inside it, and "off-..." when none of it is showing.
    Rows under a slice graph do not count as showing.
    """
    col0, row0, col1, row1 = box
    width = framing["draw"][0]
    rows = framing["model_rows"]
    centre = [(col0 + col1) // 2, (row0 + row1) // 2]
    visible = rows > 0 and col1 >= 0 and col0 <= width - 1 and row1 >= 0 and row0 <= rows - 1
    if not visible:
        overhangs = []
        if col1 < 0:
            overhangs.append((-col1, "off-left"))
        if col0 > width - 1:
            overhangs.append((col0 - (width - 1), "off-right"))
        if row1 < 0:
            overhangs.append((-row1, "off-top"))
        if rows <= 0 or row0 > rows - 1:
            overhangs.append((row0 - (rows - 1), "off-bottom"))
        side = max(overhangs)[1] if overhangs else "off-bottom"
        return {"visible": False, "centre": centre, "side": side}

    shown_col = (max(col0, 0) + min(col1, width - 1)) / 2.0
    shown_row = (max(row0, 0) + min(row1, rows - 1)) / 2.0
    dx = (shown_col - (width - 1) / 2.0) / (width / 2.0)
    dy = (shown_row - (rows - 1) / 2.0) / (rows / 2.0)
    if max(abs(dx), abs(dy)) <= 1.0 / 3.0:
        side = "centre"
    elif abs(dx) >= abs(dy):
        side = "left" if dx < 0 else "right"
    else:
        side = "top" if dy < 0 else "bottom"
    return {"visible": True, "centre": centre, "side": side}


@tutorial_bp.route("/tutorial/locate", methods=["POST"])
def tutorial_locate():
    """Where the handle and the body are for the render described by the body.

    Send the exact JSON of the /render request whose picture is on the pins.
    The answer is in the same pin coordinates as cursor_col and cursor_row:
    column 0 at the left, row 0 at the top, across the drawable area, which is
    the whole grid except for the two scrollbar rows and columns when the
    scrollbar is composed. Only the tutorial mug has landmarks.
    """
    hooks = _server()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "send the render request as JSON"}), 400
    params, model_stem, _is_pan, _fingerprint = hooks.prepare_render_params(data)
    if model_stem != TUTORIAL_MODEL_STEM:
        return jsonify({"status": "error", "message": "Only the tutorial mug has landmarks."}), 400

    mode = str(params.get("mode", "single")).lower()
    if mode not in ("single", "slice-graph"):
        # Side by side draws the cut in a narrower panel with its own framing,
        # and no lesson asks for the handle there.
        return jsonify({"status": "error", "message": "Locate works in the single and slice-graph layouts."}), 400

    landmarks = load_landmarks()
    model_path = Path(hooks.model_path_for_stem(model_stem))
    if model_path.stem != TUTORIAL_MODEL_STEM or _file_sha256(model_path) != landmarks.get("stl_sha256"):
        # A model directory seeded from an older build keeps its old copy (the
        # seeding skips files that exist), and boxes measured against the wrong
        # mesh would send someone to the wrong place without any sign of it.
        return jsonify({"status": "error", "message": "The tutorial mug on this server does not match its landmarks."}), 409

    try:
        engine = hooks.renderer_for_stem(model_stem)
        framing = _framing(engine, params, hooks.target_grid(params))
        render_mode = engine._map_render_mode(str(params.get("renderMode", "Outline")))
        shape_index = 0 if str(params.get("shape", "after")).lower() == "before" else 1
        stat = model_path.stat()
        extents = _landmark_extents(
            engine,
            (str(model_path), stat.st_mtime_ns, stat.st_size),
            framing["view_name"],
            framing["orientation"],
            float(params.get("depth", 0)),
            render_mode == "cut",
            shape_index,
            landmarks["body_outer_radius_mm"],
        )
    except Exception as error:  # noqa: BLE001 - any failure is reported, not raised
        # Answered as JSON, as /render answers its own failures, so the lesson
        # can offer Skip rather than stall on a page it cannot parse.
        return jsonify({"status": "error", "message": f"Could not locate the landmarks: {error}"}), 500

    described: dict[str, Any] = {}
    for name in ("handle", "body"):
        extent = extents.get(name)
        if extent is None:
            # The cut does not pass through it, so nothing of it is raised.
            described[name] = {"present": False, "visible": False, "box": None, "centre": None, "side": None}
            continue
        box = _pixel_box(extent, framing)
        described[name] = {"present": True, "box": box, **_describe_box(box, framing)}

    draw_w, draw_h = framing["draw"]
    screen_w, screen_h = framing["screen"]
    return jsonify(
        {
            "status": "success",
            "model": model_stem,
            "grid": {"width": draw_w, "height": draw_h},
            "display": {"width": screen_w, "height": screen_h},
            "graph_rows": framing["graph_rows"],
            "landmarks": described,
        }
    ), 200


# ---------------------------------------------------------------------------
# Prints: the mug and ten plaques
# ---------------------------------------------------------------------------

def _extrude(polygon: Polygon, height: float) -> tuple[np.ndarray, np.ndarray]:
    """A closed prism over one polygon, holes included, as (vertices, faces).

    The caps are triangulated with shapely's constrained Delaunay
    triangulation, which keeps to the polygon's edges and leaves its holes
    open. trimesh's own extrusion needs a triangulation engine this
    environment does not have.
    """
    polygon = orient(polygon, sign=1.0)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add(points: list[tuple[float, float, float]]) -> None:
        start = len(vertices)
        vertices.extend(points)
        faces.append((start, start + 1, start + 2))

    for triangle in shapely.constrained_delaunay_triangles(polygon).geoms:
        a, b, c = [tuple(p) for p in list(triangle.exterior.coords)[:3]]
        # Counter-clockwise from above, so the top cap faces up and the bottom,
        # wound the other way, faces down.
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
            b, c = c, b
        add([(a[0], a[1], height), (b[0], b[1], height), (c[0], c[1], height)])
        add([(a[0], a[1], 0.0), (c[0], c[1], 0.0), (b[0], b[1], 0.0)])

    # orient() leaves the outline counter-clockwise and the holes clockwise, so
    # the right-hand side of every edge is outside the material, which is the
    # way these walls face.
    for ring in [polygon.exterior, *polygon.interiors]:
        coords = list(ring.coords)
        for (ax, ay), (bx, by) in itertools.pairwise(coords):
            add([(ax, ay, 0.0), (bx, by, 0.0), (bx, by, height)])
            add([(ax, ay, 0.0), (bx, by, height), (ax, ay, height)])
    return np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64)


def plaque_mesh(region: Any, thickness: float = PLAQUE_THICKNESS_MM) -> trimesh.Trimesh | None:
    """One plaque: every piece of a section, extruded upward from Z = 0."""
    parts = []
    for piece in region_polygons(region):
        vertices, faces = _extrude(piece, thickness)
        parts.append(trimesh.Trimesh(vertices=vertices, faces=faces, process=True))
    return trimesh.util.concatenate(parts) if parts else None


def _band_at(bands: list[dict[str, Any]], percent: float) -> dict[str, Any] | None:
    for band in bands:
        if band["from"] <= percent <= band["to"]:
            return band
    return None


def _readme(plaques: list[tuple[str, float, float, int, dict[str, Any] | None]], height_mm: float) -> str:
    lines = [
        "Tutorial mug and plaques",
        "",
        "These are the practice mug from the cad-a11y tutorial and ten plaques cut",
        "from it, to 3D print and hold while you learn. All sizes are millimetres.",
        "",
        f"tutorial_mug.stl is the mug itself, {height_mm:.0f} mm tall. Print it standing",
        "on its base. It is the same mug the tutorial shows on the display.",
        "",
        "Each plaque is the mug cut straight across at one height, the way the",
        "display shows it when you cut along Z, 3 mm thick. They are cut at the",
        "middle of each tenth of the mug's height, from the bottom up. Print them",
        "lying flat.",
        "",
        "Which way round: turn a plaque until its handle is on the same side as",
        "on the display. In XYZ mode, cutting along Z (from above) puts the",
        "handle at the bottom edge, so lay the plaque with the handle nearest",
        "you. In Turn mode the side depends on how you turned the mug; the",
        "period key says which way the display is facing. To match the",
        "tutorial's first view, where the handle is on the left, hold the",
        "printed mug upright with its handle to your left.",
        "",
        "Some plaques are two pieces, because the cut passes through the handle",
        "where it is not joined to the mug. Keep the small handle piece with its",
        "ring, on the side nearest you.",
        "",
    ]
    for filename, percent, z_mm, pieces, band in plaques:
        what = band["description"] if band else ""
        piece_note = " Two pieces: the ring and a piece of the handle." if pieces == 2 else (
            f" {pieces} pieces." if pieces > 2 else "")
        lines.append(f"{filename}: {z_mm:.1f} mm up, {percent}% of the height. {what}{piece_note}".rstrip())
    lines.append("")
    return "\n".join(lines)


_prints_lock = threading.Lock()
_prints_zip: bytes | None = None

# A fixed timestamp on every entry, so the archive is the same bytes each time
# it is built and a cache or a checksum downstream does not see a new file.
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def build_prints_zip() -> bytes:
    """The archive, built in memory the first time it is asked for and kept.

    Nothing is written to disk: the mug is read from builtin_models/, every
    file is made in memory, and the archive lives in this process until it
    exits.
    """
    global _prints_zip
    with _prints_lock:
        if _prints_zip is not None:
            return _prints_zip

        mug_path = Path(_server().builtin_dir()) / f"{TUTORIAL_MODEL_STEM}.stl"
        mug_bytes = mug_path.read_bytes()
        mug = load_mesh_as_renderer_does(mug_path)
        landmarks = load_landmarks()
        z_min, z_max = (float(v) for v in landmarks["bbox_mm"]["z"])

        files: list[tuple[str, bytes]] = [(f"{TUTORIAL_MODEL_STEM}.stl", mug_bytes)]
        described = []
        for percent in PLAQUE_PERCENTS:
            z_mm = z_min + (z_max - z_min) * percent / 100.0
            region = section_region(mug, 2, z_mm)
            plaque = plaque_mesh(region)
            if plaque is None:
                continue
            filename = f"plaque_z{percent:02d}.stl"
            files.append((filename, plaque.export(file_type="stl")))
            described.append(
                (filename, percent, z_mm, len(region_polygons(region)), _band_at(landmarks["axes"]["z"], percent))
            )
        files.append(("README.txt", _readme(described, z_max - z_min).encode("utf-8")))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        _prints_zip = buffer.getvalue()
        return _prints_zip


@tutorial_bp.route("/tutorial/prints.zip", methods=["GET"])
def tutorial_prints():
    data = build_prints_zip()
    return send_file(
        io.BytesIO(data),
        mimetype="application/zip",
        as_attachment=True,
        download_name="tutorial_mug_prints.zip",
    )
