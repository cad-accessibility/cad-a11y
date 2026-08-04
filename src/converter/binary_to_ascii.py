"""
BinaryStlToAscii

Purpose: Converts binary STL files to ASCII STL files
"""

import os
import re
import struct

def register_file(path: str, file_type: str, description: str) -> None:
    """No-op placeholder — replace with your own tracking/logging if needed."""
    pass

"""
def _is_ascii_stl(file_path: str) -> bool:
    Check if an STL file is already in ASCII format.
    try:
        with open(file_path, 'r', encoding='utf-8', errors='strict') as f:
            Read first bytes to check for 'solid' keyword
            header = f.read(256).strip().lower()
            if header.startswith('solid'):
                Further verify: ASCII STL should contain 'facet' or 'endsolid'
                f.seek(0)
                content = f.read(4096).lower()
                if 'facet' in content or 'endsolid' in content:
                    return True
        return False
    except (UnicodeDecodeError, ValueError):
        return False
"""

def _is_ascii_stl_upload(file) -> bool:
    """Return True if the uploaded FileStorage looks like ASCII STL."""
    stream = file.stream
    try:
        original_pos = stream.tell()
    except (AttributeError, OSError):
        original_pos = 0

    try:
        stream.seek(0)
        header = stream.read(256).decode("utf-8", errors="strict").strip().lower()
        if not header.startswith("solid"):
            return False

        stream.seek(0)
        content = stream.read(4096).decode("utf-8", errors="strict").lower()
        return "facet" in content or "endsolid" in content
    except (UnicodeDecodeError, ValueError):
        return False
    finally:
        try:
            stream.seek(original_pos)
        except Exception:
            stream.seek(0)

def _read_binary_stl(file_path: str):
    """
    Read a binary STL file and return the solid name and list of triangles.

    Binary STL format:
    - 80 bytes: header
    - 4 bytes: uint32 number of triangles
    - For each triangle (50 bytes):
        - 12 bytes: normal vector (3 x float32)
        - 12 bytes: vertex 1 (3 x float32)
        - 12 bytes: vertex 2 (3 x float32)
        - 12 bytes: vertex 3 (3 x float32)
        - 2 bytes: attribute byte count (uint16)
    """
    triangles = []

    with open(file_path, 'rb') as f:
        # Read 80-byte header
        header_bytes = f.read(80)
        if len(header_bytes) < 80:
            raise ValueError("File too small to be a valid binary STL (header incomplete)")

        # Try to extract a name from the header
        try:
            header_str = header_bytes.decode('ascii', errors='replace').strip().rstrip('\x00').strip()
        except Exception:
            header_str = ""

        # Extract solid name from header if it starts with 'solid'
        solid_name = "converted"
        if header_str.lower().startswith('solid'):
            potential_name = header_str[5:].strip()
            if potential_name:
                # Clean the name - remove non-printable characters
                solid_name = ''.join(c for c in potential_name if c.isprintable()).strip()
                if not solid_name:
                    solid_name = "converted"

        # Read number of triangles (4 bytes, unsigned int, little-endian)
        num_triangles_data = f.read(4)
        if len(num_triangles_data) < 4:
            raise ValueError("File too small to be a valid binary STL (triangle count missing)")

        num_triangles = struct.unpack('<I', num_triangles_data)[0]

        # Validate file size
        expected_size = 80 + 4 + (num_triangles * 50)
        f.seek(0, 2)  # Seek to end
        actual_size = f.tell()
        f.seek(84)  # Seek back to triangle data

        if actual_size < expected_size:
            raise ValueError(
                f"File size mismatch: expected at least {expected_size} bytes for "
                f"{num_triangles} triangles, but file is only {actual_size} bytes"
            )

        if num_triangles == 0:
            raise ValueError("Binary STL contains 0 triangles")

        # Sanity check on triangle count (prevent memory issues)
        if num_triangles > 50_000_000:
            raise ValueError(
                f"STL file claims {num_triangles} triangles, which seems unreasonably large. "
                f"The file may be corrupt."
            )

        # Read each triangle
        for i in range(num_triangles):
            triangle_data = f.read(50)
            if len(triangle_data) < 50:
                raise ValueError(
                    f"Unexpected end of file at triangle {i + 1} of {num_triangles}"
                )

            # Unpack: normal (3 floats) + 3 vertices (9 floats) + attribute (1 unsigned short)
            values = struct.unpack('<12fH', triangle_data)

            normal = (values[0], values[1], values[2])
            vertex1 = (values[3], values[4], values[5])
            vertex2 = (values[6], values[7], values[8])
            vertex3 = (values[9], values[10], values[11])
            # attribute_byte_count = values[12]  # Usually 0, ignored for ASCII

            triangles.append({
                'normal': normal,
                'vertices': [vertex1, vertex2, vertex3]
            })

    return solid_name, triangles


