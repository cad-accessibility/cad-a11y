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

    view_handler = js[js.index("for (const button of viewButtons()) {\n    button.addEventListener('click'"):]
    view_handler = view_handler[:view_handler.index("\n}\n")]
    assert "if (!isXyzMode()) return;" in view_handler


def test_the_page_shows_one_mode_s_controls_at_a_time():
    """XYZ is the default, so its controls are the ones the page starts with."""
    html = _html()
    assert re.search(r'<div id="xyz-controls">', html), "XYZ controls should show by default"
    assert re.search(r'<div id="turn-controls" hidden>', html), "turn controls should start hidden"
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


def test_the_devices_use_the_keys_rule():
    """A braille display sends the plain axis letter and gets the second-press
    flip from selectAxis, so one rule covers the keyboard, the DotPad and the
    Monarch instead of three."""
    device = _function("axisCommandFromDevice")
    assert "selectAxis(axis)" in device
    assert "flipOnRepeat" not in device and "otherSide" not in device


def test_there_is_a_button_for_each_axis_and_side_and_no_other_side_button():
    """The #235 review asked for "X plus" to "Z minus" in place of three axis
    buttons and Other side, grouped by axis, with no button that turns round."""
    html = _html()
    controls = html[html.index('<div id="xyz-controls">'):]
    controls = controls[:controls.index('<p id="xyz-position"')]
    buttons = re.findall(r'<button type="button" id="([\w-]+)" data-axis="(\w)" data-side="(\w+)">([^<]+)</button>', controls)
    assert [(axis, side, label) for _id, axis, side, label in buttons] == [
        ("x", "plus", "X plus"), ("x", "minus", "X minus"),
        ("y", "plus", "Y plus"), ("y", "minus", "Y minus"),
        ("z", "plus", "Z plus"), ("z", "minus", "Z minus"),
    ]
    assert "Other side" not in controls and "axis-flip-btn" not in html
    assert "flipSide" not in _js() and "axisFlipBtn" not in _js()


def test_a_view_button_shows_its_own_view_and_never_turns_round():
    """Pressing the one showing says it again: turning round is X then X again."""
    js = _js()
    handler = js[js.index("for (const button of viewButtons()) {\n    button.addEventListener('click'"):]
    handler = handler[:handler.index("\n}\n")]
    assert "showXyzView(viewFrom(this.dataset.axis, this.dataset.side === 'plus' ? 1 : -1), announce)" in handler
    assert "selectAxis" not in handler


def test_a_view_button_looks_from_the_side_it_names():
    """X plus is the view from +X: the one whose depth points at +X, the token x-.
    viewFrom finds it from the depth vectors rather than from the token names."""
    view_from = _code_only(_function("viewFrom"))
    assert "Math.sign(VIEW_BASIS[token].depth[index]) === sign" in view_from
    basis = _view_basis()
    expected = {("x", 1): "x-", ("x", -1): "x+", ("y", 1): "y+", ("y", -1): "y-", ("z", 1): "z+", ("z", -1): "z-"}
    for (axis, sign), token in expected.items():
        (found,) = [t for t, b in basis.items() if np.sign(b["depth"]["xyz".index(axis)]) == sign]
        assert found == token


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


def test_xyz_is_the_default_and_turn_is_kept_when_chosen():
    """Jen's review of #235: XYZ, not pitch, roll and yaw, is the default. A stored
    choice of Turn is kept, and the study protocol still runs in Turn."""
    init = _function("initializeAxisSettings")
    assert "(studyMode || read(SETTINGS_AXIS_MODE_KEY) === 'turn') ? 'turn' : 'xyz'" in init
    assert re.search(r'id="axis-mode-xyz" value="xyz" checked', _html())
    assert not re.search(r'id="axis-mode-turn" value="turn" checked', _html())
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
    assert "(studyMode || " in init and "? 'turn' :" in init
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
    assert re.search(r'<div id="turn-shortcuts-section" hidden>', html)
    assert re.search(r'<div id="xyz-shortcuts-section">', html)
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


def test_the_hardware_depth_inputs_use_depth_in_both_modes():
    """The Trinkey slider sets depth through window.updateSliceDepth, and
    window.getCurrentSliceDepth reads it back. Depth from the reader is what the
    on-screen slider and every readout use in both modes now (#263), so neither
    has a branch of its own for XYZ mode. (The DotPad's and the Monarch's depth
    keys step through stepSliceDepth instead; see the next tests.)
    """
    js = _js()
    setter = js[js.index("function updateSliceDepth("):]
    setter = setter[:setter.index("\nfunction ")]
    assert "isXyzMode()" not in _code_only(setter)
    assert "writeDisplayDepthToPlanes(viewerState.currentSliceDepth);" in setter

    getter = js[js.index("function getCurrentSliceDepth("):]
    getter = getter[:getter.index("\n}")]
    assert "isXyzMode()" not in getter
    assert "return viewerState.currentSliceDepth;" in getter


