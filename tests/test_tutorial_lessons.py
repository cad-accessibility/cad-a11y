"""The first-run tutorial's lessons (app/tutorial_lessons.py), held against the
viewer they teach.

A tutorial that names the wrong key, or checks for a state the viewer never
reaches, is worse than none: the person it is for cannot see that the lesson is
wrong and will assume they are. So these check the lessons against the code
rather than against themselves:

- every key a lesson names is looked up in the viewer's keydown handler, and the
  case it lands in has to do what the key table says it does;
- every Monarch and DotPad button name is looked up in monarch-hid.js and
  dotpad-integration.js the same way;
- every viewer shortcut, main-menu button, dialog, Settings control and device
  control has to be taught by a lesson, so a new shortcut with no lesson fails
  here rather than going untaught;
- every check type a lesson uses has to be one the runner implements.

There is no JS runtime in the repo, so the JavaScript is parsed, the way
test_axis_mode.py and test_monarch_controls.py do it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from app import study_protocol
from app import tutorial_lessons as tl

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
VIEWER_HTML = ROOT / "accessible-3d-viewer.html"
MONARCH_JS = ROOT / "static" / "js" / "monarch-hid.js"
DOTPAD_JS = ROOT / "static" / "js" / "dotpad-integration.js"
TUTORIAL_JS = ROOT / "static" / "js" / "tutorial.js"
LESSONS_PY = ROOT / "app" / "tutorial_lessons.py"

# The lesson list, in order (tutorial contract, section 8, as changed by the
# #245 review): no cursor lesson while the cursor has no use, and the slice
# graph, the cube and the slider last, as extras after the end of the tutorial.
LESSON_IDS = [
    "before_you_start", "connect", "test_pattern", "meet_the_mug", "depth", "render_modes",
    "zoom_and_move", "axes", "reset_and_fit", "help_and_settings", "mug_detective",
    "your_own_model", "layout_and_graph", "cube", "slider",
]

# The contract's check types (section 4), and the parameters each takes.
CHECK_PARAMS: dict[str, tuple[set[str], set[str]]] = {
    "manual": (set(), set()),
    "answer": ({"correct"}, set()),
    "key": ({"key"}, set()),
    "keys": ({"keys"}, set()),
    "state": ({"field"}, {"equals", "lte", "gte", "tolerance"}),
    "changed": ({"field"}, set()),
    "cycle": ({"field", "values", "end"}, set()),
    "sweep": ({"axis", "from", "to"}, set()),
    "edge": (set(), set()),
    "in_band": ({"axis", "band"}, set()),
    "mark_band": ({"axis", "band"}, set()),
    "device": ({"device"}, set()),
    "ui": ({"name", "action"}, set()),
    "settings_unchanged": (set(), set()),
    "test_pattern": ({"rounds"}, set()),
    "cursor_in": ({"landmark"}, set()),
    "centred": ({"landmark"}, set()),
    "zoom_by": ({"min_increase"}, set()),
    "model_changed": (set(), set()),
    "key_help": ({"presses"}, set()),
    "slicegraph_ready": (set(), set()),
    "all": ({"checks"}, set()),
}

# What cadStudy.getState() returns (contract section 5); a check may read no other.
STATE_FIELDS = {
    "view", "depth", "render_mode", "layout_mode", "zoom", "orientation", "cursor_state",
    "output_device", "axis_mode", "cut_axis", "cut_side", "cut_percent", "model_label", "model",
    "cursor_col", "cursor_row", "compose_scrollbar", "compose_slicegraph", "slicegraph_locked",
    "display_connected", "monarch_connected", "dotpad_connected", "single_key_shortcuts",
    "last_render_state",
}

# The types whose wrong moves the runner can speak about (contract section 4).
ON_FAIL_TYPES = {"sweep", "edge", "mark_band", "answer", "key", "keys"}

STEP_FIELDS = {
    "id", "text", "sr", "braille", "check", "done", "hints", "on_fail", "answers", "narrate",
    "demo", "key_only", "when", "store", "requires",
}
LESSON_FIELDS = {"id", "part", "title", "minutes", "requires", "pose", "lock", "steps"}
POSE_FIELDS = {
    "model", "view", "axis_mode", "depth", "render_mode", "representation_mode",
    "compose_scrollbar", "zoom", "reset_pan",
}

# The key names the contract requires (section 2).
REQUIRED_KEY_NAMES = {
    "depth_deeper_10", "depth_shallower_10", "depth_deeper_1", "depth_shallower_1", "depth_near",
    "depth_far", "axis_x", "axis_y", "axis_z", "where_am_i", "origin", "reset", "render_mode",
    "layout", "fit", "zoom_in_10", "zoom_out_10", "zoom_in_1", "zoom_out_1", "pan_up", "pan_left",
    "pan_down", "pan_right", "scrollbar_overlay", "graph_overlay", "graph_refresh", "graph_lock",
    "shortcuts", "print", "cursor_mode", "cursor_move", "turn_pitch_up", "turn_pitch_down",
    "turn_yaw_left", "turn_yaw_right", "turn_roll_ccw", "turn_roll_cw", "tutorial_continue",
    "tutorial_back", "tutorial_repeat",
}

# The command names the runner's captureDeviceKey hook reports (contract section 5).
CAPTURE_NAMES = {
    "dot1", "dot4", "cursor", "axis-x", "axis-y", "axis-z", "move-left", "move-right", "move-up",
    "move-down", "other",
}

# Handled by tutorial.js rather than the viewer.
TUTORIAL_KEY_NAMES = {"tutorial_continue", "tutorial_back", "tutorial_repeat"}

# Words that assume sight. "Seen from" is allowed: it is the viewer's own phrase
# for which side a view is from, and a lesson has to quote what people hear.
VISUAL_WORDS = re.compile(
    r"\b(see|sees|seeing|saw|look|looks|looked|looking|click|clicks|clicked|clicking|watch|"
    r"watches|watching|glance|glances|visible|invisible|highlighted)\b",
    re.IGNORECASE,
)
SEEN_NOT_FROM = re.compile(r"\bseen\b(?!\s+from)", re.IGNORECASE)
DASHES = ("\u2014", "\u2013", " -- ")

KEY_PLACEHOLDER = re.compile(r"\{key:([a-z0-9_]+)\}")
ANY_BRACES = re.compile(r"\{([^{}]*)\}")


# ---------------------------------------------------------------------------
# Walking the lessons
# ---------------------------------------------------------------------------


def _steps() -> Iterator[tuple[dict, dict]]:
    for lesson in tl.LESSONS:
        for step in lesson["steps"]:
            yield lesson, step


def _checks(check: dict) -> Iterator[dict]:
    """A check and, for "all", every check inside it."""
    yield check
    for sub in check.get("checks", []) if check.get("type") == "all" else []:
        yield from _checks(sub)


def _step_strings(step: dict) -> Iterator[tuple[str, str]]:
    """Every string a person reads or hears from a step, with where it came from."""
    for field in ("text", "sr", "braille", "done", "on_fail"):
        if step[field] is not None:
            yield field, step[field]
    for i, hint in enumerate(step["hints"]):
        yield f"hints[{i}]", hint
    for answer in step["answers"] or []:
        yield "answers.label", answer["label"]
    for value, line in (step["narrate"] or {}).items():
        yield f"narrate[{value}]", line
    for i, frame in enumerate((step["demo"] or {}).get("frames", [])):
        yield f"demo.frames[{i}].say", frame["say"]


def _lesson_strings(lesson: dict) -> Iterator[tuple[str, str]]:
    yield "title", lesson["title"]
    for step in lesson["steps"]:
        for where, text in _step_strings(step):
            yield f"{step['id']}.{where}", text


def _all_user_strings() -> Iterator[tuple[str, str]]:
    for lesson in tl.LESSONS:
        for where, text in _lesson_strings(lesson):
            yield f"{lesson['id']}.{where}", text
    for name, entry in tl.KEYS.items():
        for field in ("keyboard", "monarch", "dotpad", "does"):
            if entry[field]:
                yield f"KEYS.{name}.{field}", entry[field]
    yield "UNMAPPED_DEVICE_BUTTON", tl.UNMAPPED_DEVICE_BUTTON


def _key_names_used(lesson: dict) -> set[str]:
    """Every KEYS name a lesson teaches: named in its words, or waited for by a
    check."""
    names = set()
    for _, text in _lesson_strings(lesson):
        names.update(KEY_PLACEHOLDER.findall(text))
    codes_to_names = {entry["code"]: name for name, entry in tl.KEYS.items() if entry["code"]}
    for step in lesson["steps"]:
        for check in _checks(step["check"]):
            if check["type"] == "key":
                names.add(codes_to_names[check["key"]])
            if check["type"] == "keys":
                names.update(codes_to_names[code] for code in check["keys"])
    return names


def _lesson(lesson_id: str) -> dict:
    return next(lesson for lesson in tl.LESSONS if lesson["id"] == lesson_id)


# ---------------------------------------------------------------------------
# Reading the viewer
# ---------------------------------------------------------------------------


def _viewer_js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$|\s//.*$", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def _keydown_handler() -> str:
    source = _viewer_js()
    start = source.index("document.addEventListener('keydown', function(e) {")
    end = source.index("\n});", start)
    return source[start:end]


def _supported_shortcuts() -> set[str]:
    block = re.search(r"const supportedShortcuts = new Set\(\[(.*?)\]\);", _keydown_handler(), re.DOTALL)
    assert block, "supportedShortcuts not found in the keydown handler"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _switch_cases() -> dict[str, str]:
    """{case label: body} for the keydown handler's switch. Labels that fall
    through to the next share its body."""
    handler = _keydown_handler()
    match = re.search(r"switch\s*\(\s*normalizedKey\s*\)\s*\{", handler)
    assert match, "the keydown switch on normalizedKey not found"
    body = handler[match.end():]
    parts = re.split(r"\bcase\s+'((?:[^'\\]|\\.)+)'\s*:", body)
    cases: dict[str, str] = {}
    pending: list[str] = []
    for label, chunk in zip(parts[1::2], parts[2::2]):
        pending.append(label)
        chunk = chunk.split("default:")[0]
        if _strip_comments(chunk).strip():
            for waiting in pending:
                cases[waiting] = _strip_comments(chunk)
            pending = []
    return cases


def _mode_keys(array_name: str) -> list[str]:
    block = re.search(rf"const {array_name} = \[(.*?)\];", _viewer_js(), re.DOTALL)
    assert block, f"{array_name} not found"
    return re.findall(r"key:\s*'([^']+)'", block.group(1))


def _monarch_commands() -> dict[str, dict]:
    """MONARCH_COMMANDS as {report key: {field: value}}, as in test_monarch_controls."""
    source = MONARCH_JS.read_text(encoding="utf-8")
    block = re.search(r"MONARCH_COMMANDS\s*=\s*\{(.*?)\n\s*\};", source, re.DOTALL)
    assert block, "MONARCH_COMMANDS not found"
    commands: dict[str, dict] = {}
    for raw_key, body in re.findall(r"'([^']+)'\s*:\s*\{([^}]*)\}", block.group(1)):
        entry: dict = {}
        for field, value in re.findall(r"(\w+)\s*:\s*('[^']*'|-?\d+)", body):
            entry[field] = value.strip("'") if value.startswith("'") else int(value)
        commands[raw_key] = entry
    assert commands, "no Monarch commands parsed"
    return commands


def _dotpad_js() -> str:
    return DOTPAD_JS.read_text(encoding="utf-8")


def _dotpad_on_key() -> str:
    source = _dotpad_js()
    start = source.index("function onKey(")
    end = source.index("\n}\n", start)
    return source[start:end]


def _dotpad_letters() -> dict[int, str]:
    """The DotPad's braille byte for each lowercase letter, from its NABCC table,
    checked against the dots each table line names."""
    letters: dict[int, str] = {}
    for byte, letter, dots in re.findall(
        r"0x([0-9A-Fa-f]{2}),\s*//\s*0x[0-9A-Fa-f]{2}\s+([a-z])\s+dots?\s+([\d,]+)", _dotpad_js()
    ):
        value = int(byte, 16)
        assert value == _dot_bits(dots.replace(",", " ")), f"NABCC line for {letter} disagrees"
        letters[value] = letter
    assert len(letters) == 26, "NABCC letters not parsed"
    return letters


def _dotpad_key_actions() -> dict[str, tuple[int, int]]:
    block = re.search(r"const DOTPAD_KEY_ACTIONS = \{(.*?)\};", _dotpad_js(), re.DOTALL)
    assert block, "DOTPAD_KEY_ACTIONS not found"
    return {
        name: (int(dx), int(dy))
        for name, dx, dy in re.findall(r"(\w+):\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", block.group(1))
    }


def _dot_bits(dots: str) -> int:
    """Braille dots as the byte both devices use: dot 1 is bit 0, dot 6 bit 5."""
    return sum(1 << (int(dot) - 1) for dot in dots.split())


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------


class _Elements(HTMLParser):
    """Every element, with the tags and ids of the elements around it."""

    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                      "source", "track", "wbr"})

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str | None]] = []
        self.elements: list[dict[str, Any]] = []

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, Any]:
        found = dict(attrs)
        entry = {
            "tag": tag,
            "id": found.get("id"),
            "ancestor_tags": {t for t, _ in self.stack},
            "ancestor_ids": {i for _, i in self.stack if i},
        }
        self.elements.append(entry)
        return entry

    def handle_starttag(self, tag, attrs):
        entry = self._record(tag, attrs)
        if tag not in self.VOID:
            self.stack.append((tag, entry["id"]))

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _page_elements() -> list[dict[str, Any]]:
    parser = _Elements()
    parser.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return parser.elements


CONTROL_TAGS = {"button", "input", "select", "textarea"}


def _controls_to_teach() -> dict[str, set[str]]:
    """The page's controls a person uses, by kind: main-menu buttons, dialogs,
    Settings controls, and the controls on the page itself (the device sections
    among them). The tutorial's own region and /study's are not taught; they are
    the tutorial and the study."""
    kinds: dict[str, set[str]] = {"menu": set(), "dialog": set(), "settings": set(), "page": set()}
    for element in _page_elements():
        if element["ancestor_ids"] & {"tutorial-region", "study-region"}:
            continue
        if element["tag"] == "dialog":
            assert element["id"], "a dialog without an id cannot be checked"
            kinds["dialog"].add(element["id"])
            continue
        if element["tag"] not in CONTROL_TAGS:
            continue
        if element["tag"] == "button" and "nav" in element["ancestor_tags"]:
            kinds["menu"].add(element["id"])
        elif "settings-dialog" in element["ancestor_ids"]:
            kinds["settings"].add(element["id"])
        elif "left-col" in element["ancestor_ids"] or element["id"] == "export-slice-svg-btn":
            kinds["page"].add(element["id"])
    for kind, ids in kinds.items():
        assert None not in ids, f"a {kind} control without an id cannot be checked"
    return kinds


# ---------------------------------------------------------------------------
# Reading the runner
# ---------------------------------------------------------------------------


def _registry_keys(source: str, name: str = "CHECKS") -> set[str]:
    """The property names of a JS object literal assigned to `name`, at its top
    level only, plus any `name.key = ...` assignments. A small scanner rather than
    a regex, so a nested object or a string holding a brace cannot add or hide a
    key."""
    keys: set[str] = set(re.findall(rf"\b{name}\.(\w+)\s*=(?!=)", source))
    keys |= set(re.findall(rf"\b{name}\[\s*['\"](\w+)['\"]\s*\]\s*=(?!=)", source))
    match = re.search(rf"\b{name}\s*=\s*(?:Object\.freeze\(\s*)?\{{", source)
    if not match:
        return keys
    i, depth, expecting = match.end(), 1, True
    while i < len(source) and depth > 0:
        if source.startswith("//", i):
            i = source.find("\n", i)
            i = len(source) if i == -1 else i
            continue
        if source.startswith("/*", i):
            i = source.find("*/", i) + 2
            continue
        char = source[i]
        if char in "'\"`":
            j = i + 1
            while j < len(source) and source[j] != char:
                j += 2 if source[j] == "\\" else 1
            if depth == 1 and expecting:
                after = source[j + 1:].lstrip()
                if after[:1] in (":", "("):
                    keys.add(source[i + 1:j])
                expecting = False
            i = j + 1
            continue
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif depth == 1 and char == ",":
            expecting = True
        elif depth == 1 and expecting and (char.isalpha() or char in "_$"):
            word = re.match(r"(?:async\s+)?([A-Za-z_$][\w$]*)\s*([:(,}]?)", source[i:])
            if word and word.group(2):
                keys.add(word.group(1))
            expecting = False
            i += word.end(1) if word else 1
            continue
        elif depth == 1 and not char.isspace():
            expecting = False
        i += 1
    return keys


# ---------------------------------------------------------------------------
# The shape of the data
# ---------------------------------------------------------------------------


def test_version_is_a_positive_integer():
    assert isinstance(tl.TUTORIAL_VERSION, int) and tl.TUTORIAL_VERSION >= 1


def test_fifteen_lessons_in_order_with_the_extras_last():
    assert [lesson["id"] for lesson in tl.LESSONS] == LESSON_IDS
    parts = [lesson["part"] for lesson in tl.LESSONS]
    assert parts == sorted(parts, key=tl.PARTS.index), "a lesson is out of its part's place"
    assert parts[-3:] == ["extras"] * 3


def test_the_module_lists_exactly_the_contract_check_types():
    assert set(tl.CHECK_TYPES) == set(CHECK_PARAMS)


@pytest.mark.parametrize("lesson", tl.LESSONS, ids=lambda lesson: lesson["id"])
def test_lesson_fields(lesson):
    assert set(lesson) == LESSON_FIELDS
    assert lesson["part"] in tl.PARTS
    assert isinstance(lesson["title"], str) and lesson["title"].strip()
    assert isinstance(lesson["minutes"], int) and lesson["minutes"] > 0
    assert isinstance(lesson["requires"], list)
    assert set(lesson["requires"]) <= set(tl.REQUIREMENTS)
    assert isinstance(lesson["lock"], bool)
    assert lesson["steps"], "a lesson needs at least one step"
    step_ids = [step["id"] for step in lesson["steps"]]
    assert len(step_ids) == len(set(step_ids)), "step ids repeat within the lesson"
    pose = lesson["pose"]
    if pose is not None:
        assert set(pose) == POSE_FIELDS
        assert pose["model"] == "tutorial_mug"
        assert pose["axis_mode"] in ("keep", "xyz", "turn")
        assert pose["view"] in ("x-", "x+", "y-", "y+", "z+", "z-")
        assert pose["render_mode"] in _mode_keys("renderModes")
        assert pose["representation_mode"] in _mode_keys("representationModes")


@pytest.mark.parametrize(
    "lesson_id,step", [(lesson["id"], step) for lesson, step in _steps()],
    ids=lambda value: value if isinstance(value, str) else value["id"],
)
def test_step_fields(lesson_id, step):
    assert set(step) == STEP_FIELDS
    assert isinstance(step["text"], str) and step["text"].strip()
    assert isinstance(step["done"], str) and step["done"].strip()
    assert isinstance(step["key_only"], bool)
    assert isinstance(step["requires"], list) and set(step["requires"]) <= set(tl.REQUIREMENTS)
    if step["sr"] is not None:
        # The spoken form exists to be shorter; a copy of the text is noise.
        assert step["sr"].strip() and step["sr"] != step["text"]
        assert len(step["sr"]) < len(step["text"])
    if step["on_fail"] is not None:
        assert step["on_fail"].strip()


def test_braille_fits_the_display_line_and_names_no_key():
    """The DotPad's text row is 20 cells, and braille is sent as written, so it
    cannot hold a placeholder."""
    for lesson, step in _steps():
        braille = step["braille"]
        where = f"{lesson['id']}.{step['id']}"
        assert isinstance(braille, str) and braille.strip(), where
        assert len(braille) <= 20, f"{where} braille is {len(braille)} cells: {braille!r}"
        assert "{" not in braille, f"{where} braille holds a placeholder"


def test_three_hints_each_more_specific_the_last_saying_what_to_do():
    """Hints come only on request, three at most, and the third must leave
    nothing to guess: it says what to press or choose."""
    imperative = re.compile(r"\b(press|choose|push|put|turn|stay|lay|plug)\b", re.IGNORECASE)
    for lesson, step in _steps():
        where = f"{lesson['id']}.{step['id']}"
        hints = step["hints"]
        assert isinstance(hints, list) and len(hints) == 3, where
        assert all(isinstance(h, str) and h.strip() for h in hints), where
        assert len(set(hints)) == 3, f"{where} repeats a hint"
        assert imperative.search(hints[2]), f"{where} hint 3 does not say what to do: {hints[2]!r}"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def test_every_check_is_a_contract_type_with_its_parameters():
    for lesson, step in _steps():
        for check in _checks(step["check"]):
            where = f"{lesson['id']}.{step['id']}"
            kind = check.get("type")
            assert kind in CHECK_PARAMS, f"{where} uses unknown check type {kind!r}"
            required, optional = CHECK_PARAMS[kind]
            params = set(check) - {"type"}
            assert required <= params, f"{where} {kind} is missing {required - params}"
            assert params <= required | optional, f"{where} {kind} has extra {params - required - optional}"
            if kind == "state":
                comparisons = params & {"equals", "lte", "gte"}
                assert len(comparisons) == 1, f"{where} state check needs one comparison"
            if kind == "all":
                assert len(check["checks"]) >= 2, f"{where} all() needs two or more checks"


def test_state_checks_read_fields_the_runner_exposes():
    render_modes = set(_mode_keys("renderModes"))
    layouts = set(_mode_keys("representationModes"))
    for lesson, step in _steps():
        for check in _checks(step["check"]):
            if check["type"] not in ("state", "changed", "cycle"):
                continue
            where = f"{lesson['id']}.{step['id']}"
            assert check["field"] in STATE_FIELDS, f"{where} reads unknown field {check['field']!r}"
            if check["type"] == "state" and check["field"] == "cut_axis":
                assert check["equals"] in ("x", "y", "z"), where
            if check["type"] == "state" and check["field"] == "layout_mode":
                assert check["equals"] in layouts, where
            if check["type"] == "cycle":
                known = {"render_mode": render_modes, "layout_mode": layouts,
                         "slicegraph_locked": {True, False}}[check["field"]]
                # A cycle has to go all the way round, or a mode is never met.
                assert set(check["values"]) == known, f"{where} cycle misses a mode"
                assert check["end"] in check["values"], where


def test_band_checks_name_only_the_promised_bands():
    """The mug script generates the bands; only the contract's five are promised
    to exist, so a lesson may name no other."""
    for lesson, step in _steps():
        for check in _checks(step["check"]):
            if check["type"] in ("in_band", "mark_band"):
                assert check["band"] in tl.BANDS[check["axis"]], f"{lesson['id']}.{step['id']}"
            if check["type"] == "sweep":
                assert check["axis"] in tl.BANDS
                assert 0 <= check["from"] < check["to"] <= 100


def test_every_band_a_lesson_names_is_in_the_generated_landmarks():
    """The lessons and the landmarks file are written by different code: the
    lessons name a band within its axis ("handle_loop"), and the build script
    names it with the axis ("x.handle_loop"). The runner compares the short
    form. This joins the two for real, so a renamed band or a new naming scheme
    fails here instead of every band check silently never passing."""
    landmarks = json.loads((ROOT / "app" / "tutorial_mug.landmarks.json").read_text())
    generated = {
        axis: {re.sub(r"^[xyz]\.", "", band["name"]) for band in bands}
        for axis, bands in landmarks["axes"].items()
    }
    for lesson, step in _steps():
        where = f"{lesson['id']}.{step['id']}"
        for check in _checks(step["check"]):
            if check["type"] in ("in_band", "mark_band"):
                assert check["band"] in generated[check["axis"]], f"{where}: no {check['axis']}.{check['band']}"
        if step["narrate"] and step["check"]["type"] in ("sweep", "in_band", "mark_band"):
            missing = set(step["narrate"]) - generated[step["check"]["axis"]]
            assert not missing, f"{where} narrates bands the landmarks do not have: {sorted(missing)}"


def test_the_runner_compares_band_names_without_the_axis_prefix():
    source = TUTORIAL_JS.read_text()
    assert "function shortBandName(" in source
    assert "shortBandName(b.name) === wanted" in source


def test_key_checks_wait_for_codes_in_the_key_table():
    codes = {entry["code"] for entry in tl.KEYS.values() if entry["code"]}
    for lesson, step in _steps():
        for check in _checks(step["check"]):
            if check["type"] == "key":
                assert check["key"] in codes, f"{lesson['id']}.{step['id']}"
            if check["type"] == "keys":
                assert set(check["keys"]) <= codes, f"{lesson['id']}.{step['id']}"


def test_single_character_key_checks_offer_skip_when_shortcuts_are_off():
    """A check that waits for a one-character key can never pass with single-key
    shortcuts turned off, so the step has to say so and offer Skip at once."""
    for lesson, step in _steps():
        needs_key = any(
            (check["type"] == "key" and len(check["key"]) == 1)
            or (check["type"] == "keys" and any(len(code) == 1 for code in check["keys"]))
            for check in _checks(step["check"])
        )
        if needs_key:
            assert step["key_only"], f"{lesson['id']}.{step['id']} needs a single key"
    # Panning has no button, only W, A, S and D.
    centre = next(s for s in _lesson("zoom_and_move")["steps"] if s["id"] == "centre_handle")
    assert centre["key_only"]


def test_ui_checks_name_real_dialogs():
    dialogs = _controls_to_teach()["dialog"]
    for lesson, step in _steps():
        for check in _checks(step["check"]):
            if check["type"] != "ui":
                continue
            assert check["name"] in dialogs | {"export"}, f"{lesson['id']}.{step['id']}"
            assert check["action"] in ("open", "close", "click")


def test_device_checks_name_a_known_device():
    for lesson, step in _steps():
        for check in _checks(step["check"]):
            if check["type"] == "device":
                assert check["device"] in ("any", "monarch", "dotpad", "cube", "slider")


def test_answers_match_their_checks():
    for lesson, step in _steps():
        where = f"{lesson['id']}.{step['id']}"
        check = step["check"]
        if check["type"] != "answer":
            assert step["answers"] is None and step["store"] is None, where
            continue
        answers = step["answers"]
        assert answers, f"{where} is a question with no answers"
        values = [answer["value"] for answer in answers]
        assert len(values) == len(set(values)), f"{where} repeats an answer value"
        assert all(answer["label"].strip() for answer in answers), where
        correct = check["correct"]
        if correct is None:
            # Any answer is right, so there is nothing to fail and it is kept.
            assert step["store"], f"{where} asks a question it neither checks nor keeps"
            assert step["on_fail"] is None, where
        elif correct == "computed:handle_side":
            assert set(values) <= set(tl.HANDLE_SIDES), where
        elif correct == "computed:cut_axis":
            assert sorted(values) == ["x", "y", "z"], where
        else:
            assert correct in values, f"{where} expects {correct!r}, which is not an answer"
        if correct is not None:
            assert step["store"] is None, f"{where} keeps an answer that has a right value"


def test_stored_answers_are_asked_before_they_are_used():
    stored: dict[str, set[str]] = {}
    for lesson, step in _steps():
        where = f"{lesson['id']}.{step['id']}"
        when = step["when"]
        if when is not None:
            assert set(when) == {"stored", "equals"}, where
            assert when["stored"] in stored, f"{where} depends on an answer not asked yet"
            assert when["equals"] in stored[when["stored"]], f"{where} waits for an impossible answer"
        if step["store"]:
            assert step["store"] not in stored, f"{where} stores {step['store']!r} twice"
            stored[step["store"]] = {answer["value"] for answer in step["answers"]}
    assert stored["display"] == {"monarch", "dotpad", "none"}
    assert stored["axis_choice"] == {"xyz", "turn"}


def test_on_fail_only_where_the_runner_can_tell_a_wrong_move():
    for lesson, step in _steps():
        if step["on_fail"] is not None:
            assert step["check"]["type"] in ON_FAIL_TYPES, f"{lesson['id']}.{step['id']}"


def test_narration_names_values_the_check_can_reach():
    for lesson, step in _steps():
        narrate = step["narrate"]
        if narrate is None:
            continue
        where = f"{lesson['id']}.{step['id']}"
        check = step["check"]
        assert narrate and all(line.strip() for line in narrate.values()), where
        if check["type"] == "cycle":
            assert set(narrate) == set(check["values"]), f"{where} narrates a different set"
        elif check["type"] in ("sweep", "in_band", "mark_band"):
            assert set(narrate) <= set(tl.BANDS[check["axis"]]), f"{where} narrates an unpromised band"
        else:
            pytest.fail(f"{where} narrates a {check['type']} check, which has no arrivals")


def test_the_worked_example_is_the_only_demo_and_covers_the_mug():
    demos = [(lesson["id"], step) for lesson, step in _steps() if step["demo"] is not None]
    assert [(lesson_id, step["id"]) for lesson_id, step in demos] == [("depth", "worked_example")]
    step = demos[0][1]
    assert step["check"]["type"] == "manual"
    frames = step["demo"]["frames"]
    assert [frame["cut_percent"] for frame in frames] == [5, 25, 50, 75, 95]
    assert all(set(frame) == {"cut_percent", "say"} and frame["say"].strip() for frame in frames)


def test_requirements():
    requires = {lesson["id"]: lesson["requires"] for lesson in tl.LESSONS}
    assert requires["test_pattern"] == ["display"]
    assert requires["cube"] == ["cube"]
    assert requires["slider"] == ["slider"]
    extras = [lesson["id"] for lesson in tl.LESSONS if lesson["part"] == "extras"]
    assert extras == ["layout_and_graph", "cube", "slider"]
    detective = {step["id"]: step["requires"] for step in _lesson("mug_detective")["steps"]}
    # The first two work by speech alone; the rest need something to feel.
    assert detective == {
        "which_axis": [], "back_to_handle": [], "floor_top": ["display"],
        "wall_vs_bar": ["display"], "handle_joins": ["display"],
    }


def test_turn_mode_steps_run_only_for_people_who_keep_turn_mode():
    steps = {step["id"]: step for step in _lesson("axes")["steps"]}
    for step_id in ("turn_tip", "turn_yaw", "turn_roll"):
        assert steps[step_id]["when"] == {"stored": "axis_choice", "equals": "turn"}
        assert steps[step_id]["check"] == {"type": "answer", "correct": "computed:handle_side"}
        assert [a["value"] for a in steps[step_id]["answers"]] == list(tl.HANDLE_SIDES)


def test_default_pose_is_the_study_defaults_on_the_tutorial_mug():
    """The pose most lessons start from is the one /study's onboarding uses, so
    "the handle is on the left" means the same in both."""
    for field in ("view", "depth", "render_mode", "representation_mode", "compose_scrollbar",
                  "zoom", "reset_pan"):
        assert tl.DEFAULT_POSE[field] == study_protocol.VIEWER_DEFAULTS[field], field
    assert tl.XYZ_POSE == {**tl.DEFAULT_POSE, "axis_mode": "xyz"}
    assert _lesson("axes")["pose"] == tl.XYZ_POSE


def test_locked_lessons():
    """Lesson 12 teaches the model chooser and lesson 13 the layout radios, so
    those two leave them unlocked."""
    unlocked = [lesson["id"] for lesson in tl.LESSONS if not lesson["lock"]]
    assert unlocked == ["your_own_model", "layout_and_graph"]


def test_the_whole_tutorial_is_about_an_hour():
    """The welcome says about an hour; the path without the extras has to be that."""
    core = sum(lesson["minutes"] for lesson in tl.LESSONS if lesson["part"] != "extras")
    assert 45 <= core <= 75, core


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def test_first_step_names_exit_first_and_says_what_the_tutorial_is():
    welcome = tl.LESSONS[0]["steps"][0]
    first_sentence = re.split(r"(?<=[.?!])\s", welcome["text"], maxsplit=1)[0]
    assert "Exit tutorial" in first_sentence
    text = welcome["text"]
    assert "about an hour" in text and "in parts" in text
    assert "pauses" in text and "Tutorial button" in text and "picks it back up" in text
    # What it says about recording has to be true on /viewer: the viewer keeps
    # its own logs while you practise, so the tutorial may only claim that it
    # adds no recording of its own beyond consented lesson progress.
    assert "adds no recording of its own" in text and "analytics" in text
    assert "Nothing you do here is recorded" not in text


def test_the_end_of_the_tutorial_says_how_to_redo_it_and_what_follows():
    """The last lesson before the extras is where the tutorial ends; it says so,
    how to come back to any lesson, and that the extras follow."""
    main = [lesson for lesson in tl.LESSONS if lesson["part"] != "extras"]
    words = " ".join(text for _, text in _lesson_strings(main[-1]))
    assert "Tutorial button" in words and "Choose a lesson" in words and "Start over" in words
    assert "end of the tutorial" in words and "extra lessons follow" in words


def test_no_dashes_and_no_words_that_assume_sight():
    for where, text in _all_user_strings():
        for dash in DASHES:
            assert dash not in text, f"{where} has a dash: {text!r}"
        found = VISUAL_WORDS.search(text) or SEEN_NOT_FROM.search(text)
        assert not found, f"{where} says {found.group(0)!r}: {text!r}"


def test_the_module_itself_has_no_em_dash():
    assert "\u2014" not in LESSONS_PY.read_text(encoding="utf-8")


def test_keys_are_named_only_through_placeholders():
    """A key spelled out in lesson text would name a keyboard key to a DotPad user
    and never the dot beside it. The only braces allowed are key placeholders and
    the runner's three tokens."""
    spelled = re.compile(r"\b(Page Up|Page Down|Arrow Up|Arrow Down|dot \d|dots \d)\b")
    # "Press R", "press 0", "press Period": a viewer key after press. Screen
    # reader chords (NVDA+F2, Insert+Z, VO-Q) and buttons (Continue) are not keys
    # of the viewer and stay as they are.
    pressed = re.compile(r"\bpress (?:[A-Z0-9]\b(?![+-])|Period\b|Comma\b|Home\b|End\b|Escape\b|Space\b)",
                         re.IGNORECASE)
    for where, text in _all_user_strings():
        if where.startswith("KEYS.") or where.endswith("braille"):
            continue
        for inside in ANY_BRACES.findall(text):
            assert inside.startswith("key:") or inside in tl.RUNTIME_TOKENS, f"{where}: {{{inside}}}"
        assert not spelled.search(text), f"{where} spells a key out: {text!r}"
        assert not pressed.search(text), f"{where} spells a key out: {text!r}"


