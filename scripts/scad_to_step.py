#!/usr/bin/env python3
"""
OpenSCAD to STEP Converter

This script converts OpenSCAD (.scad) files to STEP files using OpenSCAD's native export capability.
OpenSCAD can export directly to various formats including STL and 3MF, and then we can optionally
convert using external tools.

Usage:
    python scad_to_step.py input.scad [output.step]

Requirements:
- OpenSCAD must be installed and available in PATH
"""

import argparse
import os
import sys
import subprocess
import tempfile
from pathlib import Path


def check_openscad():
    """Check if OpenSCAD is available in the system PATH."""
    try:
        result = subprocess.run(['openscad', '--version'], 
                               capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def scad_to_stl(scad_file, stl_file, resolution=50):
    """
    Convert OpenSCAD file to STL using the OpenSCAD command line tool.
    
    Args:
        scad_file (str): Path to input .scad file
        stl_file (str): Path to output .stl file
        resolution (int): Resolution parameter for OpenSCAD ($fn value)
    
    Returns:
        bool: True if conversion was successful, False otherwise
    """
    try:
        # OpenSCAD command with parameters
        cmd = [
            'openscad',
            '-o', stl_file,
            '--export-format', 'binstl',  # Binary STL for smaller file size
            '-D', f'$fn={resolution}',    # Set resolution
            scad_file
        ]
        
        print(f"Converting {scad_file} to STL...")
        print(f"Command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            print(f"OpenSCAD error: {result.stderr}")
            return False
            
        if not os.path.exists(stl_file) or os.path.getsize(stl_file) == 0:
            print("Error: STL file was not created or is empty")
            return False
            
        print(f"Successfully created STL file: {stl_file}")
        return True
        
    except subprocess.TimeoutExpired:
        print("Error: OpenSCAD conversion timed out")
        return False
    except Exception as e:
        print(f"Error during SCAD to STL conversion: {e}")
        return False


def stl_to_step_with_pythonocc(stl_file, step_file):
    """
    Convert STL file to STEP using PythonOCC (if available).
    
    Args:
        stl_file (str): Path to input .stl file
        step_file (str): Path to output .step file
    
    Returns:
        bool: True if conversion was successful, False otherwise
    """
    try:
        print("Attempting conversion using PythonOCC...")
        
        # Try to import PythonOCC modules
        from OCC.Extend.DataExchange import read_stl_file, write_step_file
        
        # Read the STL file
        mesh_shape = read_stl_file(stl_file)
        if mesh_shape is None:
            print("Error: Could not read STL file with PythonOCC")
            return False
        
        print("STL file loaded successfully with PythonOCC")
        
        # Write the STEP file directly from the mesh shape
        success = write_step_file(mesh_shape, step_file)
        
        # Sometimes PythonOCC returns False even when the file is created successfully
        # Check if the file actually exists and has reasonable size
        if not success:
            if os.path.exists(step_file) and os.path.getsize(step_file) > 1000:
                print(f"Note: PythonOCC reported failure but STEP file was created successfully")
                success = True
        
        if success:
            print(f"Successfully created STEP file: {step_file}")
            return True
        else:
            print("Error: Could not write STEP file with PythonOCC")
            return False
            
    except ImportError as e:
        print(f"PythonOCC not available: {e}")
        return False
    except Exception as e:
        print(f"Error during STL to STEP conversion with PythonOCC: {e}")
        return False


def stl_to_step_with_external_tools(stl_file, step_file):
    """
    Try to convert STL to STEP using external tools like FreeCAD or OpenCASCADE.
    
    Args:
        stl_file (str): Path to input .stl file
        step_file (str): Path to output .step file
    
    Returns:
        bool: True if conversion was successful, False otherwise
    """
    # Try FreeCAD first
    try:
        print("Attempting conversion using FreeCAD...")
        freecad_script = f'''
import FreeCAD
import Import
import Part

# Import STL
doc = FreeCAD.newDocument()
Import.insert("{stl_file}", doc.Name)

# Get the imported object
obj = doc.Objects[0]

# Try to convert mesh to solid
if hasattr(obj, 'Mesh'):
    mesh = obj.Mesh
    # Create shape from mesh
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.1)
    
    # Create a Part object
    part_obj = doc.addObject("Part::Feature", "MeshSolid")
    part_obj.Shape = shape
    
    # Export as STEP
    Part.export([part_obj], "{step_file}")
    print("FreeCAD conversion successful")
else:
    print("Could not find mesh in FreeCAD object")

doc.close()
'''
        
        # Try to run FreeCAD in headless mode
        cmd = ['freecad', '--console', '--python-code', freecad_script]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0 and os.path.exists(step_file):
            print(f"Successfully created STEP file using FreeCAD: {step_file}")
            return True
        else:
            print("FreeCAD conversion failed or not available")
            
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        print(f"FreeCAD not available or failed: {e}")
    
    return False


def scad_to_step(scad_file, step_file, resolution=50, keep_stl=False, prefer_method=None):
    """
    Convert OpenSCAD file directly to STEP file.
    
    Args:
        scad_file (str): Path to input .scad file
        step_file (str): Path to output .step file
        resolution (int): Resolution parameter for OpenSCAD
        keep_stl (bool): Whether to keep the intermediate STL file
        prefer_method (str): Preferred conversion method ('pythonocc', 'freecad', or None for auto)
    
    Returns:
        bool: True if conversion was successful, False otherwise
    """
    # Validate input file
    if not os.path.exists(scad_file):
        print(f"Error: Input file does not exist: {scad_file}")
        return False
    
    if not scad_file.lower().endswith('.scad'):
        print(f"Warning: Input file does not have .scad extension: {scad_file}")
    
    # Create temporary STL file
    if keep_stl:
        stl_file = step_file.replace('.step', '.stl').replace('.STEP', '.stl')
    else:
        temp_dir = tempfile.gettempdir()
        stl_file = os.path.join(temp_dir, f"temp_{os.getpid()}.stl")
    
    try:
        # Step 1: Convert SCAD to STL
        if not scad_to_stl(scad_file, stl_file, resolution):
            return False
        
        # Step 2: Convert STL to STEP
        conversion_success = False
        
        if prefer_method == 'pythonocc' or prefer_method is None:
            conversion_success = stl_to_step_with_pythonocc(stl_file, step_file)
        
        if not conversion_success and (prefer_method == 'freecad' or prefer_method is None):
            conversion_success = stl_to_step_with_external_tools(stl_file, step_file)
        
        if not conversion_success:
            print("Error: All conversion methods failed.")
            print("Suggestions:")
            print("1. Install PythonOCC: conda install pythonocc-core")
            print("2. Install FreeCAD: brew install freecad")
            print("3. Use the STL file directly (already created)")
            return False
        
        return True
        
    finally:
        # Clean up temporary STL file if not keeping it
        if not keep_stl and os.path.exists(stl_file):
            try:
                os.remove(stl_file)
                print(f"Cleaned up temporary STL file: {stl_file}")
            except Exception as e:
                print(f"Warning: Could not remove temporary STL file: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Convert OpenSCAD files to STEP format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scad_to_step.py model.scad
  python scad_to_step.py model.scad output.step
  python scad_to_step.py model.scad --resolution 100 --keep-stl
  python scad_to_step.py model.scad --method pythonocc
        """
    )
    
    parser.add_argument('input', help='Input OpenSCAD (.scad) file')
    parser.add_argument('output', nargs='?', help='Output STEP file (optional)')
    parser.add_argument('-r', '--resolution', type=int, default=50,
                       help='OpenSCAD resolution parameter ($fn) (default: 50)')
    parser.add_argument('--keep-stl', action='store_true',
                       help='Keep intermediate STL file')
    parser.add_argument('--method', choices=['pythonocc', 'freecad'], 
                       help='Preferred conversion method')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    print(f"OpenSCAD to STEP Converter")
    print(f"Input file: {args.input}")
    
    # Check if OpenSCAD is available
    if not check_openscad():
        print("Error: OpenSCAD is not available in the system PATH")
        print("Please install OpenSCAD and ensure it's accessible from command line")
        print("  macOS: brew install openscad")
        print("  Ubuntu: sudo apt-get install openscad")
        print("  Windows: Download from https://openscad.org/downloads.html")
        sys.exit(1)
    
    # Determine output filename
    if args.output:
        output_file = args.output
    else:
        # Replace .scad extension with .step
        input_path = Path(args.input)
        output_file = str(input_path.with_suffix('.step'))
    
    # Ensure output directory exists
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    print(f"Output file: {output_file}")
    print(f"Resolution: {args.resolution}")
    if args.method:
        print(f"Preferred method: {args.method}")
    
    # Perform the conversion
    success = scad_to_step(args.input, output_file, args.resolution, args.keep_stl, args.method)
    
    if success:
        print(f"\nConversion completed successfully!")
        print(f"Output file: {output_file}")
        
        # Show file size
        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            print(f"File size: {size} bytes ({size/1024:.1f} KB)")
    else:
        print("\nConversion failed!")
        sys.exit(1)


if __name__ == '__main__':
    main()
