"""
CAD Comparison Library

This library provides a simple interface to generate comparison views of CAD models.
It processes STEP files and returns rendered image arrays based on specified parameters.
"""

import numpy as np
import contextlib
import gzip
import hashlib
import re
import threading
import time
import os
from OCC.Core.STEPControl import STEPControl_Reader
import trimesh as tm
from trimesh.exchange.stl import load_stl
from trimesh import Trimesh
from trimesh.repair import stitch, fill_holes, fix_inversion, fix_winding
from copy import copy, deepcopy
import src.converter.plane_intersection_utils as plane_inter_utils
from src.converter.single_view_stl import (
    _get_view_basis,
    _resolve_orientation_basis,
    get_cut_faces,
    get_single_view,
    project_vertices,
)
from src.converter.juxtaposition_view_stl import get_juxtaposition_view
from src.converter.superposition_view_stl import get_superposition_view
from src.converter.side_by_side_view import get_side_view
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union
from shapely import union_all
from shapely.plotting import plot_polygon
from shapely import symmetric_difference, from_wkb, to_wkb
import matplotlib.pyplot as plt
import io, PIL, json
from PIL import Image


def compute_imposed_zoom_limits(horizontal_dist, vertical_dist, center_x, center_y, zoom_level, screen_w, screen_h):
    """Compute the (x, y) axis limits CADComparisonRenderer.render() imposes for a
    given zoom level, centered on (center_x, center_y) and corrected to match the
    target screen's aspect ratio.

    Pulled out as a standalone function (rather than inlined in render()) so it can
    be unit-tested directly instead of via a hand-copied mirror that could drift
    from the real formula.

    Returns ([x_min, x_max], [y_min, y_max]).
    """
    zoom_scale = 1.0 / (zoom_level + 1.0)
    current_aspect_ratio = screen_w / screen_h

    # Use 0.5 * zoom_scale so the initial window exactly spans the model's
    # bounding box (with matplotlib's auto-margin) at zoom_level=0. A factor of
    # 1.0 here causes a 2x zoom-out, leaving the model occupying only ~50% of
    # the display height before aspect-ratio correction, and even less after it.
    x_lim = [center_x - 0.5*zoom_scale*horizontal_dist, center_x + 0.5*zoom_scale*horizontal_dist]
    y_lim = [center_y - 0.5*zoom_scale*vertical_dist, center_y + 0.5*zoom_scale*vertical_dist]

    # A degenerate extent (a perfectly flat model seen edge-on) has no aspect to
    # correct against, and dividing by it would raise.
    if horizontal_dist <= 0 or vertical_dist <= 0:
        return x_lim, y_lim

    if horizontal_dist/vertical_dist < current_aspect_ratio:
        horizontal_scale_factor = current_aspect_ratio * (y_lim[1] - y_lim[0]) / (x_lim[1] - x_lim[0])
        x_lim[0] = center_x - 0.5*horizontal_scale_factor*zoom_scale*horizontal_dist
        x_lim[1] = center_x + 0.5*horizontal_scale_factor*zoom_scale*horizontal_dist
    if horizontal_dist/vertical_dist > current_aspect_ratio:
        vertical_scale_factor = current_aspect_ratio * (y_lim[1] - y_lim[0]) / (x_lim[1] - x_lim[0])
        vertical_scale_factor = 1.0/vertical_scale_factor
        y_lim[0] = center_y - 0.5*vertical_scale_factor*zoom_scale*vertical_dist
        y_lim[1] = center_y + 0.5*vertical_scale_factor*zoom_scale*vertical_dist

    return x_lim, y_lim


def apply_pan_step(center, camera_move, pan_step_scale, horizontal_dist, vertical_dist):
    """Apply one pan step to a (x, y) camera center, returning the new center.

    Pulled out of render() for the same reason as compute_imposed_zoom_limits
    below: so the pan step size (25% of the active viewport) and
    the direction mapping can be verified directly, instead of only
    indirectly through a full render().
    """
    x, y = center
    if camera_move == "left":
        x -= pan_step_scale * horizontal_dist
    elif camera_move == "right":
        x += pan_step_scale * horizontal_dist
    elif camera_move == "up":
        y += pan_step_scale * vertical_dist
    elif camera_move == "down":
        y -= pan_step_scale * vertical_dist
    return [x, y]


def compute_pan_guidance(obj_x_range, obj_y_range, viewport_center, viewport_half_w, viewport_half_h):
    """Whether the object is fully outside the viewport, and if so, which
    way(s) (in "move object" terms) would bring it back.

    Pulled out as a standalone function, like compute_imposed_zoom_limits
    above, so it can be unit-tested directly rather than only indirectly
    through a full render().

    Args:
        obj_x_range, obj_y_range: (min, max) of the object's own projected
            extent on each axis.
        viewport_center: (x, y) of the current camera center.
        viewport_half_w, viewport_half_h: half-width/height of the current
            (zoom-scaled) viewport.

    Returns:
        (out_of_frame: bool, directions: tuple[str, ...]) — the direction(s)
        to move the OBJECT to bring it back into view, or an empty tuple if
        it isn't out of frame. Contains up to one entry per axis (i.e. horizontal
        and vertical). 
    """
    obj_x_min, obj_x_max = obj_x_range
    obj_y_min, obj_y_max = obj_y_range
    center_x, center_y = viewport_center
    viewport_x_min, viewport_x_max = center_x - viewport_half_w, center_x + viewport_half_w
    viewport_y_min, viewport_y_max = center_y - viewport_half_h, center_y + viewport_half_h

    # (direction to move the OBJECT to bring it back, how far out it is)
    horizontal_overhang = None
    if obj_x_max < viewport_x_min:
        horizontal_overhang = ("right", viewport_x_min - obj_x_max)
    elif obj_x_min > viewport_x_max:
        horizontal_overhang = ("left", obj_x_min - viewport_x_max)

    vertical_overhang = None
    if obj_y_max < viewport_y_min:
        vertical_overhang = ("up", viewport_y_min - obj_y_max)
    elif obj_y_min > viewport_y_max:
        vertical_overhang = ("down", obj_y_min - viewport_y_max)

    candidates = [c for c in (horizontal_overhang, vertical_overhang) if c is not None]
    if not candidates:
        return False, ()

    candidates.sort(key=lambda c: c[1])
    return True, tuple(direction for direction, _overhang in candidates)


VIEW_KEYS = ("top", "front", "left", "bottom", "back", "right")


def _is_canonical_orientation(view_key, orientation_basis):
    """Is this basis just the named view spelled out?

    The viewer sends a basis on every request, so without this check every
    render would take the reframing path below and pay to recompute limits that
    have already been precomputed.
    """
    resolved = _resolve_orientation_basis(orientation_basis)
    if resolved is None:
        return True
    return all(np.allclose(a, b) for a, b in zip(resolved, _get_view_basis(view_key)))


def _projected_view_axis_limits(shape, view_key, orientation_basis=None):
    """Fast replacement for reading axis limits off a full
    get_single_view(..., cut_depth=1.0, "filled", ...) render.

    _calculate_view_limits only needs the plotted data's bounding box (with
    matplotlib's default 5% margin), not an actual rendered image. At
    cut_depth=1.0 the depth-peeling cut plane sits exactly at the far edge of
    the bbox, so the cut is a geometric no-op — but get_single_view still
    pays for a full mesh-boolean cut plus a real matplotlib draw pass to
    compute this, which scales with face count and dominates renderer
    construction time for any even moderately complex mesh (seconds for a
    100K+ face STL or a STEP model with curved surfaces). Projecting the raw
    vertices directly and matching matplotlib's own auto-margin formula gives
    the same bounds in O(vertices) instead of O(faces) rendering work.
    """
    if view_key not in VIEW_KEYS:
        raise ValueError(f"Unknown view_key: {view_key}")
    coords = project_vertices(shape.vertices, view_key, orientation_basis=orientation_basis)

    x_min, y_min = coords.min(axis=0)
    x_max, y_max = coords.max(axis=0)
    x_margin = 0.05 * (x_max - x_min)
    y_margin = 0.05 * (y_max - y_min)
    return [[x_min - x_margin, x_max + x_margin], [y_min - y_margin, y_max + y_margin]]


