"""The six standard views: one convention, OpenSCAD's pictures, the reader's half cut.

Each view is a basis (right, up, depth): the model directions pointing to the
display's right, to its top edge, and out of it at the reader. Three things are
pinned here, all of which used to be false for half the views (#185):

* Every view is right-handed, depth = right x up, so the reader is always on the
  +depth side. Front, back and bottom used to have depth pointing away.
* Each view is the picture OpenSCAD shows under the same name. The table below
  is written from OpenSCAD's own view presets, not from this repository, so the
  two cannot agree by copying each other. Bottom used to be top turned 180
  degrees, and the renderer's "left" and "right" were each other's views.
* The cut removes the half on the reader's side, so 0% is the surface nearest
  the reader in every view. Front, back and bottom used to cut from the far side.

The pictures are checked by rendering, not by reading the table back: a marker
stuck on one face of a box has to come out at the edge OpenSCAD would draw it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
import trimesh

from app.cad_comparison_lib import CADComparisonRenderer
from src.converter.plane_intersection_utils import depth_peeling_single_depth_with_bbox
from src.converter.single_view_stl import VIEW_BASES, _get_view_basis, get_single_view

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"

# OpenSCAD's standard views (View menu, src/gui/MainWindow.cc:2817-2862 at
# openscad@0e6cc0b), as the model directions that end up to the right, up, and
# toward the viewer. Top: looking down at the print bed, X right, Y toward the
# top edge. Front: from -Y, X right, Z up. Right: from +X, Y right, Z up. Each
# opposite view is the same axis seen from the other side.
OPENSCAD = {
    "top":    {"right": [1, 0, 0],  "up": [0, 1, 0],  "toward_viewer": [0, 0, 1]},
    "bottom": {"right": [1, 0, 0],  "up": [0, -1, 0], "toward_viewer": [0, 0, -1]},
    "front":  {"right": [1, 0, 0],  "up": [0, 0, 1],  "toward_viewer": [0, -1, 0]},
    "back":   {"right": [-1, 0, 0], "up": [0, 0, 1],  "toward_viewer": [0, 1, 0]},
    "right":  {"right": [0, 1, 0],  "up": [0, 0, 1],  "toward_viewer": [1, 0, 0]},
    "left":   {"right": [0, -1, 0], "up": [0, 0, 1],  "toward_viewer": [-1, 0, 0]},
}

# The viewer's tokens. Kept for the study logs and defaults; the x pair reads
# backwards (x- is the view from +X).
TOKENS = {"z+": "top", "z-": "bottom", "y-": "front", "y+": "back", "x-": "right", "x+": "left"}


def _js_view_basis() -> dict[str, dict[str, np.ndarray]]:
    source = VIEWER_JS.read_text(encoding="utf-8")
    block = re.search(r"const VIEW_BASIS = \{(.*?)\n\};", source, re.S)
    assert block, "VIEW_BASIS not found in viewer.js"
    found = re.findall(
        r"'([^']+)':\s*\{\s*right:\s*(\[[^\]]*\]),\s*up:\s*(\[[^\]]*\]),\s*depth:\s*(\[[^\]]*\])",
        block.group(1),
    )
    return {
        token: {"right": np.array(json.loads(r)), "up": np.array(json.loads(u)),
                "depth": np.array(json.loads(d))}
        for token, r, u, d in found
    }


# --- The bases ---------------------------------------------------------------


@pytest.mark.parametrize("view", sorted(VIEW_BASES))
def test_every_renderer_view_is_right_handed(view):
    right, up, depth = VIEW_BASES[view]
    assert np.array_equal(np.cross(right, up), depth), f"{view}: right x up is not depth"


@pytest.mark.parametrize("token", sorted(TOKENS))
def test_every_viewer_view_is_right_handed(token):
    basis = _js_view_basis()[token]
    assert np.array_equal(np.cross(basis["right"], basis["up"]), basis["depth"]), (
        f"{token}: right x up is not depth"
    )


@pytest.mark.parametrize("view", sorted(OPENSCAD))
def test_the_renderer_views_are_openscads(view):
    right, up, depth = VIEW_BASES[view]
    expected = OPENSCAD[view]
    assert np.array_equal(right, expected["right"]), f"{view}: right"
    assert np.array_equal(up, expected["up"]), f"{view}: up"
    assert np.array_equal(depth, expected["toward_viewer"]), f"{view}: depth"


@pytest.mark.parametrize("token,view", sorted(TOKENS.items()))
def test_the_viewer_sends_the_basis_the_renderer_draws(token, view):
    """The viewer always sends a basis, so a mismatch here would shift or mirror
    every named view the moment it was drawn from the basis."""
    basis = _js_view_basis()[token]
    right, up, depth = VIEW_BASES[view]
    assert np.array_equal(basis["right"], right)
    assert np.array_equal(basis["up"], up)
    assert np.array_equal(basis["depth"], depth)


@pytest.mark.parametrize("token,view", sorted(TOKENS.items()))
def test_the_wire_tokens_reach_the_views_they_name(token, view):
    renderer = CADComparisonRenderer.__new__(CADComparisonRenderer)
    assert renderer._map_view_name(token) == view


def test_the_viewer_knows_all_six_and_no_others():
    assert set(_js_view_basis()) == set(TOKENS)
    assert set(VIEW_BASES) == set(OPENSCAD)


# --- The pictures ------------------------------------------------------------

LIMITS = [[-2.5, 2.5], [-2.5, 2.5]]
GRID = 50


def _box_with_markers(markers):
    """A 2x2x2 box centred on the origin, with a small cube stuck to it at each
    of the given (centre) positions."""
    parts = [trimesh.creation.box(extents=(2.0, 2.0, 2.0))]
    for centre in markers:
        marker = trimesh.creation.box(extents=(0.6, 0.6, 0.6))
        marker.apply_translation(np.asarray(centre, dtype=float))
        parts.append(marker)
    return trimesh.util.concatenate(parts)


def _render(shape, view, cut_depth=1.0, mode="filled"):
    image, _ = get_single_view(
        shape, shape.bounds.flatten(), cut_depth=cut_depth, view_key=view,
        rendering_mode=mode, imposed_ax_limits=LIMITS, screen_size=[GRID, GRID],
    )
    return image[..., 0] < 128  # raised


def _pixel(x, y):
    """The (row, col) where display coordinates (x, y) land in the render."""
    col = int((x - LIMITS[0][0]) / (LIMITS[0][1] - LIMITS[0][0]) * GRID)
    row = int((LIMITS[1][1] - y) / (LIMITS[1][1] - LIMITS[1][0]) * GRID)
    return row, col


PROBES = {"right": (1.45, 0.0), "left": (-1.45, 0.0), "top": (0.0, 1.45), "bottom": (0.0, -1.45)}


def _edges_with_something_past_the_body(raised):
    """Which of the four edges of the box's outline has a marker sticking past it."""
    return {edge for edge, (x, y) in PROBES.items() if raised[_pixel(x, y)]}


