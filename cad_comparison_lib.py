"""
CAD Comparison Library

This library provides a simple interface to generate comparison views of CAD models.
It processes STEP files and returns rendered image arrays based on specified parameters.
"""

import numpy as np
import os
from OCC.Core.STEPControl import STEPControl_Reader
from trimesh.exchange.stl import load_stl
from trimesh import Trimesh
from trimesh.repair import stitch, fill_holes, fix_inversion, fix_winding
from copy import copy, deepcopy
import src.converter.plane_intersection_utils as plane_inter_utils
from src.converter.single_view_stl import get_single_view, get_cut_faces, project_vertices
from src.converter.juxtaposition_view_stl import get_juxtaposition_view
from src.converter.superposition_view_stl import get_superposition_view
from src.converter.side_by_side_view import get_side_view
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely import union_all
from shapely.plotting import plot_polygon
from shapely import symmetric_difference
import matplotlib.pyplot as plt
import io, PIL
from PIL import Image

class CADComparisonRenderer:
    """
    Renderer for CAD model comparisons.
    """
    
    def __init__(self, before_model_path, after_model_path):
        """
        Initialize the renderer with two STEP model files.
        
        Args:
            before_model_path: Path to the "before" STEP file
            after_model_path: Path to the "after" STEP file
        """
        self.before_model_path = before_model_path
        self.after_model_path = after_model_path
        self.shapes = []
        self.bbox = None
        self.view_limits = None
        self.view_current_camera_center = []
        self.view_current_axis = -1
        self.view_current_view_limits = -1
        self.current_render_mode = None
        self.screen_size = [96,40]
        self.view_diff_mats = {}
        self.current_render = None
        self.current_ax_limits = []
        self.current_zoom_level = None
        
        # Load and normalize shapes
        self._load_models()
        
    def _load_models(self):
        """Load STL files and prepare shapes."""
        shapes = []
        for stl_file in [self.before_model_path, self.after_model_path]:
            with open(stl_file, "r") as fp:
                mesh_dict = load_stl(fp)
                mesh = Trimesh(mesh_dict["vertices"], mesh_dict["faces"], mesh_dict["face_normals"])
                print(mesh.is_watertight)
                print(mesh.is_volume)
                fill_holes(mesh)
                fix_inversion(mesh)
                fix_winding(mesh)
                #stitch(mesh)
                shapes.append(mesh)
        
        # Normalize both shapes
        shape_before, shape_after = plane_inter_utils.normalize_shapes_diagonal(shapes)
        self.shapes = [shape_before, shape_after]
        
        # Get common bounds
        xmin, ymin, zmin, xmax, ymax, zmax = plane_inter_utils.get_bbox_from_shapes(
            [shape_before, shape_after]
        )
        self.bbox = [xmin, ymin, zmin, xmax, ymax, zmax]
        
        # Calculate view limits for all views
        self._calculate_view_limits()
        self._compute_slice_graphs()
    
    def _calculate_view_limits(self):
        """Calculate axis limits for all views for both shapes."""
        #view_keys = ["top", "front", "side"]
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        rendering_modes = ["outline", "filled", "slice"]
        
        xmin, ymin, zmin, xmax, ymax, zmax = self.bbox
        
        view_limits = [
            [[xmin, xmax], [ymin, ymax]],  # top
            [[xmin, xmax], [ymin, ymax]],  # front
            [[xmin, xmax], [ymin, ymax]],  # side
            [[xmin, xmax], [ymin, ymax]],  # top
            [[xmin, xmax], [ymin, ymax]],  # front
            [[xmin, xmax], [ymin, ymax]],  # side
        ]
        self.view_current_camera_center = [
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
            [(xmin+xmax)/2.0, (ymin+ymax)/2.0],
        ]
        
        shape_before, shape_after = self.shapes
        
        for i, view_key in enumerate(view_keys):
            print("view_key", view_key)
            _, ax_limits_before = get_single_view(
                shape_before, self.bbox, 1.0, view_key, "filled", screen_size=self.screen_size
            )
            _, ax_limits_after = get_single_view(
                shape_after, self.bbox, 1.0, view_key, "filled", screen_size=self.screen_size
            )
            view_limits[i][0][0] = min(ax_limits_before[0][0], ax_limits_after[0][0])
            view_limits[i][0][1] = max(ax_limits_before[0][1], ax_limits_after[0][1])
            view_limits[i][1][0] = min(ax_limits_before[1][0], ax_limits_after[1][0])
            view_limits[i][1][1] = max(ax_limits_before[1][1], ax_limits_after[1][1])
            self.view_current_camera_center[i][0] = (view_limits[i][0][0] + view_limits[i][0][1])/2.0
            self.view_current_camera_center[i][1] = (view_limits[i][1][0] + view_limits[i][1][1])/2.0
        
        self.view_limits = np.array(view_limits)
        self.view_current_camera_center = np.array(self.view_current_camera_center)
        print("view_limits")
        print(np.array(view_limits))
        #exit()

    def _calculate_projected_view_limits(self, projection_mode):
        """Calculate per-view axis limits for a given projection mode."""
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        projected_limits = []

        for view_key in view_keys:
            all_x = []
            all_y = []
            for shape in self.shapes:
                coords = project_vertices(shape.vertices, view_key, projection_mode=projection_mode)
                if coords.shape[0] == 0:
                    continue
                all_x.append(coords[:, 0])
                all_y.append(coords[:, 1])

            if len(all_x) == 0:
                projected_limits.append([[0.0, 1.0], [0.0, 1.0]])
                continue

            xs = np.concatenate(all_x)
            ys = np.concatenate(all_y)
            projected_limits.append([
                [float(np.min(xs)), float(np.max(xs))],
                [float(np.min(ys)), float(np.max(ys))],
            ])

        return np.array(projected_limits)

    def _compute_slice_graphs(self):

        shape = copy(self.shapes[0])
        cut_depth = 0.0
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        for view_key in ["front", "top", "left"]:
            cut_percent = 0
            cut_faces_list = []
            diff_mat = np.zeros([101, 101])
            while cut_percent <= 100:
                #shape = deepcopy(self.shapes[0])
                cut_depth = cut_percent/100.0
                cut_faces = get_cut_faces(shape, view_key, cut_depth, self.bbox)
                coords = shape.vertices[:,[0,1]]
                if view_key == "top":
                    coords = cut_faces.vertices[:,[0,1]]
                if view_key == "front":
                    coords = cut_faces.vertices[:,[0,2]]
                if view_key == "left":
                    coords = cut_faces.vertices[:,[1,2]]
                if view_key == "bottom":
                    coords = cut_faces.vertices[:,[0,1]]
                    coords[:,0] *= -1
                    coords[:,1] *= -1
                if view_key == "back":
                    coords = cut_faces.vertices[:,[0,2]]
                    coords[:,0] *= -1
                if view_key == "right":
                    coords = cut_faces.vertices[:,[1,2]]
                    coords[:,0] *= -1
                triangles = [
                    Polygon(coords[face])
                    for face in cut_faces.faces
                ]
                #print([t.area for t in triangles])
                merged = unary_union(triangles)
                cut_faces_list.append(merged)
                cut_percent += 1
                #plot_polygon(merged)
                #plt.axis("equal")
                #plt.savefig("cut_depth_"+view_key+"_"+str(cut_percent)+".png")
                #plt.close()

            #print(len(cut_faces_list))
            # pairwise diff
            #cut_faces_list = list(reversed(cut_faces_list))
            for i in range(len(cut_faces_list)):
                for j in range(i+1, len(cut_faces_list)):
                    diff = symmetric_difference(cut_faces_list[i], cut_faces_list[j]).area
                    diff_mat[i][j] = diff
                    diff_mat[j][i] = diff
            #print(diff_mat[10, :])
            #print(len(cut_faces_list))
            #plt.plot(range(len(cut_faces_list)), diff_mat[10,:])
            #plt.ylim(0.0, 1.0)
            #plt.savefig("diff_mat_cut_"+view_key+".png")
            #plt.close()

            diff_mat /= np.max(diff_mat)
            self.view_diff_mats[view_key] = diff_mat
            if view_key == "top":
                self.view_diff_mats["bottom"] = np.flip(diff_mat)
            if view_key == "front":
                self.view_diff_mats["back"] = np.flip(diff_mat)
            if view_key == "left":
                self.view_diff_mats["right"] = np.flip(diff_mat)
        #exit()
    
    def _map_view_name(self, view_name):
        """Map view name from JSON format to internal format."""
        view_mapping = {
            "top": "top",
            "front": "front",
            "left": "left",
            "right": "right",
            "back": "back",
            "bottom": "bottom",
        }
        view_mapping = {
            "top": "z+",
            "front": "y-",
            "left": "x-",
            "right": "x+",
            "back": "y+",
            "bottom": "z-",
        }
        view_mapping = {
            "z+": "top",
            "y-": "front",
            "x-": "left",
            "x+": "right",
            "y+": "back",
            "z-": "bottom",
        }
        return view_mapping.get(view_name.lower(), "top")
    
    def _map_render_mode(self, render_mode):
        """Map render mode from JSON format to internal format."""
        mode_mapping = {
            "outline": "outline",
            "line": "line",
            "filled": "filled",
            "shaded": "filled",
            "slice": "slice",
            "cut": "slice",
        }
        return mode_mapping.get(render_mode.lower(), "outline")

    def _map_projection_mode(self, projection_mode):
        """Map projection mode from JSON format to internal format."""
        mode = (projection_mode or "orthographic").lower()
        mapping = {
            "orthographic": "orthographic",
            "oblique": "oblique",
            "isometric": "isometric",
            "cut": "cut",
        }
        return mapping.get(mode, "orthographic")
    
    def _get_view_index(self, view_key):
        """Get the index for view limits array."""
        #view_keys = ["top", "front", "side"]
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        return view_keys.index(view_key) if view_key in view_keys else 0

    def _linear_interpolation(self, a, b, param):
        return (a+ param*(b-a))

    def _draw_braille_cell(self, img_array, x, y, dots):
        """Draw a 2x4 braille cell (single-pixel dots) in black onto an RGBA image."""
        dot_positions = {
            1: (0, 0), 2: (0, 1), 3: (0, 2), 7: (0, 3),
            4: (1, 0), 5: (1, 1), 6: (1, 2), 8: (1, 3),
        }
        h, w = img_array.shape[0], img_array.shape[1]
        for dot in dots:
            if dot not in dot_positions:
                continue
            dx, dy = dot_positions[dot]
            px = x + dx
            py = y + dy
            if 0 <= px < w and 0 <= py < h:
                img_array[py, px, 0] = 0
                img_array[py, px, 1] = 0
                img_array[py, px, 2] = 0
                if img_array.shape[2] > 3:
                    img_array[py, px, 3] = 255

    def _draw_braille_text(self, img_array, text, x, y):
        """Draw a short braille string using 2x4 cells with 1px spacing."""
        # Grade-1 letter mappings plus simple symbol approximations for axis tokens.
        char_to_dots = {
            "x": [1, 3, 4, 6],
            "y": [1, 3, 4, 5, 6],
            "z": [1, 3, 5, 6],
            "+": [3, 4, 6],
            "-": [3, 6],
            " ": [],
        }
        cursor_x = x
        cell_advance = 3  # 2px cell width + 1px spacing
        for ch in text.lower():
            dots = char_to_dots.get(ch, [])
            if len(dots) > 0:
                self._draw_braille_cell(img_array, cursor_x, y, dots)
            cursor_x += cell_advance

    def _overlay_side_by_side_view_labels(self, img_array, left_axis, right_axis):
        """Overlay compact braille axis markers at the top of each side-by-side panel."""
        if img_array is None or len(img_array.shape) != 3:
            return
        h, w = img_array.shape[0], img_array.shape[1]
        if h < 6 or w < 12:
            return

        legend_width = int(w / 3)
        y = 1
        left_x = 1
        right_x = legend_width + 1
        self._draw_braille_text(img_array, left_axis, left_x, y)
        self._draw_braille_text(img_array, right_axis, right_x, y)

    def _overlay_view_info_box(self, img_array, axis_text):
        """Overlay a compact 7x5 top-left info box with axis text (e.g., x+)."""
        if img_array is None or len(img_array.shape) != 3:
            return
        h, w = img_array.shape[0], img_array.shape[1]
        if h < 5 or w < 7:
            return

        axis_text = (axis_text or "x+").lower()[:2]
        char_to_dots = {
            "x": [1, 3, 4, 6],
            "y": [1, 3, 4, 5, 6],
            "z": [1, 3, 5, 6],
            "+": [3, 4, 6],
            "-": [3, 6],
        }

        # Fixed size requested by user.
        box_x0 = 0
        box_y0 = 0
        box_w = min(w, 7)
        box_h = min(h, 5)
        box_x1 = box_x0 + box_w - 1
        box_y1 = box_y0 + box_h - 1

        # White background only (no outline).
        img_array[box_y0:box_y1 + 1, box_x0:box_x1 + 1, 0:3] = 255
        if img_array.shape[2] > 3:
            img_array[box_y0:box_y1 + 1, box_x0:box_x1 + 1, 3] = 255

        # Draw exactly two 2x4 glyphs with one blank column between them.
        # Layout inside 7x5 box: [margin][2px][gap][2px][margin].
        if len(axis_text) >= 1 and axis_text[0] in char_to_dots:
            self._draw_braille_cell(img_array, box_x0 + 1, box_y0 + 1, char_to_dots[axis_text[0]])
        if len(axis_text) >= 2 and axis_text[1] in char_to_dots:
            self._draw_braille_cell(img_array, box_x0 + 4, box_y0 + 1, char_to_dots[axis_text[1]])
    
    def render(self, params):
        """
        Render a view of the CAD model comparison based on provided parameters.
        
        Args:
            params: Dictionary containing:
                - view: "Top", "Front", or "Side" (case-insensitive)
                - zoom: "0", "1", "2", "3"
                - depth: Integer 0-100 representing depth percentage
                - renderMode: "Outline", "Filled"/"Shaded", or "Slice" (case-insensitive)
                - shape: (optional) "before" or "after", defaults to "after"
                - mode: (optional) "single", "superposition", or "juxtaposition", defaults to "single"
                - superpositionMode: (optional) "outline", "intersection", "difference before", "difference after"
        
        Returns:
            numpy array: RGBA image array
        
        Example:
            params = {
                "view": "Front",
                "depth": 30,
                "renderMode": "Shaded"
            }
            img_array = renderer.render(params)
        """
        # Extract and map parameters
        view_name = self._map_view_name(params.get("view", "Top"))
        depth_percent = params.get("depth", 0)
        render_mode = self._map_render_mode(params.get("renderMode", "Outline"))
        projection_mode = self._map_projection_mode(params.get("projectionMode", "orthographic"))
        effective_projection_mode = "orthographic" if projection_mode == "cut" else projection_mode
        if projection_mode == "cut":
            render_mode = "slice"
        print(params)
        print(params.get("renderMode", "Outline"))
        shape_choice = params.get("shape", "after").lower()
        comparison_mode = params.get("mode", "single").lower()
        superposition_mode = params.get("superpositionMode", "outline").lower()

        if "compose_scrollbar" in params.keys():
            compose_scrollbar = params["compose_scrollbar"]
        else:
            compose_scrollbar = False

        # Slice-graph mode should never show scrollbars.
        if comparison_mode == "slice-graph":
            compose_scrollbar = False

        active_view_limits = self.view_limits
        if effective_projection_mode in ["oblique", "isometric"]:
            active_view_limits = self._calculate_projected_view_limits(effective_projection_mode)

        # Define a per-view viewing window. When scrollbars are enabled we reserve
        # the last row/column for bars and the second-to-last row/column as spacer.
        render_screen_size = [self.screen_size[0], self.screen_size[1]]
        if compose_scrollbar:
            render_screen_size = [max(1, self.screen_size[0] - 2), max(1, self.screen_size[1] - 2)]

        view_legend = params.get("view", "top")
        view_cut = "x+"
        if comparison_mode == "side-by-side":
            # In side-by-side mode, keep the current/selected axis on the RIGHT panel.
            # The left legend panel is chosen as a consistent companion view.
            view_cut = params.get("view", "x+")
            legend_from_cut = {
                "x+": "z+",
                "y+": "x+",
                "z+": "y+",
                "x-": "z-",
                "y-": "x-",
                "z-": "y-",
            }
            view_legend = legend_from_cut.get(view_cut, "x+")
        else:
            if view_legend == "x+":
                view_cut = "y+"
            if view_legend == "y+":
                view_cut = "z+"
            if view_legend == "x-":
                view_cut = "y-"
            if view_legend == "y-":
                view_cut = "z-"
            if view_legend == "z-":
                view_cut = "x-"
        view_name_legend = self._map_view_name(view_legend)
        view_name_cut = self._map_view_name(view_cut)
        
        # Convert depth from 0-100 to 0.0-1.0 ratio
        cut_depth = depth_percent / 100.0
        
        # Select shape (0 = before, 1 = after)
        shape_index = 0 if shape_choice == "before" else 1
        
        # Get view index for limits
        view_index = self._get_view_index(view_name)
        if comparison_mode == "side-by-side":
            view_index = self._get_view_index(view_name_cut)

        zoom_level = int(params.get("zoom", "0"))
        camera_move = params.get("move_camera_center", "none")
        print(camera_move)

        horizontal_dist = np.abs((active_view_limits[view_index][0][1] - active_view_limits[view_index][0][0]))
        vertical_dist = np.abs((active_view_limits[view_index][1][1] - active_view_limits[view_index][1][0]))

        # For non-orthographic projections at zoom 0, add a small margin so the
        # projected model fits comfortably within the tactile frame.
        if effective_projection_mode in ["oblique", "isometric"] and zoom_level == 0:
            horizontal_dist *= 1.05
            vertical_dist *= 1.05
        current_center = np.array(self.view_current_camera_center[view_index], dtype=float)
        if effective_projection_mode in ["oblique", "isometric"] and zoom_level == 0:
            current_center = np.array([
                (active_view_limits[view_index][0][0] + active_view_limits[view_index][0][1]) / 2.0,
                (active_view_limits[view_index][1][0] + active_view_limits[view_index][1][1]) / 2.0,
            ])
        # arrow-key stepping
        #self.view_current_camera_center[view_index][1] -= (0.5**(zoom_level+2))*vertical_dist
        if camera_move == "left":
            current_center[0] -= (0.5**(zoom_level+2))*horizontal_dist
        if camera_move == "right":
            current_center[0] += (0.5**(zoom_level+2))*horizontal_dist
        if camera_move == "up":
            current_center[1] += (0.5**(zoom_level+2))*vertical_dist
        if camera_move == "down":
            current_center[1] -= (0.5**(zoom_level+2))*vertical_dist

        if effective_projection_mode == "orthographic":
            self.view_current_camera_center[view_index] = current_center

        translational_ax_limits = [
            [current_center[0] - 0.5*horizontal_dist,
            current_center[0] + 0.5*horizontal_dist],
            [current_center[1] - 0.5*vertical_dist,
            current_center[1] + 0.5*vertical_dist],
            ]

        imposed_zoom_ax_limits = [
            [current_center[0] - 0.5**(zoom_level+1)*horizontal_dist,
            current_center[0] + 0.5**(zoom_level+1)*horizontal_dist],
            [current_center[1] - 0.5**(zoom_level+1)*vertical_dist,
            current_center[1] + 0.5**(zoom_level+1)*vertical_dist],
            ]

        # compute scrollbar dimensions
        x_min = active_view_limits[view_index][1][0]
        x_max = active_view_limits[view_index][1][1]
        x_zoom_min = imposed_zoom_ax_limits[1][0]
        x_zoom_max = imposed_zoom_ax_limits[1][1]
        x_scroll_max = 1.0-(x_zoom_min-x_min)/(x_max-x_min)
        x_scroll_min = 1.0-(x_zoom_max-x_min)/(x_max-x_min)

        #y_min = translational_ax_limits[1][0]
        #y_max = translational_ax_limits[1][1]
        y_min = active_view_limits[view_index][0][0]
        y_max = active_view_limits[view_index][0][1]
        y_zoom_min = imposed_zoom_ax_limits[0][0]
        y_zoom_max = imposed_zoom_ax_limits[0][1]
        y_scroll_min = (y_zoom_min-y_min)/(y_max-y_min)
        y_scroll_max = (y_zoom_max-y_min)/(y_max-y_min)

        # This needs to account for the aspect ratio of the monarch
        current_aspect_ratio = render_screen_size[0]/render_screen_size[1]
        if comparison_mode == "side-by-side":
            current_aspect_ratio = 0.5*render_screen_size[0]/render_screen_size[1]
        if horizontal_dist/vertical_dist < current_aspect_ratio:
            horizontal_scale_factor = current_aspect_ratio * (imposed_zoom_ax_limits[1][1] - imposed_zoom_ax_limits[1][0]) / (imposed_zoom_ax_limits[0][1] - imposed_zoom_ax_limits[0][0])
            #imposed_zoom_ax_limits[0][0] = max(self.view_limits[view_index][0][0], self.view_current_camera_center[view_index][0] - horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist)
            imposed_zoom_ax_limits[0][0] = current_center[0] - horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist
            #imposed_zoom_ax_limits[0][1] = min(self.view_limits[view_index][0][1], self.view_current_camera_center[view_index][0] + horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist)
            imposed_zoom_ax_limits[0][1] = current_center[0] + horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist
        if horizontal_dist/vertical_dist > current_aspect_ratio:
            vertical_scale_factor = current_aspect_ratio * (imposed_zoom_ax_limits[1][1] - imposed_zoom_ax_limits[1][0]) / (imposed_zoom_ax_limits[0][1] - imposed_zoom_ax_limits[0][0])
            vertical_scale_factor = 1.0/vertical_scale_factor
            #imposed_zoom_ax_limits[1][0] = max(self.view_limits[view_index][1][0], self.view_current_camera_center[view_index][1] - vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist)
            #imposed_zoom_ax_limits[1][1] = min(self.view_limits[view_index][1][1], self.view_current_camera_center[view_index][1] + vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist)
            imposed_zoom_ax_limits[1][0] = current_center[1] - vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist
            imposed_zoom_ax_limits[1][1] = current_center[1] + vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist
        print("zoom_level", zoom_level, + 0.5**(zoom_level+1))
        print("imposed_zoom_ax_limits")
        print(imposed_zoom_ax_limits)
        print("self.view_limits[view_index]")
        print(active_view_limits[view_index])
        
        # Render based on comparison mode
        if comparison_mode == "superposition":
            superposition_keys = ["outline", "intersection", "difference before", "difference after"]
            superposition_key = superposition_mode if superposition_mode in superposition_keys else "outline"
            
            img_array = get_superposition_view(
                self.shapes,
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=active_view_limits[view_index],
                superposition_key=superposition_key,
                screen_size=render_screen_size,
                projection_mode=effective_projection_mode,
            )
        elif comparison_mode == "juxtaposition":
            img_array, _ = get_juxtaposition_view(
                self.shapes,
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=[],
                superposition_key=superposition_mode,
                screen_size=render_screen_size
            )
        if comparison_mode == "side-by-side":
            # Calculate aspect-ratio-adjusted limits for the legend view
            # Get the legend view index and base limits
            legend_view_index = self._get_view_index(view_name_legend)
            legend_limits = active_view_limits[legend_view_index].copy()
            
            # Calculate dimensions for legend view
            legend_horizontal_dist = np.abs(legend_limits[0][1] - legend_limits[0][0])
            legend_vertical_dist = np.abs(legend_limits[1][1] - legend_limits[1][0])
            legend_center_x = (legend_limits[0][0] + legend_limits[0][1]) / 2.0
            legend_center_y = (legend_limits[1][0] + legend_limits[1][1]) / 2.0
            
            # Apply same aspect ratio correction as used for cut view (0.5 aspect ratio for side-by-side)
            side_by_side_aspect_ratio = 0.5 * render_screen_size[0] / render_screen_size[1]
            
            # Adjust legend limits to match the aspect ratio of half-screen
            if legend_horizontal_dist / legend_vertical_dist < side_by_side_aspect_ratio:
                # Need to expand horizontal
                horizontal_scale_factor = side_by_side_aspect_ratio * legend_vertical_dist / legend_horizontal_dist
                adjusted_horizontal_dist = horizontal_scale_factor * legend_horizontal_dist
                imposed_legend_ax_limits = [
                    [legend_center_x - adjusted_horizontal_dist / 2.0,
                     legend_center_x + adjusted_horizontal_dist / 2.0],
                    legend_limits[1]
                ]
            else:
                # Need to expand vertical
                vertical_scale_factor = (legend_horizontal_dist / legend_vertical_dist) / side_by_side_aspect_ratio
                adjusted_vertical_dist = vertical_scale_factor * legend_vertical_dist
                imposed_legend_ax_limits = [
                    legend_limits[0],
                    [legend_center_y - adjusted_vertical_dist / 2.0,
                     legend_center_y + adjusted_vertical_dist / 2.0]
                ]
            
            img_array, _ = get_side_view(
                self.shapes[shape_index],
                self.bbox,
                1.0 - cut_depth,
                view_name_legend,
                view_name_cut,
                render_mode,
                imposed_ax_limits_legend=imposed_legend_ax_limits,
                #imposed_ax_limits_cut=self.view_limits[self._get_view_index(view_name_cut)]
                imposed_ax_limits_cut=imposed_zoom_ax_limits,
                screen_size=render_screen_size,
                projection_mode=effective_projection_mode,
            )
        else:  # single mode
            #print(self.view_limits[view_index])
            #print(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][0])
            #print(
            #        [self._linear_interpolation(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][0]),
            #         self._linear_interpolation(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][1])]
            #)
            img_array, _ = get_single_view(
                self.shapes[shape_index],
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=imposed_zoom_ax_limits,
                screen_size=render_screen_size,
                projection_mode=effective_projection_mode,
            )
            self.current_cut_depth = 1.0-cut_depth
            self.view_current_axis = view_name
            self.current_render_mode = render_mode
            self.view_current_view_limits = imposed_zoom_ax_limits
        self.current_ax_limits = copy(imposed_zoom_ax_limits)
        self.current_zoom_level = zoom_level
        
        # COMPOSITION STAGE
        if compose_scrollbar:
            draw_w, draw_h = render_screen_size[0], render_screen_size[1]
            full_img = np.full((self.screen_size[1], self.screen_size[0], img_array.shape[2]), 255, dtype=np.uint8)
            if full_img.shape[2] > 3:
                full_img[:, :, 3] = 255
            copy_h = min(draw_h, img_array.shape[0], full_img.shape[0])
            copy_w = min(draw_w, img_array.shape[1], full_img.shape[1])
            full_img[:copy_h, :copy_w, :] = img_array[:copy_h, :copy_w, :]
            img_array = full_img

            # Keep a 1px spacer before the scrollbars for tactile/readability separation.
            img_array[:-1,-2,:] = [255,255,255,0]
            img_array[-2,:-1,:] = [255,255,255,0]
            img_array[:,-1,:] = [255,255,255,0]
            img_array[-1,:,:] = [255,255,255,0]
            y0 = max(0, int(draw_h * x_scroll_min))
            y1 = min(int(draw_h * x_scroll_max) + 1, draw_h)
            x0 = max(0, int(draw_w * y_scroll_min))
            x1 = min(int(draw_w * y_scroll_max) + 1, draw_w)
            img_array[y0:y1, -1, :] = [0,0,0,255]
            img_array[-1, x0:x1, :] = [0,0,0,255]

        if "compose_scrollbar" in params.keys():
            compose_slice_graph = params["compose_slicegraph"]
        else:
            compose_slice_graph = False
        if compose_slice_graph:
            # Optionally use an anchored slice graph location while the user explores.
            slice_graph_locked = bool(params.get("slicegraph_locked", False))
            graph_view_name = view_name
            graph_depth_percent = depth_percent
            if slice_graph_locked:
                graph_view_name = self._map_view_name(params.get("slicegraph_view", params.get("view", "Top")))
                graph_depth_percent = int(params.get("slicegraph_depth", depth_percent))

            # take correct view_diff_mat
            graph_depth_percent = max(0, min(100, graph_depth_percent))
            graph_cut_depth = graph_depth_percent / 100.0
            cut_position_int = int(100.0 * (1.0 - graph_cut_depth))
            view_diff_mat = self.view_diff_mats[graph_view_name][cut_position_int]

            # The marker always reflects the current (live) slice position,
            # even when the graph data is locked to an anchor depth.
            # NOTE: marker_position_int is NOT inverted like cut_position_int because
            # the image has already been flipped; in the flipped image, left=0% and
            # right=100%, so the marker index should increase with depth.
            current_depth_percent = max(0, min(100, depth_percent))
            marker_position_int = int(current_depth_percent)

            # Render line-graph edge-to-edge in the graph band width.
            width_px, height_px = self.screen_size[0], self.screen_size[1]
            graph_height_px = 10
            dpi = 100 

            fig = plt.figure(figsize=(width_px / dpi, graph_height_px / dpi), dpi=dpi)
            #fig = plt.figure(figsize=(1080 / dpi, 920 / dpi), dpi=dpi)
            ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
            ax.axis('off')
            # Render graph strokes as a single pixel (no anti-aliasing expansion).
            ax.plot(range(len(view_diff_mat)), view_diff_mat, aa=False, c="black", lw=.15)
            ax = plt.gca()
            #if len(imposed_ax_limits) > 0:

            ax.set_xlim((0, len(view_diff_mat)))
            ax.set_ylim((0, 1))

            #ax_limits = np.array([ax.get_xlim(), ax.get_ylim()])
            #print(ax_limits)
            fig.savefig('test.png', dpi=dpi, pad_inches=0)

            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
            fig.clear()
            buf.seek(0)

            img = Image.open(buf)
            img_np = np.array(img)
            img_np = np.flip(img_np, axis=1)

            # Enforce a 1px slice marker column in the graph bitmap.
            # This avoids anti-aliased or multi-pixel marker thickness from plotting.
            if img_np.shape[1] > 0 and len(view_diff_mat) > 1:
                marker_col = int(round(marker_position_int * (img_np.shape[1] - 1) / (len(view_diff_mat) - 1)))
                marker_col = max(0, min(img_np.shape[1] - 1, marker_col))
                img_np[:, marker_col, :] = [0, 0, 0, 255]
            print("Slice graph")
            print(view_diff_mat)
            print(img_np.shape)
            for i in range(img_np.shape[0]):
                for j in range(img_np.shape[1]):
                    if ~img_np[i,j,0] > 0:
                        print(1, end='')
                    else:
                        print(0, end='')
                print()
            print()

            # Compose graph without an outline box. Keep only a horizontal divider
            # above the graph so the model and graph are clearly separated.
            graph_top = max(0, img_array.shape[0] - graph_height_px)
            graph_left = 0
            graph_h, graph_w = img_np.shape[0], img_np.shape[1]
            graph_bottom = min(img_array.shape[0], graph_top + graph_h)
            graph_right = min(img_array.shape[1], graph_left + graph_w)

            divider_row = max(0, graph_top - 1)
            img_array[divider_row, :, :] = [0, 0, 0, 255]

            copy_h = graph_bottom - graph_top
            copy_w = graph_right - graph_left
            if copy_h > 0 and copy_w > 0:
                img_array[graph_top:graph_bottom, graph_left:graph_right, :] = img_np[:copy_h, :copy_w, :]

        if comparison_mode == "side-by-side":
            self._overlay_side_by_side_view_labels(img_array, view_legend, view_cut)

        show_view_info_box = bool(params.get("show_view_info_box", False))
        if show_view_info_box and comparison_mode in ["single", "slice-graph"]:
            axis_text = params.get("view", "top").lower()
            self._overlay_view_info_box(img_array, axis_text)

            

        return img_array

    def init_device(self, device):
        if device.kind == "monarch":
            self.screen_size = [96, 40]
        if device.kind == "dotpad":
            self.screen_size = [60, 40]


# Convenience function for simple usage
def render_cad_comparison(before_model_path, after_model_path, params):
    """
    Convenience function to render a CAD comparison in one call.
    
    Args:
        before_model_path: Path to the "before" STEP file
        after_model_path: Path to the "after" STEP file
        params: Dictionary with rendering parameters (see CADComparisonRenderer.render)
    
    Returns:
        numpy array: RGBA image array
    
    Example:
        img_array = render_cad_comparison(
            "models/cup.step",
            "models/cup_higher.step",
            {"view": "Front", "depth": 30, "renderMode": "Shaded"}
        )
    """
    renderer = CADComparisonRenderer(before_model_path, after_model_path)
    return renderer.render(params)
