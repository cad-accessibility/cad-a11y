"""
CAD Comparison Library

This library provides a simple interface to generate comparison views of CAD models.
It processes STEP files and returns rendered image arrays based on specified parameters.
"""

import numpy as np
import os
from OCC.Core.STEPControl import STEPControl_Reader
import src.converter.plane_intersection_utils as plane_inter_utils
from src.converter.single_view_stl import get_single_view
from src.converter.juxtaposition_view_stl import get_juxtaposition_view
from src.converter.superposition_view_stl import get_superposition_view


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
        
        # Load and normalize shapes
        self._load_models()
        
    def _load_models(self):
        """Load STEP files and prepare shapes."""
        shapes = []
        for step_file in [self.before_model_path, self.after_model_path]:
            step_reader = STEPControl_Reader()
            step_reader.ReadFile(step_file)
            step_reader.TransferRoot()
            shapes.append(step_reader.Shape())
        
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
        
        self.view_limits = np.array(view_limits)
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

        zoom_quadrant = 1
        zoom_level = int(params.get("zoom", "0"))
        imposed_zoom_ax_limits = [[0, 0.4], [0, 0.4]]
        
        # Convert depth from 0-100 to 0.0-1.0 ratio
        cut_depth = depth_percent / 100.0
        
        # Select shape (0 = before, 1 = after)
        shape_index = 0 if shape_choice == "before" else 1
        
        # Get view index for limits
        view_index = self._get_view_index(view_name)
        
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
        else:  # single mode
            print(self.view_limits[view_index])
            print(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][0])
            print(
                    [self._linear_interpolation(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][0]),
                     self._linear_interpolation(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][1])]
            )
            img_array, _ = get_single_view(
                self.shapes[shape_index],
                self.bbox,
                1.0 - cut_depth,
                view_name,
                render_mode,
                imposed_ax_limits=[
                    [self._linear_interpolation(self.view_limits[view_index][0][0], self.view_limits[view_index][0][1], imposed_zoom_ax_limits[0][0]),
                     self._linear_interpolation(self.view_limits[view_index][0][0], self.view_limits[view_index][0][1], imposed_zoom_ax_limits[0][1])],
                    [self._linear_interpolation(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][0]),
                     self._linear_interpolation(self.view_limits[view_index][1][0], self.view_limits[view_index][1][1], imposed_zoom_ax_limits[1][1])]]
            )
        
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
