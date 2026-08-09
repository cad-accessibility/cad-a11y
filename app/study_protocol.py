"""The study protocol, as data.

This module is the single source of truth for what a study session consists of:
which model pairs exist, which two a given participant gets and in which order,
what the experimenter reads aloud at each step, which STL loads when, and which
questions are asked. The server, the experimenter panel and the participant view
all read the protocol from here (via ``GET /study/config`` and the session state
payload) so none of them can drift from the others.

Why a Python module rather than a config file
---------------------------------------------
An operator can still override the whole thing at runtime: if
``data/study/protocol.json`` exists (or ``STUDY_PROTOCOL_PATH`` points at a file),
it is loaded instead of the built-in definition. ``data`` is Docker-volume backed,
so that override survives a redeploy and can be edited without one. The built-in
definition stays in code because it ships with the image, needs no new dependency,
and is covered by the test suite -- a protocol that fails to parse mid-session is
a ruined session.

Counterbalancing
----------------
The protocol has three model pairs. Each participant  gets **two** of the three, in an order fixed by a
Latin square rather than drawn at random -- randomising a sample this small
routinely produces an unbalanced set, which is exactly what counterbalancing is
for. See ``assign_task_order``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2026-08-04"

# How many of the three main model pairs one participant sees. The trial pair is
# extra and is not counted here. Two is what fits a 60-90 minute session: the
# 2026-08-04 pilot spent most of an hour reaching the end of the first pair.
TASKS_PER_SESSION = 2


# ---------------------------------------------------------------------------
# Model pairs
#
# "a" is the version the participant meets first and builds a mental model of;
# "b" is the edited version they are asked to compare against it, without being
# told what changed. ``differences`` is the experimenter's answer key -- it is
# never shown to the participant, only in the control panel, so the experimenter
# can code a reported difference as a true or false positive on the spot.
# ---------------------------------------------------------------------------

MODEL_PAIRS: dict[str, dict[str, Any]] = {
    "lego": {
        "key": "lego",
        "label": "Lego brick",
        "description": "A lego brick",
        "a": {
            "model": "lego_2x3",
            "label": "2x3 Lego brick",
            "physical": "Printed 2x3 Lego brick",
        },
        "b": {
            "model": "lego_2x4",
            "label": "2x4 Lego brick",
            "physical": "Printed 2x4 Lego brick",
        },
        "differences": [
            "2x3 becomes 2x4",
            "Inside shape changes as a result",
            "Aspect ratio changes as a result",
        ],
        "unchanged": ["Peg diameter", "Peg spacing"],
    },
    "pencil_holder": {
        "key": "pencil_holder",
        "label": "Pencil holder",
        "description": (
            "A desktop pencil holder with round compartments for the pens and pencils"
        ),
        "a": {
            "model": "pencil_holder_2x2",
            "label": "2x2 pencil holder",
            "physical": "Printed 2x2 pencil holder",
        },
        "b": {
            "model": "pencil_holder_2x3",
            "label": "2x3 pencil holder",
            "physical": "Printed 2x3 pencil holder",
        },
        "differences": ["2x2 becomes 2x3", "Aspect ratio changed"],
        "unchanged": ["Hole diameter", "Hole spacing"],
    },
    "cane_tip": {
        "key": "cane_tip",
        "label": "Cane tip",
        "description": "A hook style marshmallow white cane tip",
        "a": {
            "model": "cane_tip_hook",
            "label": "Hook cane tip",
            "physical": "Printed hook cane tip",
        },
        "b": {
            "model": "cane_tip_fitted",
            "label": "Fitted cane tip",
            "physical": "Printed fitted cane tip",
        },
        "differences": [
            "Hook becomes fitted",
            "The hook is a flat tab, not a round tube",
            "Step down changes from smooth",
        ],
        "unchanged": ["Marshmallow"],
    },
    "coat_rack": {
        "key": "coat_rack",
        "label": "Coat rack",
        "description": "A miniature of a wall-mounted coat rack with pegs",
        "a": {
            "model": "coat_hanger_30",
            "label": "30 mm peg coat rack",
            "physical": "Printed 30 mm peg coat rack",
        },
        "b": {
            "model": "coat_hanger_40",
            "label": "40 mm peg coat rack",
            "physical": "Printed 40 mm peg coat rack",
        },
        "differences": [
            "30 mm pegs become 40 mm pegs",
            "The rack is taller",
            "Peg taper is more gradual",
            "Peg angle changed slightly",
        ],
        "unchanged": ["Backplate", "Peg count", "Peg spacing", "Ball size"],
    },
}

PRACTICE_PAIR = "cup"
MAIN_PAIRS = ("pencil_holder", "cane_tip", "coat_rack")

# The model used for onboarding. Not a pair: nothing is compared against it, it is
# the object the participant is given in the hand while the controls are explained.
ONBOARDING_MODEL = "mug"

# A balanced design over the three main pairs, taken two at a time. Rows 1-3 are
# the cyclic Latin square; rows 4-6 are those rows reversed. Across six
# participants every pair appears twice in first position and twice in second,
# and every ordered pair of distinct models occurs exactly once -- which is what
# stops "found more differences in the second task" from being confounded with
# "the second task was always the coat rack".
_LATIN_SQUARE_ROWS: tuple[tuple[str, ...], ...] = (
    ("pencil_holder", "cane_tip", "coat_rack"),
    ("cane_tip", "coat_rack", "pencil_holder"),
    ("coat_rack", "pencil_holder", "cane_tip"),
    ("pencil_holder", "coat_rack", "cane_tip"),
    ("cane_tip", "pencil_holder", "coat_rack"),
    ("coat_rack", "cane_tip", "pencil_holder"),
)


def assign_task_order(sequence_number: int) -> list[str]:
    """Return the main model pairs for the ``sequence_number``-th participant.

    ``sequence_number`` is 1-based enrollment order, assigned by the database when
    the participant code is first seen, so the balance holds automatically as long
    as participants are enrolled through the control panel. The experimenter can
    still override the result at enrollment (someone has already seen the coat
    rack in a pilot, a print is missing) -- this is the default, not a constraint.
    """
    if sequence_number < 1:
        sequence_number = 1
    row = _LATIN_SQUARE_ROWS[(sequence_number - 1) % len(_LATIN_SQUARE_ROWS)]
    return list(row[:TASKS_PER_SESSION])


def latin_square_preview() -> list[dict[str, Any]]:
    """The full assignment table, so the control panel can show the experimenter
    which pairs a participant is due before enrolling them -- the "cheat sheet for
    which two models to show people" the protocol asks for."""
    return [
        {
            "sequence_number": index + 1,
            "task_order": list(row[:TASKS_PER_SESSION]),
            "labels": [MODEL_PAIRS[key]["label"] for key in row[:TASKS_PER_SESSION]],
        }
        for index, row in enumerate(_LATIN_SQUARE_ROWS)
    ]


