#!/usr/bin/env python3
"""
Web server to receive commands/changes from the accessible 3D viewer.
This server logs all user interactions from the HTML interface.
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime
from copy import copy
import json
import time
import struct
import os
import io
import base64
import traceback
from PIL import Image
import numpy as np
from cad_comparison_lib import CADComparisonRenderer
from braille_display import send_to_braille_display, BrailleDisplayError, _connect
from src.converter.render_low_res import save_binary_array_as_vector_pdf

import asyncio
from bleak import BleakClient, BleakScanner
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
current_model_name = 0

# Determine repo root and resolve default coffee mug model path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SUPPORTED_MODEL_EXTENSIONS = (".stl", ".step", ".stp", ".brep")

def _discover_models(root_dir):
    discovered = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            lower_name = filename.lower()
            if lower_name.endswith(SUPPORTED_MODEL_EXTENSIONS):
                full_path = os.path.join(dirpath, filename)
                discovered.append(full_path)
    discovered.sort()
    return discovered

def _build_model_display_name(path):
    rel_path = os.path.relpath(path, os.path.join(REPO_ROOT, "model"))
    stem, _ext = os.path.splitext(rel_path)
    return stem.replace("\\", "/")

model_list = _discover_models(os.path.join(REPO_ROOT, "model"))
model_name_list = [_build_model_display_name(path) for path in model_list]

#model_list = ["coffee", "second cup", "teaparty"]
renderer_dict = {}

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

# Global renderer instance (initialized on first use to avoid startup delay)
renderer = None
current_render = None  # Store the last rendered image
device = None
cube_value = None
slider_value = 10

# Default render parameters for startup
DEFAULT_RENDER_PARAMS = {
    'view': 'Front',
    'depth': 0,
    'renderMode': 'Outline'
}

DEFAULT_OUTPUT_DEVICE = "dot"


def _coerce_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed

def _resolve_model_index(model_value):
    """Map UI model selection to a safe model index."""
    if not model_list:
        return -1
    if model_value == "none" or model_value is None:
        return 0
    try:
        idx = int(model_value)
    except (TypeError, ValueError):
        return 0
    if idx < 0 or idx >= len(model_list):
        return 0
    return idx


def _resolve_output_device(device_value):
    """Map UI output device selection to supported transport values."""
    if device_value is None:
        return DEFAULT_OUTPUT_DEVICE
    text = str(device_value).strip().lower()
    if text == "dot":
        return "dotpad"
    if text in {"monarch", "dotpad", "auto"}:
        return text
    return DEFAULT_OUTPUT_DEVICE


def get_or_create_renderer(model_index, pool):
    """Get a renderer for a model index from the given pool, creating it if needed."""
    if model_index < 0 or model_index >= len(model_list):
        raise ValueError("No models are available to render")
    if model_index not in pool:
        model_path = model_list[model_index]
        print(f"Initializing CAD renderer for model index {model_index}: {model_path}")
        renderer = CADComparisonRenderer(model_path, model_path)
        renderer.init_device(device)
        pool[model_index] = renderer
        print("Renderer initialized successfully!")
    return pool[model_index]

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

def initialize_default_braille_render():
    """Render once at startup with default params and send to braille display."""
    global current_render
    try:
        print("\n" + "=" * 60)
        print("INITIAL DEFAULT RENDER TO BRAILLE DISPLAY")
        print("=" * 60)
        print(f"Default params: {json.dumps(DEFAULT_RENDER_PARAMS)}")
        r = get_or_create_renderer()
        img_array = r.render(DEFAULT_RENDER_PARAMS)
        current_render = img_array
        print(f"Rendered image shape: {img_array.shape}")
        try:
            img_data = img_array[:, :, 3]
            bytes_written = send_to_braille_display(img_data, device)
            #print(f"Braille write: {bytes_written} bytes")
            #print("img_data summary:\n" + _format_img_data_repr(img_data))
        except BrailleDisplayError as e:
            print(f"Braille send failed: {e}")
        print("Default render complete.")
        print("=" * 60 + "\n")
    except Exception as e:
        print(f"Default render failed: {e}")
        print(traceback.format_exc())

@app.route('/')
def home():
    """Home endpoint with server status"""
    return jsonify({
        'status': 'running',
        'message': 'Accessible 3D Viewer Command Server with CAD Rendering',
        'endpoints': {
            '/command': 'POST - Send commands from the viewer (triggers render)',
            '/render': 'POST - Render CAD view with parameters',
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
        print(params["current_model"])
        current_model_name = int(params["current_model"])
        print(current_model_name)
        
        # Get or create renderer
        r = get_or_create_renderer()
        
        # Render the view
        img_array = r.render(params)
        current_render = img_array
        #print(cube_value)
        
        print(f"Rendered image shape: {img_array.shape}")
        # Send only the fourth axis (channel index 3) to the braille display, no resizing
        try:
            img_data = ~img_array[:, :, 0]
            for i in range(img_data.shape[0]):
                for j in range(img_data.shape[1]):
                    if img_data[i,j] > 0:
                        print(1, end='')
                    else:
                        print(0, end='')
                print()
            img_data[img_data > 0] = 255
            bytes_written = send_to_braille_display(img_data, device)
            #print(f"Braille write: {bytes_written} bytes")
            #print("img_data summary:\n" + _format_img_data_repr(img_data))
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
        
        return jsonify({
            'status': 'success',
            'message': 'Render complete',
            'image_shape': img_array.shape,
            'bbox': r.bbox,
            'image_base64': img_base64,
            'model_list': model_name_list
        }), 200
        
    except Exception as e:
        print(f"Error rendering: {str(e)}")
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
                    bytes_written = send_to_braille_display(img_data, device)
                    #print(f"Braille write: {bytes_written} bytes")
                    #print("img_data summary:\n" + _format_img_data_repr(img_data))
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

world_up = np.array([0, 0, 1])

# ---- CONFIG ----
DEVICE_NAME = "WT901BLE68"     # change if needed
RUN_DURATION = 30              # seconds to run before disconnecting

# UUID for notify characteristic (common for WT901BLECL)
NOTIFY_UUID = "0000ffe4-0000-1000-8000-00805f9a34fb"


def hex_to_short(raw_data):
    return list(struct.unpack("hhh", bytearray(raw_data)))

buffer = b""
angle = [0,0,0]
first_angle = []

verts = np.array([
    [-1, -1, -1],
    [ 1, -1, -1],
    [ 1,  1, -1],
    [-1,  1, -1],
    [-1, -1,  1],
    [ 1, -1,  1],
    [ 1,  1,  1],
    [-1,  1,  1],
])

faces = np.array([
    [0,1,2],[0,2,3],  # bottom
    [4,5,6],[4,6,7],  # top
    [0,1,5],[0,5,4],  # front
    [2,3,7],[2,7,6],  # back
    [1,2,6],[1,6,5],  # right
    [3,0,4],[3,4,7],  # left
])

face_normals = np.array([
    [ 0,  0,  1],  # top (+Z)
    [ 0,  0, -1],  # bottom (-Z)
    [ 0,  1,  0],  # front (-X)
    [ 0, -1,  0],  # back (+X)
    [ 1,  0,  0],  # right (+Y)
    [-1,  0,  0],  # left (-Y)
])
face_names = ["z+", "z-", "x-", "x+", "y+", "y-"]

# --- Rotation helper ---
def euler_to_matrix(x_deg, y_deg, z_deg):
    x = np.radians(x_deg)
    y = np.radians(y_deg)
    z = np.radians(z_deg)
    # Rotation matrices around X, Y, Z
    Rx = np.array([[1,0,0],[0,np.cos(x),-np.sin(x)],[0,np.sin(x),np.cos(x)]])
    Ry = np.array([[np.cos(y),0,np.sin(y)],[0,1,0],[-np.sin(y),0,np.cos(y)]])
    Rz = np.array([[np.cos(z),-np.sin(z),0],[np.sin(z),np.cos(z),0],[0,0,1]])
    # Combine Z * Y * X (intrinsic rotation)
    return Rz @ Ry @ Rx

def parse_61_frame(msg):
	if msg[1] != 0x61:  # check frame type
		return None
	angle = [hex_to_short(msg[14:20])[i] / 32768.0 * 180 for i in range(0, 3)]
	#print("x", angle[0], "y", angle[1], "z", angle[2])
	return angle

def notification_handler(sender, data):
	global buffer
	global angle
	global first_angle
	buffer += data
	parts = buffer.split(b"\x55")
	buffer = parts[-1]

	for chunk in parts[:-1]:  
		# add header back for parsing
		msg = b"\x55" + chunk
		try:
			res = parse_61_frame(msg)
			if res is not None:
				angle = res
				if len(first_angle) > 0:
					angle[0] -= first_angle[0]
					angle[1] -= first_angle[1]
					angle[2] -= first_angle[2]
		except:
			continue

async def main():
    global angle
    global first_angle
    global cube_value
    print("Scanning for device...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME)

    if device is None:
        print("Device not found.")
        return

    print(f"Connecting to {device.name}...")
    previous_angle = []

    async with BleakClient(device) as client:

        print("Connected!")

        print("\n=== SERVICES ===")
        for service in client.services:
            print(f"[Service] {service.uuid}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"  └── {char.uuid} ({props})")

            if not client.is_connected:
                print("Failed to connect.")
                return

        print("Connected!")

        await client.start_notify(NOTIFY_UUID, notification_handler)

        print(f"Reading angles for {RUN_DURATION} seconds...\n")
        start = time.time()

        #while time.time() - start < RUN_DURATION:
        while True:
            # we switch faces if a particular axis passes the 45 degree mark
            await asyncio.sleep(0.1)
            #print(angle)
            if len(first_angle) == 0 and time.time() - start > 5.0 and angle is not None:
                first_angle = copy(angle)
                print("FIRST_ANGLE", first_angle)
            R = euler_to_matrix(*angle)
            rotated_normals = face_normals @ R.T  # rotate normals to world frame
            dots = rotated_normals @ world_up  # dot product with up vector
            up_face_index = np.argmax(dots)    # the face most aligned with up
            up_face_name = face_names[up_face_index]
            #print(up_face_name)
            cube_value = up_face_name

        await client.stop_notify(NOTIFY_UUID)

print("Disconnected.")

def run_wimotion_loop():
    asyncio.run(main())


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
        'slider_value': slider_value,
        'model_list': model_name_list,
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
        device = _connect(scan_timeout=6.0, prefer_dotpad=True, preferred_device="auto")
    except BrailleDisplayError as e:
        device = None
        print(f"Braille display auto-connect skipped: {e}")
    # Render once on startup and send to braille display (background so Flask starts immediately)
    threading.Thread(target=initialize_default_braille_render, daemon=True).start()
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    print("\nWaiting for commands...\n")
    threading.Thread(target=run_wimotion_loop, daemon=True).start()
    #threading.Thread(target=start_slider_trinkey, daemon=True).start()
    app.run(debug=False, host='0.0.0.0', port=6969)