def test_runtime_tokens_only_in_step_text():
    """Only the persistent text is promised to have its tokens filled in."""
    for lesson, step in _steps():
        for where, text in _step_strings(step):
            tokens = set(ANY_BRACES.findall(text)) & set(tl.RUNTIME_TOKENS)
            if tokens:
                assert where == "text", f"{lesson['id']}.{step['id']}.{where} uses {tokens}"


def test_no_sentence_starts_with_a_key_name():
    """A resolved name can start lower case ("dot 4 or Page Up"), so it never
    opens a sentence."""
    for where, text in _all_user_strings():
        assert not re.search(r"(?:^|[.!?]\s+)\{key:", text), f"{where}: {text!r}"


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("display", tl.DISPLAYS)
def test_every_placeholder_resolves(display):
    for lesson in tl.LESSONS:
        for where, text in _lesson_strings(lesson):
            for name in KEY_PLACEHOLDER.findall(text):
                assert name in tl.KEYS, f"{lesson['id']}.{where} names unknown key {name!r}"
    payload = tl.lessons_payload(display)
    assert set(payload) == {"version", "lessons", "keys", "key_help"}
    assert payload["version"] == tl.TUTORIAL_VERSION
    assert "{key:" not in json.dumps(payload)


def test_display_buttons_are_named_only_for_that_display():
    assert tl.key_name("depth_deeper_10", "none") == "Page Up"
    assert tl.key_name("depth_deeper_10", "monarch") == "dot 4 or Page Up"
    assert tl.key_name("depth_deeper_10", "dotpad") == "dot 4 or Page Up"
    assert tl.key_name("depth_deeper_1", "dotpad") == "Arrow Up"
    assert tl.key_name("cursor_mode", "monarch") == "Space"
    assert tl.key_name("cursor_mode", "dotpad") == "dots 1 2 3 6"
    assert tl.key_name("cursor_mode", "none") == "Space on the Monarch, dots 1 2 3 6 on the DotPad"
    assert tl.key_name("pattern_left", "none") == "dot 1"
    with pytest.raises(ValueError):
        tl.key_name("no_such_key")


