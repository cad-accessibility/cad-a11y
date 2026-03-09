import os
from time import time
import polyscope as ps
from copy import copy
from trimesh import Trimesh
from trimesh.intersections import slice_mesh_plane
from trimesh.boolean import difference
from trimesh.primitives import Box
from OCC.Core.BRepFeat import BRepFeat_SplitShape
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Pln, gp_Trsf
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform, BRepBuilderAPI_MakeFace
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Display.SimpleGui import init_display
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BOPAlgo import BOPAlgo_Builder, BOPAlgo_GlueFull, BOPAlgo_Splitter
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Face, TopoDS_Iterator, TopoDS_Compound
from OCC.Core.TopoDS import topods
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GCPnts import GCPnts_TangentialDeflection
import numpy as np
import meshlib.mrmeshpy as mrmeshpy
import meshlib.mrmeshnumpy as mrmeshnumpy

def trimesh_to_meshlib_fast(tm):
    verts = np.asarray(tm.vertices, dtype=np.float32, order="C")
    faces = np.asarray(tm.faces, dtype=np.int32, order="C")

    # Create MeshLib mesh
    return mrmeshnumpy.meshFromFacesVerts(faces, verts)
    #mesh = mrmeshpy.Mesh(
    #    mrmeshpy.VertCoords(verts),         # (N,3) float32
    #    mrmeshpy.FaceVerts(faces)           # (M,3) int32
    #)
    #return mesh

def meshlib_to_trimesh_fast(mesh):
    #verts = mesh.points().toNumpy()      # shape (N,3), float32
    #faces = mesh.topology().getTriangulation().toNumpy()  # (M,3), int32
     
    verts = mrmeshnumpy.getNumpyVerts(mesh)
    faces = mrmeshnumpy.getNumpyFaces(mesh.topology)

    return Trimesh(
        vertices=verts,
        faces=faces,
        process=False  # critical for speed
    )

def sample_edge(edge: TopoDS_Edge, deflection=0.01, angle_tol=0.01):
    adaptor = BRepAdaptor_Curve(edge)
    first = adaptor.FirstParameter()
    last = adaptor.LastParameter()

    sampler = GCPnts_TangentialDeflection()
    print(first, last, deflection, angle_tol)
    sampler.Initialize(adaptor, first, last, deflection, angle_tol)

    #if not sampler.IsDone():
    #    raise RuntimeError("Sampling failed")

    points = []
    for i in range(1, sampler.NbPoints() + 1):
        pnt = sampler.Value(i)
        points.append(pnt)
        #points.append((pnt.X(), pnt.Y(), pnt.Z()))
    return points

def make_cutting_box_from_plane(plane_origin, plane_normal, bbox, extra_margin=2.0):
    # Get bounding box center
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    center = gp_Pnt((xmin + xmax)/2, (ymin + ymax)/2, (zmin + zmax)/2)
    #center = plane_origin

    # Make a big box aligned to XY
    dx = (xmax - xmin + extra_margin)
    dy = (ymax - ymin + extra_margin)
    dz = (zmax - zmin + extra_margin)

    if np.isclose(np.abs(plane_normal.X()), 1.0):
        dx -= extra_margin
        center.SetX(plane_origin.X())
        translation_vec = gp_Vec(gp_Pnt(0, dy/2, dz/2), center)
    if np.isclose(np.abs(plane_normal.Y()), 1.0):
        dy -= extra_margin
        center.SetY(plane_origin.Y())
        translation_vec = gp_Vec(gp_Pnt(dx/2, 0, dz/2), center)
    if np.isclose(np.abs(plane_normal.Z()), 1.0):
        dz -= extra_margin
        center.SetZ(plane_origin.Z())
        translation_vec = gp_Vec(gp_Pnt(dx/2, dy/2, 0), center)

    box = BRepPrimAPI_MakeBox(dx, dy, dz).Shape()

    # Translate box to center at origin
    trsf = gp_Trsf()
    trsf.SetTranslation(translation_vec)
    box = BRepBuilderAPI_Transform(box, trsf).Shape()

    return box

