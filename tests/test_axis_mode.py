"""Axis mode (#185): Turn or XYZ, and no control that acts in both.

Turn mode is pitch, roll and yaw. XYZ mode cuts along X, Y or Z, each view one of
OpenSCAD's standard views, for people who model in code and already think in
axes. The rule that holds the two together is that no key, button, chord or cube
gesture does something in both: a key from the other mode says which mode it
belongs to and does nothing else, so a mode error is heard rather than silently
turning or cutting.

There is no JS runtime in the repo, so these parse viewer.js and the page and
check properties of what they declare, the way test_viewer_modes.py and
test_monarch_controls.py do. What the viewer says was checked in a browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from app import study_protocol

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
VIEWER_HTML = ROOT / "accessible-3d-viewer.html"


def _js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _html() -> str:
    return VIEWER_HTML.read_text(encoding="utf-8")


def _mode_keys() -> dict[str, list[str]]:
    block = re.search(r"const AXIS_MODE_KEYS = \{(.*?)\n\};", _js(), re.S)
    assert block, "AXIS_MODE_KEYS not found"
    return {
        mode: re.findall(r"'([^']+)'", keys)
        for mode, keys in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1))
    }


def _supported_shortcuts() -> set[str]:
    block = re.search(r"const supportedShortcuts = new Set\(\[(.*?)\]\);", _js(), re.S)
    assert block, "supportedShortcuts not found"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _keydown_handler() -> str:
    start = _js().index("document.addEventListener('keydown', function(e) {")
    end = _js().index("\n});", start)
    return _js()[start:end]


def _code_only(source: str) -> str:
    """Source without comments, so prose about a removed line cannot fail or
    pass a check meant for code (see test_monarch_controls._code_only)."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", source)


def _view_basis() -> dict[str, dict[str, np.ndarray]]:
    block = re.search(r"const VIEW_BASIS = \{(.*?)\n\};", _js(), re.S)
    found = re.findall(
        r"'([^']+)':\s*\{\s*right:\s*(\[[^\]]*\]),\s*up:\s*(\[[^\]]*\]),\s*depth:\s*(\[[^\]]*\])",
        block.group(1),
    )
    return {t: {"right": np.array(json.loads(r)), "up": np.array(json.loads(u)),
                "depth": np.array(json.loads(d))} for t, r, u, d in found}


def _xyz_axes() -> dict[str, list[str]]:
    block = re.search(r"const XYZ_AXES = \{(.*?)\n\};", _js(), re.S)
    assert block, "XYZ_AXES not found"
    return {axis: re.findall(r"'([^']+)'", views)
            for axis, views in re.findall(r"(\w):\s*\{[^}]*views:\s*\[([^\]]*)\]", block.group(1))}


# --- Keys belong to one mode ----------------------------------------------------


def test_the_two_modes_share_no_key():
    keys = _mode_keys()
    assert set(keys) == {"turn", "xyz"}
    shared = set(keys["turn"]) & set(keys["xyz"])
    assert not shared, f"{sorted(shared)} would act in both modes"


def test_turn_mode_owns_the_six_turn_keys_and_xyz_mode_the_axis_keys():
    keys = _mode_keys()
    assert sorted(keys["turn"]) == sorted(["u", "o", "i", "k", "j", "l"])
    assert {"x", "y", "z"} <= set(keys["xyz"])


def test_every_mode_key_reaches_the_handler():
    """A key missing from supportedShortcuts is dropped before the mode check,
    so it would neither act nor say whose it is."""
    missing = {k for keys in _mode_keys().values() for k in keys} - _supported_shortcuts()
    assert not missing, f"{sorted(missing)} never reach the key handler"


def test_a_key_from_the_other_mode_is_refused_before_anything_acts():
    """The mode check has to come before the switch that acts on the key, or a
    wrong-mode press would turn or cut first and apologise after."""
    handler = _keydown_handler()
    check = handler.index("axisModeForKey(normalizedKey)")
    refuse = handler.index("announceWrongModeKey(")
    switch = handler.index("switch(normalizedKey)")
    assert check < refuse < switch
    assert "return;" in handler[refuse:switch], "a refused key must stop there"


