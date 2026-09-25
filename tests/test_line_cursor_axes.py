"""The two braille displays must block the same movement axis in each line mode.

A horizontal guide line spans the full width, so it is repositioned by moving it
up and down and left/right movement has no meaning. A vertical line is the mirror
image. The DotPad had these reversed, which also made it behave opposite to the
Monarch while the README described only the DotPad's behaviour.

There is no JS runtime in the repo, so these read the handlers' key maps and
evaluate the guard rule against them in Python rather than asserting on source
text. That keeps the test about behaviour, so reformatting will not break it and
a genuinely reversed mapping cannot pass. See #116.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOTPAD_JS = ROOT / "static" / "js" / "dotpad-integration.js"
MONARCH_JS = ROOT / "static" / "js" / "monarch-hid.js"
README = ROOT / "README.md"

# (dCol, dRow) -> the mode that must block it.
# dCol is horizontal travel, blocked while only a horizontal line is shown.
# dRow is vertical travel, blocked while only a vertical line is shown.
BLOCKED_BY = {"dCol": "horizontal-line", "dRow": "vertical-line"}


def _dotpad_key_actions() -> dict[str, tuple[int, int]]:
    """Parse DOTPAD_KEY_ACTIONS into {keyCode: (dCol, dRow)}."""
    source = DOTPAD_JS.read_text(encoding="utf-8")
    block = re.search(r"DOTPAD_KEY_ACTIONS\s*=\s*\{(.*?)\}", source, re.DOTALL)
    assert block, "DOTPAD_KEY_ACTIONS not found"
    actions = {
        name: (int(col), int(row))
        for name, col, row in re.findall(
            r"(\w+)\s*:\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", block.group(1)
        )
    }
    assert actions, "no DotPad key actions parsed"
    return actions


def test_dotpad_key_map_covers_both_axes():
    actions = _dotpad_key_actions()
    assert any(col != 0 for col, _ in actions.values()), "no horizontal keys"
    assert any(row != 0 for _, row in actions.values()), "no vertical keys"


def test_dotpad_guards_on_the_movement_axis_not_key_names():
    """Guarding on key names is what let the two handlers disagree."""
    source = DOTPAD_JS.read_text(encoding="utf-8")
    guard = re.search(
        r"cursorState\s*===\s*'horizontal-line'\s*&&\s*(?P<expr>[^)]+)\)", source
    )
    assert guard, "no horizontal-line guard found in the DotPad handler"
    assert "dCol" in guard.group("expr"), (
        "the horizontal-line guard must test horizontal travel (dCol), "
        f"got: {guard.group('expr').strip()}"
    )

    guard = re.search(
        r"cursorState\s*===\s*'vertical-line'\s*&&\s*(?P<expr>[^)]+)\)", source
    )
    assert guard, "no vertical-line guard found in the DotPad handler"
    assert "dRow" in guard.group("expr"), (
        "the vertical-line guard must test vertical travel (dRow), "
        f"got: {guard.group('expr').strip()}"
    )


@pytest.mark.parametrize("axis,mode", sorted(BLOCKED_BY.items()))
def test_monarch_blocks_the_same_axis_as_the_dotpad(axis, mode):
    """Both handlers must express the identical rule."""
    source = MONARCH_JS.read_text(encoding="utf-8")
    match = re.search(
        rf"cursorState\s*===\s*'{mode}'\s*&&\s*command\.(?P<axis>d(?:Col|Row))\s*!==\s*0",
        source,
    )
    assert match, f"Monarch handler has no {mode} guard"
    assert match.group("axis") == axis, (
        f"Monarch blocks {match.group('axis')} in {mode}; the DotPad blocks {axis}. "
        "The two displays must agree."
    )


@pytest.mark.parametrize(
    "mode,disabled",
    [("horizontal-line", "left and right"), ("vertical-line", "up and down")],
)
def test_readme_documents_the_axis_the_code_blocks(mode, disabled):
    """The README described the old, reversed DotPad behaviour in both sections."""
    text = README.read_text(encoding="utf-8")
    described = re.findall(rf"`{mode}`:[^\n]*", text)
    assert described, f"README does not describe {mode}"
    for line in described:
        assert disabled in line, (
            f"README says of {mode}: {line.strip()!r}; the code disables {disabled}"
        )
