"""What XYZ mode puts on the pins (#185), and the label that replaced "x+".

* The view info box names the axis coming out of the display as a two-cell
  capital label, e.g. ⠠⠵ for Z. It used to draw a lowercase letter and a sign,
  and the pilot found those unreadable: a lone lowercase x is the word "it" in
  contracted UEB, and dots 346 are Nemeth's plus but UEB's "ing". No sign glyph
  is drawn anywhere now.
* The origin marker, a hollow 3x3 square, sits where the model's origin is.
* The edge letters label each display axis at the edge it increases toward.

These render through the real renderer and read the pins back.
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import app.cad_comparison_lib as cad_lib
from app.server import _build_preview_payload_cache_key, _build_quantized_render_key
from app.server import app as flask_app

GRID = (96, 40)


def _write_stl(path, mesh):
    path.write_text(trimesh.exchange.stl.export_stl_ascii(mesh))
    return path


@pytest.fixture(scope="module")
def plate(tmp_path_factory):
    """A 40 x 30 x 5 mm plate with its origin at a corner, as cube([40,30,5])."""
    mesh = trimesh.creation.box(extents=(40.0, 30.0, 5.0))
    mesh.apply_translation([20.0, 15.0, 2.5])
    path = _write_stl(tmp_path_factory.mktemp("marks") / "plate.stl", mesh)
    return cad_lib.CADComparisonRenderer(str(path), str(path))


@pytest.fixture(scope="module")
def far_plate(tmp_path_factory):
    """The same plate 100 mm away from its origin, so the origin is off the display."""
    mesh = trimesh.creation.box(extents=(40.0, 30.0, 5.0))
    mesh.apply_translation([120.0, 115.0, 2.5])
    path = _write_stl(tmp_path_factory.mktemp("marks") / "far.stl", mesh)
    return cad_lib.CADComparisonRenderer(str(path), str(path))


def _raised(renderer, **params):
    base = {"view": "z+", "depth": 50, "renderMode": "Filled", "mode": "single", "zoom": 0}
    base.update(params)
    image = renderer.render(base, screen_size=list(GRID)).image
    return image[..., 0] < 128


def _cell(dots):
    """A 2x4 braille cell as a boolean array, dots 1-3 down the left column and
    4-6 down the right, one pixel per dot (as _draw_braille_cell draws them)."""
    cell = np.zeros((4, 2), dtype=bool)
    positions = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (0, 1), 5: (1, 1), 6: (2, 1)}
    for dot in dots:
        cell[positions[dot]] = True
    return cell


LETTERS = {"x": [1, 3, 4, 6], "y": [1, 3, 4, 5, 6], "z": [1, 3, 5, 6]}


# --- The info box ----------------------------------------------------------------


@pytest.mark.parametrize("token", ["z+", "z-", "y-", "y+", "x-", "x+"])
def test_the_info_box_is_a_capital_axis_letter_and_no_sign(plate, token):
    raised = _raised(plate, view=token, show_view_info_box=True)
    box = raised[0:5, 0:7]
    expected = np.zeros((5, 7), dtype=bool)
    expected[1:5, 1:3] = _cell([6])                 # the capital sign
    expected[1:5, 4:6] = _cell(LETTERS[token[0]])   # the letter
    assert np.array_equal(box, expected), (
        f"{token}: expected the capital {token[0].upper()} label, got\n{box.astype(int)}"
    )


def test_both_sides_of_an_axis_get_the_same_label(plate):
    """The side is said in speech and braille; no pin says plus or minus."""
    top = _raised(plate, view="z+", show_view_info_box=True)[0:5, 0:7]
    bottom = _raised(plate, view="z-", show_view_info_box=True)[0:5, 0:7]
    assert np.array_equal(top, bottom)


def test_no_sign_glyph_is_left_to_draw():
    with open(cad_lib.__file__, encoding="utf-8") as fp:
        source = fp.read()
    assert '"+": [3, 4, 6]' not in source and '"-": [3, 6]' not in source
    assert "_draw_braille_text" not in source


# --- The origin marker ------------------------------------------------------------


def _hollow_square_at(raised, col, row):
    patch = raised[row - 1:row + 2, col - 1:col + 2]
    ring = np.ones((3, 3), dtype=bool)
    ring[1, 1] = False
    return patch.shape == (3, 3) and np.array_equal(patch, ring)


def test_the_origin_marker_sits_at_the_models_origin(plate):
    """cube([40,30,5]) has its origin at a corner. Seen from above that is the
    bottom left corner of the outline, found here from the plain render, not
    from the formula the marker uses."""
    plain = _raised(plate)
    rows, cols = np.nonzero(plain)
    corner_col, corner_row = cols.min(), rows.max()

    marked = _raised(plate, show_origin_marker=True)
    changed = np.argwhere(marked != plain)
    assert changed.size, "no marker was drawn"
    centre_row, centre_col = changed.mean(axis=0)
    assert abs(centre_col - corner_col) <= 2 and abs(centre_row - corner_row) <= 2, (
        f"marker centred at ({centre_col:.1f}, {centre_row:.1f}), "
        f"corner at ({corner_col}, {corner_row})"
    )
    right, up, _ = cad_lib._get_view_basis("top")
    where = plate.origin_on_display(right, up, _limits(plate, "z+"), GRID)
    assert where is not None and _hollow_square_at(marked, *where)


def _limits(renderer, token):
    """The window render() draws at zoom 0 and no pan, for a GRID-sized display."""
    view = renderer._map_view_name(token)
    limits = renderer._limits_for_orientation(None, renderer._get_view_index(view), view)
    centre = renderer._default_camera_center(limits)
    size = renderer.longest_3d_dim
    width, height = GRID
    return cad_lib.compute_imposed_zoom_limits(
        size * width / min(width, height), size * height / min(width, height),
        centre[0], centre[1], 0.0, width, height,
    )


def test_no_marker_when_the_origin_is_off_the_display(far_plate):
    """Nothing to feel there; "," says where it is instead."""
    assert np.array_equal(_raised(far_plate), _raised(far_plate, show_origin_marker=True))


def test_the_origin_is_reported_as_a_fraction_of_the_object(plate, far_plate):
    """What "," and "where am I" read: along each axis, 0 at the object's lowest
    coordinate and 1 at its highest. cube([40,30,5]) has its origin at the low
    corner; the far plate's origin lies beyond its low corner on X and Y."""
    assert plate.origin_fraction() == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    x, y, z = far_plate.origin_fraction()
    assert x == pytest.approx(-100.0 / 40.0) and y == pytest.approx(-100.0 / 30.0)
    assert z == pytest.approx(0.0, abs=1e-9)


