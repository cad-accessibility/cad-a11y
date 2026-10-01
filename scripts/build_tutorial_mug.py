#!/usr/bin/env python3
"""Build the tutorial's practice mug and the landmarks its lessons check against.

    /opt/homebrew/Caskroom/miniconda/base/envs/cad-a11y/bin/python scripts/build_tutorial_mug.py

Reads builtin_models/mug.stl, which the study uses and which this leaves as it
is, and writes two files:

* builtin_models/tutorial_mug.stl: the same mug in millimetres, 95 mm tall,
  standing on Z = 0 with its body's axis on X = Y = 0 and its handle toward -Y.
  Binary STL.
* app/tutorial_mug.landmarks.json: what a cut through that mug shows at every
  position along each axis, as named bands of the viewer's cut_percent with a
  short spoken description each, plus how thick its parts come out on the pins.

Idempotent: a second run writes both files byte for byte the same. Run it again
only when mug.stl changes; tests/test_tutorial_mug.py checks that the two files
still belong together.

The lessons rely on five band names (x.handle_loop, y.handle_arms, z.floor,
z.above_floor, z.handle_beside_ring). The bands come from the geometry, so if a
different mug ever produced a different sequence of cross-sections, this stops
with an error rather than writing names that no longer describe what is cut.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import shapely
import trimesh

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.tutorial import (
    handle_mask,
    load_mesh_as_renderer_does,
    region_polygons,
    section_region,
)

SOURCE = REPO_ROOT / "builtin_models" / "mug.stl"
TARGET = REPO_ROOT / "builtin_models" / "tutorial_mug.stl"
LANDMARKS = REPO_ROOT / "app" / "tutorial_mug.landmarks.json"

HEIGHT_MM = 95.0

# Cross-sections are taken at the middle of every half percent along each axis,
# so each one stands for the half percent around it and the bands they make
# meet exactly and cover 0 to 100.
STEP_PERCENT = 0.5
# A run of cross-sections shorter than this is where one shape turns into the
# next (a plane grazing a curved wall), too thin to find with the depth keys and
# not worth a name. It joins the longer of its neighbours.
MIN_BAND_PERCENT = 1.0
# Pieces smaller than this are specks where the plane grazes a surface.
MIN_PIECE_MM2 = 0.5
# How finely a piece is sampled to split it into handle and body, and how much
# of either it needs before it counts as having some.
GRID_MM = 0.5
MIN_PART_MM2 = 2.0
# A body piece with less of its outline filled in than this is open, a U, rather
# than a solid slab of wall.
U_SOLIDITY = 0.85

# Every display this ships for is 40 pins tall and wider than that, and at zoom
# 0 the renderer fits the model's longest dimension to the smaller side of the
# drawable area (render(): scale_vertical_dist = longest_3d_dim * height /
# min(width, height)). In the single layout that is the 40 rows, whether the
# grid is a Monarch's 96x40, a DotPad's 60x40 or the 78x40 used with no display.
ZOOM0_ROWS = 40

AXES = "xyz"

DESCRIPTIONS = {
    "x.side_wall_low": "A solid piece of the side wall.",
    "x.walls_and_base_low": "Two walls and the base.",
    "x.handle_loop": "Two walls and the base, with the handle loop and its finger hole beside them.",
    "x.walls_and_base_high": "Two walls and the base.",
    "x.side_wall_high": "A solid piece of the side wall.",
    "y.handle_back": "One small bump: the back of the handle.",
    "y.handle_arms": "Two small bumps: the two arms of the handle.",
    "y.handle_meets_wall": "The side wall, with the lower arm of the handle below it.",
    "y.handle_joins_wall": "A solid piece of the side wall where the handle joins it.",
    "y.side_wall_handle_side": "A solid piece of the side wall, on the handle side.",
    "y.walls_and_base": "Two walls and the base.",
    "y.side_wall_far": "A solid piece of the side wall, away from the handle.",
    "z.foot": "A thin ring: the foot the mug stands on.",
    "z.floor": "A solid disc: the floor of the mug.",
    "z.above_floor": "A ring: the wall just above the floor.",
    "z.handle_bottom_join": "A ring with the bottom of the handle joined to it.",
    "z.handle_beside_ring": "A ring with the handle beside it.",
    "z.handle_top_join": "A ring with the top of the handle joined to it, and the handle beside it.",
    "z.handle_top": "A ring with the top of the handle beside it.",
    "z.rim": "A ring: the top of the wall, up to the rim.",
}

REQUIRED = ("x.handle_loop", "y.handle_arms", "z.floor", "z.above_floor", "z.handle_beside_ring")


# ---------------------------------------------------------------------------
# The mug
# ---------------------------------------------------------------------------

def body_axis(mesh: trimesh.Trimesh) -> tuple[float, float]:
    """Where the body's axis crosses the XY plane, in the mesh's own units.

    Taken from horizontal cuts, not from the bounding box: the handle makes the
    box lopsided in Y. The body is round, so its width in X is its diameter,
    and its centre in Y is that radius in from the side away from the handle.
    """
    low, high = mesh.bounds
    centres = []
    for fraction in np.linspace(0.1, 0.9, 9):
        pieces = region_polygons(section_region(mesh, 2, low[2] + fraction * (high[2] - low[2])))
        if not pieces:
            continue
        x0, _y0, x1, y1 = pieces[0].bounds
        radius = (x1 - x0) / 2.0
        centres.append(((x0 + x1) / 2.0, y1 - radius))
    if not centres:
        raise SystemExit("mug.stl has no horizontal cross-sections to find its body in")
    return tuple(float(v) for v in np.mean(centres, axis=0))


def build_mug() -> bytes:
    """Scale, centre and seat the study's mug, and return it as binary STL."""
    # process=False keeps the file's own faces in the file's own order, so the
    # output depends only on mug.stl and not on how trimesh merges vertices.
    mesh = trimesh.load(SOURCE, force="mesh", process=False)
    low, high = mesh.bounds
    scale = HEIGHT_MM / float(high[2] - low[2])
    centre_x, centre_y = body_axis(mesh)

    # The centre is rounded to a millionth of the mug's height, far below what
    # a float32 STL keeps, so float noise in it (1e-14 for this mug) cannot
    # change the file from one run to the next. The base is subtracted exactly,
    # so it lands on Z = 0 exactly.
    unit = float(high[2] - low[2]) * 1e-6
    origin = np.array([round(centre_x / unit) * unit, round(centre_y / unit) * unit, low[2]])
    moved = (np.asarray(mesh.vertices, dtype=float) - origin) * scale
    out = trimesh.Trimesh(vertices=moved, faces=np.asarray(mesh.faces), process=False)

    out_low, out_high = out.bounds
    radius = (out_high[0] - out_low[0]) / 2.0
    # The lessons say the handle points toward -Y. Checked rather than assumed,
    # so a mug.stl turned some other way stops the build instead of producing a
    # tutorial that describes the wrong side.
    if not (out_low[1] < -1.3 * radius and abs(out_high[1] - radius) < 0.02 * radius):
        raise SystemExit(f"the handle does not point toward -Y: bounds {out.bounds.tolist()}")
    if abs(out_low[2]) > 1e-6:
        raise SystemExit(f"the base is not on Z = 0: {out_low[2]}")
    return out.export(file_type="stl")


# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------

def body_profile(mesh: trimesh.Trimesh) -> dict[str, list[float]]:
    """The body's outer radius at each percent of its height.

    Measured only on the half away from the handle (Y >= 0), where the outline
    is the wall and nothing else, and the body is round, so that radius holds
    all the way round.
    """
    low, high = mesh.bounds
    z_values, radii = [], []
    for percent in range(101):
        z = low[2] + (high[2] - low[2]) * min(max(percent, 0.25), 99.75) / 100.0
        best = 0.0
        for piece in region_polygons(section_region(mesh, 2, z)):
            points = np.asarray(piece.exterior.coords)
            points = points[points[:, 1] >= 0.0]
            if len(points):
                best = max(best, float(np.hypot(points[:, 0], points[:, 1]).max()))
        z_values.append(round(float(low[2] + (high[2] - low[2]) * percent / 100.0), 3))
        radii.append(round(best, 3))
    return {"z_mm": z_values, "r_mm": radii}


def split_piece(piece, axis: int, value: float, profile) -> tuple[float, float]:
    """(handle area, body area) of one piece of a section, in mm²."""
    x0, y0, x1, y1 = piece.bounds
    xs = np.arange(x0 + GRID_MM / 2.0, x1, GRID_MM)
    ys = np.arange(y0 + GRID_MM / 2.0, y1, GRID_MM)
    if len(xs) and len(ys):
        grid_x, grid_y = np.meshgrid(xs, ys)
        inside = shapely.contains_xy(piece, grid_x, grid_y)
        flat = np.column_stack([grid_x[inside], grid_y[inside]])
    else:
        flat = np.zeros((0, 2))
    if not len(flat):
        # Too thin for the grid: let one point inside it speak for all of it.
        point = piece.representative_point()
        flat = np.array([[point.x, point.y]])
        cell = piece.area
    else:
        cell = GRID_MM * GRID_MM
    points = np.insert(flat, axis, value, axis=1)
    handle = handle_mask(points, profile)
    return float(handle.sum() * cell), float((~handle).sum() * cell)