def test_the_turn_buttons_refuse_in_xyz_mode_and_the_axis_buttons_in_turn_mode():
    """Only one set is shown at a time, and each also refuses when the other
    mode is on, so no button acts in both even if one were reached."""
    js = _js()
    turn_handler = js[js.index("for (const [buttonId, rotationName] of Object.entries(ORIENTATION_BUTTONS))"):]
    turn_handler = turn_handler[:turn_handler.index("\n}\n")]
    assert "if (isXyzMode()) return;" in turn_handler

    wiring = [m.start() for m in re.finditer(
        re.escape("for (const [axis, button] of Object.entries(axisButtons()))"), js)]
    axis_handler = next(
        js[start:js.index("\n}\n", start)] for start in wiring
        if "addEventListener('click'" in js[start:js.index("\n}\n", start)]
    )
    assert "if (!isXyzMode()) return;" in axis_handler
    flip = js[js.index("axisFlipBtn.addEventListener('click'"):]
    assert "if (!isXyzMode()) return;" in flip[:flip.index("});")]


def test_the_page_shows_one_mode_s_controls_at_a_time():
    html = _html()
    assert re.search(r'<div id="turn-controls">', html), "turn controls should show by default"
    assert re.search(r'<div id="xyz-controls" hidden>', html), "XYZ controls should start hidden"
    sync = _js()[_js().index("function syncAxisModeUI()"):]
    sync = sync[:sync.index("\n}\n")]
    assert "turnControls.hidden = xyz" in sync and "xyzControls.hidden = !xyz" in sync


# --- XYZ mode's views --------------------------------------------------------------


def test_each_axis_is_the_pair_of_views_along_it():
    views = _xyz_axes()
    basis = _view_basis()
    assert set(views) == {"x", "y", "z"}
    for axis, (first, second) in views.items():
        index = "xyz".index(axis)
        assert np.array_equal(basis[first]["depth"], -basis[second]["depth"]), (
            f"{axis}: the two views should look along the same axis from opposite sides"
        )
        assert basis[first]["depth"][index] != 0, f"{axis}: {first} does not look along {axis}"


def test_each_axis_starts_where_both_display_axes_increase_right_and_up():
    """Top, Front and Right: the only views where both display axes point the
    positive way, which is what blind co-designers expected on a flat board
    (Kamath et al., CHI '26)."""
    basis = _view_basis()
    for axis, (first, _second) in _xyz_axes().items():
        assert basis[first]["right"].sum() == 1 and basis[first]["up"].sum() == 1, (
            f"{axis} starts on {first}, where an axis runs the negative way"
        )


# --- Choosing the axis and the side ---------------------------------------------------


def _function(name: str) -> str:
    js = _js()
    start = js.index(f"function {name}(")
    return js[start:js.index("\n}\n", start)]


def test_an_axis_key_lands_on_the_home_view_unless_that_axis_is_showing():
    """A letter for another axis gives that axis's home view, whatever was showing
    before and whatever the cube last did, so X from anywhere on Y is Right. Only
    the letter of the axis already on show changes side, which is what makes the
    other side reachable without a modifier and never by accident."""
    select = _function("selectAxis")
    assert "const onThisAxis = showing === home || showing === other;" in select
    assert "const target = onThisAxis ? (showing === home ? other : home) : home;" in select
    handler = _keydown_handler()
    case = handler[handler.index("case 'x':"):]
    case = case[:case.index("break;")]
    assert "selectAxis(normalizedKey)" in case
    assert "shiftKey" not in case, "the axis keys ignore Shift, so Caps Lock cannot matter"


def test_caps_lock_does_not_pick_the_other_side():
    """The side comes from Shift, never from whether the letter came through in
    capitals, which Caps Lock also does."""
    case = _keydown_handler()[_keydown_handler().index("case 'x':"):]
    case = case[:case.index("break;")]
    assert "toUpperCase" not in case and "rawKey" not in case


