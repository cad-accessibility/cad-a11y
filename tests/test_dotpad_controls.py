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
        rf"if\s*\(\s*letter\s*===\s*['\"]{re.escape(letter)}['\"]\s*\)\s*\{{(?P<body>.*?)\n\s*\}}",
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


def test_dotpad_depth_controls_run_before_cursor_movement_requirements():
    source = _source(DOTPAD_JS)

    depth_indexes = [
        source.index("if (byte6 === 0x01)"),
        source.index("if (byte6 === 0x08)"),
        source.index("if (byte6 === 0x03)"),
        source.index("if (byte6 === 0x18)"),
    ]
    move_cursor_guard_index = source.index("if (typeof window.moveCursor")

    assert all(index < move_cursor_guard_index for index in depth_indexes)


def test_dot_1_decreases_depth_by_10():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x01")
    compact = _compact(body)

    assert "constpreviousDepth=window.getCurrentSliceDepth();" in compact
    assert "currentSliceDepth" not in body
    assert "Math.max(0,previousDepth-10)" in compact
    assert "window.updateSliceDepth(nextDepth,false);" in compact
    assert "window.announceDepthValue(nextDepth,previousDepth);" in compact


def test_dot_4_increases_depth_by_10():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x08")
    compact = _compact(body)

    assert "constpreviousDepth=window.getCurrentSliceDepth();" in compact
    assert "currentSliceDepth" not in body
    assert "Math.min(100,previousDepth+10)" in compact
    assert "window.updateSliceDepth(nextDepth,false);" in compact
    assert "window.announceDepthValue(nextDepth,previousDepth);" in compact


def test_dots_1_2_decrease_depth_by_1():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x03")
    compact = _compact(body)

    assert "constpreviousDepth=window.getCurrentSliceDepth();" in compact
    assert "currentSliceDepth" not in body
    assert "Math.max(0,previousDepth-1)" in compact
    assert "window.updateSliceDepth(nextDepth,false);" in compact
    assert "window.announceDepthValue(nextDepth,previousDepth);" in compact


def test_dots_4_5_increases_depth_by_1():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x18")
    compact = _compact(body)

    assert "constpreviousDepth=window.getCurrentSliceDepth();" in compact
    assert "currentSliceDepth" not in body
    assert "Math.min(100,previousDepth+1)" in compact
    assert "window.updateSliceDepth(nextDepth,false);" in compact
    assert "window.announceDepthValue(nextDepth,previousDepth);" in compact


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

    f_branch_index = compact.index("if(letter==='f'){")
    fit_guard_index = compact.index("typeofwindow.fitCurrentViewToDevice!='function'")
    fit_call_index = compact.index("window.fitCurrentViewToDevice();")
    depth_index = compact.index("if(byte6===0x01)")
    move_cursor_guard_index = compact.index("if(typeofwindow.moveCursor")

    assert f_branch_index < fit_guard_index < fit_call_index
    assert fit_call_index < depth_index
    assert fit_call_index < move_cursor_guard_index


def test_dotpad_text_scroll_chords_run_before_cursor_movement_requirements():
    source = _source(DOTPAD_JS)

    left_scroll_index = source.index("if (byte6 === 0x07)")
    right_scroll_index = source.index("if (byte6 === 0x38)")
    move_cursor_guard_index = source.index("if (typeof window.moveCursor")

    assert left_scroll_index < move_cursor_guard_index
    assert right_scroll_index < move_cursor_guard_index


def test_dot_123_scrolls_braille_text_left():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x07")
    compact = _compact(body)

    assert "scrollTactileText(-1);" in compact
    assert "return;" in compact


def test_dot_456_scrolls_braille_text_right():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x38")
    compact = _compact(body)

    assert "scrollTactileText(1);" in compact
    assert "return;" in compact
