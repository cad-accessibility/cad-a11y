"""The first-run tutorial's lessons, as data.

This module is the one place the tutorial's words live: what each step says,
what it checks, the hints it gives on request, and the name of every key it
teaches. ``GET /tutorial/lessons`` serves ``lessons_payload()``, and
static/js/tutorial.js runs it, so the page never carries lesson text of its own
and the two cannot drift apart. tests/test_tutorial_lessons.py holds the data
against the viewer: every key named here is checked against the keydown handler
and the Monarch and DotPad command maps, and every shortcut, dialog, Settings
control and device control has to be taught by some lesson.

Why data rather than code in the runner
---------------------------------------
The same reason as app/study_protocol.py: a step is words plus a check plus
hints, and keeping them together as data is what lets a test read every word a
user will hear. It also lets the wording be fixed without touching the runner.

Key names
---------
Lesson text never spells a key out. It writes ``{key:depth_deeper_10}``, and
``lessons_payload(display)`` turns that into what that person should press:
"Page Up" with no display, "dot 4 or Page Up" with a Monarch or a DotPad, or
only the display's button for a control the keyboard does not have. So a
DotPad user is told about dot 4 and nobody is told about a button they do not
have. ``KEYS`` is the one table those names come from.

Runtime tokens
--------------
Three tokens are left for the runner, because only the page knows them:

``{handle_side}``        where the mug's handle is on the display right now,
                         "left", "right", "top" or "bottom".
``{pan_toward_handle}``  the pan key that brings the handle toward the middle of
                         the display, and what it does: with the handle near the
                         left edge, "D, which moves the object right". So the
                         token ends its sentence.
``{axis_mode_name}``     the axis mode in use, "Turn" or "XYZ".

They appear only in a step's ``text``.

What the lessons must not promise
---------------------------------
- A direction in percent. ``cut_percent``, what the checks and the bands use,
  is 0 at the axis minimum and 100 at its maximum. What XYZ mode says is the
  same position measured from the model's origin, which on the mug is the
  middle of its base (#235). Either way deeper means away from the reader, and
  from X plus, Y plus or Z plus that lowers the number. Lessons say deeper and
  shallower, and name a direction only for a view the lesson has set.
- Anything visual. The people this is for are blind, so the words are find,
  feel, press and choose.
"""

from __future__ import annotations

import copy
import re
from typing import Any

TUTORIAL_VERSION: int = 1

DISPLAYS = ("monarch", "dotpad", "none")

# Every lesson can be skipped, so none is called optional; the slice graph, the
# cube and the slider come last, as extras, after the end of the tutorial
# itself (#245 review).
PARTS = ("setup", "core", "wrapup", "extras")

# What a lesson or a step can need before it can run. The runner decides what
# each means on the page (a connected display, a browser that can reach the
# cube or the slider); an unmet lesson is offered as "Cannot start yet" with
# Skip, and an unmet step is skipped without a word.
REQUIREMENTS = ("display", "cube", "slider")

RUNTIME_TOKENS = ("handle_side", "pan_toward_handle", "axis_mode_name")

# Every check type the runner implements (static/js/tutorial.js CHECKS). A lesson
# may use no other.
CHECK_TYPES = frozenset({
    "manual", "answer", "key", "keys", "state", "changed", "cycle", "sweep",
    "edge", "in_band", "mark_band", "device", "ui", "settings_unchanged",
    "test_pattern", "cursor_in", "centred", "zoom_by", "model_changed",
    "key_help", "slicegraph_ready", "all",
})

# The landmark bands the lessons rely on, by axis. The mug script generates the
# bands from the geometry and must produce at least these names; a lesson names
# no other, because nothing else is promised to exist.
BANDS = {
    "x": ("handle_loop",),
    "y": ("handle_arms",),
    "z": ("floor", "above_floor", "handle_beside_ring"),
}

# The values "computed:handle_side" can take, which are also the answer values
# of every question about where the handle is. The last two mean the handle
# points at the reader or away from them, so it is not on the display at all.
# The runner compares answers with case, spaces, underscores and hyphens folded
# together, so "toward_you" matches the "toward you" it computes.
HANDLE_SIDES = ("left", "right", "top", "bottom", "toward_you", "away_from_you")

# ---------------------------------------------------------------------------
# Keys
#
# keyboard  what the key is called, as a person would say it.
# code      the viewer's normalizedKey exactly as the keyboard interaction event
#           reports it; None for a control only a display has.
# monarch   the Monarch control, or None.
# dotpad    the DotPad control, or None.
# does      what it does, for key help: "Page Up: moves the slice plane 10% deeper".
#
# The Monarch's axis chords are what monarch-hid.js maps, and that map says the
# reports are inferred and not yet seen on hardware. They are named here because
# the code acts on them; the keyboard letter is always offered beside them.
# ---------------------------------------------------------------------------


def _k(keyboard: str | None, code: str | None, does: str, *, monarch: str | None = None,
       dotpad: str | None = None) -> dict[str, Any]:
    return {"keyboard": keyboard, "code": code, "monarch": monarch, "dotpad": dotpad, "does": does}


KEYS: dict[str, dict[str, Any]] = {
    # Depth. Dots 1 and 4 step 10% on both displays, through the same function
    # as Page Up and Page Down, so deeper means the same whatever is pressed.
    "depth_deeper_10": _k("Page Up", "pageup", "moves the slice plane 10% deeper, away from you",
                          monarch="dot 4", dotpad="dot 4"),
    "depth_shallower_10": _k("Page Down", "pagedown", "moves the slice plane 10% shallower, toward you",
                             monarch="dot 1", dotpad="dot 1"),
    "depth_deeper_1": _k("Arrow Up", "arrowup", "moves the slice plane 1% deeper, away from you"),
    "depth_shallower_1": _k("Arrow Down", "arrowdown", "moves the slice plane 1% shallower, toward you"),
    "depth_near": _k("Home", "home", "moves the slice plane to the surface nearest you"),
    "depth_far": _k("End", "end", "moves the slice plane to the far side"),
    # XYZ mode's axes. On the DotPad they are the braille letters x, y and z.
    "axis_x": _k("X", "x", "slices along X from plus, and pressed again from minus. XYZ mode only",
                 monarch="braille x", dotpad="braille x"),
    "axis_y": _k("Y", "y", "slices along Y from minus, and pressed again from plus. XYZ mode only",
                 monarch="braille y", dotpad="braille y"),
    "axis_z": _k("Z", "z", "slices along Z from plus, and pressed again from minus. XYZ mode only",
                 monarch="braille z", dotpad="braille z"),
    # Turn mode's quarter turns.
    "turn_pitch_up": _k("I", "i", "pitches the model up a quarter turn. Turn mode only"),
    "turn_pitch_down": _k("K", "k", "pitches the model down a quarter turn. Turn mode only"),
    "turn_yaw_left": _k("J", "j", "yaws the model left a quarter turn. Turn mode only"),
    "turn_yaw_right": _k("L", "l", "yaws the model right a quarter turn. Turn mode only"),
    "turn_roll_ccw": _k("U", "u", "rolls the model counterclockwise a quarter turn. Turn mode only"),
    "turn_roll_cw": _k("O", "o", "rolls the model clockwise a quarter turn. Turn mode only"),
    # Where am I.
    "where_am_i": _k("Period", ".", "says where you are: the axis you are on, what percentage the slice plane is at, the "
                     "origin, the render mode, zoom and model"),
    "origin": _k("Comma", ",", "says where the model's origin is on the display"),
    "reset": _k("0", "0", "resets the view to square, the slice plane to 50%, the zoom to 0 and "
                "the model to the centre"),
    "fit": _k("F", "f", "fits the current slice to the display"),
    # How the slice is drawn and laid out.
    "render_mode": _k("R", "r", "changes the render mode: Cut, X-Ray, Filled, Outline"),
    "layout": _k("T", "t", "changes the layout: Single, Side-by-Side, Slice Graph"),
    "scrollbar_overlay": _k("Left bracket", "[", "turns the scrollbars on or off, in the Single layout"),
    "graph_overlay": _k("Right bracket", "]", "turns the slice graph on or off, in the Single layout"),
    "graph_refresh": _k("G", "g", "moves the slice graph's anchor to the current slice plane position, "
                        "in the Slice Graph layout"),
    "graph_lock": _k("V", "v", "turns the slice graph lock on or off, in the Slice Graph layout"),
    # Zoom and pan.
    "zoom_in_10": _k("3", "3", "zooms in 10%"),
    "zoom_out_10": _k("2", "2", "zooms out 10%"),
    "zoom_in_1": _k("5", "5", "zooms in 1%"),
    "zoom_out_1": _k("4", "4", "zooms out 1%"),
    "pan_up": _k("W", "w", "moves the model up"),
    "pan_left": _k("A", "a", "moves the model left"),
    "pan_down": _k("S", "s", "moves the model down"),
    "pan_right": _k("D", "d", "moves the model right"),
    # Help and the rest.
    "shortcuts": _k("H", "h", "opens the list of keyboard shortcuts"),
    "shortcuts_question": _k("Question mark", "?", "opens the list of keyboard shortcuts"),
    "escape": _k("Escape", "escape", "closes a dialog; anywhere else, clears focus"),
    # The cursor lives only on the displays. The DotPad moves it with two
    # function keys and the two panning keys; one tap moves 1 pin, two quick taps
    # 5 and three 12. No lesson teaches it while it has nothing to do (#245
    # review); key help still names its buttons.
    "cursor_mode": _k(None, None, "changes the cursor: crosshair, guidelines, horizontal line, "
                      "vertical line, then off", monarch="Space", dotpad="dots 1 2 3 6"),
    "cursor_move": _k(None, None, "moves the cursor", monarch="the D-pad",
                      dotpad="the function and panning keys"),
    "cursor_left": _k(None, None, "moves the cursor left", monarch="D-pad left",
                      dotpad="the left panning key"),
    "cursor_right": _k(None, None, "moves the cursor right", monarch="D-pad right",
                       dotpad="the right panning key"),
    "cursor_up": _k(None, None, "moves the cursor up", monarch="D-pad up", dotpad="function key 1"),
    "cursor_down": _k(None, None, "moves the cursor down", monarch="D-pad down",
                      dotpad="function key 4"),
    # The display test pattern reads dots 1 and 4 as left and right instead of
    # moving the slice plane; the runner captures them while the pattern is up.
    "pattern_left": _k(None, None, "answers left in the display test", monarch="dot 1", dotpad="dot 1"),
    "pattern_right": _k(None, None, "answers right in the display test", monarch="dot 4",
                        dotpad="dot 4"),
    # The tutorial's own keys. tutorial.js handles them, not the viewer, and only
    # while the tutorial is running.
    "tutorial_continue": _k("N", "n", "goes on to the next step or lesson. Tutorial only"),
    "tutorial_back": _k("B", "b", "goes back one step. Tutorial only"),
    "tutorial_repeat": _k("C", "c", "repeats the current step. Tutorial only"),
    # "show Me". X, the other obvious letter, is the viewer's X axis.
    "tutorial_show_me": _k("M", "m", "shows how to do the current step, where it can. Tutorial only"),
}

