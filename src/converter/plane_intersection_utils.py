import os
from time import time
from copy import copy
from trimesh import Trimesh
from trimesh.primitives import Box
import numpy as np
import meshlib.mrmeshpy as mrmeshpy
import meshlib.mrmeshnumpy as mrmeshnumpy

from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

def trimesh_to_meshlib_fast(tm):
    verts = np.asarray(tm.vertices, dtype=np.float32, order="C")
    faces = np.asarray(tm.faces, dtype=np.int32, order="C")

    return mrmeshnumpy.meshFromFacesVerts(faces, verts)

def meshlib_to_trimesh_fast(mesh):
    verts = mrmeshnumpy.getNumpyVerts(mesh)
    faces = mrmeshnumpy.getNumpyFaces(mesh.topology)

    return Trimesh(
        vertices=verts,
        faces=faces,
        process=False  # critical for speed
    )

def compute_area(shape):
    props = GProp_GProps()
    brepgprop.SurfaceProperties(shape, props)
    return props.Mass()

def cut_shape_with_plane(shape, plane_origin, plane_normal):
    bbox = copy(shape.bounds)
    center = np.mean(bbox, axis=0)
    bbox -= center
    bbox *= 1.1
    bbox += center
    # scale bbox
    if np.isclose(plane_normal[0], 1.0):
        lower_left = bbox[0]
        lower_left[0] = plane_origin[0]
        cut_box = Box(bounds=[lower_left, bbox[1]])
    if np.isclose(plane_normal[0], -1.0):
        upper_right = bbox[1]
        upper_right[0] = plane_origin[0]
        cut_box = Box(bounds=[bbox[0], upper_right])
    if np.isclose(plane_normal[1], 1.0):
        lower_left = bbox[0]
        lower_left[1] = plane_origin[1]
        cut_box = Box(bounds=[lower_left, bbox[1]])
    if np.isclose(plane_normal[1], -1.0):
        upper_right = bbox[1]
        upper_right[1] = plane_origin[1]
        cut_box = Box(bounds=[bbox[0], upper_right])
    if np.isclose(plane_normal[2], -1.0):
        upper_right = bbox[1]
        upper_right[2] = plane_origin[2]
        cut_box = Box(bounds=[bbox[0], upper_right])
    if np.isclose(plane_normal[2], 1.0):
        lower_left = bbox[0]
        lower_left[2] = plane_origin[2]
        cut_box = Box(bounds=[lower_left, bbox[1]])
    start_time = time()
    # TRIMESH version
    #diff = difference([shape, cut_box], use_exact=True)
    # MESHLIB version
    shape_mrmesh = trimesh_to_meshlib_fast(shape)
    cut_box_mrmesh = trimesh_to_meshlib_fast(cut_box)
    diff = meshlib_to_trimesh_fast(
        mrmeshpy.boolean( shape_mrmesh, cut_box_mrmesh,
                          
                         mrmeshpy.BooleanOperation.DifferenceAB).mesh)
    return diff, True

def get_bbox_from_shapes(shapes):
    bbox = shapes[0].bounds
    xmin_0, ymin_0, zmin_0 = bbox[0]
    xmax_0, ymax_0, zmax_0 = bbox[1]
    bbox = shapes[1].bounds
    xmin_1, ymin_1, zmin_1 = bbox[0]
    xmax_1, ymax_1, zmax_1 = bbox[1]
    xmin = min(xmin_0, xmin_1)
    ymin = min(ymin_0, ymin_1)
    zmin = min(zmin_0, zmin_1)
    xmax = max(xmax_0, xmax_1)
    ymax = max(ymax_0, ymax_1)
    zmax = max(zmax_0, zmax_1)

    return xmin, ymin, zmin, xmax, ymax, zmax

def normalize_shapes_diagonal(shapes):
    # Step 1: Compute bounding box
    xmin, ymin, zmin, xmax, ymax, zmax = get_bbox_from_shapes(shapes)
    
    # Step 2: Compute diagonal length
    pmin = np.array([xmin, ymin, zmin])
    pmax = np.array([xmax, ymax, zmax])
    diagonal = np.linalg.norm(pmax-pmin)
    
    if diagonal == 0:
        raise ValueError("Bounding box has zero diagonal. Cannot normalize.")

    # Step 3: Compute scaling transformation
    scale_factor = 1.0 / diagonal
    transformed = []
    for shape in shapes:
        shape.vertices -= np.mean([pmin, pmax])
        shape.vertices *= scale_factor
        shape.vertices += np.mean([pmin, pmax])
        transformed.append(shape)

    return transformed

def faces_on_plane_fast(shape, plane_origin, plane_normal, tol=1e-3):

    V = np.asarray(shape.vertices)
    F = np.asarray(shape.faces)
    FN = np.asarray(shape.face_normals)

    # Normalize plane normal
    n = np.asarray(plane_normal, dtype=np.float64)
    n /= np.linalg.norm(n)
    p0 = np.asarray(plane_origin, dtype=np.float64)

    # Normalize face normals (handle zero-length normals)
    norms = np.linalg.norm(FN, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-10, norms)
    FNn = FN / norms

    # --- 1. Parallel normal test ---
    parallel = np.abs(FNn @ n) > (1 - tol)

    # --- 2. Triangle centroids ---
    origins = V[F].mean(axis=1)

    # --- 3. Axis-aligned plane checks (your original logic) ---
    mask = np.zeros(len(F), dtype=bool)

    # X planes
    mx = np.isclose(np.abs(FNn[:, 0]), 1.0, atol=tol)
    mx &= np.abs(origins[:, 0] - p0[0]) < tol
    mask |= mx

    # Y planes
    my = np.isclose(np.abs(FNn[:, 1]), 1.0, atol=tol)
    my &= np.abs(origins[:, 1] - p0[1]) < tol
    mask |= my

    # Z planes
    mz = np.isclose(np.abs(FNn[:, 2]), 1.0, atol=tol)
    mz &= np.abs(origins[:, 2] - p0[2]) < tol
    mask |= mz

    # Combine with parallelism constraint
    final_mask = parallel & mask

    plane_face_ids = F[final_mask]

    submesh = Trimesh(
        vertices=V,
        faces=plane_face_ids,
        process=False
    )

    return submesh

def depth_peeling_single_depth_with_bbox(shape, normal_dir, depth: float, bbox):

    xmin, ymin, zmin, xmax, ymax, zmax = bbox

    # Step 2: Project bounding box corners onto the normal vector to get min/max along normal
    corners = [
        #gp_Pnt(x, y, z)
        np.array([x, y, z])
        for x in (xmin, xmax)
        for y in (ymin, ymax)
        for z in (zmin, zmax)
    ]
    projections = [np.dot(pnt, normal_dir) for pnt in corners]
    min_proj = min(projections)
    max_proj = max(projections)

    # Step 3: Iterate along the normal
    d = min_proj+depth*(max_proj-min_proj)
    origin = d*normal_dir
    cut_shape, success = cut_shape_with_plane(shape, origin, normal_dir)

    if success and cut_shape.area > 0.0:
        return cut_shape, origin

    return shape, origin

if __name__ == "__main__":
    # Setup viewer
    print("test")