def classify(mesh: trimesh.Trimesh, axis: int, value: float, profile) -> str:
    """A short signature for what one cross-section shows.

    The words: "ring" or "disc" for the body cut across (Z), "slab" or "u" for
    the body cut lengthwise (a solid piece of wall, or two walls and the
    base), "loop" when the handle closes round a finger hole, "joined" when a
    piece is part body and part handle, "blob" when a piece is handle alone,
    and "handle_one"/"handle_two" when the cut is beyond the body.
    """
    pieces = [p for p in region_polygons(section_region(mesh, axis, value)) if p.area >= MIN_PIECE_MM2]
    if not pieces:
        return "empty"

    body_pieces, blobs, joined = [], 0, False
    for piece in pieces:
        handle_area, body_area = split_piece(piece, axis, value, profile)
        if body_area < MIN_PART_MM2:
            blobs += 1
            continue
        body_pieces.append(piece)
        if handle_area >= MIN_PART_MM2:
            joined = True

    if not body_pieces:
        return {1: "handle_one", 2: "handle_two"}.get(blobs, "handle_many")

    body = body_pieces[0]
    if axis == 2:
        axis_point = shapely.Point(0.0, 0.0)
        kind = "ring" if any(shapely.Polygon(ring).contains(axis_point) for ring in body.interiors) else "disc"
    else:
        # A hole that lies in the handle's region is the finger hole.
        for ring in body.interiors:
            point = shapely.Polygon(ring).representative_point()
            if handle_mask(np.insert(np.array([[point.x, point.y]]), axis, value, axis=1), profile)[0]:
                return "loop" + ("_blob" if blobs else "")
        kind = "u" if body.area / body.convex_hull.area < U_SOLIDITY else "slab"
    return kind + ("_joined" if joined else "") + ("_blob" if blobs else "")


