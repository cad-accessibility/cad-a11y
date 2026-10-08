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
    "demo", "key_only", "when", "store", "requires", "links",
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
    "shortcuts", "cursor_mode", "cursor_move", "turn_pitch_up", "turn_pitch_down",
    "turn_yaw_left", "turn_yaw_right", "turn_roll_ccw", "turn_roll_cw", "tutorial_continue",
    "tutorial_back", "tutorial_repeat", "tutorial_show_me",
}

# The command names the runner's captureDeviceKey hook reports (contract section 5).
CAPTURE_NAMES = {
    "dot1", "dot4", "cursor", "axis-x", "axis-y", "axis-z", "move-left", "move-right", "move-up",
    "move-down", "other",
}

# Handled by tutorial.js rather than the viewer.
TUTORIAL_KEY_NAMES = {"tutorial_continue", "tutorial_back", "tutorial_repeat", "tutorial_show_me"}

# Words that assume sight. "Seen from" is allowed: it is the viewer's own phrase
# for which side a view is from, and a lesson has to quote what people hear. The
# same goes for "Visual previews", the name of the page's region that holds
# Export, which a screen reader reads out among the landmarks.
VISUAL_WORDS = re.compile(
    r"\b(see|sees|seeing|saw|look|looks|looked|looking|click|clicks|clicked|clicking|watch|"
    r"watches|watching|glance|glances|visible|visibly|visibility|invisible|visual|visually|"
    r"highlighted)\b",
    re.IGNORECASE,
)
SEEN_NOT_FROM = re.compile(r"\bseen\b(?!\s+from)", re.IGNORECASE)
PAGE_NAMES = ("Visual previews",)
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
    for i, link in enumerate(step["links"] or []):
        yield f"links[{i}].label", link["label"]


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
    # Lesson 8's differs only in the mode and where the slice plane starts,
    # which test_lesson_8_starts_away_from_the_handle_and_its_hints_land_in_it
    # holds against the mug.
    assert tl.XYZ_POSE == {**tl.DEFAULT_POSE, "axis_mode": "xyz", "depth": tl.XYZ_POSE["depth"]}
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


def test_first_step_says_what_the_tutorial_is_and_how_to_leave():
    welcome = tl.LESSONS[0]["steps"][0]
    text = welcome["text"]
    assert "about an hour" in text and "in parts" in text
    assert "Exit tutorial" in text and "start where you left off" in text
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
        for name in PAGE_NAMES:
            text = text.replace(name, "")
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


# What a step that waits for Next ends with, and the rule lesson 1 gives for
# it (#245 review, Jen's words).
WAIT_SENTENCE = "Press Next (N) when ready."
WAIT_RULE = ("Some steps move forward by themselves when you complete them. Some wait for you to "
             "press Next (N). They always end with the sentence 'Press Next (N) when ready'.")


def _waits_for_next(step: dict) -> bool:
    """Whether only Next moves the step on: something to read or feel, or a
    place the person says they have reached."""
    return any(check["type"] in ("manual", "mark_band") for check in _checks(step["check"]))


def test_every_step_that_waits_for_next_ends_by_saying_so():
    for lesson, step in _steps():
        where = f"{lesson['id']}.{step['id']}"
        spoken = [("text", step["text"])] + ([("sr", step["sr"])] if step["sr"] is not None else [])
        for field, words in spoken:
            if _waits_for_next(step):
                assert words.endswith(" " + WAIT_SENTENCE), f"{where}.{field} does not end with it"
                assert words.count(WAIT_SENTENCE) == 1, f"{where}.{field} says it twice"
            else:
                # Said only where it is true, or it stops meaning anything.
                assert WAIT_SENTENCE not in words, f"{where}.{field} moves on by itself"


def test_lesson_1_says_once_which_steps_wait():
    found = [(lesson["id"], step["id"], where)
             for lesson, step in _steps() for where, words in _step_strings(step) if WAIT_RULE in words]
    assert found == [("before_you_start", "keys_reach", "text"), ("before_you_start", "keys_reach", "sr")]
    keys_reach = next(s for s in _lesson("before_you_start")["steps"] if s["id"] == "keys_reach")
    # And what a step that moves on by itself does, said once too.
    assert "the tutorial says what you did" in keys_reach["text"]
    assert "the tutorial says what you did" in keys_reach["sr"]
    assert "{key:tutorial_show_me}" in keys_reach["text"] and "{key:tutorial_show_me}" in keys_reach["sr"]


def test_the_printed_mug_is_information_only():
    step = next(s for s in _lesson("before_you_start")["steps"] if s["id"] == "printed_mug")
    assert step["check"] == {"type": "manual"}
    assert step["answers"] is None and step["store"] is None
    assert "optional" in step["text"] and "optional" in step["sr"]


