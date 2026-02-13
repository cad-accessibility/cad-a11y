
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Accessible NumPy Array Navigator for Braille Display

- Loads 2D .npy files named shape_{i}_view_{j}.npy from a robust directory
- Keyboard navigation: model/view indices (no projection axis)
- Sends active view to Braille display using braille_display.py
- Designed for screen readers and Braille displays
"""
import sys
import os

# Dependency checks
try:
    import numpy as np
except ImportError:
    print("Error: numpy is not installed. Please run 'pip install numpy' and try again.")
    sys.exit(1)
try:
    from braille_display import send_to_braille_display
except ImportError:
    print("Error: braille_display.py or its dependencies are missing. Please ensure braille_display.py is present and install 'hidapi' with 'pip install hidapi'.")
    sys.exit(1)

# Platform-specific single keypress input (no Enter required)
if os.name == 'nt':
    import msvcrt
else:
    import termios
    import tty

# Robust path to demo_views directory (relative to script location)
NPY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_views")

# Dynamically discover available model/view indices
import re
file_re = re.compile(r"shape_(\d+)_view_(\d+)\.npy$")
model_indices = set()
view_indices = set()
if not os.path.isdir(NPY_DIR):
    print(f"Error: Directory not found: {NPY_DIR}")
    sys.exit(1)
for fname in os.listdir(NPY_DIR):
    m = file_re.match(fname)
    if m:
        model_indices.add(int(m.group(1)))
        view_indices.add(int(m.group(2)))
if not model_indices or not view_indices:
    print(f"Error: No valid shape_#_view_#.npy files found in {NPY_DIR}")
    sys.exit(1)
model_indices = sorted(model_indices)
view_indices = sorted(view_indices)
N_MODELS = len(model_indices)
N_VIEWS = len(view_indices)

# Help text for screen readers and Braille displays
HELP_TEXT = """
Keyboard commands (press a key):
    h: Print this help text
    q: Quit
    w / s: Increase / decrease model index
    e / d: Increase / decrease view index
"""

def print_help():
    print(HELP_TEXT.strip())

def print_state(model_idx, view_idx):
    # Show 1-based indices for user clarity
    print(f"Current selection: model={model_indices[model_idx]}, view={view_indices[view_idx]}")

def get_key():
    if os.name == 'nt':
        ch = msvcrt.getch()
        try:
            return ch.decode('utf-8').lower()
        except Exception:
            return ''
    else:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch.lower()

def wrap(val, maxval):
    return (val + maxval) % maxval

def load_view_array(model_idx, view_idx):
    # Use discovered indices for filenames
    filename = f"shape_{model_indices[model_idx]}_view_{view_indices[view_idx]}.npy"
    path = os.path.join(NPY_DIR, filename)
    if not os.path.exists(path):
        print(f"Warning: File not found: {path}")
        return None
    try:
        arr = np.load(path)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return None
    if arr.ndim != 2:
        print(f"Error: Array in {path} must be 2D. Got shape {arr.shape}")
        return None
    return arr

model_idx = 0
view_idx = 0

print_help()
print_state(model_idx, view_idx)

try:
    while True:
        key = get_key()
        if key == 'q':
            print("Exiting.")
            break
        elif key == 'h':
            print_help()
        elif key == 'w':
            model_idx = wrap(model_idx + 1, N_MODELS)
        elif key == 's':
            model_idx = wrap(model_idx - 1, N_MODELS)
        elif key == 'e':
            view_idx = wrap(view_idx + 1, N_VIEWS)
        elif key == 'd':
            view_idx = wrap(view_idx - 1, N_VIEWS)
        else:
            print("Unknown key. Press 'h' for help.")
            continue
        print_state(model_idx, view_idx)
        arr = load_view_array(model_idx, view_idx)
        if arr is not None:
            send_to_braille_display(arr)
except Exception as e:
    print(f"Fatal error: {e}")
