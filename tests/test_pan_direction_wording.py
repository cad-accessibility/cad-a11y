"""Tests for the w/a/s/d "move object" direction reversal and wording (#153).

Before this fix, pressing e.g. 'w' set currentMoveCamera = "up" (panning the
CAMERA up) and immediately announced "up" -- but panning the camera up moves
the OBJECT down on screen, so the announcement said the opposite of what the
reader would feel happen. The commands were also named "pan"/"object pan",
language that describes moving the viewport, not the object.

The fix has three parts, all in viewer.js:

  * Each key now sends the OPPOSITE literal camera_move from the direction it
    means: 'w' ("move object up") sends camera_move="down", so the viewport
    shifts down and the object rises on screen.
  * The announcement is deferred to the /render response (pendingPanDirection
    -> activePanDirection), because whether the object stayed in frame is
    only known once the server has recomputed it (#153) -- announcing at
    keydown, before that response, was possible before but would be
    announcing a guess.
  * The wording is "move object <dir>" normally, or "pan <dir>, object out of
    frame" when the server reports the object went fully out of frame.

These parse the shipped viewer.js/server.py source directly (same convention
as test_orientation_controls.py), rather than re-implementing the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
SERVER_PY = ROOT / "app" / "server.py"


def _js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _case_block(key: str) -> str:
    match = re.search(rf"case '{re.escape(key)}':\n(.*?)\n\s*break;", _js(), re.S)
    assert match, f"case '{key}' not found in viewer.js"
    return match.group(1)


# key -> (literal camera_move sent to the server, "move object" direction announced)
PAN_KEYS = {
    "w": ("down", "up"),
    "s": ("up", "down"),
    "a": ("right", "left"),
    "d": ("left", "right"),
}


@pytest.mark.parametrize("key,camera_move,semantic_direction",
                          [(k, *v) for k, v in PAN_KEYS.items()])
def test_each_key_sends_the_reversed_camera_move_for_its_perceived_direction(
    key, camera_move, semantic_direction
):
    """Panning the camera <camera_move> is what makes the object appear to
    move <semantic_direction> on screen -- the two must always be opposite
    pairs (up/down, left/right), never the same word."""
    block = _case_block(key)
    assert f'viewerState.currentMoveCamera = "{camera_move}"' in block, (
        f"'{key}' should pan the camera {camera_move!r} to move the object "
        f"{semantic_direction!r}, but the literal camera_move wasn't found"
    )
    assert f"pendingPanDirection = '{semantic_direction}'" in block, (
        f"'{key}' should record {semantic_direction!r} as the perceived move-object direction"
    )


@pytest.mark.parametrize("key", list(PAN_KEYS))
def test_no_key_announces_immediately_at_keydown(key):
    """Whether the object stayed in frame is only known once the /render
    response comes back (#153) -- announcing right at the keypress would be
    announcing a guess, and was the old behavior this replaces."""
    block = _case_block(key)
    assert "announceAlert(" not in block, (
        f"'{key}' still announces synchronously instead of deferring to the /render response"
    )


def test_opposite_keys_send_opposite_camera_moves():
    """w/s and a/d are inverses of each other, so a wrong press costs exactly
    one press to undo."""
    assert PAN_KEYS["w"][0] != PAN_KEYS["s"][0]
    assert {PAN_KEYS["w"][0], PAN_KEYS["s"][0]} == {"up", "down"}
    assert {PAN_KEYS["a"][0], PAN_KEYS["d"][0]} == {"left", "right"}


def test_pending_pan_direction_is_captured_and_cleared_before_the_request_goes_out():
    """Captured synchronously into a local (activePanDirection) and cleared
    immediately, same reasoning as the model-load-announcement capture right
    above it: a second sendStateToServer call before this one's response
    arrives must not steal or duplicate this direction."""
    js = _js()
    assert "let pendingPanDirection = null;" in js
    assert "const activePanDirection = pendingPanDirection;" in js
    reset_count = len(re.findall(r"pendingPanDirection = null;", js))
    assert reset_count >= 2, (
        "expected at least the module-level declaration and the post-capture "
        "reset in sendStateToServer"
    )


def test_response_handler_announces_move_object_or_out_of_frame_guidance():
    js = _js()
    assert "if (activePanDirection) {" in js
    assert "announceAlert(`move object ${activePanDirection}`);" in js, (
        "normal pan should announce 'move object <dir>', not raw camera/pan wording"
    )
    assert "announceAlert(`pan ${data.pan_guidance_directions.join(' and ')}, object out of frame`);" in js, (
        "an out-of-frame pan should announce every recovery direction (joined with "
        "'and'), not just one -- an object out on both axes needs both named"
    )
    assert "data.object_out_of_frame && Array.isArray(data.pan_guidance_directions)" in js, (
        "the out-of-frame branch should be gated on both flags from the server, "
        "not just one"
    )


def test_out_of_frame_announcement_names_every_direction_not_just_one():
    """Regression test for the specific behavior this exists for: when the
    server reports two directions, the client must join and announce both,
    not silently pick the first."""
    js = _js()
    assert "data.pan_guidance_directions.join(' and ')" in js, (
        "out-of-frame guidance on both axes must announce both directions, "
        "not just data.pan_guidance_directions[0]"
    )
    assert "data.pan_guidance_directions[0]" not in js


def test_server_response_includes_the_out_of_frame_fields_the_client_reads():
    """The client reads data.object_out_of_frame / data.pan_guidance_directions
    straight off the /render JSON body -- pin that the server actually puts
    them there, as a list (not a single string) so an object out on both axes
    can report both directions."""
    server_py = SERVER_PY.read_text(encoding="utf-8")
    assert '"object_out_of_frame": render_result.object_out_of_frame' in server_py
    assert '"pan_guidance_directions": render_result.pan_guidance_directions' in server_py


def test_old_bare_direction_wording_is_gone_from_the_pan_keys():
    """The old behavior announced just the bare direction word ('up', 'left',
    etc.) with no "move object" framing; make sure that's actually gone from
    each pan key's block, not just supplemented."""
    for key, (_, semantic_direction) in PAN_KEYS.items():
        block = _case_block(key)
        assert f"announceAlert('{semantic_direction}')" not in block
