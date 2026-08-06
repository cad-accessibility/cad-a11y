"""Guardrails for sending viewer announcements to the DotPad text cells.

"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWER_JS = REPO_ROOT / "static" / "js" / "viewer.js"
DOTPAD_JS = REPO_ROOT / "static" / "js" / "dotpad-integration.js"


def _dotpad_source() -> str:
    return DOTPAD_JS.read_text(encoding="utf-8")


def _viewer_source() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _nabcc_table() -> list[int]:
    source = _dotpad_source()
    match = re.search(
        r"const\s+NABCC\s*=\s*new\s+Uint8Array\(\s*\[(.*?)\]\s*\)",
        source,
        re.DOTALL,
    )
    assert match, "could not find const NABCC = new Uint8Array([...])"

    values = []
    for line in match.group(1).splitlines():
        line = line.split("//", 1)[0].strip().rstrip(",")
        if line:
            values.append(int(line, 16))
    return values


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{(?P<body>.*?)\n\}}",
        source,
        re.DOTALL,
    )
    assert match is not None, f"could not find function {name}()"
    return match.group("body")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def _encode_like_dotpad(message: str, cell_count: int) -> str:
    """Python mirror of encodeAnnouncementForDotPad()."""
    nabcc = _nabcc_table()
    hex_cells = []

    for i in range(cell_count):
        ch = message[i] if i < len(message) else " "
        code = ord(ch)
        byte = nabcc[code - 0x20] if 0x20 <= code <= 0x7E else 0x00
        hex_cells.append(f"{byte:02X}")

    return "".join(hex_cells)


def _wrap_like_dotpad(message: str, cell_count: int) -> list[str]:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    pages = []
    remaining = text

    while remaining:
        if len(remaining) <= cell_count:
            pages.append(remaining)
            break

        candidate = remaining[:cell_count]
        next_char = remaining[cell_count : cell_count + 1]

        if next_char.isspace():
            pages.append(candidate)
            remaining = remaining[cell_count:].lstrip()
            continue

        break_index = candidate.rfind(" ")
        if break_index > 0:
            pages.append(remaining[:break_index])
            remaining = remaining[break_index:].lstrip()
        else:
            pages.append(remaining[:cell_count])
            remaining = remaining[cell_count:].lstrip()

    return pages or [""]


def test_nabcc_table_covers_printable_ascii():
    table = _nabcc_table()

    assert len(table) == 95
    assert table[ord(" ") - 0x20] == 0x00
    assert table[ord("a") - 0x20] == 0x01

    # TODO: fill these in from your NABCC table.
    assert table[ord("b") - 0x20] == 0x03
    assert table[ord("c") - 0x20] == 0x09


def test_short_announcements_are_padded_with_blank_cells():
    # "abc" plus two blank cells.
    assert _encode_like_dotpad("abc", 5) == "0103090000"


def test_long_announcements_are_truncated_to_cell_count():
    # Only the first three characters should fit.
    assert _encode_like_dotpad("abcdef", 3) == "010309"


def test_non_ascii_characters_become_blank_cells():
    # The accented character is outside printable ASCII, so it should encode as 00.
    assert _encode_like_dotpad("aéb", 3) == "010003"


def test_dotpad_announcement_hook_is_exported_for_viewer():
    source = _dotpad_source()

    assert "window.onTactileAnnouncement = sendAnnouncementToDotPad" in source
    assert "sdk.displayTextData" in source
    assert "DisplayMode.TextMode" in source


def test_viewer_forwards_normalized_announcements_to_tactile_hook():
    source = _viewer_source()

    assert "typeof window.onTactileAnnouncement === 'function'" in source
    assert "window.onTactileAnnouncement({" in source
    assert "message: normalizedMessage" in source
    assert "politeness" in source


def test_dotpad_text_cell_count_uses_connected_device_with_fallback():
    source = _dotpad_source()
    body = _function_body(source, "getDotPadTextCellCount")
    compact = _compact(body)

    assert "connectedDevice?.numberBrailleCellColumns||20" in compact


def test_new_announcement_resets_scroll_offset_and_renders_first_window():
    source = _dotpad_source()
    body = _function_body(source, "setTactileTextMessage")
    compact = _compact(body)

    assert "constcellCount=getDotPadTextCellCount();" in compact
    assert "tactileTextPages=buildTactileTextPages(message,cellCount);" in compact
    assert "tactileTextPageIndex=0;" in compact
    assert "renderTactileTextWindow();" in compact


def test_render_tactile_text_window_uses_current_page_and_cell_count():
    source = _dotpad_source()
    body = _function_body(source, "renderTactileTextWindow")
    compact = _compact(body)

    assert "if(!connectedDevice)return;" in compact
    assert "constcellCount=getDotPadTextCellCount();" in compact
    assert "constvisibleText=tactileTextPages[tactileTextPageIndex]||'';" in compact
    assert "encodeAnnouncementForDotPad(visibleText,cellCount)" in compact
    assert "sdk.displayTextData(textHex,connectedDevice,DisplayMode.TextMode);" in compact


def test_tactile_text_pages_are_built_from_normalized_whitespace():
    source = _dotpad_source()
    body = _function_body(source, "buildTactileTextPages")

    assert "function buildTactileTextPages(message, cellCount)" in source
    assert "replace(/\\s+/g, ' ')" in source
    assert ".trim()" in source
    assert "const nextChar = remaining.slice(cellCount, cellCount + 1);" in body
    assert "candidate.lastIndexOf(' ')" in body
    assert "candidate.search(/\\s+\\S*$/)" not in body


def test_dotpad_text_scroll_does_not_keep_stale_character_offset_state():
    source = _dotpad_source()

    assert "let tactileTextOffset" not in source
    assert "function getTactileTextMaxOffset" not in source


@pytest.mark.parametrize(
    ("message", "cell_count", "expected"),
    [
        ("short message", 20, ["short message"]),
        ("alpha beta gamma delta", 20, ["alpha beta gamma", "delta"]),
        ("alpha beta gamma delta", 10, ["alpha beta", "gamma", "delta"]),
        ("alpha    beta\tgamma", 20, ["alpha beta gamma"]),
        ("one two three", 7, ["one two", "three"]),
        ("averyveryveryverylongfilename.stl", 20, ["averyveryveryverylon", "gfilename.stl"]),
        ("word exactlytwentychars", 20, ["word", "exactlytwentychars"]),
    ],
)
def test_tactile_text_pages_break_on_whitespace_unless_word_is_too_long(
    message: str, cell_count: int, expected: list[str]
):
    assert _wrap_like_dotpad(message, cell_count) == expected


@pytest.mark.parametrize(
    ("page_count", "start_index", "delta", "expected_index"),
    [
        (0, 0, 1, 0),
        (1, 0, 1, 0),
        (2, 0, -1, 0),
        (2, 0, 1, 1),
        (2, 1, 1, 1),
        (3, 1, -1, 0),
        (3, 1, 1, 2),
        (3, 2, 1, 2),
    ],
)
def test_tactile_text_page_scroll_clamps_to_available_pages(
    page_count: int, start_index: int, delta: int, expected_index: int
):
    if page_count == 0:
        actual = 0
    else:
        max_page_index = max(0, page_count - 1)
        actual = max(0, min(max_page_index, start_index + delta))

    assert actual == expected_index


def test_scroll_tactile_text_clamps_page_index_before_rendering():
    source = _dotpad_source()
    body = _function_body(source, "scrollTactileText")
    compact = _compact(body)

    assert "if(!tactileTextPages.length)return;" in compact
    assert "constmaxPageIndex=Math.max(0,tactileTextPages.length-1);" in compact
    assert "Math.min(maxPageIndex,tactileTextPageIndex+deltaPages)" in compact
    assert "renderTactileTextWindow();" in compact


def test_announcement_hook_saves_scrollable_text_message():
    source = _dotpad_source()
    body = _function_body(source, "sendAnnouncementToDotPad")
    compact = _compact(body)

    assert "if(!connectedDevice)return;" in compact
    assert "setTactileTextMessage(message);" in compact