def test_asking_where_the_origin_is_works_in_both_modes():
    """"," was XYZ-only, which made it say "Comma is XYZ only" in Turn mode. Where
    the origin is on the display is worth asking in either, so it belongs to
    neither mode's key list, and it is said the same way in both.
    """
    keys = _mode_keys()
    assert "," not in keys["xyz"], '"," is no longer an XYZ-only key'
    assert "," not in keys["turn"]
    assert "," in _supported_shortcuts()
    assert "case ',':" in _keydown_handler()

    origin = _code_only(_function("announceOrigin"))
    assert "isXyzMode" not in origin, "where the origin is on the display does not depend on the mode"
    assert "originOnDisplayPhrase()" in origin


def test_the_origin_is_said_as_where_it_is_on_the_display():
    """The #235 review: "Horizontal: 42%, Vertical: 42%", and past the edges when
    it is off the display, "Horizontal: minus 200%"; "H: 42% V: 42%" in braille.
    Measured from the left and bottom edges, so the numbers rise the way "X right,
    Z up" does."""
    phrase = _code_only(_function("originOnDisplayPhrase"))
    assert "speech: `Horizontal: ${across}%, Vertical: ${up}%`" in phrase
    assert "braille: `H: ${place.across}% V: ${place.up}%`" in phrase
    assert "signedPercent(place.across)" in phrase, "a minus is said as a word"
    setter = _code_only(_function("setOriginDisplay"))
    assert "Math.round(across * 100)" in setter and "Math.round(up * 100)" in setter
    render = _js()[_js().index("if (data.image_base64) setOriginDisplay(data.origin_display);"):]
    assert render, "the origin's place is taken from each frame as it arrives"


def test_where_am_i_is_short_and_names_the_model_on_display():
    """The #235 review's "." : view, depth, origin, render, zoom, model. No
    layout or DotPad, and the model is the one being rendered, not the status
    bar's text, which kept the previous model's name after /ingest opened one.
    Depth is said the same way in both modes (#263)."""
    where = _code_only(_function("announceWhereAmI"))
    for part in ("Origin: ${origin.short}.", "Render: ${render}.", "Zoom: ${zoom}.", "Model: ${modelLabel()}."):
        assert part in where
    assert "Layout" not in where and "DotPad" not in where and "statusBarRest" not in _js()
    xyz = _code_only(_function("xyzDescription"))
    assert "View from ${side.speech}, ${axes.speech}. Depth: ${depth}%." in xyz
    assert "Depth: ${depth}%." in _code_only(_function("turnDescription"))
    label = _code_only(_function("modelLabel"))
    assert "viewerState.currentModel" in label
    assert "sbModel.textContent = modelLabel();" in _function("refreshStatusBar")


def test_depth_means_the_same_in_both_modes():
    """Arun's review, through Jen (#263): XYZ mode read the cut out as its
    position along the axis, measured from the model's origin, so the same key
    and the same number meant different things in the two modes. Depth is percent
    in from the surface nearest the reader in both now, and nothing that reads,
    steps or shows it has a branch for XYZ mode."""
    js = _code_only(_js())
    for gone in ("function cutPercent(", "function cutPlanePhrase(", "function cutReadout(",
                 "function stepCut(", "function announceCutStep(", "function xyzStepPercent(",
                 "originFraction(", "pendingCutAnnouncement"):
        assert gone not in js, f"{gone} is left over from the origin-based readout"
    for name in ("stepSliceDepth", "goToSliceEnd", "announceDepthValue", "refreshDepthControls"):
        body = _code_only(_function(name)).replace("const xyz = isXyzMode();", "")
        assert "isXyzMode()" not in body, name
    status = _code_only(_function("refreshStatusBar"))
    assert "sbDepth.textContent = viewerState.currentSliceDepth + '%';" in status
    assert "sbDepthLabel.textContent = 'Depth';" in status
    slider = js[js.index("sliceSlider.addEventListener('input'"):]
    assert "isXyzMode()" not in slider[:slider.index("});")]


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


