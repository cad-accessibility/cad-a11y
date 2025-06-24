import numpy as np
import create_hatch_lines_single_depth
import os
from copy import deepcopy
import svgwrite

from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Trsf, gp_Vec, gp_Ax1
from OCC.Core.Quantity import Quantity_NOC_BLACK, Quantity_NOC_RED, Quantity_NOC_WHITE, Quantity_Color, Quantity_TOC_RGB
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Display.backend import load_pyqt5
from OCC.Display.SimpleGui import init_display


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

def create_orthographic_views(step_file, cut_depth=0.9, hatch_step=0.012):
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    myshape = step_reader.Shape()

    # add hatching lines on faces
    myshape = create_hatch_lines_single_depth.create_hatch_shape(myshape, 
        depth=cut_depth, hatch_step=hatch_step, with_sampling=False)

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
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()

        bbox = Bnd_Box()
        brepbndlib.Add(myshape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        # center bbox for projection
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(-(xmin+xmax)/2.0, -(ymin+ymax)/2.0, -(zmin+zmax)/2.0,))
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()

        algo = HLRBRep_Algo()
        algo.Add(myshape)
        algo.Projector()
        algo.Update()
        algo.Hide()

        hlr = HLRBRep_HLRToShape(algo)

        edges_projected = {
            "visible": hlr.VCompound(),
            "visible_smooth": hlr.Rg1LineVCompound(),
            "visible_seam": hlr.RgNLineVCompound(),
            "visible_outlines": hlr.OutLineVCompound(),
            "visible_iso": hlr.IsoLineVCompound(),
            #"hidden": hlr.HCompound(),
            #"hidden_smooth": hlr.Rg1LineHCompound(),
            #"hidden_seam": hlr.RgNLineHCompound(),
            #"hidden_outlines": hlr.OutLineHCompound(),
            #"hidden_iso": hlr.IsoLineHCompound(),
        }

        all_edges = []
        for edge_type_key in edges_projected.keys():
            if not edges_projected[edge_type_key] is None:
                edges_2d = sample_all_edges_projected(edges_projected[edge_type_key], views[view_key])
            else:
                edges_2d = []
            all_edges += edges_2d
        
        write_svg_lines(os.path.join("svg_views", os.path.basename(step_file).split(".")[0]+"_"+str(cut_depth)+"_"+str(hatch_step)+"_"+view_key+".svg"), 
                        all_edges, width=800, height=800)

if __name__ == '__main__':
    # check for pyqt5
    #if not load_pyqt5():
    #    raise IOError("pyqt5 required to run this test")
    create_orthographic_views(os.path.join("..", "models", "brep", "lighter.step"), cut_depth=0.9, hatch_step=0.10)
    #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    #display.DisplayShape(edges_projected["visible"], color=Quantity_NOC_BLACK, update=True)
    #display.DisplayShape(myshape, color=Quantity_NOC_BLACK, update=True)
    #start_display()