# The tactile grid is ~96x40 and the preview a few hundred pixels across, so
# geometry finer than that is invisible on both while still costing render time
# on every frame: the draw scales with face count, and one interaction pays for
# several renders. A tessellation tolerance can cap this for STEP at load time,
# but an STL arrives already tessellated, so cap it here for every source.
MAX_RENDER_FACES = 40000

# The grid a renderer draws at until something tells it otherwise. A Monarch is
# 48 cells by 10 lines and a braille cell is 2x4 pixels, so this is exactly a
# Monarch, and it is what every caller that does not name a size gets. Named
# here, beside the assignment it describes, so the server's fallbacks cannot
# quietly disagree with what a renderer actually starts at.
#
# Not to be confused with the viewer's DEFAULT_TACTILE_GRID, which is 78x40: that
# is the client's answer to "no display is connected, so pick something between
# the two we support", and the client always names it explicitly.
DEFAULT_SCREEN_SIZE = (96, 40)


class RenderResult:
    """The output of one render: the image, plus the values that describe it.

    This is a return value, not stored state. Which view, orientation, camera
    centre, depth and zoom a window is looking at lives in the client and arrives
    in the request; nothing about an individual window is kept here, or on the
    renderer, which is shared by every window looking at the same model.

    Of these fields, `camera_center`, `object_out_of_frame` and
    `pan_guidance_directions` travel back to the client: a pan resolves to a
    new centre and the window sends it again next time, which is what keeps
    panning inside the window that did it, and the out-of-frame guidance
    is per-window for the same reason the centre is — the renderer is
    shared by every window on a model, so neither can live on it as instance
    state. The remaining fields are what the render settled on for its
    inputs and are read only on the server.
    """

    __slots__ = ("image", "camera_center", "framing_bounds", "view_axis",
                 "cut_depth", "render_mode", "zoom_level", "screen_size",
                 "object_out_of_frame", "pan_guidance_directions")

    def __init__(self, image, *, camera_center, framing_bounds, view_axis,
                 cut_depth, render_mode, zoom_level, screen_size,
                 object_out_of_frame, pan_guidance_directions):
        self.image = image
        # Echoed to the client, which stores it per view + orientation.
        self.camera_center = camera_center
        # Echoed to the client: whether the object is fully outside the
        # viewport this render settled on, and if so which direction(s) (in
        # "move object" terms) would bring it back. Up to two
        # entries -- an object out on both axes needs both a horizontal and
        # a vertical pan to fully recover. Neither a pan nor a rotation is
        # blocked for going out of frame; this only tells the client what
        # guidance to announce/draw.
        self.object_out_of_frame = object_out_of_frame
        self.pan_guidance_directions = pan_guidance_directions
        # The world-space rectangle [[x_min, x_max], [y_min, y_max]] this render
        # framed, once the view, the orientation basis and the zoom were applied:
        # the outer edges of what reached the image. Model-derived, so identical
        # for every window in the same state. Server-only; its one use is naming
        # saved print files so two differently framed prints cannot collide.
        # (Previously `axis_limits`.)
        self.framing_bounds = framing_bounds
        # A coarse label for the view, e.g. "x+". A summary of the orientation
        # the request carried, not the orientation itself: the full
        # forward/up/right basis stays in the request and is what frames the
        # image above.
        self.view_axis = view_axis
        # The single slice depth, 0..1, along the axis the view looks down.
        self.cut_depth = cut_depth
        self.render_mode = render_mode
        self.zoom_level = zoom_level
        self.screen_size = screen_size


def _decimate_for_display(mesh):
    """Reduce face count to roughly what the display can resolve.

    Best effort by design. Decimation needs an optional backend, and a model that
    cannot be simplified should still load and render, just more slowly, so any
    failure returns the mesh untouched rather than breaking the load.
    """
    if len(mesh.faces) <= MAX_RENDER_FACES:
        return mesh
    try:
        simplified = mesh.simplify_quadric_decimation(face_count=MAX_RENDER_FACES)
    except Exception:
        return mesh
    return simplified if len(simplified.faces) > 0 else mesh


