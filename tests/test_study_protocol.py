"""The study protocol as data: counterbalancing, step resolution, and the
invariants a session depends on.

These are cheap tests guarding expensive mistakes. A protocol that assigns the
same model pair twice, or resolves task 2 to task 1's model, produces a session
that cannot be re-run and data that cannot be used.
"""

from __future__ import annotations

import json

import pytest

from app import study_protocol


def _script_text(step: dict) -> str:
    """Every word a step puts in front of the experimenter, flattened."""
    parts: list[str] = []
    for block in step.get("script") or []:
        parts.append(block.get("text", ""))
        parts.append(block.get("note", "") or "")
        for question in block.get("questions", []) or []:
            parts.append(question.get("text", ""))
            parts.append(question.get("note", "") or "")
            parts.extend(question.get("options", []) or [])
    return "\n".join(parts)


def _spoken_text(step: dict) -> str:
    """Only the blocks the experimenter reads to the participant."""
    return "\n".join(
        block.get("text", "")
        for block in step.get("script") or []
        if block.get("kind") == "say"
    )


class TestCounterbalancing:
    def test_each_participant_gets_two_distinct_pairs(self):
        for sequence_number in range(1, 25):
            order = study_protocol.assign_task_order(sequence_number)
            assert len(order) == study_protocol.TASKS_PER_SESSION
            assert len(set(order)) == len(order), f"repeat pair at {sequence_number}"

    def test_only_main_pairs_are_assigned(self):
        """The Lego brick is the practice round and must never be a task."""
        for sequence_number in range(1, 25):
            order = study_protocol.assign_task_order(sequence_number)
            assert study_protocol.PRACTICE_PAIR not in order
            for key in order:
                assert key in study_protocol.MAIN_PAIRS

    def test_balanced_over_one_full_cycle(self):
        """Across six participants each pair appears twice in each position, and
        every ordered pair of models occurs exactly once. This is the property
        that keeps 'differences found in task 2' from being confounded with
        'task 2 was always the coat rack'."""
        orders = [study_protocol.assign_task_order(n) for n in range(1, 7)]

        firsts = [order[0] for order in orders]
        seconds = [order[1] for order in orders]
        for key in study_protocol.MAIN_PAIRS:
            assert firsts.count(key) == 2, f"{key} unbalanced in first position"
            assert seconds.count(key) == 2, f"{key} unbalanced in second position"

        assert len({tuple(order) for order in orders}) == 6

    def test_cycle_repeats_after_six(self):
        assert study_protocol.assign_task_order(7) == study_protocol.assign_task_order(1)
        assert study_protocol.assign_task_order(13) == study_protocol.assign_task_order(1)

    def test_sequence_number_is_clamped_not_crashed(self):
        """A zero or negative position must still produce a usable assignment;
        failing here would block enrollment rather than just misassign."""
        assert study_protocol.assign_task_order(0)
        assert study_protocol.assign_task_order(-5)

    def test_preview_matches_assignment(self):
        for row in study_protocol.latin_square_preview():
            assert row["task_order"] == study_protocol.assign_task_order(row["sequence_number"])
            assert len(row["labels"]) == len(row["task_order"])