def test_payload_resolves_the_same_step_differently_per_display():
    texts = {}
    for display in tl.DISPLAYS:
        depth = next(l for l in tl.lessons_payload(display)["lessons"] if l["id"] == "depth")
        texts[display] = next(s for s in depth["steps"] if s["id"] == "to_the_far_side")["text"]
    assert "Press Page Up to move it" in texts["none"]
    assert "Press dot 4 or Page Up to move it" in texts["monarch"]
    assert texts["none"] != texts["monarch"]
    pattern = next(l for l in tl.lessons_payload("dotpad")["lessons"] if l["id"] == "test_pattern")
    assert "If it is on the left half, press dot 1;" in pattern["steps"][0]["text"]


def test_payload_leaves_runtime_tokens_and_data_alone():
    payload = tl.lessons_payload("monarch")
    for original, resolved in zip(tl.LESSONS, payload["lessons"]):
        assert resolved["pose"] == original["pose"]
        for step, out in zip(original["steps"], resolved["steps"]):
            assert out["check"] == step["check"]
            assert out["braille"] == step["braille"]
            assert out["when"] == step["when"]
            for token in tl.RUNTIME_TOKENS:
                assert step["text"].count(f"{{{token}}}") == out["text"].count(f"{{{token}}}")
    zoom = next(l for l in payload["lessons"] if l["id"] == "zoom_and_move")
    assert "{handle_side}" in zoom["steps"][1]["text"]
    assert "{pan_toward_handle}" in zoom["steps"][1]["text"]


