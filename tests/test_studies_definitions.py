"""Every study definition, the helpers that build them, and the panel tokens.

A definition that fails here would fail at load on a server, where the only sign
is a line in a log nobody can read without shell access. So every definition is
checked here, including ones nobody is running yet.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import Counter
from pathlib import Path

import pytest

from app.studies import protocol, registry, tokens
from app.studies.definition import Status, validate
from app.studies.definitions import ALL, comparison_2026, example

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_MODELS = {path.stem for path in (ROOT / "builtin_models").iterdir() if path.is_file()}


# ---------------------------------------------------------------------------
# Every definition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("study", ALL, ids=lambda study: study.slug)
class TestEveryDefinition:
    def test_it_is_valid(self, study):
        assert validate(study) == []

    def test_every_model_it_loads_ships_with_the_app(self, study):
        """A study naming a model that is not in builtin_models/ has a step that
        puts nothing on the display, found mid-session."""
        missing = [stem for stem in protocol.required_models(study) if stem not in SHIPPED_MODELS]
        assert not missing, f"{study.slug} needs models that do not ship: {missing}"

    def test_every_step_says_something_to_the_participant_or_nothing_at_all(self, study):
        """participant_text is the only part of a step the participant's page
        receives. A step without it shows a blank instruction."""
        for step in study.steps:
            assert step.get("participant_text"), f"{study.slug} step {step['id']} has no participant_text"

    def test_a_served_study_has_a_token(self, study):
        """Draft, open or closed: anything that can be served needs a hash, or it
        is refused at load."""
        if study.status is not Status.RETIRED:
            assert study.token_hash and tokens.is_well_formed(study.token_hash)

    def test_the_labels_a_participant_hears_do_not_name_the_model(self, study):
        for role, label in study.model_labels.items():
            for task in study.tasks.values():
                stem = (task.get(role) or {}).get("model")
                if stem:
                    assert stem.lower() not in label.lower()
                    assert str(task.get("label") or "").lower() not in label.lower()


def test_slugs_are_unique():
    slugs = [study.slug for study in ALL]
    assert len(slugs) == len(set(slugs))


def test_nothing_is_open_by_accident():
    """Opening a study is a reviewed change. This lists what is open so that
    change shows up here too."""
    assert {study.slug for study in ALL if study.status is Status.OPEN} == set()


# ---------------------------------------------------------------------------
# The retired comparison study
# ---------------------------------------------------------------------------


class TestTheComparisonStudyIsRetired:
    def test_its_status(self):
        assert comparison_2026.STUDY.status is Status.RETIRED

    def test_it_names_the_tag_of_the_code_that_ran_it(self):
        assert comparison_2026.STUDY.instrument_tag == "study-instrument-2026"

    def test_its_data_stays_where_it_was_written(self, monkeypatch):
        monkeypatch.delenv("STUDY_DB_PATH", raising=False)
        monkeypatch.delenv("STUDY_LOG_DIR", raising=False)
        storage = comparison_2026.STUDY.resolve_storage()
        assert storage.db_path == ROOT / "data" / "db" / "study.db"
        assert storage.log_dir == ROOT / "data" / "logs" / "study"

    def test_it_has_no_token_so_it_could_not_be_served_by_mistake(self):
        """Retired is what keeps it unserved; having no token means a status
        change alone would not open it either."""
        assert comparison_2026.STUDY.token_hash is None
        assert registry.refusal(dataclasses.replace(comparison_2026.STUDY, status=Status.OPEN))


# ---------------------------------------------------------------------------
# The example study
# ---------------------------------------------------------------------------


class TestTheExample:
    def test_it_is_a_draft(self):
        assert example.STUDY.status is Status.DRAFT

    def test_its_published_token_is_its_token(self):
        assert tokens.verify(example.TOKEN, example.STUDY.token_hash)

    def test_it_is_not_a_comparison(self):
        """Its tasks are single objects, to show the engine is not tied to the
        comparison study's pairs."""
        for task in example.STUDY.tasks.values():
            assert set(task) - {"label", "description"} == {"a"}

    def test_every_participant_gets_every_task(self):
        sets = protocol.task_sets(example.STUDY)
        assert all(sorted(entry["task_order"]) == sorted(example.STUDY.tasks) for entry in sets)

    def test_its_steps_resolve_to_each_task_once(self):
        order = protocol.assign_task_order(example.STUDY, 1)
        steps = protocol.resolve_steps(example.STUDY, order)
        loaded = [step["model"]["model"] for step in steps if step["model"] and step["model"]["task_key"]]
        assert loaded == [example.STUDY.tasks[key]["a"]["model"] for key in order]

    def test_a_questionnaire_step_is_tied_to_its_task(self):
        steps = protocol.resolve_steps(example.STUDY, ["washer", "cube", "chair"])
        questions = {step["id"]: step for step in steps}["task2.questions"]
        assert questions["model"] is None, "a questionnaire step must leave the display alone"
        assert questions["task"]["key"] == "cube"