def test_the_same_key_again_gives_the_other_side_and_a_third_comes_back():
    """Minus is two presses of one key (#235 review). The letter of the axis on
    show swaps its two views, so a third press returns to the first."""
    select = _function("selectAxis")
    assert "showing === home ? other : home" in select
    assert "otherSide" not in select, "nothing should be left of the Shift argument"


def test_the_other_side_button_and_the_devices_use_the_same_rule():
    """The button is for pointer users. A braille display sends the plain axis
    letter and gets the second-press flip from selectAxis, so one rule covers the
    keyboard, the DotPad and the Monarch instead of three."""
    js = _js()
    flip_button = js[js.index("axisFlipBtn.addEventListener('click'"):]
    assert "flipSide(announce)" in flip_button[:flip_button.index("});")]
    device = _function("axisCommandFromDevice")
    assert "selectAxis(axis)" in device
    assert "flipOnRepeat" not in device and "otherSide" not in device


def test_the_log_says_whether_shift_was_held():
    report = _keydown_handler()
    report = report[report.index("reportStudyInteraction('keyboard', {"):]
    assert "shift: Boolean(e.shiftKey)" in report[:report.index("});")]


def test_the_help_names_both_sides():
    xyz = _html()[_html().index('id="xyz-shortcuts-section"'):]
    xyz = xyz[:xyz.index("</div>")]
    assert "<kbd>Shift</kbd>" not in xyz, "Shift no longer picks the side"
    assert "again" in xyz, "the help should say the same letter again gives the other side"


# --- Defaults, and where Reset went -------------------------------------------------


def test_turn_stays_the_default_until_the_pilot():
    assert "viewerState.axisMode = 'turn';" in _js()
    assert re.search(r'id="axis-mode-turn" value="turn" checked', _html())
    assert study_protocol.VIEWER_DEFAULTS["axis_mode"] == "turn"


def test_xyz_mode_marks_the_edges_and_the_origin_unless_turned_off():
    """Both drawn only in XYZ mode, and both on by default: a stored "0" is the
    only thing that turns either off."""
    js = _js()
    assert "viewerState.showAxisLetters = true;" in js
    assert "viewerState.showOriginMarker = true;" in js
    init = js[js.index("function initializeAxisSettings()"):]
    init = init[:init.index("\n}\n")]
    assert "read(SETTINGS_AXIS_LETTERS_KEY) !== '0'" in init
    assert "read(SETTINGS_ORIGIN_MARKER_KEY) !== '0'" in init
    html = _html()
    assert 'id="settings-axis-letters" checked' in html
    assert 'id="settings-origin-marker" checked' in html
    assert "show_axis_letters: isXyzMode() && viewerState.showAxisLetters" in js


def test_there_is_no_millimetre_mode_left():
    """Reading the cut in millimetres was taken out until it can be done
    reliably; nothing should still offer it or claim it."""
    js, html = _js(), _html()
    assert "settings-units-mm" not in html
    for name in ("treatUnitsAsMm", "cutUnits", "bbox_model", "millimeter"):
        assert name not in js, f"{name} is still in viewer.js"


def test_the_study_never_starts_in_xyz_mode():
    """Settings is not gated on /study, but the protocol's mode wins at start-up
    and at every model load, and is not written back to the browser."""
    js = _js()
    init = js[js.index("function initializeAxisSettings()"):]
    init = init[:init.index("\n}\n")]
    assert "studyMode ? 'turn'" in init
    defaults = js[js.index("function applyStudyDefaults(defaults)"):]
    defaults = defaults[:defaults.index("\n}\n")]
    assert "wanted.axis_mode" in defaults


def test_reset_is_0_in_the_help_and_z_is_not_reset_anywhere():
    html = _html()
    assert "<kbd>0</kbd> Reset position" in html
    assert "<kbd>Z</kbd> Reset" not in html
    assert "Key: Z." not in html
    assert "Key: 0." in html


def test_z_in_turn_mode_says_where_reset_went():
    wrong = _js()[_js().index("function announceWrongModeKey("):]
    wrong = wrong[:wrong.index("\n}\n")]
    assert "key === 'z'" in wrong and "Reset is now 0" in wrong