def test_step_links_are_the_print_files_and_the_build_guides():
    """Step text is plain, so a step's links come with it as data (#245
    review). The print files are the zip this app serves until they have a
    repository of their own; the build guides are in tangible-controls."""
    linked = {}
    for lesson, step in _steps():
        links = step["links"]
        if links is None:
            continue
        where = f"{lesson['id']}.{step['id']}"
        assert links, f"{where} has an empty list of links"
        for link in links:
            assert set(link) == {"label", "href"}, where
            assert link["label"].strip() and "{" not in link["label"], where
            assert link["href"] == "/tutorial/prints.zip" or link["href"].startswith(
                "https://github.com/cad-accessibility/"), f"{where}: {link['href']}"
        # The words say where the links are.
        assert "after this text" in step["text"], where
        linked[step["id"]] = links
    assert linked == {
        "printed_mug": [tl.PRINT_FILES_LINK],
        "build_controls": tl.BUILD_GUIDE_LINKS,
        "props_and_end": [tl.PRINT_FILES_LINK],
    }
    assert [link["href"] for link in tl.BUILD_GUIDE_LINKS] == [
        "https://github.com/cad-accessibility/tangible-controls/blob/master/docs/cube.md",
        "https://github.com/cad-accessibility/tangible-controls/blob/master/docs/slider.md",
    ]


def test_everyone_hears_where_the_build_guides_are_before_the_end():
    """Someone without the cube or the slider learns where the guides to making
    them are, and that the two extras can be skipped, before the main tutorial
    ends (#245 review)."""
    steps = _lesson("your_own_model")["steps"]
    assert [step["id"] for step in steps][-2:] == ["build_controls", "props_and_end"]
    step = steps[-2]
    assert step["requires"] == [] and step["when"] is None
    assert "skip those two extras" in step["text"] and "skip those two extras" in step["sr"]
    for part in ("parts list", "print files", "put it together", "set it up"):
        assert part in step["text"], part


# ---------------------------------------------------------------------------
# Lesson 8 against the mug. Its starting place, its hints and its answers are
# worked out here from the landmarks and the viewer's views (#245 review: the X
# step passed the moment X was pressed, because the slice plane started inside
# the handle loop). Positions are fractions along the object from its lowest
# coordinate, as the bands are, so they hold however the viewer counts the
# percentages it says.
# ---------------------------------------------------------------------------


def _landmarks() -> dict:
    return json.loads((ROOT / "app" / "tutorial_mug.landmarks.json").read_text())


def _band(axis: str, name: str) -> tuple[float, float]:
    for band in _landmarks()["axes"][axis]:
        if re.sub(r"^[xyz]\.", "", band["name"]) == name:
            return band["from"] / 100, band["to"] / 100
    raise AssertionError(f"no {axis}.{name} in the landmarks")


def _view_bases() -> dict[str, dict[str, list[int]]]:
    block = re.search(r"const VIEW_BASIS = \{(.*?)\n\};", _viewer_js(), re.DOTALL)
    assert block, "VIEW_BASIS not found in viewer.js"
    found = re.findall(
        r"'([^']+)':\s*\{\s*right:\s*(\[[^\]]*\]),\s*up:\s*(\[[^\]]*\]),\s*depth:\s*(\[[^\]]*\])",
        block.group(1),
    )
    return {view: {"right": json.loads(r), "up": json.loads(u), "depth": json.loads(d)}
            for view, r, u, d in found}


def _home_views() -> dict[str, str]:
    """The view each axis key goes to first (XYZ_AXES, the first of its views)."""
    block = re.search(r"const XYZ_AXES = \{(.*?)\n\};", _viewer_js(), re.DOTALL)
    assert block, "XYZ_AXES not found in viewer.js"
    return dict(re.findall(r"(\w):\s*\{[^}]*views:\s*\['([^']+)'", block.group(1)))


def _reader_side(view: str) -> tuple[str, int]:
    """The axis a view slices along, and the reader's side of it: 1 for the
    plus side, where depth 0 is the axis maximum (depthFromPlanePosition)."""
    depth = _view_bases()[view]["depth"]
    index = next(i for i, value in enumerate(depth) if value)
    return "xyz"[index], 1 if depth[index] > 0 else -1


def _after_presses(position: float, side: int, presses: int, deeper: bool) -> float:
    """Where a 10% key takes the slice plane: a tenth of the object a press,
    deeper running away from the reader."""
    return position + side * (-0.1 if deeper else 0.1) * presses


