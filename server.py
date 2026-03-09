#!/usr/bin/env python3
"""Accessible 3D viewer server.

Features:
- Receives render state from the viewer and renders with CADComparisonRenderer.
- Sends rendered output to a connected braille display using braille_display.py.
- Optionally reads GoDice orientation and Slider Trinkey position if hardware is present.
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
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, has_request_context, jsonify, request
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
renderer: CADComparisonRenderer | None = None
current_render: np.ndarray | None = None
state_lock = threading.Lock()
braille_log_lock = threading.Lock()
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


def _find_default_model() -> Path:
    stl_files = sorted(MODEL_DIR.glob("*.stl"))
    if stl_files:
        return stl_files[0]

    step_files = sorted(MODEL_DIR.glob("*.step")) + sorted(MODEL_DIR.glob("*.STEP"))
    if step_files:
        return step_files[0]

    raise FileNotFoundError(f"No .stl/.step model found in {MODEL_DIR}")


DEFAULT_MODEL = _find_default_model()


def get_or_create_renderer() -> CADComparisonRenderer:
    global renderer
    if renderer is None:
        _log(f"Initializing CAD renderer with: {DEFAULT_MODEL}")
        out_guard, err_guard = _renderer_stdio_guard()
        with out_guard, err_guard:
            renderer = CADComparisonRenderer(str(DEFAULT_MODEL), str(DEFAULT_MODEL))
    return renderer


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


def _render_and_send(params: dict[str, Any], *, source: str) -> tuple[np.ndarray, list[float] | None, np.ndarray]:
    global current_render

    engine = get_or_create_renderer()
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
        }
    event: dict[str, Any] = {
        "event": "braille_send",
        "sequence": sequence,
        "source": source,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": str(DEFAULT_MODEL),
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


def initialize_default_braille_render() -> None:
    _log("Preparing initial render...", force=True)

    try:
        rendered, _, _ = _render_and_send(dict(DEFAULT_RENDER_PARAMS), source="startup")
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
                "/get_data": "GET - Optional cube/slider state",
            },
        }
    )


@app.route("/render", methods=["POST"])
def render_view():
    global last_render_fingerprint, last_render_response

    try:
        params = request.get_json(silent=True) or {}
        merged_params = dict(DEFAULT_RENDER_PARAMS)
        merged_params.update(params)
        merged_params["view"] = str(merged_params.get("view", "")).lower()

        move_camera_center = str(merged_params.get("move_camera_center", "none")).lower()
        is_pan_request = move_camera_center != "none"

        fingerprint = json.dumps(merged_params, sort_keys=True, default=str)

        with state_lock:
            if (
                not is_pan_request
                and
                merged_params.get("print_view") is not True
                and last_render_fingerprint == fingerprint
                and last_render_response is not None
            ):
                return jsonify(last_render_response), 200

        with state_lock:
            view = merged_params.get("view")
            if isinstance(view, str) and view:
                state.cube_value = view
        _log(f"Render request: {merged_params}")

        rendered, bbox, braille_payload = _render_and_send(merged_params, source="http_render")
        engine = get_or_create_renderer()
        _save_print_if_requested(merged_params, engine, braille_payload)

        response = {
            "status": "success",
            "message": "Render complete",
            "image_shape": list(rendered.shape),
            "bbox": bbox,
            "image_base64": _img_to_base64_png(rendered),
        }
        with state_lock:
            if merged_params.get("print_view") is not True:
                last_render_fingerprint = fingerprint
                last_render_response = response
        return jsonify(response), 200
    except Exception as error:
        _log(f"Error rendering: {error}", force=True)
        return jsonify({"status": "error", "message": str(error)}), 400


@app.route("/get_data", methods=["GET"])
def get_data():
    with state_lock:
        payload = {
            "status": "success",
            "cube_value": state.cube_value,
            "slider_value": state.slider_value,
        }
    return jsonify(payload), 200


def main() -> int:
    _log("Server starting on http://localhost:6969", force=True)
    _log(f"Model: {DEFAULT_MODEL}", force=True)
    _log("Endpoints: POST /render, GET /get_data", force=True)
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