def test_the_help_has_a_section_for_each_mode_and_shows_one():
    html = _html()
    assert 'id="turn-shortcuts-section"' in html
    assert re.search(r'<div id="xyz-shortcuts-section" hidden>', html)
    xyz = html[html.index('id="xyz-shortcuts-section"'):]
    xyz = xyz[:xyz.index("</div>")]
    for key in ("X", "Y", "Z"):
        assert f"<kbd>{key}</kbd>" in xyz, f"XYZ help does not list {key}"
    # "," works in both modes, so it belongs outside the mode-specific sections
    # rather than in this one (#235 review).
    assert "<kbd>,</kbd>" not in xyz
    assert "<kbd>,</kbd>" in html


# --- WCAG 2.1.4 ------------------------------------------------------------------


def test_single_key_shortcuts_can_be_turned_off():
    assert 'id="settings-single-key-shortcuts"' in _html()
    handler = _keydown_handler()
    guard = handler.index("!viewerState.singleKeyShortcuts && normalizedKey.length === 1")
    assert guard < handler.index("switch(normalizedKey)")


# --- The hardware modules report what triggered a render -------------------------


@pytest.mark.parametrize("driver,source", [
    ("witmotion-imu.js", "witmotion"),
    ("trinkey-slider.js", "slider"),
])
def test_cube_and_slider_renders_are_not_logged_as_keyboard(driver, source):
    """They assigned window.pendingInputSource, which is not the variable the
    render reads (a top-level let is not a property of window), so every cube
    and slider render was logged as keyboard."""
    code = _code_only((ROOT / "static" / "js" / driver).read_text(encoding="utf-8"))
    assert "window.pendingInputSource" not in code
    js = _js()
    assert "window.pendingInputSource" not in _code_only(js)
    code = (ROOT / "static" / "js" / driver).read_text(encoding="utf-8")
    if driver == "trinkey-slider.js":
        assert f"window.setPendingInputSource?.('{source}')" in code
    else:
        assert "window.selectViewFromCube(view)" in code
        cube = js[js.index("function selectViewFromCube("):]
        assert f"pendingInputSource = '{source}';" in cube[:cube.index("\n}\n")]


def test_the_dotpad_axis_chords_are_read_before_the_depth_dots():
    """x, y and z each contain dot 1 or dot 4, the depth keys, so they must be
    matched as whole letters before any single dot is."""
    code = (ROOT / "static" / "js" / "dotpad-integration.js").read_text(encoding="utf-8")
    axis = code.index("if (letter === 'x' || letter === 'y' || letter === 'z')")
    assert axis < code.index("if (byte6 === 0x01)") and axis < code.index("if (byte6 === 0x08)")
    assert "window.axisCommandFromDevice(letter, 'dotpad')" in code


# --- The cube (witmotion-imu.js) -----------------------------------------------------


def _cube_faces() -> dict[str, list[int]]:
    """Which view each face of the cube picks when it points up, keyed by view."""
    code = (ROOT / "static" / "js" / "witmotion-imu.js").read_text(encoding="utf-8")
    normals = re.search(r"const FACE_NORMALS = \[(.*?)\];", code, re.S)
    names = re.search(r"const FACE_NAMES = \[(.*?)\];", code, re.S)
    assert normals and names, "the cube's face table was not found"
    vectors = [json.loads(v) for v in re.findall(r"\[\s*-?\d+,\s*-?\d+,\s*-?\d+\s*\]", normals.group(1))]
    labels = re.findall(r"'([^']+)'", names.group(1))
    assert len(vectors) == len(labels) == 6
    return dict(zip(labels, vectors))


def test_the_cube_lying_flat_is_the_view_from_above():
    """The one face the spec pins: Z face up is Top, Z coming out of the display."""
    assert _cube_faces()["z+"] == [0, 0, 1]


def test_each_face_of_the_cube_picks_a_different_view():
    assert set(_cube_faces()) == set(_view_basis())


