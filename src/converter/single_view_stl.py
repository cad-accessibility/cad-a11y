import matplotlib
from time import time
from PIL import Image
from copy import copy
matplotlib.use('Agg')  # Use non-GUI backend for thread safety
import numpy as np
import io, PIL
from PIL import Image
import os, json
import matplotlib.pyplot as plt
import trimesh
from matplotlib.collections import LineCollection
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, LineString, MultiLineString
from shapely.ops import unary_union
from .render_low_res import get_outlines
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox, faces_on_plane, faces_on_plane_fast, compute_area

from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Extend.DataExchange import write_stl_file
from OCC.Core.gp import gp_Pnt, gp_Dir 

#views = {
#    "top": {
#        "eye": gp_Pnt(0, 0, -1000),
#        "dir": gp_Dir(0, 0, 1)
#    },
#    "front": {
#        "eye": gp_Pnt(0, -1000, 0),
#        "dir": gp_Dir(0, 1, 0)
#    },
#    "left": {
#        "eye": gp_Pnt(-1000, 0, 0),
#        "dir": gp_Dir(1, 0, 0)
#    },
#    "bottom": {
#        "eye": gp_Pnt(0, 0, 1000),
#        "dir": gp_Dir(0, 0, -1)
#    },
#    "back": {
#        "eye": gp_Pnt(0, 1000, 0),
#        "dir": gp_Dir(0, -1, 0)
#    },
#    "right": {
#        "eye": gp_Pnt(1000, 0, 0),
#        "dir": gp_Dir(-1, 0, 0)
#    }
#}
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


def _collect_feature_edges(shape, view_key, projection_mode="orthographic", crease_degrees=22.5, orientation_basis=None):
    """Return projected line segments for silhouette + crease edges."""
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

    crease_threshold = np.deg2rad(float(crease_degrees))
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
            # Keep crease edges regardless of facing to preserve interior detail
            # in orthographic tactile views.
            crease = angle >= crease_threshold
            include_edge = silhouette or crease

        if not include_edge:
            continue

        i0, i1 = unique_edges[edge_idx]
        p0 = vertices_2d[int(i0)]
        p1 = vertices_2d[int(i1)]
        if np.allclose(p0, p1):
            continue
        segments.append([p0, p1])

    return segments


def _collect_silhouette_edges(shape, view_key, projection_mode="orthographic", orientation_basis=None):
    """Return projected outer contour only (no interior/occlusion lines)."""
    if shape is None or len(shape.faces) == 0:
        return []

    coords_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
    if coords_2d.shape[0] == 0:
        return []

    triangles = []
    for face in shape.faces:
        tri = coords_2d[np.asarray(face, dtype=int)]
        if tri.shape != (3, 2):
            continue
        poly = Polygon(tri)
        if poly.is_empty or poly.area <= 1e-12:
            continue
        triangles.append(poly)

    if len(triangles) == 0:
        return []

    merged = unary_union(triangles)
    if merged.is_empty:
        return []

    segments = []

    def _add_linestring_segments(line_geom):
        pts = np.asarray(line_geom.coords, dtype=float)
        if pts.shape[0] < 2:
            return
        for i in range(pts.shape[0] - 1):
            p0 = pts[i]
            p1 = pts[i + 1]
            if np.allclose(p0, p1):
                continue
            segments.append([p0, p1])

    # For Outline mode we intentionally keep only exterior contours.
    if isinstance(merged, Polygon):
        _add_linestring_segments(merged.exterior)
    elif isinstance(merged, MultiPolygon):
        for poly in merged.geoms:
            _add_linestring_segments(poly.exterior)
    elif isinstance(merged, GeometryCollection):
        for geom in merged.geoms:
            if isinstance(geom, Polygon):
                _add_linestring_segments(geom.exterior)
            elif isinstance(geom, MultiPolygon):
                for poly in geom.geoms:
                    _add_linestring_segments(poly.exterior)
            elif isinstance(geom, LineString):
                _add_linestring_segments(geom)
            elif isinstance(geom, MultiLineString):
                for line in geom.geoms:
                    _add_linestring_segments(line)

    return segments


def _collect_boundary_edges(shape, view_key, projection_mode="orthographic", orientation_basis=None):
    """Return projected boundary-only segments for planar/slice meshes."""
    if shape is None or len(shape.faces) == 0:
        return []

    vertices_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
    unique_edges = shape.edges_unique
    if unique_edges is None or len(unique_edges) == 0:
        return []

    edge_face_count = np.zeros(len(unique_edges), dtype=int)
    for edge_ids in shape.faces_unique_edges:
        for edge_id in edge_ids:
            edge_face_count[int(edge_id)] += 1

    segments = []
    for edge_idx, count in enumerate(edge_face_count):
        if int(count) != 1:
            continue
        i0, i1 = unique_edges[edge_idx]
        p0 = vertices_2d[int(i0)]
        p1 = vertices_2d[int(i1)]
        if np.allclose(p0, p1):
            continue
        segments.append([p0, p1])
    return segments