def compute_volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()

def compute_area(shape):
    props = GProp_GProps()
    brepgprop.SurfaceProperties(shape, props)
    return props.Mass()

def boolean_diff(shape_0, shape_1):
    # Create the cutting plane face

    print(shape_0, shape_1)
    cut_result = BRepAlgoAPI_Cut(shape_0, shape_1)
    cut_result.Build()
    
    if not cut_result.IsDone():
        return shape_0, False

    return cut_result.Shape(), True

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
    #ps.init()
    ##ps.register_surface_mesh("box", cut_box.vertices, cut_box.faces)
    #ps.register_surface_mesh("shape", shape.vertices, shape.faces)
    #ps.show()
    start_time = time()
    # TRIMESH version
    #diff = difference([shape, cut_box], use_exact=True)
    # MESHLIB version
    shape_mrmesh = trimesh_to_meshlib_fast(shape)
    cut_box_mrmesh = trimesh_to_meshlib_fast(cut_box)
    diff = meshlib_to_trimesh_fast(
        mrmeshpy.boolean( shape_mrmesh, cut_box_mrmesh,
                          
                         mrmeshpy.BooleanOperation.DifferenceAB).mesh)
    #print("diff_time", time()-start_time)
    #ps.register_surface_mesh("diff", diff.vertices, diff.faces)
    return diff, True
    return slice_mesh_plane(shape, plane_normal, plane_origin), True
    # Create the cutting plane face
    plane = gp_Pln(plane_origin, plane_normal)
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    cutting_tool = make_cutting_box_from_plane(plane_origin, plane_normal, (xmin, ymin, zmin, xmax, ymax, zmax))

    # Perform the Boolean cut
    cut_result = BRepAlgoAPI_Cut(shape, cutting_tool)
    cut_result.Build()
    
    if not cut_result.IsDone():
        return shape, False

    return cut_result.Shape(), True

def get_bbox_from_shapes(shapes):
    #bbox = Bnd_Box()
    #brepbndlib.Add(shapes[0], bbox)
    bbox = shapes[0].bounds
    print(bbox)
    #xmin_0, ymin_0, zmin_0, xmax_0, ymax_0, zmax_0 = bbox.Get()
    xmin_0, ymin_0, zmin_0 = bbox[0]
    xmax_0, ymax_0, zmax_0 = bbox[1]
    #brepbndlib.Add(shapes[1], bbox)
    bbox = shapes[1].bounds
    #xmin_1, ymin_1, zmin_1, xmax_1, ymax_1, zmax_1 = bbox.Get()
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
    #pmin = gp_Pnt(xmin, ymin, zmin)
    #pmax = gp_Pnt(xmax, ymax, zmax)
    #diagonal = pmin.Distance(pmax)
    pmin = np.array([xmin, ymin, zmin])
    pmax = np.array([xmax, ymax, zmax])
    diagonal = np.linalg.norm(pmax-pmin)
    
    if diagonal == 0:
        raise ValueError("Bounding box has zero diagonal. Cannot normalize.")

    # Step 3: Compute scaling transformation
    scale_factor = 1.0 / diagonal
    #trsf = gp_Trsf()
    #trsf.SetScale(pmin, scale_factor)  # scale about the lower corner (or use center if you prefer)
    transformed = []
    for shape in shapes:
        shape.vertices -= np.mean([pmin, pmax])
        shape.vertices *= scale_factor
        shape.vertices += np.mean([pmin, pmax])
        transformed.append(shape)

    # Step 4: Apply transformation
    #transformed = [BRepBuilderAPI_Transform(shape, trsf, True).Shape() for shape in shapes]
    return transformed

