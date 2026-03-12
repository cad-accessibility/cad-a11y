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
from matplotlib.collections import LineCollection
import trimesh
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


def project_vertices(vertices, view_key, projection_mode="orthographic"):
    """Project 3D vertices to 2D for a given view/projection mode."""
    v = np.asarray(vertices)
    x = v[:, 0]
    y = v[:, 1]
    z = v[:, 2]

    # Local camera-frame components: u (horizontal), v2 (vertical), d (depth).
    if view_key == "top":
        u, v2, d = x, y, z
    elif view_key == "front":
        u, v2, d = x, z, y
    elif view_key == "left":
        u, v2, d = y, z, x
    elif view_key == "bottom":
        u, v2, d = -x, -y, -z
    elif view_key == "back":
        u, v2, d = -x, z, -y
    elif view_key == "right":
        u, v2, d = -y, z, -x
    else:
        u, v2, d = x, y, z

    mode = (projection_mode or "orthographic").lower()
    if mode == "oblique":
        # Cabinet-style oblique projection.
        alpha = np.deg2rad(45.0)
        k = 0.5
        u2 = u + k * d * np.cos(alpha)
        v3 = v2 + k * d * np.sin(alpha)
        return np.column_stack((u2, v3))

    if mode == "isometric":
        # Isometric in local camera coordinates.
        cos30 = np.cos(np.deg2rad(30.0))
        sin30 = np.sin(np.deg2rad(30.0))
        u2 = (u - d) * cos30
        v3 = v2 + (u + d) * sin30
        return np.column_stack((u2, v3))

    # Orthographic default.
    return np.column_stack((u, v2))


def _get_projection_rows(projection_mode="orthographic"):
    """Return 2D projection rows in local (u, v, d) coordinates."""
    mode = (projection_mode or "orthographic").lower()
    if mode == "oblique":
        alpha = np.deg2rad(45.0)
        k = 0.5
        return np.array([
            [1.0, 0.0, k * np.cos(alpha)],
            [0.0, 1.0, k * np.sin(alpha)],
        ], dtype=float)
    if mode == "isometric":
        cos30 = np.cos(np.deg2rad(30.0))
        sin30 = np.sin(np.deg2rad(30.0))
        return np.array([
            [cos30, 0.0, -cos30],
            [sin30, 1.0, sin30],
        ], dtype=float)
    return np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ], dtype=float)


def _get_local_basis_world(view_key):
    """Map local (u, v, d) basis vectors to world coordinates for each view."""
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


def get_view_direction_world(view_key, projection_mode="orthographic"):
    """Get camera-to-object viewing direction in world coordinates."""
    rows = _get_projection_rows(projection_mode)
    view_local = np.cross(rows[0], rows[1])
    norm = np.linalg.norm(view_local)
    if np.isclose(norm, 0.0):
        view_local = np.array([0.0, 0.0, 1.0], dtype=float)
    else:
        view_local = view_local / norm

    e_u, e_v, e_d = _get_local_basis_world(view_key)
    view_world = view_local[0] * e_u + view_local[1] * e_v + view_local[2] * e_d
    world_norm = np.linalg.norm(view_world)
    if np.isclose(world_norm, 0.0):
        return views.get(view_key, views["top"])["dir"]
    return view_world / world_norm


def get_visible_face_mask(shape, view_key, projection_mode="orthographic"):
    """Return a boolean mask for front-facing faces for the active camera."""
    if shape is None or len(shape.faces) == 0:
        return np.zeros((0,), dtype=bool)
    view_dir = get_view_direction_world(view_key, projection_mode=projection_mode)
    face_normals = shape.face_normals
    return np.einsum("ij,j->i", face_normals, view_dir) < 0.0


