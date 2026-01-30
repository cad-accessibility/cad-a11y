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
from copy import copy
import src.converter.plane_intersection_utils as plane_inter_utils
from src.converter.single_view_stl import get_single_view
from src.converter.juxtaposition_view_stl import get_juxtaposition_view
from src.converter.superposition_view_stl import get_superposition_view
from src.converter.side_by_side_view import get_side_view

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
                shape_before, self.bbox, 1.0, view_key, "filled"
            )
            _, ax_limits_after = get_single_view(
                shape_after, self.bbox, 1.0, view_key, "filled"
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
            "filled": "filled",
            "shaded": "filled",
            "slice": "slice",
            "cut": "slice",
        }
        return mode_mapping.get(render_mode.lower(), "outline")
    
    def _get_view_index(self, view_key):
        """Get the index for view limits array."""
        #view_keys = ["top", "front", "side"]
        view_keys = ["top", "front", "left", "bottom", "back", "right"]
        return view_keys.index(view_key) if view_key in view_keys else 0

    def _linear_interpolation(self, a, b, param):
        return (a+ param*(b-a))
    
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
        print(params)
        print(params.get("renderMode", "Outline"))
        shape_choice = params.get("shape", "after").lower()
        comparison_mode = params.get("mode", "single").lower()
        superposition_mode = params.get("superpositionMode", "outline").lower()

        view_legend = params.get("view", "top")
        view_cut = "x+"
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

        horizontal_dist = np.abs((self.view_limits[view_index][0][1] - self.view_limits[view_index][0][0]))
        vertical_dist = np.abs((self.view_limits[view_index][1][1] - self.view_limits[view_index][1][0]))
        # arrow-key stepping
        #self.view_current_camera_center[view_index][1] -= (0.5**(zoom_level+2))*vertical_dist
        if camera_move == "left":
            self.view_current_camera_center[view_index][0] -= (0.5**(zoom_level+2))*horizontal_dist
        if camera_move == "right":
            self.view_current_camera_center[view_index][0] += (0.5**(zoom_level+2))*horizontal_dist
        if camera_move == "up":
            self.view_current_camera_center[view_index][1] += (0.5**(zoom_level+2))*vertical_dist
        if camera_move == "down":
            self.view_current_camera_center[view_index][1] -= (0.5**(zoom_level+2))*vertical_dist

        translational_ax_limits = [
            [self.view_current_camera_center[view_index][0] - 0.5*horizontal_dist,
            self.view_current_camera_center[view_index][0] + 0.5*horizontal_dist],
            [self.view_current_camera_center[view_index][1] - 0.5*vertical_dist,
            self.view_current_camera_center[view_index][1] + 0.5*vertical_dist],
            ]

        imposed_zoom_ax_limits = [
            [self.view_current_camera_center[view_index][0] - 0.5**(zoom_level+1)*horizontal_dist,
            self.view_current_camera_center[view_index][0] + 0.5**(zoom_level+1)*horizontal_dist],
            [self.view_current_camera_center[view_index][1] - 0.5**(zoom_level+1)*vertical_dist,
            self.view_current_camera_center[view_index][1] + 0.5**(zoom_level+1)*vertical_dist],
            ]

        # compute scrollbar dimensions
        x_min = self.view_limits[view_index][1][0]
        x_max = self.view_limits[view_index][1][1]
        x_zoom_min = imposed_zoom_ax_limits[1][0]
        x_zoom_max = imposed_zoom_ax_limits[1][1]
        x_scroll_max = 1.0-(x_zoom_min-x_min)/(x_max-x_min)
        x_scroll_min = 1.0-(x_zoom_max-x_min)/(x_max-x_min)

        #y_min = translational_ax_limits[1][0]
        #y_max = translational_ax_limits[1][1]
        y_min = self.view_limits[view_index][0][0]
        y_max = self.view_limits[view_index][0][1]
        y_zoom_min = imposed_zoom_ax_limits[0][0]
        y_zoom_max = imposed_zoom_ax_limits[0][1]
        y_scroll_min = (y_zoom_min-y_min)/(y_max-y_min)
        y_scroll_max = (y_zoom_max-y_min)/(y_max-y_min)

        # This needs to account for the aspect ratio of the monarch
        monarch_aspect_ratio = 96.0/40.0
        if comparison_mode == "side-by-side":
            monarch_aspect_ratio = 48.0/40.0
        if horizontal_dist/vertical_dist < monarch_aspect_ratio:
            horizontal_scale_factor = monarch_aspect_ratio * (imposed_zoom_ax_limits[1][1] - imposed_zoom_ax_limits[1][0]) / (imposed_zoom_ax_limits[0][1] - imposed_zoom_ax_limits[0][0])
            #imposed_zoom_ax_limits[0][0] = max(self.view_limits[view_index][0][0], self.view_current_camera_center[view_index][0] - horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist)
            imposed_zoom_ax_limits[0][0] = self.view_current_camera_center[view_index][0] - horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist
            #imposed_zoom_ax_limits[0][1] = min(self.view_limits[view_index][0][1], self.view_current_camera_center[view_index][0] + horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist)
            imposed_zoom_ax_limits[0][1] = self.view_current_camera_center[view_index][0] + horizontal_scale_factor*0.5**(zoom_level+1)*horizontal_dist
        if horizontal_dist/vertical_dist > monarch_aspect_ratio:
            vertical_scale_factor = monarch_aspect_ratio * (imposed_zoom_ax_limits[1][1] - imposed_zoom_ax_limits[1][0]) / (imposed_zoom_ax_limits[0][1] - imposed_zoom_ax_limits[0][0])
            vertical_scale_factor = 1.0/vertical_scale_factor
            #imposed_zoom_ax_limits[1][0] = max(self.view_limits[view_index][1][0], self.view_current_camera_center[view_index][1] - vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist)
            #imposed_zoom_ax_limits[1][1] = min(self.view_limits[view_index][1][1], self.view_current_camera_center[view_index][1] + vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist)
            imposed_zoom_ax_limits[1][0] = self.view_current_camera_center[view_index][1] - vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist
            imposed_zoom_ax_limits[1][1] = self.view_current_camera_center[view_index][1] + vertical_scale_factor*0.5**(zoom_level+1)*vertical_dist
        print("zoom_level", zoom_level, + 0.5**(zoom_level+1))
        print("imposed_zoom_ax_limits")
        print(imposed_zoom_ax_limits)
        print("self.view_limits[view_index]")
        print(self.view_limits[view_index])
        
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
                imposed_ax_limits=self.view_limits[view_index],
                superposition_key=superposition_key
            )
        elif comparison_mode == "juxtaposition":
            img_array, _ = get_juxtaposition_view(
                self.shapes,
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=[],
                superposition_key=superposition_mode
            )
        if comparison_mode == "side-by-side":
            img_array, _ = get_side_view(
                self.shapes[shape_index],
                self.bbox,
                1.0 - cut_depth,
                view_name_legend,
                view_name_cut,
                render_mode,
                imposed_ax_limits_legend=self.view_limits[self._get_view_index(view_name_legend)],
                #imposed_ax_limits_cut=self.view_limits[self._get_view_index(view_name_cut)]
                imposed_ax_limits_cut=imposed_zoom_ax_limits
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
                imposed_ax_limits=imposed_zoom_ax_limits
            )
        
        print("scrollbar dimensions", x_scroll_min, x_scroll_max, y_scroll_min, y_scroll_max)

        img_array[:-1,-2,:] = [255,255,255,0]
        img_array[-2,:-1,:] = [255,255,255,0]
        img_array[:,-1,:] = [255,255,255,0]
        img_array[-1,:,:] = [255,255,255,0]
        print(int(img_array.shape[0]*x_scroll_min))
        print(int(img_array.shape[0]*x_scroll_max)+1)
        img_array[max(0,int(img_array.shape[0]*x_scroll_min)):min(int(img_array.shape[0]*x_scroll_max)+1, img_array.shape[0]),-1,:] = [0,0,0,255]
        img_array[-1, max(0, int(img_array.shape[1]*y_scroll_min)):min(int(img_array.shape[1]*y_scroll_max)+1, img_array.shape[1]),:] = [0,0,0,255]
        #img_array[int(img_array.shape[0]*x_scroll_min):int(img_array.shape[0]*x_scroll_max)+1,2,1] = 0
        #img_array[int(img_array.shape[0]*x_scroll_min):int(img_array.shape[0]*x_scroll_max)+1,2,2] = 0
        #img_array[int(img_array.shape[0]*x_scroll_min):int(img_array.shape[0]*x_scroll_max)+1,2,3] = 255
        #img_array[-1, int(img_array.shape[1]*y_scroll_min):int(img_array.shape[1]*y_scroll_max)+1,0] = 255
        #print(img_array)
        #for i in range(img_array.shape[0]):
        #    for j in range(img_array.shape[1]):
        #        if img_array[i,j,0] != 255:
        #            print(1, end='')
        #        else:
        #            print(0, end='')
        #    print()
        #print(img_array)

        return img_array


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