# ---------------------------------------------------------------------------
# Validation catches what it should
# ---------------------------------------------------------------------------


def _broken(**changes):
    return dataclasses.replace(example.STUDY, **changes)


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"slug": "Has Spaces"}, "slug"),
        ({"slug": "trailing-"}, "slug"),
        ({"slug": "x" * 41}, "slug"),
        ({"version": ""}, "version"),
        ({"steps": []}, "no steps"),
        ({"steps": [*example.STUDY.steps, example.STUDY.steps[0]]}, "used twice"),
        ({"steps": [{"title": "no id"}]}, "has no id"),
        ({"design": (("chair", "nope"),)}, "unknown tasks"),
        ({"design": (("chair", "chair"),)}, "repeats a task"),
        ({"design": ()}, "no rows"),
        ({"tasks_per_session": 1}, "which no row fills"),
    ],
)
def test_validation_says_what_is_wrong(changes, expected):
    problems = validate(_broken(**changes))
    assert any(expected in problem for problem in problems), problems


# ---------------------------------------------------------------------------
# Counterbalancing helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_a_latin_square_puts_every_task_once_in_every_position(n):
    keys = [f"t{i}" for i in range(n)]
    rows = protocol.latin_square(keys)
    assert len(rows) == n
    for row in rows:
        assert sorted(row) == sorted(keys)
    for position in range(n):
        assert sorted(row[position] for row in rows) == sorted(keys)


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_a_balanced_latin_square_balances_what_follows_what(n):
    """The Williams design: besides every task in every position equally often,
    every task immediately follows every other equally often."""
    keys = [f"t{i}" for i in range(n)]
    rows = protocol.balanced_latin_square(keys)
    assert len(rows) == (n if n % 2 == 0 else 2 * n)
    for row in rows:
        assert sorted(row) == sorted(keys)
    for position in range(n):
        counts = Counter(row[position] for row in rows)
        assert len(set(counts.values())) == 1 and set(counts) == set(keys)
    neighbours = Counter(pair for row in rows for pair in itertools.pairwise(row))
    assert set(neighbours) == set(itertools.permutations(keys, 2))
    assert len(set(neighbours.values())) == 1


def test_the_rotation_wraps_and_clamps():
    rows = len(example.STUDY.design)
    assert protocol.assign_task_order(example.STUDY, 1) == protocol.assign_task_order(example.STUDY, rows + 1)
    assert protocol.assign_task_order(example.STUDY, 0) == protocol.assign_task_order(example.STUDY, 1)
    assert protocol.assign_task_order(example.STUDY, -4) == protocol.assign_task_order(example.STUDY, 1)


def test_the_protocol_fingerprint_follows_what_a_session_runs():
    """Bumping ``version`` is a person remembering to; the fingerprint is not."""
    base = protocol.protocol_hash(example.STUDY)
    assert protocol.protocol_hash(example.STUDY) == base
    reworded = [dict(step) for step in example.STUDY.steps]
    reworded[0]["participant_text"] = "Something else."
    assert protocol.protocol_hash(_broken(steps=reworded)) != base
    assert protocol.protocol_hash(_broken(title="Renamed", summary="Different")) == base, (
        "the title and summary do not change what a session does"
    )


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class TestTokens:
    def test_a_hash_verifies_its_own_token_and_no_other(self):
        token = tokens.generate()
        stored = tokens.hash_token(token)
        assert tokens.verify(token, stored)
        assert not tokens.verify(token + "x", stored)
        assert not tokens.verify("", stored)
        assert not tokens.verify(token, "")

    def test_the_hash_is_salted(self):
        assert tokens.hash_token("same") != tokens.hash_token("same")

    def test_the_hash_does_not_contain_the_token(self):
        token = tokens.generate()
        assert token not in tokens.hash_token(token)

    @pytest.mark.parametrize(
        "stored",
        ["", "plain", "scrypt$1$8$1$c2FsdA$a2V5", "scrypt$x$8$1$c2FsdA$a2V5", "sha256$abc"],
    )
    def test_a_malformed_hash_refuses_rather_than_raising(self, stored):
        assert not tokens.is_well_formed(stored)
        assert tokens.verify("anything", stored) is False

    def test_remembering_a_good_token_does_not_let_a_wrong_one_through(self):
        token = tokens.generate()
        stored = tokens.hash_token(token)
        assert tokens.verify(token, stored)
        assert not tokens.verify("wrong", stored)

    def test_generated_tokens_are_long_and_distinct(self):
        made = {tokens.generate() for _ in range(20)}
        assert len(made) == 20
        assert all(len(token) >= 40 for token in made)