def test_deeper_is_away_from_the_reader_in_both_modes():
    """Deeper raises the depth, and Home and End go to the surface nearest the
    reader and the far side, whichever mode and view (#263). The help says so."""
    step = _code_only(_function("stepSliceDepth"))
    assert "previousDepth + delta" in step
    ends = _code_only(_function("goToSliceEnd"))
    assert "const nextDepth = farSide ? 100 : 0;" in ends
    help_text = _html()[_html().index("<h3>Depth</h3>"):]
    help_text = help_text[:help_text.index("<h3>Zoom</h3>")]
    assert "The same in both axis modes: 0% is the surface nearest you and 100% the far side." in help_text
    assert "origin" not in help_text
    # Which side a view is seen from is still named by the axis that points at
    # the reader.
    side = _code_only(_function("axisSide"))
    assert "const word = sign > 0 ? 'plus' : 'minus';" in side
    assert "signedAxisOf(basis ? basis.depth" in side


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
    """The slider's value is depth in both modes, so Up raises the value it
    reports and goes deeper, as the ARIA slider pattern and the other depth keys
    both expect. XYZ mode no longer needs keys of its own for it."""
    handler = _code_only(_keydown_handler())
    assert "target === sliceSlider" not in handler
    controls = _code_only(_function("refreshDepthControls"))
    assert "sliceSlider.value = depth;" in controls
    assert "`${depth} percent depth`" in controls


def test_an_axis_key_says_the_axis_the_side_and_how_the_other_two_run():
    """Jen's review asked for a much shorter announcement, with the side said as
    plus or minus: "X from plus, Y right, Z up", or "X+ Y right Z up" in braille.
    Plus is the side the reader looks from, taken from the depth vector, since
    the token for the view from +X is x-."""
    show = _code_only(_function("showXyzView"))
    assert "cutPlanePhrase" not in show, "the axis key still reads the cut position out"
    assert "${side.letter} from ${side.word}, ${axes.speech}." in show
    assert "braille: `${side.braille} ${axes.braille}`" in show

    axes = _code_only(_function("displayAxesPhrase"))
    assert "${axisLetter(right.axis)} ${right.sign > 0 ? 'right' : 'left'}, ${axisLetter(up.axis)} ${up.sign > 0 ? 'up' : 'down'}" in axes
    side = _code_only(_function("axisSide"))
    assert "braille: `${letter}${sign > 0 ? '+' : '-'}`" in side


def test_the_step_buttons_say_deeper_and_shallower_in_both_modes():
    """In XYZ mode they read "X plus 10%", which would sit next to the "X plus"
    view button and mean something else. They move the depth 10% in both modes."""
    labels = _code_only(_function("updateButtonLabels"))
    assert "isXyzMode" not in labels and "plus" not in labels
    js = _js()
    deeper = js[js.index("deeperBtn.addEventListener('click'"):]
    deeper = deeper[:deeper.index("});")]
    assert "updateSliceDepth(viewerState.currentSliceDepth + 10, true)" in deeper and "isXyzMode" not in deeper
    shallower = js[js.index("shallowerBtn.addEventListener('click'"):]
    shallower = shallower[:shallower.index("});")]
    assert "updateSliceDepth(viewerState.currentSliceDepth - 10, true)" in shallower
    assert "isXyzMode" not in shallower


def test_a_focused_list_radio_or_slider_keeps_its_own_navigation_keys():
    """The #235 review: Home and End were taken from a focused list, radio group
    or slider, and the arrows and Page Up/Down with them, so a screen reader user
    could not jump within the control. Those keys now go to the control, except on
    the depth slider, whose keys the viewer handles in the slider's own terms."""
    handler = _code_only(_keydown_handler())
    guard = handler[handler.index("const ownsNavigationKeys"):]
    guard = guard[:guard.index("return;")]
    for selector in ("select", 'input[type="radio"]', 'input[type="range"]', '[role="radio"]',
                     '[role="listbox"]', '[role="slider"]'):
        assert selector in guard
    assert "target !== sliceSlider" in guard
    native = re.search(r"const NATIVE_NAVIGATION_KEYS = \[([^\]]*)\]", handler)
    assert set(re.findall(r"'([^']+)'", native.group(1))) == {
        "arrowup", "arrowdown", "pageup", "pagedown", "home", "end"}
    assert handler.index("const ownsNavigationKeys") < handler.index("switch(normalizedKey)")
    assert handler.index("const ownsNavigationKeys") < handler.index("reportStudyInteraction('keyboard'")
