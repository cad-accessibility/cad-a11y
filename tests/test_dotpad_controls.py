from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOTPAD_JS = ROOT / "static" / "js" / "dotpad-integration.js"
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def _byte6_branch(source: str, byte_value: str) -> str:
    match = re.search(
        rf"if\s*\(\s*byte6\s*===\s*{re.escape(byte_value)}\s*\)\s*\{{(?P<body>.*?)\n\s*\}}",
        source,
        re.DOTALL,
    )
    assert match is not None, f"Missing DotPad depth branch for {byte_value}"
    return match.group("body")


def _letter_branch(source: str, letter: str) -> str:
    match = re.search(
        rf"if\s*\(\s*letter\s*===\s*['\"]{re.escape(letter)}['\"]\s*\)\s*\{{(?P<body>.*?)\n\s{{4}}\}}",
        source,
        re.DOTALL,
    )
    assert match is not None, f"Missing DotPad letter branch for {letter}"
    return match.group("body")


def test_viewer_exposes_depth_helpers():
    source = _source(VIEWER_JS)

    assert "window.getCurrentSliceDepth = getCurrentSliceDepth;" in source
    assert "window.updateSliceDepth = updateSliceDepth;" in source
    assert "window.announceDepthValue = announceDepthValue;" in source
    assert "window.stepSliceDepth = stepSliceDepth;" in source
    assert "window.goToSliceEnd = goToSliceEnd;" in source


def test_dotpad_depth_controls_run_before_cursor_movement_requirements():
    source = _source(DOTPAD_JS)

    depth_indexes = [
        source.index(f"if (byte6 === {byte})")
        for byte in ("0x01", "0x08", "0x03", "0x18", "0x07", "0x38")
    ]
    move_cursor_guard_index = source.index("if (typeof window.moveCursor")

    assert all(index < move_cursor_guard_index for index in depth_indexes)


def test_dot_1_goes_shallower():
    """Through the viewer's stepSliceDepth, the function Page Down uses, so
    shallower means toward the reader in both axis modes (#235 review)."""
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x01")
    compact = _compact(body)

    assert "window.stepSliceDepth(-100/n);" in compact
    assert "currentSliceDepth" not in body
    assert "getCurrentSliceDepth" not in body, "a device step must not do its own depth arithmetic"


def test_dot_4_goes_deeper():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x08")
    compact = _compact(body)

    assert "window.stepSliceDepth(100/n);" in compact
    assert "currentSliceDepth" not in body
    assert "getCurrentSliceDepth" not in body, "a device step must not do its own depth arithmetic"


def test_dots_1_2_go_shallower_by_1():
    body = _byte6_branch(_source(DOTPAD_JS), "0x03")

    assert "window.stepSliceDepth(-1);" in _compact(body)
    assert "getCurrentSliceDepth" not in body, "a device step must not do its own depth arithmetic"


def test_dots_4_5_go_deeper_by_1():
    body = _byte6_branch(_source(DOTPAD_JS), "0x18")

    assert "window.stepSliceDepth(1);" in _compact(body)
    assert "getCurrentSliceDepth" not in body, "a device step must not do its own depth arithmetic"


def test_dots_1_2_3_are_home():
    """The surface nearest the reader, through the function Home uses (#280)."""
    body = _compact(_byte6_branch(_source(DOTPAD_JS), "0x07"))

    assert "window.goToSliceEnd(false);" in body
    assert "scrollTactileText" not in body


def test_dots_4_5_6_are_end():
    body = _compact(_byte6_branch(_source(DOTPAD_JS), "0x38"))

    assert "window.goToSliceEnd(true);" in body
    assert "scrollTactileText" not in body


def test_dotpad_cursor_cycle_uses_v_chord():
    source = _source(DOTPAD_JS)
    body = _letter_branch(source, "v")
    compact = _compact(body)

    assert "window.cycleCursorState();" in compact
    assert "letter==='c'" not in _compact(source)


def test_viewer_exposes_fit_helper_for_dotpad():
    source = _source(VIEWER_JS)

    assert "window.fitCurrentViewToDevice = fitCurrentViewToDevice;" in source


def test_dotpad_fit_control_is_scoped_to_f_chord():
    source = _source(DOTPAD_JS)
    compact = _compact(source)

    assert "window.fitCurrentViewToDevice();" in _compact(_letter_branch(source, "f"))
    f_branch_index = compact.index("if(letter==='f'){")
    depth_index = compact.index("if(byte6===0x01)")
    move_cursor_guard_index = compact.index("if(typeofwindow.moveCursor")

    assert f_branch_index < depth_index < move_cursor_guard_index


def test_panning_keys_scroll_the_text_line_while_the_cursor_is_off():
    """LP and RP page through a long message, as a braille display's panning keys
    do, and still move the cursor once it is on (#154)."""
    source = _source(DOTPAD_JS)
    compact = _compact(source)

    scroll = re.search(r"const DOTPAD_TEXT_SCROLL = \{(.*?)\};", source, re.DOTALL)
    assert scroll is not None, "DOTPAD_TEXT_SCROLL not found"
    assert dict(re.findall(r"(\w+):\s*(-?\d+)", scroll.group(1))) == {
        "PanningLeft": "-1",
        "PanningRight": "1",
    }
    branch = "if(cursorState==='none'&&DOTPAD_TEXT_SCROLL[currKeyCode]){"
    assert branch + "scrollTactileText(DOTPAD_TEXT_SCROLL[currKeyCode]);return;}" in compact
    assert compact.index(branch) < compact.index("if(typeofwindow.moveCursor")
    actions = re.search(r"const DOTPAD_KEY_ACTIONS = \{(.*?)\};", source, re.DOTALL)
    assert actions is not None
    assert "PanningLeft: [-1, 0]" in actions.group(1)
    assert "PanningRight: [1, 0]" in actions.group(1)


def test_the_tutorial_hears_the_cursor_state_the_key_will_act_in():
    """Key help names a panning key for what it is about to do, so the cursor
    state is read before the tutorial sees the key."""
    compact = _compact(_source(DOTPAD_JS))
    on_key = compact[compact.index("functiononKey("):]

    read = on_key.index("constcursorState=window.whichCursor?window.whichCursor():'none';")
    capture = on_key.index("name:dotpadTutorialName(currKeyCode,byte6,letter,cursorState)")
    assert read < capture
    assert "(cursorState==='none'?DOTPAD_TUTORIAL_TEXT_KEYS[currKeyCode]:null)" in compact
