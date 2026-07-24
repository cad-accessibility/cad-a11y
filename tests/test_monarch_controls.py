from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONARCH_JS = ROOT / "static" / "js" / "monarch-hid.js"


def _source() -> str:
    return MONARCH_JS.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def test_monarch_sets_and_clears_tactile_display_dimensions():
    source = _source()

    assert "window.connectedTactileDisplay = {" in source
    assert "type: 'Monarch'" in source
    assert "connection: 'hid'" in source
    assert "pixelWidth: 96" in source
    assert "pixelHeight: 40" in source
    assert "window.connectedTactileDisplay = null;" in source


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