def runs_along(mesh: trimesh.Trimesh, axis: int, profile) -> list[list]:
    """[signature, from %, to %] for each stretch of one shape along an axis."""
    low, high = mesh.bounds
    steps = round(100.0 / STEP_PERCENT)
    runs: list[list] = []
    for i in range(steps):
        middle = (i + 0.5) * STEP_PERCENT
        signature = classify(mesh, axis, low[axis] + (high[axis] - low[axis]) * middle / 100.0, profile)
        start, end = i * STEP_PERCENT, (i + 1) * STEP_PERCENT
        if runs and runs[-1][0] == signature:
            runs[-1][2] = end
        else:
            runs.append([signature, start, end])

    # Fold the short in-between stretches into a neighbour, shortest first, and
    # join neighbours that end up the same.
    while True:
        short = [i for i, run in enumerate(runs) if run[2] - run[1] < MIN_BAND_PERCENT - 1e-9]
        if not short or len(runs) == 1:
            break
        i = min(short, key=lambda k: runs[k][2] - runs[k][1])
        left = runs[i - 1] if i > 0 else None
        right = runs[i + 1] if i + 1 < len(runs) else None
        into = left if right is None or (left is not None and left[2] - left[1] >= right[2] - right[1]) else right
        into[1], into[2] = min(into[1], runs[i][1]), max(into[2], runs[i][2])
        del runs[i]
        merged = [runs[0]]
        for run in runs[1:]:
            if run[0] == merged[-1][0]:
                merged[-1][2] = run[2]
            else:
                merged.append(run)
        runs = merged
    return runs


def name_runs(axis: str, runs: list[list]) -> list[list]:
    """[name, from %, to %] for each band: a name for each stretch from its
    shape and what came before it, with neighbours that get the same name (two
    ways of showing the same part) joined into one band."""
    names: list[str] = []
    seen: set[str] = set()
    bands: list[list] = []
    for signature, start, end in runs:
        if axis == "x":
            name = {
                "slab": "side_wall_high" if names else "side_wall_low",
                "u": "walls_and_base_high" if "handle_loop" in seen else "walls_and_base_low",
                "loop": "handle_loop",
            }.get(signature)
        elif axis == "y":
            name = {
                "handle_one": "handle_back",
                "handle_two": "handle_arms",
                "slab_blob": "handle_meets_wall",
                "slab_joined_blob": "handle_meets_wall",
                "slab_joined": "handle_joins_wall",
                "slab": "side_wall_far" if "walls_and_base" in seen else "side_wall_handle_side",
                "u": "walls_and_base",
            }.get(signature)
        else:
            handle_seen = seen & {"handle_bottom_join", "handle_beside_ring", "handle_top_join"}
            name = {
                "ring": "rim" if handle_seen else ("above_floor" if "floor" in seen else "foot"),
                "disc": "floor",
                "ring_joined": "handle_bottom_join",
                "ring_blob": "handle_top" if "handle_top_join" in seen else "handle_beside_ring",
                "ring_joined_blob": "handle_top_join",
            }.get(signature)
        if bands and name is not None and bands[-1][0] == name:
            bands[-1][2] = end
            continue
        if name is None or name in seen:
            raise SystemExit(
                f"unexpected cross-section along {axis.upper()} at {start}-{end}%: {signature} "
                f"after {names}. The mug is not the shape these names describe."
            )
        seen.add(name)
        names.append(name)
        bands.append([name, start, end])
    return bands


def segment_lengths(region, line) -> list[tuple[float, shapely.LineString]]:
    cut = region.intersection(line)
    parts = [cut] if isinstance(cut, shapely.LineString) else list(getattr(cut, "geoms", []))
    return [(part.length, part) for part in parts if isinstance(part, shapely.LineString) and part.length > 0]