# ---------------------------------------------------------------------------
# Viewer state every model auto-load starts from (issue #163).
#
# view "x-" is 180 degrees of yaw from the viewer's own x+ default, which is what
# puts the mug upright with the handle to the right and matches the orientation of
# the 3D print the participant is holding. Cut mode and 50% depth are what the
# pilot found people actually used; a fresh load in Filled at some other depth
# cost minutes of the session before anything could be felt.
# ---------------------------------------------------------------------------

VIEWER_DEFAULTS: dict[str, Any] = {
    "view": "x-",
    "depth": 50,
    "render_mode": "cut",
    "representation_mode": "single",
    "compose_scrollbar": True,
    "zoom": 0.0,
    "reset_pan": True,
}


# ---------------------------------------------------------------------------
# Questionnaires
#
# These are here to be *read*, not to be filled in. Every question is asked
# verbally and the answer is written on the experimenter's own sheet -- the app
# offers no fields for them and stores none of them, which is what the team
# decided after the 2026-08-04 pilot: participants are tired by the time the
# rating scale comes round and give better answers in conversation than by
# working through a form.
#
# They live in the panel so the experimenter has one place to look during a
# session instead of a second document open beside it.
# ---------------------------------------------------------------------------

_SCREEN_READER_LEVELS = "Expert / Comfortable / Can use / Beginner / Don't use"

BACKGROUND_QUESTIONS: list[dict[str, Any]] = [
    {
        "text": (
            "How does your vision impact your ability to work with computers and "
            "graphics programs?"
        )
    },
    {
        "text": "If you use a screen reader, how comfortable are you with each of these?",
        "note": f"Read each row aloud. Scale for each: {_SCREEN_READER_LEVELS}.",
        "options": ["NVDA", "JAWS", "VoiceOver", "TalkBack", "Other (which?)"],
    },
    {
        "text": "Describe your experience with Braille, if any.",
        "note": "Braille is not required for this study. This is for context only.",
    },
    {
        "text": "(If relevant) How often do you currently use Braille?",
        "options": ["Never", "Rarely", "Sometimes", "Often", "Daily"],
    },
    {
        "text": "Have you used tactile graphics before?",
        "options": ["Never", "In the past, but no longer", "Occasionally", "Regularly"],
    },
    {
        "text": "How much do you like using tactile graphics?",
        "options": [
            "Not at all",
            "A little",
            "Somewhat",
            "Very much",
        ],
    },
    {
        "text": "How comfortable are you with tactile graphics?",
        "options": [
            "Not at all comfortable",
            "A little comfortable",
            "Somewhat comfortable",
            "Very comfortable",
        ],
    },
    {
        "text": (
            "Have you used a refreshable tactile pin display, such as a braille "
            "display with tactile graphics capability, before?"
        ),
        "options": ["No", "Yes -- ask them to describe it"],
    },
    {
        "text": "Do you have any experience with CAD or 3D modeling tools?",
        "options": ["No", "Yes -- ask them to describe it"],
    },
    {
        "text": "Do you have any experience with 3D printing?",
        "options": ["No", "Yes -- ask them to describe it"],
    },
    {
        "text": (
            "Describe any experiences you have exploring physical 3D models or "
            "objects to understand their structure -- tactile museum exhibits, 3D "
            "printed models, physical prototypes."
        )
    },
]

