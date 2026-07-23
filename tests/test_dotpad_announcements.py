"""Guardrails for sending viewer announcements to the DotPad text cells.

"""

from __future__ import annotations

import re
from pathlib import Path

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
    assert "isAlert" in source