def zoom0_pins(mesh: trimesh.Trimesh) -> tuple[dict[str, int], dict[str, float]]:
    """How many pins thick the wall, the floor and the handle's bar come out at
    zoom 0, measured in the cut the tutorial opens on (X at 50%)."""
    low, high = mesh.bounds
    region = section_region(mesh, 0, (low[0] + high[0]) / 2.0)  # (y, z) plane
    span = 1000.0
    height = float(high[2] - low[2])

    # The wall on the side away from the handle, halfway up.
    across = shapely.LineString([(-span, height / 2.0), (span, height / 2.0)])
    wall = max(segment_lengths(region, across), key=lambda s: s[1].bounds[2])[0]

    # The floor under the middle of the mug: the lowest solid stretch there.
    down = shapely.LineString([(0.0, -span), (0.0, span)])
    floor = min(segment_lengths(region, down), key=lambda s: s[1].bounds[1])[0]

    # The handle's bar at the height where the handle reaches furthest out.
    outline = np.asarray(region_polygons(region)[0].exterior.coords)
    bar_z = float(outline[np.argmin(outline[:, 0]), 1])
    across_bar = shapely.LineString([(-span, bar_z), (span, bar_z)])
    bar = min(segment_lengths(region, across_bar), key=lambda s: s[1].bounds[0])[0]

    longest = float(max(high - low))
    per_mm = ZOOM0_ROWS / longest
    thickness = {"wall": round(wall, 2), "floor": round(floor, 2), "handle_bar": round(bar, 2)}
    pins = {name: round(mm * per_mm) for name, mm in thickness.items()}
    return pins, thickness


def build_landmarks(stl_path: Path) -> dict:
    """The landmarks of the mug in this STL, measured on it as the viewer
    loads it."""
    mesh = load_mesh_as_renderer_does(stl_path)
    low, high = mesh.bounds
    profile = body_profile(mesh)

    axes = {}
    for index, axis in enumerate(AXES):
        bands = name_runs(axis, runs_along(mesh, index, profile))
        extent = float(high[index] - low[index])
        axes[axis] = [
            {
                "name": f"{axis}.{name}",
                "from": round(start, 2),
                "to": round(end, 2),
                "from_mm": round(float(low[index]) + extent * start / 100.0, 2),
                "to_mm": round(float(low[index]) + extent * end / 100.0, 2),
                "description": DESCRIPTIONS[f"{axis}.{name}"],
            }
            for name, start, end in bands
        ]

    present = {band["name"] for bands in axes.values() for band in bands}
    missing = [name for name in REQUIRED if name not in present]
    if missing:
        raise SystemExit(f"the lessons need bands this mug does not have: {missing}")

    pins, thickness = zoom0_pins(mesh)
    return {
        "model": "tutorial_mug",
        "stl_sha256": hashlib.sha256(stl_path.read_bytes()).hexdigest(),
        "units": "mm",
        "bbox_mm": {axis: [round(float(low[i]), 3), round(float(high[i]), 3)] for i, axis in enumerate(AXES)},
        "handle_direction": [0, -1, 0],
        # Estimated from the renderer's zoom-0 scale (see ZOOM0_ROWS): the
        # longest side of the mug spans the 40 rows, so each pin is that length
        # divided by 40. What the pins actually raise can differ by one either
        # way, depending on where an edge falls between two pins.
        "pins_at_zoom0": pins,
        "thickness_mm": thickness,
        # The body's outer radius against height. The server uses it to tell
        # handle from body when it locates them on the pins (app.tutorial).
        "body_outer_radius_mm": profile,
        "axes": axes,
    }


def main() -> int:
    stl_bytes = build_mug()
    if not TARGET.exists() or TARGET.read_bytes() != stl_bytes:
        TARGET.write_bytes(stl_bytes)
    landmarks = build_landmarks(TARGET)
    text = json.dumps(landmarks, indent=2) + "\n"
    if not LANDMARKS.exists() or LANDMARKS.read_text(encoding="utf-8") != text:
        LANDMARKS.write_text(text, encoding="utf-8")
    print(f"{TARGET.relative_to(REPO_ROOT)}: {landmarks['stl_sha256']}")
    for bands in landmarks["axes"].values():
        for band in bands:
            print(f"  {band['name']:<26} {band['from']:6.2f} - {band['to']:6.2f}%  {band['description']}")
    print(f"pins at zoom 0: {landmarks['pins_at_zoom0']} (mm: {landmarks['thickness_mm']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
