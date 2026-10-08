"""Turning a study definition into what the two pages show.

A definition's steps are written once, with references in place of models,
because which model a step loads depends on which tasks this participant was
given. ``resolve_steps`` fills those in for one session. Everything the panel
and the participant page need is baked in here, so neither re-derives it and
they cannot disagree about which model belongs to step 12.

What a step may carry
---------------------
``id``, ``part_id``, ``part_title``, ``title``
    Where it sits. ``part_id`` is the phase column of the export, which is what
    keeps a keypress during onboarding from being counted as task work.
``script``
    For the experimenter: a list of blocks, each one of

      do    an action the experimenter performs
      say   words to read to the participant, shown in quotes
      ask   questions to ask aloud; may carry a list of questions
      note  context or a reminder, never spoken

    The panel labels every block with its kind, so the difference between
    reading something out and doing it survives a screen reader.
``participant_text``
    What the participant's page says on this step. Nothing else in the step
    reaches the participant's browser.
``model``
    What loads on the display, as a reference:

      {"kind": "fixed", "model": "mug"}            always that model
      {"kind": "task", "slot": 1, "version": "b"}  role "b" of the participant's
                                                   first task

    A step with no model leaves the display alone. Conversation steps should
    not have one: blanking the display under someone's fingers mid-sentence is
    its own small disaster.
``physical_model``
    What to hand over: ``{"kind": "literal", "label": "..."}`` or a task
    reference like ``model``'s, which reads the role's ``physical``.
``checklist``, ``facilitator_prompts``, ``strategy_prompts``, ``task_slot``
    For the panel. ``task_slot`` ties a step without a model (a questionnaire
    after a task, say) to that task's answer key.

Counterbalancing
----------------
``latin_square`` and ``balanced_latin_square`` build a definition's ``design``.
The balanced one is the Williams design: every task appears in every position
equally often and every task follows every other exactly once, so a carryover
from one task to the next is spread evenly too. With an odd number of tasks that
needs twice as many rows, which it returns.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Sequence
from typing import Any

from .definition import Study

# ---------------------------------------------------------------------------
# Writing steps
# ---------------------------------------------------------------------------


def do(text: str) -> dict[str, Any]:
    return {"kind": "do", "text": text}


def say(text: str) -> dict[str, Any]:
    return {"kind": "say", "text": text}


def note(text: str) -> dict[str, Any]:
    return {"kind": "note", "text": text}


def ask(text: str, questions: list[dict[str, Any]] | None = None, note: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"kind": "ask", "text": text}
    if questions:
        block["questions"] = questions
    if note:
        block["note"] = note
    return block


def fixed_model(stem: str) -> dict[str, Any]:
    return {"kind": "fixed", "model": stem}


def task_model(slot: int, version: str = "a") -> dict[str, Any]:
    return {"kind": "task", "slot": slot, "version": version}


# ---------------------------------------------------------------------------
# Counterbalancing
# ---------------------------------------------------------------------------


def latin_square(keys: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """The cyclic Latin square: each task once in every position."""
    n = len(keys)
    return tuple(tuple(keys[(column + row) % n] for column in range(n)) for row in range(n))


def balanced_latin_square(keys: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """The Williams design over ``keys``.

    The first row goes 0, 1, n-1, 2, n-2, ... and each row after it adds one,
    which puts every ordered pair of neighbours in the design exactly once. An
    odd n cannot manage that in n rows, so the reversed rows are added.
    """
    n = len(keys)
    if n == 0:
        return ()
    sequence = [0]
    low, high, take_low = 1, n - 1, True
    while len(sequence) < n:
        if take_low:
            sequence.append(low)
            low += 1
        else:
            sequence.append(high)
            high -= 1
        take_low = not take_low
    rows = [[(start + offset) % n for start in sequence] for offset in range(n)]
    if n % 2:
        rows += [list(reversed(row)) for row in rows]
    return tuple(tuple(keys[index] for index in row) for row in rows)


def set_id(task_order: Sequence[str]) -> str:
    """The stable name of one task set.

    Order matters -- "pencil holder then cane tip" is a different cell of the
    design from "cane tip then pencil holder" -- so the id is the order joined,
    not a sorted key. It is also what the used/unused bookkeeping counts by, so a
    session's stored ``task_order`` maps onto a set without a lookup table.
    """
    return "+".join(str(key) for key in task_order)


def task_label(study: Study, key: str) -> str:
    return str((study.tasks.get(key) or {}).get("label") or key)


def task_sets(study: Study) -> list[dict[str, Any]]:
    """One entry per row of the design, for the experimenter to pick from.

    One round of the study is these run once each. The panel lists them all and
    strikes through the ones already recorded, so choosing is reading a list
    rather than working out where a counter has got to -- and an experimenter
    can deviate deliberately (a print is missing, a participant has seen one of
    these in a pilot) without that silently unbalancing the design, because the
    list still shows what it cost.
    """
    sets = []
    for index, row in enumerate(study.design):
        order = study.session_tasks(row)
        sets.append(
            {
                "id": set_id(order),
                "position": index + 1,
                "task_order": order,
                "labels": [task_label(study, key) for key in order],
            }
        )
    return sets


def assign_task_order(study: Study, sequence_number: int) -> list[str]:
    """The set the ``sequence_number``-th participant would get by rotation.

    The experimenter picks the set in the panel, so this is not the ordinary
    path. It stays because a session must always end up with some assignment:
    the start request may name no set, and answering that with an empty task
    order would produce a session whose task steps load nothing.
    """
    if not study.design:
        return []
    sequence_number = max(1, int(sequence_number))
    return study.session_tasks(study.design[(sequence_number - 1) % len(study.design)])


def design_preview(study: Study) -> list[dict[str, Any]]:
    """The whole assignment table, keyed by the rotation position
    ``assign_task_order`` uses."""
    return [{"sequence_number": entry["position"], **entry} for entry in task_sets(study)]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_task_key(ref: dict[str, Any], task_order: list[str]) -> str | None:
    # Anything but a task reference resolves to nothing, which is deliberate: the
    # comparison study removed its rehearsal round, and a reference to one should
    # leave the display alone rather than load something arbitrary.
    if ref.get("kind") == "task":
        try:
            slot = int(ref.get("slot") or 0)
        except (TypeError, ValueError):
            return None
        if 1 <= slot <= len(task_order):
            return task_order[slot - 1]
    return None


def _resolve_model(study: Study, ref: dict[str, Any] | None, task_order: list[str]) -> dict[str, Any] | None:
    """A step's model reference as a concrete stem plus its labels."""
    if not ref:
        return None
    if ref.get("kind") == "fixed":
        stem = str(ref.get("model") or "")
        return {"model": stem, "label": stem, "task_key": None, "version": None} if stem else None

    task_key = _resolve_task_key(ref, task_order)
    task = study.tasks.get(task_key) if task_key else None
    if not task:
        return None
    version = str(ref.get("version") or "a")
    entry = task.get(version) or {}
    return {
        "model": entry.get("model"),
        "label": entry.get("label"),
        "task_key": task_key,
        "task_label": task.get("label"),
        "version": version,
    }