PRESS_COUNTS = {"once": 1, "twice": 2, "three times": 3, "four times": 4}


def _presses_in(hint: str) -> tuple[bool, int]:
    match = re.search(r"\{key:depth_(deeper|shallower)_10\} (once|twice|three times|four times)", hint)
    assert match, f"no count of presses in {hint!r}"
    return match.group(1) == "deeper", PRESS_COUNTS[match.group(2)]


def _inside(position: float, band: tuple[float, float], margin: float = 0.0) -> bool:
    return band[0] + margin <= position <= band[1] - margin


def test_lesson_8_starts_away_from_the_handle_and_its_hints_land_in_it():
    """The X and Y steps do not pass on the axis key alone, the presses their
    last hint gives reach the band and one press fewer does not. A landing has
    to be a percent inside the band, so a step rounded to a whole percent still
    lands in it."""
    lesson = _lesson("axes")
    steps = {step["id"]: step for step in lesson["steps"]}
    pose = lesson["pose"]
    homes = _home_views()
    # The pose puts its view's axis at its depth and the others in the middle
    # (viewer.js, applyStudyDefaults).
    axis, side = _reader_side(pose["view"])
    planes = {"x": 0.5, "y": 0.5, "z": 0.5}
    planes[axis] = 1 - pose["depth"] / 100 if side > 0 else pose["depth"] / 100

    # Along Z from the middle: the ring with the handle beside it, as cut_z says.
    assert steps["cut_z"]["check"] == {"type": "state", "field": "cut_axis", "equals": "z"}
    assert _inside(planes["z"], _band("z", "handle_beside_ring"), 0.01)
    assert "ring" in steps["cut_z"]["done"] and "handle" in steps["cut_z"]["done"]

    for step_id, axis, band in (("cut_x_handle", "x", "handle_loop"), ("cut_y_arms", "y", "handle_arms")):
        step = steps[step_id]
        assert step["check"] == {"type": "all", "checks": [
            {"type": "state", "field": "cut_axis", "equals": axis},
            {"type": "in_band", "axis": axis, "band": band},
        ]}, step_id
        target = _band(axis, band)
        assert not _inside(planes[axis], target), f"{step_id} passes on the axis key alone"
        # The hint names the side the axis key gives first.
        view_axis, view_side = _reader_side(homes[axis])
        assert view_axis == axis
        side_words = f"{axis.upper()} from {'plus' if view_side > 0 else 'minus'}"
        assert side_words in step["hints"][2], f"{step_id} does not name {side_words}"
        deeper, presses = _presses_in(step["hints"][2])
        landed = _after_presses(planes[axis], view_side, presses, deeper)
        assert _inside(landed, target, 0.01), f"{step_id}: {presses} presses land at {landed:.2f}"
        short = _after_presses(planes[axis], view_side, presses - 1, deeper)
        assert not _inside(short, target), f"{step_id}: {presses - 1} presses are already enough"
        planes[axis] = landed


def test_lesson_8_answers_follow_from_the_mug():
    landmarks = _landmarks()
    handle = landmarks["handle_direction"]
    steps = {step["id"]: step for step in _lesson("axes")["steps"]}
    # The handle lies across Y and Z, so a slice along X shows its whole curve.
    assert handle[0] == 0 and steps["which_axis_loop"]["check"]["correct"] == "x"
    assert steps["handle_sign"]["check"]["correct"] == ("negative" if handle[1] < 0 else "positive")
    # "Viewing from X plus, the viewer said Y right, and the handle was on the
    # left."
    right = _view_bases()[_home_views()["x"]]["right"]
    assert right == [0, 1, 0] and sum(h * r for h, r in zip(handle, right)) < 0
    assert "X plus, the viewer said Y right, and the handle was on the left" in steps["handle_sign"]["hints"][1]
    # "The mug base is centered on the origin in X and Y": the body reaches as
    # far each way along X, and as far along plus Y, and its base is at Z 0.
    box = landmarks["bbox_mm"]
    assert box["x"][0] == -box["x"][1] and box["y"][1] == box["x"][1] and box["z"][0] == 0
    assert "centered on the origin in X and Y" in steps["origin"]["text"]


