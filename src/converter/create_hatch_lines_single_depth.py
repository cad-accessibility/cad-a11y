import os
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Face
from OCC.Display.SimpleGui import init_display
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Pln, gp_Trsf
from copy import deepcopy
import plane_intersection_utils

def create_hatch_lines_and_cut_shape(shape, depth=0.5, hatch_step=0.025, cut_normal_dir=[1,0,0], hatch_normal_dir=[0,0,1], with_sampling=True):
    shape = plane_intersection_utils.normalize_shape_diagonal(shape)
    cut_shape = plane_intersection_utils.depth_peeling_single_depth(shape, gp_Dir(cut_normal_dir[0], cut_normal_dir[1], cut_normal_dir[2]), depth=depth)
    section_edges = plane_intersection_utils.get_hatch_section_edges(cut_shape, gp_Dir(hatch_normal_dir[0], hatch_normal_dir[1], hatch_normal_dir[2]), step=hatch_step)
    segment_edges = []

    if with_sampling:
        sampled_edges = [plane_intersection_utils.sample_edge(edge) for edge in section_edges]
        for edge in sampled_edges:
            line = []
            for i in range(len(edge)-1):
                line.append([deepcopy(edge[i]), deepcopy(edge[i+1])])
            segment_edges.append(line)
    else:
        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        builder.Add(compound, cut_shape)
        for l in section_edges:
            builder.Add(compound, l)
        cut_shape = compound

    return segment_edges, cut_shape

def create_hatch_lines(shape, depth=0.5, cut_normal_dir=[1,0,0], hatch_normal_dir=[0,0,1]):
    shape = plane_intersection_utils.normalize_shape_diagonal(shape)
    cut_shape = plane_intersection_utils.depth_peeling_single_depth(shape, gp_Dir(cut_normal_dir[0], cut_normal_dir[1], cut_normal_dir[2]), 0.3)
    section_edges = plane_intersection_utils.get_hatch_section_edges(cut_shape, gp_Dir(hatch_normal_dir[0], hatch_normal_dir[1], hatch_normal_dir[2]), step=0.025)
    sampled_edges = [plane_intersection_utils.sample_edge(edge) for edge in section_edges]
    segment_edges = []
    for edge in sampled_edges:
        line = []
        for i in range(len(edge)-1):
            line.append([edge[i], edge[i+1]])
        segment_edges.append(line)

    return segment_edges

    display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    display.DisplayShape(cut_shape, transparency=0.8)
    for edge in section_edges:
        display.DisplayShape(edge, color="RED", update=False)
    display.FitAll()
    start_display()
    exit()

if __name__ == "__main__":
    step_file = os.path.join("..", "models", "brep", "lighter.step")
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    shape = step_reader.Shape()

    hatch_lines = create_hatch_lines(shape)