def _resolve_physical(study: Study, ref: dict[str, Any] | None, task_order: list[str]) -> str | None:
    if not ref:
        return None
    if ref.get("kind") == "literal":
        return ref.get("label")
    task_key = _resolve_task_key(ref, task_order)
    task = study.tasks.get(task_key) if task_key else None
    if not task:
        return None
    return (task.get(str(ref.get("version") or "a")) or {}).get("physical")


def resolve_steps(study: Study, task_order: list[str] | None) -> list[dict[str, Any]]:
    """The study's steps with this session's models substituted in."""
    order = list(task_order or [])
    resolved: list[dict[str, Any]] = []
    for index, raw in enumerate(study.steps):
        step = copy.deepcopy(raw)
        step["index"] = index

        model = _resolve_model(study, step.get("model"), order)
        step["model"] = model
        step["physical_model"] = _resolve_physical(study, step.get("physical_model"), order)

        # The task this step belongs to, for the answer key.
        task_key = None
        if model and model.get("task_key"):
            task_key = model["task_key"]
        elif step.get("task_slot"):
            task_key = _resolve_task_key({"kind": "task", "slot": step["task_slot"]}, order)
        step["task_key"] = task_key
        task = study.tasks.get(task_key) if task_key else None
        step["task"] = (
            {
                "key": task_key,
                "label": task.get("label"),
                "description": task.get("description"),
                "differences": list(task.get("differences") or []),
                "unchanged": list(task.get("unchanged") or []),
            }
            if task
            else None
        )
        step["script"] = _resolve_script(step.get("script"), task)
        resolved.append(step)
    return resolved