def test_payload_is_a_copy():
    payload = tl.lessons_payload("none")
    payload["lessons"][0]["steps"][0]["text"] = "changed"
    payload["keys"]["reset"]["keyboard"] = "changed"
    assert tl.LESSONS[0]["steps"][0]["text"] != "changed"
    assert tl.KEYS["reset"]["keyboard"] == "0"


def test_an_unknown_display_gets_keyboard_names():
    assert tl.lessons_payload("braillenote") == tl.lessons_payload("none")
    assert tl.lessons_payload(" DotPad ") == tl.lessons_payload("dotpad")


def test_payload_is_json():
    for display in tl.DISPLAYS:
        json.dumps(tl.lessons_payload(display))


def test_key_help_describes_every_key_and_every_device_button():
    help_text = tl.lessons_payload("none")["key_help"]
    for name, entry in tl.KEYS.items():
        if entry["code"]:
            assert help_text[entry["code"]] == f"{entry['keyboard']}: {entry['does']}", name
    for device in ("monarch", "dotpad"):
        for command in CAPTURE_NAMES:
            assert help_text[f"{device}:{command}"].strip(), f"{device}:{command}"
    assert help_text["dotpad:dot4"] == "dot 4: moves the cut 10% deeper, away from you"
    assert help_text["monarch:cursor"].startswith("Space: ")
    assert help_text["dotpad:other"] == tl.UNMAPPED_DEVICE_BUTTON