class TestStepResolution:
    @pytest.fixture()
    def steps(self):
        return study_protocol.resolve_steps(["cane_tip", "coat_rack"])

    def test_step_ids_are_unique(self, steps):
        ids = [step["id"] for step in steps]
        assert len(ids) == len(set(ids))

    def test_index_matches_position(self, steps):
        for position, step in enumerate(steps):
            assert step["index"] == position

    def test_task_slots_resolve_in_assigned_order(self, steps):
        by_id = {step["id"]: step for step in steps}
        assert by_id["task1.a.virtual"]["model"]["model"] == "cane_tip_hook"
        assert by_id["task1.b.virtual"]["model"]["model"] == "cane_tip_fitted"
        assert by_id["task2.a.virtual"]["model"]["model"] == "coat_hanger_30"
        assert by_id["task2.b.virtual"]["model"]["model"] == "coat_hanger_40"

    def test_task_order_actually_changes_the_models(self):
        """Guards the bug that would silently ruin counterbalancing: a resolver
        that ignores the assignment and always returns the same pair."""
        first = study_protocol.resolve_steps(["cane_tip", "coat_rack"])
        second = study_protocol.resolve_steps(["coat_rack", "cane_tip"])
        by_id_first = {s["id"]: s for s in first}
        by_id_second = {s["id"]: s for s in second}
        assert (
            by_id_first["task1.a.virtual"]["model"]["model"]
            != by_id_second["task1.a.virtual"]["model"]["model"]
        )

    def test_onboarding_uses_the_mug(self, steps):
        onboarding = [s for s in steps if s["part_id"] == "onboarding"]
        assert onboarding
        for step in onboarding:
            assert step["model"]["model"] == "mug"

    def test_conversation_steps_load_nothing(self, steps):
        """The opening and the discussion must not blank the display: the
        participant may still have their hands on it."""
        for step in steps:
            if step["part_id"] in ("opening", "background", "discussion"):
                assert step["model"] is None

    def test_every_exploration_step_has_an_answer_key(self, steps):
        for step in steps:
            if step["id"].endswith((".a.virtual", ".b.virtual", ".a.physical", ".b.physical")):
                assert step["pair"], f"{step['id']} has no pair"
                assert step["pair"]["differences"], f"{step['id']} has no answer key"

    def test_script_placeholders_are_substituted(self, steps):
        """An unresolved placeholder would be read aloud to a participant."""
        for step in steps:
            text = _script_text(step)
            assert "{description}" not in text, step["id"]
            assert "{label}" not in text, step["id"]

    def test_part_a_script_carries_the_pair_description(self, steps):
        by_id = {step["id"]: step for step in steps}
        script = _script_text(by_id["task1.a.virtual"])
        assert study_protocol.MODEL_PAIRS["cane_tip"]["description"] in script

    def test_physical_model_reminders_resolve(self, steps):
        by_id = {step["id"]: step for step in steps}
        assert by_id["task1.a.physical"]["physical_model"] == "Printed hook cane tip"
        assert by_id["task2.b.physical"]["physical_model"] == "Printed 40 mm peg coat rack"

    def test_questions_are_carried_as_text_to_read_not_as_fields(self, steps):
        """The questions are shown to the experimenter so they have one place to
        look, but the app captures no answers -- a step carries no field
        definitions, only prose to read out."""
        for step in steps:
            assert "survey" not in step
            assert "likert" not in step
            for block in step["script"]:
                assert "input" not in block
                assert "response" not in block
                for question in block.get("questions", []):
                    assert "id" not in question
                    assert "response" not in question

    def test_every_block_declares_what_it_is(self, steps):
        """The whole point of the block format: the experimenter must never have
        to work out whether a sentence is an instruction or something to say."""
        for step in steps:
            assert step["script"], f"{step['id']} has no script"
            for block in step["script"]:
                assert block["kind"] in ("do", "say", "ask", "note"), block
                assert block["text"].strip(), f"empty block in {step['id']}"

    def test_a_plain_string_script_still_loads(self):
        """A protocol override written before the block format existed must not
        render an empty step in the middle of a session."""
        blocks = study_protocol._resolve_script("just some prose", None)
        assert blocks == [{"kind": "note", "text": "just some prose"}]

    def test_an_unknown_block_kind_degrades_to_a_note(self):
        blocks = study_protocol._resolve_script([{"kind": "shout", "text": "hi"}], None)
        assert blocks[0]["kind"] == "note"


class TestScriptContent:
    @pytest.fixture()
    def by_id(self):
        return {s["id"]: s for s in study_protocol.resolve_steps(["cane_tip", "coat_rack"])}

    def test_the_opening_does_not_take_consent(self, by_id):
        """Consent is given before the session -- the participant would not have
        the link otherwise. Asking again would be redundant and a strange open."""
        step = by_id["opening.settle"]

        notes = " ".join(b["text"] for b in step["script"] if b["kind"] == "note").lower()
        assert "consented before" in notes, "the step should record that consent is already handled"

        # Nothing the experimenter is told to *do* or *say* may be about
        # obtaining it. Checking by block kind rather than by substring, so the
        # note above explaining that consent is already given does not count.
        for block in step["script"]:
            if block["kind"] in ("do", "say", "ask"):
                assert "consent" not in block["text"].lower(), (
                    f"opening still tries to take consent: {block['text']}"
                )

        assert not any(
            "information sheet" in _script_text(s).lower()
            for s in (by_id["opening.settle"], by_id["opening.setup"])
        )

    def test_no_step_is_titled_or_named_for_consent(self, by_id):
        for step in by_id.values():
            assert "consent" not in step["id"]
            assert "consent" not in step["title"].lower()
            assert "consent" not in step["part_title"].lower()

    def test_the_opening_is_about_settling_in(self, by_id):
        spoken = _spoken_text(by_id["opening.settle"]).lower()
        assert "thanks for coming" in spoken
        assert "no right answers" in spoken

    def test_setup_is_the_second_step(self, by_id):
        assert by_id["opening.setup"]["index"] == by_id["opening.settle"]["index"] + 1
        text = _script_text(by_id["opening.setup"]).lower()
        assert "chrome" in text
        assert "press lightly" in text
        assert "not to scale" in text

    def test_no_step_calls_a_real_task_practice(self, by_id):
        """There is no rehearsal round any more; nothing may describe the
        participant's work as one."""
        for step_id, step in by_id.items():
            spoken = _spoken_text(step).lower()
            assert "practice" not in spoken, f"{step_id} calls a real task practice"

    def test_the_second_task_just_carries_on(self, by_id):
        """The intro line lives at the top of task2.a.virtual's script now
        (#180 merged the standalone "introduce the object" step into it)."""
        spoken = _spoken_text(by_id["task2.a.virtual"]).lower()
        assert "practice" not in spoken
        assert "same" in spoken and "again" in spoken

    def test_task_intros_are_not_conditional_on_the_experimenter(self, by_id):
        """The wording is fixed per slot. A branch to resolve mid-session is a
        branch to get wrong mid-session."""
        for step_id in ("task1.a.virtual", "task2.a.virtual"):
            assert "if this is" not in _script_text(by_id[step_id]).lower()

    def test_background_questions_are_present_to_read(self, by_id):
        """Asked verbally, but the experimenter should not need a second document
        open beside the panel."""
        text = _script_text(by_id["background.questionnaire"])
        for question in study_protocol.BACKGROUND_QUESTIONS:
            assert question["text"] in text

    def test_rating_items_are_present_to_read_after_each_task(self, by_id):
        for step_id in ("task1.rating", "task2.rating"):
            text = _script_text(by_id[step_id])
            for item in study_protocol.RATING_ITEMS:
                assert item["text"] in text, f"{item['text']} missing from {step_id}"

    def test_question_steps_say_the_answers_go_on_paper(self, by_id):
        """So nobody hunts the panel for a field that does not exist."""
        for step_id in ("background.questionnaire", "task1.rating"):
            text = _script_text(by_id[step_id]).lower()
            assert "your own sheet" in text
            assert "records nothing" in text

    def test_discussion_questions_are_present(self, by_id):
        text = _script_text(by_id["discussion.reflection"])
        assert "real-world contexts" in text

    def test_part_b_reminds_the_experimenter_not_to_give_it_away(self, by_id):
        for step_id in ("task1.b.virtual", "task2.b.virtual"):
            notes = " ".join(
                block["text"] for block in by_id[step_id]["script"] if block["kind"] == "note"
            ).lower()
            assert "do not say what changed" in notes

    def test_unassigned_slots_resolve_to_nothing_rather_than_guessing(self):
        """A session started with one pair must leave task 2 empty rather than
        quietly reusing task 1's model."""
        steps = study_protocol.resolve_steps(["cane_tip"])
        by_id = {step["id"]: step for step in steps}
        assert by_id["task2.a.virtual"]["model"] is None


