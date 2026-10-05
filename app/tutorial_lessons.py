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
# does      what it does, for key help: "Page Up: moves the cut 10% deeper".
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
    "depth_deeper_10": _k("Page Up", "pageup", "moves the cut 10% deeper, away from you",
                          monarch="dot 4", dotpad="dot 4"),
    "depth_shallower_10": _k("Page Down", "pagedown", "moves the cut 10% shallower, toward you",
                             monarch="dot 1", dotpad="dot 1"),
    "depth_deeper_1": _k("Arrow Up", "arrowup", "moves the cut 1% deeper, away from you"),
    "depth_shallower_1": _k("Arrow Down", "arrowdown", "moves the cut 1% shallower, toward you"),
    "depth_near": _k("Home", "home", "moves the cut to the surface nearest you"),
    "depth_far": _k("End", "end", "moves the cut to the far side"),
    # XYZ mode's axes. On the DotPad they are the braille letters x, y and z.
    "axis_x": _k("X", "x", "cuts along X from plus, and pressed again from minus. XYZ mode only",
                 monarch="dots 1 3 4 6", dotpad="dots 1 3 4 6"),
    "axis_y": _k("Y", "y", "cuts along Y from minus, and pressed again from plus. XYZ mode only",
                 monarch="dots 1 3 4 5 6", dotpad="dots 1 3 4 5 6"),
    "axis_z": _k("Z", "z", "cuts along Z from plus, and pressed again from minus. XYZ mode only",
                 monarch="dots 1 3 5 6", dotpad="dots 1 3 5 6"),
    # Turn mode's quarter turns.
    "turn_pitch_up": _k("I", "i", "pitches the model up a quarter turn. Turn mode only"),
    "turn_pitch_down": _k("K", "k", "pitches the model down a quarter turn. Turn mode only"),
    "turn_yaw_left": _k("J", "j", "yaws the model left a quarter turn. Turn mode only"),
    "turn_yaw_right": _k("L", "l", "yaws the model right a quarter turn. Turn mode only"),
    "turn_roll_ccw": _k("U", "u", "rolls the model counterclockwise a quarter turn. Turn mode only"),
    "turn_roll_cw": _k("O", "o", "rolls the model clockwise a quarter turn. Turn mode only"),
    # Where am I.
    "where_am_i": _k("Period", ".", "says where you are: the side you are on, the cut, the "
                     "origin, the render mode, zoom and model"),
    "origin": _k("Comma", ",", "says where the model's origin is on the display"),
    "reset": _k("0", "0", "resets the view to square, the cut to the middle, the zoom to 0 and "
                "the model to the centre"),
    "fit": _k("F", "f", "fits the current slice to the display"),
    # How the slice is drawn and laid out.
    "render_mode": _k("R", "r", "changes the render mode: Cut, X-Ray, Filled, Outline"),
    "layout": _k("T", "t", "changes the layout: Single, Side-by-Side, Slice Graph"),
    "scrollbar_overlay": _k("Left bracket", "[", "turns the scrollbars on or off, in the Single layout"),
    "graph_overlay": _k("Right bracket", "]", "turns the slice graph on or off, in the Single layout"),
    "graph_refresh": _k("G", "g", "moves the slice graph's anchor to the current cut, "
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
    "print": _k("P", "p", "saves a copy of the render on the server; nothing prints or downloads"),
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
    # moving the cut; the runner captures them while the pattern is up.
    "pattern_left": _k(None, None, "answers left in the display test", monarch="dot 1", dotpad="dot 1"),
    "pattern_right": _k(None, None, "answers right in the display test", monarch="dot 4",
                        dotpad="dot 4"),
    # The tutorial's own keys. tutorial.js handles them, not the viewer, and only
    # while the tutorial is running.
    "tutorial_continue": _k("N", "n", "goes on to the next step or lesson. Tutorial only"),
    "tutorial_back": _k("B", "b", "goes back one step. Tutorial only"),
    "tutorial_repeat": _k("C", "c", "repeats the current step. Tutorial only"),
}

# The command names the runner's captureDeviceKey hook reports for a Monarch or
# DotPad button, and the key each one is. Key help describes a device button
# through this, as "dot 4: moves the cut 10% deeper".
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


def _step(step_id: str, text: str, *, braille: str, check: dict[str, Any], done: str,
          hints: tuple[str, str, str], sr: str | None = None, on_fail: str | None = None,
          answers: list[dict[str, str]] | None = None, narrate: dict[str, str] | None = None,
          demo: dict[str, Any] | None = None, key_only: bool = False,
          when: dict[str, str] | None = None, store: str | None = None,
          requires: tuple[str, ...] = ()) -> dict[str, Any]:
    """Every step carries every field, so the runner never has to ask whether one
    is there."""
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


# The pose most lessons start from: the mug standing upright, cut through its
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