# ---------------------------------------------------------------------------
# The key table against the viewer
# ---------------------------------------------------------------------------


def test_key_table_fields_and_required_names():
    assert REQUIRED_KEY_NAMES <= set(tl.KEYS)
    for name, entry in tl.KEYS.items():
        assert set(entry) == {"keyboard", "code", "monarch", "dotpad", "does"}, name
        assert entry["does"].strip(), name
        assert (entry["keyboard"] is None) == (entry["code"] is None), name
        if entry["keyboard"] is None:
            # A display-only control has to exist on both displays, or someone
            # is taught a control their display does not have.
            assert entry["monarch"] and entry["dotpad"], name
    codes = [entry["code"] for entry in tl.KEYS.values() if entry["code"]]
    assert len(codes) == len(set(codes)), "two keys share a code"
    assert tl.KEYS["cursor_mode"]["monarch"] == "Space"
    assert tl.KEYS["cursor_mode"]["dotpad"] == "dots 1 2 3 6"
    assert set(tl.DEVICE_COMMANDS) == CAPTURE_NAMES


def test_every_viewer_key_in_the_table_is_one_the_viewer_handles():
    supported = _supported_shortcuts()
    cases = _switch_cases()
    for name, entry in tl.KEYS.items():
        code = entry["code"]
        if code is None:
            continue
        if name in TUTORIAL_KEY_NAMES:
            # The tutorial's own keys must be free in the viewer, or one press
            # would do two things.
            assert code not in supported, f"{code!r} is a viewer shortcut too"
            continue
        assert code in supported, f"{name}: {code!r} is not in supportedShortcuts"
        assert code in cases, f"{name}: no case {code!r} in the keydown switch"