def _collect_slice_outline_segments(shape, view_key, projection_mode="orthographic", orientation_basis=None):
    """Return clean slice outlines by unioning projected triangle polygons.

    This removes internal triangulation diagonals that can appear when relying
    purely on mesh boundary-edge counting for plane-cut meshes.
    """
    if shape is None or len(shape.faces) == 0:
        return []

    coords_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
    if coords_2d.shape[0] == 0:
        return []

    triangles = []
    for face in shape.faces:
        tri = coords_2d[np.asarray(face, dtype=int)]
        if tri.shape != (3, 2):
            continue
        poly = Polygon(tri)
        if poly.is_empty or poly.area <= 1e-12:
            continue
        triangles.append(poly)

    if len(triangles) == 0:
        return []

    merged = unary_union(triangles)
    if merged.is_empty:
        return []

    boundaries = merged.boundary
    segments = []

    def _add_linestring_segments(line_geom):
        pts = np.asarray(line_geom.coords, dtype=float)
        if pts.shape[0] < 2:
            return
        for i in range(pts.shape[0] - 1):
            p0 = pts[i]
            p1 = pts[i + 1]
            if np.allclose(p0, p1):
                continue
            segments.append([p0, p1])

    if isinstance(boundaries, LineString):
        _add_linestring_segments(boundaries)
    elif isinstance(boundaries, MultiLineString):
        for line in boundaries.geoms:
            _add_linestring_segments(line)
    elif isinstance(merged, Polygon):
        _add_linestring_segments(merged.exterior)
        for ring in merged.interiors:
            _add_linestring_segments(ring)
    elif isinstance(merged, MultiPolygon):
        for poly in merged.geoms:
            _add_linestring_segments(poly.exterior)
            for ring in poly.interiors:
                _add_linestring_segments(ring)
    elif isinstance(merged, GeometryCollection):
        for geom in merged.geoms:
            if isinstance(geom, Polygon):
                _add_linestring_segments(geom.exterior)
                for ring in geom.interiors:
                    _add_linestring_segments(ring)
            elif isinstance(geom, LineString):
                _add_linestring_segments(geom)
            elif isinstance(geom, MultiLineString):
                for line in geom.geoms:
                    _add_linestring_segments(line)

    return segments


def _draw_line_bresenham(binary_img, x0, y0, x1, y1):
    """Draw a single-pixel line into a uint8 mask using Bresenham."""
    h, w = binary_img.shape
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    x, y = x0, y0
    while True:
        if 0 <= x < w and 0 <= y < h:
            binary_img[y, x] = 255
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _segments_to_binary_rgba(segments, imposed_ax_limits, screen_size):
    """Rasterize projected segments directly to a binary RGBA image."""
    width_px, height_px = int(screen_size[0]), int(screen_size[1])
    mask = np.zeros((height_px, width_px), dtype=np.uint8)

    x_min, x_max = float(imposed_ax_limits[0][0]), float(imposed_ax_limits[0][1])
    y_min, y_max = float(imposed_ax_limits[1][0]), float(imposed_ax_limits[1][1])
    x_span = max(1e-12, x_max - x_min)
    y_span = max(1e-12, y_max - y_min)

    for seg in segments:
        (x0, y0), (x1, y1) = seg
        c0 = int(round((float(x0) - x_min) / x_span * (width_px - 1)))
        c1 = int(round((float(x1) - x_min) / x_span * (width_px - 1)))
        r0 = int(round((1.0 - (float(y0) - y_min) / y_span) * (height_px - 1)))
        r1 = int(round((1.0 - (float(y1) - y_min) / y_span) * (height_px - 1)))
        _draw_line_bresenham(mask, c0, r0, c1, r1)

    rgba = np.full((height_px, width_px, 4), 255, dtype=np.uint8)
    ink = mask > 0
    rgba[ink] = [0, 0, 0, 255]
    return rgba


def get_cut_faces(shape, view_key, cut_depth, bbox, orientation_basis=None):
    _, _, normal_dir = _get_view_basis(view_key, orientation_basis=orientation_basis)
    shape_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    shape_faces = faces_on_plane_fast(shape_cut, plane_origin, normal_dir)
    #print(shape_cut.area, shape_faces.area, plane_origin, bbox)
    return shape_faces

