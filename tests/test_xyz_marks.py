"""What XYZ mode puts on the pins (#185), and the label that replaced "x+".

* The view info box is the view label (#267): a braille letter and sign for the
  axis you look down, with an extra column and row for the display's two axes.
  Before that it drew a capital letter with no sign, and before that a lowercase
  letter and a sign, which the pilot found unreadable (a lone lowercase x is the
  word "it" in contracted UEB, and dots 346 are Nemeth's plus but UEB's "ing").
* The origin marker, a hollow 3x3 square, sits where the model's origin is.

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


def test_the_origin_s_place_on_the_display_is_where_the_marker_goes(plate, far_plate):
    """What "," and "." read (#235 review): across from the left edge and up from
    the bottom, as fractions of the display, so the pin the marker is drawn on
    and the numbers said agree. Off the display they run past 0 and 1: the far
    plate's origin lies beyond its low corner on X and Y, off the bottom left."""
    right, up, _ = cad_lib._get_view_basis("top")
    limits = _limits(plate, "z+")
    across, rise = plate.origin_display_fraction(right, up, limits)
    col, row = plate.origin_on_display(right, up, limits, GRID)
    assert col == int(np.floor(across * GRID[0]))
    assert row == int(np.floor((1.0 - rise) * GRID[1]))
    far_across, far_rise = far_plate.origin_display_fraction(right, up, _limits(far_plate, "z+"))
    assert far_across < 0 and far_rise < 0


def test_a_single_view_render_says_where_the_origin_lands_on_the_display():
    """In either mode, marked or not. Side by side has two frames, so it says
    nothing rather than pick one."""
    flask_app.config["TESTING"] = True
    request = {
        "view": "z+", "renderMode": "Cut", "depth": 50, "zoom": 0,
        "current_model": 0, "target_pixel_width": 96, "target_pixel_height": 40,
    }
    with flask_app.test_client() as client:
        single = client.post("/render", json={**request, "mode": "single"}).get_json()
        side_by_side = client.post("/render", json={**request, "mode": "side-by-side"}).get_json()
    assert len(single["origin_display"]) == 2
    assert all(isinstance(value, float) for value in single["origin_display"])
    assert "origin_display" not in side_by_side


def test_no_marks_unless_asked(plate):
    assert np.array_equal(
        _raised(plate), _raised(plate, show_origin_marker=False, show_axis_letters=False)
    )


# --- The view label ---------------------------------------------------------------

# 7 wide and 6 tall, 3 pins in from the top left corner (_VIEW_LABEL_OFFSET).
LABEL_X, LABEL_Y, LABEL_W, LABEL_H = 3, 3, 7, 6
PLUS, MINUS = [3, 4, 6], [3, 6]


def _footprint(raised):
    return raised[LABEL_Y:LABEL_Y + LABEL_H, LABEL_X:LABEL_X + LABEL_W]


def _read_label(raised, column_right, row_above):
    """Read the label back the way a reader would: the letter and sign cells
    inside the 6x5 box, and the pins in the extra column and row."""
    box = _footprint(raised)
    corner = box[0 if row_above else -1, -1 if column_right else 0]
    column = box[:, -1 if column_right else 0].copy()
    row = box[0 if row_above else -1, :].copy()
    column[0 if row_above else -1] = False  # the shared corner is read separately
    row[-1 if column_right else 0] = False
    # The cells: three rows tall, in the box's middle rows.
    cell_top = 2 if row_above else 1
    letter_left = 0 if column_right else 2
    letter = box[cell_top:cell_top + 3, letter_left:letter_left + 2]
    sign = box[cell_top:cell_top + 3, letter_left + 3:letter_left + 5]
    return corner, int(column.sum()), int(row.sum()), letter, sign


def _cell3(dots):
    return _cell(dots)[:3]


@pytest.mark.parametrize("token,look,sign,column_right,row_above,across,vertical", [
    ("z+", "z", PLUS, True, True, 1, 2),    # Top: from above, X right, Y up
    ("z-", "z", MINUS, True, False, 1, 2),  # Bottom: Y increases toward the bottom
    ("y-", "y", MINUS, True, True, 1, 3),   # Front: X right, Z up
    ("y+", "y", PLUS, False, True, 1, 3),   # Back: X increases to the left
    ("x-", "x", PLUS, True, True, 2, 3),    # Right view, from +X: Y right, Z up
    ("x+", "x", MINUS, False, True, 2, 3),  # Left view, from -X: Y increases left
])
def test_the_view_label_names_the_view_and_where_the_axes_increase(
        plate, token, look, sign, column_right, row_above, across, vertical):
    raised = _raised(plate, view=token, show_view_info_box=True)
    corner, column_pins, row_pins, letter, sign_cell = _read_label(raised, column_right, row_above)
    assert np.array_equal(letter, _cell3(LETTERS[look]))
    assert np.array_equal(sign_cell, _cell3(sign))
    assert (column_pins, row_pins) == (across, vertical)
    assert not corner


def test_the_view_label_leaves_a_blank_pin_between_its_extra_column_and_the_letter(plate):
    for token, column_right in (("z+", True), ("y+", False)):
        box = _footprint(_raised(plate, view=token, show_view_info_box=True))
        beside_column = box[1:, 5 if column_right else 1]
        assert not beside_column.any()


def test_the_view_label_wins_over_the_model_and_the_origin_marker(plate):
    """The plate's origin is at its corner, which lands near the top left or
    bottom left, and the label is drawn last."""
    with_label = _raised(plate, show_view_info_box=True)
    both = _raised(plate, show_view_info_box=True, show_origin_marker=True)
    assert np.array_equal(_footprint(with_label), _footprint(both))


def test_the_view_label_is_drawn_whatever_the_origin_marker_setting(plate):
    assert _footprint(_raised(plate, show_view_info_box=True)).any()
    assert not np.array_equal(_raised(plate), _raised(plate, show_view_info_box=True))


@pytest.mark.parametrize("token", ["z+", "z-", "y-", "y+", "x-", "x+"])
def test_with_the_edge_letters_the_label_is_only_the_letter_and_sign(plate, token):
    """The edge letters say which way the axes run, so the label leaves out its
    extra column and row, and the letter and sign sit where they do with them."""
    look = {"z+": "z", "z-": "z", "y-": "y", "y+": "y", "x-": "x", "x+": "x"}[token]
    sign = PLUS if token in ("z+", "y+", "x-") else MINUS
    raised = _raised(plate, view=token, show_view_info_box=True, show_axis_letters=True)
    box = raised[LABEL_Y:LABEL_Y + 5, LABEL_X:LABEL_X + 6]
    expected = np.zeros((5, 6), dtype=bool)
    expected[1:4, 0:2] = _cell3(LETTERS[look])
    expected[1:4, 3:5] = _cell3(sign)
    assert np.array_equal(box, expected)
    # Nothing else of the label: the pins beside and above the box are blank.
    assert not raised[LABEL_Y - 1:LABEL_Y + 6, LABEL_X + 6:LABEL_X + 7].any()
    assert not raised[LABEL_Y + 5:LABEL_Y + 6, LABEL_X:LABEL_X + 7].any()


@pytest.mark.parametrize("token", ["z+", "z-"])
def test_the_edge_letters_keep_clear_of_a_slice_graph(plate, token):
    """They are drawn over a slice graph's layout too, but only above its divider."""
    plain = _raised(plate, view=token, compose_slicegraph=True, compose_scrollbar=False)
    lettered = _raised(plate, view=token, compose_slicegraph=True, compose_scrollbar=False,
                       show_axis_letters=True)
    graph_rows = 11  # the graph's 10 rows and its divider
    assert not np.array_equal(plain[:-graph_rows], lettered[:-graph_rows])
    assert np.array_equal(plain[-graph_rows:], lettered[-graph_rows:])


def test_the_edge_letters_are_not_drawn_side_by_side(plate):
    assert np.array_equal(
        _raised(plate, mode="side-by-side"), _raised(plate, mode="side-by-side", show_axis_letters=True)
    )


def test_the_view_label_is_not_drawn_side_by_side(plate):
    assert np.array_equal(
        _raised(plate, mode="side-by-side"), _raised(plate, mode="side-by-side", show_view_info_box=True)
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


@pytest.mark.parametrize("flag", ["show_origin_marker", "show_axis_letters", "show_view_info_box"])
def test_a_mark_changes_the_render_key(flag):
    """Drawn onto the image, so a key without it serves a render with the mark
    to a window that turned it off, and the other way round."""
    base = {"view": "z+", "depth": 50, "renderMode": "Cut", "mode": "single", "zoom": 0}
    assert (_build_quantized_render_key({**base, flag: False}, "m")
            != _build_quantized_render_key({**base, flag: True}, "m"))
    assert (_build_preview_payload_cache_key({**base, flag: False}, "m", 60, 40)
            != _build_preview_payload_cache_key({**base, flag: True}, "m", 60, 40))
