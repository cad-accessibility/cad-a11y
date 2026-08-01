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
* The six named views are not consistently handed. Deriving one basis axis from
  the other two mirrors half of them, so all three are carried explicitly.

The rotation tests read the constants out of viewer.js so they describe the
shipped behaviour rather than a copy of it.
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

# The viewer's name for each view, and the renderer's.
TOKEN_TO_VIEW = {
    "z+": "top", "y-": "front", "x-": "left",
    "x+": "right", "y+": "back", "z-": "bottom",
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


def _press(basis, rotation_name):
    """Mirrors applyRelativeRotation's switch exactly: each turn replaces two
    of the current (right, up, depth) vectors with each other (negating one)
    and leaves the third untouched -- defined directly in terms of the
    CURRENT basis rather than as a right-hand-rule turn about a world-frame
    axis, so it is the same physical turn regardless of which of the six
    named views (or any orientation reached from them) it starts from. See
    the comment above RELATIVE_ROTATIONS in viewer.js for why that distinction
    matters: a world-frame formula looked consistent only because it was only
    ever checked from one view."""
    right, up, depth = basis["right"], basis["up"], basis["depth"]
    after = dict(basis)
    if rotation_name == "rollClockwise":
        after["right"], after["up"] = up, -right
    elif rotation_name == "rollCounterclockwise":
        after["right"], after["up"] = -up, right
    elif rotation_name == "pitchUp":
        after["up"], after["depth"] = -depth, up
    elif rotation_name == "pitchDown":
        after["up"], after["depth"] = depth, -up
    elif rotation_name == "yawLeft":
        after["right"], after["depth"] = depth, -right
    elif rotation_name == "yawRight":
        after["right"], after["depth"] = -depth, right
    else:
        raise ValueError(rotation_name)
    return after


def _front():
    return {name: np.array(value) for name, value in _view_basis()["y-"].items()}


def _facing_the_reader(basis):
    """The model direction pointing out of the screen. The camera looks along
    +depth, so the face a reader meets is the one on -depth."""
    return -basis["depth"]


# --- The six keys behave as the issue specifies ----------------------------


def test_every_key_is_wired_with_the_wording_the_issue_asks_for():
    """The speech is the only confirmation a reader gets that the key landed."""
    assert {name: r["speech"] for name, r in _rotations().items()} == {
        "rollCounterclockwise": "Roll counterclockwise",
        "rollClockwise": "Roll clockwise",
        "pitchUp": "Pitch up",
        "pitchDown": "Pitch down",
        "yawLeft": "Yaw left",
        "yawRight": "Yaw right",
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


def _world_frame_formula(basis, rotation_name):
    """The formula this feature shipped with and no longer uses: rotate by a
    fixed +-90 about a world-frame axis (right for pitch, up for yaw, depth
    for roll) using the right-hand rule. It agreed with the current swap-based
    _press only at one sign of det(right, up, depth) -- see
    test_the_fix_actually_changed_behaviour_where_it_needed_to for which sign,
    and why it differs between pitch/yaw and roll. front/back/bottom (where
    pitch and yaw were previously correct) was the only view anything had
    ever been checked from, which is why the bug shipped. Kept here only so
    the tests below can prove the fix actually changed behaviour where it
    needed to, not just that the new formula is internally consistent with
    itself."""
    axis_name, turns = {
        "pitchUp": ("right", 1), "pitchDown": ("right", -1),
        "yawLeft": ("up", 1), "yawRight": ("up", -1),
        "rollCounterclockwise": ("depth", -1), "rollClockwise": ("depth", 1),
    }[rotation_name]
    k = np.array(basis[axis_name], dtype=float)
    out = {}
    for name in ("right", "up", "depth"):
        v = np.array(basis[name], dtype=float)
        for _ in range(turns % 4):
            v = k * np.dot(k, v) + np.cross(k, v)
        out[name] = np.rint(v).astype(int)
    return out


@pytest.mark.parametrize("wire_token,view_key", sorted(TOKEN_TO_VIEW.items()))
@pytest.mark.parametrize("rotation_name", [
    "pitchUp", "pitchDown", "yawLeft", "yawRight", "rollCounterclockwise", "rollClockwise",
])
def test_the_fix_actually_changed_behaviour_where_it_needed_to(rotation_name, wire_token, view_key):
    """Proves the swap-based formula is not just self-consistent but actually
    different from the old, buggy one exactly where it needed to be. The two
    formulas agree only at one sign of det(right, up, depth) -- which sign
    depends on the rotation, since pitch/yaw rotate about the first/second
    basis vector and roll about the third: pitch and yaw agreed with the old
    formula at det=-1 (front/back/bottom, the only view ever checked, which is
    why the bug shipped), roll agreed at det=+1 (top/left/right). A test that
    only checked the new formula against itself could not tell a real fix
    from a no-op change."""
    base = _view_basis()[wire_token]
    d = np.dot(np.cross(base["right"], base["up"]), base["depth"])
    fixed = _press(base, rotation_name)
    old = _world_frame_formula(base, rotation_name)
    agrees = all(np.array_equal(fixed[k], old[k]) for k in base)
    agrees_at_negative_d = rotation_name in (
        "pitchUp", "pitchDown", "yawLeft", "yawRight",
    )
    expected = agrees_at_negative_d if d < 0 else not agrees_at_negative_d
    assert agrees == expected, (
        f"{rotation_name} at {view_key} (det={d:+.0f}): expected agreement={expected}, got {agrees}"
    )


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


# --- The basis is taken as given -------------------------------------------


def test_a_complete_basis_is_used_exactly_as_given():
    """Half the named views have right x up = -depth. Recomputing an axis from
    the other two therefore mirrors them, which is why nothing is recomputed."""
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
    """7 through = were one key per named view, with no key left for roll."""
    js = _js()
    for shortcut in ("'7'", "'8'", "'9'", "'0'", "'='"):
        assert f"case {shortcut}:" not in js, f"{shortcut} still selects a view"
    assert "Digit7" not in js, "the numpad alias for a removed shortcut is still there"


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
