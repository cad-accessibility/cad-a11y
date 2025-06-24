import os
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Face
from OCC.Display.SimpleGui import init_display
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Pln, gp_Trsf
from copy import deepcopy
import plane_intersection_utils

def create_hatch_shape(shape, depth=0.5, hatch_step=0.025, cut_normal_dir=[1,0,0], hatch_normal_dir=[0,0,1], with_sampling=True):
    shape = plane_intersection_utils.normalize_shape_diagonal(shape)
    cut_shape = plane_intersection_utils.depth_peeling_single_depth(shape, gp_Dir(cut_normal_dir[0], cut_normal_dir[1], cut_normal_dir[2]), depth=depth)
    fused_shape = plane_intersection_utils.hatch_shape(cut_shape, gp_Dir(hatch_normal_dir[0], hatch_normal_dir[1], hatch_normal_dir[2]), step=hatch_step)
    return fused_shape

if __name__ == "__main__":
    step_file = os.path.join("..", "models", "brep", "lighter.step")
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    shape = step_reader.Shape()
