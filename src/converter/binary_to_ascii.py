import struct
from io import StringIO

def _is_ascii_stl_upload(file) -> bool:
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

def _read_binary_stl(file):
    triangles = []

    stream = file.stream

    try:
        original_pos = stream.tell()
    except (AttributeError, OSError):

        original_pos = 0

    stream.seek(0)

    header_bytes = stream.read(80)

    if len(header_bytes) < 80:
        raise ValueError("File too small to be a valid binary STL (header incomplete)")

    try:
        header_str = header_bytes.decode('ascii', errors='replace').strip().rstrip('\x00').strip()
    except Exception:
        header_str = ""

    solid_name = "converted"

    if header_str.lower().startswith('solid'):
        potential_name = header_str[5:].strip()

        if potential_name:

            solid_name = ''.join(c for c in potential_name if c.isprintable()).strip()

            if not solid_name:
                solid_name = "converted"

    num_triangles_data = stream.read(4)

    if len(num_triangles_data) < 4:
        raise ValueError("File too small to be a valid binary STL (triangle count missing)")

    num_triangles = struct.unpack('<I', num_triangles_data)[0]

    expected_size = 80 + 4 + (num_triangles * 50)

    stream.seek(0, 2)  # Seek to end

    actual_size = stream.tell()

    stream.seek(84)  # Seek back to triangle data

    if actual_size < expected_size:
        raise ValueError(
            f"File size mismatch: expected at least {expected_size} bytes for "
            f"{num_triangles} triangles, but file is only {actual_size} bytes"
        )

    if num_triangles == 0:
        raise ValueError("Binary STL contains 0 triangles")

    if num_triangles > 50_000_000:
        raise ValueError(
            f"STL file claims {num_triangles} triangles, which seems unreasonably large. "
            f"The file may be corrupt."
         )

    for i in range(num_triangles):
        triangle_data = stream.read(50)

        if len(triangle_data) < 50:
            raise ValueError(
            f"Unexpected end of file at triangle {i + 1} of {num_triangles}"
            )

        values = struct.unpack('<12fH', triangle_data)

        normal = (values[0], values[1], values[2])

        vertex1 = (values[3], values[4], values[5])

        vertex2 = (values[6], values[7], values[8])

        vertex3 = (values[9], values[10], values[11])

        triangles.append({

            'normal': normal,

            'vertices': [vertex1, vertex2, vertex3]

        })

    return solid_name, triangles

def _write_ascii_stl(solid_name: str, triangles: list):
    f = StringIO()

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
    # change stringIO into string
    f_text = f.getvalue()
    return f_text

def execute(file):
    if not file:
         raise ValueError("Could not find input STL file. Please provide a valid .stl file.")

    if _is_ascii_stl_upload(file):
        return file

    try:
        solid_name, triangles = _read_binary_stl(file)
    except ValueError as e:
        raise ValueError(f"Error reading binary STL: {e}")
    except struct.error as e:
        raise ValueError(f"Error parsing binary STL data: {e}")
    except Exception as e:
        raise ValueError(f"Error reading STL file: {e}")

    try:
        new_file = _write_ascii_stl(solid_name, triangles)
    except Exception as e:
        raise ValueError(f"Error writing ASCII STL: {e}")

    return new_file