class CADComparisonRenderer:
    """
    Renderer for CAD model comparisons.
    """

    # Memo of a pure computation, at class level so an instance built without
    # __init__ still has it. Composition tests do exactly that, to exercise a
    # single stage without paying to load a mesh. Shared safely because the value
    # depends only on the model, the view and the basis, never on who asked.
    _oriented_limits: dict = {}
    _oriented_limits_lock = threading.Lock()


    def __init__(self, before_model_path, after_model_path):
        """
        Initialize the renderer with two STEP model files.
        
        Args:
            before_model_path: Path to the "before" STEP file
            after_model_path: Path to the "after" STEP file
        """
        self.before_model_path = before_model_path
        self.after_model_path = after_model_path
        self.shapes = []
        self.bbox = None
        self.view_limits = None
        self.view_default_camera_center = []
        self.screen_size = list(DEFAULT_SCREEN_SIZE)
        self.view_diff_mats = {}
        self.view_cut_polygons = {}
        self._slice_graphs_ready = False
        self._precompute_in_progress = False
        self._precompute_lock = threading.Lock()
        self._precompute_done = threading.Event()
        self._precompute_done.set()
        # Set by _get_zoom_filtered_slice_profile on every slice-graph render:
        # whether the profile it returned was real (precompute finished for this
        # view) or the flat placeholder precompute hands back while it's still
        # running. The client uses this to refresh automatically, instead
        # of a blind horizontal line sitting there until something else happens
        # to trigger a re-render.
        self.slicegraph_ready = True
        # Bumped from 2: the file is per model now, lives on the data volume
        # rather than inside the image, and carries the cut polygons as well as
        # the difference matrices. An older file is unreadable on all three
        # counts, so it must not be mistaken for a warm start.
        self.cache_version = 3
        self.cache_path = self._precompute_cache_path()
        self._model_signature_value = None
        # The framing in force, so a turn reframes but a redraw does not.
        self._oriented_limits = {}
        self._oriented_limits_lock = threading.Lock()
        
        # Load and normalize shapes
        self._load_models()
        
    def _load_one_mesh(self, model_file):
        """Load a single mesh file and apply the standard repairs."""
        ext = os.path.splitext(model_file)[1].lower()

        if ext == ".stl":
            # STL parsing expects binary bytes for binary STL files.
            with open(model_file, "rb") as fp:
                mesh_dict = load_stl(fp)
                mesh = Trimesh(mesh_dict["vertices"], mesh_dict["faces"], mesh_dict["face_normals"])
        else:
            # STEP/BREP and other mesh-like formats should go through trimesh's generic loader.
            # For STEP, trimesh's default OpenCASCADE tessellation tolerance is far finer
            # than a 96x40 tactile display (or even the ~800px high-fidelity preview) can
            # resolve -- a modestly curved model like a mug can explode into 200K+ faces,
            # taking 10-30+ seconds to load. tol_relative=True scales the tolerance to each
            # model's own size instead of a fixed absolute unit, so it holds up across
            # differently-scaled STEP files; 0.001 (0.1% of the bounding-box diagonal) was
            # visually indistinguishable from the untessellated default in testing while
            # cutting load time by 1-2 orders of magnitude. Ignored for non-STEP formats.
            try:
                loaded = tm.load(model_file, force='mesh', tol_linear=0.001, tol_relative=True)
            except Exception as exc:
                raise ValueError(f"Failed to load model file '{model_file}': {exc}") from exc

            if isinstance(loaded, tm.Scene):
                geometries = [geom for geom in loaded.geometry.values() if isinstance(geom, tm.Trimesh)]
                if not geometries:
                    raise ValueError(f"Model file '{model_file}' produced an empty scene")
                mesh = tm.util.concatenate(geometries)
            elif isinstance(loaded, tm.Trimesh):
                mesh = loaded
            else:
                raise ValueError(f"Unsupported mesh object type for '{model_file}': {type(loaded)}")

        fill_holes(mesh)
        fix_inversion(mesh)
        fix_winding(mesh)
        #stitch(mesh)
        return _decimate_for_display(mesh)

    def _load_models(self):
        """Load mesh files and prepare shapes."""
        same_file = os.path.abspath(self.before_model_path) == os.path.abspath(self.after_model_path)

        before_mesh = self._load_one_mesh(self.before_model_path)
        # "before" and "after" are the same upload for every caller today (e.g. the
        # /ingest workshop flow never does a real comparison), so skip loading and
        # repairing the identical file a second time and just copy the result.
        after_mesh = before_mesh.copy() if same_file else self._load_one_mesh(self.after_model_path)
        shapes = [before_mesh, after_mesh]

        # Normalize both shapes
        shape_before, shape_after = plane_inter_utils.normalize_shapes_diagonal(shapes)
        self.shapes = [shape_before, shape_after]

        # Get common bounds
        xmin, ymin, zmin, xmax, ymax, zmax = plane_inter_utils.get_bbox_from_shapes(
            [shape_before, shape_after]
        )
        self.bbox = [xmin, ymin, zmin, xmax, ymax, zmax]

        # Calculate view limits for all views
        self._calculate_view_limits(same_shapes=same_file)
        #self._compute_slice_graphs()

    def _limits_for_orientation(self, orientation_basis, view_index, view_key):
        """The window to draw, for this view at this orientation.

        Pure: it reads the model and returns limits, and mutates nothing. It used
        to write self.view_limits[view_index] in place and memoize on an instance
        key, which meant two windows looking at one model from different
        orientations fought over the framing and each intermittently rendered
        with the other's.

        The named views keep their precomputed limits. A rotated model projects
        somewhere else entirely, so it gets limits measured along the axes it is
        actually turned to: pitching a chair to look down on it otherwise framed
        a region it no longer occupied and rendered an empty display.

        The memo is kept, but as a cache of a pure result keyed by the basis, so
        it is a saving rather than a piece of state. Two windows asking the same
        question share the answer, which is correct.
        """
        if _is_canonical_orientation(view_key, orientation_basis):
            return self.view_limits[view_index]

        resolved = _resolve_orientation_basis(orientation_basis)
        key = (view_index, tuple(np.round(np.concatenate(resolved), 6)))
        with self._oriented_limits_lock:
            cached = self._oriented_limits.get(key)
        if cached is not None:
            return cached

        limits = _projected_view_axis_limits(self.shapes[0], view_key, orientation_basis)
        for other in self.shapes[1:]:
            other_limits = _projected_view_axis_limits(other, view_key, orientation_basis)
            limits = [
                [min(limits[0][0], other_limits[0][0]), max(limits[0][1], other_limits[0][1])],
                [min(limits[1][0], other_limits[1][0]), max(limits[1][1], other_limits[1][1])],
            ]
        limits = np.array(limits)
        with self._oriented_limits_lock:
            self._oriented_limits[key] = limits
        return limits

    @staticmethod
    def _default_camera_center(limits):
        """The middle of the window, which is where a view starts before any pan."""
        return [float(np.mean(limits[0])), float(np.mean(limits[1]))]

    @staticmethod
    def _object_pan_guidance(view_limits, camera_center, zoom_scale, horizontal_dist, vertical_dist):
        """Whether the object is fully outside the viewport for this render,
        and if so which way(s) would bring it back.

        A return value, not stored state, for the same reason camera_center is
        now one (see its comment above): the renderer is shared by every
        window on a model, so instance attributes here would let one window's
        framing overwrite the guidance another window is about to read.

        Checked unconditionally on every render, not just after a pan: a
        rotation changes the object's projected extent (view_limits) just as
        easily as a pan changes the camera center, so either can be the reason
        the object and the viewport no longer overlap. Neither is blocked —
        this only decides what guidance, if any, to hand back to the caller.
        """
        viewport_half_w = 0.5 * zoom_scale * horizontal_dist
        viewport_half_h = 0.5 * zoom_scale * vertical_dist
        return compute_pan_guidance(
            obj_x_range=tuple(view_limits[0]),
            obj_y_range=tuple(view_limits[1]),
            viewport_center=tuple(camera_center),
            viewport_half_w=viewport_half_w,
            viewport_half_h=viewport_half_h,
        )

    def _draw_pan_guidance_arrow(self, img_array, render_screen_size, direction):
        """Draw a small filled-triangle arrow at the edge of the frame, tip
        pointing the way to pan to bring the object back into view.

        Sighted-preview only — a blind/low-vision user gets this from the
        announcement built from pan_guidance_directions in the server
        response, not from the image. Called once per direction when the
        object is out on both axes, so two arrows (e.g. one at the top edge,
        one at the right edge) rather than a single diagonal one: the
        controls are strictly up/down/left/right, so a diagonal arrow would
        point somewhere no single keypress reaches.
        """
        draw_w, draw_h = render_screen_size[0], render_screen_size[1]
        if draw_w < 4 or draw_h < 4:
            return
        size = max(6, min(draw_w, draw_h) // 12)
        color = [220, 30, 30]
        has_alpha = img_array.shape[2] > 3
        mid_x, mid_y = draw_w // 2, draw_h // 2
        margin = 2

        def paint(y0, y1, x0, x1):
            img_array[y0:y1, x0:x1, 0:3] = color
            if has_alpha:
                img_array[y0:y1, x0:x1, 3] = 255

        if direction in ("right", "left"):
            tip_x = (draw_w - 1 - margin) if direction == "right" else margin
            step = -1 if direction == "right" else 1
            for i in range(size):
                x = tip_x + step * i
                if x < 0 or x >= draw_w:
                    break
                y0 = max(0, mid_y - i)
                y1 = min(draw_h, mid_y + i + 1)
                paint(y0, y1, x, x + 1)
        elif direction in ("up", "down"):
            tip_y = margin if direction == "up" else (draw_h - 1 - margin)
            step = 1 if direction == "up" else -1
            for i in range(size):
                y = tip_y + step * i
                if y < 0 or y >= draw_h:
                    break
                x0 = max(0, mid_x - i)
                x1 = min(draw_w, mid_x + i + 1)
                paint(y, y + 1, x0, x1)

    def _calculate_view_limits(self, same_shapes=False):
        """Calculate axis limits for all views for both shapes."""
        #view_keys = ["top", "front", "side"]
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        rendering_modes = ["outline", "filled", "cut"]
        
        xmin, ymin, zmin, xmax, ymax, zmax = self.bbox
        
        view_limits = [
            [[xmin, xmax], [ymin, ymax]],  # top
            [[xmin, xmax], [ymin, ymax]],  # front
            [[xmin, xmax], [ymin, ymax]],  # side
            [[xmin, xmax], [ymin, ymax]],  # top
            [[xmin, xmax], [ymin, ymax]],  # front
            [[xmin, xmax], [ymin, ymax]],  # side
        ]
        self.view_default_camera_center = [
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
        ]
        
        shape_before, shape_after = self.shapes
        
        for i, view_key in enumerate(view_keys):
            ax_limits_before = _projected_view_axis_limits(shape_before, view_key)
            if same_shapes:
                # Identical shapes always produce identical limits, so the min/max
                # below against a second render would be a no-op; skip it.
                ax_limits_after = ax_limits_before
            else:
                ax_limits_after = _projected_view_axis_limits(shape_after, view_key)
            view_limits[i][0][0] = min(ax_limits_before[0][0], ax_limits_after[0][0])
            view_limits[i][0][1] = max(ax_limits_before[0][1], ax_limits_after[0][1])
            view_limits[i][1][0] = min(ax_limits_before[1][0], ax_limits_after[1][0])
            view_limits[i][1][1] = max(ax_limits_before[1][1], ax_limits_after[1][1])
            self.view_default_camera_center[i][0] = (view_limits[i][0][0] + view_limits[i][0][1])/2.0
            self.view_default_camera_center[i][1] = (view_limits[i][1][0] + view_limits[i][1][1])/2.0
        
        self.view_limits = np.array(view_limits)
        self.view_default_camera_center = np.array(self.view_default_camera_center)

    # Background precompute is CPU-bound and runs in a plain thread, so without
    # yielding here it can hold the GIL long enough to starve Flask's other
    # request-handling threads (threaded=True) for the whole job. Sleeping
    # briefly and periodically gives those threads regular chances to run. The
    # intervals are tuned to yield often enough to stay responsive while keeping
    # the added wall-clock small (about ten yields per view in each loop).
    _PRECOMPUTE_YIELD_EVERY = 10
    _PRECOMPUTE_YIELD_SECONDS = 0.01
    _PRECOMPUTE_DIFF_YIELD_EVERY = 500

    def _compute_slice_graphs(self):

        shape = copy(self.shapes[0])
        cut_depth = 0.0
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        for view_key in view_keys:
            cut_percent = 0
            cut_faces_list = []
            while cut_percent <= 100:
                #shape = deepcopy(self.shapes[0])
                cut_depth = cut_percent/100.0
                cut_faces = get_cut_faces(shape, view_key, cut_depth, self.bbox)
                coords = shape.vertices[:,[0,1]]
                if view_key == "top":
                    coords = cut_faces.vertices[:,[0,1]]
                if view_key == "front":
                    coords = cut_faces.vertices[:,[0,2]]
                if view_key == "left":
                    coords = cut_faces.vertices[:,[1,2]]
                if view_key == "bottom":
                    coords = cut_faces.vertices[:,[0,1]]
                    coords[:,0] *= -1
                    coords[:,1] *= -1
                if view_key == "back":
                    coords = cut_faces.vertices[:,[0,2]]
                    coords[:,0] *= -1
                if view_key == "right":
                    coords = cut_faces.vertices[:,[1,2]]
                    coords[:,0] *= -1
                triangles = [
                    Polygon(coords[face])
                    for face in cut_faces.faces
                ]
                #print([t.area for t in triangles])
                merged = unary_union(triangles)
                cut_faces_list.append(merged)
                cut_percent += 1
                if cut_percent % self._PRECOMPUTE_YIELD_EVERY == 0:
                    time.sleep(self._PRECOMPUTE_YIELD_SECONDS)
                #plot_polygon(merged)
                #plt.axis("equal")
                #plt.savefig("cut_depth_"+view_key+"_"+str(cut_percent)+".png")
                #plt.close()

            # pairwise diff
            diff_mat = np.zeros([101, 101])
            pair_count = 0
            for i in range(len(cut_faces_list)):
                for j in range(i+1, len(cut_faces_list)):
                    diff = symmetric_difference(cut_faces_list[i], cut_faces_list[j]).area
                    diff_mat[i][j] = diff
                    diff_mat[j][i] = diff
                    pair_count += 1
                    if pair_count % self._PRECOMPUTE_DIFF_YIELD_EVERY == 0:
                        time.sleep(self._PRECOMPUTE_YIELD_SECONDS)
            max_diff = np.max(diff_mat)
            if max_diff > 0:
                diff_mat /= max_diff
            self.view_diff_mats[view_key] = diff_mat
            self.view_cut_polygons[view_key] = cut_faces_list

    def start_background_slice_precompute(self):
        """Make the slice data available, computing it only if it is not on disk.

        A previous run's result is loaded in the caller's thread rather than a
        worker: it is a file read, and doing it here means a warm model is ready
        the moment this returns instead of some milliseconds later.
        """
        with self._precompute_lock:
            if self._slice_graphs_ready or self._precompute_in_progress:
                return False
            self._precompute_in_progress = True
            self._precompute_done.clear()

        if self.load_precompute_cache():
            with self._precompute_lock:
                self._slice_graphs_ready = True
                self._precompute_in_progress = False
                self._precompute_done.set()
            return False

        worker = threading.Thread(
            target=self._finish_slice_graph_precompute,
            name="cad-slice-precompute",
            daemon=True,
        )
        worker.start()
        return True
    
    def _finish_slice_graph_precompute(self):
        try:
            self._compute_slice_graphs()
            self._save_precompute_cache(self._model_signature_value or self._model_signature())
            self._slice_graphs_ready = True
        except Exception as error:
            # A daemon thread's uncaught exception is otherwise invisible: it
            # neither surfaces to a caller nor appears in quiet-mode's redirected
            # stdout/stderr, so slicegraph_ready would stay False forever with no
            # trace of why. sys.__stderr__ bypasses that redirect deliberately.
            import sys, traceback
            print(f"Slice-graph precompute failed: {error!r}", file=sys.__stderr__, flush=True)
            traceback.print_exc(file=sys.__stderr__)
        finally:
            with self._precompute_lock:
                self._precompute_in_progress = False
                self._precompute_done.set()

    def _precompute_cache_path(self):
        """Where this model's precomputed slice data lives.

        One file per model, named from the model and a hash of its signature, so
        a changed file simply misses rather than reading somebody else's data.
        There used to be a single shared filename with no model in it, so every
        renderer overwrote the same file and the last one to finish won.

        Under data/ because that is the mounted volume. The old location was
        inside the image, so anything written there vanished on the next build,
        which would have made this cache worthless even once it was read.
        """
        signature = json.dumps(self._model_signature(), sort_keys=True)
        digest = hashlib.sha256(signature.encode()).hexdigest()[:12]
        stem = os.path.splitext(os.path.basename(self.after_model_path))[0]
        safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:80]
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "renders", "precompute", f"{safe_stem}-{digest}.json.gz",
        )

    def load_precompute_cache(self):
        """Restore a previous run's slice data, or return False.

        Gzipped JSON: the difference matrices compress about thirty to one, so a
        model costs roughly 16 KB on disk instead of half a megabyte. Polygons go
        as WKB rather than pickle, so reading a cache file cannot execute
        anything.

        Anything unreadable, stale or the wrong shape is treated as a miss and
        recomputed. A cache that lies is worse than no cache.
        """
        try:
            with gzip.open(self.cache_path, "rt", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, ValueError):
            return False

        if payload.get("cache_version") != self.cache_version:
            return False
        if payload.get("model_signature") != self._model_signature():
            return False

        try:
            diff_mats = {
                key: np.asarray(value, dtype=float)
                for key, value in payload["slice_diff_mats"].items()
            }
            cut_polygons = {
                key: [from_wkb(bytes.fromhex(blob)) for blob in blobs]
                for key, blobs in payload["slice_cut_polygons"].items()
            }
        except (KeyError, TypeError, ValueError):
            return False

        if not diff_mats or set(diff_mats) != set(cut_polygons):
            return False

        self.view_diff_mats = diff_mats
        self.view_cut_polygons = cut_polygons
        return True

    def _save_precompute_cache(self, signature):
        """Persist what the precompute produced, so a restart does not redo it.

        Both halves are needed: the difference matrices alone leave the slice
        graph on its flat fallback, because the zoom-filtered profile is computed
        from the cut polygons.

        Best effort. A model that cannot be cached still works, just slowly, so a
        read-only or full disk must not break rendering.
        """
        # The model may have been deleted (its renderer forgotten) while this
        # precompute was still running. Writing now would leave an orphan cache
        # file for a model that no longer exists.
        if getattr(self, "_discarded", False):
            return

        cache_payload = {
            "cache_version": self.cache_version,
            "model_signature": signature,
            "slice_diff_mats": {
                key: np.asarray(value, dtype=float).tolist()
                for key, value in self.view_diff_mats.items()
            },
            "slice_cut_polygons": {
                key: [to_wkb(polygon).hex() for polygon in polygons]
                for key, polygons in self.view_cut_polygons.items()
            },
        }

        temporary = f"{self.cache_path}.{os.getpid()}.tmp"
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with gzip.open(temporary, "wt", encoding="utf-8") as fp:
                json.dump(cache_payload, fp)
            # Renamed into place so a reader never sees a half-written file.
            os.replace(temporary, self.cache_path)
        except Exception:
            # Deliberately broad. This is a speed-up, and every way it can fail
            # (read-only mount, full disk, a path the filesystem rejects) must
            # cost the next start some seconds rather than cost this render.
            with contextlib.suppress(Exception):
                os.unlink(temporary)

    def _model_signature(self):
        """Build a lightweight signature that changes when source STL files change."""
        signature = {
            "before": self._file_signature(self.before_model_path),
            "after": self._file_signature(self.after_model_path),
        }
        return signature

    def _file_signature(self, file_path):
        try:
            stat_info = os.stat(file_path)
            return {
                "path": os.path.abspath(file_path),
                "size": int(stat_info.st_size),
                "mtime_ns": int(stat_info.st_mtime_ns),
            }
        except OSError:
            return {
                "path": os.path.abspath(file_path),
                "size": -1,
                "mtime_ns": -1,
            }

    def _get_zoom_filtered_slice_profile(self, view_key, anchor_depth_percent, zoom_ax_limits):
        """Compute a slice-graph profile using only geometry inside current zoom limits."""
        # Slice-graph mode is the only consumer of the precomputed data, so it is
        # kicked off here, lazily, on first actual use rather than for every model.
        # This call is a no-op once precompute has started or finished.
        self.start_background_slice_precompute()

        cut_polygons = self.view_cut_polygons.get(view_key)
        if cut_polygons is None or len(cut_polygons) == 0:
            # Precompute for this view hasn't finished yet (view_diff_mats and
            # view_cut_polygons for a view are always populated together, so this
            # is effectively "nothing ready"); return a flat profile so the graph
            # shows something sane instead of crashing while precompute runs.
            # slicegraph_ready flags this render's profile as the placeholder,
            # not a real one, so the client knows to refresh once precompute
            # actually finishes instead of leaving a flat line on screen.
            self.slicegraph_ready = False
            diff_row = self.view_diff_mats.get(view_key)
            if diff_row is None:
                return np.zeros(101, dtype=float)
            return diff_row[anchor_depth_percent]

        self.slicegraph_ready = True

        x0 = min(float(zoom_ax_limits[0][0]), float(zoom_ax_limits[0][1]))
        x1 = max(float(zoom_ax_limits[0][0]), float(zoom_ax_limits[0][1]))
        y0 = min(float(zoom_ax_limits[1][0]), float(zoom_ax_limits[1][1]))
        y1 = max(float(zoom_ax_limits[1][0]), float(zoom_ax_limits[1][1]))
        zoom_window = box(x0, y0, x1, y1)

        anchor_index = max(0, min(100, int(anchor_depth_percent)))
        anchor_poly = cut_polygons[anchor_index].intersection(zoom_window)

        profile = np.zeros(101, dtype=float)
        for depth_index, depth_poly in enumerate(cut_polygons):
            clipped_poly = depth_poly.intersection(zoom_window)
            profile[depth_index] = symmetric_difference(anchor_poly, clipped_poly).area

        max_profile = np.max(profile)
        if max_profile > 0:
            profile /= max_profile
        return profile

    def compute_fit_view(self, params, *, screen_size=None):
        """Compute a zoom and camera center that fits the current slice on the tactile display."""
        view_name = self._map_view_name(params.get("view", "Top"))
        depth_percent = float(params.get("depth", 0))
        shape_choice = str(params.get("shape", "after")).lower()
        shape_index = 0 if shape_choice == "before" else 1
        view_index = self._get_view_index(view_name)

        # Match render(): it passes 1.0 - depth_percent / 100.0 into get_single_view().
        cut_depth = 1.0 - (depth_percent / 100.0)

        # This is the existing slice helper. It returns the mesh that lies on the cut plane.
        slice_faces = get_cut_faces(
            self.shapes[shape_index],
            view_name,
            cut_depth,
            self.bbox,
        )

        if slice_faces is None or len(slice_faces.faces) == 0:
            # Fallback: do not invent a weird camera if there is no slice to fit.
            return {
                "zoom": float(params.get("zoom", 0.0)),
                "camera_center": self.view_default_camera_center[view_index].tolist(),
            }

        used_vertex_ids = np.unique(slice_faces.faces.reshape(-1))
        slice_vertices = slice_faces.vertices[used_vertex_ids]
        coords = project_vertices(slice_vertices, view_name)
        min_x = float(np.min(coords[:,0]))
        max_x = float(np.max(coords[:,0]))
        min_y = float(np.min(coords[:,1]))
        max_y = float(np.max(coords[:,1]))

        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0

        slice_width = max_x - min_x
        slice_height = max_y - min_y

        if not np.isfinite(slice_width) or not np.isfinite(slice_height) or slice_width <= 0 or slice_height <= 0:
            # Fallback: if empty slice
            return {
                "zoom": float(params.get("zoom", 0.0)),
                "camera_center": self.view_default_camera_center[view_index].tolist(),
            }
        slice_aspect = slice_width / slice_height
        
        screen_size = list(screen_size) if screen_size else list(self.screen_size)
        render_screen_size = [screen_size[0], screen_size[1]]
        if bool(params.get("compose_scrollbar", False)):
            render_screen_size = [
                max(1, screen_size[0] - 2),
                max(1, screen_size[1] - 2),
            ]
        device_aspect = render_screen_size[0] / render_screen_size[1]

        margin = 1.0

        if slice_aspect > device_aspect:
            fitted_width = slice_width * margin
            fitted_height = fitted_width / device_aspect
        else:
            fitted_height = slice_height * margin
            fitted_width = fitted_height * device_aspect

        horizontal_dist = self.view_limits[view_index][0][1] - self.view_limits[view_index][0][0]
        vertical_dist = self.view_limits[view_index][1][1] - self.view_limits[view_index][1][0]

        screen_w_for_ratio = render_screen_size[0]
        if str(params.get("mode", "single")).lower() == "side-by-side":
            screen_w_for_ratio = 0.5 * render_screen_size[0]

        base_x_lim, base_y_lim = compute_imposed_zoom_limits(
            horizontal_dist,
            vertical_dist,
            self.view_default_camera_center[view_index][0],
            self.view_default_camera_center[view_index][1],
            0.0,
            screen_w_for_ratio,
            render_screen_size[1],
        )

        base_width = base_x_lim[1] - base_x_lim[0]
        base_height = base_y_lim[1] - base_y_lim[0]

        width_scale = fitted_width / base_width
        height_scale = fitted_height / base_height
        zoom_scale = max(width_scale, height_scale)
        zoom = (1.0 / zoom_scale) - 1.0

        return {
            "zoom": zoom,
            "camera_center": [center_x, center_y],
            "debug_fit": {
                "slice_aspect": slice_aspect,
                "device_aspect": device_aspect,
                "fit_limited_by": "width" if slice_aspect > device_aspect else "height",
                "slice_width": slice_width,
                "slice_height": slice_height,
                "fitted_width": fitted_width,
                "fitted_height": fitted_height,
                "zoom_scale": zoom_scale,
            },
        }
        

    
    def _map_view_name(self, view_name):
        """Map view name from JSON format to internal format."""
        view_mapping = {
            "top": "top",
            "front": "front",
            "left": "left",
            "right": "right",
            "back": "back",
            "bottom": "bottom",
        }
        view_mapping = {
            "top": "z+",
            "front": "y-",
            "left": "x-",
            "right": "x+",
            "back": "y+",
            "bottom": "z-",
        }
        view_mapping = {
            "z+": "top",
            "y-": "front",
            "x-": "left",
            "x+": "right",
            "y+": "back",
            "z-": "bottom",
        }
        return view_mapping.get(view_name.lower(), "top")
    
    def _map_render_mode(self, render_mode):
        """Map render mode from JSON format to internal format.

        The mode the interface calls Cut was named "slice" internally, which read
        as the unrelated slice depth and slice-graph features (#66). The internal
        name is "cut" throughout now. Both older wire names are still accepted, so
        a stored request or an external caller does not break.
        """
        mode_mapping = {
            "outline": "outline",
            "x-ray": "x-ray",
            "filled": "filled",
            "shaded": "filled",
            "slice": "cut",
            "cut": "cut",
        }
        return mode_mapping.get(render_mode.lower(), "outline")
    
    def _get_view_index(self, view_key):
        """Get the index for view limits array."""
        #view_keys = ["top", "front", "side"]
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        return view_keys.index(view_key) if view_key in view_keys else 0

    def _linear_interpolation(self, a, b, param):
        return (a+ param*(b-a))

    def _draw_braille_cell(self, img_array, x, y, dots):
        """Draw a 2x4 braille cell (single-pixel dots) in black onto an RGBA image."""
        dot_positions = {
            1: (0, 0), 2: (0, 1), 3: (0, 2), 7: (0, 3),
            4: (1, 0), 5: (1, 1), 6: (1, 2), 8: (1, 3),
        }
        h, w = img_array.shape[0], img_array.shape[1]
        for dot in dots:
            if dot not in dot_positions:
                continue
            dx, dy = dot_positions[dot]
            px = x + dx
            py = y + dy
            if 0 <= px < w and 0 <= py < h:
                img_array[py, px, 0] = 0
                img_array[py, px, 1] = 0
                img_array[py, px, 2] = 0
                if img_array.shape[2] > 3:
                    img_array[py, px, 3] = 255

    def _draw_braille_text(self, img_array, text, x, y):
        """Draw a short braille string using 2x4 cells with 1px spacing."""
        # Grade-1 letter mappings plus simple symbol approximations for axis tokens.
        char_to_dots = {
            "x": [1, 3, 4, 6],
            "y": [1, 3, 4, 5, 6],
            "z": [1, 3, 5, 6],
            "+": [3, 4, 6],
            "-": [3, 6],
            " ": [],
        }
        cursor_x = x
        cell_advance = 3  # 2px cell width + 1px spacing
        for ch in text.lower():
            dots = char_to_dots.get(ch, [])
            if len(dots) > 0:
                self._draw_braille_cell(img_array, cursor_x, y, dots)
            cursor_x += cell_advance

    def _overlay_side_by_side_view_labels(self, img_array, left_axis, right_axis):
        """Overlay compact braille axis markers at the top of each side-by-side panel."""
        if img_array is None or len(img_array.shape) != 3:
            return
        h, w = img_array.shape[0], img_array.shape[1]
        if h < 6 or w < 12:
            return

        legend_width = int(w / 3)
        y = 1
        left_x = 1
        right_x = legend_width + 1
        self._draw_braille_text(img_array, left_axis, left_x, y)
        self._draw_braille_text(img_array, right_axis, right_x, y)

    def _overlay_view_info_box(self, img_array, axis_text):
        """Overlay a compact 7x5 top-left info box with axis text (e.g., x+)."""
        if img_array is None or len(img_array.shape) != 3:
            return
        h, w = img_array.shape[0], img_array.shape[1]
        if h < 5 or w < 7:
            return

        axis_text = (axis_text or "x+").lower()[:2]
        char_to_dots = {
            "x": [1, 3, 4, 6],
            "y": [1, 3, 4, 5, 6],
            "z": [1, 3, 5, 6],
            "+": [3, 4, 6],
            "-": [3, 6],
        }

        # Fixed size requested by user.
        box_x0 = 0
        box_y0 = 0
        box_w = min(w, 7)
        box_h = min(h, 5)
        box_x1 = box_x0 + box_w - 1
        box_y1 = box_y0 + box_h - 1

        # White background only (no outline).
        img_array[box_y0:box_y1 + 1, box_x0:box_x1 + 1, 0:3] = 255
        if img_array.shape[2] > 3:
            img_array[box_y0:box_y1 + 1, box_x0:box_x1 + 1, 3] = 255

        # Draw exactly two 2x4 glyphs with one blank column between them.
        # Layout inside 7x5 box: [margin][2px][gap][2px][margin].
        if len(axis_text) >= 1 and axis_text[0] in char_to_dots:
            self._draw_braille_cell(img_array, box_x0 + 1, box_y0 + 1, char_to_dots[axis_text[0]])
        if len(axis_text) >= 2 and axis_text[1] in char_to_dots:
            self._draw_braille_cell(img_array, box_x0 + 4, box_y0 + 1, char_to_dots[axis_text[1]])
    
    def render(self, params, *, screen_size=None):
        """
        Render a view of the CAD model comparison based on provided parameters.
        
        Args:
            params: Dictionary containing:
                - view: "Top", "Front", or "Side" (case-insensitive)
                - zoom: Float in [0, 3], where 0 is no zoom and larger values zoom in
                - depth: Integer 0-100 representing depth percentage
                - renderMode: "Outline", "Filled"/"Shaded", or "Slice" (case-insensitive)
                - shape: (optional) "before" or "after", defaults to "after"
                - mode: (optional) "single", "superposition", or "juxtaposition", defaults to "single"
                - superpositionMode: (optional) "outline", "intersection", "difference before", "difference after"
        
        Returns:
            RenderResult: the image, plus the camera centre and framing this
            request resolved to, so the caller can send them back next time.
        
        Example:
            params = {
                "view": "Front",
                "depth": 30,
                "renderMode": "Shaded"
            }
            result = renderer.render(params)
            img_array = result.image
        """
        # Extract and map parameters
        view_name = self._map_view_name(params.get("view", "Top"))
        depth_percent = params.get("depth", 0)
        render_mode = self._map_render_mode(params.get("renderMode", "Outline"))
        shape_choice = params.get("shape", "after").lower()
        comparison_mode = params.get("mode", "single").lower()
        superposition_mode = params.get("superpositionMode", "outline").lower()

        zoom_level = float(params.get("zoom", 0.0))
        zoom_level = max(0.0, min(10.0, zoom_level))

        if "compose_scrollbar" in params.keys():
            compose_scrollbar = params["compose_scrollbar"]
        else:
            compose_scrollbar = False

        # Slice-graph mode should never show scrollbars.
        if comparison_mode == "slice-graph":
            compose_scrollbar = False

        # At zoom_level 0 the whole model extent is already visible, so a
        # scrollbar would have nothing meaningful to show (#150).
        if zoom_level == 0.0:
            compose_scrollbar = False

        # Define a per-view viewing window. When scrollbars are enabled we reserve
        # the last row/column for bars and the second-to-last row/column as spacer.
        screen_size = list(screen_size) if screen_size else list(self.screen_size)
        render_screen_size = [screen_size[0], screen_size[1]]
        if compose_scrollbar:
            render_screen_size = [max(1, screen_size[0] - 2), max(1, screen_size[1] - 2)]

        # Cursor coordinates are display-space row/column values from the frontend.
        # They are relative to the current drawable render area, not CAD x/y/z.
        compose_cursor = bool(params.get("compose_cursor", False))
        default_cursor_col = render_screen_size[0] // 2
        default_cursor_row = render_screen_size[1] // 2
        cursor_col = int(params.get("cursor_col", default_cursor_col))
        cursor_row = int(params.get("cursor_row", default_cursor_row))
        cursor_col = max(0, min(render_screen_size[0] - 1, cursor_col))
        cursor_row = max(0, min(render_screen_size[1] - 1, cursor_row))

        cursor_state = str(params.get("cursor_state", "none")).lower()


        view_legend = params.get("view", "top")
        view_cut = "x+"
        if comparison_mode == "side-by-side":
            # In side-by-side mode, keep the current/selected axis on the RIGHT panel.
            # The left legend panel is chosen as a consistent companion view.
            view_cut = params.get("view", "x+")
            legend_from_cut = {
                "x+": "z+",
                "y+": "x+",
                "z+": "y+",
                "x-": "z-",
                "y-": "x-",
                "z-": "y-",
            }
            view_legend = legend_from_cut.get(view_cut, "x+")
        else:
            if view_legend == "x+":
                view_cut = "y+"
            if view_legend == "y+":
                view_cut = "z+"
            if view_legend == "x-":
                view_cut = "y-"
            if view_legend == "y-":
                view_cut = "z-"
            if view_legend == "z-":
                view_cut = "x-"
        view_name_legend = self._map_view_name(view_legend)
        view_name_cut = self._map_view_name(view_cut)
        
        # Convert depth from 0-100 to 0.0-1.0 ratio
        cut_depth = depth_percent / 100.0
        
        # Select shape (0 = before, 1 = after)
        shape_index = 0 if shape_choice == "before" else 1
        
        # Get view index for limits
        view_index = self._get_view_index(view_name)
        if comparison_mode == "side-by-side":
            view_index = self._get_view_index(view_name_cut)

        # Linear zoom mapping: 0 -> full window, 1 -> half, 2 -> one-third, etc.
        zoom_scale = 1.0 / (zoom_level + 1.0)
        camera_move = params.get("move_camera_center", "none")

        # The window to draw for this view at this orientation. A rotated model
        # projects somewhere else entirely, so it is measured along the axes it
        # is actually turned to.
        view_limits = self._limits_for_orientation(
            params.get("orientation"), view_index, view_name
        )

        # Where this request is looking, as a local. The caller owns it: it is
        # handed back in the result and sent again next time. It used to live on
        # this instance, which is shared by every window on this model, so one
        # window's pan moved another's view and the pans accumulated.
        #
        # No camera centre means "wherever this view starts". The caller keys its
        # stored centre by view *and* orientation, so turning the model gives it
        # nothing to send and the framing resets to the middle of the new extent,
        # which is the only sensible place once the model has turned underneath.
        requested_center = params.get("camera_center")
        if isinstance(requested_center, (list, tuple)) and len(requested_center) == 2:
            camera_center = [float(requested_center[0]), float(requested_center[1])]
        else:
            camera_center = self._default_camera_center(view_limits)

        horizontal_dist = np.abs(view_limits[0][1] - view_limits[0][0])
        vertical_dist = np.abs(view_limits[1][1] - view_limits[1][0])
        # arrow-key stepping. 0.25, not 0.5: a pan moves by 25% of the active
        # (zoom-scaled) viewport, so 75% of what was visible before is still
        # visible after.
        pan_step_scale = 0.25 * zoom_scale
        camera_center = apply_pan_step(camera_center, camera_move, pan_step_scale, horizontal_dist, vertical_dist)

        object_out_of_frame, pan_guidance_directions = self._object_pan_guidance(
            view_limits, camera_center, zoom_scale, horizontal_dist, vertical_dist
        )

        # Compute scrollbar dimensions after final zoom/aspect correction so
        # scrollbar thumb size/position track zoom changes continuously.
        x_scroll_min = 0.0
        x_scroll_max = 1.0
        y_scroll_min = 0.0
        y_scroll_max = 1.0

        # This needs to account for the aspect ratio of the monarch
        screen_w_for_ratio = render_screen_size[0]
        if comparison_mode == "side-by-side":
            screen_w_for_ratio = 0.5*render_screen_size[0]
        imposed_zoom_ax_limits = list(compute_imposed_zoom_limits(
            horizontal_dist,
            vertical_dist,
            camera_center[0],
            camera_center[1],
            zoom_level,
            screen_w_for_ratio,
            render_screen_size[1],
        ))

        x_min = view_limits[1][0]
        x_max = view_limits[1][1]
        x_range = x_max - x_min
        if x_range != 0:
            x_zoom_min = imposed_zoom_ax_limits[1][0]
            x_zoom_max = imposed_zoom_ax_limits[1][1]
            x_scroll_max = 1.0 - (x_zoom_min - x_min) / x_range
            x_scroll_min = 1.0 - (x_zoom_max - x_min) / x_range

        y_min = view_limits[0][0]
        y_max = view_limits[0][1]
        y_range = y_max - y_min
        if y_range != 0:
            y_zoom_min = imposed_zoom_ax_limits[0][0]
            y_zoom_max = imposed_zoom_ax_limits[0][1]
            y_scroll_min = (y_zoom_min - y_min) / y_range
            y_scroll_max = (y_zoom_max - y_min) / y_range

        x_scroll_min = max(0.0, min(1.0, x_scroll_min))
        x_scroll_max = max(0.0, min(1.0, x_scroll_max))
        y_scroll_min = max(0.0, min(1.0, y_scroll_min))
        y_scroll_max = max(0.0, min(1.0, y_scroll_max))
        # Render based on comparison mode
        if comparison_mode == "superposition":
            superposition_keys = ["outline", "intersection", "difference before", "difference after"]
            superposition_key = superposition_mode if superposition_mode in superposition_keys else "outline"
            
            img_array = get_superposition_view(
                self.shapes,
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=view_limits,
                superposition_key=superposition_key,
                screen_size=render_screen_size
            )
        elif comparison_mode == "juxtaposition":
            img_array, _ = get_juxtaposition_view(
                self.shapes,
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=[],
                superposition_key=superposition_mode,
                screen_size=render_screen_size
            )
        if comparison_mode == "side-by-side":
            # Calculate aspect-ratio-adjusted limits for the legend view
            # Get the legend view index and base limits
            legend_view_index = self._get_view_index(view_name_legend)
            legend_limits = self.view_limits[legend_view_index].copy()
            
            # Calculate dimensions for legend view
            legend_horizontal_dist = np.abs(legend_limits[0][1] - legend_limits[0][0])
            legend_vertical_dist = np.abs(legend_limits[1][1] - legend_limits[1][0])
            legend_center_x = (legend_limits[0][0] + legend_limits[0][1]) / 2.0
            legend_center_y = (legend_limits[1][0] + legend_limits[1][1]) / 2.0
            
            # Apply same aspect ratio correction as used for cut view (0.5 aspect ratio for side-by-side)
            side_by_side_aspect_ratio = 0.5 * render_screen_size[0] / render_screen_size[1]
            
            # Adjust legend limits to match the aspect ratio of half-screen
            if legend_horizontal_dist / legend_vertical_dist < side_by_side_aspect_ratio:
                # Need to expand horizontal
                horizontal_scale_factor = side_by_side_aspect_ratio * legend_vertical_dist / legend_horizontal_dist
                adjusted_horizontal_dist = horizontal_scale_factor * legend_horizontal_dist
                imposed_legend_ax_limits = [
                    [legend_center_x - adjusted_horizontal_dist / 2.0,
                     legend_center_x + adjusted_horizontal_dist / 2.0],
                    legend_limits[1]
                ]
            else:
                # Need to expand vertical
                vertical_scale_factor = (legend_horizontal_dist / legend_vertical_dist) / side_by_side_aspect_ratio
                adjusted_vertical_dist = vertical_scale_factor * legend_vertical_dist
                imposed_legend_ax_limits = [
                    legend_limits[0],
                    [legend_center_y - adjusted_vertical_dist / 2.0,
                     legend_center_y + adjusted_vertical_dist / 2.0]
                ]
            
            img_array, _ = get_side_view(
                self.shapes[shape_index],
                self.bbox,
                1.0 - cut_depth,
                view_name_legend,
                view_name_cut,
                render_mode,
                imposed_ax_limits_legend=imposed_legend_ax_limits,
                #imposed_ax_limits_cut=self.view_limits[self._get_view_index(view_name_cut)]
                imposed_ax_limits_cut=imposed_zoom_ax_limits,
                screen_size=render_screen_size
            )
        else:  # single mode
            #print(view_limits)
            #print(view_limits[1][0], view_limits[1][1], imposed_zoom_ax_limits[1][0])
            #print(
            #        [self._linear_interpolation(view_limits[1][0], view_limits[1][1], imposed_zoom_ax_limits[1][0]),
            #         self._linear_interpolation(view_limits[1][0], view_limits[1][1], imposed_zoom_ax_limits[1][1])]
            #)
            # The orientation the viewer is holding the model at. Without this the
            # renderer only ever saw the six named views, so the roll, pitch and
            # yaw keys could move the camera but never change the picture.
            img_array, _ = get_single_view(
                self.shapes[shape_index],
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=imposed_zoom_ax_limits,
                screen_size=render_screen_size,
                orientation_basis=params.get("orientation"),
            )
        #plt.imshow(img_array)
        #plt.savefig("payload_before.png")
        
        # COMPOSITION STAGE
        if compose_scrollbar:
            draw_w, draw_h = render_screen_size[0], render_screen_size[1]
            full_img = np.full((screen_size[1], screen_size[0], img_array.shape[2]), 255, dtype=np.uint8)
            if full_img.shape[2] > 3:
                full_img[:, :, 3] = 255
            copy_h = min(draw_h, img_array.shape[0], full_img.shape[0])
            copy_w = min(draw_w, img_array.shape[1], full_img.shape[1])
            full_img[:copy_h, :copy_w, :] = img_array[:copy_h, :copy_w, :]
            img_array = full_img

            # Keep a 1px spacer before the scrollbars for tactile/readability separation.
            img_array[:-1,-2,:] = [255,255,255,0]
            img_array[-2,:-1,:] = [255,255,255,0]
            img_array[:,-1,:] = [255,255,255,0]
            img_array[-1,:,:] = [255,255,255,0]
            y0 = max(0, int(draw_h * x_scroll_min))
            y1 = min(int(draw_h * x_scroll_max) + 1, draw_h)
            x0 = max(0, int(draw_w * y_scroll_min))
            x1 = min(int(draw_w * y_scroll_max) + 1, draw_w)
            img_array[y0:y1, -1, :] = [0,0,0,255]
            img_array[-1, x0:x1, :] = [0,0,0,255]

        if compose_cursor:
            if cursor_state == "none":
                pass
            elif cursor_state == "crosshair":
                # Draw a small crosshair cursor using display-space coordinates.
                crosshair_size = 5
                draw_w, draw_h = render_screen_size[0], render_screen_size[1]
                col = max(0, min(draw_w - 1, cursor_col))
                row = max(0, min(draw_h - 1, cursor_row))
                half = crosshair_size // 2
                x0 = max(0, col - half)
                x1 = min(draw_w, col + half + 1)
                y0 = max(0, row - half)
                y1 = min(draw_h, row + half + 1)
                # First draw a white square background to ensure visibility against any model color.
                img_array[y0:y1, x0:x1, 0:3] = [255, 255, 255]
                # Then draw the black crosshair lines on top of the white square.
                img_array[row, x0:x1, 0:3] = [0, 0, 0]
                img_array[y0:y1, col, 0:3] = [0, 0, 0]
                if img_array.shape[2] > 3:
                    img_array[y0:y1, x0:x1, 3] = 255
                    img_array[row, x0:x1, 3] = 255
                    img_array[y0:y1, col, 3] = 255
            elif cursor_state == "guidelines":
                # Draw horizontal and vertical guide lines using display-space coordinates.
                draw_w, draw_h = render_screen_size[0], render_screen_size[1]
                col = max(0, min(draw_w - 1, cursor_col))
                row = max(0, min(draw_h - 1, cursor_row))
                x0 = max(0, col - 1)
                x1 = min(draw_w, col + 2)
                y0 = max(0, row - 1)
                y1 = min(draw_h, row + 2)
                # Draw white lines on both sides of each black cursor line to ensure visibility against any model color.
                img_array[y0:y1, :, 0:3] = [255, 255, 255]
                img_array[:, x0:x1, 0:3] = [255, 255, 255]
                img_array[row, :, 0:3] = [0, 0, 0]
                img_array[:, col, 0:3] = [0, 0, 0]
                if img_array.shape[2] > 3:
                    img_array[y0:y1, :, 3] = 255
                    img_array[:, x0:x1, 3] = 255
                    img_array[row, :, 3] = 255
                    img_array[:, col, 3] = 255
            elif cursor_state == "horizontal-line":
                # Draw a horizontal guide line cursor using display-space coordinates.
                draw_w, draw_h = render_screen_size[0], render_screen_size[1]
                row = max(0, min(draw_h - 1, cursor_row))
                y0 = max(0, row - 1)
                y1 = min(draw_h, row + 2)
                img_array[y0:y1, :, 0:3] = [255, 255, 255]
                img_array[row, :, 0:3] = [0, 0, 0]
                if img_array.shape[2] > 3:
                    img_array[row, :, 3] = 255
            elif cursor_state == "vertical-line":
                # Draw a vertical guide line cursor using display-space coordinates.
                draw_w, draw_h = render_screen_size[0], render_screen_size[1]
                col = max(0, min(draw_w - 1, cursor_col))
                x0 = max(0, col - 1)
                x1 = min(draw_w, col + 2)
                img_array[:, x0:x1, 0:3] = [255, 255, 255]
                img_array[:, col, 0:3] = [0, 0, 0]
                if img_array.shape[2] > 3:
                    img_array[:, col, 3] = 255

        if object_out_of_frame and pan_guidance_directions:
            # Sighted-preview-only guidance — the announcement (built from
            # pan_guidance_directions in the server response) is what actually
            # conveys this to someone not looking at the screen. One
            # arrow per direction: out on both axes draws two (e.g. top edge
            # and right edge), not a single diagonal one the controls can't
            # act on in a single press.
            for direction in pan_guidance_directions:
                self._draw_pan_guidance_arrow(img_array, render_screen_size, direction)

        if "compose_scrollbar" in params.keys():
            compose_slice_graph = params["compose_slicegraph"]
        else:
            compose_slice_graph = False
        if compose_slice_graph:
            # Optionally use an anchored slice graph location while the user explores.
            slice_graph_locked = bool(params.get("slicegraph_locked", False))
            graph_view_name = view_name
            graph_depth_percent = depth_percent
            if slice_graph_locked:
                graph_view_name = self._map_view_name(params.get("slicegraph_view", params.get("view", "Top")))
                graph_depth_percent = int(params.get("slicegraph_depth", depth_percent))

            # take correct view_diff_mat
            graph_depth_percent = max(0, min(100, graph_depth_percent))
            graph_cut_depth = graph_depth_percent / 100.0
            cut_position_int = int(100.0 * (1.0 - graph_cut_depth))
            graph_zoom_ax_limits = imposed_zoom_ax_limits
            if graph_view_name != view_name:
                graph_view_index = self._get_view_index(graph_view_name)
                graph_zoom_ax_limits = self.view_limits[graph_view_index]
            view_diff_mat = self._get_zoom_filtered_slice_profile(
                graph_view_name,
                cut_position_int,
                graph_zoom_ax_limits,
            )

            # The marker always reflects the current (live) slice position,
            # even when the graph data is locked to an anchor depth.
            # NOTE: marker_position_int is NOT inverted like cut_position_int because
            # the image has already been flipped; in the flipped image, left=0% and
            # right=100%, so the marker index should increase with depth.
            current_depth_percent = max(0, min(100, depth_percent))
            marker_position_int = int(current_depth_percent)

            # Render line-graph edge-to-edge in the graph band width.
            width_px, height_px = screen_size[0], screen_size[1]
            graph_height_px = 10
            dpi = 100 

            fig = plt.figure(figsize=(width_px / dpi, graph_height_px / dpi), dpi=dpi)
            #fig = plt.figure(figsize=(1080 / dpi, 920 / dpi), dpi=dpi)
            ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
            ax.axis('off')
            # Render graph strokes as a single pixel (no anti-aliasing expansion).
            ax.plot(range(len(view_diff_mat)), view_diff_mat, aa=False, c="black", lw=.15)
            ax = plt.gca()
            #if len(imposed_ax_limits) > 0:

            ax.set_xlim((0, len(view_diff_mat)))
            ax.set_ylim((0, 1))

            #ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
            #print(ax_limits)
            #fig.savefig('test.png', dpi=dpi, pad_inches=0)

            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
            fig.clear()
            buf.seek(0)

            img = Image.open(buf)
            img_np = np.array(img)
            img_np = np.flip(img_np, axis=1)

            # Enforce a 1px slice marker column in the graph bitmap.
            # This avoids anti-aliased or multi-pixel marker thickness from plotting.
            if img_np.shape[1] > 0 and len(view_diff_mat) > 1:
                marker_col = int(round(marker_position_int * (img_np.shape[1] - 1) / (len(view_diff_mat) - 1)))
                marker_col = max(0, min(img_np.shape[1] - 1, marker_col))
                img_np[:, marker_col, :] = [0, 0, 0, 255]
            # Compose graph without an outline box. Keep only a horizontal divider
            # above the graph so the model and graph are clearly separated.
            graph_top = max(0, img_array.shape[0] - graph_height_px)
            graph_left = 0
            graph_h, graph_w = img_np.shape[0], img_np.shape[1]
            graph_bottom = min(img_array.shape[0], graph_top + graph_h)
            graph_right = min(img_array.shape[1], graph_left + graph_w)

            divider_row = max(0, graph_top - 1)
            img_array[divider_row, :, :] = [0, 0, 0, 255]

            copy_h = graph_bottom - graph_top
            copy_w = graph_right - graph_left
            if copy_h > 0 and copy_w > 0:
                img_array[graph_top:graph_bottom, graph_left:graph_right, :] = img_np[:copy_h, :copy_w, :]

        if comparison_mode == "side-by-side":
            self._overlay_side_by_side_view_labels(img_array, view_legend, view_cut)

        show_view_info_box = bool(params.get("show_view_info_box", False))
        if show_view_info_box and comparison_mode in ["single", "slice-graph"]:
            axis_text = params.get("view", "top").lower()
            self._overlay_view_info_box(img_array, axis_text)

        return RenderResult(
            img_array,
            camera_center=list(camera_center),
            framing_bounds=imposed_zoom_ax_limits,
            view_axis=view_name,
            cut_depth=1.0 - cut_depth,
            render_mode=render_mode,
            zoom_level=zoom_level,
            screen_size=list(screen_size),
            object_out_of_frame=object_out_of_frame,
            pan_guidance_directions=list(pan_guidance_directions),
        )

# Convenience function for simple usage
def render_cad_comparison(before_model_path, after_model_path, params):
    """
    Convenience function to render a CAD comparison in one call.
    
    Args:
        before_model_path: Path to the "before" STEP file
        after_model_path: Path to the "after" STEP file
        params: Dictionary with rendering parameters (see CADComparisonRenderer.render)
    
    Returns:
        numpy array: RGBA image array
    
    Example:
        img_array = render_cad_comparison(
            "models/cup.step",
            "models/cup_higher.step",
            {"view": "Front", "depth": 30, "renderMode": "Shaded"}
        )
    """
    renderer = CADComparisonRenderer(before_model_path, after_model_path)
    return renderer.render(params).image