def test_the_slider_sweep_crosses_the_handle_it_narrates():
    step = next(s for s in _lesson("slider")["steps"] if s["id"] == "slide_sweep")
    low, high = _band("x", "handle_loop")
    assert step["check"]["from"] / 100 < low and step["check"]["to"] / 100 > high
    assert set(step["narrate"]) == {"handle_loop"}


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
    assert help_text["dotpad:dot4"] == "dot 4: moves the slice plane 10% deeper, away from you"
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
    letter = re.fullmatch(r"braille ([xyz])", name)
    if letter:
        # The axis letters are the braille letters x, y and z: dots 1 3 4 6,
        # 1 3 4 5 6 and 1 3 5 6.
        dots = {"x": "1 3 4 6", "y": "1 3 4 5 6", "z": "1 3 5 6"}[letter.group(1)]
        return [f"32:{_dot_bits(dots)},0,0"]
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
        if letter in "xyz":
            assert tl.KEYS[name]["dotpad"] == f"braille {letter}", name
            continue
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

# Controls no lesson names one by one (#245 review). About only says who makes
# the viewer. The first lesson says the page below the tutorial has a section
# for each capability, and Settings comes up throughout, so the buttons,
# sliders and Settings controls are not listed in the lessons.
CONTROLS_NOT_TAUGHT = {
    "nav-about-btn",
    "about-dialog",
    "nav-settings-btn",
    "settings-dialog",
    "session-consent-dialog",
    "settings-axis-letters",
    "output-device-dotpad",
    "output-device-monarch",
    "settings-enable-debug-panel",
    "settings-enable-bbox",
    "show-view-info-box",
    "settings-close-btn",
    "pitch-up-btn",
    "pitch-down-btn",
    "yaw-left-btn",
    "yaw-right-btn",
    "roll-ccw-btn",
    "roll-cw-btn",
    "view-x-plus-btn",
    "view-x-minus-btn",
    "view-y-plus-btn",
    "view-y-minus-btn",
    "view-z-plus-btn",
    "view-z-minus-btn",
    "slice-depth-slider",
    "deeper-btn",
    "shallower-btn",
    "render-mode-filled",
    "render-mode-outline",
    "render-mode-cut",
    "render-mode-xray",
    "zoom-input",
    "zoom-out-btn",
    "zoom-in-btn",
    "delete-model-btn",
    "upload-model-input",
}

# Every main-menu button, dialog, Settings control and page control, the lesson
# that teaches it, and words that lesson has to contain about it.
CONTROL_COVERAGE = {
    # Main menu.
    "device-connect-btn": ("connect", "Connect and Disconnect in the main menu"),
    "device-disconnect-btn": ("connect", "Disconnect in the main menu"),
    "nav-help-btn": ("help_and_settings", "Help, in the main menu"),
    # Dialogs.
    "shortcuts-dialog": ("help_and_settings", "list of every keyboard shortcut"),
    # Settings.
    "axis-mode-turn": ("axes", "Turn mode"),
    "axis-mode-xyz": ("axes", "XYZ mode"),
    "settings-origin-marker": ("axes", "marks the origin"),
    "settings-single-key-shortcuts": ("before_you_start", "Single-key shortcuts"),
    "settings-enable-slider": ("slider", "Slider, under Hardware Controls"),
    "settings-enable-cube": ("cube", "Cube, under Hardware Controls"),
    "slice-graph-lock-checkbox": ("layout_and_graph", "Lock slice graph"),
    "slice-graph-mode-difference": ("layout_and_graph", "difference from the anchor"),
    "slice-graph-mode-column-count": ("layout_and_graph", "area of each slice"),
    # The page.
    "view-mode-single": ("layout_and_graph", "same three are radio buttons"),
    "view-mode-side-by-side": ("layout_and_graph", "same three are radio buttons"),
    "view-mode-slice-graph": ("layout_and_graph", "same three are radio buttons"),
    "reset-position-btn": ("reset_and_fit", "Reset Position button"),
    "model-list-dropdown": ("your_own_model", "Model list"),
    "export-slice-svg-btn": ("your_own_model", "Export Current View as Image"),
    # The device sections.
    "trinkey-connect-btn": ("slider", "choose Connect USB"),
    "trinkey-disconnect-btn": ("slider", "Disconnect, next to Connect USB"),
    "witmotion-connect-btn": ("cube", "choose Connect BLE"),
    "witmotion-disconnect-btn": ("cube", "Disconnect, next to Connect BLE"),
}

# The Tutorial button the runner adds to the main menu is taught where the
# tutorial says how to come back to it.
TUTORIAL_BUTTON = ("your_own_model", "choose the Tutorial button in the main menu")

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
    assert set(KEY_COVERAGE) == supported - KEYS_THAT_DO_NOTHING, (
        "a viewer shortcut has no lesson, or a lesson teaches one that is gone: "
        f"{sorted(set(KEY_COVERAGE) ^ (supported - KEYS_THAT_DO_NOTHING))}"
    )
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
    missing = found - covered - tutorial_buttons - CONTROLS_NOT_TAUGHT
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
