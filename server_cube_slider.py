#!/usr/bin/env python3
"""
Web server to receive commands/changes from the accessible 3D viewer.
This server logs all user interactions from the HTML interface.
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime
import json
import os
import io
import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image
import numpy as np
from cad_comparison_lib import CADComparisonRenderer
from braille_display import send_to_braille_display, BrailleDisplayError, _connect
from src.converter.render_low_res import save_binary_array_as_vector_pdf
from src.converter.single_view_stl import views as SLICE_VIEWS
from src.converter.single_view_stl import get_single_view
from src.converter.single_view_stl import project_vertices
from src.converter.plane_intersection_utils import (
    depth_peeling_single_depth_with_bbox,
    faces_on_plane_fast,
)

import asyncio
import bleak
import threading
import godice
import serial
from serial.tools import list_ports

app = Flask(__name__)
CORS(app)  # Enable CORS to allow requests from the HTML file

# Store commands in memory (could be replaced with database)
commands_log = []
model_list = []
model_name_list = []
DEFAULT_MODEL_BASENAME = "lego_3002"
current_model_name = 0

# Determine repo root and resolve default coffee mug model path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
discovered_models = []
for f in os.listdir(os.path.join(REPO_ROOT, "model")):
    if f.lower().endswith(".stl"):
        model_name = os.path.splitext(f)[0]
        model_path = os.path.join(REPO_ROOT, "model", f)
        discovered_models.append((model_name.lower(), model_name, model_path))

discovered_models.sort(key=lambda item: item[0])
for _lower_name, model_name, model_path in discovered_models:
    model_name_list.append(model_name)
    model_list.append(model_path)

if DEFAULT_MODEL_BASENAME in model_name_list:
    current_model_name = model_name_list.index(DEFAULT_MODEL_BASENAME)

#model_list = ["coffee", "second cup", "teaparty"]
renderer_dict = {}
export_renderer_dict = {}

#_coffee_candidates = [
#    os.path.join(REPO_ROOT, "model", first_stl_file)
#    #os.path.join(REPO_ROOT, "Mug_after.step"),
#]
#_default_model = next((p for p in _coffee_candidates if os.path.exists(p)), _coffee_candidates[0])

# Initialize CAD renderer with default models (use the same file for before/after by default)
#before_model = _default_model
#after_model = _default_model
#print(f"Default model set to: {before_model}")
print(f"Model list: {model_list}")
print(f"Default model: {model_name_list[current_model_name] if model_name_list else 'none'}")

# Global renderer instance (initialized on first use to avoid startup delay)
renderer = None
current_render = None  # Store the last rendered image
device = None
renderer_lock = threading.Lock()

# Default render parameters for startup
DEFAULT_RENDER_PARAMS = {
    'view': 'Front',
    'depth': 0,
    'renderMode': 'Outline'
}


def _coerce_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _downsample_binary_to_braille_space(
    binary_img: np.ndarray,
    target_width: int,
    target_height: int,
) -> np.ndarray:
    """Downsample high-fidelity binary by mapping each raised pixel to nearest braille-space pixel.
    
    This line-aware approach preserves thin edges and lines that would be lost with
    threshold-based pooling. Each raised pixel in high-fidelity activates the 
    corresponding nearest pixel in downsampled space via nearest-neighbor mapping.
    """
    h, w = binary_img.shape
    out = np.zeros((target_height, target_width), dtype=np.uint8)
    
    # Get coordinates of all raised pixels in high-fidelity image
    raised_ys, raised_xs = np.where(binary_img > 0)
    
    print(f"[Downsample] Input shape: {h}x{w}, target: {target_height}x{target_width}")
    print(f"[Downsample] Raised pixels in input: {len(raised_ys)}")
    
    if len(raised_ys) == 0:
        print(f"[Downsample] No raised pixels, returning zeros")
        return out
    
    # Map high-fidelity coordinates to downsampled coordinates by nearest-neighbor rasterization.
    # Handle edge cases where h or w is 1.
    if h > 1:
        downsampled_ys = np.round(raised_ys * (target_height - 1) / (h - 1)).astype(int)
    else:
        downsampled_ys = np.zeros_like(raised_ys, dtype=int)
    
    if w > 1:
        downsampled_xs = np.round(raised_xs * (target_width - 1) / (w - 1)).astype(int)
    else:
        downsampled_xs = np.zeros_like(raised_xs, dtype=int)
    
    # Clamp to valid range
    downsampled_ys = np.clip(downsampled_ys, 0, target_height - 1)
    downsampled_xs = np.clip(downsampled_xs, 0, target_width - 1)
    
    # Activate those pixels in output
    out[downsampled_ys, downsampled_xs] = 255
    
    print(f"[Downsample] Raised pixels in output: {np.count_nonzero(out)}")
    print(f"[Downsample] Output shape: {out.shape}")
    
    return out


def _downsample_binary_any_hit(
    binary_img: np.ndarray,
    target_width: int,
    target_height: int,
) -> np.ndarray:
    """Conservative fallback: a target cell is raised if any source pixel in its block is raised."""
    h, w = binary_img.shape
    out = np.zeros((target_height, target_width), dtype=np.uint8)
    y_edges = np.linspace(0, h, target_height + 1, dtype=int)
    x_edges = np.linspace(0, w, target_width + 1, dtype=int)
    for y in range(target_height):
        y0, y1 = y_edges[y], y_edges[y + 1]
        if y1 <= y0:
            y1 = min(h, y0 + 1)
        for x in range(target_width):
            x0, x1 = x_edges[x], x_edges[x + 1]
            if x1 <= x0:
                x1 = min(w, x0 + 1)
            block = binary_img[y0:y1, x0:x1]
            if block.size > 0 and np.any(block > 0):
                out[y, x] = 255
    return out


def _payload_stats(payload):
    total_cells = int(payload.size)
    raised_cells = int(np.count_nonzero(payload > 0))
    payload_bytes = payload.astype(np.uint8, copy=False).tobytes()
    import hashlib
    return {
        "shape": list(payload.shape),
        "dtype": str(payload.dtype),
        "total_cells": total_cells,
        "raised_cells": raised_cells,
        "raised_ratio": float(raised_cells / total_cells) if total_cells else 0.0,
        "sum": int(np.sum(payload, dtype=np.int64)),
        "min": int(np.min(payload)) if total_cells else 0,
        "max": int(np.max(payload)) if total_cells else 0,
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }


def _mesh_stats(mesh):
    if mesh is None:
        return {"status": "unavailable"}
    try:
        bounds = mesh.bounds.tolist() if getattr(mesh, "bounds", None) is not None else []
    except Exception:
        bounds = []
    return {
        "status": "ok",
        "vertices": int(len(getattr(mesh, "vertices", []))),
        "faces": int(len(getattr(mesh, "faces", []))),
        "area": float(getattr(mesh, "area", 0.0)),
        "volume": float(getattr(mesh, "volume", 0.0)),
        "is_watertight": bool(getattr(mesh, "is_watertight", False)),
        "is_volume": bool(getattr(mesh, "is_volume", False)),
        "bounds": bounds,
    }


def _compute_slice_plane_debug(bbox, normal_dir, effective_slice_depth):
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    corners = np.array(
        [
            [x, y, z]
            for x in (xmin, xmax)
            for y in (ymin, ymax)
            for z in (zmin, zmax)
        ],
        dtype=float,
    )
    projections = corners @ normal_dir
    min_proj = float(np.min(projections))
    max_proj = float(np.max(projections))
    depth_clamped = float(np.clip(effective_slice_depth, 0.0, 1.0))
    d = min_proj + depth_clamped * (max_proj - min_proj)
    plane_origin = (normal_dir * d).tolist()
    return {
        "normal": normal_dir.tolist(),
        "depth_effective": depth_clamped,
        "min_projection": min_proj,
        "max_projection": max_proj,
        "plane_projection": float(d),
        "plane_origin": plane_origin,
    }


def _normalize_stage_image(img):
    arr = np.asarray(img)
    if arr.ndim == 2:
        rgb = np.stack([arr, arr, arr], axis=-1).astype(np.uint8, copy=False)
        alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
        return np.concatenate([rgb, alpha], axis=-1)
    if arr.ndim == 3 and arr.shape[2] == 1:
        return _normalize_stage_image(arr[:, :, 0])
    if arr.ndim == 3 and arr.shape[2] == 3:
        alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
        return np.concatenate([arr.astype(np.uint8, copy=False), alpha], axis=-1)
    if arr.ndim == 3 and arr.shape[2] == 4:
        return arr.astype(np.uint8, copy=False)
    return np.full((1, 1, 4), 255, dtype=np.uint8)


def _stage_preview_base64(img):
    rgba = _normalize_stage_image(img)
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode='RGBA').save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def _binary_preview_base64(mask_2d, *, scale=1):
    """Preview binary masks as black dots on white for easier visual debugging."""
    m = np.asarray(mask_2d)
    if m.ndim != 2:
        m = np.squeeze(m)
    if m.ndim != 2:
        return _stage_preview_base64(mask_2d)

    # Raised/active pixels should appear black on white for readability.
    rgba = np.full((m.shape[0], m.shape[1], 4), 255, dtype=np.uint8)
    raised = m > 0
    rgba[raised, 0:3] = 0

    s = max(1, int(scale))
    if s > 1:
        rgba = np.repeat(np.repeat(rgba, s, axis=0), s, axis=1)

    return _stage_preview_base64(rgba)


def _binary_cell_preview_base64(mask_2d, *, cell_size=8, gap=1):
    """Preview a binary grid where each active cell is rendered as a solid square."""
    m = np.asarray(mask_2d)
    if m.ndim != 2:
        m = np.squeeze(m)
    if m.ndim != 2:
        return _stage_preview_base64(mask_2d)

    h, w = m.shape
    cs = max(1, int(cell_size))
    gp = max(0, int(gap))
    tile = cs + gp
    out_h = h * tile - gp
    out_w = w * tile - gp

    # White canvas, then paint active cells as solid black squares.
    rgba = np.full((out_h, out_w, 4), 255, dtype=np.uint8)
    ys, xs = np.where(m > 0)
    for y, x in zip(ys, xs):
        y0 = int(y) * tile
        x0 = int(x) * tile
        rgba[y0:y0 + cs, x0:x0 + cs, 0:3] = 0

    return _stage_preview_base64(rgba)


def _mesh_preview_base64(mesh, bbox, view_key, screen_size=None):
    if mesh is None or len(getattr(mesh, 'faces', [])) == 0:
        return None
    if not screen_size:
        screen_size = [220, 90]
    try:
        stage_img, _ = get_single_view(
            mesh,
            bbox,
            cut_depth=0.5,
            view_key=view_key,
            rendering_mode='filled',
            imposed_ax_limits=[],
            screen_size=screen_size,
            projection_mode='orthographic',
        )
        return _stage_preview_base64(stage_img)
    except Exception:
        return None


def _concat_stage_tiles(images, padding=2):
    valid = [img for img in images if img is not None and img.size > 0]
    if not valid:
        return np.full((1, 1, 4), 255, dtype=np.uint8)
    norm = [_normalize_stage_image(img) for img in valid]
    max_h = max(img.shape[0] for img in norm)
    total_w = sum(img.shape[1] for img in norm) + padding * (len(norm) - 1)
    canvas = np.full((max_h, total_w, 4), 255, dtype=np.uint8)
    x0 = 0
    for img in norm:
        h, w = img.shape[0], img.shape[1]
        canvas[:h, x0:x0 + w, :] = img
        x0 += w + padding
    return canvas


def _face_depth_values(mesh, view_key):
    if mesh is None or len(getattr(mesh, 'faces', [])) == 0:
        return np.zeros((0,), dtype=float)
    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if view_key == 'top':
        d = verts[:, 2]
    elif view_key == 'front':
        d = verts[:, 1]
    elif view_key == 'left':
        d = verts[:, 0]
    elif view_key == 'bottom':
        d = -verts[:, 2]
    elif view_key == 'back':
        d = -verts[:, 1]
    elif view_key == 'right':
        d = -verts[:, 0]
    else:
        d = verts[:, 2]
    face_d = d[faces].mean(axis=1)
    dmin = float(np.min(face_d)) if len(face_d) else 0.0
    dmax = float(np.max(face_d)) if len(face_d) else 1.0
    if np.isclose(dmax, dmin):
        return np.zeros_like(face_d)
    return (face_d - dmin) / (dmax - dmin)


def _render_mesh_debug(mesh, view_key, screen_size=None, aa=False, color_by_depth=False):
    if mesh is None or len(getattr(mesh, 'faces', [])) == 0:
        return None
    if not screen_size:
        screen_size = [220, 90]
    coords = project_vertices(mesh.vertices, view_key, projection_mode='orthographic')
    if coords.shape[0] == 0:
        return None

    dpi = 100
    fig = plt.figure(figsize=(screen_size[0] / dpi, screen_size[1] / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    triangles = np.asarray(mesh.faces)
    if color_by_depth:
        face_values = _face_depth_values(mesh, view_key=view_key)
        ax.tripcolor(
            coords[:, 0],
            coords[:, 1],
            triangles=triangles,
            facecolors=face_values,
            cmap='turbo',
            aa=aa,
            edgecolor='none',
            shading='flat',
        )
    else:
        colors = np.zeros((len(triangles),), dtype=float)
        ax.tripcolor(
            coords[:, 0],
            coords[:, 1],
            triangles=triangles,
            facecolors=colors,
            cmap='gray',
            aa=aa,
            edgecolor='none',
            shading='flat',
        )
    ax.set_aspect('equal')
    ax.autoscale_view()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return np.array(Image.open(buf).convert('RGBA'))


def _binary_preview_from_channel0(render_rgba, threshold):
    channel0 = render_rgba[:, :, 0]
    raised = channel0 < threshold
    out = np.full((render_rgba.shape[0], render_rgba.shape[1], 4), 255, dtype=np.uint8)
    out[raised, 0:3] = 0
    out[raised, 3] = 255
    return out, int(np.count_nonzero(raised))


def _depth_peel_progress_preview(mesh, bbox, normal_dir, view_key):
    if mesh is None or len(getattr(mesh, 'faces', [])) == 0:
        return None
    tiles = []
    for depth in [0.0, 0.25, 0.5, 0.75, 1.0]:
        cut_mesh, _ = depth_peeling_single_depth_with_bbox(mesh, normal_dir, depth=depth, bbox=bbox)
        tile = _render_mesh_debug(cut_mesh, view_key=view_key, screen_size=[120, 70], color_by_depth=True)
        if tile is not None:
            tiles.append(tile)
    if not tiles:
        return None
    return _concat_stage_tiles(tiles, padding=1)


def _build_pipeline_debug_payload(
    engine,
    params,
    rendered,
    braille_payload,
    *,
    raw_binary=None,
):
    print(f"[DEBUG] braille_payload received: shape={braille_payload.shape}, dtype={braille_payload.dtype}, min={np.min(braille_payload)}, max={np.max(braille_payload)}, nonzero={np.count_nonzero(braille_payload)}")
    requested_view = str(params.get("view", "")).lower()
    view = requested_view
    if hasattr(engine, "_map_view_name"):
        try:
            view = str(engine._map_view_name(requested_view)).lower()
        except Exception:
            view = requested_view
    render_mode = str(params.get("renderMode", "outline")).lower()
    projection_mode = str(params.get("projectionMode", "orthographic")).lower()
    shape_choice = str(params.get("shape", "after")).lower()
    user_depth_percent = int(params.get("depth", 0))
    user_depth_percent = int(np.clip(user_depth_percent, 0, 100))
    effective_slice_depth = 1.0 - (float(user_depth_percent) / 100.0)
    shape_index = 0 if shape_choice == "before" else 1

    shapes = getattr(engine, "shapes", [])
    selected_shape = shapes[shape_index] if shape_index < len(shapes) else None

    debug_payload = {
        "pipeline_version": 1,
        "stages": [
            {
                "id": "request",
                "title": "Request Params",
                "status": "ok",
                "data": {
                    "pipeline_function": "CADComparisonRenderer.render(params)",
                    "view": view,
                    "view_requested": requested_view,
                    "render_mode": render_mode,
                    "projection_mode": projection_mode,
                    "shape": shape_choice,
                    "depth_percent": user_depth_percent,
                    "depth_effective": effective_slice_depth,
                },
            },
            {
                "id": "mesh_input",
                "title": "Input Mesh",
                "status": "ok",
                "data": {
                    "pipeline_function": "CADComparisonRenderer._load_models() → shapes[shape_index]",
                    **_mesh_stats(selected_shape),
                },
                "preview_image_base64": _mesh_preview_base64(selected_shape, list(getattr(engine, "bbox", []) or []), view),
            },
        ],
    }

    bbox = list(getattr(engine, "bbox", []) or [])
    if len(bbox) == 6 and view in SLICE_VIEWS and selected_shape is not None:
        try:
            normal_dir = np.array(SLICE_VIEWS[view]["dir"], dtype=float)
            normal_norm = np.linalg.norm(normal_dir)
            if not np.isclose(normal_norm, 0.0):
                normal_dir /= normal_norm

            clipped_mesh, plane_origin = depth_peeling_single_depth_with_bbox(
                selected_shape,
                normal_dir,
                depth=effective_slice_depth,
                bbox=bbox,
            )

            debug_payload["stages"].append(
                {
                    "id": "depth_peel",
                    "title": "Depth Peel Result",
                    "status": "ok",
                    "data": {
                        "pipeline_function": "depth_peeling_single_depth_with_bbox(shape, normal_dir, depth, bbox)",
                        "plane_origin": np.asarray(plane_origin).tolist(),
                        "mesh": _mesh_stats(clipped_mesh),
                    },
                    "preview_image_base64": _mesh_preview_base64(clipped_mesh, bbox, view),
                }
            )

            plane_faces_mesh = faces_on_plane_fast(clipped_mesh, plane_origin, normal_dir)
            debug_payload["stages"].append(
                {
                    "id": "slice_faces",
                    "title": "Faces On Slice Plane",
                    "status": "ok",
                    "data": {
                        "pipeline_function": "faces_on_plane_fast(clipped_mesh, plane_origin, normal_dir) → used by get_single_view() in slice mode",
                        "mesh": _mesh_stats(plane_faces_mesh),
                        "note": "Used by slice mode; still reported for all modes.",
                    },
                    "preview_image_base64": _mesh_preview_base64(plane_faces_mesh, bbox, view),
                }
            )
        except Exception as error:
            debug_payload["stages"].append(
                {
                    "id": "slice_pipeline",
                    "title": "Slice Pipeline",
                    "status": "error",
                    "data": {"message": str(error)},
                }
            )
    else:
        debug_payload["stages"].append(
            {
                "id": "slice_pipeline",
                "title": "Slice Pipeline",
                "status": "skipped",
                "data": {"message": "Missing bbox, view mapping, or input mesh."},
            }
        )

    image_stats = {
        "shape": list(rendered.shape),
        "dtype": str(rendered.dtype),
        "channel0_nonzero": int(np.count_nonzero(rendered[:, :, 0])),
        "channel0_min": int(np.min(rendered[:, :, 0])) if rendered.size else 0,
        "channel0_max": int(np.max(rendered[:, :, 0])) if rendered.size else 0,
    }

    # Slice graph precomputation stage
    view_diff_mats = getattr(engine, "view_diff_mats", {})
    if view_diff_mats:
        cut_position_int = int(np.clip(100 - user_depth_percent, 0, 100))
        mat = view_diff_mats.get(view)
        row_data = mat[cut_position_int].tolist() if mat is not None else None
        available_views = list(view_diff_mats.keys())
        mat_shape = list(mat.shape) if mat is not None else None
        debug_payload["stages"].append(
            {
                "id": "slice_graph_data",
                "title": "Slice Graph Data (view_diff_mats)",
                "status": "ok",
                "data": {
                    "pipeline_function": "CADComparisonRenderer.__init__() -> _load_models() -> _compute_slice_graphs() -> render() compose_slicegraph branch",
                    "precomputed_at_load_time": True,
                    "available_views": available_views,
                    "current_view": view,
                    "matrix_shape": mat_shape,
                    "cut_position_int": cut_position_int,
                    "row_at_current_depth": row_data,
                    "note": "Pairwise slice-area difference matrix precomputed during renderer initialization. Row at current depth drives the line graph overlaid on the braille display.",
                },
            }
        )
    else:
        debug_payload["stages"].append(
            {
                "id": "slice_graph_data",
                "title": "Slice Graph Data (view_diff_mats)",
                "status": "skipped",
                "data": {
                    "pipeline_function": "CADComparisonRenderer._compute_slice_graphs() → view_diff_mats",
                    "note": "No precomputed slice graph data found on renderer.",
                },
            }
        )

    debug_payload["stages"].append(
        {
            "id": "renderer_output_raw",
            "title": "Renderer Output (Raw)",
            "status": "ok",
            "data": {
                "pipeline_function": "CADComparisonRenderer.render(params) → returns RGBA ndarray",
                "shape": list(rendered.shape),
                "dtype": str(rendered.dtype),
                "note": "Direct output from CADComparisonRenderer.render(...) before downstream conversion.",
            },
            "preview_image_base64": _stage_preview_base64(rendered),
        }
    )

    debug_payload["stages"].append(
        {
            "id": "render_image",
            "title": "Rendered Image",
            "status": "ok",
            "data": {
                "pipeline_function": "CADComparisonRenderer.render(params) → channel extraction for braille",
                **image_stats,
            },
            "preview_image_base64": _stage_preview_base64(rendered),
        }
    )

    if raw_binary is not None:
        debug_payload["stages"].append(
            {
                "id": "hf_binary_raw",
                "title": "HF Binary Raw",
                "status": "ok",
                "data": {
                    "pipeline_function": "rendered[:, :, 0] -> invert -> threshold(>0)",
                    "shape": list(raw_binary.shape),
                    "dtype": str(raw_binary.dtype),
                    "nonzero": int(np.count_nonzero(raw_binary)),
                    "note": "Unoptimized high-fidelity binary used as current payload source.",
                },
                "preview_image_base64": _binary_preview_base64(raw_binary, scale=2),
            }
        )

    # Render each braille cell as an explicit 8x8 debug square.
    braille_preview_b64 = _binary_cell_preview_base64(braille_payload, cell_size=8, gap=1)
    print(f"[DEBUG] braille_preview_b64 length: {len(braille_preview_b64)}")
    
    debug_payload["stages"].append(
        {
            "id": "braille_payload",
            "title": "Braille Payload",
            "status": "ok",
            "data": {
                "pipeline_function": "_downsample_binary_to_braille_space() -> send_to_braille_display(img_data)",
                **_payload_stats(braille_payload),
            },
            "preview_image_base64": braille_preview_b64,
        }
    )
    return debug_payload

def _resolve_model_index(model_value):
    """Map UI model selection to a safe model index."""
    if model_value == "none" or model_value is None:
        return current_model_name
    try:
        idx = int(model_value)
    except (TypeError, ValueError):
        return current_model_name
    if idx < 0 or idx >= len(model_list):
        return current_model_name
    return idx


def get_or_create_renderer(model_index, pool):
    """Get a renderer for a model index from the given pool, creating it if needed."""
    if model_index not in pool:
        model_path = model_list[model_index]
        print(f"Initializing CAD renderer for model index {model_index}: {model_path}")
        renderer = CADComparisonRenderer(model_path, model_path)
        renderer.init_device(device)
        pool[model_index] = renderer
        print("Renderer initialized successfully!")
    return pool[model_index]


def _warm_model_cache(model_index, pool):
    """Ensure a model renderer is cached and return True if newly created."""
    created = model_index not in pool
    get_or_create_renderer(model_index, pool)
    return created

def _format_img_data_repr(arr2d: np.ndarray) -> str:
    # Ensure 2D
    a = np.asarray(arr2d)
    if a.ndim == 3 and a.shape[-1] == 1:
        a = a.squeeze(-1)
    # Stats
    nz = int((a > 0).sum())
    stats = f"shape={a.shape}, dtype={a.dtype}, min={int(a.min())}, max={int(a.max())}, mean={float(a.mean()):.2f}, nonzero={nz}"
    # Tiny preview (thresholded), '#'=raised, '.'=flat
    h, w = min(8, a.shape[0]), min(16, a.shape[1])
    preview = (a[:h, :w] > 0)
    lines = [''.join('#' if v else '.' for v in row) for row in preview]
    return stats + ("\n" + "\n".join(lines) if lines else "")


def _print_binary_image_01(arr2d: np.ndarray, *, label: str = "binary_image") -> None:
    """Print a 2D binary image as 0/1 rows for debugging."""
    a = np.asarray(arr2d)
    if a.ndim == 3 and a.shape[-1] == 1:
        a = a.squeeze(-1)
    if a.ndim != 2:
        print(f"[{label}] expected 2D array, got shape={a.shape}")
        return

    b = (a > 0)
    print(f"[{label}] shape={b.shape}, nonzero={int(np.count_nonzero(b))}")
    print(f"[{label}] BEGIN")
    for row in b:
        print(''.join('1' if px else '0' for px in row))
    print(f"[{label}] END")


def _draw_braille_cell_on_payload(payload: np.ndarray, x: int, y: int, dots) -> None:
    """Draw a 2x4 braille cell on a binary payload (255 raised, 0 lowered)."""
    dot_positions = {
        1: (0, 0), 2: (0, 1), 3: (0, 2), 7: (0, 3),
        4: (1, 0), 5: (1, 1), 6: (1, 2), 8: (1, 3),
    }
    h, w = payload.shape
    for dot in dots:
        if dot not in dot_positions:
            continue
        dx, dy = dot_positions[dot]
        px = x + dx
        py = y + dy
        if 0 <= px < w and 0 <= py < h:
            payload[py, px] = 255


def _overlay_view_info_box_on_payload(payload: np.ndarray, axis_text: str) -> None:
    """Overlay a compact 7x5 braille info box in the top-left corner."""
    if payload is None or payload.ndim != 2:
        return
    h, w = payload.shape
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

    # Clear background region so only braille dots remain raised.
    payload[0:5, 0:7] = 0

    if len(axis_text) >= 1 and axis_text[0] in char_to_dots:
        _draw_braille_cell_on_payload(payload, 1, 1, char_to_dots[axis_text[0]])
    if len(axis_text) >= 2 and axis_text[1] in char_to_dots:
        _draw_braille_cell_on_payload(payload, 4, 1, char_to_dots[axis_text[1]])


def _draw_braille_text_on_payload(payload: np.ndarray, text: str, x: int, y: int) -> None:
    """Draw a short braille string using 2x4 cells with 1px spacing."""
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
    for ch in (text or "").lower():
        dots = char_to_dots.get(ch, [])
        if len(dots) > 0:
            _draw_braille_cell_on_payload(payload, cursor_x, y, dots)
        cursor_x += cell_advance


def _overlay_side_by_side_view_labels_on_payload(payload: np.ndarray, left_axis: str, right_axis: str) -> None:
    """Overlay compact braille axis labels for side-by-side mode on final payload."""
    if payload is None or payload.ndim != 2:
        return
    h, w = payload.shape
    if h < 6 or w < 12:
        return

    legend_width = int(w / 3)
    y = 1
    left_x = 1
    right_x = legend_width + 1
    _draw_braille_text_on_payload(payload, left_axis, left_x, y)
    _draw_braille_text_on_payload(payload, right_axis, right_x, y)

def initialize_default_braille_render():
    """Render once at startup with default params and send to braille display."""
    global current_render
    try:
        print("\n" + "=" * 60)
        print("INITIAL DEFAULT RENDER TO BRAILLE DISPLAY")
        print("=" * 60)
        print(f"Default params: {json.dumps(DEFAULT_RENDER_PARAMS)}")
        if not model_list:
            print("No STL models found in model/; skipping default render.")
            return
        r = get_or_create_renderer(current_model_name, renderer_dict)
        img_array = r.render(DEFAULT_RENDER_PARAMS)
        current_render = img_array
        print(f"Rendered image shape: {img_array.shape}")
        try:
            img_data = img_array[:, :, 3]
            bytes_written = send_to_braille_display(img_data)
            print(f"Braille write: {bytes_written} bytes")
            print("img_data summary:\n" + _format_img_data_repr(img_data))
        except BrailleDisplayError as e:
            print(f"Braille send failed: {e}")
        print("Default render complete.")
        print("=" * 60 + "\n")
    except Exception as e:
        print(f"Default render failed: {e}")

@app.route('/')
def home():
    """Home endpoint with server status"""
    return jsonify({
        'status': 'running',
        'message': 'Accessible 3D Viewer Command Server with CAD Rendering',
        'endpoints': {
            '/command': 'POST - Send commands from the viewer (triggers render)',
            '/render': 'POST - Render CAD view with parameters',
            '/render/export-source': 'POST - Render high-fidelity tactile source for export',
            '/render/image': 'GET - Get last rendered image as PNG',
            '/render/base64': 'GET - Get last rendered image as base64',
            '/commands': 'GET - Retrieve all logged commands',
            '/commands/clear': 'POST - Clear all logged commands',
            '/models': 'POST - Change the before/after model files'
        }
    })

@app.route('/render', methods=['POST'])
def render_view():
    """Render CAD view with given parameters"""
    global current_render
    global cube_value
    global current_model_name
    try:
        params = request.get_json()
        cube_value = params["view"]
        
        print("\n" + "=" * 60)
        print("RENDERING CAD VIEW")
        print("=" * 60)
        print(f"Parameters: {json.dumps(params, indent=2)}")
        #params["mode"] = "side_by_side"
        requested_model_index = _resolve_model_index(params.get("current_model"))
        model_changed = requested_model_index != current_model_name
        current_model_name = requested_model_index
        print(f"Current model index: {current_model_name}")
        if model_changed:
            print(f"Model changed to: {model_name_list[current_model_name]}")
        
        # Serialize access to shared renderer state.
        # /render/export-source temporarily overrides screen_size, so overlapping
        # requests can otherwise produce a tiny image in the top-left corner.
        show_view_info_box = bool(params.get("show_view_info_box", False))
        show_side_by_side_labels = bool(params.get("show_side_by_side_labels", True))
        comparison_mode = str(params.get("mode", "single")).lower()
        apply_payload_info_box = show_view_info_box and comparison_mode in ["single", "slice-graph"]
        apply_payload_side_by_side_labels = show_side_by_side_labels and comparison_mode == "side-by-side"

        with renderer_lock:
            # Warm/cache renderer for this model; once built, slice precompute is reused
            cache_warmed = _warm_model_cache(current_model_name, renderer_dict)
            # Get low-fidelity renderer from its dedicated pool
            r = get_or_create_renderer(current_model_name, renderer_dict)

            # Draw view-info braille in final payload stage, not in high-fidelity
            # renderer output, so dot geometry stays faithful on the physical grid.
            render_params = dict(params)
            if apply_payload_info_box:
                render_params["show_view_info_box"] = False
            if apply_payload_side_by_side_labels:
                render_params["show_side_by_side_labels"] = False

            # 1) Compute view at high fidelity from the original STL geometry.
            target_screen_size = list(r.screen_size) if r.screen_size else [96, 40]
            target_w = _coerce_positive_int(target_screen_size[0], 96)
            target_h = _coerce_positive_int(target_screen_size[1], 40)

            high_fidelity_width = _coerce_positive_int(
                params.get("high_fidelity_width", target_w * 8),
                target_w * 8,
            )
            high_fidelity_height = max(1, int(round(high_fidelity_width * (float(target_h) / float(target_w)))))

            original_screen_size = list(r.screen_size)
            r.screen_size = [high_fidelity_width, high_fidelity_height]
            try:
                img_array = r.render(render_params)
            finally:
                r.screen_size = original_screen_size

            current_render = img_array
        #print(cube_value)
        
        print(f"Rendered high-fidelity image shape: {img_array.shape}")
        # 2) Convert to black/white while still in high-fidelity space.
        high_fidelity_grayscale = np.bitwise_not(img_array[:, :, 0].astype(np.uint8, copy=False))
        high_fidelity_binary_raw = np.where(high_fidelity_grayscale > 0, 255, 0).astype(np.uint8)
        # 3) Use raw high-fidelity binary for braille projection.
        high_fidelity_binary_for_payload = high_fidelity_binary_raw

        print(
            "[Pipeline] nonzero counts:",
            f"channel0={int(np.count_nonzero(img_array[:, :, 0]))}",
            f"gray={int(np.count_nonzero(high_fidelity_grayscale))}",
            f"raw={int(np.count_nonzero(high_fidelity_binary_raw))}",
            f"used={int(np.count_nonzero(high_fidelity_binary_for_payload))}",
        )

        # 4) Render into braille space (low-fidelity display) via ratio-aware block reduction.
        img_data = _downsample_binary_to_braille_space(
            high_fidelity_binary_for_payload,
            target_width=target_w,
            target_height=target_h,
        )

        if np.count_nonzero(img_data) == 0 and np.count_nonzero(high_fidelity_binary_for_payload) > 0:
            print("[Pipeline] nearest-neighbor downsample produced empty payload; falling back to any-hit block downsample")
            img_data = _downsample_binary_any_hit(
                high_fidelity_binary_for_payload,
                target_width=target_w,
                target_height=target_h,
            )

        if apply_payload_info_box:
            axis_text = str(params.get("view", "x+"))
            _overlay_view_info_box_on_payload(img_data, axis_text)

        if apply_payload_side_by_side_labels:
            right_axis = str(params.get("view", "x+")).lower()
            legend_from_cut = {
                "x+": "z+",
                "y+": "x+",
                "z+": "y+",
                "x-": "z-",
                "y-": "x-",
                "z-": "y-",
            }
            left_axis = legend_from_cut.get(right_axis, "x+")
            _overlay_side_by_side_view_labels_on_payload(img_data, left_axis, right_axis)

        # Disabled verbose 0/1 image dump to keep logs shareable.

        try:
            bytes_written = send_to_braille_display(img_data)
            print(f"Braille write: {bytes_written} bytes")
            print("img_data summary:\n" + _format_img_data_repr(img_data))
        except BrailleDisplayError as e:
            print(f"Braille send failed: {e}")
        print("=" * 60 + "\n")
        
        # Convert to base64 for easy transmission
        img = Image.fromarray(img_array.astype('uint8'), 'RGBA')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        if params["print_view"]:
            if not os.path.exists("renders"):
                os.mkdir("renders")
            new_file_name_inc = 0
            # search for current maximal file name
            for file_name in os.listdir("renders"):
                if not "print_" in file_name:
                    continue
                second_part = file_name.split("print_")[1]
                if not "_" in second_part:
                    continue
                file_name_inc = int(second_part.split("_")[0])
                if file_name_inc >= new_file_name_inc:
                    new_file_name_inc = file_name_inc+1
            print_file_name = "print_"+str(new_file_name_inc)+"_"+str(r.current_render_mode)+"_"+str(r.current_cut_depth)+"_"+str(r.view_current_axis)+"_"+str(np.array(r.view_current_view_limits).tolist())
            save_binary_array_as_vector_pdf(img_data, os.path.join("renders", print_file_name+".pdf"))
            with open(os.path.join("renders", print_file_name+".npy"), "wb") as fp:
                np.save(fp, img_data)
            #img.save(os.path.join("renders", "0.png"), format="PNG")

        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        # Encode tactile preview as explicit 8x8 cell squares for visibility.
        try:
            tactile_base64 = _binary_cell_preview_base64(img_data, cell_size=8, gap=1)
        except Exception:
            tactile_base64 = img_base64

        debug_pipeline = _build_pipeline_debug_payload(
            engine=r,
            params=params,
            rendered=img_array,
            braille_payload=img_data,
            raw_binary=high_fidelity_binary_raw,
        )

        return jsonify({
            'status': 'success',
            'message': 'Render complete',
            'image_shape': img_data.shape,
            'bbox': r.bbox,
            'image_base64': tactile_base64,
            'model_list': model_name_list,
            'current_model': model_name_list[current_model_name],
            'renderer_cache_warmed': cache_warmed,
            'debug_pipeline': debug_pipeline,
        }), 200
        
    except Exception as e:
        print(f"Error rendering: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400


@app.route('/render/export-source', methods=['POST', 'OPTIONS'])
def render_export_source():
    """Render a high-fidelity tactile source image for export without braille I/O."""
    global current_model_name

    if request.method == 'OPTIONS':
        return ('', 200)

    try:
        params = request.get_json(silent=True) or {}
        merged_params = dict(DEFAULT_RENDER_PARAMS)
        merged_params.update(params)

        model_value = merged_params.get('current_model', 'none')
        current_model_name = _resolve_model_index(model_value)

        export_width = _coerce_positive_int(merged_params.get('export_width', 1000), 1000)

        with renderer_lock:
            # Use an export-dedicated renderer pool so high-fidelity renders do not
            # mutate low-fidelity renderer state.
            renderer = get_or_create_renderer(current_model_name, export_renderer_dict)
            original_screen_size = list(renderer.screen_size)
            if not original_screen_size or original_screen_size[0] <= 0:
                original_screen_size = [96, 40]

            aspect_ratio = float(original_screen_size[1]) / float(original_screen_size[0])
            export_height = max(1, int(round(export_width * aspect_ratio)))

            renderer.screen_size = [export_width, export_height]
            try:
                img_array = renderer.render(merged_params)
            finally:
                renderer.screen_size = original_screen_size

        img_data = ~img_array[:, :, 0]
        img_data[img_data > 0] = 255
        img_data = img_data.astype(np.uint8, copy=False)

        tactile_img = Image.fromarray(img_data, 'L')
        tactile_buffer = io.BytesIO()
        tactile_img.save(tactile_buffer, format='PNG')
        tactile_buffer.seek(0)
        tactile_base64 = base64.b64encode(tactile_buffer.getvalue()).decode('utf-8')

        return jsonify({
            'status': 'success',
            'message': 'Export source render complete',
            'image_shape': list(img_data.shape),
            'image_base64': tactile_base64,
            'export_width': export_width,
            'export_height': export_height,
        }), 200
    except Exception as e:
        print(f"Error rendering export source: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400

@app.route('/render/image', methods=['GET'])
def get_render_image():
    """Get the last rendered image as PNG file"""
    global current_render
    if current_render is None:
        return jsonify({
            'status': 'error',
            'message': 'No image has been rendered yet'
        }), 404
    
    try:
        img = Image.fromarray(current_render.astype('uint8'), 'RGBA')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return send_file(buffer, mimetype='image/png')
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400

@app.route('/render/base64', methods=['GET'])
def get_render_base64():
    """Get the last rendered image as base64 string"""
    global current_render
    if current_render is None:
        return jsonify({
            'status': 'error',
            'message': 'No image has been rendered yet'
        }), 404
    
    try:
        img = Image.fromarray(current_render.astype('uint8'), 'RGBA')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return jsonify({
            'status': 'success',
            'image_base64': img_base64,
            'image_shape': current_render.shape
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400

@app.route('/command', methods=['POST'])
def receive_command():
    """Receive and log a command from the 3D viewer, and trigger render if applicable"""
    global current_render
    global cube_value
    try:
        data = request.get_json()
        
        # Add timestamp to the command
        command_entry = {
            'timestamp': datetime.now().isoformat(),
            'data': data
        }
        
        commands_log.append(command_entry)
        
        # Print to console for debugging - Enhanced output
        print("\n" + "=" * 60)
        print(f"[{command_entry['timestamp']}] COMMAND RECEIVED #{len(commands_log)}")
        print("=" * 60)
        print(f"Type: {data.get('type', 'unknown')}")
        print(f"Action: {data.get('action', 'N/A')}")
        print("\nFull Data:")
        print(json.dumps(data, indent=2))
        print("=" * 60 + "\n")
        
        # Check if this command contains render parameters
        render_params = None
        if 'view' in data or 'renderMode' in data or 'depth' in data:
            # This looks like a render command, extract params
            render_params = {
                'view': data.get('view', 'Front'),
                'depth': data.get('depth', 0),
                'renderMode': data.get('renderMode', 'Outline')
            }
            cube_value = data.get("view")
            print(cube_value)
            
            # Add optional params if present
            if 'shape' in data:
                render_params['shape'] = data['shape']
            if 'mode' in data:
                render_params['mode'] = data['mode']
            if 'superpositionMode' in data:
                render_params['superpositionMode'] = data['superpositionMode']
        
        response_data = {
            'status': 'success',
            'message': 'Command received',
            'command_id': len(commands_log)
        }
        
        # If we have render params, automatically render
        if render_params:
            try:
                print("Auto-rendering based on command parameters...")
                r = get_or_create_renderer()
                img_array = r.render(render_params)
                current_render = img_array

                # for i in range(img_array.shape[0]):
                #     for j in range(img_array.shape[1]):
                #         if img_array[i,j,0] == 255:
                #             print(1, end='')
                #         else:
                #             print(0, end='')
                #     print()

                # Send only the fourth axis (channel index 3) to the braille display, no resizing
                try:
                    img_data = img_array[:, :, 3]
                    for i in range(img_data.shape[0]):
                        for j in range(img_data.shape[1]):
                            if img_data[i,j,0] == 255:
                                print(1, end='')
                            else:
                                print(0, end='')
                        print()
                    bytes_written = send_to_braille_display(img_data)
                    print(f"Braille write: {bytes_written} bytes")
                    print("img_data summary:\n" + _format_img_data_repr(img_data))
                except BrailleDisplayError as e:
                    print(f"Braille send failed: {e}")
                
                # Convert to base64
                img = Image.fromarray(img_array.astype('uint8'), 'RGBA')
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                buffer.seek(0)
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                
                response_data['render'] = {
                    'status': 'success',
                    'image_shape': img_array.shape,
                    'image_base64': img_base64
                }
                print(f"Auto-render complete! Image shape: {img_array.shape}")
            except Exception as render_error:
                print(f"Auto-render failed: {str(render_error)}")
                response_data['render'] = {
                    'status': 'error',
                    'message': str(render_error)
                }
        
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"Error processing command: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400

@app.route('/commands', methods=['GET'])
def get_commands():
    """Retrieve all logged commands"""
    return jsonify({
        'status': 'success',
        'total_commands': len(commands_log),
        'commands': commands_log
    })

@app.route('/commands/clear', methods=['POST'])
def clear_commands():
    """Clear all logged commands"""
    global commands_log
    count = len(commands_log)
    commands_log = []
    return jsonify({
        'status': 'success',
        'message': f'Cleared {count} commands'
    })

@app.route('/commands/stats', methods=['GET'])
def get_stats():
    """Get statistics about logged commands"""
    if not commands_log:
        return jsonify({
            'status': 'success',
            'total_commands': 0,
            'stats': {}
        })
    
    # Count commands by type
    type_counts = {}
    action_counts = {}
    
    for entry in commands_log:
        data = entry['data']
        cmd_type = data.get('type', 'unknown')
        action = data.get('action', 'unknown')
        
        type_counts[cmd_type] = type_counts.get(cmd_type, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1
    
    return jsonify({
        'status': 'success',
        'total_commands': len(commands_log),
        'stats': {
            'by_type': type_counts,
            'by_action': action_counts,
            'first_command': commands_log[0]['timestamp'],
            'last_command': commands_log[-1]['timestamp']
        }
    })

@app.route('/device', methods=['GET', 'POST'])
def set_device():
    """GET: return current device status. POST: connect to specified device type."""
    global device
    if request.method == 'GET':
        if device is None:
            return jsonify({'status': 'ok', 'device': 'none', 'connected': False})
        return jsonify({'status': 'ok', 'device': device.kind, 'connected': True})

    data = request.get_json() or {}
    device_type = (data.get('device') or 'none').lower()

    if device_type == 'none':
        device = None
        return jsonify({'status': 'ok', 'device': 'none', 'connected': False})

    prefer_dotpad = (device_type == 'dotpad')
    try:
        device = _connect(scan_timeout=6.0, prefer_dotpad=prefer_dotpad)
        return jsonify({'status': 'ok', 'device': device.kind, 'connected': True})
    except BrailleDisplayError as e:
        device = None
        return jsonify({'status': 'error', 'message': str(e), 'device': 'none', 'connected': False}), 200


@app.route('/models', methods=['POST'])
def change_models():
    """Change the before/after model files"""
    global renderer, before_model, after_model, current_render
    try:
        data = request.get_json()
        new_before = data.get('before')
        new_after = data.get('after')
        
        if not new_before or not new_after:
            return jsonify({
                'status': 'error',
                'message': 'Both "before" and "after" model paths are required'
            }), 400
        
        # Validate files exist
        if not os.path.exists(new_before):
            return jsonify({
                'status': 'error',
                'message': f'Before model not found: {new_before}'
            }), 400
        
        if not os.path.exists(new_after):
            return jsonify({
                'status': 'error',
                'message': f'After model not found: {new_after}'
            }), 400
        
        # Update models
        before_model = new_before
        after_model = new_after
        
        # Reset renderer to force reload
        renderer = None
        current_render = None
        
        print(f"\nModel files updated:")
        print(f"  Before: {before_model}")
        print(f"  After: {after_model}\n")
        
        return jsonify({
            'status': 'success',
            'message': 'Model files updated',
            'before': before_model,
            'after': after_model
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400

view_cube_mapping = {
    3: "x-",
    4: "x+",
    2: "y-",
    5: "y+",
    1: "z-",
    6: "z+"
}

cube_value = view_cube_mapping[6]
slider_value = 0

async def notification_callback(number, stability_descr):
    global cube_value
    """
    GoDice number notification callback.
    Called each time GoDice is flipped, receiving flip event data:
    :param number: a rolled number
    :param stability_descr: an additional value clarifying device movement state, ie stable, rolling...
    """
    if stability_descr in [godice.StabilityDescriptor.MOVE_STABLE, godice.StabilityDescriptor.STABLE]:
        print(f"Number: {number}, stability descriptor: {stability_descr}")
        cube_value = view_cube_mapping[number]
        #return {'cube_value': view_cube_mapping[number]}

def filter_godice_devices(dev_advdata_tuples):
    """
    Receives all discovered devices and returns only GoDice devices
    """
    return [
        (dev, adv_data)
        for dev, adv_data in dev_advdata_tuples
        if (dev.name and dev.name.startswith("GoDice"))
    ]


def select_closest_device(dev_advdata_tuples):
    """
    Finds the closest device based on RSSI are returns it
    """
    def _rssi_as_key(dev_advdata):
        _, adv_data = dev_advdata
        return adv_data.rssi

    return max(dev_advdata_tuples, key=_rssi_as_key)


def print_device_info(devices):
    """
    Prints short summary of discovered devices
    """
    for dev, adv_data in devices:
        print(f"Name: {dev.name}, address: {dev.address}, rssi: {adv_data.rssi}")

async def godice_main():
    global dice
    #print("Discovering GoDice devices...")
    print("Discovering  devices...")
    discovery_res = await bleak.BleakScanner.discover(timeout=10, return_adv=True)
    device_advdata_tuples = discovery_res.values()
    device_advdata_tuples = filter_godice_devices(device_advdata_tuples)

    print("Discovered devices...")
    print_device_info(device_advdata_tuples)

    print("Connecting to a closest device...")
    device, _adv_data = select_closest_device(device_advdata_tuples)

    async with godice.create(device.address, godice.Shell.D6) as dice:
        print(f"Connected to {device.name}")

        color = await dice.get_color()
        battery_lvl = await dice.get_battery_level()
        print(f"Color: {color}")
        print(f"Battery: {battery_lvl}")

        print("Listening to position updates. Flip your dice")
        await dice.subscribe_number_notification(notification_callback)
        while True:
            await asyncio.sleep(30)  # sleep to keep callbacks alive
    print("end godice")

def dice_main_thread():
    asyncio.run(godice_main())


def start_slider_trinkey():
    global slider_value
    slider_trinkey_port = None
    ports = list_ports.comports(include_links=False)
    for p in ports:
        if p.pid is not None:
            print("Port:", p.device, "-", hex(p.pid), end="\t")
            if p.pid == 0x8102:
                slider_trinkey_port = p
                print("Found Slider Trinkey!")
                trinkey = serial.Serial(p.device)
                break
            else:
                print("Did not find Slider Trinkey port :(")
                exit()
    
    while True:
        avg_value = 0
        for i in range(10):
            x = trinkey.readline().decode('utf-8')
            #print(x)
            if not x.startswith("Slider: "):
                continue
            avg_value += int(float(x.split(": ")[1]))
        val = int(avg_value/10)
        if 100 - val != slider_value:
            slider_value = 100 - val
        #print(val)
        #print("position", val)

@app.route('/get_data')
def get_data():
    return jsonify({
        'status': 'success',
        'cube_value': cube_value,
        'slider_value': slider_value
    })

if __name__ == '__main__':
    print("=" * 70)
    print("Accessible 3D Viewer Command Server with CAD Rendering")
    print("=" * 70)
    print("Server starting on http://localhost:6969")
    print("\nCurrent Models:")
    #print(f"  Before: {before_model}")
    #print(f"  After:  {after_model}")
    print("\nEndpoints:")
    print("  - POST /command           - Receive commands (auto-renders if params present)")
    print("  - POST /render            - Explicitly render with parameters")
    print("  - GET  /render/image      - Get last rendered image as PNG")
    print("  - GET  /render/base64     - Get last rendered image as base64")
    print("  - GET  /commands          - View all logged commands")
    print("  - POST /commands/clear    - Clear command log")
    print("  - GET  /commands/stats    - View command statistics")
    print("  - POST /models            - Change before/after model files")
    print("=" * 70)

    try:
        device = _connect(scan_timeout=6.0, prefer_dotpad=False)
    except BrailleDisplayError as e:
        print(f"[WARNING] No braille display found, running without device: {e}")
        device = None
    # Render once on startup and send to braille display
    initialize_default_braille_render()
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    print("\nWaiting for commands...\n")
    #threading.Thread(target=dice_main_thread, daemon=True).start()
    #threading.Thread(target=start_slider_trinkey, daemon=True).start()
    app.run(debug=False, host='0.0.0.0', port=6969)
