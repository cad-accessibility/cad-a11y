import numpy as np
import io, PIL
from PIL import Image
import os, json
import matplotlib.pyplot as plt
import trimesh
from .render_low_res import get_outlines
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox, faces_on_plane, compute_area

from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Extend.DataExchange import write_stl_file
from OCC.Core.gp import gp_Pnt, gp_Dir 

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

def get_single_view(shape_brep, bbox, cut_depth=0.9, view_key="top", rendering_mode="filled", imposed_ax_limits=[]):

    normal_dir = views[view_key]["dir"]
    shape_brep, plane_origin = depth_peeling_single_depth_with_bbox(shape_brep, gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
                                                                  depth=cut_depth, bbox=bbox)
    if rendering_mode == "slice":
        shape_brep = faces_on_plane(shape_brep, plane_origin, normal_dir)

    width_px, height_px = 64, 40
    dpi = 100 
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    area = compute_area(shape_brep)
    if not np.isclose(area, 0.0):
        write_stl_file(shape_brep, "model.stl", linear_deflection=0.1)
        shape = trimesh.load_mesh("model.stl")

        colors = [0.0 for i in range(len(shape.faces))]
        coords = shape.vertices[:,[0,1]]
        if view_key == "front":
            coords = shape.vertices[:,[0,2]]
        if view_key == "side":
            coords = shape.vertices[:,[1,2]]
        ax.tripcolor(coords[:,0], coords[:, 1], facecolors=colors, cmap="gray", triangles=shape.faces)

    ax.set_aspect('equal')
    ax = plt.gca()
    if len(imposed_ax_limits) > 0:
        ax.set_xlim(imposed_ax_limits[0])
        ax.set_ylim(imposed_ax_limits[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
    fig.clear()
    buf.seek(0)

    img = Image.open(buf)
    img_np = np.array(img)
    #plt.imshow(img_np)
    #plt.show()
    if plt.fignum_exists(fig.number):
        plt.close(fig.number)

    # extract outline
    #print(img_np)
    if rendering_mode in ["filled", "slice"]:
        return img_np, ax_limits
    if rendering_mode == "outline":
        outlines_np = get_outlines(img_np)
        return outlines_np, ax_limits

if __name__ == '__main__':
    model_file = os.path.join("src", "models", "brep", "cup_higher.step")
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(model_file)
    step_reader.TransferRoot()
    shape_step = step_reader.Shape()
    write_stl_file(shape_step, "model.stl", linear_deflection=0.1)
    shape = trimesh.load_mesh("model.stl")
    print(shape.faces.shape)
    get_single_view(shape, shape.bounds, view_key="top")
    exit()
    get_single_view(shape, shape.bounds, view_key="front")
    get_single_view(shape, shape.bounds, view_key="side")