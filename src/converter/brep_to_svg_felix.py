import numpy as np
import create_hatch_lines_single_depth
import os
from copy import deepcopy

from OCC.Core.gp import gp_Pnt, gp_Dir,gp_Ax2, gp_Trsf, gp_Vec, gp_Ax1
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.Quantity import Quantity_NOC_BLACK, Quantity_NOC_RED, Quantity_NOC_WHITE, Quantity_Color, Quantity_TOC_RGB
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Face
from OCC.Core.ShapeAnalysis import ShapeAnalysis_Surface, shapeanalysis_GetFaceUVBounds, shapeanalysis

from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.BRepClass import BRepClass_FaceClassifier
from OCC.Core.TopAbs import TopAbs_ON, TopAbs_IN, TopAbs_OUT

from OCC.Display.backend import load_pyqt5
from OCC.Display.SimpleGui import init_display
from PyQt5.QtCore import QTimer

import svgwrite

def get_bbox(lines):
    pts = []
    for line in lines:
        pts += line
    pts = np.array(pts)
    return [np.min(pts[:, 0]), np.max(pts[:, 0]), np.min(pts[:, 1]), np.max(pts[:, 1])]

def write_svg_lines(filename, lines, width=500, height=500, stroke_width=1.0):
    if len(lines) == 0:
        x_min, x_max, y_min, y_max = 0, 100, 0, 100
    else:
        x_min, x_max, y_min, y_max = get_bbox(lines)

    scale_factor = np.min([width/(x_max-x_min), height/(y_max-y_min)])

    dwg = svgwrite.Drawing(filename,
        size=(f"{width}px", f"{height}px"),
    )
    dwg = svgwrite.Drawing(filename, size=(f"{width}px", f"{height}px"))
    
    for line in lines:
        copied_line = deepcopy(line)
        for i in range(len(line)):
            copied_line[i] = [scale_factor*(line[i][0]-x_min), scale_factor*(line[i][1]-y_min)]
        dwg.add(dwg.polyline(points=copied_line, stroke='black', stroke_width=stroke_width, fill="none"))
    
    dwg.save()

# check for pyqt5
#if not load_pyqt5():
#    raise IOError("pyqt5 required to run this test")


def sample_edge(edge, view, num_samples=20):
    adaptor = BRepAdaptor_Curve(edge)
    first = adaptor.FirstParameter()
    last = adaptor.LastParameter()
    
    # Sample parameters
    params = np.linspace(first, last, num_samples)
    points_2d = [[adaptor.Value(u).X(), adaptor.Value(u).Y()] for u in params]

    return points_2d

# Loop through edges in a shape
def sample_all_edges_projected(shape, view, num_samples=20):
    edge_explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    all_edge_polylines = []

    while edge_explorer.More():
        edge = edge_explorer.Current()
        polyline = sample_edge(edge, view, num_samples)
        if polyline:
            all_edge_polylines.append(polyline)
        edge_explorer.Next()

    return all_edge_polylines

# Step 2: Sample UV lines and evaluate 3D points
def generate_hatch_lines_on_surface(surf, trsf=None, umin=0.0, umax=1.0, vmin=0.0, vmax=1.0, hatch_spacing=0.05, samples_per_line=50):
    lines_3d = []
    v = vmin
    while v <= vmax:
        points_3d = []
        for i in range(samples_per_line + 1):
            u = umin + i * (umax - umin) / samples_per_line
            pnt = gp_Pnt(0,0,0)
            #pnt = surf.D0(u, v)
            surf.D0(u, v, pnt)
            if not trsf is None:
                pnt.Transform(trsf)
            points_3d.append([pnt.X(), pnt.Y(), pnt.Z()])
        lines_3d.append(np.array(points_3d))
        v += hatch_spacing
    return lines_3d

def is_point_in_face(point: gp_Pnt, face, tolerance=1e-6):
    classifier = BRepClass_FaceClassifier()
    classifier.Perform(face, point, tolerance)
    state = classifier.State()
    return state in (TopAbs_IN, TopAbs_ON)

def get_hatching_lines(shape):

    face_explorer = TopExp_Explorer(shape, TopAbs_FACE)

    hatching_lines = []
    while face_explorer.More():
        face = face_explorer.Current()
        surf = BRep_Tool.Surface(face)
        umin, umax, vmin, vmax = shapeanalysis.GetFaceUVBounds(face)

        face_hatch_lines = generate_hatch_lines_on_surface(surf, None, umin=umin, umax=umax, vmin=vmin, vmax=vmax, 
                                                           hatch_spacing=2.0, samples_per_line=20)

        for line in face_hatch_lines:
            if len(line) < 2:
                continue 
            #print(line)
            edge_count = 0
            segment = []
            for i in range(len(line) - 1):
                p1 = gp_Pnt(line[i][0], line[i][1], line[i][2])
                p2 = gp_Pnt(line[i+1][0], line[i+1][1], line[i+1][2])
                if not (is_point_in_face(p1, face, tolerance=1e-1) and is_point_in_face(p2, face,tolerance=1e-1)):
                    if edge_count > 0 and i< len(line)-1:
                        hatching_lines.append(segment)
                        segment = []
                        edge_count = 0
                    continue
                if p1.Distance(p2) < 1e-6:
                    continue
                segment.append([p1, p2])
                edge_count += 1
            if edge_count == 0:
                continue
            hatching_lines.append(segment)
        face_explorer.Next()

    return hatching_lines