def _write_ascii_stl(file_path: str, solid_name: str, triangles: list):
    """Write triangles to an ASCII STL file."""
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(f"solid {solid_name}\n")

        for tri in triangles:
            nx, ny, nz = tri['normal']
            f.write(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}\n")
            f.write("    outer loop\n")

            for vertex in tri['vertices']:
                vx, vy, vz = vertex
                f.write(f"      vertex {vx:.6e} {vy:.6e} {vz:.6e}\n")

            f.write("    endloop\n")
            f.write("  endfacet\n")

        f.write(f"endsolid {solid_name}\n")


def execute(file = None):
    """
    Execute the binary-to-ASCII STL conversion.

    Args:
        file: Resolved input file path (provided by the runtime)

    Returns:
        Status message with output file path
    """
    # Resolve input file path
    if file:
        stl_path = file
    
    if not stl_path:
        return "Error: Could not find input STL file. Please provide a valid .stl file path."

    if not os.path.exists(stl_path):
        return f"Error: File not found: {stl_path}"

    # Check file size
    file_size = os.path.getsize(stl_path)
    if file_size < 84:
        return "Error: File is too small to be a valid STL file (minimum 84 bytes for binary STL)"

    input_dir = os.path.dirname(os.path.abspath(stl_path))

    # Check if already ASCII
    if _is_ascii_stl(stl_path):
        # If it's already ASCII, just copy it through
        base = os.path.splitext(os.path.basename(stl_path))[0]
        out_name = sanitize_filename(f"{base}_ascii", ".stl")
        out_path = get_file_path(out_name, input_dir)

        with open(stl_path, 'r', encoding='utf-8') as f:
            ascii_content = f.read()

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(ascii_content)

        register_file(out_path, 'stl', 'ASCII STL file (input was already ASCII)')
        return f"Note: Input file was already in ASCII STL format. Copied to {out_path}"

    # Read binary STL
    try:
        solid_name, triangles = _read_binary_stl(stl_path)
    except ValueError as e:
        return f"Error reading binary STL: {e}"
    except struct.error as e:
        return f"Error parsing binary STL data: {e}"
    except Exception as e:
        return f"Error reading STL file: {e}"

    # Generate output path
    base = os.path.splitext(os.path.basename(stl_path))[0]
    out_name = sanitize_filename(f"{base}_ascii", ".stl")
    out_path = get_file_path(out_name, input_dir)

    # Write ASCII STL
    try:
        _write_ascii_stl(out_path, solid_name, triangles)
    except Exception as e:
        return f"Error writing ASCII STL: {e}"

    register_file(out_path, 'stl', 'Converted ASCII STL file')

    return (
        f"Successfully converted binary STL to ASCII STL.\n"
        f"  Input: {stl_path}\n"
        f"  Output: {out_path}\n"
        f"  Solid name: {solid_name}\n"
        f"  Triangles: {len(triangles):,}"
    )
