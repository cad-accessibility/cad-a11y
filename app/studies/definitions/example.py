"""An example study, to copy when starting a new one.

It is short on purpose and uses one of everything: script blocks of each kind, a
fixed practice model, tasks counterbalanced with a balanced Latin square, a
questionnaire step tied to a task, viewer defaults and prompts. Its tasks are
single objects to explore rather than pairs to compare, to show the engine is
not tied to the comparison study's design.

It is a draft, so no server runs it. Its panel token is ``example-panel-token``,
published here and in docs/STUDIES.md so the example works out of the box. That
makes it a token anyone knows, so two things guard it. The registry refuses to
serve any other study whose hash matches it: copying this file without making a
new token fails at load rather than opening a study with a public password.
And this study itself runs only where ``CAD_A11Y_OPEN_STUDIES=example`` comes
with ``CAD_A11Y_ALLOW_EXAMPLE_STUDY=1``, which docker-compose.yml never passes:
``docker-compose.example-study.yml`` sets both, for a development machine and
for CI's accessibility check. ``python -m app.studies token`` makes a new token.

To start a new study from this file:

1. Copy it under ``definitions/`` with a new slug, title and version, and add it
   to ``definitions/__init__.py``.
2. Run ``python -m app.studies token`` and put the hash it prints in
   ``token_hash``. Share the token with the experimenters privately.
3. Write the steps and tasks. ``tests/test_studies_definitions.py`` checks every
   definition, so ``pytest`` tells you what is wrong with one.
4. Try it locally with ``CAD_A11Y_OPEN_STUDIES=<slug>``.
5. Set ``status`` to ``Status.OPEN`` when it should run on the servers.
"""

from __future__ import annotations

from ..definition import Status, Study
from ..protocol import (
    ask,
    balanced_latin_square,
    do,
    fixed_model,
    note,
    say,
    task_model,
)

TOKEN = "example-panel-token"

_TASK_KEYS = ["chair", "washer", "cube"]

_TASKS = {
    # One role, "a", per task. A comparison study gives each task two roles,
    # "a" and "b", and steps that load each in turn; see comparison_2026.py.
    "chair": {
        "label": "Rocking chair",
        "a": {"model": "rocking_chair", "label": "Rocking chair"},
    },
    "washer": {
        "label": "Sleeve washer",
        "a": {"model": "sleeve_washer", "label": "Sleeve washer"},
    },
    "cube": {
        "label": "Labelled cube",
        "a": {"model": "labeled_cube", "label": "Labelled cube"},
    },
}


def _task_steps(slot: int) -> list[dict]:
    part_id = f"task{slot}"
    part_title = f"Task {slot}"
    return [
        {
            "id": f"{part_id}.explore",
            "part_id": part_id,
            "part_title": part_title,
            "title": "Explore the object",
            "script": [
                do("Advance the step so the object loads on the participant's display."),
                say(
                    "There is a new object on the display. Explore it, and tell me what "
                    "you think it is. Take as long as you like."
                ),
                note("Do not name the object. Its name is what the participant is working out."),
            ],
            "participant_text": (
                "Explore the object on the display and say what you think it is."
            ),
            "model": task_model(slot),
        },
        {
            "id": f"{part_id}.questions",
            "part_id": part_id,
            "part_title": part_title,
            "title": "Questions about this object",
            "script": [
                ask(
                    "Ask, and write the answers on your sheet:",
                    questions=[
                        {"text": "What do you think the object is?"},
                        {"text": "How sure are you, from 1, not at all, to 5, completely?"},
                    ],
                    note="Nothing here records the answers. The app keeps interactions only.",
                ),
            ],
            "participant_text": "Your experimenter has a few questions about that object.",
            # No model, so the display keeps the object while they answer; the
            # slot ties the step to its task, for the panel.
            "task_slot": slot,
        },
    ]


_STEPS = [
    {
        "id": "opening",
        "part_id": "opening",
        "part_title": "Opening",
        "title": "Settling in",
        "script": [
            note("Consent is taken before the session. This step is for settling in."),
            say("Thank you for joining. We will start with a few minutes of practice."),
            do("Check the participant's screen reader is speaking and the display is connected."),
        ],
        "participant_text": "Welcome. Your experimenter will start in a moment.",
        "checklist": ["Screen reader speaking", "Display connected"],
    },
    {
        "id": "practice",
        "part_id": "practice",
        "part_title": "Practice",
        "title": "Practise with the keys",
        "script": [
            say(
                "Here is an object to practise on. The arrow keys move the slice, and H "
                "lists every command."
            ),
            note("Answer any question about the interface here, before the tasks."),
        ],
        "participant_text": "Practise with the keys. Press H for the list of commands.",
        "model": fixed_model("mug"),
    },
    *[step for slot in range(1, len(_TASK_KEYS) + 1) for step in _task_steps(slot)],
    {
        "id": "closing",
        "part_id": "closing",
        "part_title": "Closing",
        "title": "Thank the participant",
        "script": [
            say("That is everything. Thank you."),
            do("Finish the session from the panel."),
        ],
        "participant_text": "That is everything. Thank you.",
    },
]

STUDY = Study(
    slug="example",
    title="Example study",
    status=Status.DRAFT,
    version="1",
    summary="Each participant practises on a mug, then explores three objects in a balanced order.",
    steps=_STEPS,
    token_hash="scrypt$32768$8$1$QUHP51Lqwv6tx2TCSod0Gw$Y82vKNwI1nIZpnQVP2A6E0L7y2IBE6Jlm2davgZkRkA",
    tasks=_TASKS,
    design=balanced_latin_square(_TASK_KEYS),
    viewer_defaults={
        "axis_mode": "xyz",
        "view": "y-",
        "depth": 50,
        "render_mode": "cut",
        "representation_mode": "single",
        "compose_scrollbar": True,
        "zoom": 0.0,
        "reset_pan": True,
    },
    model_labels={"a": "Object"},
    facilitator_prompts=[
        "What are you noticing right now?",
        "Can you say more about that?",
    ],
    strategy_prompts=[
        "You might try moving the slice; press the up or down arrow.",
        "You might check where you are; press the period key.",
    ],
)
