import matplotlib
import io

from skimage.transform import resize_local_mean
from PIL import Image
from copy import copy
matplotlib.use('Agg')  # Use non-GUI backend for thread safety
from matplotlib.collections import LineCollection
import numpy as np
import io, PIL
from PIL import Image
import os, json
import matplotlib.pyplot as plt
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .render_low_res import get_outlines
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox, faces_on_plane_fast

views = {
    "top": {
        "eye": np.array([0, 0, -1000.0]),
        "dir": np.array([0, 0, 1.0])
    },
    "front": {
        "eye": np.array([0, -1000, 0.0]),
        "dir": np.array([0, 1, 0.0])
    },
    "left": {
        "eye": np.array([-1000.0, 0, 0]),
        "dir": np.array([1.0, 0, 0])
    },
    "bottom": {
        "eye": np.array([0, 0, 1000.0]),
        "dir": np.array([0, 0, -1.0])
    },
    "back": {
        "eye": np.array([0, 1000.0, 0]),
        "dir": np.array([0, -1.0, 0])
    },
    "right": {
        "eye": np.array([1000.0, 0, 0]),
        "dir": np.array([-1.0, 0, 0])
    }
}

def get_cut_faces(shape, view_key, cut_depth, bbox):
    normal_dir = views[view_key]["dir"]
    shape_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    shape_faces = faces_on_plane_fast(shape_cut, plane_origin, normal_dir)
    #print(shape_cut.area, shape_faces.area, plane_origin, bbox)
    return shape_faces

def _safe_unit(vec):
    arr = np.asarray(vec, dtype=float).reshape(-1)
    if arr.size != 3:
        return None
    if not np.all(np.isfinite(arr)):
        return None
    norm = np.linalg.norm(arr)
    if norm < 1e-12:
        return None
    return arr / norm


def _resolve_orientation_basis(orientation_basis):
    """Return an orthonormal (right, up, depth) basis from orientation metadata.

    Accepted keys:
    - depth or forward: viewing direction
    - up: camera-up hint
    - right: optional camera-right hint used when up is missing/degenerate
    """
    if not isinstance(orientation_basis, dict):
        return None

    depth_hint = orientation_basis.get("depth", orientation_basis.get("forward"))
    depth_axis = _safe_unit(depth_hint)
    if depth_axis is None:
        return None

    up_hint = _safe_unit(orientation_basis.get("up"))
    right_hint = _safe_unit(orientation_basis.get("right"))

    if up_hint is not None:
        right_axis = np.cross(up_hint, depth_axis)
        right_axis = _safe_unit(right_axis)
        if right_axis is not None:
            up_axis = _safe_unit(np.cross(depth_axis, right_axis))
            if up_axis is not None:
                return right_axis, up_axis, depth_axis

    if right_hint is not None:
        up_axis = np.cross(depth_axis, right_hint)
        up_axis = _safe_unit(up_axis)
        if up_axis is not None:
            right_axis = _safe_unit(np.cross(up_axis, depth_axis))
            if right_axis is not None:
                return right_axis, up_axis, depth_axis

    return None

