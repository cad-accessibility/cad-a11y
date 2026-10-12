"""Roll, pitch and yaw: six keys that turn the model in 90 degree steps.

The axes are fixed to the display, not to the model. X runs across the screen, Y
up it, Z out of it toward the reader, and they stay put while the model turns
under them. That is the point of the feature: it lets someone reorient a model
without having to know or track which model axis currently points where.

Three separate things had to be true for this to work, and none of them were:

* The renderer had to receive the orientation. The browser computed a basis and
  the server validated it, and then it was dropped: the render picked its
  projection by view name, so anything off the six named views could not be
  drawn.
* A roll had to redraw. It leaves the same face toward the reader, so the view
  name does not change, and the redraw was conditional on the name changing.
* The turns had to go the way the reader expects from every view. They were
  written for a reader on the -depth side, which only three of the six views
  had, so from the default view pitch and yaw ran backwards (#185). Every view
  now has depth pointing at the reader (see test_standard_views.py).

The rotation tests read the turn formulas out of viewer.js's own switch
statement, so they test the shipped code rather than a copy of it that could
agree with itself and nothing else.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
import trimesh

from src.converter.single_view_stl import _resolve_orientation_basis, get_single_view

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"

# The viewer's name for each view, and the renderer's. x- is the view from +X,
# OpenSCAD's Right; the token is historical, the key is not.
TOKEN_TO_VIEW = {
    "z+": "top", "y-": "front", "x-": "right",
    "x+": "left", "y+": "back", "z-": "bottom",
}


# --- What the browser actually ships ---------------------------------------


def _js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _view_basis() -> dict[str, dict[str, np.ndarray]]:
    block = re.search(r"const VIEW_BASIS = \{(.*?)\n\};", _js(), re.S)
    assert block, "VIEW_BASIS not found in viewer.js"
    found = re.findall(
        r"'([^']+)':\s*\{\s*right:\s*(\[[^\]]*\]),\s*up:\s*(\[[^\]]*\]),\s*depth:\s*(\[[^\]]*\])",
        block.group(1),
    )
    return {
        token: {
            "right": np.array(json.loads(right)),
            "up": np.array(json.loads(up)),
            "depth": np.array(json.loads(depth)),
        }
        for token, right, up, depth in found
    }


def _rotations() -> dict[str, dict]:
    block = re.search(r"const RELATIVE_ROTATIONS = \{(.*?)\n\};", _js(), re.S)
    assert block, "RELATIVE_ROTATIONS not found in viewer.js"
    return {
        name: {"speech": speech}
        for name, speech in re.findall(
            r"(\w+):\s*\{\s*speech:\s*'([^']+)'",
            block.group(1),
        )
    }


def _js_turns() -> dict[str, dict[str, tuple[int, str]]]:
    """The turn formulas as viewer.js writes them: for each rotation, which basis
    vector each assigned one is taken from, and with which sign.

    Parsed rather than copied, so a test cannot pass by agreeing with a Python
    transcription that has drifted from the switch the browser runs."""
    body = re.search(r"function applyRelativeRotation\(.*?\n\}", _js(), re.S)
    assert body, "applyRelativeRotation not found in viewer.js"
    turns = {}
    for name, case in re.findall(r"case '(\w+)':(.*?)break;", body.group(0), re.S):
        assigned = {}
        for target, negated, source in re.findall(
            r"viewerState\.orientation(Right|Up|Depth)\s*=\s*(negateVec3\()?(right|up|depth)\)?;",
            case,
        ):
            assigned[target.lower()] = (-1 if negated else 1, source)
        assert len(assigned) == 2, f"{name}: expected two assignments, parsed {assigned}"
        turns[name] = assigned
    return turns


def _press(basis, rotation_name):
    """One key press, computed by the formulas parsed out of viewer.js. Each
    turn replaces two of the current (right, up, depth) vectors, one of them
    negated, and leaves the third alone."""
    after = dict(basis)
    for target, (sign, source) in _js_turns()[rotation_name].items():
        after[target] = sign * np.asarray(basis[source])
    return after


def _front():
    return {name: np.array(value) for name, value in _view_basis()["y-"].items()}


def _facing_the_reader(basis):
    """The model direction pointing out of the screen, at the reader: depth, in
    every view. The face a reader meets is the one on +depth."""
    return basis["depth"]


# --- The six keys behave as the issue specifies ----------------------------


def test_every_key_is_wired_with_the_wording_the_issue_asks_for():
    """The speech is the only confirmation a reader gets that the key landed."""
    assert {name: r["speech"] for name, r in _rotations().items()} == {
        "rollCounterclockwise": "roll counterclockwise",
        "rollClockwise": "roll clockwise",
        "pitchUp": "pitch up",
        "pitchDown": "pitch down",
        "yawLeft": "yaw left",
        "yawRight": "yaw right",
    }


def test_pitching_up_lifts_the_near_face_to_the_top():
    """Nose up: the face the reader was meeting rotates to become the top, and
    the underside comes round to meet them."""
    after = _press(_front(), "pitchUp")

    assert np.array_equal(after["up"], [0, -1, 0]), "the near face should now be up"
    assert np.array_equal(_facing_the_reader(after), [0, 0, -1]), (
        "the underside should now face the reader"
    )


def test_pitching_down_is_the_other_way():
    after = _press(_front(), "pitchDown")
    assert np.array_equal(_facing_the_reader(after), [0, 0, 1]), (
        "the top of the model should now face the reader"
    )


def test_yawing_left_turns_the_right_side_toward_the_reader():
    """Clockwise seen from above, which brings what was on the right round."""
    front = _front()
    after = _press(front, "yawLeft")
    assert np.array_equal(_facing_the_reader(after), front["right"])


def test_yawing_right_turns_the_left_side_toward_the_reader():
    front = _front()
    after = _press(front, "yawRight")
    assert np.array_equal(_facing_the_reader(after), -front["right"])


def test_rolling_keeps_the_same_face_toward_the_reader():
    """Roll spins the picture in its own plane. Nothing new comes into view,
    which is exactly why the redraw cannot be conditional on the view name."""
    front = _front()
    for name in ("rollCounterclockwise", "rollClockwise"):
        after = _press(front, name)
        assert np.array_equal(after["depth"], front["depth"]), f"{name} changed the face"
        assert not np.array_equal(after["up"], front["up"]), f"{name} changed nothing"


def test_rolling_counterclockwise_brings_the_right_side_up():
    front = _front()
    after = _press(front, "rollCounterclockwise")
    assert np.array_equal(after["up"], front["right"])


def test_pitch_and_yaw_are_reversible():
    """Every key has an opposite, so a wrong turn costs one press to undo."""
    for forward, back in (("pitchUp", "pitchDown"), ("yawLeft", "yawRight"),
                          ("rollCounterclockwise", "rollClockwise")):
        there = _press(_front(), forward)
        assert all(np.array_equal(_press(there, back)[k], _front()[k]) for k in there), (
            f"{back} did not undo {forward}"
        )


AIRPLANE = {
    # What each key does to a model airplane flying out of the display at the
    # reader, nose toward them. The expected basis after the press, written in
    # terms of the basis before it: the nose is depth, the airplane's top is up,
    # its right wing is right.
    "pitchUp": {"up": ("depth", 1), "depth": ("up", -1)},        # nose up, belly faces you
    "pitchDown": {"up": ("depth", -1), "depth": ("up", 1)},      # nose down, back faces you
    "yawLeft": {"right": ("depth", -1), "depth": ("right", 1)},  # nose to your left, right wing faces you
    "yawRight": {"right": ("depth", 1), "depth": ("right", -1)}, # nose to your right, left wing faces you
    "rollClockwise": {"right": ("up", 1), "up": ("right", -1)},  # the top swings to your right
    "rollCounterclockwise": {"right": ("up", -1), "up": ("right", 1)},
}


@pytest.mark.parametrize("wire_token", sorted(TOKEN_TO_VIEW))
@pytest.mark.parametrize("rotation_name", sorted(AIRPLANE))
def test_every_turn_goes_the_way_the_reader_expects_from_every_view(rotation_name, wire_token):
    """#185: the turns are centred on the reader. PR #189's tests checked that
    two directions differed, which a fully reversed implementation also passes;
    this checks which way each one goes, from each of the six views."""
    before = _view_basis()[wire_token]
    after = _press(before, rotation_name)
    for target, (source, sign) in AIRPLANE[rotation_name].items():
        assert np.array_equal(after[target], sign * before[source]), (
            f"{rotation_name} from {TOKEN_TO_VIEW[wire_token]}: {target} should be "
            f"{'-' if sign < 0 else ''}{source}"
        )


@pytest.mark.parametrize("wire_token", sorted(TOKEN_TO_VIEW))
@pytest.mark.parametrize("rotation_name", sorted(AIRPLANE))
def test_a_turn_keeps_the_basis_right_handed(rotation_name, wire_token):
    """depth = right x up is what puts the reader on the +depth side, and the
    cut, the turns and the depth readout all rely on it. A turn that broke it
    would silently mirror everything after it."""
    after = _press(_view_basis()[wire_token], rotation_name)
    assert np.array_equal(np.cross(after["right"], after["up"]), after["depth"])


@pytest.mark.parametrize("name", [
    "rollCounterclockwise", "rollClockwise", "pitchUp", "pitchDown", "yawLeft", "yawRight",
])
def test_four_presses_come_back_to_where_you_started(name):
    """Ninety degrees at a time, so a reader can always get home by pressing on
    rather than having to remember what they did."""
    basis = start = _front()
    for _ in range(4):
        basis = _press(basis, name)
    assert all(np.array_equal(basis[k], start[k]) for k in basis)


def test_the_basis_stays_a_basis_however_far_you_turn():
    """Axis-aligned, unit, and mutually perpendicular after any run of presses.
    Drift here would show up as a slowly skewing model."""
    basis = _front()
    for name in ["pitchUp", "yawLeft", "rollClockwise", "pitchUp", "yawRight",
                 "rollCounterclockwise", "pitchDown", "yawLeft"]:
        basis = _press(basis, name)
        vectors = [basis[k] for k in ("right", "up", "depth")]
        for vector in vectors:
            assert sorted(np.abs(vector)) == [0, 0, 1], f"{vector} is not an axis"
        for i in range(3):
            for j in range(i + 1, 3):
                assert np.dot(vectors[i], vectors[j]) == 0, "axes stopped being perpendicular"


def test_the_viewer_names_every_view_the_renderer_knows():
    assert set(_view_basis()) == set(TOKEN_TO_VIEW)


# --- The basis reaches the renderer, and means the same thing there ---------


def _shape():
    """Deliberately asymmetric in all three axes, so a wrong axis or a mirrored
    one shows up as a different picture rather than an identical one."""
    body = trimesh.creation.box(extents=(3.0, 2.0, 1.0))
    nub = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    nub.apply_translation([1.25, 0.75, 0.5])
    return trimesh.util.concatenate([body, nub])


def _chiral_shape():
    """Like _shape, but with a nub poking past every face, not just one --
    _shape's single nub sits inside the body's footprint when viewed from
    directly above or below (it only pokes out along Z, which isn't part of
    the top/bottom picture), so it can't tell a correct roll from a backwards
    one at those two views. Centered on the origin so rotating the basis is a
    pure rotation of the picture, not also a shift of it."""
    body = trimesh.creation.box(extents=(3.0, 2.0, 1.0))
    nub_x = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    nub_x.apply_translation([1.75, 0.75, 0.25])
    nub_y = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    nub_y.apply_translation([0.75, 1.25, -0.25])
    nub_z = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    nub_z.apply_translation([-0.75, -0.25, 0.75])
    shape = trimesh.util.concatenate([body, nub_x, nub_y, nub_z])
    shape.apply_translation(-shape.bounds.mean(axis=0))
    return shape


def _render(view_key, basis=None, mode="filled", depth=0.9):
    shape = _shape()
    image, _ = get_single_view(shape, shape.bounds.flatten(), cut_depth=depth,
                               view_key=view_key, rendering_mode=mode,
                               screen_size=[96, 40], orientation_basis=basis)
    return image


def _basis_payload(token):
    basis = _view_basis()[token]
    return {
        "scheme": "basis-v1",
        "forward": basis["depth"].tolist(),
        "up": basis["up"].tolist(),
        "right": basis["right"].tolist(),
    }


@pytest.mark.parametrize("token,view_key", sorted(TOKEN_TO_VIEW.items()))
def test_sending_a_named_views_basis_draws_that_named_view(token, view_key):
    """The safety property. The browser now always sends a basis, so if the two
    disagreed every existing view would shift or mirror the day this landed."""
    assert np.array_equal(_render(view_key), _render(view_key, _basis_payload(token))), (
        f"{token} drawn from its basis does not match the {view_key} view"
    )


def test_a_rolled_basis_actually_changes_the_picture():
    """The bug underneath the feature: the basis was accepted, validated, and
    then never passed to the renderer, so rolling redrew the same image."""
    rolled = _press(_front(), "rollCounterclockwise")
    payload = {
        "scheme": "basis-v1",
        "forward": rolled["depth"].tolist(),
        "up": rolled["up"].tolist(),
        "right": rolled["right"].tolist(),
    }
    assert not np.array_equal(_render("front"), _render("front", payload)), (
        "a roll produced an identical render"
    )


def test_rolling_counterclockwise_turns_the_picture_counterclockwise():
    """The strongest statement available: on a square frame with fixed limits, a
    roll is exactly a quarter turn of the rendered image, nothing added and
    nothing lost. It also pins the direction, since a counterclockwise turn is
    the one that carries the top right corner to the top left. Because the
    swap in _press/applyRelativeRotation is defined directly on the current
    basis rather than as a turn about a world-frame axis, this holds from
    every one of the six named views, not just front (see
    test_rolling_is_clockwise_or_counterclockwise_from_every_named_view)."""
    rolled = _press(_front(), "rollCounterclockwise")
    payload = {
        "scheme": "basis-v1",
        "forward": rolled["depth"].tolist(),
        "up": rolled["up"].tolist(),
        "right": rolled["right"].tolist(),
    }
    shape = _shape()
    square = dict(cut_depth=0.9, view_key="front", rendering_mode="filled",
                  imposed_ax_limits=[[-2, 2], [-2, 2]], screen_size=[64, 64])

    upright, _ = get_single_view(shape, shape.bounds.flatten(), **square)
    turned, _ = get_single_view(shape, shape.bounds.flatten(),
                                orientation_basis=payload, **square)

    assert np.array_equal(turned[..., 0] < 128, np.rot90(upright[..., 0] < 128, 1))


@pytest.mark.parametrize("wire_token,view_key", sorted(TOKEN_TO_VIEW.items()))
def test_rolling_clockwise_is_clockwise_from_every_named_view(wire_token, view_key):
    """The regression test for the actual bug: the six named views are not
    consistently handed (see the comment on RELATIVE_ROTATIONS), so a formula
    that rotates by a fixed turns count about a world-frame axis looks
    clockwise from some views and counterclockwise from others. It had only
    ever been checked from front, which is why it shipped looking right and
    wasn't. This checks all six directly against the renderer, not just the
    vector algebra, so a regression here would have to fool both."""
    base = _view_basis()[wire_token]
    rolled = _press(base, "rollClockwise")
    payload = {
        "scheme": "basis-v1",
        "forward": rolled["depth"].tolist(),
        "up": rolled["up"].tolist(),
        "right": rolled["right"].tolist(),
    }
    shape = _chiral_shape()
    square = dict(cut_depth=1.0, view_key=view_key, rendering_mode="filled",
                  imposed_ax_limits=[[-3, 3], [-3, 3]], screen_size=[64, 64])

    upright, _ = get_single_view(shape, shape.bounds.flatten(), **square)
    turned, _ = get_single_view(shape, shape.bounds.flatten(),
                                orientation_basis=payload, **square)

    assert np.array_equal(turned[..., 0] < 128, np.rot90(upright[..., 0] < 128, -1)), (
        f"rollClockwise was not clockwise from {view_key}"
    )


def test_the_cut_follows_the_orientation():
    """Cut slices into the screen. Under a rotated basis it has to follow, or it
    would keep slicing along whichever model axis the view name happened to
    name and cut from the side instead of the front."""
    rotated = _press(_front(), "pitchUp")
    payload = {
        "scheme": "basis-v1",
        "forward": rotated["depth"].tolist(),
        "up": rotated["up"].tolist(),
        "right": rotated["right"].tolist(),
    }
    shallow = _render("front", payload, mode="cut", depth=0.25)
    deep = _render("front", payload, mode="cut", depth=0.75)
    assert not np.array_equal(shallow, deep), "cut depth had no effect once rotated"


# Where the airplane's nose (the model direction pointing at the reader before
# the press) and its top (the direction pointing up before the press) must be
# afterwards, as edges of the display. Written from the description of the keys,
# not from the formulas.
NOSE_AND_TOP_AFTER = {
    "pitchUp": {"nose": "top"},
    "pitchDown": {"nose": "bottom"},
    "yawLeft": {"nose": "left"},
    "yawRight": {"nose": "right"},
    "rollClockwise": {"top": "right"},
    "rollCounterclockwise": {"top": "left"},
}
_SQUARE = [[-2.5, 2.5], [-2.5, 2.5]]
_EDGE_PROBES = {"right": (1.45, 0.0), "left": (-1.45, 0.0), "top": (0.0, 1.45), "bottom": (0.0, -1.45)}


def _edge_of(image):
    raised = image[..., 0] < 128
    found = set()
    for edge, (x, y) in _EDGE_PROBES.items():
        col = int((x + 2.5) / 5.0 * raised.shape[1])
        row = int((2.5 - y) / 5.0 * raised.shape[0])
        if raised[row, col]:
            found.add(edge)
    return found


@pytest.mark.parametrize("wire_token,view_key", sorted(TOKEN_TO_VIEW.items()))
@pytest.mark.parametrize("rotation_name", sorted(NOSE_AND_TOP_AFTER))
def test_after_each_turn_the_nose_is_drawn_where_the_key_sent_it(rotation_name, wire_token, view_key):
    """A known turn against a known-correct picture, from every view, through
    the real renderer: the regression #185 asked for. From the default view
    (x+) pitch up used to swing the nose down and yaw left swing it right."""
    before = _view_basis()[wire_token]
    (part, expected_edge), = NOSE_AND_TOP_AFTER[rotation_name].items()
    marked = before["depth"] if part == "nose" else before["up"]

    body = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
    marker = trimesh.creation.box(extents=(0.6, 0.6, 0.6))
    marker.apply_translation(1.3 * np.asarray(marked, dtype=float))
    shape = trimesh.util.concatenate([body, marker])

    after = _press(before, rotation_name)
    payload = {
        "scheme": "basis-v1",
        "forward": after["depth"].tolist(),
        "up": after["up"].tolist(),
        "right": after["right"].tolist(),
    }
    image, _ = get_single_view(shape, shape.bounds.flatten(), cut_depth=1.0, view_key=view_key,
                               rendering_mode="filled", imposed_ax_limits=_SQUARE,
                               screen_size=[50, 50], orientation_basis=payload)
    assert _edge_of(image) == {expected_edge}, (
        f"{rotation_name} from the {view_key} view: the {part} should be at the "
        f"{expected_edge} edge, found {_edge_of(image) or 'nowhere'}"
    )


# --- The basis is taken as given -------------------------------------------


def test_a_complete_basis_is_used_exactly_as_given():
    """Even a left-handed one, as this is. The viewer only sends right-handed
    bases now, but correcting one would mean guessing which of the three axes
    the sender got wrong, so a complete, perpendicular basis is drawn as sent."""
    right, up, depth = [1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]
    resolved = _resolve_orientation_basis({"right": right, "up": up, "forward": depth})

    assert np.allclose(resolved[0], right), "right was not taken as given"
    assert np.allclose(resolved[1], up)
    assert np.allclose(resolved[2], depth)


def test_a_partial_basis_still_resolves():
    """A mirrored picture beats no picture if a client sends less than three."""
    resolved = _resolve_orientation_basis({"up": [0, 0, 1], "forward": [0, 1, 0]})
    assert resolved is not None
    assert np.allclose(resolved[2], [0, 1, 0])


def test_a_skewed_basis_is_not_used_as_given():
    """right and up here are the same vector -- not perpendicular, so using
    them as given would silently skew the projection rather than just
    mirroring it. Should fall back to deriving a valid perpendicular pair
    instead of being accepted."""
    depth = [0.0, 1.0, 0.0]
    resolved = _resolve_orientation_basis(
        {"right": [1.0, 0.0, 0.0], "up": [1.0, 0.0, 0.0], "forward": depth}
    )
    assert resolved is not None
    right, up, resolved_depth = resolved
    assert abs(np.dot(right, up)) < 1e-6, "right and up were not made perpendicular"
    assert abs(np.dot(right, resolved_depth)) < 1e-6
    assert abs(np.dot(up, resolved_depth)) < 1e-6
    assert np.allclose(resolved_depth, depth), "depth should still be used as given"


def test_a_nearly_orthogonal_basis_still_gets_corrected():
    """Not just exact duplicates -- a basis a few degrees off perpendicular
    should also be rejected as given, not accepted because it is close."""
    resolved = _resolve_orientation_basis(
        {"right": [1.0, 0.0, 0.05], "up": [0.0, 0.0, 1.0], "forward": [0.0, 1.0, 0.0]}
    )
    assert resolved is not None
    right, up, depth = resolved
    assert abs(np.dot(right, up)) < 1e-6
    assert abs(np.dot(right, depth)) < 1e-6


def test_nonsense_is_refused_rather_than_guessed_at():
    for payload in (None, {}, "front", {"up": [0, 0, 1]}, {"forward": [0, 0, 0]},
                    {"forward": [1, 2]}, {"forward": [float("nan"), 0, 0]}):
        assert _resolve_orientation_basis(payload) is None, payload


# --- The page offers turns, not a list of axes -----------------------------


VIEWER_HTML = ROOT / "accessible-3d-viewer.html"


def _html() -> str:
    return VIEWER_HTML.read_text(encoding="utf-8")


def test_the_axis_pickers_are_gone():
    """Naming orientations was the approach this replaces. It could not express
    roll at all, and it asked a reader to know how the model sits relative to
    axes they cannot see."""
    html = _html()
    assert 'name="view-select"' not in html, "the axis radio group is still on the page"
    assert "view-select-heading" not in html

    js = _js()
    assert "view-select" not in js, "the viewer still wires up the axis radios"


def test_the_number_keys_no_longer_name_views():
    """7 through = were one key per named view, with no key left for roll. 0 is
    back, as Reset (it was Z until XYZ mode needed Z), and nothing else: it must
    not select a view."""
    js = _js()
    for shortcut in ("'7'", "'8'", "'9'", "'-'", "'='"):
        assert f"case {shortcut}:" not in js, f"{shortcut} still selects a view"
    assert "Digit7" not in js and "Numpad7" not in js, "a removed shortcut alias is still there"
    zero = re.search(r"case '0':\n(.*?)\n\s*break;", js, re.S)
    assert zero, "0 should be the reset key"
    assert "resetOrientationZoomAndDepth()" in zero.group(1)
    assert "updateView(" not in zero.group(1) and "setOrientationFromView(" not in zero.group(1)

def test_the_page_offers_all_six_turns():
    html = _html()
    for button_id in ("pitch-up-btn", "pitch-down-btn", "yaw-left-btn",
                      "yaw-right-btn", "roll-ccw-btn", "roll-cw-btn"):
        assert f'id="{button_id}"' in html, f"{button_id} missing from the page"


def test_every_button_is_wired_to_a_rotation():
    """A button that announces but does not turn is worse than no button."""
    block = re.search(r"const ORIENTATION_BUTTONS = \{(.*?)\n\};", _js(), re.S)
    assert block, "the buttons are not wired up"
    wired = dict(re.findall(r"'([\w-]+)':\s*'(\w+)'", block.group(1)))

    assert set(wired.values()) == set(_rotations()), "buttons and rotations disagree"
    assert set(wired) == {"pitch-up-btn", "pitch-down-btn", "yaw-left-btn",
                          "yaw-right-btn", "roll-ccw-btn", "roll-cw-btn"}


def test_the_simplified_workshop_ui_still_shows_the_turns():
    """That view hides every section except a named few, so a renamed section
    silently disappears from it."""
    css = (ROOT / "static" / "css" / "viewer.css").read_text(encoding="utf-8")
    assert 'body.simple-ui section[aria-labelledby="orientation-heading"]' in css


def test_the_keyboard_help_matches_the_keys():
    html = _html()
    for key, action in (("U", "Roll counterclockwise"), ("O", "Roll clockwise"),
                        ("I", "Pitch up"), ("K", "Pitch down"),
                        ("J", "Yaw left"), ("L", "Yaw right")):
        assert f"<kbd>{key}</kbd> {action}" in html, f"help for {key} is wrong or missing"
    assert "View x-" not in html and "View z+" not in html, "help still lists removed keys"
