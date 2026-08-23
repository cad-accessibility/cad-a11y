"""Tests for the Z shortcut / Reset Position button (#183).

"Position reset" was found to only actually reset pan: the Z key and the
Reset Position button set currentMoveCamera = "reset" and sent it to the
server, but never touched viewerState.currentZoom, the orientation basis
(orientationRight/Up/Depth), or the slice depth, so a rotated, zoomed-in, or
deeply-sliced view came back centred on itself rather than on the object.

The fix adds a shared resetOrientationZoomAndDepth() helper -- called by both
the 'z' keydown case and the Reset Position button -- that puts the
orientation back to the straight-on basis for whatever view is currently
selected (setOrientationFromView), zooms back out to 0 (updateZoom), and
brings the slice plane back to the middle of every axis (resetSlicePlanes),
the same way applyStudyDefaults already does for a study step's starting
view.

These parse the shipped viewer.js source directly (same convention as
test_pan_direction_wording.py), rather than re-implementing the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"


def _js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _case_block(key: str) -> str:
    match = re.search(rf"case '{re.escape(key)}':\n(.*?)\n\s*break;", _js(), re.S)
    assert match, f"case '{key}' not found in viewer.js"
    return match.group(1)


def _function_block(name: str) -> str:
    js = _js()
    start = js.index(f"function {name}(")
    # Body runs from the first '{' after the signature to its matching '}'.
    brace_start = js.index("{", start)
    depth = 0
    for i in range(brace_start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[brace_start:i + 1]
    raise AssertionError(f"unbalanced braces in function {name}")


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
        "reset should bring the slice plane back to 50% too, not just "
        "orientation and zoom"
    )
    # resetSlicePlanes() must run before syncSliceDepthFromPlanes() re-derives
    # the displayed percentage from it, or the display would show the stale
    # pre-reset depth for one more render.
    assert block.index("resetSlicePlanes()") < block.index("syncSliceDepthFromPlanes()")


def test_z_key_calls_the_shared_reset_helper():
    block = _case_block("z")
    assert "resetOrientationZoomAndDepth()" in block, (
        "'z' should reset orientation and zoom the same way the Reset "
        "Position button does, not just pan"
    )
    assert "clearCameraCenterState()" in block


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