XYZ_POSE: dict[str, Any] = {**DEFAULT_POSE, "axis_mode": "xyz"}

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
                "Choose Exit tutorial at any time to leave and use the viewer on your own. "
                "The tutorial takes about an hour, and you can do it in parts: Exit tutorial "
                "pauses it, and the Tutorial button in the main menu picks it back up where you "
                "stopped. The tutorial adds no recording of its own: if you allowed analytics, "
                "it notes only which lessons you finish or skip, and the viewer keeps its usual "
                "logs while you practise. Under each step, Last thing said keeps the last thing the "
                "viewer or the tutorial said, in case you missed it. To start, which display are you "
                "using today?",
                sr="Exit tutorial pauses the tutorial at any time, and the Tutorial button picks it "
                   "back up. Under each step, Last thing said keeps the last thing said. Which "
                   "display are you using today?",
                braille="Tutorial: display?",
                check=_answer(None),
                answers=_answers(("Monarch", "monarch"), ("DotPad", "dotpad"), ("No display", "none")),
                store="display",
                done="Noted. The lessons will name the buttons for your display.",
                hints=(
                    ("Choose the display in front of you. If you have neither a Monarch nor a "
                     "DotPad, choose No display: the lessons work by speech, and the parts that "
                     "need a display are skipped."),
                    ("Your answer only decides which buttons the lessons name. You can connect a "
                     "display later with Connect in the main menu."),
                    "Choose Monarch, DotPad or No display. If you are not sure, choose No display.",
                ),
            ),
            _step(
                "printed_mug",
                "Do you have the printed tutorial mug in your hands? It is optional. If you have "
                "it, some lessons ask you to compare it with the display. The files to print it "
                "come at the end of the tutorial.",
                braille="Printed mug?",
                check=_answer(None),
                answers=_answers(("Yes", "yes"), ("No", "no")),
                store="has_print",
                done="Noted.",
                hints=(
                    ("The printed mug is a 3D print of the same mug the lessons use. Most people "
                     "do not have one."),
                    "Your answer only changes a few sentences in later lessons.",
                    "Choose Yes if you are holding the printed mug, otherwise choose No.",
                ),
            ),
            _step(
                "keys_reach",
                "The viewer is driven with single keys, and some screen readers keep those keys "
                "for themselves. Press {key:where_am_i} now. The viewer answers with where you "
                "are. If nothing happens, choose Keys aren't working.",
                braille="Press period",
                check=_key("."),
                key_only=True,
                done="The viewer heard you, so single keys reach it. From now on "
                     "{key:tutorial_continue} is the same as Next, {key:tutorial_back} is Back "
                     "and {key:tutorial_repeat} is Repeat.",
                on_fail="Keys are reaching the viewer, but that was a different one. "
                        "Press {key:where_am_i}.",
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
                     "in Settings, under Keyboard, turn it back on, or choose Skip step."),
                ),
            ),
        ],
    ),
    _lesson(
        "connect", "setup", "Connect your display", minutes=3,
        steps=[
            _step(
                "connect",
                "Now connect your display. Turn it on, then choose Connect Monarch or Connect "
                "DotPad in the tutorial area. The browser opens a list of devices: choose yours "
                "and press Enter. Later, Connect and Disconnect in the main menu do the same for "
                "the display chosen under Output Device in Settings.",
                sr="Turn your display on, choose Connect Monarch or Connect DotPad in the tutorial "
                   "area, and choose it in the browser's list.",
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
                     "Connect DotPad again. For the Monarch, plug it in and turn it on, then choose "
                     "Connect Monarch again. If it still fails, choose Skip step and connect later "
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
                braille="Bump left or right?",
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
                "shows one slice through it, as if you cut the mug with a knife and felt the cut "
                "face: raised pins are the mug, lowered pins are air. The slice is not to scale; "
                "it is scaled so the whole mug fits in the middle of the display. Right now the "
                "cut runs from the rim down to "
                "the base, through the middle of the mug. If you have the printed mug, hold it "
                "upright with the handle to your left. Press Next when you are ready.",
                sr="The display shows one slice through the mug, as if you cut it and felt the cut "
                   "face. It is scaled to fit the display. Press Next when ready.",
                braille="A slice of the mug",
                check=_manual(),
                done="Next, find the handle.",
                hints=(
                    ("Feel the whole display slowly, edge to edge. The cut face of the mug is "
                     "the raised area in the middle."),
                    ("The cut goes through the middle of the mug, so you feel two walls, the base "
                     "joining them, and the handle on one side."),
                    "Press Next, or {key:tutorial_continue}, to go on.",
                ),
            ),
            _step(
                "where_handle",
                "The mug is a U shape: two walls and the base between them. Where is the "
                "handle? Feel around the mug, then choose Left, Right, Top or Bottom.",
                braille="Where is the handle?",
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
                braille="Press period",
                check=_key("."),
                key_only=True,
                done="That is where you are: the side you are on, where the cut is, where the "
                     "origin is on the display, then the render mode, zoom and model.",
                on_fail="That was a different key. Press {key:where_am_i}.",
                hints=(
                    ("The answer is spoken. On the DotPad a short form also appears on the "
                     "braille line."),
                    "It is the key for the full stop at the end of a sentence.",
                    "Press {key:where_am_i}.",
                ),
            ),
            _step(
                "reset",
                "Reset squares the view up, brings the cut back to the middle, zooms out and "
                "centres the mug. Use it whenever you get lost. It does not undo a turn to "
                "another side; Return to lesson start, in the tutorial area, puts the whole "
                "lesson back as it began. Press {key:reset} now.",
                sr="Reset squares the view up, brings the cut to the middle, zooms out and centres "
                   "the mug. Press {key:reset} now.",
                braille="Press 0 to reset",
                check=_key("0"),
                key_only=True,
                done="Reset. The view is square, the cut is in the middle and the zoom is 0.",
                on_fail="That was a different key. Press {key:reset}.",
                hints=(
                    "Reset is the zero key, on the top row or the number pad.",
                    ("The Reset Position button in the Zoom section does the same, but this step "
                     "wants the key."),
                    "Press {key:reset}.",
                ),
            ),
        ],
    ),
    _lesson(
        "depth", "core", "Moving the cut", minutes=6, pose=DEFAULT_POSE,
        steps=[
            _step(
                "worked_example",
                "First, a worked example. Each time you press Next, the tutorial moves the "
                "cut for you, from near the far side of the mug to near the side closest to you. "
                "Feel each slice and listen to what it is.",
                braille="Worked example",
                check=_manual(),
                demo={"frames": [
                    # The slice at 5% and at 95% spans only the top two thirds of the
                    # mug: the wall flares out toward the rim, so near its edge the
                    # cut misses the base (#245 review).
                    {"cut_percent": 5,
                     "say": "Near the far side the cut only grazes the wall, where the mug is "
                            "widest. You feel one solid piece, widest at the rim and narrowing as "
                            "it goes down, and it stops about a third of the way above the base."},
                    {"cut_percent": 25,
                     "say": "A quarter of the way in, the cut passes through the hollow: two walls "
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
                done="That was the whole mug, one slice at a time.",
                hints=(
                    "Press Next for the next slice. There are five.",
                    ("Feel how the shape changes from slice to slice: solid, a U, a U with the "
                     "handle."),
                    "Press Next, or {key:tutorial_continue}, until the example ends.",
                ),
            ),
            _step(
                "to_the_far_side",
                "Now you move the cut. Press {key:depth_deeper_10} to move it 10% deeper, away "
                "from you, and {key:depth_shallower_10} to move it 10% back toward you. Press "
                "{key:depth_deeper_10} until the cut reaches the far side of the mug and a press "
                "changes nothing. On a step like this, if you get stuck, Show me does it for you "
                "and says what is there.",
                sr="Press {key:depth_deeper_10} until the cut reaches the far side of the mug and a "
                   "press changes nothing.",
                braille="Go to the far side",
                check=_edge(),
                done="You are at the edge of the mug. Past here there is nothing to cut.",
                on_fail="That went back toward you. Keep going one way until the number stops "
                        "changing.",
                hints=(
                    "Deeper means away from you, into the mug.",
                    ("Each press is a tenth of the mug, so it takes at most ten presses. At the "
                     "edge the viewer says the same number again."),
                    "Press {key:depth_deeper_10} again and again until the number stops changing.",
                ),
            ),
            _step(
                "mark_handle",
                "Now bring the cut back to the handle. Press {key:depth_shallower_10} until you "
                "feel the handle loop on the left, then press Next while you are in it.",
                braille="Find the handle loop",
                check=_mark_band("x", "handle_loop"),
                done="That is the handle loop. It is only there in the middle part of the mug.",
                on_fail="Not there yet. The handle loop is only in the middle part of the mug.",
                hints=(
                    ("The handle is in the middle of the mug, so from the far side you come back "
                     "about halfway."),
                    "Feel for the loop on the left of the mug after each press. When it is there, stop.",
                    ("Press {key:reset} to bring the cut to the middle, which is inside the handle, "
                     "then press Next."),
                ),
            ),
            _step(
                "fine_steps",
                "Two more keys jump straight to the ends: {key:depth_near} goes to the surface "
                "nearest you and {key:depth_far} to the far side. For fine work, "
                "{key:depth_deeper_1} and {key:depth_shallower_1} move the cut 1% at a time. The "
                "Depth section has a slider and Deeper and Shallower buttons that do the same. "
                "Press {key:depth_near}, then {key:depth_far}.",
                sr="Press {key:depth_near} for the surface nearest you, then {key:depth_far} for the "
                   "far side.",
                braille="Home, then End",
                check=_keys("home", "end"),
                done="Those are all the depth keys.",
                on_fail="That was a different key. Press {key:depth_near}, then {key:depth_far}.",
                hints=(
                    "On many laptops Home and End are Fn with Left Arrow and Fn with Right Arrow.",
                    "The order does not matter; the step needs both.",
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
                "Render modes change how the slice is drawn. There are four: Cut, X-Ray, Filled "
                "and Outline. Press {key:render_mode} four times, stopping to feel each one, to "
                "go through all four and come back to Cut. The Rendering Mode section has the "
                "same four as radio buttons.",
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
                    "cut": "Cut: the face of the cut, where the knife went through the mug. This "
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
                "Zoom in 10% with {key:zoom_in_10} and out 10% with {key:zoom_out_10}. Press "
                "{key:zoom_in_10} twice. The mug grows until it fills the display from top to "
                "bottom.",
                braille="Zoom in twice",
                check=_zoom_by(0.2),
                done="Zoomed in 20%. The mug now fills the display from top to bottom.",
                hints=(
                    "Zoom makes the mug bigger so you can feel details.",
                    "Each press of {key:zoom_in_10} adds 10%, and this step needs 20%.",
                    "Press {key:zoom_in_10} two times.",
                ),
            ),
            _step(
                "centre_handle",
                "To move the mug, press {key:pan_up}, {key:pan_left}, {key:pan_down} or "
                "{key:pan_right}: up, left, down or right, a quarter of the display at a time. "
                "The handle is on the {handle_side} side of the mug. To bring it toward the middle, press "
                "{pan_toward_handle}. After each move the tutorial says where the handle is. Stop "
                "when it is in the middle.",
                sr="Move the mug with {key:pan_up}, {key:pan_left}, {key:pan_down} and "
                   "{key:pan_right} until the tutorial says the handle is in the middle.",
                braille="Handle to the middle",
                check=_centred("handle"),
                key_only=True,
                done="That is it. Moving the object is how you bring any part of a model to the middle.",
                hints=(
                    ("The keys move the mug, not your hand: to bring something near the left edge "
                     "toward the middle, move the mug right."),
                    ("Each press moves the mug a quarter of the display, so it takes one or two "
                     "presses."),
                    ("With the handle near the left edge, press {key:pan_right}. With it near the "
                     "right edge, press {key:pan_left}."),
                ),
            ),
            _step(
                "zoom_out",
                "Now zoom back out. Press {key:zoom_out_10} until the zoom is back at 0, or press "
                "{key:reset}, which also puts the mug back in the middle. For fine steps, "
                "{key:zoom_in_1} and {key:zoom_out_1} zoom by 1%. The Zoom section also has a "
                "Zoom level field and Zoom In and Zoom Out buttons.",
                sr="Press {key:zoom_out_10} until the zoom is back at 0, or press {key:reset}.",
                braille="Zoom back out to 0",
                check=_state("zoom", lte=0.001),
                done="The zoom is back at 0.",
                hints=(
                    "Zoom cannot go below 0, so extra presses do no harm.",
                    "You zoomed in twice, so it takes two presses of {key:zoom_out_10}.",
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
                "This lesson uses XYZ mode, which works like OpenSCAD: you choose the axis to cut "
                "along. It is the viewer's own mode unless you chose Turn, and at the end of the "
                "lesson you choose which mode to keep. The mug stands on its base on the table, "
                "with Z pointing up from the table. X and Y run across the table. The axis keys "
                "are {key:axis_x} for X, {key:axis_y} for Y and {key:axis_z} for Z, and the same key "
                "again gives the cut from the other side. The viewer names the side you cut from as "
                "plus or minus: X from plus means you are on the plus side of X. The cut's number "
                "is its place along the axis, in percent of the mug's size, with 0 at the model's "
                "origin, the middle of the base. Deeper still means away from you, so from X plus, "
                "Y plus or Z plus, going deeper lowers the number. If a step is hard, Show me does "
                "it for you.",
                sr="This lesson uses XYZ mode: you choose the axis to cut along, and the same key "
                   "again gives the other side. The cut's number counts from the model's origin, "
                   "the middle of the base. Press Next when ready.",
                braille="XYZ mode: the axes",
                check=_manual(),
                done="Next, cut along Z.",
                hints=(
                    ("Z is up. The base of the mug is at the low end of Z, and the rim at the "
                     "high end."),
                    "X and Y lie flat, like the edges of a sheet of paper on the table.",
                    "Press Next, or {key:tutorial_continue}, to try the axis keys.",
                ),
            ),
            _step(
                "cut_z",
                "Press {key:axis_z}. The cut now runs across the mug, parallel to the table, and "
                "the viewer says Z from plus, X right, Y up: you are above the mug, with X running "
                "to the right and Y up the display. The Orientation section also has a button for "
                "each axis and side, X plus to Z minus.",
                sr="Press {key:axis_z} to cut along Z, across the mug, parallel to the table.",
                braille="Cut along Z",
                check=_state("cut_axis", equals="z"),
                done="You are cutting along Z. The cut is a ring, the wall of the mug, with a small "
                     "separate piece beside it, the handle.",
                hints=(
                    "Z is the axis that points up from the table.",
                    "The viewer says Z from plus when you are there.",
                    "Press {key:axis_z} once.",
                ),
            ),
            _step(
                "sweep_z",
                "Move the cut down to the floor of the mug and back up to the rim. From above, Z "
                "plus, {key:depth_deeper_10} goes deeper, down the mug, and {key:depth_shallower_10} "
                "comes back up. On the way you pass the ring of the wall with the handle beside "
                "it, and near the bottom the solid floor.",
                sr="Move the cut down to the floor of the mug with {key:depth_deeper_10}, and back up "
                   "to the rim with {key:depth_shallower_10}.",
                braille="Floor to rim",
                check=_sweep("z", 10, 90),
                narrate={
                    "floor": "The floor: a solid disc.",
                    "above_floor": "Just above the floor: a ring, the wall.",
                    "handle_beside_ring": "The ring, with the handle beside it as a small "
                                          "separate piece.",
                },
                done="That was the whole mug, from the floor to the rim.",
                on_fail="You are not cutting along Z any more. Press {key:axis_z} to come back, then "
                        "go down to the floor and up to the rim.",
                hints=(
                    "From above, deeper is down toward the table and shallower is up toward you.",
                    ("The number goes down as you go deeper: about 10 at the floor, and about 90 "
                     "near the rim."),
                    ("Press {key:depth_deeper_10} four times, then {key:depth_shallower_10} eight "
                     "times."),
                ),
            ),
            _step(
                "cut_x_handle",
                "Press {key:axis_x}. The viewer says X from plus, and the cut runs from the rim to "
                "the base again, as in the first lessons. Each axis keeps its own cut, so X is "
                "still at 0, the middle, through the handle loop.",
                braille="Cut along X",
                check=_all(_state("cut_axis", equals="x"), _in_band("x", "handle_loop")),
                done="That is the handle loop, cut along X.",
                hints=(
                    "X runs across the mug, from one side to the other.",
                    ("If you moved the X cut earlier, bring it back toward the middle until the "
                     "handle loop is there."),
                    ("Press {key:axis_x}. If you do not feel the loop, press {key:reset} to bring "
                     "every cut back to the middle."),
                ),
            ),
            _step(
                "cut_y_arms",
                "Press {key:axis_y}. The viewer says Y from minus: you face the mug from the front, "
                "with the handle pointing at you. Press {key:depth_shallower_10} to move the cut "
                "toward you until you feel two small separate pieces. Those are the two arms of the "
                "handle, cut across.",
                sr="Press {key:axis_y}, then {key:depth_shallower_10} until you feel two small "
                   "separate pieces, the arms of the handle.",
                braille="Find the handle arms",
                check=_all(_state("cut_axis", equals="y"), _in_band("y", "handle_arms")),
                done="Those two pieces are the handle's arms, cut across.",
                hints=(
                    "From the front the handle points at you, so it is on the near side of the mug.",
                    "The arms are near the end of the handle, most of the way toward you.",
                    ("Press {key:axis_y} until you hear Y from minus, then press "
                     "{key:depth_shallower_10} three times."),
                ),
            ),
            _step(
                "which_axis_loop",
                "Which axis did you cut along to feel the whole handle loop, with its finger hole?",
                braille="Axis for the loop?",
                check=_answer("x"),
                answers=_answers(("X", "x"), ("Y", "y"), ("Z", "z")),
                done="Yes, X. A cut along X through the middle runs through the handle from end "
                     "to end.",
                on_fail="Not that one. Think of the cut that gave you the loop with a hole in it.",
                hints=(
                    "Along Z you felt rings, and along Y the two small arms.",
                    "The loop with the hole was the same cut as in the first lessons.",
                    "Choose X.",
                ),
            ),
            _step(
                "handle_sign",
                "Which way does the handle point: toward positive Y or toward negative Y?",
                braille="Which way on Y?",
                check=_answer("negative"),
                answers=_answers(("Positive Y", "positive"), ("Negative Y", "negative")),
                done="Yes. The handle points toward negative Y, the low end of the Y numbers.",
                on_fail="Not that way. Think of where the handle's arms were on the Y numbers.",
                hints=(
                    "Cutting along X, the viewer said Y right, and the handle was on the left.",
                    "Cutting along Y, you found the handle's arms below 0, around minus 45.",
                    "Choose Negative Y.",
                ),
            ),
            _step(
                "origin",
                "Every model has an origin, the point where X, Y and Z are all 0. Press "
                "{key:origin} to hear where it is on the display: how far across from the left "
                "edge, and how far up from the bottom.",
                braille="Where is the origin?",
                check=_key(","),
                key_only=True,
                done="In this mug the origin is in the middle of the base, where it meets the table.",
                on_fail="That was a different key. Press {key:origin}.",
                hints=(
                    "The origin is the point every coordinate is measured from.",
                    "In XYZ mode the display also marks the origin with a small hollow square.",
                    "Press {key:origin}.",
                ),
            ),
            _step(
                "keep_mode",
                "You can keep XYZ mode, or use Turn mode, which turns the model a quarter turn at "
                "a time with pitch, roll and yaw. In XYZ mode the display also marks the origin "
                "with a small hollow square and puts the axis letters at its edges; Settings can "
                "turn either off. You can change the mode any time in Settings, under Axis Mode. "
                "Which do you want?",
                sr="Keep XYZ mode, or use Turn mode, which turns the model with pitch, roll and "
                   "yaw? Settings can change it any time.",
                braille="Keep XYZ mode?",
                check=_answer(None),
                answers=_answers(("Keep XYZ mode", "xyz"), ("Use Turn mode", "turn")),
                store="axis_choice",
                done="Done. You can change it any time in Settings.",
                hints=(
                    ("XYZ mode suits people who think in coordinates, as in OpenSCAD. Turn mode "
                     "suits people who like to turn the object in their hands."),
                    "Nothing is lost either way; Settings switches between them.",
                    "Choose Keep XYZ mode or Use Turn mode.",
                ),
            ),
            _step(
                "turn_tip",
                "Turn mode is on. Each turn key turns the mug a quarter turn: {key:turn_pitch_up} "
                "and {key:turn_pitch_down} pitch it up and down, {key:turn_yaw_left} and "
                "{key:turn_yaw_right} yaw it left and right, and {key:turn_roll_ccw} and "
                "{key:turn_roll_cw} roll it counterclockwise and clockwise. The Orientation "
                "section has a button for each. Press {key:turn_pitch_up} once. Where is the "
                "handle now?",
                sr="Turn mode is on. Press {key:turn_pitch_up} once to pitch the mug up. Where is "
                   "the handle now?",
                braille="Pitch up: handle?",
                check=_answer("computed:handle_side"),
                answers=_HANDLE_ANSWERS,
                when={"stored": "axis_choice", "equals": "turn"},
                done="Yes. That is where the handle went.",
                on_fail="Not there. Feel the edges for the handle, or press {key:where_am_i}.",
                hints=_TURN_HINTS,
            ),
            _step(
                "turn_yaw",
                "Press {key:turn_yaw_left} once to yaw the mug left. Where is the handle now?",
                braille="Yaw left: handle?",
                check=_answer("computed:handle_side"),
                answers=_HANDLE_ANSWERS,
                when={"stored": "axis_choice", "equals": "turn"},
                done="Yes. That is where the handle went.",
                on_fail="Not there. Feel the edges for the handle, or press {key:where_am_i}.",
                hints=_TURN_HINTS,
            ),
            _step(
                "turn_roll",
                "Press {key:turn_roll_cw} once to roll the mug clockwise. Where is the handle now?",
                braille="Roll: handle now?",
                check=_answer("computed:handle_side"),
                answers=_HANDLE_ANSWERS,
                when={"stored": "axis_choice", "equals": "turn"},
                done="Yes. A roll keeps the same side of the mug toward you and turns it on the "
                     "display.",
                on_fail="Not there. Feel the edges for the handle, or press {key:where_am_i}.",
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
                "The tutorial has zoomed in and moved the mug off the middle, the way it can "
                "happen while you explore. Press {key:reset} to put it straight.",
                sr="Press {key:reset} to put it straight.",
                braille="Press 0 to reset",
                check=_all(_key("0"), _state("zoom", lte=0.001)),
                key_only=True,
                done="Reset. The mug is square, in the middle and zoomed out.",
                hints=(
                    "Reset is for whenever you are lost.",
                    ("Reset squares the view up, brings the cut to the middle, zooms out and "
                     "centres the mug. The Reset Position button in the Zoom section does the same."),
                    "Press {key:reset}.",
                ),
            ),
            _step(
                "fit",
                "Fit zooms and moves the view so the current slice fills the display. Press "
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
        "help_and_settings", "wrapup", "Help, Settings and coming back", minutes=5,
        steps=[
            _step(
                "shortcuts",
                "The list of every keyboard shortcut, grouped by heading, opens with "
                "{key:shortcuts}. Help, in the main menu, opens the same list. Press "
                "{key:shortcuts} now, read as much as you like, then press {key:escape} or choose "
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
            _step(
                "key_help",
                "Key help tells you what a key or a display button does, without doing it. Choose "
                "Key help in the tutorial area to turn it on. Then press {key:render_mode}, and then "
                "{key:depth_deeper_10}: each time, the tutorial says what that key does, and the mug "
                "does not change. Choose Key help again to turn it off. Key help is in the "
                "shortcuts list too.",
                sr="Choose Key help, press {key:render_mode} and then {key:depth_deeper_10} to hear what "
                   "they do, then choose Key help again.",
                braille="Try key help",
                check=_key_help(2),
                done="That is Key help. Use it whenever you are not sure what a key does.",
                hints=(
                    "While Key help is on, nothing you press changes the mug.",
                    "Key help is a button in the tutorial area, after Return to lesson start.",
                    ("Choose Key help, press {key:render_mode}, press {key:depth_deeper_10}, then "
                     "choose Key help again."),
                ),
            ),
            _step(
                "settings_tour",
                # Jen found the long list of sections confusing in the step itself
                # (#245 review), so the step says what to do and the first hint
                # says what is there.
                "Settings, in the main menu, holds what you set once and rarely change. Open "
                "Settings from the main menu, move through it without changing anything, then "
                "close it with {key:escape} or its Close button. The first hint says what is in it.",
                sr="Open Settings from the main menu, move through it without changing anything, "
                   "then close it.",
                braille="Tour Settings",
                check=_settings_unchanged(),
                done="Nothing changed. Settings is there whenever you need it.",
                hints=(
                    ("Settings has Axis Mode, Turn or XYZ, with the origin mark and the axis letters "
                     "for XYZ mode; Keyboard, where Single-key shortcuts can be turned off if your "
                     "screen reader or speech input sends letters by mistake; Output Device, DotPad "
                     "or Monarch, which Connect in the main menu uses; Hardware Controls, which show "
                     "the Slider and Cube sections; Debugging, with the Debug Panel and Bounding Box; "
                     "and Display, with View info box on display, Lock slice graph and Graph Mode."),
                    ("Tab moves from control to control. Leave every checkbox and radio button as "
                     "it is this time."),
                    "Choose Settings in the main menu, then press {key:escape} to close it.",
                ),
            ),
            _step(
                "where_resume",
                "Whenever you want this tutorial again, choose the Tutorial button in the main "
                "menu. It offers Resume, Start over and the list of lessons, where any lesson can "
                "be done again. The top of the keyboard shortcuts list has Resume, Start over and "
                "Choose a lesson too. About, also in the main menu, says who makes the viewer. "
                "Press Next.",
                sr="To come back to the tutorial, choose the Tutorial button in the main menu. "
                   "Press Next.",
                braille="Tutorial button",
                check=_manual(),
                done="Next, the mug detective.",
                hints=(
                    "The main menu is a navigation landmark at the top of the page.",
                    "Your screen reader's list of landmarks goes straight to it.",
                    "Press Next, or {key:tutorial_continue}.",
                ),
            ),
        ],
    ),
    _lesson(
        "mug_detective", "wrapup", "Mug detective", minutes=6, pose=DEFAULT_POSE,
        steps=[
            _step(
                "which_axis",
                "Time to use what you know. The tutorial has moved the cut to another axis and "
                "position without saying which. You are in {axis_mode_name} mode. Which axis are "
                "you cutting along? Feel the slice, or press {key:where_am_i}.",
                sr="Which axis are you cutting along? Feel the slice, or press {key:where_am_i}.",
                braille="Which axis?",
                check=_answer("computed:cut_axis"),
                answers=_answers(("X", "x"), ("Y", "y"), ("Z", "z")),
                done="Right.",
                on_fail="Not that one. Press {key:where_am_i} and listen to the first words.",
                hints=(
                    ("Rings mean a cut across the mug, parallel to the table. A U shape means a "
                     "cut from the rim to the base."),
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
                # The setup never leaves the cut inside the loop, so there is always
                # something to find (#245 review).
                "Now find the handle loop: the loop with a hole for your finger, from the first "
                "lessons. Cut along X through the middle of the mug, and press Next when you are "
                "in it.",
                braille="Back to the loop",
                check=_mark_band("x", "handle_loop"),
                done="Found it.",
                on_fail="Not there yet. You need a cut along X, through the middle of the mug.",
                hints=(
                    "The handle loop is there only when you cut along X through the middle.",
                    ("In XYZ mode, {key:axis_x} cuts along X. In Turn mode, a yaw turns the mug "
                     "until {key:where_am_i} says View from the right or the left, which is a cut "
                     "along X."),
                    ("In XYZ mode, press {key:axis_x}, then {key:reset}, then Next. In Turn mode, "
                     "press {key:turn_yaw_left} until {key:where_am_i} says View from the right or "
                     "the left, then {key:reset}, then Next."),
                ),
            ),
            _step(
                "floor_top",
                "Now cut along Z and find the ring just above the floor: going down the mug, it is "
                "the last ring before the cut turns solid. Press Next there.",
                braille="Ring above the floor",
                check=_mark_band("z", "above_floor"),
                requires=("display",),
                done="That is just above the floor. A little deeper, and the floor is solid.",
                on_fail="Not there yet. You want the last ring before the solid floor.",
                hints=(
                    ("Near the bottom of the mug the cut is a solid disc: the floor. Just above "
                     "it, the middle is hollow and you feel a ring."),
                    ("In XYZ mode, press {key:axis_z} until you hear Z from plus. In Turn mode, "
                     "press {key:turn_pitch_down} until {key:where_am_i} says View from above."),
                    ("From above, press {key:depth_deeper_10} until the cut turns solid, then "
                     "{key:depth_shallower_1} one step at a time until it is a ring again, and "
                     "press Next."),
                ),
            ),
            _step(
                "wall_vs_bar",
                "Press {key:reset} to bring the cut to the middle of the mug, where the handle bar "
                "is a small separate piece beside the ring. Measure both in the direction from the "
                "middle of the mug toward the handle. Which is thicker: the wall of the ring on "
                "the handle side, or the handle bar?",
                sr="Press {key:reset}, then measure toward the handle: which is thicker, the wall of "
                   "the ring on the handle side, or the handle bar?",
                braille="Wall or bar thicker?",
                check=_answer("wall"),
                answers=_answers(("The wall", "wall"), ("The handle bar", "bar"),
                                 ("About the same", "same")),
                requires=("display",),
                done="Yes. The wall is thicker than the handle bar.",
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
                "Last one. Does the handle connect to the mug in one place or two? Move the cut up "
                "and down along Z and feel where the separate piece meets the ring, or cut along X "
                "through the handle loop.",
                sr="Does the handle connect to the mug in one place or two?",
                braille="One place or two?",
                check=_answer("two"),
                answers=_answers(("One place", "one"), ("Two places", "two")),
                requires=("display",),
                done="Yes, two: at the top and at the bottom. That is what makes the loop.",
                on_fail="Not quite. Follow the handle from one end to the other.",
                hints=(
                    ("Along Z, the handle is a separate piece in the middle of the mug. Notice "
                     "where it stops being separate."),
                    ("Cut along X through the middle, as in the depth lesson: the loop meets the "
                     "wall of the U in two places."),
                    "It joins at the top and at the bottom. Choose Two places.",
                ),
            ),
        ],
    ),
    # The model chooser and upload are what this lesson teaches, so it unlocks
    # them.
    _lesson(
        "your_own_model", "wrapup", "Your own model", minutes=4, lock=False,
        steps=[
            _step(
                "pick_model",
                "The model controls are unlocked now. Choose a model from the Model list, or "
                "upload an STL or STEP file of your own with Upload model. An upload lasts until "
                "this tab closes or reloads; Remove uploaded model deletes it sooner.",
                braille="Pick a model",
                check=_model_changed(),
                done="Your model is loaded. Everything you learned works the same on it.",
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
                done="The image is on its way. The viewer says slice exported as png when the file "
                     "is saved.",
                hints=(
                    "The Export section is near the end of the page, in the Visual previews area.",
                    "The file is a PNG, named after the view, the depth and the render mode.",
                    "Choose the Export Current View as Image button.",
                ),
            ),
            # The print key is not taught: it does not work as it says, and it is to
            # come out of the help (#245 review).
            _step(
                "props_and_end",
                "One last thing: if you want the printed mug and ten slice plaques to hold, the "
                "tutorial area has a link to the files for a 3D printer. That is the end of the "
                "tutorial. Three extra lessons follow, for the slice graph, the cube and the "
                "slider; Next starts them, and Exit tutorial leaves them for another time. To redo "
                "any lesson, choose the Tutorial button in the main menu and pick it from the list "
                "of lessons, or use Choose a lesson in the keyboard shortcuts list; Start over runs "
                "the whole tutorial again.",
                sr="That is the end of the tutorial. Three extra lessons follow: Next starts them, "
                   "and Exit tutorial leaves them. To redo any lesson, choose the Tutorial button in "
                   "the main menu.",
                braille="Tutorial complete",
                check=_manual(),
                done="That was the last step.",
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
                    "side-by-side": "Side-by-Side: your cut on the right, and on the left the whole "
                                    "mug from another side, with a line where the cut passes.",
                    "slice-graph": "Slice Graph: your cut, with a graph along the bottom rows.",
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
                "The first slice graph for a model can take 20 to 40 seconds to work out. There is "
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
                "is the graph. From left to right it runs through the mug, from the side nearest "
                "you to the far side, one point for each depth. It compares every slice with one "
                "anchor slice, the cut you had when you came into Slice Graph: where the line is "
                "high, the slice at that depth is very different from the anchor, and where it is "
                "low, much the same. The single upright line across the graph is where your cut is "
                "now. Find it, then press Next.",
                sr="The bottom rows are the graph: left is the side nearest you, right the far side, "
                   "and the upright line is your cut. Press Next when you have found it.",
                braille="Feel the graph",
                check=_manual(),
                done="That upright line is your cut.",
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
                braille="Move the cut",
                check=_changed("depth"),
                done="The line follows your cut, and the graph stays put.",
                hints=(
                    "Deeper moves the upright line to the right.",
                    "Each press moves the cut a tenth of the way through the mug.",
                    "Press {key:depth_deeper_10}, then feel where the upright line is now.",
                ),
            ),
            _step(
                "graph_anchor",
                "Press {key:graph_refresh} to make the cut you are on now the anchor. The graph is "
                "worked out again against it: low around your cut, where the slices are much like "
                "it, and higher where they differ.",
                braille="G: new anchor",
                check=_key("g"),
                key_only=True,
                done="The viewer said the view and the depth of the new anchor.",
                on_fail="That was a different key. Press {key:graph_refresh}.",
                hints=(
                    "Only in the Slice Graph layout does this key do anything.",
                    "Afterwards the graph is at its lowest around your cut.",
                    "Press {key:graph_refresh}.",
                ),
            ),
            _step(
                "graph_lock",
                "The graph is locked, so it stays put while you move the cut. Press "
                "{key:graph_lock} to unlock it: then it follows your cut, worked out again against "
                "each new slice. Press {key:graph_lock} again to lock it. Settings has the same "
                "lock, as Lock slice graph, and Graph Mode, which shows the area of each slice "
                "instead of the difference from the anchor.",
                sr="Press {key:graph_lock} to unlock the graph, so it follows your cut, and again to "
                   "lock it.",
                braille="V: unlock, lock",
                # Off and back on, so the graph is left locked as the lesson found
                # it. Settings' Lock slice graph counts too.
                check=_cycle("slicegraph_locked", [True, False], True),
                done="The graph is locked again, and stays put while you move the cut.",
                hints=(
                    ("Stay in the Slice Graph layout; anywhere else this key says not in "
                     "slice-graph mode."),
                    "Unlocked, each move of the cut works the graph out again against the new slice.",
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
                # The cut stays where the lesson put it, the middle, which along Y is
                # the walls and the base: the handle is further toward minus Y (#245
                # review).
                done="The cube chose Y: the cut runs from the rim to the base through both walls of "
                     "the mug. The handle is further toward minus Y, so it is not in this cut.",
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
                "The slider sets where the cut is directly: one end of the slider is one side of "
                "the mug and the other end is the other side. Slide it slowly from one end to the "
                "other. The viewer says where the cut is when you stop.",
                braille="Slide end to end",
                check=_sweep("x", 10, 90),
                narrate={"handle_loop": "The handle loop, in the middle."},
                done="That was the whole mug, from one side to the other.",
                on_fail="The cut is no longer along X. Press {key:reset}, then slide from one end "
                        "to the other.",
                hints=(
                    "Move the slider slowly; the display follows it.",
                    "The cut has to reach near both sides of the mug.",
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
    cut 10% deeper, away from you". Both displays are always there, since either
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