def _get_view_basis(view_key, orientation_basis=None):
    """Return (right, up, depth) axes for the selected view.

    If orientation_basis is provided and valid, it takes precedence.
    """
    custom_basis = _resolve_orientation_basis(orientation_basis)
    if custom_basis is not None:
        return custom_basis

    basis = {
        "top": (
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
        ),
        "front": (
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
        ),
        "left": (
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            np.array([1.0, 0.0, 0.0]),
        ),
        "bottom": (
            np.array([-1.0, 0.0, 0.0]),
            np.array([0.0, -1.0, 0.0]),
            np.array([0.0, 0.0, -1.0]),
        ),
        "back": (
            np.array([-1.0, 0.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, -1.0, 0.0]),
        ),
        "right": (
            np.array([0.0, -1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    }
    return basis.get(view_key, basis["top"])


def project_vertices(vertices, view_key, projection_mode="orthographic", orientation_basis=None):
    """Project 3D vertices into 2D for the selected view/projection."""
    if vertices is None or len(vertices) == 0:
        return np.zeros((0, 2), dtype=float)

    right_axis, up_axis, depth_axis = _get_view_basis(view_key, orientation_basis=orientation_basis)
    x = vertices @ right_axis
    y = vertices @ up_axis
    z = vertices @ depth_axis

    mode = (projection_mode or "orthographic").lower()
    if mode == "none":
        mode = "orthographic"
    if mode == "oblique":
        # Cabinet projection keeps depth readable without over-stretching.
        theta = np.deg2rad(45.0)
        depth_scale = 0.5
        x = x + depth_scale * z * np.cos(theta)
        y = y + depth_scale * z * np.sin(theta)
    elif mode == "isometric":
        # Lightweight axonometric effect for tactile readability.
        x = x + 0.60 * z
        y = y + 0.35 * z

    return np.column_stack((x, y))

def _collect_feature_edges(shape, view_key, projection_mode="orthographic", xray_degrees=22.5,
                           orientation_basis=None):
    """Return projected line segments for silhouette + xray edges.

    Note these are the mesh's silhouette edges, every edge where facing flips.
    On a hollow model that includes the far wall and the inside of the cavity,
    which is what x-ray wants and is emphatically not an outline. The visible
    outline is _silhouette_rings above.
    """
    if shape is None or len(shape.faces) == 0:
        return []

    vertices_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
    unique_edges = shape.edges_unique
    if unique_edges is None or len(unique_edges) == 0:
        return []

    face_normals = np.asarray(shape.face_normals)
    _, _, view_dir = _get_view_basis(view_key, orientation_basis=orientation_basis)
    front_facing = (face_normals @ view_dir) < -1e-6

    edge_to_faces = [[] for _ in range(len(unique_edges))]
    for face_idx, edge_ids in enumerate(shape.faces_unique_edges):
        for edge_id in edge_ids:
            edge_to_faces[int(edge_id)].append(face_idx)

    xray_threshold = np.deg2rad(float(xray_degrees))
    segments = []
    for edge_idx, adjacent_faces in enumerate(edge_to_faces):
        include_edge = False
        if len(adjacent_faces) == 1:
            include_edge = bool(front_facing[adjacent_faces[0]])
        elif len(adjacent_faces) >= 2:
            f0, f1 = adjacent_faces[0], adjacent_faces[1]
            n0 = face_normals[f0]
            n1 = face_normals[f1]
            dot = float(np.clip(np.dot(n0, n1), -1.0, 1.0))
            angle = np.arccos(dot)
            silhouette = bool(front_facing[f0]) != bool(front_facing[f1])
            # Keep x-ray edges regardless of facing to preserve interior detail
            # in orthographic tactile views.
            xray = angle >= xray_threshold
            include_edge = silhouette or xray

        if not include_edge:
            continue

        i0, i1 = unique_edges[edge_idx]
        p0 = vertices_2d[int(i0)]
        p1 = vertices_2d[int(i1)]
        p0_3d = shape.vertices[int(i0)]
        p1_3d = shape.vertices[int(i1)]
        #if np.allclose(p0, p1):
        #    continue
        if np.allclose(p0_3d, p1_3d):
            continue
        segments.append(np.array([p0_3d, p1_3d]))

    return np.array(segments)


# The figure is drawn supersampled and area-averaged down to the target grid, so
# a partly covered output pixel carries a real coverage fraction instead of a
# yes/no. That is what lets the raised-ink rule decide by coverage on the tactile
# grid, where the target is only ~96px across and the supersampled canvas is
# still small. It stops paying for itself long before the ~800px preview, where a
# fixed 8x factor means a 6400px canvas and a render cost that scales with its
# area. Capping the canvas keeps the full factor for tactile-sized targets (their
# output is unchanged) and falls back to a still-generous factor for large ones.
def _capture_svg(fig, svg_sink):
    """Stash the figure as SVG before it is rasterised, if anyone asked for it.

    Everything up to fig.canvas.draw() is vector: the triangles of a filled or
    cut view, the line segments of an x-ray. After it there are only pixels, and
    the supersample-and-downsample step that follows is what turns coverage into
    raised pins. So a caller wanting real geometry has to be handed it here.

    Best effort: an export failing must never break the render the display is
    waiting on.
    """
    if svg_sink is None:
        return
    try:
        buffer = io.BytesIO()
        fig.savefig(buffer, format="svg")
        svg_sink.append(buffer.getvalue().decode("utf-8"))
    except Exception:
        pass


SUPERSAMPLE = 8
MAX_CANVAS_PX = 3200

# With _to_braille_payload's majority (>50%) threshold, a line that straddles an
# output-pixel boundary splits its coverage as (a, W-a) between the two
# neighbors: too thin and both sides can land under 50% (a gap, neither pixel
# raised); too thick and both can reach 50%+ (a doubled line). 8x oversampling at
# 800dpi puts the boundary exactly at 1 output-pixel = 0.72pt, but matplotlib's
# own line antialiasing spreads coverage further than the nominal width, so the
# real crossover was found empirically by sweeping a line across a pixel boundary
# and counting raised pixels at each sub-pixel offset: 0.65pt gave exactly 1
# raised pixel at all 41 positions tested, with zero gaps and zero doubles
# (0.72pt still doubled at ~15% of offsets; 0.6pt gapped at ~15-27%).
TACTILE_LINE_PT = 0.65


def _silhouette_rings(shape, view_key):
    """The visible outline of the projected shape, as closed rings.

    Rings rather than loose segments so the drawing comes out as a handful of
    connected paths: joins meet properly, and anyone restyling the file
    downstream gets a shape to select rather than a few hundred separate lines.

    Not the same thing as the mesh's silhouette edges. Those include every edge
    where facing flips, which on a hollow model means the far wall and the inside
    of the cavity as well: the pencil holder yields 1032 of them where its
    outline is one ring. What is actually wanted is the boundary of the union of
    the projected triangles, holes included, so that is what this computes.

    The union is the expensive step, around half a second on a 19k-face mesh, but
    it collapses to very little: that same mesh comes out as four rings of 210
    points in total.
    """
    faces = np.asarray(shape.faces)
    if len(faces) == 0:
        return []

    triangles = _project_points(shape.vertices[faces], view_key).reshape(-1, 3, 2)

    polygons = [Polygon(t) for t in triangles]
    merged = unary_union([p for p in polygons if p.is_valid and not p.is_empty])
    if merged.is_empty:
        return []

    rings = []
    parts = merged.geoms if hasattr(merged, "geoms") else [merged]
    for part in parts:
        if not isinstance(part, Polygon):
            continue
        for ring in [part.exterior, *part.interiors]:
            points = np.asarray(ring.coords)
            if len(points) >= 2:
                rings.append(points)
    return rings


def _project_points(points, view_key):
    """Drop 3-D points onto the 2-D plane of a named view."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if view_key == "top":
        flat = points[:, [0, 1]]
    elif view_key == "front":
        flat = points[:, [0, 2]]
    elif view_key == "left":
        flat = points[:, [1, 2]]
    elif view_key == "bottom":
        flat = points[:, [0, 1]] * [-1, -1]
    elif view_key == "back":
        flat = points[:, [0, 2]] * [-1, 1]
    elif view_key == "right":
        flat = points[:, [1, 2]] * [-1, 1]
    else:
        flat = points[:, [0, 1]]
    return flat


def _project_segments(segments, view_key):
    """Flatten 3-D edge segments into the 2-D plane of a named view."""
    return _project_points(segments, view_key).reshape(-1, 2, 2)


def _render_line_work(segments_2d, ax_limits, width_px, height_px, dpi, render_dpi, svg_sink):
    """Draw projected line work, capture it as vector, then rasterise it.

    Outline and x-ray both come through here, so both get the same stroke width,
    the same capture point and the same downsample. The pins and the exported
    drawing are then two readings of one figure rather than two computations that
    have to be kept in step by hand.
    """
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=render_dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')
    ax.set_aspect('equal')
    ax.set_xlim(ax_limits[0])
    ax.set_ylim(ax_limits[1])

    if len(segments_2d):
        ax.add_collection(LineCollection(segments_2d, colors="black", linewidths=TACTILE_LINE_PT))

    # The line work replaces whatever base figure was captured before it: strokes
    # are what these modes are, and a stroke stays restylable in an SVG.
    if svg_sink is not None:
        svg_sink.clear()
    _capture_svg(fig, svg_sink)
    fig.canvas.draw()

    img = np.asarray(fig.canvas.buffer_rgba())
    if plt.fignum_exists(fig.number):
        plt.close(fig.number)
    plt.close()

    img_np = resize_local_mean(img, (height_px, width_px))
    return (img_np * 255).astype(np.uint8)


def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="filled",
                    imposed_ax_limits=[], screen_size=[96,40], svg_sink=None):
    print("get_single_view", rendering_mode)

    shape = copy(shape)
    normal_dir = views[view_key]["dir"]
    shape, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    if rendering_mode == "cut":
        shape = faces_on_plane_fast(shape, plane_origin, normal_dir)

    # Target pixel resolution
    width_px, height_px = screen_size[0], screen_size[1]
    dpi = 100

    # figsize is width_px/dpi inches, so the drawn canvas is figsize * render_dpi
    # pixels across. Solve for the dpi that lands on the capped canvas width.
    canvas_px = min(width_px * SUPERSAMPLE, MAX_CANVAS_PX)
    render_dpi = max(dpi, int(round(canvas_px * dpi / max(1, width_px))))

    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=render_dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    ax.set_aspect('equal')
    if len(imposed_ax_limits) > 0:
        ax.set_xlim(imposed_ax_limits[0])
        ax.set_ylim(imposed_ax_limits[1])

    if type(shape) != list and len(shape.faces) > 0 and not np.isclose(shape.area, 0.0):

        colors = [0.0 for i in range(len(shape.faces))]
        if view_key == "top":
            coords = shape.vertices[:,[0,1]]
        if view_key == "front":
            coords = shape.vertices[:,[0,2]]
        if view_key == "left":
            coords = shape.vertices[:,[1,2]]
        if view_key == "bottom":
            coords = shape.vertices[:,[0,1]]
            coords[:,0] *= -1
            coords[:,1] *= -1
        if view_key == "back":
            coords = shape.vertices[:,[0,2]]
            coords[:,0] *= -1
        if view_key == "right":
            coords = shape.vertices[:,[1,2]]
            coords[:,0] *= -1
        ax.tripcolor(coords[:,0], coords[:, 1], facecolors=colors, cmap="gray", triangles=shape.faces, aa=False, edgecolor="#00000000", shading="flat")

    if len(imposed_ax_limits) > 0:
        ax.set_xlim(imposed_ax_limits[0])
        ax.set_ylim(imposed_ax_limits[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])

    _capture_svg(fig, svg_sink)
    fig.canvas.draw()

    img = np.asarray(fig.canvas.buffer_rgba())

    img_np = resize_local_mean(img, (height_px, width_px))
    img_np = (img_np * 255).astype(np.uint8)

    if plt.fignum_exists(fig.number):
        plt.close(fig.number)
    plt.close()

    if rendering_mode in ["filled", "cut"]:
        return img_np, ax_limits
    if rendering_mode == "x-ray":
        outline_mask = get_outlines(img_np)[1]
        segments_2d = _project_segments(_collect_feature_edges(shape, view_key), view_key)
        img_np = _render_line_work(segments_2d, ax_limits,
                                   width_px, height_px, dpi, render_dpi, svg_sink)
        img_np[outline_mask] = [0, 0, 0, 255]
        return img_np, ax_limits

    if rendering_mode == "outline":
        outlines_np, _ = get_outlines(img_np)

        # The pins keep coming from the raster mask, which is closed by
        # construction. Stroking the boundary instead leaves gaps: at the width a
        # single pin needs, a run that straddles a pixel boundary can fall under
        # the coverage the majority rule wants, and the mug's rim came out
        # dashed. X-ray only gets away with strokes because it unions this same
        # mask back in.
        #
        # The export still gets real geometry rather than a trace of the grid.
        # It costs a union of the projected triangles, around half a second on a
        # 19k-face mesh, so it is only paid when someone is actually downloading.
        if svg_sink is not None:
            _render_line_work(_silhouette_rings(shape, view_key), ax_limits,
                              width_px, height_px, dpi, render_dpi, svg_sink)
        return outlines_np, ax_limits

if __name__ == '__main__':
    shape = trimesh.load_mesh("../../model/lego_2x3.stl")
    get_single_view(shape, shape.bounds.flatten(), cut_depth=0.41, rendering_mode="cut", view_key="left")
    exit()