def add_hatching_lines(shape):

    face_explorer = TopExp_Explorer(shape, TopAbs_FACE)
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, shape)


    wires = []
    while face_explorer.More():
        #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
        #display.View.SetBackgroundColor(Quantity_Color(0.8, 0.8, 0.9, Quantity_TOC_RGB))
        #display.Repaint()
        face = face_explorer.Current()
        loc = face.Location()
        surf = BRep_Tool.Surface(face)
        #umin, umax, vmin, vmax = shapeanalysis_GetFaceUVBounds(face)
        umin, umax, vmin, vmax = shapeanalysis.GetFaceUVBounds(face)

        #trsf = loc.Transformation()
        trsf = None
        #surf = surface_handle.GetObject()
        face_hatch_lines = generate_hatch_lines_on_surface(surf, trsf, umin=umin, umax=umax, vmin=vmin, vmax=vmax, 
                                                           hatch_spacing=2.0, samples_per_line=10)
        #display.EraseAll()
        #display.SetBackgroundColor((1.0, 1.0, 1.0))  # white background, for example
        for line in face_hatch_lines:
            if len(line) < 2:
                continue  # skip degenerate cases
            #print(line)
            wire_builder = BRepBuilderAPI_MakeWire()
            segment_ended = False
            edge_count = 0
            for i in range(len(line) - 1):
                p1 = gp_Pnt(line[i][0], line[i][1], line[i][2])
                p2 = gp_Pnt(line[i+1][0], line[i+1][1], line[i+1][2])
                if not (is_point_in_face(p1, face) and is_point_in_face(p2, face)):
                    if edge_count > 0 and i< len(line)-1:
                        wire = wire_builder.Wire()
                        wires.append(wire)
                        builder.Add(compound, wire)
                        wire_builder = BRepBuilderAPI_MakeWire()
                        edge_count = 0
                    continue
                if p1.Distance(p2) < 1e-6:
                    continue
                e_builder = BRepBuilderAPI_MakeEdge(p1, p2)
                #if not e_builder.IsDone():
                #    print("Edge construction failed.")
                #    continue  # skip this segment
                edge = e_builder.Edge()
                #print(line[i], line[i+1])
                edge_count += 1
                wire_builder.Add(edge)
            if edge_count == 0:
                continue
            wire = wire_builder.Wire()
            #display.DisplayShape(wire, color=Quantity_NOC_BLACK, update=True)
            wires.append(wire)
            builder.Add(compound, wire)
        #display.DisplayShape(face, color=Quantity_NOC_BLACK, update=True)
        #display.DisplayShape(BRepBuilderAPI_MakeFace(surf, 1e-6).Face(), color=Quantity_NOC_BLACK, update=True)
        #start_display()
            #builder.Add(compound, face)
        face_explorer.Next()

    return compound

def get_visibility_checker(shape, tolerance=1e-6):

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, tolerance)
    return intersector

def print_point(p):
    return print(str(p.X()) +", "+str(p.Y()) +", "+str(p.Z()))

def is_point_visible(p, visibility_checker, projector, tolerance=1e-6):
    print("is_point_visible")
    print_point(p)

    p_2d = projector.Project(p)
    #return p_2d, True
    line_3d = projector.Shoot(p_2d[0], p_2d[1])
    
    #print(line_3d.Direction().X(), line_3d.Direction().Y(), line_3d.Direction().Z())
    #print(line_3d.Location().X(), line_3d.Location().Y(), line_3d.Location().Z())
    try:
        visibility_checker.Perform(line_3d, -1e6, 1e6)  # can also use +/-inf
    except:
        return p_2d, False

    if visibility_checker.NbPnt() == 0:
        return p_2d, False
    for i in range(1, visibility_checker.NbPnt()+1):
        print_point(visibility_checker.Pnt(i))
        print(p.Distance(visibility_checker.Pnt(i)))
    intersection_point = visibility_checker.Pnt(1)  # 1-based index
    if p.Distance(intersection_point) < tolerance:
        return p_2d, True
    return p_2d, False
    

