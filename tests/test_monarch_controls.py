"""Tests for the Monarch control handler.

These used to assert that particular literals appeared in `monarch-hid.js`, which
had three problems (#116). Reformatting broke them without any behaviour change.
A genuinely wrong mapping still passed, because each assertion was copied from
the same literal it was checking, so the obvious response to a failure was to
update the literal. And a negative assertion matched an identifier appearing in a
comment rather than in code.

There is no JS runtime in the repo, so instead of executing the handler these
parse its command map and check properties of the parsed data: that the movement
commands form the four unit directions, that depth commands are equal and
opposite, that no report key is defined twice. Those hold regardless of
formatting, and a structural mistake cannot be papered over by editing a string
in the test.

The limit is worth stating: which physical key produces which report id can only
be confirmed against the hardware or its documentation. Nothing in this
repository can tell you that a given report id really is the left button, so
these do not claim to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MONARCH_JS = ROOT / "static" / "js" / "monarch-hid.js"


def _source() -> str:
    return MONARCH_JS.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def _code_only(source: str) -> str:
    """Source with comments and string literals removed.

    A negative assertion over raw source matches an identifier that appears only
    in an explanatory comment, which is how removing a line in #114 failed a test
    it should have passed.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"(?m)//.*$", "", source)
    return re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", "''", source)


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
    # The single shared slot is what coupled the two devices together. Checked
    # against code with comments stripped, so a mention in prose cannot keep
    # this passing after the slot itself is gone.
    assert "connectedTactileDisplay" not in _compact(_code_only(_source()))


def _command_map() -> dict[str, dict]:
    """Parse MONARCH_COMMANDS into {report_key: {field: value}}."""
    source = _source()
    block = re.search(r"MONARCH_COMMANDS\s*=\s*\{(.*?)\n\s*\};", source, re.DOTALL)
    assert block, "MONARCH_COMMANDS not found"

    commands: dict[str, dict] = {}
    for raw_key, body in re.findall(r"'([^']+)'\s*:\s*\{([^}]*)\}", block.group(1)):
        entry: dict = {}
        for field, value in re.findall(r"(\w+)\s*:\s*('[^']*'|-?\d+)", body):
            entry[field] = ast.literal_eval(value) if value.startswith("'") else int(value)
        assert raw_key not in commands, f"report key {raw_key} is defined twice"
        commands[raw_key] = entry

    assert commands, "no commands parsed"
    return commands


def _by_type(command_type: str) -> list[dict]:
    return [c for c in _command_map().values() if c.get("type") == command_type]


# --- Structure ------------------------------------------------------------


def test_every_command_has_a_known_type():
    known = {"move", "depth", "cycle-cursor"}
    for key, command in _command_map().items():
        assert command.get("type") in known, f"{key} has unknown type {command.get('type')!r}"


def test_report_keys_are_well_formed():
    """`reportId:byte,byte,byte`. A malformed key silently never matches."""
    for key in _command_map():
        assert re.fullmatch(r"\d+:\d+,\d+,\d+", key), f"malformed report key {key!r}"


# --- Movement -------------------------------------------------------------


def test_movement_commands_are_the_four_unit_directions():
    """Exactly one command per direction, each a unit step along one axis.

    Catches what a literal comparison could not: a duplicated vector, a diagonal,
    or a step of the wrong size.
    """
    vectors = sorted((c["dCol"], c["dRow"]) for c in _by_type("move"))
    assert vectors == [(-1, 0), (0, -1), (0, 1), (1, 0)], (
        f"movement commands are not the four unit directions: {vectors}"
    )


def test_movement_directions_come_in_opposing_pairs():
    vectors = {(c["dCol"], c["dRow"]) for c in _by_type("move")}
    for col, row in vectors:
        assert (-col, -row) in vectors, (
            f"({col}, {row}) has no opposite; one direction is unreachable"
        )


def test_no_movement_command_is_a_no_op():
    for command in _by_type("move"):
        assert (command["dCol"], command["dRow"]) != (0, 0), "a key mapped to no movement at all"


# --- Depth ----------------------------------------------------------------


def test_depth_commands_are_equal_and_opposite():
    deltas = sorted(c["delta"] for c in _by_type("depth"))
    assert len(deltas) == 2, f"expected one shallower and one deeper, got {deltas}"
    assert deltas[0] == -deltas[1], f"depth steps are not symmetric: {deltas}"
    assert deltas[1] > 0


# --- Cursor ---------------------------------------------------------------


def test_exactly_one_cursor_cycle_command():
    assert len(_by_type("cycle-cursor")) == 1


# --- Wiring ---------------------------------------------------------------
#
# These stay source-based. They are about whether one function is called from
# another, which cannot be observed by parsing data, and asserting on a call site
# is not the failure mode #116 describes.


def test_input_reports_dispatch_to_the_command_handler():
    source = _source()
    assert "addEventListener('inputreport'" in source
    assert "monarchReportKey(" in source
    assert "MONARCH_COMMANDS[" in source
    assert "handleMonarchCommand(" in source


def test_report_key_honours_the_dataview_window():
    """Reading the whole underlying buffer would key off the wrong bytes whenever
    the view is a window onto a larger one, and every control would stop matching."""
    assert "newUint8Array(data.buffer,data.byteOffset,data.byteLength)" in _compact(_source())


def test_monarch_does_not_claim_the_tactile_display_dimensions():
    """Claiming them costs a full extra render per interaction and would wipe the
    entry belonging to a DotPad connected at the same time."""
    assert "connectedTactileDisplay=" not in _compact(_source())


def test_depth_and_cursor_go_through_the_viewer_helpers():
    source = _source()
    for helper in (
        "window.getCurrentSliceDepth?.()",
        "window.updateSliceDepth?.(",
        "window.announceDepthValue?.(",
        "window.whichCursor?.()",
        "window.moveCursor?.(",
        "window.cycleCursorState?.(",
    ):
        assert helper in source, f"{helper} is not called"


def test_handler_does_not_reach_into_viewer_private_state():
    """Checked against code with comments and strings stripped, so naming the
    variable in an explanatory comment does not fail the test."""
    assert "currentSliceDepth" not in _code_only(_source())


# --- The parsing these rely on --------------------------------------------


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("// currentSliceDepth explained here\nlet x = 1;", False),
        ("let y = currentSliceDepth;", True),
        ("/* currentSliceDepth */\nlet z = 2;", False),
    ],
)
def test_code_only_ignores_comments(snippet, expected):
    """If the stripper is wrong, the negative assertions above prove nothing."""
    assert ("currentSliceDepth" in _code_only(snippet)) is expected
