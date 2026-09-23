import matplotlib
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
from .render_low_res import get_outlines
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox, faces_on_plane_fast

# The six standard views, each as (right, up, depth): the model directions that
# point to the display's right, to its top edge, and out of it at the reader.
#
# One convention for all six. depth = right x up, so every view is right-handed
# and the reader always sits on the +depth side. The cut removes the half on that
# side, which is what makes 0% the surface nearest the reader in every view, and
# the picture is what OpenSCAD shows for the view of the same name (checked
# against its presets, src/gui/MainWindow.cc:2817-2862 at openscad@0e6cc0b).
#
# This used to be two conventions applied view by view: top, left and right had
# right x up = +depth, while front, back and bottom had -depth, so those three cut
# from the far side, and bottom was top turned 180 degrees rather than a view from
# below. The turn keys were written for the far-side three, which is why pitch and
# yaw ran backwards from the viewer's default view (#185).
#
# "left" and "right" were also the wrong way round: the basis filed under "left"
# looked at the model from +X, which is OpenSCAD's Right view. The keys now name
# the view they hold. The viewer's wire tokens are unchanged (x- is still the view
# from +X) and _map_view_name translates them.
VIEW_BASES = {
    "top": (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ),
    "bottom": (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, -1.0, 0.0]),
        np.array([0.0, 0.0, -1.0]),
    ),
    "front": (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, -1.0, 0.0]),
    ),
    "back": (
        np.array([-1.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, 1.0, 0.0]),
    ),
    "right": (
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 0.0, 0.0]),
    ),
    "left": (
        np.array([0.0, -1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
    ),
}


def get_cut_faces(shape, view_key, cut_depth, bbox, orientation_basis=None):
    """The faces lying on the cut plane, for this view at this orientation.

    The normal comes from the same basis the picture is drawn with, so a caller
    that fits or measures a slice cuts where the render cuts. It used to come from
    a separate table of eye positions that disagreed with the bases, and ignored
    the orientation entirely, so fitting a turned model measured some other slice.
    """
    normal_dir = _get_view_basis(view_key, orientation_basis=orientation_basis)[2]
    shape_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    shape_faces = faces_on_plane_fast(shape_cut, plane_origin, normal_dir)
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
    - forward (or depth): the model direction pointing out of the display, at
      the reader. The wire has always called it "forward"; it is the same
      vector the viewer calls depth, and the cut removes the half it points to.
      It is not the direction the camera looks, which is its opposite.
    - up: the model direction pointing to the display's top edge
    - right: the model direction pointing to the display's right edge

    All three are used as given when all three are present and mutually
    perpendicular. Every basis the viewer sends is right-handed (forward =
    right x up); a left-handed one is still drawn as given rather than
    corrected, because correcting it would mean guessing which of the three the
    sender got wrong.

    A complete but skewed basis (not mutually perpendicular) falls through to
    the same derivation used for a partial one, rather than being used as
    given -- projecting onto non-orthogonal axes would silently skew the
    picture instead of just mirroring it. The derivation keeps the basis
    right-handed.
    """
    if not isinstance(orientation_basis, dict):
        return None

    depth_hint = orientation_basis.get("depth", orientation_basis.get("forward"))
    depth_axis = _safe_unit(depth_hint)
    if depth_axis is None:
        return None

    up_axis = _safe_unit(orientation_basis.get("up"))
    right_axis = _safe_unit(orientation_basis.get("right"))

    if (
        right_axis is not None
        and up_axis is not None
        and abs(np.dot(right_axis, up_axis)) < 1e-6
        and abs(np.dot(right_axis, depth_axis)) < 1e-6
        and abs(np.dot(up_axis, depth_axis)) < 1e-6
    ):
        return right_axis, up_axis, depth_axis

    if up_axis is not None:
        right_axis = _safe_unit(np.cross(up_axis, depth_axis))
        if right_axis is not None:
            return right_axis, up_axis, depth_axis

    if right_axis is not None:
        up_axis = _safe_unit(np.cross(depth_axis, right_axis))
        if up_axis is not None:
            return right_axis, up_axis, depth_axis

    return None

def _get_view_basis(view_key, orientation_basis=None):
    """Return (right, up, depth) axes for the selected view.

    If orientation_basis is provided and valid, it takes precedence.
    """
    custom_basis = _resolve_orientation_basis(orientation_basis)
    if custom_basis is not None:
        return custom_basis
    return VIEW_BASES.get(view_key, VIEW_BASES["top"])


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

def _collect_feature_edges(shape, view_key, projection_mode="orthographic", xray_degrees=22.5, orientation_basis=None):
    """Return projected line segments for silhouette + xray edges."""
    if shape is None or len(shape.faces) == 0:
        return []

    vertices_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
    unique_edges = shape.edges_unique
    if unique_edges is None or len(unique_edges) == 0:
        return []

    face_normals = np.asarray(shape.face_normals)
    # depth points at the reader, so a face the reader can see has a normal
    # along +depth. This read "< -1e-6" while half the views had depth pointing
    # away; it was right for those three and inside out for the other three.
    _, _, toward_reader = _get_view_basis(view_key, orientation_basis=orientation_basis)
    front_facing = (face_normals @ toward_reader) > 1e-6

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
SUPERSAMPLE = 8
MAX_CANVAS_PX = 3200


def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="filled",
                    imposed_ax_limits=[], screen_size=[96,40], orientation_basis=None):
    print("get_single_view", rendering_mode)

    shape = copy(shape)
    # Cut along whichever way the viewer is actually looking, removing the half
    # on the reader's side. Under a rotated orientation this is the only normal
    # that slices into the screen rather than along a fixed model axis.
    normal_dir = _get_view_basis(view_key, orientation_basis=orientation_basis)[2]
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
        # One projection for every mode and every orientation. This used to pick
        # columns per named view, which meant an orientation off the six named
        # ones could not be drawn at all: the basis was accepted by the server,
        # validated, and then never reached the renderer.
        coords = project_vertices(shape.vertices, view_key, orientation_basis=orientation_basis)
        ax.tripcolor(coords[:,0], coords[:, 1], facecolors=colors, cmap="gray", triangles=shape.faces, aa=False, edgecolor="#00000000", shading="flat")

    if len(imposed_ax_limits) > 0:
        ax.set_xlim(imposed_ax_limits[0])
        ax.set_ylim(imposed_ax_limits[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])

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
        outlines_np, outline_mask = get_outlines(img_np)
        fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=render_dpi)
        ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
        ax.axis('off')

        ax.set_aspect('equal')
        ax.set_xlim(ax_limits[0])
        ax.set_ylim(ax_limits[1])

        segments = _collect_feature_edges(shape, view_key, orientation_basis=orientation_basis)
        segments_2d = project_vertices(
            segments.reshape(-1, 3), view_key, orientation_basis=orientation_basis
        ).reshape(-1, 2, 2)
        # With _to_braille_payload's majority (>50%) threshold, a line that
        # straddles an output-pixel boundary splits its coverage as (a, W-a)
        # between the two neighbors: too thin and both sides can land under
        # 50% (a gap, neither pixel raised); too thick and both can reach
        # 50%+ (a doubled line). 8x oversampling at 800dpi puts the boundary
        # exactly at 1 output-pixel = 0.72pt, but matplotlib's own line
        # antialiasing spreads coverage further than the nominal width, so
        # the real crossover was found empirically by sweeping a line across
        # a pixel boundary and counting raised pixels at each sub-pixel
        # offset: 0.65pt gave exactly 1 raised pixel at all 41 positions
        # tested, with zero gaps and zero doubles (0.72pt still doubled at
        # ~15% of offsets; 0.6pt gapped at ~15-27%).
        line_collection = LineCollection(segments_2d, colors="black", linewidths=0.65)
        ax.add_collection(line_collection)

        fig.canvas.draw()

        img = np.asarray(fig.canvas.buffer_rgba())
        if plt.fignum_exists(fig.number):
            plt.close(fig.number)
        plt.close()

        img_np = resize_local_mean(img, (height_px, width_px))
        img_np = (img_np * 255).astype(np.uint8)
        img_np[outline_mask] = [0,0,0,255]
        return img_np, ax_limits

    if rendering_mode == "outline":
        outlines_np, outline_mask = get_outlines(img_np)
        return outlines_np, ax_limits

if __name__ == '__main__':
    shape = trimesh.load_mesh("../../model/lego_2x3.stl")
    get_single_view(shape, shape.bounds.flatten(), cut_depth=0.41, rendering_mode="cut", view_key="left")
    exit()
