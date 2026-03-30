#!/usr/bin/env python3
"""Accessible 3D viewer server.

Features:
- Receives render state from the viewer and renders with CADComparisonRenderer.
- Sends rendered output to a connected braille display using braille_display.py.
- Optionally reads GoDice orientation and Slider Trinkey position if hardware is present.
- Supports command logging endpoints used by the legacy cube/slider server.
- Opens accessible-3d-viewer.html in the default browser at startup.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, has_request_context, jsonify, request, send_file
from flask_cors import CORS
from PIL import Image

from braille_display import BrailleDisplayError, send_to_braille_display
from cad_comparison_lib import CADComparisonRenderer
from src.converter.render_low_res import save_binary_array_as_vector_pdf

try:
    import bleak  # type: ignore
except Exception:
    bleak = None

try:
    import godice  # type: ignore
except Exception:
    godice = None

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:
    serial = None
    list_ports = None


logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app)


@dataclass
class RuntimeState:
    cube_value: str = "z+"
    slider_value: int = 0
    current_model_index: int = 0


if getattr(sys, "frozen", False):
    # In bundled mode, runtime assets are expected next to the executable.
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    REPO_ROOT = Path(__file__).resolve().parent
MODEL_DIR = REPO_ROOT / "model"
RENDERS_DIR = REPO_ROOT / "renders"
STUDY_LOG_DIR = REPO_ROOT / "study_logs"
BRAILLE_LOG_PATH = Path(os.getenv("BRAILLE_LOG_PATH", str(STUDY_LOG_DIR / "braille_send_events.jsonl")))

DEFAULT_RENDER_PARAMS: dict[str, Any] = {
    "view": "y-",
    "zoom": "0",
    "depth": 0,
    "renderMode": "Outline",
    "mode": "single",
    "move_camera_center": "none",
    "print_view": False,
}

VIEW_CUBE_MAPPING = {
    1: "z-",
    2: "y-",
    3: "x-",
    4: "x+",
    5: "y+",
    6: "z+",
}

state = RuntimeState()
renderers_by_model: dict[int, CADComparisonRenderer] = {}
current_render: np.ndarray | None = None
commands_log: list[dict[str, Any]] = []
state_lock = threading.Lock()
braille_log_lock = threading.Lock()
commands_log_lock = threading.Lock()
braille_send_sequence = 0
last_render_fingerprint: str | None = None
last_render_response: dict[str, Any] | None = None

# Quiet-by-default: set SERVER_VERBOSE=1 to see detailed logs.
QUIET_MODE = os.getenv("SERVER_VERBOSE", "0").lower() not in {"1", "true", "yes", "on"}


def _log(message: str, *, force: bool = False) -> None:
    if force or not QUIET_MODE:
        print(message)


def _renderer_stdio_guard():
    if QUIET_MODE:
        sink = io.StringIO()
        return contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink)
    return contextlib.nullcontext(), contextlib.nullcontext()


def _discover_models() -> list[Path]:
    patterns = ("*.stl", "*.step", "*.STEP")
    models: list[Path] = []
    for pattern in patterns:
        models.extend(sorted(MODEL_DIR.glob(pattern)))
    # Deduplicate while preserving order.
    return list(dict.fromkeys(models))


def _find_default_model() -> Path:
    models = _discover_models()
    if models:
        return models[0]
    raise FileNotFoundError(f"No .stl/.step model found in {MODEL_DIR}")


DEFAULT_MODEL = _find_default_model()
AVAILABLE_MODELS = _discover_models() or [DEFAULT_MODEL]
MODEL_NAME_LIST = [model_path.stem for model_path in AVAILABLE_MODELS]


def _normalize_model_index(raw_index: Any) -> int:
    if raw_index is None:
        with state_lock:
            return state.current_model_index
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return 0
    if index < 0 or index >= len(AVAILABLE_MODELS):
        return 0
    return index


def get_or_create_renderer(model_index: int | None = None) -> CADComparisonRenderer:
    index = _normalize_model_index(model_index)
    if index not in renderers_by_model:
        model_path = AVAILABLE_MODELS[index]
        _log(f"Initializing CAD renderer with: {model_path}")
        out_guard, err_guard = _renderer_stdio_guard()
        with out_guard, err_guard:
            renderers_by_model[index] = CADComparisonRenderer(str(model_path), str(model_path))
    return renderers_by_model[index]


def _to_braille_payload(rendered_rgba: np.ndarray) -> np.ndarray:
    # Existing flow from previous server: invert first channel and convert to 0/255.
    payload = np.bitwise_not(rendered_rgba[:, :, 0])
    return np.where(payload > 0, 255, 0).astype(np.uint8)


def _payload_stats(payload: np.ndarray) -> dict[str, Any]:
    total_cells = int(payload.size)
    raised_cells = int(np.count_nonzero(payload > 0))
    payload_bytes = payload.astype(np.uint8, copy=False).tobytes()
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


def _collect_request_context() -> dict[str, Any]:
    if not has_request_context():
        return {}
    return {
        "endpoint": request.path,
        "method": request.method,
        "remote_addr": request.remote_addr,
        "user_agent": request.user_agent.string,
    }


def _write_braille_event(event: dict[str, Any]) -> None:
    BRAILLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, ensure_ascii=False)
    with braille_log_lock:
        with BRAILLE_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _next_braille_send_sequence() -> int:
    global braille_send_sequence
    with braille_log_lock:
        braille_send_sequence += 1
        return braille_send_sequence


def _render_and_send(
    params: dict[str, Any], *, source: str, model_index: int
) -> tuple[np.ndarray, list[float] | None, np.ndarray]:
    global current_render

    engine = get_or_create_renderer(model_index)
    out_guard, err_guard = _renderer_stdio_guard()
    with out_guard, err_guard:
        rendered = engine.render(params)
    current_render = rendered

    braille_payload = _to_braille_payload(rendered)
    started_at = time.perf_counter()
    sequence = _next_braille_send_sequence()
    with state_lock:
        state_snapshot = {
            "cube_value": state.cube_value,
            "slider_value": state.slider_value,
            "current_model_index": state.current_model_index,
        }
    event: dict[str, Any] = {
        "event": "braille_send",
        "sequence": sequence,
        "source": source,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": str(AVAILABLE_MODELS[model_index]),
        "model_index": model_index,
        "params": {
            "view": params.get("view"),
            "zoom": params.get("zoom"),
            "depth": params.get("depth"),
            "renderMode": params.get("renderMode"),
            "mode": params.get("mode"),
            "move_camera_center": params.get("move_camera_center"),
            "print_view": params.get("print_view"),
        },
        "state": state_snapshot,
        "render_shape": list(rendered.shape),
        "payload": _payload_stats(braille_payload),
        "request": _collect_request_context(),
    }

    try:
        bytes_written = send_to_braille_display(braille_payload)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        event.update(
            {
                "status": "success",
                "bytes_written": int(bytes_written),
                "send_duration_ms": round(elapsed_ms, 3),
            }
        )
        _write_braille_event(event)
        _log(f"Braille write: {bytes_written} bytes")
    except BrailleDisplayError as error:
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        event.update(
            {
                "status": "error",
                "error_type": type(error).__name__,
                "error": str(error),
                "send_duration_ms": round(elapsed_ms, 3),
            }
        )
        _write_braille_event(event)
        _log(f"Braille send failed: {error}", force=True)

    bbox = getattr(engine, "bbox", None)
    return rendered, bbox, braille_payload


def _save_print_if_requested(params: dict[str, Any], engine: CADComparisonRenderer, img_data: np.ndarray) -> None:
    if not params.get("print_view"):
        return

    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    next_index = 0
    for file_path in RENDERS_DIR.glob("print_*.pdf"):
        parts = file_path.stem.split("_")
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[1])
            next_index = max(next_index, idx + 1)
        except ValueError:
            continue

    stem = (
        f"print_{next_index}_"
        f"{engine.current_render_mode}_"
        f"{engine.current_cut_depth}_"
        f"{engine.view_current_axis}_"
        f"{np.array(engine.view_current_view_limits).tolist()}"
    )
    pdf_path = RENDERS_DIR / f"{stem}.pdf"
    npy_path = RENDERS_DIR / f"{stem}.npy"
    save_binary_array_as_vector_pdf(img_data, str(pdf_path))
    with npy_path.open("wb") as handle:
        np.save(handle, img_data)


def _img_to_base64_png(img_array: np.ndarray) -> str:
    image = Image.fromarray(img_array.astype("uint8"))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _img_to_png_bytes(img_array: np.ndarray) -> io.BytesIO:
    image = Image.fromarray(img_array.astype("uint8"))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _prepare_render_params(raw_params: dict[str, Any] | None) -> tuple[dict[str, Any], int, bool, str]:
    params = raw_params or {}
    merged_params = dict(DEFAULT_RENDER_PARAMS)
    merged_params.update(params)
    merged_params["view"] = str(merged_params.get("view", "")).lower()

    model_index = _normalize_model_index(merged_params.get("current_model"))
    merged_params["current_model"] = model_index

    move_camera_center = str(merged_params.get("move_camera_center", "none")).lower()
    is_pan_request = move_camera_center != "none"
    fingerprint = json.dumps(merged_params, sort_keys=True, default=str)
    return merged_params, model_index, is_pan_request, fingerprint


def _render_response(merged_params: dict[str, Any], *, source: str) -> dict[str, Any]:
    model_index = int(merged_params.get("current_model", 0))
    rendered, bbox, braille_payload = _render_and_send(merged_params, source=source, model_index=model_index)
    engine = get_or_create_renderer(model_index)
    _save_print_if_requested(merged_params, engine, braille_payload)
    return {
        "status": "success",
        "message": "Render complete",
        "image_shape": list(rendered.shape),
        "bbox": bbox,
        "image_base64": _img_to_base64_png(rendered),
        "model_list": MODEL_NAME_LIST,
        "current_model": model_index,
    }


def _record_command(data: dict[str, Any]) -> int:
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data": data,
    }
    with commands_log_lock:
        commands_log.append(entry)
        return len(commands_log)


def initialize_default_braille_render() -> None:
    _log("Preparing initial render...", force=True)

    try:
        merged_params, model_index, _is_pan_request, _fingerprint = _prepare_render_params(dict(DEFAULT_RENDER_PARAMS))
        rendered, _, _ = _render_and_send(merged_params, source="startup", model_index=model_index)
        _log(f"Initial render ready: shape={tuple(rendered.shape)}", force=True)
    except Exception as error:
        _log(f"Initial render failed: {error}", force=True)


def open_viewer_in_browser() -> None:
    html_path = REPO_ROOT / "accessible-3d-viewer.html"
    if not html_path.exists():
        _log(f"Viewer file not found: {html_path}", force=True)
        return

    try:
        webbrowser.open(html_path.as_uri(), new=1)
        _log(f"Opened viewer: {html_path}", force=True)
    except Exception as error:
        _log(f"Could not open viewer in browser: {error}", force=True)


def _filter_godice_devices(dev_advdata_tuples):
    return [
        (device, adv_data)
        for device, adv_data in dev_advdata_tuples
        if device.name and device.name.startswith("GoDice")
    ]


def _select_closest_device(dev_advdata_tuples):
    return max(dev_advdata_tuples, key=lambda item: item[1].rssi)


async def _godice_notification_callback(number, stability_descr):
    if godice is None:
        return
    if stability_descr not in [godice.StabilityDescriptor.MOVE_STABLE, godice.StabilityDescriptor.STABLE]:
        return
    if number not in VIEW_CUBE_MAPPING:
        return
    with state_lock:
        state.cube_value = VIEW_CUBE_MAPPING[number]
    _log(f"GoDice stable face {number} -> view {state.cube_value}")


async def _godice_worker() -> None:
    if bleak is None or godice is None:
        _log("GoDice dependencies unavailable; cube input disabled.", force=True)
        return

    _log("Searching for GoDice devices...")
    discovery = await bleak.BleakScanner.discover(timeout=8, return_adv=True)
    candidates = _filter_godice_devices(discovery.values())
    if not candidates:
        _log("No GoDice found; cube input disabled.", force=True)
        return

    device, _ = _select_closest_device(candidates)
    _log(f"Connecting GoDice: {device.name} ({device.address})", force=True)

    async with godice.create(device.address, godice.Shell.D6) as dice:
        _log("GoDice connected.", force=True)
        await dice.subscribe_number_notification(_godice_notification_callback)
        while True:
            await asyncio.sleep(30)


def _run_godice_thread() -> None:
    try:
        asyncio.run(_godice_worker())
    except Exception as error:
        _log(f"GoDice integration disabled after error: {error}", force=True)


def _slider_worker() -> None:
    if serial is None or list_ports is None:
        _log("Serial dependencies unavailable; slider input disabled.", force=True)
        return

    while True:
        trinkey_port = None
        for port in list_ports.comports(include_links=False):
            if getattr(port, "pid", None) == 0x8102:
                trinkey_port = port
                break

        if trinkey_port is None:
            _log("Slider Trinkey not found; slider input disabled.", force=True)
            return

        _log(f"Slider Trinkey found at {trinkey_port.device}", force=True)
        try:
            with serial.Serial(trinkey_port.device, timeout=1) as trinkey:
                while True:
                    line = trinkey.readline().decode("utf-8", errors="ignore").strip()
                    if not line.startswith("Slider: "):
                        continue
                    try:
                        value = int(float(line.split(": ", maxsplit=1)[1]))
                    except ValueError:
                        continue
                    with state_lock:
                        state.slider_value = value
        except Exception as error:
            _log(f"Slider disconnected ({error}); retrying in 3s...", force=True)
            time.sleep(3)


def start_optional_hardware_watchers() -> None:
    threading.Thread(target=_run_godice_thread, daemon=True).start()
    threading.Thread(target=_slider_worker, daemon=True).start()


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "status": "running",
            "message": "Accessible 3D Viewer server",
            "endpoints": {
                "/render": "POST - Render CAD view with parameters",
                "/render/image": "GET - Get last rendered image as PNG",
                "/render/base64": "GET - Get last rendered image as base64",
                "/command": "POST - Receive and log command; auto-render if render params exist",
                "/commands": "GET - Retrieve logged commands",
                "/commands/clear": "POST - Clear command log",
                "/commands/stats": "GET - Command statistics",
                "/models": "GET/POST - List or update active model index",
                "/get_data": "GET - Optional cube/slider state",
            },
        }
    )


@app.route("/render", methods=["POST"])
def render_view():
    global last_render_fingerprint, last_render_response

    try:
        merged_params, model_index, is_pan_request, fingerprint = _prepare_render_params(request.get_json(silent=True))

        with state_lock:
            if (
                not is_pan_request
                and merged_params.get("print_view") is not True
                and last_render_fingerprint == fingerprint
                and last_render_response is not None
            ):
                return jsonify(last_render_response), 200

        with state_lock:
            view = merged_params.get("view")
            if isinstance(view, str) and view:
                state.cube_value = view
            state.current_model_index = model_index

        _log(f"Render request: {merged_params}")
        response = _render_response(merged_params, source="http_render")

        with state_lock:
            if merged_params.get("print_view") is not True:
                last_render_fingerprint = fingerprint
                last_render_response = response
        return jsonify(response), 200
    except Exception as error:
        _log(f"Error rendering: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/render/image", methods=["GET"])
def get_render_image():
    if current_render is None:
        return jsonify({"status": "error", "message": "No image has been rendered yet"}), 404
    try:
        return send_file(_img_to_png_bytes(current_render), mimetype="image/png")
    except Exception as error:
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/render/base64", methods=["GET"])
def get_render_base64():
    if current_render is None:
        return jsonify({"status": "error", "message": "No image has been rendered yet"}), 404
    try:
        return jsonify(
            {
                "status": "success",
                "image_base64": _img_to_base64_png(current_render),
                "image_shape": list(current_render.shape),
            }
        ), 200
    except Exception as error:
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/command", methods=["POST"])
def receive_command():
    try:
        data = request.get_json(silent=True) or {}
        command_id = _record_command(data)

        response_data: dict[str, Any] = {
            "status": "success",
            "message": "Command received",
            "command_id": command_id,
        }

        render_keys = {"view", "renderMode", "depth", "zoom", "mode", "move_camera_center", "print_view", "current_model"}
        if any(key in data for key in render_keys):
            merged_params, model_index, _is_pan_request, _fingerprint = _prepare_render_params(data)
            with state_lock:
                view = merged_params.get("view")
                if isinstance(view, str) and view:
                    state.cube_value = view
                state.current_model_index = model_index
            response_data["render"] = _render_response(merged_params, source="command_auto_render")

        return jsonify(response_data), 200
    except Exception as error:
        _log(f"Error processing command: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/commands", methods=["GET"])
def get_commands():
    with commands_log_lock:
        payload = {
            "status": "success",
            "total_commands": len(commands_log),
            "commands": list(commands_log),
        }
    return jsonify(payload), 200


@app.route("/commands/clear", methods=["POST"])
def clear_commands():
    with commands_log_lock:
        count = len(commands_log)
        commands_log.clear()
    return jsonify({"status": "success", "message": f"Cleared {count} commands"}), 200


@app.route("/commands/stats", methods=["GET"])
def get_stats():
    with commands_log_lock:
        snapshot = list(commands_log)

    if not snapshot:
        return jsonify({"status": "success", "total_commands": 0, "stats": {}}), 200

    type_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for entry in snapshot:
        data = entry.get("data", {})
        if not isinstance(data, dict):
            continue
        cmd_type = str(data.get("type", "unknown"))
        action = str(data.get("action", "unknown"))
        type_counts[cmd_type] = type_counts.get(cmd_type, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1

    return jsonify(
        {
            "status": "success",
            "total_commands": len(snapshot),
            "stats": {
                "by_type": type_counts,
                "by_action": action_counts,
                "first_command": snapshot[0].get("timestamp"),
                "last_command": snapshot[-1].get("timestamp"),
            },
        }
    ), 200


@app.route("/models", methods=["GET", "POST"])
def models_endpoint():
    global last_render_fingerprint, last_render_response

    if request.method == "GET":
        with state_lock:
            current_index = state.current_model_index
        return jsonify(
            {
                "status": "success",
                "model_list": MODEL_NAME_LIST,
                "model_paths": [str(model) for model in AVAILABLE_MODELS],
                "current_model": current_index,
            }
        ), 200

    try:
        data = request.get_json(silent=True) or {}
        model_index = _normalize_model_index(data.get("current_model", data.get("model_index")))
        with state_lock:
            state.current_model_index = model_index
            # Changing model invalidates response cache.
            last_render_fingerprint = None
            last_render_response = None
        return jsonify(
            {
                "status": "success",
                "message": "Current model updated",
                "current_model": model_index,
                "model_name": MODEL_NAME_LIST[model_index],
                "model_path": str(AVAILABLE_MODELS[model_index]),
            }
        ), 200
    except Exception as error:
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/get_data", methods=["GET"])
def get_data():
    with state_lock:
        payload = {
            "status": "success",
            "cube_value": state.cube_value,
            "slider_value": state.slider_value,
            "current_model": state.current_model_index,
        }
    return jsonify(payload), 200


def main() -> int:
    _log("Server starting on http://localhost:6969", force=True)
    _log(f"Model directory: {MODEL_DIR}", force=True)
    _log(f"Models found: {len(AVAILABLE_MODELS)}", force=True)
    _log("Endpoints: POST /render, POST /command, GET /get_data", force=True)
    _log(f"Braille send logs: {BRAILLE_LOG_PATH}", force=True)
    if QUIET_MODE:
        _log("Output mode: quiet (set SERVER_VERBOSE=1 for debug logs)", force=True)

    initialize_default_braille_render()
    start_optional_hardware_watchers()
    open_viewer_in_browser()

    _log("Ready.", force=True)
    app.run(debug=False, host="0.0.0.0", port=6969)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())