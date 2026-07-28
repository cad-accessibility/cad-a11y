from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONARCH_JS = ROOT / "static" / "js" / "monarch-hid.js"


def _source() -> str:
    return MONARCH_JS.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def test_monarch_registers_its_grid_under_its_own_key():
    """Monarch reports its dimensions, into a registry keyed per device.

    It used to withhold them, for two reasons that no longer hold. Claiming them
    was said to cost an extra render, but the opposite is true: naming a size is
    what routes the request down the single-render path. And clearing them on
    disconnect would wipe a co-connected DotPad's entry, which was a consequence
    of one shared global rather than of the Monarch reporting anything.

    A Monarch is 48 cells by 10 lines and a braille cell is 2x4 pixels, so the
    grid it reports is exactly the 96x40 default.
    """
    compact = _compact(_source())
    assert "setTactileDisplay?.('monarch_hid'" in compact
    assert "pixelWidth:96" in compact
    assert "pixelHeight:40" in compact
    # The single shared slot is what coupled the two devices together.
    assert "connectedTactileDisplay" not in compact


def test_monarch_report_key_honours_the_dataview_window():
    """The report key must respect the DataView's offset and length.

    Reading the whole underlying buffer would key off the wrong bytes whenever the
    view is a window onto a larger one, and every mapped control would silently
    stop matching.
    """
    assert "newUint8Array(data.buffer,data.byteOffset,data.byteLength)" in _compact(_source())


def test_monarch_input_reports_dispatch_to_command_handler():
    source = _source()

    assert "addEventListener('inputreport'" in source
    assert "const key = monarchReportKey(e.reportId, e.data);" in source
    assert "const command = MONARCH_COMMANDS[key];" in source
    assert "handleMonarchCommand(command);" in source


def test_monarch_command_map_contains_workshop_controls():
    source = _source()

    assert "'32:0,32,0': { type: 'move', dCol: -1, dRow: 0 }" in source
    assert "'32:0,64,0': { type: 'move', dCol: 1, dRow: 0 }" in source
    assert "'32:0,8,0': { type: 'move', dCol: 0, dRow: -1 }" in source
    assert "'32:0,16,0': { type: 'move', dCol: 0, dRow: 1 }" in source
    assert "'32:1,0,0': { type: 'depth', delta: -10 }" in source
    assert "'32:8,0,0': { type: 'depth', delta: 10 }" in source
    assert "'32:0,1,0': { type: 'cycle-cursor' }" in source


def test_monarch_depth_uses_viewer_helpers_not_private_state():
    source = _source()
    compact = _compact(source)

    assert "window.getCurrentSliceDepth?.()" in source
    assert "window.updateSliceDepth?.(nextDepth,false);" in compact
    assert "window.announceDepthValue?.(nextDepth,previousDepth);" in compact
    assert "currentSliceDepth" not in source


def test_monarch_cursor_uses_viewer_cursor_helpers():
    source = _source()

    assert "window.whichCursor?.()" in source
    assert "window.moveCursor?.(command.dCol, command.dRow);" in source
    assert "window.cycleCursorState?.();" in source