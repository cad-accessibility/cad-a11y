"""The comparison study, closed.

Each participant explored a printed and a virtual mug to learn the system, then
two of three model pairs -- a Lego brick, a pencil holder and a cane tip -- each
as an original and an edited version, describing what changed. It ran in 2026
at ``/study``, and the last code that served it is tagged
``study-instrument-2026``.

Closed means the only thing served for it is its control panel's data
downloads, behind its own token, so its data can come off both servers without
shell access. No session can start and there is no participant page. Once the
data is exported, checked and stored, set it to ``Status.RETIRED`` and remove
``token_hash``; then nothing is served for it at all.

The definition stays either way, because it is the record of what every session
in its data was asked to do, step by step. Its data stays where it was written,
in ``data/db/study.db`` and ``data/logs/study/``, from before studies had
directories of their own.

Nothing below has been edited since it ran beyond what moving it here needed,
with one addition: ``VIEWER_DEFAULTS`` names Turn as the axis mode, the only
mode there was when it ran. Two things this file cannot settle. The code that
served it read ``data/study/protocol.json`` instead of its built-in protocol
when that file existed, and whether either server had one is not known. And
its sessions carry no ``protocol_hash``, which came later, so the data cannot
say which protocol they ran. What a step may carry and how the references
resolve is in ``protocol.py``.

Counterbalancing
----------------
Each participant got **two** of the three pairs. Taking three objects two at a
time in order gives six sets, and running all six once is one balanced round:
every object appears twice first and twice second, so "found more in the second
task" cannot be confounded with "the second task was always the cane tip".
Randomising a sample this small routinely produces an unbalanced set, which is
exactly what counterbalancing is for.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..definition import Status, Storage, Study, repo_root
from ..protocol import ask as _ask
from ..protocol import do as _do
from ..protocol import note as _note
from ..protocol import say as _say

PROTOCOL_VERSION = "2026-08-04"

# How many of the three model pairs one participant sees. Two is what fits a
# 60-90 minute session: the 2026-08-04 pilot spent most of an hour reaching the
# end of the first pair. Three taken two at a time is also what makes a round
# six sets long -- see ``protocol.task_sets``.
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
}

# The coat rack was a fourth pair and is no longer part of the study. Its STLs
# still ship, because they are used by the render-cache tests and are perfectly
# good models -- they are simply not something a participant is asked to explore.
# Sessions already recorded against it keep their task_order; those keys resolve
# to no pair, which is what the resolver already does for an unassigned slot.
MAIN_PAIRS = ("pencil_holder", "cane_tip", "lego")

# The model used for onboarding. Not a pair: nothing is compared against it, it is
# the object the participant is given in the hand while the controls are explained.
ONBOARDING_MODEL = "mug"

# A balanced design over the three main pairs, taken two at a time. Rows 1-3 are
# the cyclic Latin square; rows 4-6 swap the last two of each. Across six
# participants every pair appears twice in first position and twice in second,
# and every ordered pair of distinct models occurs exactly once -- which is what
# stops "found more differences in the second task" from being confounded with
# "the second task was always the cane tip".
_LATIN_SQUARE_ROWS: tuple[tuple[str, ...], ...] = (
    ("pencil_holder", "cane_tip", "lego"),
    ("cane_tip", "lego", "pencil_holder"),
    ("lego", "pencil_holder", "cane_tip"),
    ("pencil_holder", "lego", "cane_tip"),
    ("cane_tip", "pencil_holder", "lego"),
    ("lego", "cane_tip", "pencil_holder"),
)



# ---------------------------------------------------------------------------
# Viewer state every model auto-load starts from (issue #163).
#
# view "x-" is 180 degrees of yaw from the viewer's own x+ default. It is OpenSCAD's
# Right view, seen from +X, so Y increases to the right and Z up: the mug stands
# upright, and its handle, which points toward -Y in mug.stl, is at the LEFT edge.
# This comment used to say the right edge, which the geometry never supported;
# check the printed mug in the participant's hand against a display before any
# script names a side. Cut mode and 50% depth are what the pilot found people
# actually used; a fresh load in Filled at some other depth cost minutes of the
# session before anything could be felt.
# ---------------------------------------------------------------------------

VIEWER_DEFAULTS: dict[str, Any] = {
    # Turn mode (pitch, roll, yaw), the mode the protocol was written for. XYZ
    # mode (#185) stays out of the study until it has been piloted.
    "axis_mode": "turn",
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




def _pair_steps(
    part_id: str,
    part_title: str,
    model_ref_kind: str,
    slot: int | None,
    *,
    intro_script: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The four exploration steps every model pair shares (Part A on the display
    and in the hand, then Part B the same way), parameterised by which pair they
    are for.

    Written once rather than twice because the wording is identical across pairs
    by design -- the protocol keeps the prompts constant so that differences
    between tasks are differences between models, not between scripts.

    ``intro_script`` is prepended to Part A's virtual step rather than living in
    its own step: a separate "introduce the object" step that says nothing else
    was a step change for one line of narration, with nothing for the
    participant to do in it.
    """

    def ref(version: str) -> dict[str, Any]:
        return {"kind": model_ref_kind, "slot": slot, "version": version}

    # There is no rehearsal round: onboarding on the mug is where the participant
    # learns the system, and both tasks after it are real. Nothing here may
    # describe the participant's work as practice.
    closing_note = "This is a real task. Take as long as the participant needs."

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
                    "Confirm the pin display is connected. It connects over Bluetooth, "
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


def _legacy_storage() -> Storage:
    """Where this study's data was written. ``STUDY_DB_PATH`` and
    ``STUDY_LOG_DIR`` are the variables the old code read, kept so a copy of
    the data restored somewhere else can be pointed at."""
    root = repo_root() / "data"
    db_path = os.environ.get("STUDY_DB_PATH", "").strip()
    log_dir = os.environ.get("STUDY_LOG_DIR", "").strip()
    return Storage(
        db_path=Path(db_path) if db_path else root / "db" / "study.db",
        log_dir=Path(log_dir) if log_dir else root / "logs" / "study",
    )


STUDY = Study(
    slug="comparison-2026",
    title="Model comparison study (2026)",
    status=Status.CLOSED,
    version=PROTOCOL_VERSION,
    summary=(
        "Each participant explores a mug, then "
        f"{TASKS_PER_SESSION} of the three model pairs."
    ),
    steps=STEPS,
    tasks=MODEL_PAIRS,
    design=_LATIN_SQUARE_ROWS,
    tasks_per_session=TASKS_PER_SESSION,
    viewer_defaults=VIEWER_DEFAULTS,
    model_labels={"a": "First object", "b": "Second object"},
    facilitator_prompts=FACILITATOR_PROMPTS,
    strategy_prompts=STRATEGY_PROMPTS,
    storage=_legacy_storage,
    instrument_tag="study-instrument-2026",
    # Only while it is closed. Retiring it takes this out with it.
    token_hash="scrypt$32768$8$1$qqcD8wlD-ipWeaR4Yf_uVw$Sw4uHZoIXwgX8apWkCT_Lwnrx4L8uyyCfzKOsOGjf-c",
)