# Where a marker on each face of the model appears in each view, straight from
# OpenSCAD's table: a model direction equal to +right sticks out to the right,
# +up out of the top, and along the viewing axis it is hidden behind the box.
def _openscad_edge(view, direction):
    table = OPENSCAD[view]
    direction = np.asarray(direction)
    if np.array_equal(direction, table["right"]):
        return "right"
    if np.array_equal(direction, -np.asarray(table["right"])):
        return "left"
    if np.array_equal(direction, table["up"]):
        return "top"
    if np.array_equal(direction, -np.asarray(table["up"])):
        return "bottom"
    return None  # pointing at the viewer or away: inside the outline


FACES = {
    "+X": [1, 0, 0], "-X": [-1, 0, 0], "+Y": [0, 1, 0],
    "-Y": [0, -1, 0], "+Z": [0, 0, 1], "-Z": [0, 0, -1],
}


@pytest.mark.parametrize("view", sorted(OPENSCAD))
@pytest.mark.parametrize("face", sorted(FACES))
def test_a_marker_on_each_face_lands_where_openscad_draws_it(view, face):
    """The direction test the old suite lacked: not that two views differ, but
    that a feature on the model's +X face (say) is at the right-hand edge in the
    views where OpenSCAD puts it there, and at the left-hand edge where OpenSCAD
    puts it there."""
    direction = FACES[face]
    shape = _box_with_markers([1.3 * np.asarray(direction, dtype=float)])
    expected = _openscad_edge(view, direction)
    found = _edges_with_something_past_the_body(_render(shape, view))
    assert found == ({expected} if expected else set()), (
        f"{view} view: a marker on the {face} face should show at "
        f"{expected or 'no edge (it points along the view)'}, found {found or 'none'}"
    )


@pytest.mark.parametrize("view", sorted(OPENSCAD))
def test_the_cut_removes_the_half_nearest_the_reader(view):
    """Two markers beside the box, one on the reader's half and one on the far
    half, at opposite sides so they cannot hide each other. Cutting halfway must
    leave only the far one."""
    # Where the reader is comes from OpenSCAD's table, not from the renderer's
    # basis: taken from the basis, this would agree with any renderer, including
    # the old one that cut front, back and bottom from the far side.
    right = np.asarray(OPENSCAD[view]["right"], dtype=float)
    toward_reader = np.asarray(OPENSCAD[view]["toward_viewer"], dtype=float)
    near = 0.7 * toward_reader + 1.3 * right
    far = -0.7 * toward_reader - 1.3 * right
    shape = _box_with_markers([near, far])

    # render() passes 1 - depth/100, so a wire depth of 50% is t = 0.5.
    raised = _render(shape, view, cut_depth=0.5)
    found = _edges_with_something_past_the_body(raised)
    assert found == {"left"}, (
        f"{view}: after a halfway cut the far marker (left) should remain and the "
        f"near one (right) should be gone; found {found or 'neither'}"
    )


@pytest.mark.parametrize("view", sorted(OPENSCAD))
@pytest.mark.parametrize("wire_depth", [0, 10, 50, 90, 100])
def test_wire_depth_is_measured_in_from_the_readers_surface(view, wire_depth):
    """0% is the surface nearest the reader and 100% the far side, in every view,
    which is what the viewer's help has always told people and was only true
    for three of the six."""
    shape = trimesh.creation.box(extents=(4.0, 6.0, 8.0))
    shape.apply_translation([1.0, 2.0, 3.0])
    bbox = shape.bounds.flatten()

    # The plane the renderer would cut, found the way get_single_view finds it...
    normal = _get_view_basis(view)[2]
    _, origin = depth_peeling_single_depth_with_bbox(
        shape, normal, depth=1.0 - wire_depth / 100.0, bbox=bbox
    )
    # ...measured from the side OpenSCAD puts the viewer on.
    toward_reader = np.asarray(OPENSCAD[view]["toward_viewer"], dtype=float)
    along = float(np.dot(origin, toward_reader))
    ends = sorted(float(np.dot(corner, toward_reader)) for corner in shape.bounds)
    readers_surface, far_side = ends[1], ends[0]
    expected = readers_surface + (far_side - readers_surface) * wire_depth / 100.0
    assert along == pytest.approx(expected), (
        f"{view} at {wire_depth}%: the plane should be {wire_depth}% in from the reader's surface"
    )
