import matplotlib
import sys
matplotlib.use('Agg')  # Use non-GUI backend for thread safety
from copy import deepcopy
import numpy as np
import io, PIL
from PIL import Image
import os, json
import matplotlib.pyplot as plt
import trimesh
from .render_low_res import get_outlines
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox, faces_on_plane, compute_area, faces_on_plane_fast
from .single_view_stl import get_single_view

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

def get_side_view(shape, bbox, cut_depth=0.9, view_key_legend="top", view_key_cut="left",  rendering_mode="filled", imposed_ax_limits_legend=[], imposed_ax_limits_cut=[], screen_size=[60, 40], projection_mode="none"):

    #shape_brep_copy = deepcopy(shape_brep)
    # Calculate dimensions based on screen_size
    total_width = screen_size[0]
    legend_width = int(total_width / 3)
    cut_width = total_width - legend_width  # Ensures exact fit
    total_height = screen_size[1]
    
    requested_projection_mode = (projection_mode or "orthographic").lower()
    if requested_projection_mode == "none":
        requested_projection_mode = "orthographic"

    # Cut view
    #cut_depth = 0.5
    normal_dir = views[view_key_cut]["dir"]
    #shape_brep_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape_brep, gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
    #                                                              depth=cut_depth, bbox=bbox)
    shape_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    if rendering_mode == "slice":
        shape_cut = faces_on_plane_fast(shape_cut, plane_origin, normal_dir)

    # Target pixel resolution (cut view gets 2/3 of width)
    cut_render_mode = rendering_mode
    if requested_projection_mode == "cut":
        cut_render_mode = "slice"
        requested_projection_mode = "orthographic"

    cut_img, _ = get_single_view(
        shape_cut,
        bbox,
        cut_depth=0.0,
        view_key=view_key_cut,
        rendering_mode=cut_render_mode,
        imposed_ax_limits=imposed_ax_limits_cut,
        screen_size=[cut_width, total_height],
        projection_mode=requested_projection_mode,
    )

    # Legend - view
    #shape_brep = shape_brep_copy

    legend_img, _ = get_single_view(
        shape,
        bbox,
        cut_depth=0.0,
        view_key=view_key_legend,
        rendering_mode="outline",
        imposed_ax_limits=imposed_ax_limits_legend,
        screen_size=[legend_width, total_height],
        projection_mode="orthographic",
    )
    legend_ax_limits = np.array(imposed_ax_limits_legend) if len(imposed_ax_limits_legend) > 0 else np.array([[0.0, float(legend_width)], [0.0, float(total_height)]])

    # Line img (must match legend width)
    width_px, height_px = legend_width, total_height
    dpi = 100 
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')
    # Add line at plane_origin in legend_img
    line_vec = np.cross(views[view_key_cut]["dir"], views[view_key_legend]["dir"])

    plane_origin_np = np.array(plane_origin)
    #print("plane_origin_np", plane_origin_np)
    #print("line_vec", line_vec)
    line_pts = np.array([plane_origin_np - 1000.0*line_vec, plane_origin_np + 1000.0*line_vec])
    #print("view_limits_legend", imposed_ax_limits_legend)
    #print("line pts", line_pts)
    if view_key_legend == "top":
        coords = line_pts[:,[0,1]]
    if view_key_legend == "front":
        coords = line_pts[:,[0,2]]
    if view_key_legend == "left":
        coords = line_pts[:,[1,2]]
    if view_key_legend == "bottom":
        coords = line_pts[:,[0,1]]
        coords[:,0] *= -1
        coords[:,1] *= -1
    if view_key_legend == "back":
        coords = line_pts[:,[0,2]]
        coords[:,0] *= -1
    if view_key_legend == "right":
        coords = line_pts[:,[1,2]]
        coords[:,0] *= -1
    ax.plot(coords[:, 0], coords[:, 1], linewidth=0.5, color=(0.0,0.0,0.0,1.0), aa=False)
    #print("line coords", coords)
    ax.set_aspect('equal')
    ax = plt.gca()
    ax.set_xlim(legend_ax_limits[0])
    ax.set_ylim(legend_ax_limits[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
    fig.clear()
    buf.seek(0)

    img = Image.open(buf)
    img_np = np.array(img)
    for i in range(img_np.shape[0]):
        for j in range(img_np.shape[1]):
            if img_np[i,j,0] == 0:
                legend_img[i,j,0] = 0
            #    print(1, end='')
            #else:
            #    print(0, end='')
        #print()
    
    #line_img = img_np

    #plt.imshow(img_np)
    #plt.show()
    if plt.fignum_exists(fig.number):
        plt.close(fig.number)

    combined_view = np.zeros([total_height, total_width, 4], dtype=legend_img.dtype) 
    combined_view[:, :legend_width] = legend_img
    combined_view[:, legend_width:] = cut_img
    #for i in range(combined_view.shape[0]):
    #    for j in range(combined_view.shape[1]):
    #        if combined_view[i,j,0] == 255:
    #            print(1, end='')
    #        else:
    #            print(combined_view[i, j])
    #            print(0, end='')
    #    print()

    return combined_view, -1

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