class TestRequiredModels:
    def test_lists_every_model_the_protocol_loads(self):
        required = study_protocol.required_models()
        assert "mug" in required
        for pair in study_protocol.MODEL_PAIRS.values():
            assert pair["a"]["model"] in required
            assert pair["b"]["model"] in required

    def test_all_required_models_ship_with_the_app(self):
        """A protocol naming a model that is not in builtin_models/ means a step
        that puts nothing on the display, discovered mid-session."""
        from pathlib import Path

        builtin_dir = Path(__file__).resolve().parent.parent / "builtin_models"
        shipped = {path.stem for path in builtin_dir.iterdir() if path.is_file()}
        missing = [stem for stem in study_protocol.required_models() if stem not in shipped]
        assert not missing, f"protocol needs models that do not ship: {missing}"


class TestViewerDefaults:
    def test_matches_the_agreed_study_defaults(self):
        """Issue #163: the state every model load starts from."""
        defaults = study_protocol.VIEWER_DEFAULTS
        assert defaults["render_mode"] == "cut"
        assert defaults["representation_mode"] == "single"
        assert defaults["compose_scrollbar"] is True
        assert defaults["depth"] == 50
        assert defaults["zoom"] == 0.0
        assert defaults["reset_pan"] is True
        # 180 degrees of yaw from the viewer's own x+ default, which is what puts
        # the mug upright with the handle to the right.
        assert defaults["view"] == "x-"


class TestProtocolOverride:
    def test_uses_the_builtin_protocol_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STUDY_PROTOCOL_PATH", str(tmp_path / "absent.json"))
        study_protocol.load_protocol()
        assert study_protocol.load_protocol()["source"] == "builtin"
        assert study_protocol.override_error() is None

    def test_a_valid_override_replaces_the_steps(self, tmp_path, monkeypatch):
        override = tmp_path / "protocol.json"
        override.write_text(
            json.dumps({"version": "test-1", "steps": [{"id": "only", "part_id": "p", "title": "t"}]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("STUDY_PROTOCOL_PATH", str(override))
        protocol = study_protocol.load_protocol()
        assert protocol["version"] == "test-1"
        assert len(protocol["steps"]) == 1

    def test_a_broken_override_falls_back_instead_of_raising(self, tmp_path, monkeypatch):
        """A typo in an edited protocol must not take down a session in progress."""
        override = tmp_path / "protocol.json"
        override.write_text("{ not json", encoding="utf-8")
        monkeypatch.setenv("STUDY_PROTOCOL_PATH", str(override))
        protocol = study_protocol.load_protocol()
        assert protocol["source"] == "builtin"
        assert study_protocol.override_error() is not None

    def test_an_empty_override_falls_back(self, tmp_path, monkeypatch):
        override = tmp_path / "protocol.json"
        override.write_text(json.dumps({"steps": []}), encoding="utf-8")
        monkeypatch.setenv("STUDY_PROTOCOL_PATH", str(override))
        assert study_protocol.load_protocol()["source"] == "builtin"
        assert study_protocol.override_error() is not None