# The command names the runner's captureDeviceKey hook reports for a Monarch or
# DotPad button, and the key each one is. Key help describes a device button
# through this, as "dot 4: moves the slice plane 10% deeper".
DEVICE_COMMANDS: dict[str, str | None] = {
    "dot1": "depth_shallower_10",
    "dot4": "depth_deeper_10",
    "cursor": "cursor_mode",
    "axis-x": "axis_x",
    "axis-y": "axis_y",
    "axis-z": "axis_z",
    "move-left": "cursor_left",
    "move-right": "cursor_right",
    "move-up": "cursor_up",
    "move-down": "cursor_down",
    "other": None,
}

UNMAPPED_DEVICE_BUTTON = "That button does nothing in the viewer."

# ---------------------------------------------------------------------------
# Checks. One helper per type, so a lesson reads as what it waits for.
# ---------------------------------------------------------------------------


def _manual() -> dict[str, Any]:
    return {"type": "manual"}


def _answer(correct: str | None) -> dict[str, Any]:
    return {"type": "answer", "correct": correct}


def _key(code: str) -> dict[str, Any]:
    return {"type": "key", "key": code}


def _keys(*codes: str) -> dict[str, Any]:
    return {"type": "keys", "keys": list(codes)}


def _state(field: str, **condition: Any) -> dict[str, Any]:
    return {"type": "state", "field": field, **condition}


def _changed(field: str) -> dict[str, Any]:
    return {"type": "changed", "field": field}


def _cycle(field: str, values: list[str], end: str) -> dict[str, Any]:
    return {"type": "cycle", "field": field, "values": list(values), "end": end}


def _sweep(axis: str, low: float, high: float) -> dict[str, Any]:
    return {"type": "sweep", "axis": axis, "from": low, "to": high}


def _edge() -> dict[str, Any]:
    return {"type": "edge"}


def _in_band(axis: str, band: str) -> dict[str, Any]:
    return {"type": "in_band", "axis": axis, "band": band}


def _mark_band(axis: str, band: str) -> dict[str, Any]:
    return {"type": "mark_band", "axis": axis, "band": band}


def _device(device: str) -> dict[str, Any]:
    return {"type": "device", "device": device}


def _ui(name: str, action: str) -> dict[str, Any]:
    return {"type": "ui", "name": name, "action": action}


def _settings_unchanged() -> dict[str, Any]:
    return {"type": "settings_unchanged"}


def _test_pattern(rounds: int) -> dict[str, Any]:
    return {"type": "test_pattern", "rounds": rounds}


def _cursor_in(landmark: str) -> dict[str, Any]:
    return {"type": "cursor_in", "landmark": landmark}


def _centred(landmark: str) -> dict[str, Any]:
    return {"type": "centred", "landmark": landmark}


def _zoom_by(min_increase: float) -> dict[str, Any]:
    return {"type": "zoom_by", "min_increase": min_increase}


def _model_changed() -> dict[str, Any]:
    return {"type": "model_changed"}


def _key_help(presses: int) -> dict[str, Any]:
    return {"type": "key_help", "presses": presses}


def _slicegraph_ready() -> dict[str, Any]:
    return {"type": "slicegraph_ready"}


def _all(*checks: dict[str, Any]) -> dict[str, Any]:
    return {"type": "all", "checks": list(checks)}


def _answers(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"label": label, "value": value} for label, value in pairs]


# The printable mug and its ten slice plaques. They are to move to a repository
# of their own in the cad-accessibility organization, which can hold a written
# tutorial later (#245 review); until it exists, this is the zip the app serves.
PRINT_FILES_LINK = {"label": "Printable tutorial mug and slice plaques (zip file)", "href": "/tutorial/prints.zip"}

# The guides for building the two controls the last two extras use, in the
# tangible-controls repository: written to be read with a screen reader.
BUILD_GUIDE_LINKS = [
    {
        "label": "How to make and use the orientation cube",
        "href": "https://github.com/cad-accessibility/tangible-controls/blob/master/docs/cube.md",
    },
    {
        "label": "How to make and use the slider",
        "href": "https://github.com/cad-accessibility/tangible-controls/blob/master/docs/slider.md",
    },
]