# What each key's case in the keydown switch has to do. Checked against the case
# body, so a key moved to another action fails here rather than in a lesson.
KEY_EFFECTS = {
    "depth_deeper_10": "stepSliceDepth(10)",
    "depth_shallower_10": "stepSliceDepth(-10)",
    "depth_deeper_1": "stepSliceDepth(1)",
    "depth_shallower_1": "stepSliceDepth(-1)",
    "depth_near": "goToSliceEnd(normalizedKey === 'end')",
    "depth_far": "goToSliceEnd(normalizedKey === 'end')",
    "axis_x": "selectAxis(normalizedKey)",
    "axis_y": "selectAxis(normalizedKey)",
    "axis_z": "selectAxis(normalizedKey)",
    "turn_pitch_up": "applyRelativeRotation('pitchUp')",
    "turn_pitch_down": "applyRelativeRotation('pitchDown')",
    "turn_yaw_left": "applyRelativeRotation('yawLeft')",
    "turn_yaw_right": "applyRelativeRotation('yawRight')",
    "turn_roll_ccw": "applyRelativeRotation('rollCounterclockwise')",
    "turn_roll_cw": "applyRelativeRotation('rollClockwise')",
    "where_am_i": "announceWhereAmI()",
    "origin": "announceOrigin()",
    "reset": "resetOrientationZoomAndDepth()",
    "fit": "fitCurrentViewToDevice()",
    "render_mode": "queueRenderModeStep()",
    "layout": "cycleRepresentationMode(",
    "scrollbar_overlay": "viewerState.composeScrollbar = !viewerState.composeScrollbar",
    "graph_overlay": "viewerState.composeSliceGraph = !viewerState.composeSliceGraph",
    "graph_refresh": "captureSliceGraphAnchor(",
    "graph_lock": "toggleSliceGraphLock()",
    "zoom_in_10": "viewerState.currentZoom + ZOOM_STEP",
    "zoom_out_10": "viewerState.currentZoom - ZOOM_STEP",
    "zoom_in_1": "viewerState.currentZoom + FINE_ZOOM_STEP",
    "zoom_out_1": "viewerState.currentZoom - FINE_ZOOM_STEP",
    "pan_up": "pendingPanDirection = 'up'",
    "pan_left": "pendingPanDirection = 'left'",
    "pan_down": "pendingPanDirection = 'down'",
    "pan_right": "pendingPanDirection = 'right'",
    "shortcuts": "openShortcutsDialog()",
    "shortcuts_question": "openShortcutsDialog()",
    "escape": ".blur()",
    "print": "print_view()",
}


def test_every_key_does_what_the_table_says():
    cases = _switch_cases()
    viewer_keys = {n for n, e in tl.KEYS.items() if e["code"] and n not in TUTORIAL_KEY_NAMES}
    assert set(KEY_EFFECTS) == viewer_keys, "a key in the table has no expected effect here"
    for name, effect in KEY_EFFECTS.items():
        body = cases[tl.KEYS[name]["code"]]
        assert _compact(effect) in _compact(body), f"{name}: case does not call {effect}"


def test_step_sizes_are_what_the_table_says():
    source = _viewer_js()
    assert re.search(r"const ZOOM_STEP = 0\.1;", source)
    assert re.search(r"const FINE_ZOOM_STEP = 0\.01;", source)
    assert "10%" in tl.KEYS["zoom_in_10"]["does"] and "1%" in tl.KEYS["zoom_in_1"]["does"]
    assert "10%" in tl.KEYS["depth_deeper_10"]["does"] and "1%" in tl.KEYS["depth_deeper_1"]["does"]


# What each display button has to do, in each device file's own terms.
MONARCH_EFFECTS = {
    "depth_deeper_10": {"type": "depth", "delta": 10},
    "depth_shallower_10": {"type": "depth", "delta": -10},
    "pattern_right": {"type": "depth", "delta": 10},
    "pattern_left": {"type": "depth", "delta": -10},
    "axis_x": {"type": "axis", "axis": "x"},
    "axis_y": {"type": "axis", "axis": "y"},
    "axis_z": {"type": "axis", "axis": "z"},
    "cursor_mode": {"type": "cycle-cursor"},
    "cursor_left": {"type": "move", "dCol": -1, "dRow": 0},
    "cursor_right": {"type": "move", "dCol": 1, "dRow": 0},
    "cursor_up": {"type": "move", "dCol": 0, "dRow": -1},
    "cursor_down": {"type": "move", "dCol": 0, "dRow": 1},
}