def get_projected_feature_segments(
    shape,
    view_key,
    projection_mode="orthographic",
    feature_angle_deg=1.0,
    cull_hidden=False,
    silhouette_only=False,
):
    """Return 2D line segments for boundary/sharp edges, removing coplanar triangulation edges."""
    if shape is None or len(shape.faces) == 0:
        return np.zeros((0, 2, 2), dtype=float)

    feature_edges = set()

    # Orthographic tactile line mode: keep only true silhouettes and open boundaries.
    # This avoids many extra lines from curved-surface triangulation.
    if silhouette_only:
        visible_faces = get_visible_face_mask(shape, view_key, projection_mode=projection_mode)

        try:
            for pair, edge in zip(shape.face_adjacency, shape.face_adjacency_edges):
                f0 = int(pair[0])
                f1 = int(pair[1])
                if visible_faces[f0] != visible_faces[f1]:
                    feature_edges.add(tuple(sorted((int(edge[0]), int(edge[1])))))
        except Exception:
            pass

        # Keep mesh boundaries if present.
        try:
            boundary_edges = shape.edges_boundary
            for edge in boundary_edges:
                feature_edges.add(tuple(sorted((int(edge[0]), int(edge[1])))))
        except Exception:
            pass

        if len(feature_edges) == 0:
            # Fallback to open-boundary-only approximation if adjacency data is unavailable.
            try:
                boundary_edges = shape.edges_boundary
                for edge in boundary_edges:
                    feature_edges.add(tuple(sorted((int(edge[0]), int(edge[1])))))
            except Exception:
                pass

        edge_idx = np.array(list(feature_edges), dtype=int) if len(feature_edges) > 0 else np.zeros((0, 2), dtype=int)
        coords_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode)
        return coords_2d[edge_idx] if edge_idx.shape[0] > 0 else np.zeros((0, 2, 2), dtype=float)

    # Keep sharp adjacency edges only (face normal change above threshold).
    try:
        angle_threshold = np.deg2rad(feature_angle_deg)
        adjacency_edges = shape.face_adjacency_edges
        adjacency_angles = shape.face_adjacency_angles
        sharp_mask = adjacency_angles > angle_threshold
        for edge in adjacency_edges[sharp_mask]:
            feature_edges.add(tuple(sorted((int(edge[0]), int(edge[1])))))
    except Exception:
        pass

    # Keep mesh boundary edges (if any open boundaries exist).
    try:
        boundary_edges = shape.edges_boundary
        for edge in boundary_edges:
            feature_edges.add(tuple(sorted((int(edge[0]), int(edge[1])))))
    except Exception:
        pass

    # Fallback: if extraction failed, use unique edges.
    if len(feature_edges) == 0:
        for edge in shape.edges_unique:
            feature_edges.add(tuple(sorted((int(edge[0]), int(edge[1])))))

    edge_idx = np.array(list(feature_edges), dtype=int)

    if cull_hidden and edge_idx.shape[0] > 0:
        visible_faces = get_visible_face_mask(shape, view_key, projection_mode=projection_mode)
        edge_to_faces = {}

        # Build edge -> adjacent faces map from face adjacency.
        try:
            for pair, edge in zip(shape.face_adjacency, shape.face_adjacency_edges):
                edge_key = tuple(sorted((int(edge[0]), int(edge[1]))))
                edge_to_faces[edge_key] = [int(pair[0]), int(pair[1])]
        except Exception:
            pass

        # Add boundary edges if present.
        try:
            for edge, face_idx in zip(shape.edges_sorted, shape.edges_face):
                edge_key = tuple(sorted((int(edge[0]), int(edge[1]))))
                if face_idx >= 0 and edge_key not in edge_to_faces:
                    edge_to_faces[edge_key] = [int(face_idx)]
        except Exception:
            pass

        visible_edge_mask = np.zeros(edge_idx.shape[0], dtype=bool)
        for i, edge in enumerate(edge_idx):
            edge_key = tuple(sorted((int(edge[0]), int(edge[1]))))
            faces = edge_to_faces.get(edge_key, [])
            if len(faces) == 0:
                # Keep unknown edges to avoid dropping geometry unexpectedly.
                visible_edge_mask[i] = True
                continue
            visible_edge_mask[i] = any(
                0 <= f < len(visible_faces) and visible_faces[f] for f in faces
            )
        edge_idx = edge_idx[visible_edge_mask]

    coords_2d = project_vertices(shape.vertices, view_key, projection_mode=projection_mode)
    return coords_2d[edge_idx]

def get_cut_faces(shape, view_key, cut_depth, bbox):
    normal_dir = views[view_key]["dir"]
    shape_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    shape_faces = faces_on_plane_fast(shape_cut, plane_origin, normal_dir)
    #print(shape_cut.area, shape_faces.area, plane_origin, bbox)
    return shape_faces

def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="filled", imposed_ax_limits=[], screen_size=[96,40], projection_mode="orthographic"):

    shape = copy(shape)
    print("rendering mode", rendering_mode, "view key", view_key)
    print("cut depth", cut_depth)
    #cut_depth = 0.5
    normal_dir = views[view_key]["dir"]
    if rendering_mode == "slice":
        #shape_brep, plane_origin = depth_peeling_single_depth_with_bbox(shape_brep, gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
        #                                                              depth=cut_depth, bbox=bbox)
        #shape_brep = faces_on_plane(shape_brep, plane_origin, normal_dir)
        shape, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
        shape = faces_on_plane_fast(shape, plane_origin, normal_dir)

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

        coords = project_vertices(shape.vertices, view_key, projection_mode=projection_mode)

        # Compute feature/silhouette segments once for modes that need linework.
        segments_2d = []
        if rendering_mode in ["line", "outline", "filled"]:
            segments_2d = get_projected_feature_segments(
                shape,
                view_key,
                projection_mode=projection_mode,
                cull_hidden=(projection_mode in ["isometric", "oblique"]),
                silhouette_only=(projection_mode in ["orthographic", "oblique", "isometric"]),
            )

        if rendering_mode in ["line", "outline"]:
            if len(segments_2d) > 0:
                ax.add_collection(LineCollection(segments_2d, colors="black", linewidths=1.0, antialiaseds=False))
                ax.autoscale_view()
        else:
            triangles = shape.faces
            # Always cull hidden faces for raster fills to avoid back-face overlap
            # artifacts (especially in orthographic outline extraction).
            face_mask = get_visible_face_mask(shape, view_key, projection_mode=projection_mode)
            if np.any(face_mask):
                triangles = shape.faces[face_mask]
            colors = [0.0 for i in range(len(triangles))]
            ax.tripcolor(
                coords[:,0],
                coords[:, 1],
                facecolors=colors,
                cmap="gray",
                triangles=triangles,
                aa=False,
                edgecolor="none",
                shading="flat",
            )
            # Draw silhouette/feature edges on top of the filled shape so the
            # outline is explicitly rasterized — otherwise it depends on which
            # triangles happen to cover pixel centres, giving a ragged boundary.
            if rendering_mode == "filled" and len(segments_2d) > 0:
                ax.add_collection(LineCollection(segments_2d, colors="black", linewidths=1.0, antialiaseds=False))

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
    for i in range(img_np.shape[0]):
        for j in range(img_np.shape[1]):
            if ~img_np[i,j,0] > 0:
                print(1, end='')
            else:
                print(0, end='')
        print()

    if plt.fignum_exists(fig.number):
        plt.close(fig.number)

    #print(img_np)
    if rendering_mode in ["filled", "slice", "line", "outline"]:
        return img_np, ax_limits

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
