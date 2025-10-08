# OpenSCAD to STEP Converter

A Python script that converts OpenSCAD (.scad) files to STEP (.step) format for use in CAD applications and the CAD accessibility tool.

## Overview

This script provides a two-step conversion process:
1. **OpenSCAD to STL**: Uses OpenSCAD's command-line interface to render the .scad file to STL format
2. **STL to STEP**: Converts the STL mesh to STEP format using PythonOCC or FreeCAD

## Requirements

### Essential
- **OpenSCAD**: Must be installed and available in system PATH
  - macOS: `brew install openscad`
  - Ubuntu: `sudo apt-get install openscad`
  - Windows: Download from https://openscad.org/downloads.html

### For STEP Conversion (at least one required)
- **PythonOCC**: `conda install pythonocc-core` (recommended)
- **FreeCAD**: `brew install freecad` (alternative)

## Usage

### Basic Usage
```bash
# Convert with default settings (output: model.step)
python scripts/scad_to_step.py model.scad

# Specify output filename
python scripts/scad_to_step.py model.scad output.step
```

### Advanced Options
```bash
# Higher resolution (more detail, larger file, slower)
python scripts/scad_to_step.py model.scad --resolution 100

# Keep intermediate STL file
python scripts/scad_to_step.py model.scad --keep-stl

# Force specific conversion method
python scripts/scad_to_step.py model.scad --method pythonocc
python scripts/scad_to_step.py model.scad --method freecad

# Get help
python scripts/scad_to_step.py --help
```

## Parameters

- `input`: Input OpenSCAD (.scad) file (required)
- `output`: Output STEP file path (optional, defaults to input name with .step extension)
- `--resolution`: OpenSCAD resolution parameter ($fn) for mesh quality (default: 50)
- `--keep-stl`: Keep the intermediate STL file for inspection
- `--method`: Force specific conversion method (`pythonocc` or `freecad`)
- `--verbose`: Enable verbose output

## Resolution Guidelines

The `--resolution` parameter controls the OpenSCAD `$fn` setting, which affects:
- **Quality**: Higher values = smoother curves and better detail
- **File Size**: Higher values = larger files
- **Processing Time**: Higher values = slower conversion

Recommended values:
- **Draft**: 20-30 (fast, good for testing)
- **Standard**: 50-75 (good balance)
- **High Quality**: 100-150 (detailed, slow)
- **Production**: 200+ (very detailed, very slow)

## Example

Convert the included coffee mug example:
```bash
cd /path/to/cad-a11y
python scripts/scad_to_step.py "coffee mug.scad" coffee_mug.step --resolution 75 --keep-stl
```

This will:
1. Render the OpenSCAD file to STL with 75 segments per circle
2. Convert the STL to STEP format
3. Keep both the STL and STEP files for inspection

## Integration with CAD-A11Y

Once you have a STEP file, you can use it with the other tools in this repository:

```bash
# Convert STEP to accessible SVG views
python src/converter/brep_to_svg.py coffee_mug.step -o svg_output/

# Use in the web application
python app.py
# Then upload the STEP file through the web interface
```

## Troubleshooting

### OpenSCAD Not Found
```
Error: OpenSCAD is not available in the system PATH
```
**Solution**: Install OpenSCAD and ensure it's in your PATH

### No Conversion Methods Available
```
Error: All conversion methods failed.
```
**Solutions**:
1. Install PythonOCC: `conda install pythonocc-core`
2. Install FreeCAD: `brew install freecad`
3. Use the generated STL file directly

### Large File Sizes
STEP files can be quite large (several MB) compared to the original SCAD files (few KB). This is normal because:
- SCAD files contain parametric instructions
- STEP files contain the actual 3D mesh geometry
- You can reduce size by lowering the `--resolution` parameter

### Slow Conversion
For complex models:
- Use lower resolution for testing (`--resolution 20`)
- Increase resolution only for final output
- Consider simplifying the OpenSCAD model if possible

## File Format Details

- **Input**: `.scad` files (OpenSCAD format)
- **Intermediate**: `.stl` files (STereoLithography format)
- **Output**: `.step` or `.stp` files (Standard for Exchange of Product Data)

STEP files are widely supported by CAD applications and can be used for:
- 3D printing preparation
- CAD software import
- Engineering analysis
- Accessibility visualization (with this tool)