def _monarch_reports(name: str, commands: dict[str, dict]) -> list[str]:
    """The Monarch report keys a control name stands for."""
    dots = re.fullmatch(r"dots? ([1-8](?: [1-8])*)", name)
    if dots:
        # The dot keys arrive as a bitfield in the first byte: dot 1 is 1 and
        # dot 4 is 8, which is how the depth keys are mapped.
        return [f"32:{_dot_bits(dots.group(1))},0,0"]
    if name == "Space":
        return [key for key, c in commands.items() if c["type"] == "cycle-cursor"]
    if name == "the D-pad":
        return [key for key, c in commands.items() if c["type"] == "move"]
    direction = re.fullmatch(r"D-pad (left|right|up|down)", name)
    if direction:
        vector = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[direction.group(1)]
        return [key for key, c in commands.items()
                if c["type"] == "move" and (c["dCol"], c["dRow"]) == vector]
    pytest.fail(f"unrecognised Monarch control name {name!r}")


def test_monarch_names_are_what_monarch_hid_maps():
    commands = _monarch_commands()
    named = {name for name, entry in tl.KEYS.items() if entry["monarch"]}
    assert named == set(MONARCH_EFFECTS) | {"cursor_move"}
    for name, effect in MONARCH_EFFECTS.items():
        reports = _monarch_reports(tl.KEYS[name]["monarch"], commands)
        assert len(reports) == 1, f"{name}: {tl.KEYS[name]['monarch']!r} is not one report"
        assert reports[0] in commands, f"{name}: no Monarch command {reports[0]}"
        assert commands[reports[0]] == effect, f"{name}: {commands[reports[0]]}"
    assert len(_monarch_reports(tl.KEYS["cursor_move"]["monarch"], commands)) == 4


DOTPAD_ACTIONS = {
    "cursor_up": ("KeyFunction1", (0, -1)),
    "cursor_down": ("KeyFunction4", (0, 1)),
    "cursor_left": ("PanningLeft", (-1, 0)),
    "cursor_right": ("PanningRight", (1, 0)),
}
DOTPAD_KEY_NAMES = {
    "function key 1": "KeyFunction1",
    "function key 4": "KeyFunction4",
    "the left panning key": "PanningLeft",
    "the right panning key": "PanningRight",
}


def test_dotpad_names_are_what_dotpad_integration_maps():
    on_key = _compact(_strip_comments(_dotpad_on_key()))
    letters = _dotpad_letters()
    assert "constn=10;" in on_key
    # Dots 1 and 4 alone step the depth by 100/n, shallower and deeper.
    for name, byte, call in (
        ("depth_shallower_10", 0x01, "window.stepSliceDepth(-100/n)"),
        ("depth_deeper_10", 0x08, "window.stepSliceDepth(100/n)"),
        ("pattern_left", 0x01, None),
        ("pattern_right", 0x08, None),
    ):
        dots = re.fullmatch(r"dot (\d)", tl.KEYS[name]["dotpad"])
        assert dots and _dot_bits(dots.group(1)) == byte, name
        if call:
            branch = on_key.split(f"if(byte6===0x{byte:02x})", 1)
            assert len(branch) == 2, f"no branch for byte 0x{byte:02x}"
            assert branch[1].startswith("{" + call), f"{name}: byte 0x{byte:02x} does not {call}"
    # The chords are read as whole braille letters.
    for name, letter in (("axis_x", "x"), ("axis_y", "y"), ("axis_z", "z"), ("cursor_mode", "v")):
        dots = re.fullmatch(r"dots ([1-6](?: [1-6])*)", tl.KEYS[name]["dotpad"])
        assert dots, name
        assert letters.get(_dot_bits(dots.group(1))) == letter, f"{name} is not the letter {letter}"
    assert "if(letter==='x'||letter==='y'||letter==='z'){" in on_key
    assert "window.axisCommandFromDevice(letter,'dotpad')" in on_key
    cursor_branch = on_key.split("if(letter==='v'){", 1)
    assert len(cursor_branch) == 2, "no branch for the letter v"
    assert cursor_branch[1].startswith(
        "if(typeofwindow.cycleCursorState==='function'){window.cycleCursorState()"
    ), "the v chord does not cycle the cursor"
    # The cursor keys.
    actions = _dotpad_key_actions()
    for name, (key, vector) in DOTPAD_ACTIONS.items():
        assert DOTPAD_KEY_NAMES[tl.KEYS[name]["dotpad"]] == key, name
        assert actions[key] == vector, f"{key} moves {actions[key]}, not {vector}"
    assert set(actions) == set(DOTPAD_KEY_NAMES.values())


# ---------------------------------------------------------------------------
# Coverage: everything the viewer offers is taught somewhere
# ---------------------------------------------------------------------------

# Every viewer shortcut and the lesson that teaches it. q and e are in
# supportedShortcuts but do nothing. A shortcut added to the viewer fails
# test_every_viewer_shortcut_is_taught until it has a row here and a lesson.
KEY_COVERAGE = {
    ".": "before_you_start",
    "0": "meet_the_mug",
    "arrowup": "depth", "arrowdown": "depth", "pageup": "depth", "pagedown": "depth",
    "home": "depth", "end": "depth",
    "r": "render_modes",
    "2": "zoom_and_move", "3": "zoom_and_move", "4": "zoom_and_move", "5": "zoom_and_move",
    "w": "zoom_and_move", "a": "zoom_and_move", "s": "zoom_and_move", "d": "zoom_and_move",
    "x": "axes", "y": "axes", "z": "axes", ",": "axes",
    "u": "axes", "o": "axes", "i": "axes", "k": "axes", "j": "axes", "l": "axes",
    "t": "layout_and_graph", "g": "layout_and_graph", "v": "layout_and_graph",
    "[": "layout_and_graph", "]": "layout_and_graph",
    "f": "reset_and_fit",
    "h": "help_and_settings", "?": "help_and_settings", "escape": "help_and_settings",
}
KEYS_THAT_DO_NOTHING = {"q", "e"}
# Shortcuts the viewer has and no lesson teaches, each with the reason. P is not
# taught because it does not do what it says; the #245 review asked for it to
# come out of the help instead.
KEYS_NOT_TAUGHT = {"p": "does not work as the help says; to come out of the help"}

