import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend for thread safety
from copy import deepcopy
import numpy as np
import io, PIL
from PIL import Image
import os, json
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import trimesh
from .render_low_res import get_outlines
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox, faces_on_plane, compute_area
from .single_view_stl import (
    get_single_view,
    project_vertices,
    get_projected_feature_segments,
    get_visible_face_mask,
)

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

def get_side_view(
    shape,
    bbox,
    cut_depth=0.9,
    view_key_legend="top",
    view_key_cut="left",
    rendering_mode="filled",
    imposed_ax_limits_legend=[],
    imposed_ax_limits_cut=[],
    screen_size=[60, 40],
    projection_mode="orthographic",
):

    print("get_side_view: rendering mode", rendering_mode, "view_key_legend", view_key_legend, "view_key_cut", view_key_cut)
    #shape_brep_copy = deepcopy(shape_brep)
    # Calculate dimensions based on screen_size
    total_width = screen_size[0]
    legend_width = int(total_width / 3)
    cut_width = total_width - legend_width  # Ensures exact fit
    total_height = screen_size[1]
    
    # Cut view
    #cut_depth = 0.5
    normal_dir = views[view_key_cut]["dir"]
    #shape_brep_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape_brep, gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
    #                                                              depth=cut_depth, bbox=bbox)
    shape_cut, plane_origin = depth_peeling_single_depth_with_bbox(shape, normal_dir, depth=cut_depth, bbox=bbox)
    shape_cut = faces_on_plane(shape_cut, plane_origin, normal_dir)

    # Target pixel resolution (cut view gets 2/3 of width)
    width_px, height_px = cut_width, total_height
    dpi = 100 
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    #area = compute_area(shape_brep_cut)
    #if not np.isclose(area, 0.0):
    if type(shape_cut) != list and len(shape_cut.faces) > 0 and not np.isclose(shape_cut.area, 0.0):
        #write_stl_file(shape_brep_cut, "model.stl", linear_deflection=0.1)
        #shape = trimesh.load_mesh("model.stl")

        coords = project_vertices(shape_cut.vertices, view_key_cut, projection_mode=projection_mode)
        segments_2d = get_projected_feature_segments(
            shape_cut,
            view_key_cut,
            projection_mode=projection_mode,
            cull_hidden=(projection_mode in ["isometric", "oblique"]),
            silhouette_only=(projection_mode in ["orthographic", "oblique", "isometric"]),
        )

        if rendering_mode in ["line", "outline"]:
            if len(segments_2d) > 0:
                ax.add_collection(LineCollection(segments_2d, colors="black", linewidths=1.0, antialiaseds=False))
                ax.autoscale_view()
        else:
            cut_triangles = shape_cut.faces
            if projection_mode in ["isometric", "oblique"]:
                cut_face_mask = get_visible_face_mask(shape_cut, view_key_cut, projection_mode=projection_mode)
                if np.any(cut_face_mask):
                    cut_triangles = shape_cut.faces[cut_face_mask]
            colors = [0.0 for i in range(len(cut_triangles))]
            ax.tripcolor(
                coords[:,0],
                coords[:, 1],
                facecolors=colors,
                cmap="gray",
                triangles=cut_triangles,
                aa=False,
                edgecolor="none",
            )
            if len(segments_2d) > 0:
                ax.add_collection(LineCollection(segments_2d, colors="black", linewidths=1.0, antialiaseds=False))

    ax.set_aspect('equal')
    ax = plt.gca()
    if len(imposed_ax_limits_cut) > 0:
        ax.set_xlim(imposed_ax_limits_cut[0])
        ax.set_ylim(imposed_ax_limits_cut[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
    print(ax_limits)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
    fig.clear()
    buf.seek(0)

    img = Image.open(buf)
    img_np = np.array(img)
    #for i in range(img_np.shape[0]):
    #    for j in range(img_np.shape[1]):
    #        if img_np[i,j,0] == 255:
    #            print(1, end='')
    #        else:
    #            print(0, end='')
    #    print()

    #plt.imshow(img_np)
    #plt.show()
    if plt.fignum_exists(fig.number):
        plt.close(fig.number)

    # Right panel uses direct, explicit linework/fill rendering from this pass.
    cut_img = img_np

    # Legend - view
    #shape_brep = shape_brep_copy

    normal_dir = views[view_key_legend]["dir"]

    # Target pixel resolution (legend view gets 1/3 of width)
    width_px, height_px = legend_width, total_height
    dpi = 100 
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    #area = compute_area(shape_brep)
    if not np.isclose(shape.area, 0.0):
        # Draw legend as explicit silhouette/feature lines for stable braille output.
        legend_segments_2d = get_projected_feature_segments(
            shape,
            view_key_legend,
            projection_mode=projection_mode,
            cull_hidden=(projection_mode in ["isometric", "oblique"]),
            silhouette_only=(projection_mode in ["orthographic", "oblique", "isometric"]),
        )
        if len(legend_segments_2d) > 0:
            ax.add_collection(LineCollection(legend_segments_2d, colors="black", linewidths=1.0, antialiaseds=False))
            ax.autoscale_view()
        else:
            # Fallback for degenerate meshes where edge extraction yields no segments.
            legend_triangles = shape.faces
            if projection_mode in ["isometric", "oblique"]:
                legend_face_mask = get_visible_face_mask(shape, view_key_legend, projection_mode=projection_mode)
                if np.any(legend_face_mask):
                    legend_triangles = shape.faces[legend_face_mask]
            colors = [0.0 for i in range(len(legend_triangles))]
            coords = project_vertices(shape.vertices, view_key_legend, projection_mode=projection_mode)
            ax.tripcolor(
                coords[:,0],
                coords[:, 1],
                facecolors=colors,
                cmap="gray",
                triangles=legend_triangles,
                aa=False,
                edgecolor="none",
            )


    ax.set_aspect('equal')
    ax = plt.gca()
    if len(imposed_ax_limits_legend) > 0:
        ax.set_xlim(imposed_ax_limits_legend[0])
        ax.set_ylim(imposed_ax_limits_legend[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
    # Expand by 1 pixel on each side so the model never fills the full width,
    # ensuring the slice line is always visible at the edge.
    x_margin = (ax_limits[0][1] - ax_limits[0][0]) / legend_width
    y_margin = (ax_limits[1][1] - ax_limits[1][0]) / total_height
    ax.set_xlim(ax_limits[0][0] - x_margin, ax_limits[0][1] + x_margin)
    ax.set_ylim(ax_limits[1][0] - y_margin, ax_limits[1][1] + y_margin)
    legend_ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
    print(legend_ax_limits)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
    fig.clear()
    buf.seek(0)

    img = Image.open(buf)
    img_np = np.array(img)
    #for i in range(img_np.shape[0]):
    #    for j in range(img_np.shape[1]):
    #        if img_np[i,j,0] == 255:
    #            print(1, end='')
    #        else:
    #            print(0, end='')
    #    print()

    #plt.imshow(img_np)
    #plt.show()
    if plt.fignum_exists(fig.number):
        plt.close(fig.number)
    # Left panel (legend view) is already rendered as stable explicit linework.
    legend_img = img_np

    # Line img (must match legend width)
    width_px, height_px = legend_width, total_height
    dpi = 100
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
    ax.axis('off')

    # Add line at plane_origin in legend_img.
    line_vec = np.cross(views[view_key_cut]["dir"], views[view_key_legend]["dir"])
    plane_origin_np = np.array(plane_origin)
    line_pts = np.array([plane_origin_np - 1000.0*line_vec, plane_origin_np + 1000.0*line_vec])
    coords = project_vertices(line_pts, view_key_legend, projection_mode=projection_mode)
    ax.plot(coords[:, 0], coords[:, 1], linewidth=1.0, color=(0.0,0.0,0.0,1.0), aa=False)
    #print("line coords", coords)
    ax.set_aspect('equal')
    ax = plt.gca()
    ax.set_xlim(legend_ax_limits[0])
    ax.set_ylim(legend_ax_limits[1])
    ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
    print(ax_limits)

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
