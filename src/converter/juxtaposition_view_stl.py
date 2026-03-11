import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend for thread safety
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

def get_juxtaposition_view(shapes, bbox, cut_depth=0.9, view_key="top", rendering_mode="outline", 
                           imposed_ax_limits=[],
                           superposition_key="intersection",
                           screen_size=[96,40]):

    normal_dir = views[view_key]["dir"]
    shape_brep_0, plane_origin_0 = depth_peeling_single_depth_with_bbox(shapes[0], gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
                                                                  depth=cut_depth, bbox=bbox)
    shape_brep_1, plane_origin_1 = depth_peeling_single_depth_with_bbox(shapes[1], gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
                                                                  depth=cut_depth, bbox=bbox)
    # TODO:
    #if rendering_mode == "slice":
    #    shape_brep_0 = faces_on_plane(shape_brep_0, plane_origin_0, normal_dir)
    #    shape_brep_1 = faces_on_plane(shape_brep_1, plane_origin_1, normal_dir)

    # Target pixel resolution
    width_px, height_px = screen_size[0], screen_size[1]
    dpi = 100 
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    area_0 = compute_area(shape_brep_0)
    area_1 = compute_area(shape_brep_1)

    coords_0 = []
    coords_1 = []
    colors_0 = []
    colors_1 = []

    if not np.isclose(area_0, 0.0):
        write_stl_file(shape_brep_0, "model.stl", linear_deflection=0.1)
        shape = trimesh.load_mesh("model.stl")
        triangles_0 = shape.faces

        colors_0 = [0.0 for i in range(len(shape.faces))]
        coords_0 = shape.vertices[:,[0,1]]
        if view_key == "front":
            coords_0 = shape.vertices[:,[0,2]]
        if view_key == "side":
            coords_0 = shape.vertices[:,[1,2]]

    if not np.isclose(area_1, 0.0):
        write_stl_file(shape_brep_1, "model.stl", linear_deflection=0.1)
        shape = trimesh.load_mesh("model.stl")
        triangles_1 = shape.faces

        colors_1 = [0.0 for i in range(len(shape.faces))]
        coords_1 = shape.vertices[:,[0,1]]
        if view_key == "front":
            coords_1 = shape.vertices[:,[0,2]]
        if view_key == "side":
            coords_1 = shape.vertices[:,[1,2]]

    bounds_0 = [np.min(coords_0[:, 0]), np.max(coords_0[:, 0])]
    bounds_1 = [np.min(coords_1[:, 0]), np.max(coords_1[:, 0])]
    offset_dist = abs(bounds_0[1]-bounds_1[0])
    coords_0[:, 0] -= 1.1*0.5*offset_dist
    coords_1[:, 0] += 1.1*0.5*offset_dist

    ax.tripcolor(coords_0[:,0], coords_0[:, 1], facecolors=colors_0, cmap="gray", triangles=triangles_0, aa=True)
    ax.tripcolor(coords_1[:,0], coords_1[:, 1], facecolors=colors_1, cmap="gray", triangles=triangles_1, aa=True)

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