# Every main-menu button, dialog, Settings control and page control, the lesson
# that teaches it, and words that lesson has to contain about it.
CONTROL_COVERAGE = {
    # Main menu.
    "device-connect-btn": ("connect", "Connect and Disconnect in the main menu"),
    "device-disconnect-btn": ("connect", "Disconnect in the main menu"),
    "nav-about-btn": ("help_and_settings", "About, also in the main menu"),
    "nav-help-btn": ("help_and_settings", "Help, in the main menu"),
    "nav-settings-btn": ("help_and_settings", "Open Settings from the main menu"),
    # Dialogs.
    "shortcuts-dialog": ("help_and_settings", "list of every keyboard shortcut"),
    "about-dialog": ("help_and_settings", "says who makes the viewer"),
    "settings-dialog": ("help_and_settings", "Settings has"),
    "session-consent-dialog": ("before_you_start", "if you allowed analytics"),
    # Settings.
    "axis-mode-turn": ("axes", "Turn mode"),
    "axis-mode-xyz": ("axes", "XYZ mode"),
    "settings-origin-marker": ("axes", "marks the origin"),
    "settings-axis-letters": ("axes", "axis letters"),
    "settings-single-key-shortcuts": ("before_you_start", "Single-key shortcuts"),
    "output-device-dotpad": ("connect", "Output Device"),
    "output-device-monarch": ("connect", "Output Device"),
    "settings-enable-slider": ("slider", "Slider, under Hardware Controls"),
    "settings-enable-cube": ("cube", "Cube, under Hardware Controls"),
    "settings-enable-debug-panel": ("help_and_settings", "Debug Panel"),
    "settings-enable-bbox": ("help_and_settings", "Bounding Box"),
    "show-view-info-box": ("help_and_settings", "View info box on display"),
    "slice-graph-lock-checkbox": ("layout_and_graph", "Lock slice graph"),
    "slice-graph-mode-difference": ("layout_and_graph", "difference from the anchor"),
    "slice-graph-mode-column-count": ("layout_and_graph", "area of each slice"),
    "settings-close-btn": ("help_and_settings", "its Close button"),
    # The page.
    "pitch-up-btn": ("axes", "The Orientation section has a button for each"),
    "pitch-down-btn": ("axes", "The Orientation section has a button for each"),
    "yaw-left-btn": ("axes", "The Orientation section has a button for each"),
    "yaw-right-btn": ("axes", "The Orientation section has a button for each"),
    "roll-ccw-btn": ("axes", "The Orientation section has a button for each"),
    "roll-cw-btn": ("axes", "The Orientation section has a button for each"),
    "view-x-plus-btn": ("axes", "X plus to Z minus"),
    "view-x-minus-btn": ("axes", "X plus to Z minus"),
    "view-y-plus-btn": ("axes", "X plus to Z minus"),
    "view-y-minus-btn": ("axes", "X plus to Z minus"),
    "view-z-plus-btn": ("axes", "X plus to Z minus"),
    "view-z-minus-btn": ("axes", "X plus to Z minus"),
    "slice-depth-slider": ("depth", "The Depth section has a slider"),
    "deeper-btn": ("depth", "Deeper and Shallower buttons"),
    "shallower-btn": ("depth", "Deeper and Shallower buttons"),
    "render-mode-filled": ("render_modes", "same four as radio buttons"),
    "render-mode-outline": ("render_modes", "same four as radio buttons"),
    "render-mode-cut": ("render_modes", "same four as radio buttons"),
    "render-mode-xray": ("render_modes", "same four as radio buttons"),
    "view-mode-single": ("layout_and_graph", "same three are radio buttons"),
    "view-mode-side-by-side": ("layout_and_graph", "same three are radio buttons"),
    "view-mode-slice-graph": ("layout_and_graph", "same three are radio buttons"),
    "zoom-input": ("zoom_and_move", "Zoom level field"),
    "zoom-out-btn": ("zoom_and_move", "Zoom In and Zoom Out buttons"),
    "zoom-in-btn": ("zoom_and_move", "Zoom In and Zoom Out buttons"),
    "reset-position-btn": ("reset_and_fit", "Reset Position button"),
    "model-list-dropdown": ("your_own_model", "Model list"),
    "delete-model-btn": ("your_own_model", "Remove uploaded model"),
    "upload-model-input": ("your_own_model", "Upload model"),
    "export-slice-svg-btn": ("your_own_model", "Export Current View as Image"),
    # The device sections.
    "trinkey-connect-btn": ("slider", "choose Connect USB"),
    "trinkey-disconnect-btn": ("slider", "Disconnect, next to Connect USB"),
    "witmotion-connect-btn": ("cube", "choose Connect BLE"),
    "witmotion-disconnect-btn": ("cube", "Disconnect, next to Connect BLE"),
}

# The Tutorial button the runner adds to the main menu is taught where the
# tutorial says how to come back to it.
TUTORIAL_BUTTON = ("help_and_settings", "choose the Tutorial button in the main menu")

# Display buttons, by the name the runner's capture hook reports, and the lesson
# that teaches them.
DEVICE_COVERAGE = {
    "dot1": ("depth", "depth_shallower_10"),
    "dot4": ("depth", "depth_deeper_10"),
    "axis-x": ("axes", "axis_x"),
    "axis-y": ("axes", "axis_y"),
    "axis-z": ("axes", "axis_z"),
}
# Display buttons no lesson teaches. The cursor has nothing to do yet, so its
# lesson came out (#245 review); key help still names these buttons.
DEVICE_BUTTONS_NOT_TAUGHT = {"cursor", "move-left", "move-right", "move-up", "move-down"}


def test_every_viewer_shortcut_is_taught():
    supported = _supported_shortcuts()
    expected = supported - KEYS_THAT_DO_NOTHING - set(KEYS_NOT_TAUGHT)
    assert set(KEY_COVERAGE) == expected, (
        "a viewer shortcut has no lesson, or a lesson teaches one that is gone: "
        f"{sorted(set(KEY_COVERAGE) ^ expected)}"
    )
    assert set(KEYS_NOT_TAUGHT) <= supported, "a key left untaught is no longer in the viewer"
    by_code: dict[str, set[str]] = {}
    for name, entry in tl.KEYS.items():
        if entry["code"]:
            by_code.setdefault(entry["code"], set()).add(name)
    for code, lesson_id in KEY_COVERAGE.items():
        assert code in by_code, f"{code!r} is not in the key table"
        taught = _key_names_used(_lesson(lesson_id))
        assert by_code[code] & taught, f"{lesson_id} does not teach {code!r}"


def test_the_keys_that_do_nothing_still_do_nothing():
    """If q or e ever gets a case in the switch, it needs a lesson."""
    cases = _switch_cases()
    assert KEYS_THAT_DO_NOTHING <= _supported_shortcuts()
    assert not KEYS_THAT_DO_NOTHING & set(cases)


def test_every_control_is_taught():
    kinds = _controls_to_teach()
    found = set().union(*kinds.values())
    covered = set(CONTROL_COVERAGE)
    tutorial_buttons = {i for i in kinds["menu"] if "tutorial" in i}
    missing = found - covered - tutorial_buttons
    assert not missing, f"controls with no lesson: {sorted(missing)}"
    gone = covered - found
    assert not gone, f"coverage rows for controls no longer on the page: {sorted(gone)}"
    rows = dict(CONTROL_COVERAGE)
    rows.update({i: TUTORIAL_BUTTON for i in tutorial_buttons})
    for control, (lesson_id, phrase) in rows.items():
        words = " ".join(text for _, text in _lesson_strings(_lesson(lesson_id)))
        assert phrase.lower() in words.lower(), f"{lesson_id} never mentions {control} ({phrase!r})"


def test_every_display_button_is_taught():
    assert set(DEVICE_COVERAGE) == CAPTURE_NAMES - {"other"} - DEVICE_BUTTONS_NOT_TAUGHT
    for command, (lesson_id, name) in DEVICE_COVERAGE.items():
        assert tl.DEVICE_COMMANDS[command] == name or name == "cursor_move", command
        assert name in _key_names_used(_lesson(lesson_id)), f"{lesson_id} does not teach {command}"


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def test_every_check_type_used_is_implemented_by_the_runner():
    if not TUTORIAL_JS.exists():
        pytest.skip("static/js/tutorial.js is not written yet (the runner is a separate part); "
                    "this runs once it exists")
    implemented = _registry_keys(TUTORIAL_JS.read_text(encoding="utf-8"))
    assert implemented, "static/js/tutorial.js has no CHECKS registry this test can read"
    used = {check["type"] for _, step in _steps() for check in _checks(step["check"])}
    assert used <= implemented, f"check types the runner lacks: {sorted(used - implemented)}"


def test_the_registry_reader_reads_top_level_keys_only():
    source = """
    const CHECKS = Object.freeze({
        manual: () => true,
        'in_band': function (step) { return { band: 'x', nested: 1 }; },
        async answer(step, event) { const inner = { key: 2 }; return inner; },
        // edge: commented out
        keys: (s) => ['a', 'b'].every(k => s.seen.has(k)),
        all,
    });
    CHECKS.extra = () => false;
    """
    assert _registry_keys(source) == {"manual", "in_band", "answer", "keys", "all", "extra"}
