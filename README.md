# CAD Accessibility Tool

A tool for converting CAD models in BREP format to accessible SVG representations from multiple viewpoints.

## Project Overview

This project aims to make CAD models more accessible by converting BREP (Boundary Representation) files into SVG visualizations that can be easily viewed and shared. The tool provides multiple views of CAD models (top, front, right) to enhance accessibility for users who may benefit from 2D representations.

## Directory Structure

```
cad-a11y/
│
├── app.py                  # Main web application entry point
├── environment.yml         # Conda environment configuration
│
├── src/
│   ├── converter/          # Core conversion logic
│   │   └── brep_to_svg.py  # BREP to SVG conversion script
│   │
│   ├── models/             # Model files
│   │   ├── brep/           # Input BREP files
│   │   └── svg/            # Output SVG files
│   │
│   └── webapp/             # Web application code
│       ├── app/            # Flask application
│       ├── config/         # Configuration settings
│       ├── routes/         # Route definitions
│       ├── static/         # Static assets (CSS, JS, images)
│       └── templates/      # HTML templates
│
└── scripts/                # Utility scripts
    └── generate_cylinder_brep.py  # Script to generate sample BREP files
```

## Getting Started

### Prerequisites

- Python 3.9+
- Conda (for environment management)

### Installation

1. Clone this repository
2. Create and activate the Conda environment:
   ```
   conda env create -f environment.yml
   conda init zsh
   [unsure if this is needed] conda create --name cad-a11y
   conda activate cad-a11y
   conda install python=3.10
   conda install pip
   conda install pythonocc-core
   [use your package manager] to install freecad
   pip install -r requirements.txt   ```

### Running the Command-line Tool


#### Converting OpenSCAD to STL
Example to convert OpenSCAD files to STL format (new feature):

```
python scripts/scad_to_stl.py openscad_scripts/vase.scad model/vase.stl
```

For more options and detailed usage, see `scripts/README_scad_to_step.md`.

### Connecting devices
## Connecting the Monarch
1. Make sure that the Monarch is charged enough to be able to turn it on. If it does not turn on (after clicking for longer than 3 seconds), you can use a laptop charger to charge it.
2. Turn it off.
3. Connect it with the accompanying USB-C-to-USB-C cable to your laptop
4. Turn the Monarch on.
5. On the Monarch, use the right arrow keys to navigate to "Braille Terminal". For that, press the "up" arrow twice. Then, press the right-most braille keyboard button twice (it sometimes says "USB button" after the first keystroke. This is a good sign.).

## Connecting the Tactile ViewCube
1. Charge the GoDice with the accompanying charger for around 10 seconds. The "5" face has the charging connectors.
2. Put the GoDice in the Tactile ViewCube, and make sure that the face "3" points towards the round nubbin and the face "6" points upwards.
3. Close the Tactile ViewCube.

## Connecting the Adafruit Slider Trinkey
1. Optional: Put the slider on its plastic bed to be able to more easily manipulate it.
2. Plug the slider into an USB port or use the adaptor to plug it into an USB-C port. 

### Running the Web Application

After connecting all the devices, as described above, you can now run the web application:

```
python server_cube_slider.py
```

Then, open accessible-3d-viewer.html in a browser.

You should now be able to interact with the website and your terminal and your monarch should serve as displays.

### Building a Windows .exe

This repository includes a PyInstaller setup for `server.py`:

- Spec file: `server_pyinstaller.spec`
- Build script: `build_windows_exe.ps1`

On Windows PowerShell:

```powershell
# Optional: set up dependencies first
.\setup_windows.ps1

# Build using conda env (default: cad-a11y)
.\build_windows_exe.ps1

# Or build with current Python instead of conda
.\build_windows_exe.ps1 -UseCurrentPython
```

The executable is created at:

```text
dist\cad-a11y-server\cad-a11y-server.exe
```

Run it from PowerShell:

```powershell
& '.\dist\cad-a11y-server\cad-a11y-server.exe'
```

Notes:

- This is an onedir build (folder with exe + bundled dependencies), which is more reliable for the CAD stack.
- The build includes `model/` and `accessible-3d-viewer.html` so runtime paths work in the packaged app.

## Future Development

- Expanding to support additional CAD file formats
- Improving SVG rendering and accessibility features
- Adding user authentication and file management
