import os
from time import time
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Pln, gp_Trsf
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
from OCC.Display.SimpleGui import init_display
from OCC.Core.TopTools import TopTools_ListIteratorOfListOfShape
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.TopoDS import TopoDS_Edge
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GCPnts import GCPnts_TangentialDeflection
import numpy as np

def sample_edge(edge: TopoDS_Edge, deflection=0.01, angle_tol=0.01):
    adaptor = BRepAdaptor_Curve(edge)
    first = adaptor.FirstParameter()
    last = adaptor.LastParameter()

    sampler = GCPnts_TangentialDeflection()
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

    if np.isclose(plane_normal.X(), 1.0):
        dx -= extra_margin
        center.SetX(plane_origin.X())
        translation_vec = gp_Vec(gp_Pnt(0, dy/2, dz/2), center)
    if np.isclose(plane_normal.Y(), 1.0):
        dy -= extra_margin
        center.SetY(plane_origin.Y())
        translation_vec = gp_Vec(gp_Pnt(dx/2, 0, dz/2), center)
    if np.isclose(plane_normal.Z(), 1.0):
        dz -= extra_margin
        center.SetZ(plane_origin.Z())
        translation_vec = gp_Vec(gp_Pnt(dx/2, dy/2, 0), center)

    box = BRepPrimAPI_MakeBox(dx, dy, dz).Shape()

    # Translate box to center at origin
    trsf = gp_Trsf()
    #trsf.SetTranslation(gp_Vec(gp_Pnt(dx/2, dy/2, -dz), center))
    trsf.SetTranslation(translation_vec)
    box = BRepBuilderAPI_Transform(box, trsf).Shape()

    return box

def compute_volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    #brepgprop_VolumeProperties(shape, props)
    return props.Mass()

def cut_shape_with_plane(shape, plane_origin, plane_normal):
    # Create the cutting plane face
    plane = gp_Pln(plane_origin, plane_normal)
    bbox = Bnd_Box()
    #brepbndlib_Add(shape, bbox)
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    cutting_tool = make_cutting_box_from_plane(plane_origin, plane_normal, (xmin, ymin, zmin, xmax, ymax, zmax))
    #face = BRepBuilderAPI_MakeFace(plane).Face()
    #display.DisplayShape(cutting_tool, transparency=0.8)
    #display.DisplayShape(shape, transparency=0.8)
    #display.FitAll()
    #start_display()
    #exit()

    # Perform the Boolean cut
    cut_result = BRepAlgoAPI_Cut(shape, cutting_tool)
    cut_result.Build()
    
    if not cut_result.IsDone():
        #raise RuntimeError("Cutting operation failed.")
        return shape, False

    return cut_result.Shape(), True

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
    all_section_edges = []
    d = min_proj
    cut_shapes = []
    while d <= max_proj:
        origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
        cut_plane = gp_Pln(origin, normal_dir)
        cut_shape, success = cut_shape_with_plane(shape, origin, normal_dir)
        if success and compute_volume(cut_shape) > 0.0:
            cut_shapes.append(cut_shape)
        d += step

    cut_shapes.append(shape)
    return cut_shapes

def get_hatch_section_edges(shape, normal_dir: gp_Dir, step: float):
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
    all_section_edges = []
    d = min_proj
    while d <= max_proj:
        origin = gp_Pnt(normal_vec.Scaled(d).X(), normal_vec.Scaled(d).Y(), normal_vec.Scaled(d).Z())
        plane = gp_Pln(origin, normal_dir)
        face_plane = BRepBuilderAPI_MakeFace(plane).Face()

        section = BRepAlgoAPI_Section(shape, face_plane, True)
        section.ComputePCurveOn1(True)
        section.Approximation(True)
        section.Build()

        if not section.IsDone():
            print(f"Section failed at d = {d}")
            d += step
            continue

        edges = []
        edge_iter = TopTools_ListIteratorOfListOfShape(section.SectionEdges())
        while edge_iter.More():
            edge = edge_iter.Value()
            edges.append(edge)
            edge_iter.Next()

        all_section_edges += edges
        d += step

    return all_section_edges

if __name__ == "__main__":
    # Setup viewer
    display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")

    # Create a shape (e.g., a box)
    #shape = BRepPrimAPI_MakeBox(100, 100, 100).Shape()
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(os.path.join("..", "models", "brep", "lighter.step"))
    step_reader.TransferRoot()
    shape = step_reader.Shape()
    shape = normalize_shape_diagonal(shape)
    x_normal_dir = gp_Dir(1, 0, 0)
    z_normal_dir = gp_Dir(0, 0, 1)
    y_normal_dir = gp_Dir(0, 1, 0)

    all_section_edges = []

    #cut_shapes = depth_peeling(shape, x_normal_dir, step=0.025)
    cut_shapes = [shape]
    for i, cut_shape in enumerate(cut_shapes):
        print(i, len(cut_shapes))
        all_section_edges += get_hatch_section_edges(cut_shape, z_normal_dir, step=0.025)

    print(all_section_edges)

    for edge in all_section_edges:
        sampled_edge = sample_edge(edge)
        print(sampled_edge)
        #exit()

    for cut_shape in cut_shapes:
        display.DisplayShape(cut_shape, transparency=0.8)
    for edge in all_section_edges:
        display.DisplayShape(edge, color="RED", update=False)
    display.FitAll()
    start_display()
    exit()

    # Display original shape and intersection curves
    display.DisplayShape(shape, transparency=0.8)
    for edge in section_edges:
        display.DisplayShape(edge, color="RED", update=False)

    display.FitAll()
    start_display()
    exit()



    # Create a cutting plane
    origin = gp_Pnt(0, 0, 50)
    plane = gp_Pln(origin, normal)
    face_plane = BRepBuilderAPI_MakeFace(plane).Face()

    # Perform intersection
    section = BRepAlgoAPI_Section(shape, face_plane, True)
    section.ComputePCurveOn1(True)
    section.Approximation(True)
    section.Build()

    if not section.IsDone():
        raise RuntimeError("Section operation failed")

    # Get result as edges (intersection curves)
    section_edges = []

    edge_iter = TopTools_ListIteratorOfListOfShape(section.SectionEdges())
    while edge_iter.More():
        edge = edge_iter.Value()
        #edge = it.Value()
        section_edges.append(edge)
        edge_iter.Next()

    # Display original shape and intersection curves
    display.DisplayShape(shape, transparency=0.8)
    for edge in section_edges:
        display.DisplayShape(edge, color="RED", update=False)

    display.FitAll()
    start_display()