def test_every_render_tells_the_viewer_where_the_origin_is():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        body = client.post("/render", json={
            "view": "z+", "renderMode": "Cut", "mode": "single", "depth": 50, "zoom": 0,
            "current_model": 0, "target_pixel_width": 96, "target_pixel_height": 40,
        }).get_json()
    assert len(body["origin_fraction"]) == 3
    assert all(value is None or isinstance(value, float) for value in body["origin_fraction"])


def test_no_marks_unless_asked(plate):
    assert np.array_equal(
        _raised(plate), _raised(plate, show_origin_marker=False, show_axis_letters=False)
    )


# --- The edge letters --------------------------------------------------------------


def _label_at(raised, x, y):
    """Which axis letter, if any, is drawn as a capital label with its top-left at (x, y)."""
    sign = raised[y:y + 4, x:x + 2]
    letter = raised[y:y + 4, x + 3:x + 5]
    if not np.array_equal(sign, _cell([6])):
        return None
    for name, dots in LETTERS.items():
        if np.array_equal(letter, _cell(dots)):
            return name
    return None


def _edges(raised):
    """The letter at the middle of each edge, where _overlay_axis_letters puts them."""
    width, height = GRID
    box_w, box_h = 7, 6
    return {
        "right": _label_at(raised, width - box_w + 1, (height - box_h) // 2 + 1),
        "left": _label_at(raised, 1, (height - box_h) // 2 + 1),
        "top": _label_at(raised, (width - box_w) // 2 + 1, 1),
        "bottom": _label_at(raised, (width - box_w) // 2 + 1, height - box_h + 1),
    }


@pytest.mark.parametrize("token,expected", [
    ("z+", {"right": "x", "top": "y"}),     # Top: X right, Y toward the top edge
    ("z-", {"right": "x", "bottom": "y"}),  # Bottom: Y now toward the bottom edge
    ("y-", {"right": "x", "top": "z"}),     # Front
    ("y+", {"left": "x", "top": "z"}),      # Back: X increases to the left
    ("x-", {"right": "y", "top": "z"}),     # Right
    ("x+", {"left": "y", "top": "z"}),      # Left: Y increases to the left
])
def test_each_axis_letter_sits_at_the_edge_it_increases_toward(plate, token, expected):
    found = {edge: letter for edge, letter in _edges(_raised(plate, view=token, show_axis_letters=True)).items()
             if letter}
    assert found == expected


# --- The render cache keys the marks --------------------------------------------------


@pytest.mark.parametrize("flag", ["show_origin_marker", "show_axis_letters"])
def test_a_mark_changes_the_render_key(flag):
    """Drawn onto the image, so a key without it serves a render with the mark
    to a window that turned it off, and the other way round."""
    base = {"view": "z+", "depth": 50, "renderMode": "Cut", "mode": "single", "zoom": 0}
    assert (_build_quantized_render_key({**base, flag: False}, "m")
            != _build_quantized_render_key({**base, flag: True}, "m"))
    assert (_build_preview_payload_cache_key({**base, flag: False}, "m", 60, 40)
            != _build_preview_payload_cache_key({**base, flag: True}, "m", 60, 40))
