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
from PIL import Image
import numpy as np
from cad_comparison_lib import CADComparisonRenderer
from braille_display import send_to_braille_display, BrailleDisplayError
from src.converter.render_low_res import save_binary_array_as_vector_pdf

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

# Determine repo root and resolve default coffee mug model path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
first_stl_file = ""
for f in os.listdir(os.path.join(REPO_ROOT, "model")):
    if ".stl" in f:
        first_stl_file = f

_coffee_candidates = [
    os.path.join(REPO_ROOT, "model", first_stl_file)
    #os.path.join(REPO_ROOT, "Mug_after.step"),
]
_default_model = next((p for p in _coffee_candidates if os.path.exists(p)), _coffee_candidates[0])

# Initialize CAD renderer with default models (use the same file for before/after by default)
before_model = _default_model
after_model = _default_model
print(f"Default model set to: {before_model}")

# Global renderer instance (initialized on first use to avoid startup delay)
renderer = None
current_render = None  # Store the last rendered image

# Default render parameters for startup
DEFAULT_RENDER_PARAMS = {
    'view': 'Front',
    'depth': 0,
    'renderMode': 'Outline'
}

def get_or_create_renderer():
    """Lazy initialization of renderer to avoid startup delay."""
    global renderer
    if renderer is None:
        print("Initializing CAD renderer...")
        renderer = CADComparisonRenderer(before_model, after_model)
        print("Renderer initialized successfully!")
    return renderer

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
    try:
        params = request.get_json()
        cube_value = params["view"]
        
        print("\n" + "=" * 60)
        print("RENDERING CAD VIEW")
        print("=" * 60)
        print(f"Parameters: {json.dumps(params, indent=2)}")
        #params["mode"] = "side_by_side"
        
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
                file_name_inc = int(file_name.split("print_")[1].split(".")[0])
                if file_name_inc >= new_file_name_inc:
                    new_file_name_inc = file_name_inc+1
            save_binary_array_as_vector_pdf(img_data, os.path.join("renders", "print_"+str(new_file_name_inc)+".pdf"))
            with open(os.path.join("renders", "print_"+str(new_file_name_inc)+".npy"), "wb") as fp:
                np.save(fp, img_data)
            #img.save(os.path.join("renders", "0.png"), format="PNG")

        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return jsonify({
            'status': 'success',
            'message': 'Render complete',
            'image_shape': img_array.shape,
            'bbox': r.bbox,
            'image_base64': img_base64
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
        x = trinkey.readline().decode('utf-8')
        if not x.startswith("Slider: "):
            continue
        val = int(float(x.split(": ")[1]))
        slider_value = val
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
    print(f"  Before: {before_model}")
    print(f"  After:  {after_model}")
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

    # Render once on startup and send to braille display
    initialize_default_braille_render()
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    print("\nWaiting for commands...\n")
    # threading.Thread(target=dice_main_thread, daemon=True).start()
    #threading.Thread(target=start_slider_trinkey, daemon=True).start()
    app.run(debug=False, host='0.0.0.0', port=6969)
