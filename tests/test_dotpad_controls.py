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


def test_viewer_exposes_depth_helpers():
    source = _source(VIEWER_JS)

    assert "window.getCurrentSliceDepth = getCurrentSliceDepth;" in source
    assert "window.updateSliceDepth = updateSliceDepth;" in source
    assert "window.announceDepthShortcut = announceDepthShortcut;" in source


def test_dotpad_depth_controls_run_before_cursor_movement_requirements():
    source = _source(DOTPAD_JS)

    dot_1_index = source.index("if (byte6 === 0x01)")
    dot_4_index = source.index("if (byte6 === 0x08)")
    move_cursor_guard_index = source.index("if (typeof window.moveCursor")

    assert dot_1_index < move_cursor_guard_index
    assert dot_4_index < move_cursor_guard_index


def test_dot_1_goes_shallower():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x01")
    compact = _compact(body)

    assert "constpreviousDepth=window.getCurrentSliceDepth();" in compact
    assert "currentSliceDepth" not in body
    assert "Math.max(0," in body
    assert "-100/n" in compact or "-10" in compact
    assert "window.updateSliceDepth(nextDepth,false);" in compact
    assert "window.announceDepthShortcut('Dot1',previousDepth,nextDepth);" in compact


def test_dot_4_goes_deeper():
    source = _source(DOTPAD_JS)
    body = _byte6_branch(source, "0x08")
    compact = _compact(body)

    assert "constpreviousDepth=window.getCurrentSliceDepth();" in compact
    assert "currentSliceDepth" not in body
    assert "Math.min(100," in body
    assert "+100/n" in compact or "+10" in compact
    assert "window.updateSliceDepth(nextDepth,false);" in compact
    assert "window.announceDepthShortcut('Dot4',previousDepth,nextDepth);" in compact