RATING_SCALE_NOTE = "Record each answer from 1, strongly disagree, to 5, strongly agree."

RATING_ITEMS: list[dict[str, Any]] = [
    {"text": "I knew where I was in the model as I explored."},
    {"text": "I learned about the model as I explored."},
    {
        "text": (
            "I felt confident that I found all the differences between the two "
            "different models."
        )
    },
    {"text": "I found switching between axes helpful."},
    {"text": "I found moving forward and backward through slices helpful."},
    {"text": "Overall, how would you rate the system?"},
]

# Open-ended, never leading. Available to the experimenter at every exploration
# step; using one is logged so the transcript can be read against what was asked.
FACILITATOR_PROMPTS = [
    "What are you noticing right now?",
    "How does this compare to what you expected?",
    "What does this remind you of?",
    "Is anything surprising or unclear?",
    "Can you say more about that?",
    "Is it hollow?",
    "What do you think that is?",
]

# Shown only when the experimenter marks the participant as stuck, because they
# name a specific control and so steer the exploration.
STRATEGY_PROMPTS = [
    "You might try turning the object to look at a different face; press I, K, J, "
    "L, U or O.",
    "You might try returning to an earlier slice; press Dot 1 or Dot 4 to go "
    "shallower.",
    "You might try a different rendering; press R to cycle the render mode.",
    "You might check where you are; press the period key to hear the current state.",
]


# ---------------------------------------------------------------------------
# Steps
#
# ``script`` is a list of blocks rather than a wall of prose, because a step is
# rarely just words to read out. Most mix something to do, something to say and
# something to keep in mind, and running them together makes the experimenter
# work out which is which mid-session -- reading an instruction aloud, or
# performing a sentence. Each block declares what it is:
#
#   do    an action the experimenter performs
#   say   words to read to the participant, shown in quotes
#   ask   questions to ask verbally; may carry a list of questions
#   note  context or a reminder, never spoken
#
# The panel renders the label alongside each block, so the distinction survives
# for an experimenter using a screen reader rather than relying on how the text
# happens to look.
#
# ``model`` is a *reference*, not a filename, because two of the four pairs are
# chosen per participant. Resolution happens in ``resolve_steps``:
#   {"kind": "fixed", "model": "mug"}      -> always that model
#   {"kind": "practice", "version": "a"}   -> the practice pair's first model
#   {"kind": "task", "slot": 1, "version": "b"}
#                                          -> the second model of the participant's
#                                             first assigned pair
# A step with no ``model`` leaves whatever is on the display alone: the opening,
# questionnaire and discussion parts are conversation, and blanking the display
# under the participant's fingers mid-sentence would be its own small disaster.
# ---------------------------------------------------------------------------

_EXPLORATION_PROMPTS = {
    "facilitator_prompts": FACILITATOR_PROMPTS,
    "strategy_prompts": STRATEGY_PROMPTS,
}


def _do(text: str) -> dict[str, Any]:
    return {"kind": "do", "text": text}


def _say(text: str) -> dict[str, Any]:
    return {"kind": "say", "text": text}


def _note(text: str) -> dict[str, Any]:
    return {"kind": "note", "text": text}


def _ask(text: str, questions: list[dict[str, Any]] | None = None, note: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"kind": "ask", "text": text}
    if questions:
        block["questions"] = questions
    if note:
        block["note"] = note
    return block