def normalize_shape_diagonal(shape):
    # Step 1: Compute bounding box
    bbox = Bnd_Box()
    #brepbndlib_Add(shape, bbox)
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    
    # Step 2: Compute diagonal length
    pmin = gp_Pnt(xmin, ymin, zmin)
    pmax = gp_Pnt(xmax, ymax, zmax)
    diagonal = pmin.Distance(pmax)
    
    if diagonal == 0:
        raise ValueError("Bounding box has zero diagonal. Cannot normalize.")

    # Step 3: Compute scaling transformation
    scale_factor = 1.0 / diagonal
    trsf = gp_Trsf()
    trsf.SetScale(pmin, scale_factor)  # scale about the lower corner (or use center if you prefer)

    # Step 4: Apply transformation
    transformed = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
    return transformed

def depth_peeling_single_depth_shapes(shapes, normal_dir: gp_Dir, depth: float):

    xmin, ymin, zmin, xmax, ymax, zmax = get_bbox_from_shapes(shapes)

    # Step 2: Project bounding box corners onto the normal vector to get min/max along normal
    corners = [
        gp_Pnt(x, y, z)
        for x in (xmin, xmax)
        for y in (ymin, ymax)
        for z in (zmin, zmax)
    ]
    normal_vec = gp_Vec(normal_dir)
    projections = [gp_Vec(gp_Pnt(0, 0, 0), pnt).Dot(normal_vec) for pnt in corners]
    min_proj = min(projections)
    max_proj = max(projections)

    # Step 3: Iterate along the normal
    d = min_proj+depth*(max_proj-min_proj)
    origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())

    cut_shape_0, success_0 = cut_shape_with_plane(shapes[0], origin, normal_dir)
    cut_shape_1, success_1 = cut_shape_with_plane(shapes[1], origin, normal_dir)
    #if success_0 and compute_volume(cut_shape_0) > 0.0 and success_1 and compute_volume(cut_shape_1) > 0.0:
    if success_0 and cut_shape_0.is_watertight and cut_shape_0.volume > 0.0 and success_1 and cut_shape_1.is_watertight and cut_shape_1.volume > 0.0:
        return [cut_shape_0, cut_shape_1]

    return shapes

#def faces_on_plane(shape, plane_origin: gp_Pnt, plane_normal: gp_Dir, tol=1e-7):
#    """
#    Returns a new OCC shape composed of all faces of `shape` that lie on the plane
#    defined by `plane_origin` and `plane_normal`.
#    """
#    compound = TopoDS_Compound()
#    builder = BRep_Builder()
#    builder.MakeCompound(compound)
#
#    exp = TopExp_Explorer(shape, TopAbs_FACE)
#    while exp.More():
#        face = topods.Face(exp.Current())
#        adaptor = BRepAdaptor_Surface(face)
#        if adaptor.GetType() == GeomAbs_Plane:
#            plane = adaptor.Plane()
#            # Compare normals
#            n = plane.Axis().Direction()
#            origin = plane.Location()
#            if n.IsParallel(plane_normal, tol):
#                if np.isclose(n.X(), 1.0) and np.abs(origin.X() - plane_origin.X()) < tol:
#                    builder.Add(compound, face)
#                if np.isclose(n.Y(), 1.0) and np.abs(origin.Y() - plane_origin.Y()) < tol:
#                    builder.Add(compound, face)
#                if np.isclose(n.Z(), 1.0) and np.abs(origin.Z() - plane_origin.Z()) < tol:
#                    builder.Add(compound, face)
#        exp.Next()
#    
#    return compound

