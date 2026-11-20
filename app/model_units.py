"""The length unit a model's coordinates are in, when something says so (#234).

Geometry alone cannot tell: a part 40 across could be 40 mm or 40 inches, and the
built-ins ship the same LEGO brick twice, ten times apart. So a unit is known only
from one of these, and is otherwise None:

* A unit the person chose at upload or sent to /ingest, kept in the STL's own
  header (``declare_stl_units``), so it survives restarts and needs no database.
* A STEP file: it declares its unit, and the loader (trimesh through cascadio)
  converts every STEP file to metres.
* OpenSCAD's stamp on an STL. OpenSCAD has no units, but its STLs are millimetres
  by convention and every slicer reads them that way.
* A built-in whose size matches a real object, recognised by its contents, since
  a seeded copy on a server is never refreshed.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_LANDMARKS = Path(__file__).resolve().with_name("tutorial_mug.landmarks.json")

UNITS = ("mm", "in", "m")
MM_PER_UNIT = {"mm": 1.0, "in": 25.4, "m": 1000.0}

_DECLARED = re.compile(rb"cad-a11y units=(mm|in|m)\b")
_OPENSCAD_ASCII = b"solid OpenSCAD_Model"
_OPENSCAD_BINARY = b"OpenSCAD Model"
_STL_HEADER = 80


@functools.cache
def _builtin_units_by_sha256() -> dict[str, str]:
    units = {
        # lego_3002 and lego_3002e: 0.0238 x 0.0159 x 0.0114, a real 2x3 brick in metres.
        "e46161fc9cb885397449779b651075453718f0aaad0696e50c71ac92d7c9a151": "m",
        "9eb1493f6ba8ef9723d6cff3e63814302aa40cbb1bb97eb2de248eb97801aa1b": "m",
    }
    # Read where app/tutorial.py reads it, and only when first needed, so a
    # missing file leaves the mug's unit unknown instead of stopping the server.
    try:
        landmarks = json.loads(_LANDMARKS.read_text(encoding="utf-8"))
        units[landmarks["stl_sha256"]] = landmarks["units"]
    except (OSError, ValueError, KeyError):
        pass
    return units


def parse_units(value: object) -> str | None:
    """A unit sent by a person or a tool, or None for anything else, "unknown" included."""
    text = str(value or "").strip().lower()
    return text if text in UNITS else None


def _is_binary_stl(data: bytes) -> bool:
    if len(data) < _STL_HEADER + 4:
        return False
    (count,) = struct.unpack_from("<I", data, _STL_HEADER)
    return len(data) == _STL_HEADER + 4 + 50 * count


def declare_stl_units(path: Path, unit: str) -> None:
    """Write the unit into the STL's header: the binary format's 80-byte header,
    or the name on an ASCII file's first line. The geometry is untouched."""
    if unit not in UNITS:
        raise ValueError(f"unknown unit {unit!r}")
    data = path.read_bytes()
    stamp = f"cad-a11y units={unit}".encode()
    if _is_binary_stl(data):
        data = stamp.ljust(_STL_HEADER, b"\0") + data[_STL_HEADER:]
    elif data.startswith(b"solid"):
        end = data.find(b"\n")
        if end < 0:
            return  # No line to replace without taking the body with it.
        data = b"solid " + stamp + data[end:]
    else:
        return  # Neither kind of STL: leave it, and its unit stays unknown.
    path.write_bytes(data)


def units_for(path: Path) -> str | None:
    """The unit the model at this path is in, or None when nothing says."""
    suffix = path.suffix.lower()
    if suffix in (".step", ".stp"):
        return "m"
    if suffix != ".stl":
        return None
    with path.open("rb") as handle:
        header = handle.read(_STL_HEADER)
    declared = _DECLARED.search(header)
    if declared:
        return declared.group(1).decode()
    if header.startswith(_OPENSCAD_ASCII) or header.startswith(_OPENSCAD_BINARY):
        return "mm"
    return _builtin_units_by_sha256().get(hashlib.sha256(path.read_bytes()).hexdigest())


def slice_coordinate_fields(params: dict, bounds: list[float] | None) -> dict:
    """A study row's cut_value and cut_units: where the slice was, in millimetres
    from the origin. Worked out from the view and depth the server rendered and
    the model's extent, the same way the renderer places the cut, so it cannot
    disagree with the picture whatever the viewer reports. Blank when the
    model's unit is not known, or the view does not face along an axis."""
    from src.converter.single_view_stl import _get_view_basis

    blank = {"cut_value": None, "cut_units": None}
    try:
        depth = float(params.get("depth"))
    except (TypeError, ValueError):
        return blank
    # Without an orientation _get_view_basis falls back to its default view, so
    # the cut would be logged along the wrong axis; the viewer always sends one.
    if bounds is None or not math.isfinite(depth) or not params.get("orientation"):
        return blank
    forward = _get_view_basis(params.get("view"), orientation_basis=params.get("orientation"))[2]
    axis = max(range(3), key=lambda i: abs(forward[i]))
    if not math.isclose(abs(forward[axis]), 1.0, abs_tol=1e-6):
        return blank
    low, high = bounds[axis], bounds[axis + 3]
    # The renderer cuts 1 - depth/100 of the way along "forward", which points at
    # the reader, so depth is measured in from the side the reader faces.
    along = 1.0 - depth / 100.0 if forward[axis] > 0 else depth / 100.0
    return {"cut_value": round(low + along * (high - low), 2), "cut_units": "mm"}


def bounds_mm(raw_bbox, unit: str | None) -> list[float] | None:
    """The object's extent, [xmin, ymin, zmin, xmax, ymax, zmax], in millimetres."""
    if unit is None or raw_bbox is None:
        return None
    return [float(value) * MM_PER_UNIT[unit] for value in raw_bbox]