def test_turning_the_cube_over_gives_the_other_side_of_the_same_axis():
    """In XYZ mode the face pointing up picks the axis and the side, so the face
    opposite it must be the same axis seen from the other side, never another
    axis. Checked against the viewer's own bases."""
    faces = _cube_faces()
    basis = _view_basis()
    for view, normal in faces.items():
        (opposite,) = [v for v, n in faces.items() if n == [-c for c in normal]]
        assert np.array_equal(basis[view]["depth"], -basis[opposite]["depth"]), (
            f"turning the cube over from {view} lands on {opposite}, a different axis"
        )


def test_the_cube_goes_through_the_viewer_and_waits_for_a_settled_face():
    code = _code_only((ROOT / "static" / "js" / "witmotion-imu.js").read_text(encoding="utf-8"))
    handler = code[code.index("function onCharacteristicChanged("):]
    assert "alignment < FACE_UP_MIN_ALIGNMENT" in handler
    assert "now - candidateSince < FACE_SETTLE_MS" in handler
    assert handler.index("FACE_SETTLE_MS") < handler.index("window.selectViewFromCube(view)")


# --- What the review of #235 turned up -------------------------------------------


def test_the_hardware_depth_inputs_use_the_axis_scale_in_xyz_mode():
    """The Trinkey slider sets depth through window.updateSliceDepth, and
    window.getCurrentSliceDepth reads it back. In XYZ mode those have to speak the
    position along the cut axis, the number the on-screen slider shows.

    Left on Turn mode's depth-from-the-reader they ran the other way: from the
    default views the slider pushed to 30 put the plane at 1 - 0.30 and the
    readout jumped to 70. (The DotPad's and the Monarch's depth keys step through
    stepSliceDepth instead; see the next test.)
    """
    js = _js()
    setter = js[js.index("function updateSliceDepth("):]
    setter = setter[:setter.index("\nfunction ")]
    assert "isXyzMode()" in setter, "updateSliceDepth still treats every mode as depth"
    assert "setCutPosition(currentCutAxis()" in setter
    assert setter.index("setCutPosition") < setter.index("writeDisplayDepthToPlanes"), (
        "the XYZ branch must return before the depth-from-the-reader path"
    )

    getter = js[js.index("function getCurrentSliceDepth("):]
    getter = getter[:getter.index("\n}")]
    assert "cutPercent(currentCutAxis())" in getter, (
        "a device reads this, adds its step and hands it back, so it must be the "
        "same scale updateSliceDepth expects"
    )


def test_asking_where_the_origin_is_works_in_both_modes():
    """"," was XYZ-only, which made it say "Comma is XYZ only" in Turn mode. Where
    the origin sits against the cut and the outline is worth asking in either, so
    it belongs to neither mode's key list.
    """
    keys = _mode_keys()
    assert "," not in keys["xyz"], '"," is no longer an XYZ-only key'
    assert "," not in keys["turn"]
    assert "," in _supported_shortcuts()
    assert "case ',':" in _keydown_handler()

    js = _js()
    origin = js[js.index("function announceOrigin("):]
    origin = origin[:origin.index("\n}")]
    assert "!isXyzMode()" in origin, "announceOrigin has no wording for Turn mode"
    turn_branch = origin[origin.index("if (!isXyzMode())"):]
    turn_branch = turn_branch[:turn_branch.index("return;")]
    assert "signedPercent" not in turn_branch, (
        "Turn mode is not working in an axis, so it should not be given a "
        "coordinate along one"
    )