def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="filled", imposed_ax_limits=[], screen_size=[96,40], projection_mode="none", orientation_basis=None):

    shape = copy(shape)
    original_shape = shape
    #print("rendering mode", rendering_mode, "view key", view_key)
    #print("cut depth", cut_depth)
    #cut_depth = 0.5
    _, _, normal_dir = _get_view_basis(view_key, orientation_basis=orientation_basis)
    projection_mode = (projection_mode or "orthographic").lower()
    if projection_mode == "none":
        projection_mode = "orthographic"

    # Preserve full geometry for orthographic outline so inner feature edges
    # are not discarded by depth peeling.
    if rendering_mode == "outline" and projection_mode == "orthographic":
        shape = original_shape
        plane_origin = None
    else:
        shape, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)

    if rendering_mode == "slice":
        shape = faces_on_plane_fast(shape, plane_origin, normal_dir)

    if projection_mode in ["orthographic", "silhouette"] and len(imposed_ax_limits) > 0 and rendering_mode in ["outline", "slice"]:
        if rendering_mode == "outline":
            if projection_mode == "silhouette":
                segments = _collect_silhouette_edges(shape, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
            else:
                segments = _collect_feature_edges(shape, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
        else:
            segments = _collect_slice_outline_segments(shape, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
            if len(segments) == 0:
                segments = _collect_boundary_edges(shape, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
        binary_rgba = _segments_to_binary_rgba(segments, imposed_ax_limits, screen_size)
        return binary_rgba, np.array([imposed_ax_limits[0], imposed_ax_limits[1]], dtype=float)

    # Target pixel resolution
    width_px, height_px = screen_size[0], screen_size[1]
    dpi = 100 

    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    #area = compute_area(shape_brep)
    if type(shape) != list and len(shape.faces) > 0 and not np.isclose(shape.area, 0.0):
        #write_stl_file(shape_brep, "model.stl", linear_deflection=0.1)
        #shape = trimesh.load_mesh("model.stl")

        colors = [0.0 for i in range(len(shape.faces))]
        coords = project_vertices(shape.vertices, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)

        if rendering_mode == "outline" and projection_mode in ["orthographic", "silhouette"]:
            if projection_mode == "silhouette":
                feature_segments = _collect_silhouette_edges(shape, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
            else:
                feature_segments = _collect_feature_edges(shape, view_key, projection_mode=projection_mode, orientation_basis=orientation_basis)
            if len(feature_segments) > 0:
                ax.add_collection(LineCollection(feature_segments, colors="black", linewidths=0.6, antialiased=True))
        else:
            edge_color = "none" if rendering_mode == "slice" else "b"
            antialias = False if rendering_mode == "slice" else True
            ax.tripcolor(
                coords[:,0],
                coords[:, 1],
                facecolors=colors,
                cmap="gray",
                triangles=shape.faces,
                aa=antialias,
                edgecolor=edge_color,
                shading="flat",
            )

        # for each pixel, get triangle ID and barycentric coordinates

        # for each pixel, get triangle ID and barycentric coordinates

    ax.set_aspect('equal')
    ax = plt.gca()
    if len(imposed_ax_limits) > 0:
        ax.set_xlim(imposed_ax_limits[0])
        ax.set_ylim(imposed_ax_limits[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
    #print(ax_limits)
    #fig.savefig('test.png', dpi=dpi, pad_inches=0)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
    fig.clear()
    buf.seek(0)

    img = Image.open(buf)
    img_np = np.array(img)
    #for i in range(img_np.shape[0]):
    #    for j in range(img_np.shape[1]):
    #        if ~img_np[i,j,0] > 0:
    #            print(1, end='')
    #        else:
    #            print(0, end='')
    #    print()

    if plt.fignum_exists(fig.number):
        plt.close(fig.number)

    #print(img_np)
    #outlines_np = get_outlines(img_np)
    if rendering_mode == "filled":
        return img_np, ax_limits
    if rendering_mode == "slice":
        slice_outline = get_outlines(img_np)
        return slice_outline, ax_limits
    if rendering_mode == "outline":
        mode = (projection_mode or "orthographic").lower()
        if mode == "none":
            mode = "orthographic"
        if mode == "orthographic":
            return img_np, ax_limits
        outlines_np = get_outlines(img_np)
        #im = Image.fromarray(barycentric_coords)
        #im.save("barycentric_coords.png")
        return outlines_np, ax_limits

if __name__ == '__main__':
    #model_file = os.path.join("src", "models", "brep", "cup_higher.step")
    #step_reader = STEPControl_Reader()
    #step_reader.ReadFile(model_file)
    #step_reader.TransferRoot()
    #shape_step = step_reader.Shape()
    #write_stl_file(shape_step, "model.stl", linear_deflection=0.1)
    shape = trimesh.load_mesh("../../model/model.stl")
    #print(shape.faces.shape)
    get_single_view(shape, shape.bounds.flatten(), cut_depth=0.5, rendering_mode="slice", view_key="right")
    exit()
    get_single_view(shape, shape.bounds, view_key="front")
    get_single_view(shape, shape.bounds, view_key="side")