def _step(step_id: str, text: str, *, braille: str, check: dict[str, Any], done: str,
          hints: tuple[str, str, str], sr: str | None = None, on_fail: str | None = None,
          answers: list[dict[str, str]] | None = None, narrate: dict[str, str] | None = None,
          demo: dict[str, Any] | None = None, key_only: bool = False,
          when: dict[str, str] | None = None, store: str | None = None,
          requires: tuple[str, ...] = (), links: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Every step carries every field, so the runner never has to ask whether one
    is there. ``links`` are shown after the step's text, as links: the text
    itself is plain."""
    return {
        "id": step_id,
        "text": text,
        "sr": sr,
        "braille": braille,
        "check": check,
        "done": done,
        "hints": list(hints),
        "on_fail": on_fail,
        "answers": answers,
        "narrate": narrate,
        "demo": demo,
        "key_only": key_only,
        "when": when,
        "store": store,
        "requires": list(requires),
        "links": copy.deepcopy(links),
    }


def _lesson(lesson_id: str, part: str, title: str, *, minutes: int, steps: list[dict[str, Any]],
            requires: tuple[str, ...] = (),
            pose: dict[str, Any] | None = None, lock: bool = True) -> dict[str, Any]:
    return {
        "id": lesson_id,
        "part": part,
        "title": title,
        "minutes": minutes,
        "requires": list(requires),
        "pose": copy.deepcopy(pose),
        "lock": lock,
        "steps": steps,
    }


# The pose most lessons start from: the mug standing upright, slice plane through its
# middle from the right, so the U of its walls and base is on the display with
# the handle loop on the LEFT edge. It is study_protocol.VIEWER_DEFAULTS on the
# tutorial mug, except that the axis mode is left as the person has it: the view
# from the right is the same picture in Turn mode and XYZ mode.
DEFAULT_POSE: dict[str, Any] = {
    "model": "tutorial_mug",
    "view": "x-",
    "axis_mode": "keep",
    "depth": 50,
    "render_mode": "cut",
    "representation_mode": "single",
    "compose_scrollbar": True,
    "zoom": 0.0,
    "reset_pan": True,
}

# Lesson 8 starts in XYZ mode with the slice plane 20% in from the right, away
# from the handle. From the middle, pressing X was already inside the handle
# loop, and passed the step that asks for it before anyone had found it (#245
# review).
XYZ_POSE: dict[str, Any] = {**DEFAULT_POSE, "axis_mode": "xyz", "depth": 20}

# The six places the handle can be after a turn, as answer buttons.
_HANDLE_ANSWERS = _answers(
    ("Left", "left"), ("Right", "right"), ("Top", "top"), ("Bottom", "bottom"),
    ("Toward you", "toward_you"), ("Away from you", "away_from_you"),
)

# The hints for "where is the handle now?" after a turn. The third reads the
# answer off what the period key says in Turn mode, which names how Y runs on
# the display, or the side you are on when Y points at you. The handle
# points toward minus Y.
_TURN_HINTS = (
    "Feel around the mug for the handle loop, or a small separate piece of it.",
    "If there is no handle on the display at all, it points toward you or away from you.",
    ("Press {key:where_am_i}. If it says Y right, choose Left; Y left, choose Right; Y up, choose "
     "Bottom; Y down, choose Top. If it says View from the front, choose Toward you; View from the "
     "back, choose Away from you."),
)

# ---------------------------------------------------------------------------
# The lessons, in order. "Lesson N of 15" comes from this order.
# ---------------------------------------------------------------------------

LESSONS: list[dict[str, Any]] = [
    # -- Setup ---------------------------------------------------------------
    _lesson(
        "before_you_start", "setup", "Before you start", minutes=3, pose=DEFAULT_POSE,
        steps=[
            _step(
                "welcome",
                "The tutorial takes about an hour, and you can do it in parts. "
                "Choose Exit tutorial at any time to leave. "
                "If you return, the tutorial will start where you left off. "
                "Below the tutorial is an accessible webpage made up of sections "
                "corresponding to each of the capabilities introduced here. "
                "To start, which display are you using today?",
                sr="Exit tutorial pauses the tutorial. If you return, the tutorial will start where you "
                   "left off. To start, which display are you using today?",
                braille="Which display?",
                check=_answer(None),
                answers=_answers(("Monarch", "monarch"), ("DotPad", "dotpad"), ("No display", "none")),
                store="display",
                done="Noted. In the future, use Settings (main menu) to change display type.",
                hints=(
                    ("If you have neither a Monarch nor a DotPad, choose No display."),
                    ("You can select a display that you plan to use in the future."),
                    "Choose Monarch, DotPad or No display. If you are not sure, choose No display.",
                ),
            ),
            _step(
                "printed_mug",
                "The printed mug is optional. It is a 3D print of the practice mug, and it can help "
                "with some lessons. You can download and print it from the link after this text. "
                "Press Next (N) when ready.",
                sr="The printed mug is optional. The link after this text has the files to print it. "
                   "Press Next (N) when ready.",
                braille="Optional printed mug",
                check=_manual(),
                links=[PRINT_FILES_LINK],
                done="Next, check that the keys work.",
                hints=(
                    ("The files are a zip with the mug, ten flat plaques, each one slice across the "
                     "mug's height, and a short text file that says what each one is."),
                    "Most people do not have a printed mug. Every lesson works without one.",
                    "Press Next, or {key:tutorial_continue}, to go on.",
                ),
            ),
            _step(
                "keys_reach",
                "This tutorial teaches key commands and concepts. You can press "
                "{key:shortcuts_question} at any time for a list of every key command. The tutorial "
                "has four commands of its own: {key:tutorial_continue} is Next, {key:tutorial_back} "
                "is Back, {key:tutorial_repeat} is Repeat and {key:tutorial_show_me} is Show me. "
                "Some steps move forward by themselves when you complete them. Some wait for you to "
                "press Next (N). They always end with the sentence 'Press Next (N) when ready'. "
                "When a step moves forward by itself, the tutorial says what you did, then reads "
                "the next step. Key commands can conflict with a screen reader in browse mode, so "
                "switch to focus or forms mode, then press {key:where_am_i} now. If nothing "
                "happens, choose Keys aren't working.",
                sr="The tutorial's own keys are {key:tutorial_continue} for Next, {key:tutorial_back} "
                   "for Back, {key:tutorial_repeat} for Repeat and {key:tutorial_show_me} for Show me. "
                   "Some steps move forward by themselves when you complete them. Some wait for you "
                   "to press Next (N). They always end with the sentence 'Press Next (N) when ready'. "
                   "When a step moves forward by itself, the tutorial says what you did, then the "
                   "next step. Switch your screen reader to focus or forms mode, then press "
                   "{key:where_am_i}.",
                braille="Press period",
                check=_key("."),
                key_only=True,
                done="The keypress worked. From now on {key:tutorial_continue} is Next, "
                     "{key:tutorial_back} is Back, {key:tutorial_repeat} is Repeat and "
                     "{key:tutorial_show_me} is Show me.",
                on_fail="The keys work, but that was a different key. Press {key:where_am_i}.",
                hints=(
                    ("Your screen reader may be in browse mode, where it uses single keys to move "
                     "around the page. Switch it to focus mode or forms mode, then press "
                     "{key:where_am_i} again."),
                    ("NVDA: press NVDA+Space for focus mode, or NVDA+Shift+Space to turn single "
                     "letter navigation off. JAWS: press Insert+Z to turn the virtual cursor off. "
                     "VoiceOver: press Left Arrow and Right Arrow together, or VO-Shift-Q, to turn "
                     "Quick Nav off, and VO-Q to turn single-key Quick Nav off. Narrator: press "
                     "Caps Lock+Space to turn scan mode off."),
                    ("To pass just the next key to the viewer, press NVDA+F2 in NVDA or Insert+3 "
                     "in JAWS, then press {key:where_am_i}. If Single-key shortcuts is turned off "
                     "in Settings (main menu), under Keyboard, turn it back on, or choose Skip step."),
                ),
            ),
        ],
    ),
    _lesson(
        "connect", "setup", "Connect your display", minutes=3,
        steps=[
            _step(
                "connect",
                "Now connect your display. Turn it on, then choose Connect and Disconnect in the main "
                "menu, which shows Connect while nothing is connected. The browser shows a list of "
                "devices in a popup. Choose yours and press Enter.",
                sr="Turn your display on, choose Connect in the main menu, "
                   "and choose your display in the browser's popup.",
                braille="Connect display",
                check=_device("any"),
                done="Your display is connected.",
                hints=(
                    ("The browser's device list is a small dialog. Move through it with the arrow "
                     "keys and press Enter on your display; Escape closes it without connecting. "
                     "Connecting needs Chrome or Edge."),
                    ("The Monarch is listed only while it is plugged in by USB, turned on and in "
                     "Braille Terminal. The DotPad shows its own name on its braille line when it "
                     "turns on, and the same name is in the list."),
                    ("For the DotPad, turn Bluetooth on in your computer's settings, then choose "
                     "Connect again. For the Monarch, plug it in and turn it on, then choose "
                     "Connect again. If it still fails, choose Skip step and connect later "
                     "from Connect in the main menu."),
                ),
            ),
        ],
    ),
    _lesson(
        "test_pattern", "setup", "Test your display", minutes=3, requires=("display",),
        steps=[
            _step(
                "feel",
                "This checks that your display shows what the viewer sends. Each frame has a "
                "raised border round the whole display and a small square bump near one corner. "
                "Feel for the bump. If it is on the left half, press {key:pattern_left}; if it "
                "is on the right half, press {key:pattern_right}. You can also choose Left or "
                "Right in the tutorial area. Lift your hands off the display between frames so "
                "the pins can refresh. Two right answers in a row finish this lesson.",
                sr="Feel for the small bump near one corner. Left half, press {key:pattern_left}. "
                   "Right half, press {key:pattern_right}. Lift your hands between frames.",
                braille="Bump L or R?",
                check=_test_pattern(2),
                done="Your display shows the pattern correctly.",
                hints=(
                    ("Start at a corner of the border and trace along it. The bump is a solid "
                     "square of pins just inside one corner."),
                    "Only left or right matters, not top or bottom. The bump is 3 pins wide.",
                    ("Find the bump, then press {key:pattern_left} if it is on the left half or "
                     "{key:pattern_right} if it is on the right half."),
                ),
            ),
        ],
    ),
    # -- Core ----------------------------------------------------------------
    _lesson(
        "meet_the_mug", "core", "Meet the mug", minutes=5, pose=DEFAULT_POSE,
        steps=[
            _step(
                "what_a_slice_is",
                "This is the practice mug. The display does not show the whole mug at once. It "
                "shows one slice through it, as if you sliced the mug with a knife. "
                "Raised pins show where the mug touches the slice, lowered pins are air. The slice is not to scale; "
                "it is scaled so the whole mug fits in the middle of the display. Right now the "
                "slice plane touches the rim and "
                "the base, through the middle of the mug. If you have the printed mug, hold it "
                "upright with the handle to your left. Press Next (N) when ready.",
                sr="The display shows one slice through the mug, as if you sliced it with a knife. "
                   "It is scaled to fit the display. Press Next (N) when ready.",
                braille="Mug slice. N: next.",
                check=_manual(),
                done="Next, find the handle.",
                hints=(
                    ("Feel the whole display slowly, edge to edge. The slice of the mug is the "
                     "raised area in the middle."),
                    ("The slice plane goes through the middle of the mug, so you feel two walls, the base "
                     "joining them, and the handle on one side."),
                    "Press Next, or {key:tutorial_continue}, to go on.",
                ),
            ),
            _step(
                "where_handle",
                "The mug is a U shape: two walls and the base between them. Where is the "
                "handle? Feel around the mug, then choose Left, Right, Top or Bottom.",
                braille="Find the handle",
                # Found by touch, so skipped without a display: speech alone
                # would leave nothing to answer from but a guess.
                requires=("display",),
                check=_answer("computed:handle_side"),
                answers=_answers(("Left", "left"), ("Right", "right"), ("Top", "top"),
                                 ("Bottom", "bottom")),
                done="Yes. The handle is a loop with a hole through it, where your finger goes.",
                on_fail="Not that side. Feel around each side of the mug for a loop with a hole in it.",
                hints=(
                    "The handle sticks out from one wall, about halfway up.",
                    "Feel for a loop with a hole in it, joined to the wall in two places.",
                    ("From this side the handle is on the left of the mug, unless the mug has "
                     "moved since the lesson began. Choose Left."),
                ),
            ),
            _step(
                "where_am_i",
                "Whenever you want to know where you are, press {key:where_am_i}. Try it now.",
                braille="Status: .",
                check=_key("."),
                key_only=True,
                done="Now you know the side you are on, where the slice plane is, where the "
                     "origin is on the display and the render mode, zoom and model.",
                on_fail="That was a different key. Press {key:where_am_i}.",
                hints=(
                    ("The answer is spoken. On the DotPad a short form also appears on the "
                     "braille line."),
                    "The Period key is near the bottom right of the main keyboard.",
                    "Press {key:where_am_i} now.",
                ),
            ),
            _step(
                "reset",
                "Reset moves the slice plane to 50%, zooms out to fit the mug "
                "to screen and centres the mug. Use it whenever you get lost. Try {key:reset} now.",
                sr="Reset fits the mug to screen, centers, and moves the slice plane to 50%. "
                   "Try {key:reset} now.",
                braille="Reset pos: 0",
                check=_key("0"),
                key_only=True,
                done="Reset. The view is centered, the slice plane is 50% and the zoom is 0.",
                on_fail="That was a different key. Press {key:reset}.",
                hints=(
                    "Reset is the zero key, on the top row or the number pad.",
                    ("The Reset Position button in the Zoom section does the same, but try "
                     "the key."),
                    "Press {key:reset}.",
                ),
            ),
        ],
    ),
    _lesson(
        "depth", "core", "Moving the slice plane", minutes=6, pose=DEFAULT_POSE,
        steps=[
            _step(
                "worked_example",
                "First, a worked example. Each time you press Next, the tutorial moves the "
                "slice plane for you, from near the far side of the mug to near the side closest to you. "
                "Feel each slice and listen to what it is. This demo visits five depths. "
                "Press Next (N) when ready.",
                braille="N demos traverse",
                check=_manual(),
                demo={"frames": [
                    # The slice at 5% and at 95% spans only the top two thirds of the
                    # mug: the wall flares out toward the rim, so near its edge the
                    # slice plane misses the base (#245 review).
                    {"cut_percent": 5,
                     "say": "Near the far side the slice plane only grazes the wall, where the mug is "
                            "widest. You feel one solid piece, widest at the rim and narrowing as "
                            "it goes down, and it stops about a third of the way above the base."},
                    {"cut_percent": 25,
                     "say": "A quarter of the way in, the slice plane passes through the hollow: two walls "
                            "and the base, a U shape."},
                    {"cut_percent": 50,
                     "say": "The middle. The U is at its widest, and the handle joins it on the "
                            "left: a loop with a hole for your finger."},
                    {"cut_percent": 75,
                     "say": "Past the handle, the U again. The mug is the same on both sides of "
                            "its middle."},
                    {"cut_percent": 95,
                     "say": "Near the side closest to you, the same shape again: the wall, grazed "
                            "from this side, one piece from the rim that stops above the base."},
                ]},
                done="We traversed the whole mug, one slice at a time.",
                hints=(
                    "Press Next for the next slice. We will visit five landmarks.",
                    ("Feel how the shape changes from slice to slice: solid, a U, a U with the "
                     "handle."),
                    "Press Next, or {key:tutorial_continue}, until the example ends.",
                ),
            ),
            _step(
                "to_the_far_side",
                "Now try it yourself. Press {key:depth_deeper_10} to move it 10% deeper, away "
                "from you, and {key:depth_shallower_10} to move it 10% toward you. Press "
                "{key:depth_deeper_10} until the slice plane reaches the far side of the mug. "
                "Remember: if you get stuck, Show me demonstrates and says what is there.",
                sr="Press {key:depth_deeper_10} until the slice plane reaches the far side of the mug.",
                braille="In dot 4, Out dot 1",
                check=_edge(),
                done="You are at the edge of the mug. Past here there is nothing to slice.",
                on_fail="That went back toward you. Keep going one way until the number stops "
                        "changing.",
                hints=(
                    ("Deeper means away from you, into the mug. If you go too far, press "
                     "{key:depth_shallower_10}."),
                    "Each press is a tenth of the mug, so it takes at most ten presses.",
                    "Press {key:depth_deeper_10} again and again until the number stops changing.",
                ),
            ),
            _step(
                "mark_handle",
                "Now find the handle. Press {key:depth_shallower_10} until you feel the handle "
                "loop on the left. Press Next (N) when ready.",
                braille="Find the handle",
                check=_mark_band("x", "handle_loop"),
                done="That is the handle. It is present in the middle part of the mug.",
                on_fail="Not there yet. The handle is only in the middle part of the mug.",
                hints=(
                    ("The handle is in the middle of the mug, so from the far side you come back "
                     "about halfway."),
                    "Feel for the loop on the left of the mug after each press. When it is there, stop.",
                    ("Press {key:reset} to bring the slice plane to the middle, which is inside the handle, "
                     "then press Next."),
                ),
            ),
            _step(
                "fine_steps",
                "To move to either side, use {key:depth_near} (0%) "
                "and {key:depth_far} (100%). For fine work, "
                "{key:depth_deeper_1} and {key:depth_shallower_1} move 1% at a time. "
                "Press {key:depth_near}, then {key:depth_far}.",
                sr="Press {key:depth_near} for the surface nearest you, then {key:depth_far} for the "
                   "far side.",
                braille="Home, then End",
                check=_keys("home", "end"),
                done="Those are all the depth keys.",
                on_fail="That was a different key. Press {key:depth_near}, then {key:depth_far}.",
                hints=(
                    "On many laptops Home and End are Fn with Left Arrow and Fn with Right Arrow.",
                    "The order does not matter but you do need to try both.",
                    "Press {key:depth_near}, then press {key:depth_far}.",
                ),
            ),
        ],
    ),
    _lesson(
        "render_modes", "core", "Render modes", minutes=4, pose=DEFAULT_POSE,
        steps=[
            _step(
                "cycle",
                "Render modes show different views of the slice plane: Cut, X-Ray, Filled "
                "and Outline. Press {key:render_mode} four times, stopping to feel each one, to "
                "go through all four and come back to Cut.",
                sr="Press {key:render_mode} four times, feeling each one, to go through Cut, X-Ray, "
                   "Filled and Outline and back to Cut.",
                braille="R through the modes",
                check=_cycle("render_mode", ["cut", "xray", "filled", "outline"], "cut"),
                narrate={
                    "xray": "X-Ray: the outline of the slice plus the edges hidden behind it: the "
                            "rim, the inside wall and the foot.",
                    "filled": "Filled: the solid profile of the whole mug from this side. The "
                              "hollow inside disappears.",
                    "outline": "Outline: only the border of that profile.",
                    "cut": "Cut: the face of the slice plane, where the knife went through the mug. This "
                           "is the mode for most work.",
                },
                done="Back to Cut, the mode the rest of the tutorial uses.",
                hints=(
                    "Each press moves to the next mode, and the viewer says its name.",
                    "From Cut the order is X-Ray, Filled, Outline, then Cut again.",
                    "Press {key:render_mode} until you hear Cut again: four presses from Cut.",
                ),
            ),
        ],
    ),
    _lesson(
        "zoom_and_move", "core", "Zoom and move", minutes=5, pose=DEFAULT_POSE,
        steps=[
            _step(
                "zoom_in",
                "Zoom in 10% with {key:zoom_in_10} or 1% with {key:zoom_in_1}, and out 10% with {key:zoom_out_10}. Press "
                "{key:zoom_in_10} twice.",
                braille="Zoom twice",
                check=_zoom_by(0.2),
                done="Zoomed in 20%. The mug now fills the display from top to bottom.",
                hints=(
                    "Zoom makes the mug bigger so you can feel details.",
                    "Each press of {key:zoom_in_10} adds 10%.",
                    "Press {key:zoom_in_10} two times.",
                ),
            ),
            _step(
                "centre_handle",
                "To pan the mug, press {key:pan_up}, {key:pan_left}, {key:pan_down} or "
                "{key:pan_right}: up, left, down or right, a quarter of the display at a time. "
                "The handle is on the {handle_side} side of the mug. To bring it to the center, "
                "press {pan_toward_handle}.",
                sr="Move the mug with {key:pan_up}, {key:pan_left}, {key:pan_down} and "
                   "{key:pan_right} until the tutorial says the handle is in the middle.",
                braille="Handle to center",
                check=_centred("handle"),
                key_only=True,
                done="Handle centered",
                hints=(
                    ("The keys move the object, not the window."),
                    ("Each press moves the mug a quarter of the display."),
                    ("With the handle near the left edge, press {key:pan_right}. With it near the "
                     "right edge, press {key:pan_left}."),
                ),
            ),
            _step(
                "zoom_out",
                "Now zoom out. Press {key:zoom_out_10} (10%) or {key:zoom_out_1} (1%) repeatedly or press {key:reset}.",
                sr="Press {key:zoom_out_10} repeatedly or {key:reset} until the zoom is 0.",
                braille="Zoom out to 0",
                check=_state("zoom", lte=0.001),
                done="Zoom is 0.",
                hints=(
                    "Zoom cannot go below 0, so extra presses do no harm.",
                    "You zoomed in twice, so press {key:zoom_out_10} twice.",
                    "Press {key:zoom_out_10} twice, or press {key:reset} once.",
                ),
            ),
        ],
    ),
    _lesson(
        "axes", "core", "X, Y and Z", minutes=10, pose=XYZ_POSE,
        steps=[
            _step(
                "about_axes",
                "This lesson uses XYZ mode, where you choose an axis instead of a face. "
                "For example, if the mug stands on the table, the Z axis points up from the table. "
                "The keys are {key:axis_x} for X, {key:axis_y} for Y and {key:axis_z} for Z. "
                "On a display, enter the braille letter X, Y or Z to do the same thing. "
                "For some views (like fill, outline and x-ray), direction matters. "
                "If you view the mug from above, fill shows everything below the slice plane. "
                "If you view it from below, fill shows everything above the slice plane. To switch, "
                "press the same axis key twice. Slice depth is always a percentage from 0 on any "
                "axis, regardless of the direction you are viewing from. Press Next (N) when ready.",
                sr="This lesson uses XYZ mode: you choose the axis to slice along, and the same key "
                   "again gives the other side. The slice plane's number counts from the model's origin, "
                   "the middle of the base. Press Next (N) when ready.",
                braille="Press X Y or Z",
                check=_manual(),
                done="Next, slice along Z.",
                hints=(
                    ("Z is up. The base of the mug is at the low end of Z, and the rim at the "
                     "high end."),
                    "X and Y lie flat, like the edges of a sheet of paper on the table.",
                    "Press Next, or {key:tutorial_continue}, to try the axis keys.",
                ),
            ),
            _step(
                "cut_z",
                "Press {key:axis_z}. The slice plane is now parallel to the table, and "
                "the viewer says Z from plus, X right, Y up: you are above the mug, facing down. "
                "On your display, X increases to the right and Y increases up the display. "
                "This is indicated with a braille X at the display's right and a braille Y at its top.",
                sr="Press {key:axis_z} to slice along Z, across the mug, parallel to the table.",
                braille="Slice along Z",
                check=_state("cut_axis", equals="z"),
                done="You are slicing along Z. The slice is a ring, the wall of the mug, with a small "
                     "separate piece beside it, the handle.",
                hints=(
                    "Z is the axis that points up from the table.",
                    "The viewer says Z from plus when you select Z once.",
                    "Press {key:axis_z} to switch from plus to minus.",
                ),
            ),
            _step(
                "origin",
                "We call the place where the three axes meet the origin. At the origin, depth is 0 "
                "along all axes. A 3D model has a position in space in XYZ mode. The mug base is "
                "centered on the origin in X and Y. Press {key:origin} to hear where the origin is on the display: how far across from the left "
                "edge, and how far up from the bottom. In XYZ mode the display also marks the origin with a small hollow square.",
                braille="Press ,",
                check=_key(","),
                key_only=True,
                done="In this mug the origin is in the middle of the base. Because of the handle, it is not centered in the mug.",
                on_fail="That was a different key. Press {key:origin}.",
                hints=(
                    "The origin is the point every coordinate is measured from.",
                    "Feel for the hollow square to check its position.",
                    "Press {key:origin}.",
                ),
            ),
            _step(
                "cut_x_handle",
                "In XYZ mode the slice plane's position is measured from the origin, so 0 no longer "
                "means the far side of the model. Press {key:where_am_i} at any time to check where "
                "the slice plane is. Now press {key:axis_x} to slice along X. The slice plane starts "
                "away from the handle, so press {key:depth_deeper_10} until you feel the handle loop.",
                sr="Press {key:axis_x} to slice along X, then {key:depth_deeper_10} until you feel the "
                   "handle loop.",
                braille="Find handle on X",
                check=_all(_state("cut_axis", equals="x"), _in_band("x", "handle_loop")),
                done="That is the handle loop.",
                hints=(
                    "From the X axis, the slice plane divides the mug vertically across the handle.",
                    ("Each press moves the slice plane a tenth of the way through the mug, and the "
                     "handle is in the middle."),
                    ("Press {key:axis_x} until the viewer says X from plus, then press "
                     "{key:depth_deeper_10} three times."),
                ),
            ),
            _step(
                "cut_y_arms",
                "Press {key:axis_y}. If the viewer says Y from minus you are facing the mug from the front, "
                "with the handle pointing at you. Press {key:depth_shallower_10} to move the slice plane "
                "toward you until you feel two small separate pieces. The slice plane is intersecting the handle "
                "in two places because it curves outward (crossing the plane) and then back (crossing it again).",
                sr="Press {key:axis_y}, then {key:depth_shallower_10} until you feel two small "
                   "separate pieces, the arms of the handle.",
                braille="Find the handle arms",
                check=_all(_state("cut_axis", equals="y"), _in_band("y", "handle_arms")),
                done="You are slicing through the handle.",
                hints=(
                    "From the front the handle points at you, so it is on the near side of the mug.",
                    "The arms are near the end of the handle, most of the way toward you.",
                    ("Press {key:axis_y} until you hear Y from minus, then press "
                     "{key:depth_shallower_10} three times."),
                ),
            ),
            _step(
                "which_axis_loop",
                "Which axis showed the handle from the side, so you can feel its whole curve at once?",
                braille="Axis: handle side?",
                check=_answer("x"),
                answers=_answers(("X", "x"), ("Y", "y"), ("Z", "z")),
                done="Yes, X. Viewed from the X axis, the slice plane cuts through the handle from the side.",
                on_fail="Not that one. Think of the slice plane that showed you the handle's full curve.",
                hints=(
                    "Along Z you felt rings, and along Y the two small arms.",
                    "The handle's curve is only on screen when the slice plane is in the middle of the mug.",
                    "Choose X.",
                ),
            ),
            _step(
                "handle_sign",
                "Which way does the handle currently point: toward positive Y or toward negative Y?",
                braille="Which way on Y?",
                check=_answer("negative"),
                answers=_answers(("Positive Y", "positive"), ("Negative Y", "negative")),
                done="Yes. The handle points toward negative Y, the low end of the Y numbers.",
                on_fail="Think of where the handle's arms were on the Y numbers.",
                hints=(
                    "The {key:where_am_i} key may help.",
                    "Viewing from X plus, the viewer said Y right, and the handle was on the left.",
                    "Choose Negative Y.",
                ),
            ),
            _step(
                "keep_mode",
                "You can stay in XYZ mode, or use Turn mode (pitch, roll and yaw). You can change again "
                "in Settings (main menu), under Axis Mode. Which do you prefer?",
                sr="Keep XYZ mode, or use Turn mode, which turns the model with pitch, roll and "
                   "yaw? Change in Settings (main menu) at any time.",
                braille="Keep XYZ mode?",
                check=_answer(None),
                answers=_answers(("Keep XYZ mode", "xyz"), ("Use Turn mode", "turn")),
                store="axis_choice",
                done="Done. You can change this again in Settings (main menu).",
                hints=(
                    ("XYZ mode helps if you are modeling, or think in coordinates. Turn mode "
                     "is helpful if you think about turning the object in your hands."),
                    "Both can be useful at different times; Use Settings (main menu) to switch between them.",
                    "Choose Keep XYZ mode or Use Turn mode.",
                ),
            ),
            _step(
                "turn_tip",
                "Turn mode rotates by 90 degrees: {key:turn_pitch_up} "
                "and {key:turn_pitch_down} pitch it up and down, {key:turn_yaw_left} and "
                "{key:turn_yaw_right} yaw it left and right, and {key:turn_roll_ccw} and "
                "{key:turn_roll_cw} roll it counterclockwise and clockwise. Press {key:turn_pitch_up} once. Where is the "
                "handle now?",
                sr="Turn mode is on. Press {key:turn_pitch_up} once to pitch the mug up. Where is "
                   "the handle now?",
                braille="Pitch up: handle?",
                check=_answer("computed:handle_side"),
                answers=_HANDLE_ANSWERS,
                when={"stored": "axis_choice", "equals": "turn"},
                done="Correct.",
                on_fail="Feel the edges for the handle, or press {key:where_am_i}.",
                hints=_TURN_HINTS,
            ),
            _step(
                "turn_yaw",
                "Press {key:turn_yaw_left} once to yaw the mug left. Where is the handle now?",
                braille="Yaw left: handle?",
                check=_answer("computed:handle_side"),
                answers=_HANDLE_ANSWERS,
                when={"stored": "axis_choice", "equals": "turn"},
                done="Correct.",
                on_fail="Feel the edges for the handle, or press {key:where_am_i}.",
                hints=_TURN_HINTS,
            ),
            _step(
                "turn_roll",
                "Press {key:turn_roll_cw} once to roll the mug clockwise. Where is the handle now?",
                braille="Roll: handle now?",
                check=_answer("computed:handle_side"),
                answers=_HANDLE_ANSWERS,
                when={"stored": "axis_choice", "equals": "turn"},
                done="Correct. Roll keeps the same side of the mug toward you and rotates it on the "
                     "display.",
                on_fail="Feel the edges for the handle, or press {key:where_am_i}.",
                hints=_TURN_HINTS,
            ),
        ],
    ),
    # -- Wrap-up -------------------------------------------------------------
    _lesson(
        "reset_and_fit", "wrapup", "Reset and fit", minutes=2, pose=DEFAULT_POSE,
        steps=[
            _step(
                "reset",
                "The tutorial has zoomed in and moved the mug off the middle. Press {key:reset} to "
                "return to center, fit the mug to the display and move the slice plane back on every "
                "axis (50% in Turn mode; the origin in XYZ mode).",
                sr="Press {key:reset} to return to center.",
                braille="Press 0 to reset",
                check=_all(_key("0"), _state("zoom", lte=0.001)),
                key_only=True,
                done="The mug is centered and fits the screen.",
                hints=(
                    "Reset helps when you are lost.",
                    ("Reset centers the origin and fits the mug "
                     "to screen. Use it whenever you get lost. "
                     "The Reset Position button in the Zoom section does the same."),
                    "Press {key:reset}.",
                ),
            ),
            _step(
                "fit",
                "Fit is similar to reset, but for the current slice. "
                "It adjusts the zoom and pans so the current slice fills the display. Press "
                "{key:fit}.",
                braille="Press F to fit",
                check=_key("f"),
                key_only=True,
                done="Fitted. The slice fills the display.",
                on_fail="That was a different key. Press {key:fit}.",
                hints=(
                    "Fit helps after a turn or a zoom leaves the slice small or off to one side.",
                    "Reset zooms all the way out; Fit zooms in on the slice you have.",
                    "Press {key:fit}.",
                ),
            ),
        ],
    ),
    _lesson(
        "help_and_settings", "wrapup", "Help and coming back", minutes=5,
        steps=[
            _step(
                "shortcuts",
                "The shortcuts key, {key:shortcuts}, opens a list of every keyboard shortcut, grouped by "
                "heading. Help, in the main menu, opens the same list. Press "
                "{key:shortcuts} now. When you are ready, press {key:escape} or choose "
                "Close to come back.",
                sr="Press {key:shortcuts} to open the list of keyboard shortcuts, then {key:escape} "
                   "or Close to come back.",
                braille="Open and close help",
                check=_ui("shortcuts-dialog", "close"),
                done="You are back from the shortcuts list.",
                hints=(
                    "The list is a dialog. Move through it by heading with your screen reader.",
                    "It also opens with {key:shortcuts_question}.",
                    "Press {key:shortcuts}, then press {key:escape}.",
                ),
            ),
        ],
    ),
    _lesson(
        "mug_detective", "wrapup", "Mug detective", minutes=6, pose=DEFAULT_POSE,
        steps=[
            _step(
                "which_axis",
                "Time to practice. The tutorial has moved the slice plane to an unknown axis and "
                "position without saying which. You are in {axis_mode_name} mode. Which axis are "
                "you on?",
                sr="Which axis are you on?",
                braille="Which axis?",
                check=_answer("computed:cut_axis"),
                answers=_answers(("X", "x"), ("Y", "y"), ("Z", "z")),
                done="Right.",
                on_fail="Press {key:where_am_i} and listen to the first words.",
                hints=(
                    ("Rings mean a slice across the mug, parallel to the table. A U shape means a "
                     "slice from the rim to the base."),
                    ("In XYZ mode, {key:where_am_i} names the axis first. In Turn mode it names the "
                     "side you face: from above or below means Z, from the front or the back means "
                     "Y, and from the right or the left means X."),
                    ("Press {key:where_am_i} and listen to its first sentence. X, or from the right "
                     "or the left: choose X. Y, or from the front or the back: choose Y. Z, or from "
                     "above or below: choose Z."),
                ),
            ),
            _step(
                "back_to_handle",
                # The setup never leaves the slice plane inside the loop, so there is always
                # something to find (#245 review).
                "Now find the handle in the view that shows the whole curve. Press Next (N) when "
                "ready.",
                braille="Back to the loop",
                check=_mark_band("x", "handle_loop"),
                done="Found it.",
                on_fail="Not there yet. You need to slice along X, through the middle of the mug.",
                hints=(
                    "The handle loop is there only when you slice along X through the middle.",
                    ("In XYZ mode, {key:axis_x} views along X. In Turn mode, a yaw turns the mug "
                     "until {key:where_am_i} says View from the right or the left, which is a view "
                     "along X."),
                    ("In XYZ mode, press {key:axis_x}, then {key:reset}, then Next. In Turn mode, "
                     "press {key:turn_yaw_left} until {key:where_am_i} says View from the right or "
                     "the left, then {key:reset}, then Next."),
                ),
            ),
            _step(
                "floor_top",
                "Now slice across the mug from above, and find the ring just above the floor: going "
                "down the mug, it is the last ring before the slice turns solid. Press Next (N) when "
                "ready.",
                braille="Ring above the floor",
                check=_mark_band("z", "above_floor"),
                requires=("display",),
                done="That is just above the floor. A little deeper, and the floor is solid.",
                on_fail="Not there yet. You want the last ring before the solid floor.",
                hints=(
                    ("Near the bottom of the mug the slice is a solid disc: the floor. Just above "
                     "it, the middle is hollow and you feel a ring."),
                    ("In XYZ mode, press {key:axis_z} until you hear Z from plus. In Turn mode, "
                     "press {key:turn_pitch_down} until {key:where_am_i} says View from above."),
                    ("From above, press {key:depth_deeper_10} until the slice turns solid, then "
                     "{key:depth_shallower_1} one step at a time until it is a ring again, and "
                     "press Next."),
                ),
            ),
            _step(
                "wall_vs_bar",
                "Select the view where the handle bar is a small separate piece beside the ring. "
                "Measure both in the direction from the middle of the mug toward the handle. "
                "Which is thicker: the wall of the ring on the handle side, or the handle bar?",
                sr="Which is thicker, the wall of the ring on the handle side, or the handle bar?",
                braille="Wall or bar thicker?",
                check=_answer("wall"),
                answers=_answers(("The wall", "wall"), ("The handle bar", "bar"),
                                 ("About the same", "same")),
                requires=("display",),
                done="The wall is thicker than the handle bar.",
                on_fail="Not quite. Count the raised pins across each, in the direction toward the "
                        "handle.",
                hints=(
                    "The bar is the small piece on its own; the wall is the ring next to it.",
                    ("Count the pins across the wall on the handle side, then across the bar, both "
                     "toward the handle."),
                    "The wall is thicker. Choose The wall.",
                ),
            ),
            _step(
                "handle_joins",
                # Asked the way Jen put it (#245 review): where it joins was harder to
                # answer than how many places.
                "Does the handle connect to the mug in one place or two?",
                braille="One place or two?",
                check=_answer("two"),
                answers=_answers(("One place", "one"), ("Two places", "two")),
                requires=("display",),
                done="Yes, two: it connects at the top and at the bottom",
                on_fail="Not quite. Follow the handle from one end to the other.",
                hints=(
                    ("Along Z, the handle is a separate piece in the middle of the mug. Notice "
                     "where it stops being separate."),
                    ("Slice along X through the middle, as in the depth lesson: the loop meets the "
                     "wall of the U in two places."),
                    "It joins at the top and at the bottom. Choose Two places.",
                ),
            ),
        ],
    ),
    # The model chooser and upload are what this lesson teaches, so it unlocks
    # them.
    _lesson(
        "your_own_model", "wrapup", "Your own model", minutes=5, lock=False,
        steps=[
            _step(
                "pick_model",
                "Choose a model from the Model list, or upload an STL or STEP file of your own. "
                "An upload lasts until this tab closes or reloads.",
                braille="Pick a model",
                check=_model_changed(),
                done="Your model is loaded.",
                hints=(
                    ("The Model section comes after Zoom. The list opens with Alt+Down Arrow; "
                     "choose with the arrow keys and press Enter."),
                    "An upload is processed on the server, which can take a moment for a big file.",
                    ("Go to the Model list, open it with Alt+Down Arrow, choose any model other "
                     "than the tutorial mug, and press Enter."),
                ),
            ),
            _step(
                "export",
                "Export Current View as Image, in the Export section, saves the current slice as "
                "a picture file, for a sighted helper or a document. Choose it now.",
                braille="Export an image",
                check=_ui("export", "click"),
                done="The viewer says slice exported as png when the file is saved.",
                hints=(
                    "The Export section is near the end of the page, in the Visual previews area.",
                    "The file is a PNG, named after the view, the depth and the render mode.",
                    "Choose the Export Current View as Image button.",
                ),
            ),
            # Open to everyone, so that someone without the cube or the slider
            # knows where the guides to making them are, and that the two extras
            # for them can be skipped (#245 review).
            _step(
                "build_controls",
                "Two of the extras after the main tutorial use controls you build yourself: an "
                "orientation cube, which changes the view when you turn it, and a slider, which "
                "moves the slice plane. The guides to making them are on GitHub, in the "
                "tangible-controls repository, at the links after this text, and they are written to "
                "be read with a screen reader. The cube's guide has its parts list, the print files "
                "on each release, how to put it together and how to connect it. The slider's guide "
                "has its parts list and how to set it up and connect it. If you do not have them, "
                "you can skip those two extras. Press Next (N) when ready.",
                sr="Two extras use a cube and a slider you build yourself, and the links after this "
                   "text are the guides to making them. Without them, you can skip those two "
                   "extras. Press Next (N) when ready.",
                braille="Guides: cube, slider",
                check=_manual(),
                links=BUILD_GUIDE_LINKS,
                done="Next, the end of the main tutorial.",
                hints=(
                    ("The cube needs a 3D printer and a WitMotion motion sensor. The slider is an "
                     "Adafruit Slider Trinkey, a small board with a USB plug."),
                    "Both need Chrome or Edge, which can reach Bluetooth and serial devices.",
                    "Press Next, or {key:tutorial_continue}, to go on.",
                ),
            ),
            _step(
                "props_and_end",
                "One last thing: if you want the printed mug, the link after this text has the files "
                "for a 3D printer. That is the end of the main tutorial. Three extra lessons follow, "
                "on the slice graph, the cube and the slider: Next starts them, and Exit tutorial "
                "leaves them for another time. To return or redo any lesson, choose the Tutorial "
                "button in the main menu and pick it from the list of lessons, or use Choose a "
                "lesson in the keyboard shortcuts list; Start over runs the whole tutorial again. "
                "Press Next (N) when ready.",
                sr="That is the end of the tutorial. Three extra lessons follow: Next starts them, "
                   "and Exit tutorial leaves them. To redo any lesson, choose the Tutorial button in "
                   "the main menu. Press Next (N) when ready.",
                braille="Tutorial complete",
                check=_manual(),
                links=[PRINT_FILES_LINK],
                done="Exit tutorial (button) to leave",
                hints=(
                    ("The files are a zip with the mug, ten flat plaques, each one slice across the "
                     "mug's height, and a short text file that says what each one is."),
                    "The extras need the slice graph layout, the WitMotion cube or the Trinkey slider.",
                    "Press Next, or {key:tutorial_continue}, for the extras, or choose Exit tutorial.",
                ),
            ),
        ],
    ),
    # -- Extras ----------------------------------------------------------------
    # After the end of the tutorial itself, for anyone who wants them (#245
    # review). Layout radios are what the first teaches, so it does not lock them.
    # It walks through the slice graph before asking for G and V: re-anchoring
    # means nothing until the graph does.
    _lesson(
        "layout_and_graph", "extras", "Layouts and the slice graph", minutes=8, lock=False,
        steps=[
            _step(
                "layouts",
                "Layouts change how the display is laid out: Single, Side-by-Side and Slice Graph. "
                "Press {key:layout} until you reach Slice Graph, feeling each layout on the way. "
                "The same three are radio buttons after the render modes.",
                sr="Press {key:layout} until you reach Slice Graph, feeling each layout on the way.",
                braille="T to Slice Graph",
                check=_cycle("layout_mode", ["single", "side-by-side", "slice-graph"], "slice-graph"),
                narrate={
                    "single": "Single: one slice, with scrollbars along the bottom and right edges "
                              "when you are zoomed in.",
                    "side-by-side": "Side-by-Side: your slice on the right, and on the left the whole "
                                    "mug from another side, with a line where the slice plane passes.",
                    "slice-graph": "Slice Graph: your slice, with a graph along the bottom rows.",
                },
                done="This is the Slice Graph layout.",
                hints=(
                    "Each press moves to the next layout, and the viewer says its name.",
                    "From Single the order is Side-by-Side, then Slice Graph.",
                    "Press {key:layout} until you hear Slice Graph: two presses from Single.",
                ),
            ),
            _step(
                "graph_ready",
                "The first slice graph for a model can take 20 to 40 seconds to build. There is "
                "nothing to press: the tutorial tells you when it is ready.",
                sr="The graph can take 20 to 40 seconds. The tutorial says when it is ready.",
                braille="Wait for the graph",
                check=_slicegraph_ready(),
                done="The slice graph is ready.",
                hints=(
                    "The graph arrives by itself.",
                    ("The graph is only worked out in the Slice Graph layout. If you left it, "
                     "come back to it."),
                    ("Stay in Slice Graph and wait. If you left it, press {key:layout} until you "
                     "hear Slice Graph."),
                ),
            ),
            _step(
                "graph_read",
                "Feel along the bottom rows of the display, under the raised line across it: that "
                "is the graph. From left to right it traverses the mug, from the side nearest "
                "you to the far side, one percentage point for each depth. It compares every slice with one "
                "anchor slice, the slice plane you had when you came into Slice Graph: where the line is "
                "high, the slice at that depth is very different from the anchor, and where it is "
                "low, much the same. The single upright line across the graph is where your slice "
                "plane is now. Find it. Press Next (N) when ready.",
                sr="The bottom rows are the graph: left is the side nearest you, right the far side, "
                   "and the upright line is your slice plane. Press Next (N) when ready.",
                braille="Feel the graph",
                check=_manual(),
                done="That upright line shows the position of your slice plane.",
                hints=(
                    "The graph is in the bottom rows, under a raised line that runs across the display.",
                    "The upright line crosses every row of the graph; nothing else in it does.",
                    "Find the upright line in the bottom rows, then press Next.",
                ),
            ),
            _step(
                "graph_move",
                "Press {key:depth_deeper_10} two or three times and feel the upright line move to "
                "the right, deeper into the mug. The graph itself stays as it is: it is locked to "
                "the anchor.",
                braille="Move the slice plane",
                check=_changed("depth"),
                done="The line follows your slice plane, and the graph does not change.",
                hints=(
                    "Deeper moves the upright line to the right.",
                    "Each press moves the slice plane a tenth of the way through the mug.",
                    "Press {key:depth_deeper_10}, then feel where the upright line is now.",
                ),
            ),
            _step(
                "graph_anchor",
                "Press {key:graph_refresh} to make the slice plane you are on now the anchor. The graph is "
                "worked out again against it: low around your slice plane, where the slices are much like "
                "it, and higher where they differ.",
                braille="G: new anchor",
                check=_key("g"),
                key_only=True,
                done="The viewer said the view and the depth of the new anchor.",
                on_fail="That was a different key. Press {key:graph_refresh}.",
                hints=(
                    "Only in the Slice Graph layout does this key do anything.",
                    "Afterwards the graph is at its lowest around your slice plane.",
                    "Press {key:graph_refresh}.",
                ),
            ),
            _step(
                "graph_lock",
                "The graph is locked, so it stays put while you move the slice plane. Press "
                "{key:graph_lock} to unlock it: then it recalculates at your current slice plane."
                "Press {key:graph_lock} again to lock it. Settings has the same "
                "lock, as Lock slice graph, and Graph Mode, which shows the area of each slice "
                "instead of the difference from the anchor.",
                sr="Press {key:graph_lock} to unlock the graph, so it follows your slice plane, and again to "
                   "lock it.",
                braille="V: unlock, lock",
                # Off and back on, so the graph is left locked as the lesson found
                # it. Settings' Lock slice graph counts too.
                check=_cycle("slicegraph_locked", [True, False], True),
                done="The graph is locked again, and stays put while you move the slice plane.",
                hints=(
                    ("Stay in the Slice Graph layout; anywhere else this key says not in "
                     "slice-graph mode."),
                    "Unlocked, each move of the slice plane works the graph out again against the new slice.",
                    "Press {key:graph_lock} once to unlock, and once more to lock.",
                ),
            ),
            _step(
                "overlays",
                "Press {key:layout} once more to come back to Single. In Single, two overlays turn "
                "on and off: press {key:scrollbar_overlay} for the scrollbars, which appear only "
                "when you are zoomed in, and {key:graph_overlay} for the slice graph along the "
                "bottom. Press each one once.",
                sr="Press {key:layout} to come back to Single, then {key:scrollbar_overlay} and "
                   "{key:graph_overlay} once each.",
                braille="Single, then [ and ]",
                check=_all(_state("layout_mode", equals="single"), _keys("[", "]")),
                key_only=True,
                done="Those are the overlays. Changing the layout sets them back.",
                hints=(
                    "The overlays only work in the Single layout.",
                    "Each one says on or off.",
                    ("Press {key:layout} until you hear Single, then press {key:scrollbar_overlay} "
                     "and {key:graph_overlay}."),
                ),
            ),
        ],
    ),
    _lesson(
        "cube", "extras", "The cube", minutes=4, requires=("cube",),
        pose=DEFAULT_POSE,
        steps=[
            _step(
                "connect_cube",
                "This lesson needs the WitMotion cube; if you do not have one, Skip lesson moves on. "
                "The tutorial has turned on the WitMotion IMU section for it; Cube, under Hardware Controls in Settings, "
                "shows or hides that section. Lay the cube flat and still on the table first: it "
                "takes its starting position from its first reading at least 20 seconds after "
                "this page loaded. Then choose Connect BLE in that section and choose the cube in "
                "the browser's list.",
                sr="Lay the cube flat and still, then choose Connect BLE in the WitMotion IMU "
                   "section and choose the cube in the browser's list.",
                braille="Cube flat, connect",
                check=_device("cube"),
                done="The cube is connected. Disconnect, next to Connect BLE, lets it go.",
                hints=(
                    "Connect BLE is a button in the WitMotion IMU section, after the Model section.",
                    ("Turn the cube on and turn Bluetooth on. Its name in the list starts with "
                     "WT9, BWT or WITMOTION."),
                    ("Lay the cube flat, choose Connect BLE, pick the cube in the list and press "
                     "Enter. If it will not connect, choose Skip step."),
                ),
            ),
            _step(
                "cube_axis",
                "Now turn the cube so its Y face points up. It starts with Z up, because you "
                "laid it flat. Once a face has been up for a moment, the viewer names the new "
                "view. Stop when you hear Y from minus or Y from plus, or Front view or Back view "
                "in Turn mode.",
                sr="Turn the cube so its Y face points up, and hold it still until the viewer names "
                   "the new view.",
                braille="Turn Y face up",
                check=_state("cut_axis", equals="y"),
                # The slice plane stays where the lesson put it, the middle, which along Y is
                # the walls and the base: the handle is further toward minus Y (#245
                # review).
                done="The slice plane cuts along the Y axis from the rim to the base through both walls of "
                     "the mug. The handle is further toward minus Y, so it is not in this view.",
                hints=(
                    "Turn the cube a quarter turn at a time and hold it still after each turn.",
                    "A face has to point clearly up, not tilted toward a corner.",
                    ("Tip the cube a quarter turn onto one side, and turn it until the viewer says "
                     "Y from minus, Y from plus, Front view or Back view."),
                ),
            ),
        ],
    ),
    _lesson(
        "slider", "extras", "The slider", minutes=3, requires=("slider",),
        pose=DEFAULT_POSE,
        steps=[
            _step(
                "connect_slider",
                "This lesson needs the Trinkey slider; if you do not have one, Skip lesson moves on. "
                "The tutorial has turned on the Trinkey Slider section for it; Slider, under Hardware Controls in Settings, "
                "shows or hides that section. Plug the slider in, choose Connect USB in that "
                "section, and choose it in the browser's list of serial ports.",
                sr="Plug the slider in, choose Connect USB in the Trinkey Slider section, and choose "
                   "it in the browser's list.",
                braille="Connect the slider",
                check=_device("slider"),
                done="The slider is connected. Disconnect, next to Connect USB, lets it go.",
                hints=(
                    ("Connect USB is a button in the Trinkey Slider section, after the Model "
                     "section."),
                    ("If the slider is not in the first list, the browser opens a second list with "
                     "every serial port; choose the one that appeared when you plugged it in."),
                    ("Plug the slider in, choose Connect USB, pick it in the list and press Enter. "
                     "If it will not connect, choose Skip step."),
                ),
            ),
            _step(
                "slide_sweep",
                "The slider sets where the slice plane is directly: one end of the slider is one side of "
                "the mug and the other end is the other side. Slide it slowly from one end to the "
                "other. The viewer says where the slice plane is when you stop.",
                braille="Slide end to end",
                check=_sweep("x", 10, 90),
                narrate={"handle_loop": "The handle loop, in the middle."},
                done="That was the whole mug, from one side to the other.",
                on_fail="The slice plane is no longer along X. Press {key:reset}, then slide from one "
                        "end to the other.",
                hints=(
                    "Move the slider slowly; the display follows it.",
                    "The slice plane has to reach near both sides of the mug.",
                    "Push the slider all the way to one end, then all the way to the other.",
                ),
            ),
            _step(
                "slide_handle",
                "Now stop the slider in the middle, where you feel the handle loop.",
                braille="Stop at the handle",
                check=_in_band("x", "handle_loop"),
                done="Stopped in the handle loop.",
                hints=(
                    "The handle is in the middle part of the mug.",
                    "Move the slider slowly toward its middle and feel for the loop on the left.",
                    "Put the slider at its middle.",
                ),
            ),
        ],
    ),
]

# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------

_KEY_PLACEHOLDER = re.compile(r"\{key:([a-z0-9_]+)\}")

# Fields that are data rather than words, and never hold a placeholder.
_DATA_FIELDS = frozenset({"id", "check", "when", "store", "requires", "braille", "pose", "part"})


def key_name(name: str, display: str = "none") -> str:
    """What to call KEYS[name] for someone using this display: the keyboard key,
    the display's button or both, "dot 4 or Page Up"."""
    if name not in KEYS:
        raise ValueError(f"unknown key name {name!r}")
    entry = KEYS[name]
    keyboard = entry["keyboard"]
    device = entry.get(display) if display in ("monarch", "dotpad") else None
    if keyboard and device:
        return f"{device} or {keyboard}"
    if keyboard:
        return keyboard
    if device:
        return device
    # A display-only control with no display named: say it for both, once when
    # they are the same button.
    monarch, dotpad = entry["monarch"], entry["dotpad"]
    if monarch and dotpad and monarch != dotpad:
        return f"{monarch} on the Monarch, {dotpad} on the DotPad"
    return monarch or dotpad or name


def _resolve(value: Any, display: str) -> Any:
    if isinstance(value, str):
        return _KEY_PLACEHOLDER.sub(lambda m: key_name(m.group(1), display), value)
    if isinstance(value, list):
        return [_resolve(item, display) for item in value]
    if isinstance(value, dict):
        return {k: (v if k in _DATA_FIELDS else _resolve(v, display)) for k, v in value.items()}
    return value


def _key_help() -> dict[str, str]:
    """What key help says for each key and display button: "Page Up: moves the
    slice plane 10% deeper, away from you". Both displays are always there, since either
    can be connected at any time."""
    help_text: dict[str, str] = {}
    for entry in KEYS.values():
        if entry["code"] is not None:
            help_text[entry["code"]] = f"{entry['keyboard']}: {entry['does']}"
    for device in ("monarch", "dotpad"):
        for command, name in DEVICE_COMMANDS.items():
            button = KEYS[name][device] if name else None
            if button:
                help_text[f"{device}:{command}"] = f"{button}: {KEYS[name]['does']}"
            else:
                help_text[f"{device}:{command}"] = UNMAPPED_DEVICE_BUTTON
    return help_text


def lessons_payload(display: str = "none") -> dict[str, Any]:
    """The lessons for one display, with every {key:NAME} resolved.

    display is "monarch", "dotpad" or "none"; anything else is treated as "none",
    so a mistyped query string still gets a tutorial that names keyboard keys.
    Runtime tokens ({handle_side} and the rest) are left for the runner. Returns
    {"version", "lessons", "keys", "key_help"}; the server adds the landmarks."""
    display = str(display or "none").strip().lower()
    if display not in DISPLAYS:
        display = "none"
    return {
        "version": TUTORIAL_VERSION,
        "lessons": _resolve(copy.deepcopy(LESSONS), display),
        "keys": copy.deepcopy(KEYS),
        "key_help": _key_help(),
    }