def _substitute(text: str, task: dict[str, Any] | None) -> str:
    """Fill {description} and {label} with the task this participant actually got.

    Done here rather than in the panel so the substitution lives in one place and
    an unresolved placeholder can never be read aloud to a participant.
    """
    if not task:
        return text
    return text.replace("{description}", str(task.get("description") or "")).replace(
        "{label}", str(task.get("label") or "")
    )


def _resolve_script(script: Any, task: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A step's script as a list of blocks with placeholders filled. A plain
    string becomes a single note, and an unknown kind degrades to one."""
    if not script:
        return []
    if isinstance(script, str):
        return [{"kind": "note", "text": _substitute(script, task)}]

    blocks: list[dict[str, Any]] = []
    for raw in script:
        if isinstance(raw, str):
            blocks.append({"kind": "note", "text": _substitute(raw, task)})
            continue
        block = dict(raw)
        kind = str(block.get("kind") or "note")
        block["kind"] = kind if kind in ("do", "say", "ask", "note") else "note"
        block["text"] = _substitute(str(block.get("text") or ""), task)
        if block.get("note"):
            block["note"] = _substitute(str(block["note"]), task)
        if block.get("questions"):
            block["questions"] = [
                {**question, "text": _substitute(str(question.get("text") or ""), task)}
                for question in block["questions"]
            ]
        blocks.append(block)
    return blocks


def required_models(study: Study) -> list[str]:
    """Every model stem the study can ask the viewer to load.

    The control panel checks these against the server's model list, so a
    missing STL is found before the participant sits down rather than when the
    step that needs it puts nothing on the display.
    """
    stems: set[str] = set()
    for step in study.steps:
        ref = step.get("model")
        if isinstance(ref, dict) and ref.get("kind") == "fixed" and ref.get("model"):
            stems.add(str(ref["model"]))
    used_roles = {
        str((step.get("model") or {}).get("version") or "a")
        for step in study.steps
        if isinstance(step.get("model"), dict) and step["model"].get("kind") == "task"
    }
    used_tasks = {key for row in study.design for key in study.session_tasks(row)}
    for key in used_tasks:
        task = study.tasks.get(key) or {}
        for role in used_roles:
            entry = task.get(role) or {}
            if entry.get("model"):
                stems.add(str(entry["model"]))
    return sorted(stems)


def protocol_hash(study: Study) -> str:
    """A fingerprint of everything that decides what a session does.

    ``version`` is bumped by hand, and hands forget. This is recorded beside it
    on every session, so two sessions that ran different protocols under the
    same version number can still be told apart in the data.
    """
    content = {
        "steps": study.steps,
        "tasks": study.tasks,
        "design": [list(row) for row in study.design],
        "tasks_per_session": study.tasks_per_session,
        "viewer_defaults": study.viewer_defaults,
        "model_labels": study.model_labels,
        # What the experimenter is given to say is part of what a session does,
        # so editing it changes the fingerprint too (#258 review). The title and
        # summary describe the study, and do not.
        "facilitator_prompts": study.facilitator_prompts,
        "strategy_prompts": study.strategy_prompts,
    }
    encoded = json.dumps(content, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def config_payload(study: Study) -> dict[str, Any]:
    """Everything the control panel needs before a session exists."""
    return {
        "slug": study.slug,
        "title": study.title,
        "summary": study.summary,
        "version": study.version,
        "protocol_hash": protocol_hash(study),
        "tasks_per_session": study.tasks_per_session,
        "tasks": study.tasks,
        "facilitator_prompts": study.facilitator_prompts,
        "strategy_prompts": study.strategy_prompts,
        "viewer_defaults": study.viewer_defaults,
        "design": design_preview(study),
    }