def _pair_steps(
    part_id: str,
    part_title: str,
    model_ref_kind: str,
    slot: int | None,
    *,
    is_practice: bool = False,
    intro_script: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The four exploration steps every model pair shares (Part A on the display
    and in the hand, then Part B the same way), parameterised by which pair they
    are for.

    Written once rather than four times because the wording is identical across
    pairs by design -- the protocol keeps the prompts constant so that differences
    between tasks are differences between models, not between scripts.

    ``intro_script`` is prepended to Part A's virtual step rather than living in
    its own step: a separate "introduce the object" step that says nothing else
    was a step change for one line of narration, with nothing for the
    participant to do in it.
    """

    def ref(version: str) -> dict[str, Any]:
        if model_ref_kind == "practice":
            return {"kind": "practice", "version": version}
        return {"kind": "task", "slot": slot, "version": version}

    # Only the Lego brick is practice. Everything after it is a real task, so
    # nothing here may describe the participant's work as a rehearsal.
    closing_note = (
        "This is the practice round. Do not say so until it is over."
        if is_practice
        else "This is a real task. Take as long as the participant needs."
    )

    return [
        {
            "id": f"{part_id}.a.virtual",
            "part_id": part_id,
            "part_title": part_title,
            "title": "Part A - explore the model on the display",
            "script": [
                *(intro_script or []),
                _say("Imagine that you downloaded a {label} to print out. It is a {description}"),
                _do("Advance the step so the model loads on the participant's display."),
                _say(
                    "There's no time limit and nothing to get right. I'm interested in "
                    "how you go about figuring it out, so please keep talking as you go."
                ),
                _note(closing_note),
            ],
            "participant_text": (
                "Explore the model on the display and describe what you find. Take "
                "as long as you like."
            ),
            "model": ref("a"),
            **_EXPLORATION_PROMPTS,
        },
        {
            "id": f"{part_id}.a.physical",
            "part_id": part_id,
            "part_title": part_title,
            "title": "Part A - hand over the printed model",
            "script": [
                _do("Give the participant the printed version of the same object."),
                _ask(
                    "Ask, and keep them thinking aloud:",
                    questions=[
                        {"text": "How does this compare with what you had built up in your head?"},
                        {"text": "Was there anything here you did not get from the display?"},
                        {"text": "Was there anything the display told you that this confirms?"},
                    ],
                ),
            ],
            "participant_text": (
                "Feel the printed model and describe how it compares with what you "
                "explored on the display."
            ),
            "physical_model": {"kind": model_ref_kind, "slot": slot, "version": "a"},
            "model": ref("a"),
            **_EXPLORATION_PROMPTS,
        },
        {
            "id": f"{part_id}.b.virtual",
            "part_id": part_id,
            "part_title": part_title,
            "title": "Part B - explore the edited model on the display",
            "script": [
                _say(
                    "Now imagine that you found an older style of this object and want "
                    "to compare the differences."
                ),
                _do("Advance the step so the second version loads on the display."),
                _say(
                    "There's another version on the display now. Your goal is to work "
                    "out how it differs from the one you were just exploring. Keep "
                    "thinking aloud."
                ),
                _note(
                    "Do not say what changed, or which version is newer. The answer key "
                    "is below for your reference only."
                ),
            ],
            "participant_text": (
                "A second version of the object is on the display. Describe how it "
                "differs from the one you explored before."
            ),
            "model": ref("b"),
            **_EXPLORATION_PROMPTS,
        },
        {
            "id": f"{part_id}.b.physical",
            "part_id": part_id,
            "part_title": part_title,
            "title": "Part B - hand over the printed edited model",
            "script": [
                _do("Give the participant the printed version of the second object."),
                _ask(
                    "Ask, and keep them thinking aloud:",
                    questions=[
                        {"text": "Has your sense of what changed shifted now that you can feel it?"},
                        {"text": "If so, how?"},
                        {"text": "Was there a difference you only found by touch?"},
                    ],
                ),
            ],
            "participant_text": (
                "Feel the second printed model and say whether your sense of what "
                "changed has shifted."
            ),
            "physical_model": {"kind": model_ref_kind, "slot": slot, "version": "b"},
            "model": ref("b"),
            **_EXPLORATION_PROMPTS,
        },
    ]


def _build_steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []

    # -- Part 1: settling in -----------------------------------------------
    #
    # Consent is not here. It is taken before the session: the participant only
    # has this link because they have already consented, so asking again would
    # be both redundant and a strange way to open.
    steps.append(
        {
            "id": "opening.settle",
            "part_id": "opening",
            "part_title": "Getting started",
            "title": "Settle the participant in",
            "script": [
                _note(
                    "Confirm that the participant consented before this session, using the google form. "
                    "If not, walk them through the consent form. "
                    "You should also work to put them at ease in this step."
                ),
                _do("Introduce yourself, and anyone else in the room or on the call."),
                _say(
                    "Thanks for coming. Before we start, a quick sense of what we'll "
                    "do: I'll show you how the system works on a mug, and then you'll "
                    "explore a couple of objects and compare two versions of each."
                ),
                _say(
                    "There are no right answers and nothing is being tested about you. "
                    "I'm interested in how you go about it, so I'll ask you to keep "
                    "talking as you explore."
                ),
                _say("Take as long as you like at every step, and ask me anything as we go."),
                _do("Start the audio and video recording."),
                _say("I'm starting the recording now, as we described when you signed up."),
            ],
            "participant_text": "Let's get started.",
            "checklist": [
                "Introductions done",
                "Participant knows what the session will involve",
                "Recording started",
            ],
        }
    )

    # -- Part 2: setting up ------------------------------------------------
    steps.append(
        {
            "id": "opening.setup",
            "part_id": "opening",
            "part_title": "Getting started",
            "title": "Set up the machine and the display",
            "script": [
                _note(
                    "Confirm the pin display is connected.d It connects over Bluetooth, "
                    "which only works in Chrome, so it has to be Chrome. "
                ),
                _do("Check they are in Chrome, and help them pair the pin display."),
                _do(
                    "Put the laptop to the right of the pin display. The arrow keys "
                    "and IJKL are easier to reach with the right hand while the left "
                    "hand reads."
                ),
                _say(
                    "Two things before you touch anything. Press lightly while the "
                    "display is refreshing -- pressing hard while the pins move can "
                    "stop it updating properly."
                ),
                _say(
                    "And the model on the display is not to scale. It's zoomed in, so "
                    "it gives you more detail than the printed version you'll hold."
                ),
                _do("Lay out the printed models for the onboarding and both tasks."),
            ],
            "participant_text": "Your experimenter will check that your display is connected.",
            "checklist": [
                "Participant is in Chrome",
                "Braille display paired and connected",
                "Laptop positioned to the right of the display",
                "Told: press lightly while the display refreshes",
                "Told: the display is not to scale, it is zoomed in for detail",
                "Printed models for onboarding and both tasks laid out",
            ],
        }
    )

    # -- Part 3: background questions --------------------------------------
    steps.append(
        {
            "id": "background.questionnaire",
            "part_id": "background",
            "part_title": "Background questions",
            "title": "Background questions",
            "script": [
                _say(
                    "This questionnaire helps us understand your background and "
                    "experience. There are no right or wrong answers. Your responses "
                    "will be used only to provide context for the study and will not "
                    "affect your participation."
                ),
                _ask(
                    "Ask each of these and write the answers on your own sheet.",
                    questions=BACKGROUND_QUESTIONS,
                    note=(
                        "Asked verbally. The app records nothing from this step -- the "
                        "answers exist only on your sheet."
                    ),
                ),
            ],
            "participant_text": (
                "Your experimenter will ask a few background questions. There are no "
                "right or wrong answers."
            ),
            "checklist": ["Background questions asked and written down"],
        }
    )

    # -- Part 4: onboarding on the mug -------------------------------------
    steps.append(
        {
            "id": "onboarding.explain",
            "part_id": "onboarding",
            "part_title": "System onboarding",
            "title": "Explain slicing with the printed mug",
            "script": [
                _do("Put the printed mug in the participant's hands. Let them feel the whole object."),
                _say(
                    "Instead of showing you the whole mug at once, the display shows a "
                    "slice through it -- like cutting the mug and showing you the face "
                    "of the cut."
                ),
                _do(
                    "Hand them the sliced mug plaques one at a time, showing the same "
                    "slice on the braille display as you do, so the physical slice and "
                    "the display are in their hands together."
                ),
                _note(
                    "Keep this experiential. Talk about turning the object and which "
                    "way it is facing, not about x, y and z."
                ),
            ],
            "participant_text": "Feel the printed mug while your experimenter explains slicing.",
            "model": {"kind": "fixed", "model": ONBOARDING_MODEL},
            "physical_model": {"kind": "literal", "label": "Printed mug and sliced mug plaques"},
            "checklist": [
                "Participant has felt the whole mug",
                "Participant has felt at least two sliced plaques with the matching display",
            ],
        }
    )
    steps.append(
        {
            "id": "onboarding.depth",
            "part_id": "onboarding",
            "part_title": "System onboarding",
            "title": "Moving the slice",
            "script": [
                _do("Hand the computer back. The participant presses the keys, not you."),
                _say(
                    "You're looking at a slice through the middle of the mug. Press "
                    "Dot 1 to move the slice one way and Dot 4 to move it the "
                    "other. Each press moves it one percent. Try that now and tell me "
                    "what you notice changing."
                ),
                _say(
                    "You can also use Dot 1 2 together and Dot 4 5 together  to move ten percent at a "
                    "time. "
                ),
                _note(
                    "Show them where the dots are. Say that second line only if one percent is too slow to produce a "
                    "noticeable change. "
                ),
                _note(
                    "Which direction reads as deeper depends on how the mug is facing, "
                    "and its orientation on load decides which end is zero percent. "
                    "Check that yourself before the session so you are not telling the "
                    "participant the wrong direction."
                ),
            ],
            "participant_text": (
                "Try Dot 1 and Dot 4 to move the slice, and Dot 1 2 or Dot 4 5  "
                "for larger steps."
            ),
            "model": {"kind": "fixed", "model": ONBOARDING_MODEL},
            "checklist": ["Participant has moved the slice in both directions"],
            **_EXPLORATION_PROMPTS,
        }
    )
    steps.append(
        {
            "id": "onboarding.orientation",
            "part_id": "onboarding",
            "part_title": "System onboarding",
            "title": "Turning the object",
            "script": [
                _say(
                    "Now let's look at it from somewhere else. you can turn the object"
                    " around any axis to see it from a different direction"
                ),
                _ask(
                    "Before they press anything, ask:",
                    questions=[{"text": "Press I to tip it up. What do you expect will change?"}],
                ),
                _ask(
                    "After the turn, ask:",
                    questions=[{"text": "Tell me what you notice that's different."}],
                ),
                _do(
                    "Show the six turning keys with the mug in their hands rather than "
                    "in words: J and L turn it one way, I and K tip it, U and O spin it "
                    "towards and away from them."
                ),
            ],
            "participant_text": (
                "Turn the object with I and K, J and L, U and O, and notice what "
                "changes."
            ),
            "model": {"kind": "fixed", "model": ONBOARDING_MODEL},
            "checklist": [
                "Participant has changed which face they are slicing at least once",
                "Participant noticed the change",
            ],
            **_EXPLORATION_PROMPTS,
        }
    )
    steps.append(
        {
            "id": "onboarding.modes",
            "part_id": "onboarding",
            "part_title": "System onboarding",
            "title": "Rendering mode and reading back position",
            "script": [
                _say("Press R to change how the slice is drawn (we call this a rendering mode). Tell me what's different about it."),
                _say(
                    "Now press the period key. That reads back where you are. Does it "
                    "match where you thought you were?"
                ),
                _note("If they ask for the full list of commands, point them at H."),
            ],
            "participant_text": (
                "Press R to change how the slice is rendered, and the period key to hear "
                "where you are. Press H for the full list of shortcuts."
            ),
            "model": {"kind": "fixed", "model": ONBOARDING_MODEL},
            **_EXPLORATION_PROMPTS,
        }
    )
    steps.append(
        {
            "id": "onboarding.think_aloud",
            "part_id": "onboarding",
            "part_title": "System onboarding",
            "title": "Think-aloud training",
            "script": [
                _say(
                    "For the rest of the session I'd like you to think aloud. Say what "
                    "you're about to press, what you expect it to do, and what you find. "
                    "It helps me understand how you're building a picture of the object."
                ),
                _do(
                    "Give a worked example on the mug: say what you are about to press, "
                    "what you expect, then what you found."
                ),
                _do("Ask them to try it on the mug while you listen."),
                _say("Questions about the interface are welcome at any point."),
            ],
            "participant_text": (
                "Say what you are about to press, what you expect, and what you find. "
                "Questions are welcome at any time."
            ),
            "model": {"kind": "fixed", "model": ONBOARDING_MODEL},
            "checklist": ["Participant has thought aloud on the mug at least once"],
        }
    )
    steps.append(
        {
            "id": "onboarding.free_explore",
            "part_id": "onboarding",
            "part_title": "System onboarding",
            "title": "Free exploration",
            "script": [
                _say(
                    "Now explore freely. Rotate it, change slice position, and change rendering "
                    "mode. Tell me where you want to go and what you're finding."
                ),
                _note(
                    "Let this run until they seem to have run out of interest and look "
                    "comfortable. Do not move on until both boxes below are true."
                ),
            ],
            "participant_text": "Explore the mug freely. Tell me where you want to go and what you find.",
            "model": {"kind": "fixed", "model": ONBOARDING_MODEL},
            "checklist": [
                "Moved the slice in both directions",
                "Changed the face being sliced and noticed the change",
                "Appears comfortable with the controls",
            ],
            **_EXPLORATION_PROMPTS,
        }
    )


    # -- The two assigned model pairs -----------------------
    #
    # These are the real tasks. 
    for slot in range(1, TASKS_PER_SESSION + 1):
        part_id = f"task{slot}"
        part_title = f"Task {slot}"
        if slot == 1:
            intro_script = [
                _say(
                    "Now that you've seen how this works on the mug, we'll do the same "
                    "thing for real, with a different object."
                ),
            ]
        else:
            intro_script = [
                _say("Now we will do the same thing again with one more object."),
            ]
        steps.extend(_pair_steps(part_id, part_title, "task", slot, intro_script=intro_script))
        steps.append(
            {
                "id": f"{part_id}.rating",
                "part_id": part_id,
                "part_title": part_title,
                "title": "Rating scale",
                "script": [
                    _say(
                        "Before we move on, I'd like to ask a few quick questions about "
                        "how that felt. Please rate each from 1, strongly disagree, to "
                        "5, strongly agree."
                    ),
                    _ask(
                        "Read each item aloud and write the answer on your own sheet.",
                        questions=RATING_ITEMS,
                        note=RATING_SCALE_NOTE
                        + " Asked verbally; the app records nothing from this step.",
                    ),
                ],
                "participant_text": (
                    "Your experimenter will read a few statements. Rate each from 1, "
                    "strongly disagree, to 5, strongly agree."
                ),
                "checklist": ["Rating scale asked and written down"],
                "task_slot": slot,
            }
        )

    # -- Post-session discussion -----------------------------------
    steps.append(
        {
            "id": "discussion.approach",
            "part_id": "discussion",
            "part_title": "Post-session discussion",
            "title": "How they went about it",
            "script": [
                _note("Take the notes yourself. Do not ask the participant to write anything."),
                _ask(
                    "Guide a short conversation around these:",
                    questions=[
                        {
                            "text": (
                                "Walk me through how you approached exploring the "
                                "models. Did that approach change over time?"
                            )
                        },
                        {
                            "text": (
                                "How confident did you feel overall, and did that change "
                                "over time?"
                            )
                        },
                        {
                            "text": (
                                "Can you tell me up to three things you liked, or "
                                "disliked, about how CadA11y supported model exploration?"
                            )
                        },
                    ],
                ),
            ],
            "participant_text": "A few questions about how the session went.",
        }
    )
    steps.append(
        {
            "id": "discussion.reflection",
            "part_id": "discussion",
            "part_title": "Post-session discussion",
            "title": "Open-ended reflection",
            "script": [
                _ask(
                    "Ask each of these and note the answers:",
                    questions=[
                        {"text": "How might this system be useful to you in real-world contexts?"},
                        {
                            "text": (
                                "Is there anything else we should consider before we "
                                "release CadA11y to the community?"
                            )
                        },
                        {"text": "Any final comments on the study?"},
                    ],
                ),
            ],
            "participant_text": "A few last questions, then we're done.",
        }
    )
    steps.append(
        {
            "id": "discussion.close",
            "part_id": "discussion",
            "part_title": "Post-session discussion",
            "title": "Close the session",
            "script": [
                _say("That's everything. Thank you -- this was really useful."),
                _do("Stop the recording."),
                _do(
                    "Arrange compensation: $40 per hour by Amazon or Visa gift card, a "
                    "lower amount if they have asked for one to protect benefits, plus "
                    "up to $30 travel."
                ),
                _do(
                    "End the session in this panel. That writes the final log entry and "
                    "closes the record."
                ),
            ],
            "participant_text": "That's the end of the session. Thank you.",
            "checklist": [
                "Recording stopped",
                "Compensation arranged",
                "Your own notes and answer sheets complete",
            ],
        }
    )

    return steps


STEPS: list[dict[str, Any]] = _build_steps()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve_pair_key(ref: dict[str, Any], task_order: list[str]) -> str | None:
    kind = ref.get("kind")
    if kind == "practice":
        return PRACTICE_PAIR
    if kind == "task":
        slot = int(ref.get("slot") or 0)
        if 1 <= slot <= len(task_order):
            return task_order[slot - 1]
    return None


def _resolve_model(ref: dict[str, Any] | None, task_order: list[str]) -> dict[str, Any] | None:
    """Turn a step's model reference into a concrete model stem plus its labels."""
    if not ref:
        return None
    if ref.get("kind") == "fixed":
        stem = str(ref.get("model") or "")
        return {"model": stem, "label": stem, "pair_key": None, "version": None} if stem else None

    pair_key = _resolve_pair_key(ref, task_order)
    if not pair_key:
        return None
    pair = MODEL_PAIRS.get(pair_key)
    if not pair:
        return None
    version = str(ref.get("version") or "a")
    entry = pair.get(version) or {}
    return {
        "model": entry.get("model"),
        "label": entry.get("label"),
        "pair_key": pair_key,
        "pair_label": pair.get("label"),
        "version": version,
    }


def _resolve_physical(ref: dict[str, Any] | None, task_order: list[str]) -> str | None:
    if not ref:
        return None
    if ref.get("kind") == "literal":
        return ref.get("label")
    pair_key = _resolve_pair_key(ref, task_order)
    if not pair_key:
        return None
    pair = MODEL_PAIRS.get(pair_key) or {}
    entry = pair.get(str(ref.get("version") or "a")) or {}
    return entry.get("physical")


def resolve_steps(task_order: list[str] | None) -> list[dict[str, Any]]:
    """Return the protocol's steps with this participant's models substituted in.

    Everything the two front-ends need is baked in here -- resolved model stem,
    resolved physical-model reminder, the answer key for the pair -- so neither has
    to re-derive it and they cannot disagree about which model belongs to step 12.
    """
    order = list(task_order or [])
    protocol = load_protocol()

    resolved: list[dict[str, Any]] = []
    for index, raw in enumerate(protocol["steps"]):
        step = copy.deepcopy(raw)
        step["index"] = index

        model = _resolve_model(step.get("model"), order)
        step["model"] = model
        step["physical_model"] = _resolve_physical(step.get("physical_model"), order)

        # The pair this step belongs to, for the answer key and for tagging
        # observations with the model they were made about.
        pair_key = None
        if model and model.get("pair_key"):
            pair_key = model["pair_key"]
        elif step.get("task_slot"):
            slot = int(step["task_slot"])
            if 1 <= slot <= len(order):
                pair_key = order[slot - 1]
        step["pair_key"] = pair_key
        pair = protocol["model_pairs"].get(pair_key) if pair_key else None
        step["pair"] = (
            {
                "key": pair_key,
                "label": pair.get("label"),
                "description": pair.get("description"),
                "differences": pair.get("differences", []),
                "unchanged": pair.get("unchanged", []),
            }
            if pair
            else None
        )

        step["script"] = _resolve_script(step.get("script"), pair)

        resolved.append(step)
    return resolved


def _substitute(text: str, pair: dict[str, Any] | None) -> str:
    """Fill {description} and {label} with the pair this participant actually got.

    Done here rather than in the panel so the substitution lives in one place and
    an unresolved placeholder can never be read aloud to a participant.
    """
    if not pair:
        return text
    return (
        text.replace("{description}", str(pair.get("description") or ""))
        .replace("{label}", str(pair.get("label") or ""))
    )


def _resolve_script(script: Any, pair: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalise a step's script to a list of blocks with placeholders filled.

    A plain string is accepted and becomes a single note, so a protocol override
    written before the block format existed still loads rather than rendering an
    empty step in the middle of a session.
    """
    if not script:
        return []
    if isinstance(script, str):
        return [{"kind": "note", "text": _substitute(script, pair)}]

    blocks: list[dict[str, Any]] = []
    for raw in script:
        if isinstance(raw, str):
            blocks.append({"kind": "note", "text": _substitute(raw, pair)})
            continue
        block = dict(raw)
        kind = str(block.get("kind") or "note")
        block["kind"] = kind if kind in ("do", "say", "ask", "note") else "note"
        block["text"] = _substitute(str(block.get("text") or ""), pair)
        if block.get("note"):
            block["note"] = _substitute(str(block["note"]), pair)
        if block.get("questions"):
            block["questions"] = [
                {**question, "text": _substitute(str(question.get("text") or ""), pair)}
                for question in block["questions"]
            ]
        blocks.append(block)
    return blocks


def step_count(task_order: list[str] | None = None) -> int:
    return len(resolve_steps(task_order))


# ---------------------------------------------------------------------------
# Optional runtime override
# ---------------------------------------------------------------------------

def _override_path() -> Path | None:
    """Where an operator-supplied protocol would live, if there is one."""
    env_path = os.getenv("STUDY_PROTOCOL_PATH", "").strip()
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "study" / "protocol.json"


_BUILTIN_PROTOCOL: dict[str, Any] = {
    "version": PROTOCOL_VERSION,
    "source": "builtin",
    "tasks_per_session": TASKS_PER_SESSION,
    "practice_pair": PRACTICE_PAIR,
    "main_pairs": list(MAIN_PAIRS),
    "onboarding_model": ONBOARDING_MODEL,
    "model_pairs": MODEL_PAIRS,
    "steps": STEPS,
    "facilitator_prompts": FACILITATOR_PROMPTS,
    "strategy_prompts": STRATEGY_PROMPTS,
    "viewer_defaults": VIEWER_DEFAULTS,
}

_cached_override: dict[str, Any] | None = None
# Path as well as mtime: keying on mtime alone would serve a stale protocol if
# STUDY_PROTOCOL_PATH were repointed at a different file with the same timestamp.
_cached_override_key: tuple[str, float] | None = None
_override_error: str | None = None


def load_protocol() -> dict[str, Any]:
    """Return the active protocol, preferring a valid runtime override.

    A malformed or unreadable override is ignored rather than raised: a session in
    progress must not be taken down by a typo in an edited file. The reason is kept
    in ``override_error()`` and reported by ``GET /study/config`` so the mistake is
    visible in the control panel instead of failing silently.
    """
    global _cached_override, _cached_override_key, _override_error

    path = _override_path()
    if not path or not path.is_file():
        _cached_override = None
        _cached_override_key = None
        _override_error = None
        return _BUILTIN_PROTOCOL

    try:
        key = (str(path), path.stat().st_mtime)
        if _cached_override is not None and _cached_override_key == key:
            return _cached_override
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("protocol override must be a JSON object")
        merged = {**_BUILTIN_PROTOCOL, **data, "source": str(path)}
        if not isinstance(merged.get("steps"), list) or not merged["steps"]:
            raise ValueError("protocol override has no steps")
        _cached_override = merged
        _cached_override_key = key
        _override_error = None
        return merged
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        _cached_override = None
        _cached_override_key = None
        _override_error = f"{path}: {error}"
        return _BUILTIN_PROTOCOL


def override_error() -> str | None:
    return _override_error


def config_payload() -> dict[str, Any]:
    """Everything the control panel needs before a session exists."""
    protocol = load_protocol()
    return {
        "version": protocol.get("version"),
        "source": protocol.get("source"),
        "override_error": override_error(),
        "tasks_per_session": protocol.get("tasks_per_session"),
        "practice_pair": protocol.get("practice_pair"),
        "main_pairs": protocol.get("main_pairs"),
        "model_pairs": protocol.get("model_pairs"),
        "facilitator_prompts": protocol.get("facilitator_prompts"),
        "strategy_prompts": protocol.get("strategy_prompts"),
        "viewer_defaults": protocol.get("viewer_defaults"),
        "latin_square": latin_square_preview(),
    }


def required_models() -> list[str]:
    """Every model stem the protocol will ask the viewer to load.

    The control panel checks these against the server's model list at enrollment,
    so a missing STL is found before the participant is sitting down rather than
    when the step that needs it fails to load.
    """
    protocol = load_protocol()
    stems = {protocol.get("onboarding_model") or ONBOARDING_MODEL}
    for pair in protocol.get("model_pairs", {}).values():
        for version in ("a", "b"):
            entry = pair.get(version) or {}
            if entry.get("model"):
                stems.add(entry["model"])
    return sorted(stems)
