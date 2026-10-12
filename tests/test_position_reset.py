"""Tests for the reset shortcut / Reset Position button (#183).

The shortcut was Z until XYZ mode (#185) needed Z to mean "cut along Z"; a key
cannot mean reset in one axis mode and a cut in the other, so reset moved to 0
for everyone. Z in Turn mode now says where reset went.

"Position reset" was found to only actually reset pan: the Z key and the
Reset Position button set currentMoveCamera = "reset" and sent it to the
server, but never touched viewerState.currentZoom, the orientation basis
(orientationRight/Up/Depth), or the slice depth, so a rotated, zoomed-in, or
deeply-sliced view came back centred on itself rather than on the object.

The fix adds a shared resetOrientationZoomAndDepth() helper -- called by both
the 'z' keydown case and the Reset Position button -- that puts the
orientation back to the straight-on basis for whatever view is currently
selected (setOrientationFromView), zooms back out to 0 (updateZoom), and
brings the slice plane back to the middle of every axis (resetSlicePlanes), the
way applyStudyDefaults does for a study step's starting view, and a newly
opened model starts there too. That holds in both axis modes: XYZ mode reset to
the model's origin for a while, until depth went back to meaning the same in
both modes (#263).

These parse the shipped viewer.js source directly (same convention as
test_pan_direction_wording.py), rather than re-implementing the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
VIEWER_HTML = ROOT / "accessible-3d-viewer.html"


def _js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _case_block(key: str) -> str:
    match = re.search(rf"case '{re.escape(key)}':\n(.*?)\n\s*break;", _js(), re.S)
    assert match, f"case '{key}' not found in viewer.js"
    return match.group(1)


def _function_block(name: str) -> str:
    return _block_after(f"function {name}(")


def _block_after(marker: str) -> str:
    js = _js()
    start = js.index(marker)
    # Body runs from the first '{' after the marker to its matching '}'.
    brace_start = js.index("{", start)
    depth = 0
    for i in range(brace_start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[brace_start:i + 1]
    raise AssertionError(f"unbalanced braces after {marker}")


def test_reset_orientation_zoom_and_depth_helper_resets_all_three():
    """The helper this exists for: orientation back to the current view's own
    basis (not a fixed default -- turning the model first must stick), zoom
    back to 0, and the slice plane back to the middle of every axis."""
    block = _function_block("resetOrientationZoomAndDepth")
    assert "setOrientationFromView(viewerState.currentView)" in block, (
        "reset should undo roll/pitch/yaw back to the CURRENT view's own "
        "basis, not switch to a different named view"
    )
    assert re.search(r"updateZoom\(\s*0\s*,", block), (
        "reset should zoom back out to 0 (MIN_ZOOM), the same value the "
        "viewer starts at"
    )
    assert "resetSlicePlanes()" in block, (
        "reset should bring the slice plane back too, not just orientation "
        "and zoom"
    )
    # The planes must be reset before syncSliceDepthFromPlanes() re-derives
    # the displayed percentage from them, or the display would show the stale
    # pre-reset depth for one more render.
    assert block.index("resetSlicePlanes()") < block.index("syncSliceDepthFromPlanes()")


def test_every_plane_resets_to_the_middle_in_both_modes():
    """50% on all three axes, whichever the axis mode (#263). Nothing places
    the plane at the model's origin any more."""
    assert "{ x: 0.5, y: 0.5, z: 0.5 }" in _function_block("resetSlicePlanes")
    js = _js()
    for gone in ("resetSlicePlanesForMode", "placeNewModelAtOrigin", "fetchModelOrigin", "/render/origin"):
        assert gone not in js, gone


def test_reset_says_position_reset_in_both_modes():
    assert "announceAlert('Position reset')" in _case_block("0")
    button = re.search(
        r"resetPositionBtn\.addEventListener\('click', function\(\) \{\n(.*?)\n\s*\}\);", _js(), re.S,
    )
    assert button and "announce('Position reset')" in button.group(1)


def test_every_way_of_opening_a_model_starts_in_the_middle():
    """From the list, an upload, an ingest, or the model left showing after a
    delete: the plane starts in the middle, and the depth sent is read off the
    reset planes, never the previous model's."""
    for opening, marker in {
        "the model list": 'getElementById("model-list-dropdown").addEventListener("change"',
        "an upload": "getElementById('upload-model-input').addEventListener('change'",
    }.items():
        block = _block_after(marker)
        assert "resetOrientationZoomAndDepth()" in block, opening
        assert block.index("resetOrientationZoomAndDepth()") < block.rindex("sendStateToServer()"), opening
    for opening, marker in {
        "removing the model on display": "getElementById('delete-model-btn').addEventListener('click'",
        "an ingest": "function applyServerState(",
    }.items():
        block = _block_after(marker)
        assert block.index("resetSlicePlanes()") < block.index("syncSliceDepthFromPlanes()"), opening
        assert block.index("syncSliceDepthFromPlanes()") < block.rindex("sendStateToServer()"), opening
    assert "resetSlicePlanes();" in _block_after("function applyStudyDefaults(")
    assert "await" not in _block_after('getElementById("model-list-dropdown").addEventListener("change"')


def test_the_shortcuts_list_says_reset_puts_the_slice_plane_at_50():
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert ("<kbd>0</kbd> Reset position: the view to square, the slice plane to 50%, the zoom to 0 "
            "and the model to the centre") in html
    assert "reset-plane-help" not in html and "reset-plane-help" not in _js()


def test_the_reset_key_calls_the_shared_reset_helper():
    block = _case_block("0")
    assert "resetOrientationZoomAndDepth()" in block, (
        "'0' should reset orientation and zoom the same way the Reset "
        "Position button does, not just pan"
    )
    assert "clearCameraCenterState()" in block


def test_z_no_longer_resets():
    """Z picks the Z axis in XYZ mode, so it cannot also be reset."""
    match = re.search(r"case 'z':\n(.*?)\n\s*break;", _js(), re.S)
    assert match is None or "resetOrientationZoomAndDepth()" not in match.group(1)


def test_reset_button_calls_the_shared_reset_helper():
    js = _js()
    match = re.search(
        r"resetPositionBtn\.addEventListener\('click', function\(\) \{\n(.*?)\n\s*\}\);",
        js, re.S,
    )
    assert match, "resetPositionBtn click handler not found in viewer.js"
    block = match.group(1)
    assert "resetOrientationZoomAndDepth()" in block, (
        "the Reset Position button should reset orientation and zoom the "
        "same way the 'z' key does, not just pan"
    )
    assert "clearCameraCenterState()" in block
