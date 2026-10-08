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
brings the slice plane back on every axis (resetSlicePlanesForMode): to the
middle in Turn mode, the way applyStudyDefaults does for a study step's
starting view, and to the model's origin in XYZ mode (#235 review), where a
model opened in XYZ mode also starts.

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
    back to 0, and the slice plane back on every axis, where the mode puts it."""
    block = _function_block("resetOrientationZoomAndDepth")
    assert "setOrientationFromView(viewerState.currentView)" in block, (
        "reset should undo roll/pitch/yaw back to the CURRENT view's own "
        "basis, not switch to a different named view"
    )
    assert re.search(r"updateZoom\(\s*0\s*,", block), (
        "reset should zoom back out to 0 (MIN_ZOOM), the same value the "
        "viewer starts at"
    )
    assert "resetSlicePlanesForMode()" in block, (
        "reset should bring the slice plane back too, not just orientation "
        "and zoom"
    )
    # The planes must be reset before syncSliceDepthFromPlanes() re-derives
    # the displayed percentage from them, or the display would show the stale
    # pre-reset depth for one more render.
    assert block.index("resetSlicePlanesForMode()") < block.index("syncSliceDepthFromPlanes()")


def test_turn_mode_resets_every_plane_to_the_middle():
    """50% on all three axes, as before XYZ mode existed."""
    assert "{ x: 0.5, y: 0.5, z: 0.5 }" in _function_block("resetSlicePlanes")
    block = _function_block("resetSlicePlanesForMode")
    assert block.index("resetSlicePlanes()") < block.index("if (!isXyzMode()) return;")


def test_xyz_mode_resets_every_plane_to_the_origin():
    """The origin reads 0% on every axis of every model (#235 review). An origin
    beyond the object is reached only as far as the nearest face, since the cut
    stays inside the object; an axis whose origin is not known stays in the
    middle."""
    block = _function_block("resetSlicePlanesForMode")
    assert "for (const axis of ['x', 'y', 'z'])" in block
    assert "originFraction(axis)" in block
    assert "Math.min(1, Math.max(0, origin))" in block


def test_reset_says_where_the_slice_plane_went():
    """In XYZ mode reset says the plane is at the origin, or how far along it is
    when the origin lies beyond the object. Turn mode says what it always said."""
    block = _function_block("announcePositionReset")
    assert "isXyzMode() ? cutPercent() : null" in block
    assert "'Position reset. Slice plane at the origin.'" in block
    assert "emit('Position reset')" in block
    assert "announcePositionReset(announceAlert)" in _case_block("0")
    assert "'Position reset'" not in _case_block("0")
    button = re.search(
        r"resetPositionBtn\.addEventListener\('click', function\(\) \{\n(.*?)\n\s*\}\);", _js(), re.S,
    )
    assert button and "announcePositionReset(announce)" in button.group(1)


def test_every_way_of_opening_a_model_starts_where_a_reset_does():
    """A model opened in XYZ mode starts at its origin, so its first view and a
    reset agree (#235 review). The origin is asked for before the first render,
    so the display gets one frame rather than one at 50% and then another."""
    openings = {
        "the model list": 'getElementById("model-list-dropdown").addEventListener("change"',
        "an upload": "getElementById('upload-model-input').addEventListener('change'",
        "removing the model on display": "getElementById('delete-model-btn').addEventListener('click'",
        "an ingest": "function applyServerState(",
        "page load and ?model=": "document.addEventListener('DOMContentLoaded', async function()",
    }
    for opening, marker in openings.items():
        block = _block_after(marker)
        assert "placeNewModelAtOrigin()" in block, opening
        assert block.index("placeNewModelAtOrigin()") < block.rindex("sendStateToServer()"), opening


def test_the_origin_is_asked_for_only_when_it_is_needed():
    """Only in XYZ mode and only for a model whose origin is not known yet; a
    cut moved while the answer was on its way is left where it was put."""
    block = _function_block("placeNewModelAtOrigin")
    assert "if (!isXyzMode() || originKnown()) return true;" in block
    assert "await fetchModelOrigin(model)" in block
    assert "if (model !== viewerState.currentModel) return false;" in block
    assert "untouched" in block
    assert "/render/origin" in _function_block("fetchModelOrigin")


def test_the_shortcuts_list_says_where_reset_puts_the_slice_plane():
    """The wording follows the mode: 50% in Turn mode, the origin in XYZ mode."""
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert '<kbd>0</kbd> Reset position: the view to square, the slice plane to <span id="reset-plane-help">' in html
    assert "resetPlaneHelp.textContent = xyz ? 'the origin' : '50%'" in _function_block("syncAxisModeUI")


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
