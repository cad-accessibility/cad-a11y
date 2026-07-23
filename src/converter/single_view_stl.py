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

def _collect_feature_edges(shape, view_key, projection_mode="orthographic", xray_degrees=22.5, orientation_basis=None):
    """Return projected line segments for silhouette + xray edges."""
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


def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="filled", imposed_ax_limits=[], screen_size=[96,40]):
    print("get_single_view", rendering_mode)

    shape = copy(shape)
    normal_dir = views[view_key]["dir"]
    shape, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    if rendering_mode == "slice":
        shape = faces_on_plane_fast(shape, plane_origin, normal_dir)

    # Target pixel resolution
    width_px, height_px = screen_size[0], screen_size[1]
    dpi = 100 

    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=800)
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

    fig.canvas.draw()

    img = np.asarray(fig.canvas.buffer_rgba())

    img_np = resize_local_mean(img, (height_px, width_px))
    img_np = (img_np * 255).astype(np.uint8)

    if plt.fignum_exists(fig.number):
        plt.close(fig.number)
    plt.close()

    if rendering_mode in ["filled", "slice"]:
        return img_np, ax_limits
    if rendering_mode == "x-ray":
        outlines_np, outline_mask = get_outlines(img_np)
        fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=800)
        ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
        ax.axis('off')

        ax.set_aspect('equal')
        ax.set_xlim(ax_limits[0])
        ax.set_ylim(ax_limits[1])

        segments = _collect_feature_edges(shape, view_key)
        segments_2d = []
        segments = segments.reshape(-1,3)
        if view_key == "top":
            segments_2d = segments[:,[0,1]]
        if view_key == "front":
            segments_2d = segments[:,[0,2]]
        if view_key == "left":
            segments_2d = segments[:,[1,2]]
        if view_key == "bottom":
            segments_2d = segments[:,[0,1]]
            segments_2d[:,0] *= -1
            segments_2d[:,1] *= -1
        if view_key == "back":
            segments_2d = segments[:,[0,2]]
            segments_2d[:,0] *= -1
        if view_key == "right":
            segments_2d = segments[:,[1,2]]
            segments_2d[:,0] *= -1
        segments_2d = segments_2d.reshape(-1,2,2)
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
    get_single_view(shape, shape.bounds.flatten(), cut_depth=0.41, rendering_mode="slice", view_key="left")
    exit()