def create_orthographic_views(step_file, cut_depth=0.9, hatch_step=0.012):
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    myshape = step_reader.Shape()

    # add hatching lines on faces
    #myshape = add_hatching_lines(myshape)
    #hatching_lines = get_hatching_lines(myshape)
    hatching_lines, myshape = create_hatch_lines_single_depth.create_hatch_lines_and_cut_shape(myshape, 
        depth=cut_depth, hatch_step=hatch_step, with_sampling=False)
    #print(hatching_lines)

    # UNCOMMENT for debug visualization
    #builder = BRep_Builder()
    #compound = TopoDS_Compound()
    #builder.MakeCompound(compound)
    #builder.Add(compound, myshape)
    #for l in hatching_lines:
    #    for seg in l:
    #        edge = BRepBuilderAPI_MakeEdge(seg[0], seg[1]).Edge()
    #        builder.Add(compound, edge)
    #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    #display.DisplayShape(compound, transparency=0.8, color=Quantity_NOC_BLACK, update=True)
    #start_display()
    #exit()

    views = {
        "top": {
            "eye": gp_Pnt(0, 0, -1000),
            "dir": gp_Dir(0, 0, 1)
        },
        "front": {
            "eye": gp_Pnt(0, -1000, 0),
            "dir": gp_Dir(0, 1, 0)
        },
        "side": {
            "eye": gp_Pnt(-1000, 0, 0),
            "dir": gp_Dir(1, 0, 0)
        }
    }

    for view_key in views.keys():
        print("view_key", view_key)

        trsf = gp_Trsf()
        axis = gp_Ax1(gp_Pnt(0, 0, 0), views[view_key]["dir"])  # Y-axis
        trsf.SetRotation(axis, -0.5 * 3.141592653589793)  # -90 degrees in radians
        ##trsf.SetTranslation(gp_Vec(0, 0, 50))  # X=0, Y=0, Z=50
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()

        for i in range(len(hatching_lines)):
            for l in range(len(hatching_lines[i])):
                for p in range(len(hatching_lines[i][l])):
                    hatching_lines[i][l][p].Transform(trsf)

        #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
        #builder = BRep_Builder()
        #compound = TopoDS_Compound()
        #builder.MakeCompound(compound)
        #builder.Add(compound, myshape)
        #for l in hatching_lines:
        #    for seg in l:
        #        edge = BRepBuilderAPI_MakeEdge(seg[0], seg[1]).Edge()
        #        builder.Add(compound, edge)
        #display.DisplayShape(compound, transparency=0.8, color=Quantity_NOC_BLACK, update=True)
        #display.FitAll()
        #start_display()
        #exit()
        #continue

        algo = HLRBRep_Algo()
        algo.Add(myshape)
        algo.Update()
        algo.Hide()

        hlr = HLRBRep_HLRToShape(algo)
        proj = algo.Projector()
        #print(proj.Direction())
        #eye = proj.Location()  # gp_Pnt
        #print(eye.X(), eye.Y(), eye.Z())

        edges_projected = {
            "visible": hlr.VCompound(),
            #"hidden": hlr.HCompound(),
            "visible_smooth": hlr.Rg1LineVCompound(),
            #"hidden_smooth": hlr.Rg1LineHCompound(),
            "visible_seam": hlr.RgNLineVCompound(),
            #"hidden_seam": hlr.RgNLineHCompound(),
            "visible_outlines": hlr.OutLineVCompound(),
            #"hidden_outlines": hlr.OutLineHCompound(),
            "visible_iso": hlr.IsoLineVCompound(),
            #"hidden_iso": hlr.IsoLineHCompound(),
        }

        #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
        #display.DisplayShape(edges_projected["visible"], color=Quantity_NOC_BLACK, update=True)
        #display.DisplayShape(myshape, color=Quantity_NOC_BLACK, update=True)
        #start_display()

        visible_hatching_lines = []
        seg_counter = 0
        # Visibility check for hatching lines
        last_p_2d = None
        visibility_checker = get_visibility_checker(myshape)
        for i, hatch_line in enumerate(hatching_lines):
            print(i, len(hatching_lines))
            visible_hatch_line = []
            for hatch_seg in hatch_line:
                seg_counter += 1
                p0_2d, p0_vis = is_point_visible(hatch_seg[0], visibility_checker, proj, tolerance=1e-5)
                p1_2d, p1_vis = is_point_visible(hatch_seg[1], visibility_checker, proj, tolerance=1e-5)
                if (p0_vis and p1_vis) or (p0_vis or p1_vis):
                    #visible_hatching_lines.append([p0_2d, p1_2d])
                    visible_hatch_line.append([p0_2d[0], p0_2d[1]])
                elif len(visible_hatch_line) > 0:
                    visible_hatch_line.append([last_p_2d[0], last_p_2d[1]])
                    visible_hatching_lines.append(visible_hatch_line)
                    visible_hatch_line = []
                last_p_2d = p1_2d
            if len(visible_hatch_line) > 0:
                visible_hatching_lines.append(visible_hatch_line)

        #print(visible_hatching_lines)
        all_edges = []
        for edge_type_key in edges_projected.keys():
            if not edges_projected[edge_type_key] is None:
                edges_2d = sample_all_edges_projected(edges_projected[edge_type_key], views[view_key])
            else:
                edges_2d = []
            all_edges += edges_2d
        
            #write_svg_lines(view_key+"_"+edge_type_key+".svg", edges_2d)
        write_svg_lines(os.path.join("svg_views", os.path.basename(step_file).split(".")[0]+"_"+str(cut_depth)+"_"+view_key+".svg"), all_edges+visible_hatching_lines, 
                        width=800, height=800)

if __name__ == '__main__':
    create_orthographic_views(os.path.join("..", "models", "brep", "cut_cube.step"), cut_depth=0.9, hatch_step=0.01)