def _function(name: str) -> str:
    js = _js()
    body = js[js.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def test_the_depth_keys_move_the_cut_the_same_way_in_both_modes():
    """Jen's review asked for a given key to move the cut the same way in both
    modes. XYZ mode had its own table sending Up toward +axis, which from Top,
    Right and Back is toward the reader, the opposite of Turn mode's Up. Now there
    is one set of cases, and they go through functions that handle both modes."""
    handler = _code_only(_keydown_handler())
    assert "xyzCutKeys" not in handler, "XYZ mode still has its own depth-key table"
    compact = re.sub(r"\s+", "", handler)
    for key, call in (
        ("arrowup", "stepSliceDepth(1);"),
        ("arrowdown", "stepSliceDepth(-1);"),
        ("pageup", "stepSliceDepth(10);"),
        ("pagedown", "stepSliceDepth(-10);"),
    ):
        case = compact[compact.index(f"case'{key}':"):]
        case = case[:case.index("break;")]
        assert call in case, f"{key} does not step the depth through stepSliceDepth"
    ends = compact[compact.index("case'home':"):]
    ends = ends[:ends.index("break;")]
    assert "goToSliceEnd(normalizedKey==='end');" in ends


def test_deeper_is_away_from_the_reader_in_xyz_mode():
    """The reader is on the +depth side of the cut axis, so deeper runs toward
    -sign. From Top, Right and Back the reader is on the + side, so deeper lowers
    the number read out; that is what the help and the README say."""
    step = _code_only(_function("stepSliceDepth"))
    xyz = step[step.index("if (isXyzMode())"):]
    xyz = xyz[:xyz.index("return stepCut(")+60]
    assert "activeSliceAxis()" in xyz
    assert "-sign * Math.sign(delta)" in xyz

    lowers = {t for t, b in _view_basis().items() if b["depth"].sum() > 0}
    assert lowers == {"z+", "x-", "y+"}, "the views where deeper lowers the number"
    names = {"z+": "above", "x-": "the right", "y+": "the back"}
    js = _js()
    assert all(names[t] == re.search(rf"'{re.escape(t)}': '([^']+)'", js[js.index("const VIEW_SIDES = {"):]).group(1)
               for t in lowers)
    help_text = _html()[_html().index("<h3>Depth</h3>"):]
    assert "seen from above, the right or the back, going deeper lowers the number" in help_text

    ends = _code_only(_function("goToSliceEnd"))
    assert "const nearest = sign > 0 ? 1 : 0;" in ends, "Home must be the surface nearest the reader"


def test_the_device_depth_keys_step_like_the_arrow_keys():
    """The DotPad's depth dots and the Monarch's depth keys used to add their step
    to the axis position, so their "deeper" went toward +axis. They now call the
    same function as Arrow Up and Down."""
    dotpad = _code_only((ROOT / "static" / "js" / "dotpad-integration.js").read_text(encoding="utf-8"))
    monarch = _code_only((ROOT / "static" / "js" / "monarch-hid.js").read_text(encoding="utf-8"))
    assert "window.stepSliceDepth(-100/n)" in dotpad and "window.stepSliceDepth(100/n)" in dotpad
    assert "window.stepSliceDepth?.(command.delta)" in monarch
    for source in (dotpad, monarch):
        assert "getCurrentSliceDepth" not in source


def test_a_focused_depth_slider_keeps_the_slider_pattern():
    """The slider shows the position along the axis in XYZ mode, and a focused
    slider's Up has to raise the value it reports. So while it has focus the keys
    step along the axis; everywhere else they go deeper."""
    handler = _code_only(_keydown_handler())
    block = handler[handler.index("if (isXyzMode() && target === sliceSlider)"):]
    block = block[:block.index("switch(normalizedKey)")]
    assert "arrowup: () => stepCut(1," in block
    assert "home: () => setCutPosition(currentCutAxis(), 0)" in block
    assert handler.index("target === sliceSlider") < handler.index("switch(normalizedKey)")


def test_an_axis_key_says_the_axis_the_side_and_how_the_other_two_run():
    """Jen's review asked for a much shorter announcement: the axis and side, then
    the other two axes, "Y from the front, X right, Z up", and pressing Y again
    "Y from the back, X left, Z up". The side is named rather than said as plus or
    minus: for X the tokens name the side opposite the reader's, so "plus" could
    not mean one thing on every axis."""
    show = _code_only(_function("showXyzView"))
    assert "cutPositionPhrase" not in show, "the axis key still reads the cut position out"
    assert "now increases" not in show
    assert "${letter} ${side.short}, ${axes.speech}." in show
    assert "displayAxesPhrase(currentBasis(), false)" in show

    axes = _code_only(_function("displayAxesPhrase"))
    assert "${axisLetter(right.axis)} ${rightWord}, ${axisLetter(up.axis)} ${upWord}" in axes
    side = _code_only(_function("sideOfView"))
    assert "short: `from ${side}`" in side