def faces_on_plane(shape, plane_origin, plane_normal, tol=1e-3):
    start_time = time()

    plane_face_ids = []
    plane_normal = np.array(plane_normal)
    plane_normal /= np.linalg.norm(plane_normal)
    selected_points = []
    print(plane_normal)
    for i, tri in enumerate(shape.faces):
        # Compare normals
        n = copy(shape.face_normals[i])
        n /= np.linalg.norm(n)
        origin = np.mean(shape.vertices[tri], axis=0)
        if np.isclose(np.abs(np.dot(plane_normal, n)), 1, atol=tol):
            if np.isclose(np.abs(n[0]), 1.0) and np.abs(origin[0] - plane_origin[0]) < tol:
                plane_face_ids.append(tri)
                selected_points.append(shape.vertices[tri])
            if np.isclose(np.abs(n[1]), 1.0) and np.abs(origin[1] - plane_origin[1]) < tol:
                plane_face_ids.append(tri)
                selected_points.append(shape.vertices[tri])
            if np.isclose(np.abs(n[2]), 1.0) and np.abs(origin[2] - plane_origin[2]) < tol:
                plane_face_ids.append(tri)
                selected_points.append(shape.vertices[tri])
    #ps.init()
    #ps.register_point_cloud("points", np.array(selected_points).reshape(-1, 3))
    submesh = Trimesh(vertices=shape.vertices, faces=plane_face_ids)
    
    #ps.register_surface_mesh("submesh", submesh.vertices, submesh.faces)
    #ps.show()
    print("faces_on_plane time", time()-start_time)
    return submesh

def faces_on_plane_fast(shape, plane_origin, plane_normal, tol=1e-3):
    start_time = time()

    V = np.asarray(shape.vertices)
    F = np.asarray(shape.faces)
    FN = np.asarray(shape.face_normals)

    # Normalize plane normal
    n = np.asarray(plane_normal, dtype=np.float64)
    n /= np.linalg.norm(n)
    p0 = np.asarray(plane_origin, dtype=np.float64)

    # Normalize face normals
    FNn = FN / np.linalg.norm(FN, axis=1, keepdims=True)

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

    #print("faces_on_plane_fast time", time() - start_time)
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
    #normal_vec = gp_Vec(normal_dir)
    #projections = [gp_Vec(gp_Pnt(0, 0, 0), pnt).Dot(normal_vec) for pnt in corners]
    projections = [np.dot(pnt, normal_dir) for pnt in corners]
    min_proj = min(projections)
    max_proj = max(projections)

    # Step 3: Iterate along the normal
    d = min_proj+depth*(max_proj-min_proj)
    #origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
    #origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
    origin = d*normal_dir
    #print(origin)
    cut_shape, success = cut_shape_with_plane(shape, origin, normal_dir)
    #print(cut_shape)
    #ps.init()
    #ps.register_surface_mesh("shape", shape.vertices, shape.faces)
    #ps.register_surface_mesh("cut_shape", cut_shape.vertices, cut_shape.faces)
    #ps.show()
    #trimesh.intersections.slice_faces_plane

    #plane = gp_Pln(origin, normal_dir)

    #if get_section_only:
    #    # Compute section (intersection)
    #    #section = BRepAlgoAPI_Section(shape, plane, True)
    #    #section.ComputePCurveOn1(True)
    #    #section.Build()
    #    #return section.Shape()
    #    return faces_on_plane(cut_shape, origin, normal_dir)

    #if success and compute_volume(cut_shape) > 0.0:
    if success and cut_shape.area > 0.0:
        return cut_shape, origin

    return shape, origin

def depth_peeling_single_depth(shape, normal_dir: gp_Dir, depth: float):
    # Step 1: Compute bounding box
    bbox = Bnd_Box()
    #brepbndlib_Add(shape, bbox)
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

    # Step 2: Project bounding box corners onto the normal vector to get min/max along normal
    corners = [
        gp_Pnt(x, y, z)
        for x in (xmin, xmax)
        for y in (ymin, ymax)
        for z in (zmin, zmax)
    ]
    normal_vec = gp_Vec(normal_dir)
    projections = [gp_Vec(gp_Pnt(0, 0, 0), pnt).Dot(normal_vec) for pnt in corners]
    min_proj = min(projections)
    max_proj = max(projections)

    # Step 3: Iterate along the normal
    d = min_proj+depth*(max_proj-min_proj)
    origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
    cut_shape, success = cut_shape_with_plane(shape, origin, normal_dir)

    if success and compute_volume(cut_shape) > 0.0:
        return cut_shape

    return shape

def depth_peeling(shape, normal_dir: gp_Dir, step: float):
    # Step 1: Compute bounding box
    bbox = Bnd_Box()
    #brepbndlib_Add(shape, bbox)
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

    # Step 2: Project bounding box corners onto the normal vector to get min/max along normal
    corners = [
        gp_Pnt(x, y, z)
        for x in (xmin, xmax)
        for y in (ymin, ymax)
        for z in (zmin, zmax)
    ]
    normal_vec = gp_Vec(normal_dir)
    projections = [gp_Vec(gp_Pnt(0, 0, 0), pnt).Dot(normal_vec) for pnt in corners]
    min_proj = min(projections)
    max_proj = max(projections)

    # Step 3: Iterate along the normal
    d = min_proj
    cut_shapes = []
    while d <= max_proj:
        origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
        cut_shape, success = cut_shape_with_plane(shape, origin, normal_dir)
        if success and compute_volume(cut_shape) > 0.0:
            cut_shapes.append(cut_shape)
        d += step

    cut_shapes.append(shape)
    return cut_shapes

def fuse_solids_in_compounds(compounds):

    solids = []
    for compound in compounds:
        explorer = TopExp_Explorer(compound, TopAbs_SOLID)
        while explorer.More():
            solid = topods.Solid(explorer.Current())
            solids.append(solid)
            explorer.Next()

    if not solids:
        raise ValueError("No solids found in compound.")

    if len(solids) == 1:
        return solids[0]  # Nothing to fuse

    print(len(solids))

    builder = BOPAlgo_Builder()
    builder.SetGlue(BOPAlgo_GlueFull)  # Enable gluing
    for shape in solids:
        builder.AddArgument(shape)
    builder.Perform()
    fused_shape = builder.Shape()

    return fused_shape

def hatch_shape(shape, normal_dir: gp_Dir, step: float):
    # Step 1: Compute bounding box
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

    # Step 2: Project bounding box corners onto the normal vector to get min/max along normal
    corners = [
        gp_Pnt(x, y, z)
        for x in (xmin, xmax)
        for y in (ymin, ymax)
        for z in (zmin, zmax)
    ]
    normal_vec = gp_Vec(normal_dir)
    projections = [gp_Vec(gp_Pnt(0, 0, 0), pnt).Dot(normal_vec) for pnt in corners]
    min_proj = min(projections)
    max_proj = max(projections)

    # Step 3: Iterate along the normal
    d = min_proj
    splitter = BRepFeat_SplitShape(shape)
    all_shapes = []
    while d <= max_proj:
        origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
        plane = gp_Pln(origin, normal_dir)
        face_plane = BRepBuilderAPI_MakeFace(plane).Face()

        splitter = BOPAlgo_Splitter()
        splitter.AddTool(face_plane)  # add the splitting tool
        splitter.AddArgument(shape)   # the shape to split
        splitter.Perform()

        result_shape = splitter.Shape()
        all_shapes.append(result_shape)
        d += step

    fused_shape = fuse_solids_in_compounds(all_shapes)
    return fused_shape

if __name__ == "__main__":
    # Setup viewer
    display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")

    step_reader = STEPControl_Reader()
    step_reader.ReadFile(os.path.join("..", "models", "brep", "lighter.step"))
    step_reader.TransferRoot()
    shape = step_reader.Shape()
    shape = normalize_shape_diagonal(shape)
    x_normal_dir = gp_Dir(1, 0, 0)
    z_normal_dir = gp_Dir(0, 0, 1)
    y_normal_dir = gp_Dir(0, 1, 0)