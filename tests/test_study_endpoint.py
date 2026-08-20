"""Integration tests for the /study endpoints.

Runs against a real SQLite database in a temp directory; no mocks. The emphasis
is on the two things a session cannot recover from: data that was not recorded,
and the answer key reaching the participant's browser.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

import pytest

import app.study as study_module
import app.study_db as study_db
import app.study_protocol as study_protocol
from app.server import app as flask_app


TOKEN = "test-token"


@pytest.fixture()
def study_env(tmp_path, monkeypatch):
    """Point the study database and log directory at a temp dir, and pin the
    control token so the tests are not at the mercy of a generated one."""
    monkeypatch.setattr(study_db, "DB_PATH", tmp_path / "study.db")
    monkeypatch.setattr(study_db, "LOG_DIR", tmp_path / "logs")
    # Most of these run with the optional gate turned on, so the token paths stay
    # covered. The default -- no gate at all -- is covered by TestPanelIsOpen.
    monkeypatch.setenv("STUDY_CONTROL_TOKEN", TOKEN)
    study_db._local.__dict__.clear()
    study_db._failures.update(
        {"db_writes": 0, "db_reads": 0, "jsonl_writes": 0, "last_error": None}
    )
    study_module._ready_signals.clear()
    # The idle sweep throttles itself with a module-level timestamp, so it has
    # to be reset or one test's sweep suppresses the next test's.
    study_module._last_sweep_at = 0.0
    study_db.init_db()
    yield tmp_path
    study_db._local.__dict__.clear()


@pytest.fixture()
def client(study_env):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _auth(**extra):
    return {"X-Study-Token": TOKEN, **extra}


def _start(client, **payload):
    return client.post("/study/session/start", json=payload, headers=_auth())


def _key(state_or_id):
    """The join code for a session, as a participant's browser would carry it."""
    if isinstance(state_or_id, dict):
        return state_or_id["participant_key"]
    return study_db.get_study_session(int(state_or_id))["participant_key"]


def _advance_to(client, step_id):
    """Advance until the named step is current. Used instead of hard-coding step
    numbers so a protocol edit does not silently retarget these tests."""
    for _ in range(200):
        state = client.get("/study/state", headers=_auth()).get_json()
        if state["step"]["id"] == step_id:
            return state
        if state["step_index"] >= state["step_count"] - 1:
            break
        client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
    raise AssertionError(f"never reached step {step_id}")


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestAccessControl:
    def test_participant_view_needs_no_token(self, client):
        assert client.get("/study").status_code == 200

    def test_control_panel_requires_a_token(self, client):
        assert client.get("/study/control").status_code == 403
        assert client.get("/study/control?token=wrong").status_code == 403
        assert client.get(f"/study/control?token={TOKEN}").status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "/study/config",
            "/study/sessions",
            "/study/sets",
        ],
    )
    def test_experimenter_endpoints_require_a_token(self, client, path):
        assert client.get(path).status_code == 403

    @pytest.mark.parametrize(
        "path",
        [
            "/study/session/start",
            "/study/session/end",
            "/study/step/advance",
        ],
    )
    def test_mutating_endpoints_require_a_token(self, client, path):
        assert client.post(path, json={}).status_code == 403

    def test_token_accepted_in_header_or_query(self, client):
        assert client.get("/study/config", headers=_auth()).status_code == 200
        assert client.get(f"/study/config?token={TOKEN}").status_code == 200

    def test_rejection_names_the_variable_that_turns_the_gate_on(self, client):
        body = client.get("/study/config").get_json()
        assert "STUDY_CONTROL_TOKEN" in body["message"]


class TestPanelIsOpenByDefault:
    """Running a session should be: open the app, start.

    The panel used to be gated on a generated secret that an experimenter had to
    find in the server log. Now it is open unless the deployment sets
    STUDY_CONTROL_TOKEN, which turns the gate back on without changing anything
    else.
    """

    @pytest.fixture()
    def open_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(study_db, "DB_PATH", tmp_path / "study.db")
        monkeypatch.setattr(study_db, "LOG_DIR", tmp_path / "logs")
        monkeypatch.delenv("STUDY_CONTROL_TOKEN", raising=False)
        study_db._local.__dict__.clear()
        study_db.init_db()
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            yield c
        study_db._local.__dict__.clear()

    def test_the_panel_opens_with_no_token(self, open_client):
        assert open_client.get("/study/control").status_code == 200

    def test_a_session_can_be_run_with_no_token(self, open_client):
        started = open_client.post("/study/session/start", json={})
        assert started.status_code == 200
        session_id = started.get_json()["state"]["study_session_id"]
        assert open_client.post(
            "/study/step/advance", json={"direction": "next", "study_session_id": session_id}
        ).status_code == 200
        assert open_client.post(
            "/study/session/end", json={"study_session_id": session_id}
        ).status_code == 200

    def test_there_is_no_generated_secret_to_find(self, open_client, tmp_path):
        """No token file, no startup banner, nothing to go looking for."""
        assert study_module.control_token() == ""
        assert not list(tmp_path.glob("**/*token*"))

    def test_setting_the_variable_turns_the_gate_back_on(self, open_client, monkeypatch):
        monkeypatch.setenv("STUDY_CONTROL_TOKEN", "gated")
        assert open_client.get("/study/control").status_code == 403
        assert open_client.get("/study/control?token=gated").status_code == 200


# ---------------------------------------------------------------------------
# What the participant is allowed to see
# ---------------------------------------------------------------------------

class TestParticipantPayloadWithholdsTheAnswer:
    def test_the_participant_is_given_a_neutral_label_to_display(self, client):
        """The stem has to be sent -- a model is addressed by name now (#123) --
        so what protects the participant is that the label is the only one of the
        two the interface ever shows or announces."""
        state = _start(client, participant_code="P01").get_json()["state"]
        _advance_to(client, "task1.a.virtual")
        payload = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["model"]["label"] == "First object"
        assert payload["model"]["stem"] == "pencil_holder_2x2"

    def test_nothing_describing_the_model_reaches_the_participant(self, client):
        """Everything that would *tell* the participant what the object is or how
        it changed: the human label, the description, the answer key."""
        _start(client, participant_code="P01")
        _advance_to(client, "task1.b.virtual")
        serialised = json.dumps(client.get("/study/state").get_json())

        for leak in (
            "pencil holder",  # the pair's human label and description
            "compartments",
            "2x3 pencil holder",  # the model's own label
            "Aspect ratio",  # the answer key
        ):
            assert leak.lower() not in serialised.lower(), f"participant payload leaks {leak!r}"

    def test_part_b_label_does_not_say_what_changed(self, client):
        state = _start(client, participant_code="P01").get_json()["state"]
        _advance_to(client, "task1.b.virtual")
        payload = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["model"]["label"] == "Second object"

    def test_no_answer_key_or_script_reaches_the_participant(self, client):
        _start(client, participant_code="P01")
        _advance_to(client, "task1.b.virtual")
        payload = client.get("/study/state").get_json()
        assert "script" not in payload
        assert "pair" not in payload
        assert "differences" not in json.dumps(payload)

    def test_experimenter_does_get_the_answer_key(self, client):
        """The withholding must be about the audience, not about the data being
        missing -- otherwise the panel is useless."""
        _start(client, participant_code="P01", task_order=["cane_tip", "lego"])
        state = _advance_to(client, "task1.b.virtual")
        assert state["step"]["pair"]["differences"]
        assert state["step"]["model"]["model"] == "cane_tip_fitted"
        assert state["step"]["script"]


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class TestEnrollment:
    def test_start_mints_a_participant_id_when_none_is_given(self, client):
        body = _start(client).get_json()
        assert body["state"]["participant_code"] == "P01"

    def test_each_run_gets_a_new_participant_id(self, client):
        first = _start(client).get_json()["state"]["participant_code"]
        client.post("/study/session/end", json={}, headers=_auth())
        second = _start(client).get_json()["state"]["participant_code"]
        assert first == "P01"
        assert second == "P02"

    def test_task_order_defaults_to_the_latin_square(self, client):
        first = _start(client).get_json()["state"]["task_order"]
        client.post("/study/session/end", json={}, headers=_auth())
        second = _start(client).get_json()["state"]["task_order"]
        assert first == ["pencil_holder", "cane_tip"]
        assert second == ["cane_tip", "lego"]

    def test_a_returning_participant_keeps_their_assignment(self, client):
        first = _start(client, participant_code="P01").get_json()["state"]["task_order"]
        client.post("/study/session/end", json={}, headers=_auth())
        _start(client, participant_code="P09")
        client.post("/study/session/end", json={}, headers=_auth())
        again = _start(
            client, participant_code="P01", session_number=2
        ).get_json()["state"]["task_order"]
        assert again == first

    def test_explicit_task_order_is_honoured(self, client):
        body = _start(client, task_order=["lego", "pencil_holder"]).get_json()
        assert body["state"]["task_order"] == ["lego", "pencil_holder"]

    def test_unknown_pairs_are_rejected_rather_than_silently_dropped(self, client):
        response = _start(client, task_order=["not_a_model"])
        assert response.status_code == 400

    def test_starting_a_session_leaves_a_running_one_alone(self, client):
        """This used to abandon the previous session. Two experimenters share one
        deployment, so that silently ended someone else's participant mid-task."""
        first_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        second_id = _start(client, participant_code="P02").get_json()["state"]["study_session_id"]

        assert study_db.get_study_session(first_id)["status"] == "active"
        assert study_db.get_study_session(second_id)["status"] == "active"
        assert {s["participant_code"] for s in study_db.list_active_sessions()} == {"P01", "P02"}

    def test_a_panel_is_told_which_other_sessions_are_running(self, client):
        """So a second experimenter knows someone else is mid-session on this
        deployment, without it reading as an error -- they run side by side."""
        _start(client, participant_code="P01")
        second = _start(client, participant_code="P02").get_json()["state"]["study_session_id"]

        state = client.get(
            f"/study/state?study_session_id={second}", headers=_auth()
        ).get_json()
        listed = {o["participant_code"]: o["is_current"] for o in state["active_sessions"]}
        assert listed == {"P01": False, "P02": True}

    def test_duplicate_participant_and_session_number_is_refused(self, client):
        _start(client, participant_code="P01", session_number=1)
        response = _start(client, participant_code="P01", session_number=1)
        assert response.status_code == 409

    @pytest.mark.parametrize("code", ["../etc", "a/b", "x" * 40, "P01.json"])
    def test_dangerous_participant_codes_are_refused(self, client, code):
        """The code becomes part of the log filename."""
        assert _start(client, participant_code=code).status_code == 400

    def test_session_number_must_be_positive(self, client):
        assert _start(client, session_number=0).status_code == 400


# ---------------------------------------------------------------------------
# Stepping through the protocol
# ---------------------------------------------------------------------------

class TestSteps:
    def test_session_starts_at_the_first_step(self, client):
        state = _start(client).get_json()["state"]
        assert state["step_index"] == 0
        assert state["step"]["part_id"] == "opening"

    def test_advance_moves_forward_and_back(self, client):
        _start(client)
        client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
        state = client.get("/study/state", headers=_auth()).get_json()
        assert state["step_index"] == 1
        client.post("/study/step/advance", json={"direction": "previous"}, headers=_auth())
        assert client.get("/study/state", headers=_auth()).get_json()["step_index"] == 0

    def test_cannot_step_before_the_first_or_past_the_last(self, client):
        _start(client)
        client.post("/study/step/advance", json={"direction": "previous"}, headers=_auth())
        assert client.get("/study/state", headers=_auth()).get_json()["step_index"] == 0

        state = client.get("/study/state", headers=_auth()).get_json()
        client.post(
            "/study/step/advance", json={"step_index": state["step_count"] + 50}, headers=_auth()
        )
        final = client.get("/study/state", headers=_auth()).get_json()
        assert final["step_index"] == final["step_count"] - 1

    def test_jump_to_an_absolute_step(self, client):
        _start(client)
        client.post("/study/step/advance", json={"step_index": 5}, headers=_auth())
        assert client.get("/study/state", headers=_auth()).get_json()["step_index"] == 5

    def test_advance_without_a_session_is_a_clean_404(self, client):
        assert client.post("/study/step/advance", json={}, headers=_auth()).status_code == 404


# ---------------------------------------------------------------------------
# The ready button
# ---------------------------------------------------------------------------

class TestReadySignal:
    def test_ready_is_logged_and_shown_to_the_experimenter(self, client):
        state = _start(client).get_json()["state"]
        assert client.post(
            f"/study/step/ready?s={state['participant_key']}", json={"client_id": "c1"}
        ).status_code == 200
        state = client.get("/study/state", headers=_auth()).get_json()
        assert state["participant_ready"]["step_id"] == state["step"]["id"]

    def test_ready_does_not_advance_the_session(self, client):
        """Advisory by design: a stray press must not skip a step and lose the
        exploration that was still in progress."""
        _start(client)
        before = client.get("/study/state", headers=_auth()).get_json()["step_index"]
        client.post("/study/step/ready", json={})
        after = client.get("/study/state", headers=_auth()).get_json()["step_index"]
        assert before == after

    def test_the_signal_belongs_to_the_step_it_was_raised_on(self, client):
        _start(client)
        client.post("/study/step/ready", json={})
        client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
        state = client.get("/study/state", headers=_auth()).get_json()
        assert state["participant_ready"] is None

    def test_ready_without_a_session_is_a_clean_404(self, client):
        assert client.post("/study/step/ready", json={}).status_code == 404


# ---------------------------------------------------------------------------
# Interaction logging
# ---------------------------------------------------------------------------

class TestEventLogging:
    def test_participant_events_are_recorded_against_the_participant(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        client.post(
            f"/study/event?s={_key(session_id)}",
            json={
                "event_type": "keyboard",
                "event_data": {"key": "arrowup"},
                "viewer_state": {"depth": 51, "view": "x-"},
                "client_id": "c1",
            },
        )
        export = study_db.export_session(session_id)
        keyboard = [e for e in export["events"] if e["event_type"] == "keyboard"]
        assert len(keyboard) == 1
        assert keyboard[0]["participant_code"] == "P01"
        assert keyboard[0]["event_data"]["key"] == "arrowup"

    def test_unknown_event_types_are_rejected(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        response = client.post(
            f"/study/event?s={_key(session_id)}", json={"event_type": "exfiltrate"}
        )
        assert response.status_code == 400

    def test_events_without_a_session_are_ignored_not_errors(self, client):
        """A participant page left open after a session ends must not spam errors
        into the browser console during the next session's setup."""
        response = client.post("/study/event?s=ZZZZ", json={"event_type": "keyboard"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "ignored"

    def test_step_context_is_attached_to_every_event(self, client):
        session_id = _start(
            client, task_order=["cane_tip", "lego"]
        ).get_json()["state"]["study_session_id"]
        state = _advance_to(client, "task1.a.virtual")
        client.post(
            f"/study/event?s={_key(session_id)}",
            json={"event_type": "keyboard", "event_data": {"key": "r"}},
        )
        export = study_db.export_session(session_id)
        keyboard = [e for e in export["events"] if e["event_type"] == "keyboard"][0]
        assert keyboard["step_id"] == state["step"]["id"]
        assert keyboard["part_id"] == "task1"

    def test_timings_are_recorded(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        event = study_db.export_session(session_id)["events"][-1]
        assert event["elapsed_ms"] is not None and event["elapsed_ms"] >= 0
        assert event["step_elapsed_ms"] is not None and event["step_elapsed_ms"] >= 0

    def test_the_step_clock_restarts_on_advance(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        client.post("/study/step/advance", json={"step_index": 3}, headers=_auth())
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        event = study_db.export_session(session_id)["events"][-1]
        # Session elapsed keeps counting from the start; the step clock does not.
        assert event["step_elapsed_ms"] <= event["elapsed_ms"]

    def test_ordering_is_shared_between_events_and_renders(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        study_db.record_render(
            session_id, model="mug", view="x-", render_mode="Cut", layout_mode="single",
            depth=50, zoom=0, input_source="keyboard", cache_hit=False,
        )
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        export = study_db.export_session(session_id)
        sequences = [e["seq"] for e in export["events"]] + [r["seq"] for r in export["renders"]]
        assert len(sequences) == len(set(sequences)), "seq must be unique across both tables"

    def test_session_lifecycle_is_logged(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
        client.post("/study/session/end", json={}, headers=_auth())
        types = [e["event_type"] for e in study_db.export_session(session_id)["events"]]
        assert "session_start" in types
        assert "step_advance" in types
        assert "session_end" in types

    def test_model_autoload_is_logged_with_the_real_model_name(self, client):
        session_id = _start(
            client, participant_code="P01", task_order=["cane_tip", "lego"]
        ).get_json()["state"]["study_session_id"]
        _advance_to(client, "task1.b.virtual")
        events = study_db.export_session(session_id)["events"]
        autoloads = [e for e in events if e["event_type"] == "model_autoload"]
        assert any(e["event_data"]["model"] == "cane_tip_fitted" for e in autoloads)


class TestJsonlLog:
    def test_every_event_is_mirrored_to_the_jsonl_log(self, client, study_env):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        client.post(
            f"/study/event?s={_key(session_id)}",
            json={"event_type": "keyboard", "event_data": {"key": "r"}},
        )

        log_path = study_db.get_study_session(session_id)["log_path"]
        lines = [json.loads(line) for line in open(log_path, encoding="utf-8")]
        assert lines
        assert lines[0]["event_type"] == "session_start"
        assert lines[-1]["event_type"] == "keyboard"

    def test_every_line_is_self_describing(self, client):
        """The JSONL is the reconstruction record: a line has to be readable
        without joining it against anything else."""
        session_id = _start(
            client, participant_code="P01", task_order=["cane_tip", "lego"]
        ).get_json()["state"]["study_session_id"]
        _advance_to(client, "task1.a.virtual")
        client.post(
            f"/study/event?s={_key(session_id)}",
            json={"event_type": "keyboard", "viewer_state": {"depth": 60, "view": "x-"}},
        )
        log_path = study_db.get_study_session(session_id)["log_path"]
        line = [json.loads(line) for line in open(log_path, encoding="utf-8")][-1]
        for field in (
            "timestamp", "elapsed_ms", "step_elapsed_ms", "participant_code",
            "task_order", "part_id", "step_id", "event_type", "viewer_state",
        ):
            assert field in line, f"{field} missing from the reconstruction log"
        assert line["viewer_state"]["depth"] == 60

    def test_log_filename_carries_participant_and_session(self, client):
        session_id = _start(
            client, participant_code="P07", session_number=2
        ).get_json()["state"]["study_session_id"]
        log_path = study_db.get_study_session(session_id)["log_path"]
        assert "P07_S2_" in log_path


class TestRenderLogging:
    def test_renders_are_attributed_via_the_session_header(self, client, monkeypatch):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        study_module.record_render_for_request  # attribution happens inside /render

        with flask_app.test_request_context(
            "/render", headers={"X-Study-Session": str(session_id)}
        ):
            study_module.record_render_for_request(
                {
                    "view": "x-", "renderMode": "Cut", "mode": "single",
                    "depth": 50, "zoom": 0.0, "input_source": "keyboard",
                },
                model_stem="mug",
                cache_hit=False,
            )
        renders = study_db.export_session(session_id)["renders"]
        assert len(renders) == 1
        assert renders[0]["render_mode"] == "Cut"
        assert renders[0]["cache_hit"] == 0

    def test_cached_renders_are_recorded_too(self, client):
        """A cache hit still put a new image under the participant's fingers.
        Dropping them would lose most of a fast arrow-key traversal."""
        session_id = _start(client).get_json()["state"]["study_session_id"]
        with flask_app.test_request_context(
            "/render", headers={"X-Study-Session": str(session_id)}
        ):
            study_module.record_render_for_request({"depth": 50}, model_stem="mug", cache_hit=True)
        renders = study_db.export_session(session_id)["renders"]
        assert renders[0]["cache_hit"] == 1

    def test_renders_without_the_header_are_not_attributed(self, client):
        """Someone else using /viewer during a session must not land in the
        study record."""
        session_id = _start(client).get_json()["state"]["study_session_id"]
        with flask_app.test_request_context("/render"):
            study_module.record_render_for_request({"depth": 50}, model_stem="mug", cache_hit=False)
        assert study_db.export_session(session_id)["renders"] == []

    def test_a_fault_in_render_logging_never_breaks_the_render(self, client, monkeypatch):
        """This hook runs inside /render's try block, where a raise becomes a 400.
        A logging fault must not stop the participant's display updating."""
        session_id = _start(client).get_json()["state"]["study_session_id"]

        def broken(*_args, **_kwargs):
            raise RuntimeError("protocol resolution blew up")

        monkeypatch.setattr(study_module, "_current_step", broken)
        with flask_app.test_request_context(
            "/render", headers={"X-Study-Session": str(session_id)}
        ):
            study_module.record_render_for_request({"depth": 50}, model_stem="mug", cache_hit=False)

        assert study_db.logging_health()["db_write_failures"] >= 1

    def test_renders_for_an_ended_session_are_not_recorded(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        client.post("/study/session/end", json={}, headers=_auth())
        with flask_app.test_request_context(
            "/render", headers={"X-Study-Session": str(session_id)}
        ):
            study_module.record_render_for_request({"depth": 50}, model_stem="mug", cache_hit=False)
        assert study_db.export_session(session_id)["renders"] == []


# ---------------------------------------------------------------------------
# Scope of what is stored
# ---------------------------------------------------------------------------

class TestStorageScope:
    def test_the_study_database_holds_only_interactions_and_timings(self, study_env):
        import sqlite3

        conn = sqlite3.connect(str(study_db.DB_PATH))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert tables >= {"participants", "study_sessions", "study_events", "study_renders"}
        # Nothing that would hold what a participant said or what the
        # experimenter thought: those are recorded verbally, off this system.
        for absent in ("study_observations", "study_likert", "study_survey", "study_notes"):
            assert absent not in tables

    def test_no_endpoint_accepts_questionnaire_or_note_content(self, client):
        _start(client)
        for path in ("/study/observation", "/study/likert", "/study/survey", "/study/note"):
            response = client.post(path, json={"text": "x"}, headers=_auth())
            assert response.status_code == 404, f"{path} should not exist"

    def test_the_study_database_is_a_separate_file(self):
        import app.db as analytics_db

        assert study_db.DB_PATH != analytics_db.DB_PATH


# ---------------------------------------------------------------------------
# Health reporting and export
# ---------------------------------------------------------------------------

class TestLoggingHealth:
    def test_healthy_by_default(self, client):
        state = _start(client).get_json()["state"]
        assert state["logging"]["db_write_failures"] == 0
        assert state["logging"]["jsonl_write_failures"] == 0

    def test_a_failed_write_is_counted_and_surfaced_not_raised(self, client, monkeypatch):
        """The session must survive a logging failure, and the experimenter must
        be told during it rather than after."""
        import sqlite3

        session_id = _start(client).get_json()["state"]["study_session_id"]

        def broken(_conn, _session_id):
            raise sqlite3.OperationalError("database or disk is full")

        monkeypatch.setattr(study_db, "_next_seq", broken)
        response = client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})

        assert response.status_code == 200
        assert study_db.logging_health()["db_write_failures"] >= 1
        # The JSONL is written independently, so the interaction is still on
        # disk even though the database write failed. That is the whole point of
        # having two records.
        log_path = study_db.get_study_session(session_id)["log_path"]
        lines = [json.loads(line) for line in open(log_path, encoding="utf-8")]
        assert lines[-1]["event_type"] == "keyboard"

    def test_a_database_read_failure_does_not_500_the_participant(self, client, monkeypatch):
        """Every request starts with a session lookup. A storage problem must
        degrade the log, not put an error page in front of a participant who is
        mid-exploration."""
        import sqlite3

        state = _start(client).get_json()["state"]

        def broken():
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr(study_db, "_get_conn", broken)
        response = client.post(
            f"/study/event?s={state['participant_key']}", json={"event_type": "keyboard"}
        )

        assert response.status_code == 200
        assert study_db.logging_health()["db_read_failures"] >= 1


class TestDataIsRecoverableFromTheFilesAlone:
    """What an analyst gets by copying files off the server, rather than by
    reading through the running app."""

    def test_the_database_file_is_self_contained_after_a_session_ends(self, client, study_env):
        """WAL mode keeps recent writes in study.db-wal until SQLite checkpoints.
        Copying study.db on its own -- the obvious way to get a session off the
        server -- then yields a database missing everything still in the WAL. A
        real session lost about a third of its rows that way."""
        import shutil
        import sqlite3

        state = _start(client, participant_code="P01").get_json()["state"]
        for _ in range(12):
            client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
            client.post(f"/study/event?s={state['participant_key']}",
                        json={"event_type": "keyboard", "event_data": {"key": "arrowup"}})
        client.post("/study/session/end", json={}, headers=_auth())

        live = study_db.export_session(1)
        expected = len(live["events"]) + len(live["renders"])

        # Copy ONLY the main database file, as someone would with docker cp.
        alone = study_env / "copied-alone.db"
        shutil.copy(study_db.DB_PATH, alone)
        conn = sqlite3.connect(str(alone))
        got = (
            conn.execute("SELECT COUNT(*) FROM study_events").fetchone()[0]
            + conn.execute("SELECT COUNT(*) FROM study_renders").fetchone()[0]
        )
        status = conn.execute("SELECT status FROM study_sessions").fetchone()[0]
        conn.close()

        assert got == expected, f"copying study.db alone lost {expected - got} of {expected} rows"
        assert status == "completed", "the copied database does not even show the session as ended"

    def test_the_jsonl_is_written_in_seq_order(self, client, monkeypatch):
        """The file's whole point is being an ordered append-only record. Two
        clients posting at the same moment must not be able to append in the
        opposite order to the sequence numbers they were given.

        The append is slowed down deliberately. Without it the race is real but
        rare, and a test that only sometimes reproduces the bug is a test that
        reports the bug fixed when it is not -- this one passed against the
        broken code before the delay was added.
        """
        import threading
        import time

        session_id = _start(client).get_json()["state"]["study_session_id"]

        real_append = study_db._append_jsonl

        def slow_append(session, record):
            # Long enough that another thread will take the next seq and reach
            # its own append first, if appends are not serialised with the
            # sequence numbers.
            time.sleep(0.005)
            real_append(session, record)

        monkeypatch.setattr(study_db, "_append_jsonl", slow_append)

        def spam(n):
            for i in range(n):
                study_db.record_event(session_id, "keyboard", event_data={"i": i})

        threads = [threading.Thread(target=spam, args=(8,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        log_path = study_db.get_study_session(session_id)["log_path"]
        sequences = [json.loads(line)["seq"] for line in open(log_path, encoding="utf-8")]
        assert sequences == sorted(sequences), (
            f"JSONL lines are out of sequence order: {sequences}"
        )
        assert len(sequences) == len(set(sequences)), "a line was written twice"

    def test_the_jsonl_and_the_database_agree_on_row_count(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        for _ in range(5):
            client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
            client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        export = study_db.export_session(session_id)
        log_path = study_db.get_study_session(session_id)["log_path"]
        lines = sum(1 for _ in open(log_path, encoding="utf-8"))
        assert lines == len(export["events"]) + len(export["renders"])


class TestExport:
    def test_export_returns_the_whole_session(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        body = client.get(f"/study/sessions/{session_id}/export", headers=_auth()).get_json()
        assert body["session"]["participant_code"] == "P01"
        assert body["participant"]["id"] == 1
        assert any(e["event_type"] == "keyboard" for e in body["events"])

    def test_export_requires_a_token(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        assert client.get(f"/study/sessions/{session_id}/export").status_code == 403

    def test_unknown_session_is_a_clean_404(self, client):
        assert client.get("/study/sessions/999/export", headers=_auth()).status_code == 404


class TestModelSets:
    """The list the experimenter picks a set from, and how it knows which sets a
    round has already been through.

    "Used" is derived from completed sessions rather than stored, so these tests
    are mostly about the derivation holding up under the things that actually
    happen: a session abandoned, a set deliberately run twice, a round finishing.
    """

    def _sets(self, client):
        return {
            entry["id"]: entry
            for entry in client.get("/study/sets", headers=_auth()).get_json()["sets"]
        }

    def _finish(self, client, **payload):
        state = _start(client, **payload).get_json()["state"]
        client.post(
            "/study/session/end",
            json={"study_session_id": state["study_session_id"], "status": "completed"},
            headers=_auth(),
        )
        return state

    def test_a_fresh_database_offers_every_set(self, client):
        body = client.get("/study/sets", headers=_auth()).get_json()
        assert body["round"] == 1
        assert body["sets_per_round"] == 6
        assert body["remaining"] == 6
        assert len(body["sets"]) == 6
        assert not any(entry["used"] for entry in body["sets"])

    def test_a_completed_session_strikes_its_set_out(self, client):
        self._finish(client, task_order=["cane_tip", "lego"])
        sets = self._sets(client)
        assert sets["cane_tip+lego"]["used"] is True
        assert sets["cane_tip+lego"]["completed_count"] == 1
        assert sets["lego+cane_tip"]["used"] is False, "the reverse order is its own set"
        assert client.get("/study/sets", headers=_auth()).get_json()["remaining"] == 5

    def test_an_unfinished_session_does_not_consume_its_set(self, client):
        """A session that was started and never completed did not run that set.
        Marking it used would quietly drop a cell out of the design."""
        _start(client, task_order=["cane_tip", "lego"])
        sets = self._sets(client)
        assert sets["cane_tip+lego"]["used"] is False
        assert sets["cane_tip+lego"]["in_progress"] == 1

    def test_an_abandoned_session_does_not_consume_its_set_either(self, client):
        state = _start(client, task_order=["cane_tip", "lego"]).get_json()["state"]
        client.post(
            "/study/session/end",
            json={"study_session_id": state["study_session_id"], "status": "abandoned"},
            headers=_auth(),
        )
        sets = self._sets(client)
        assert sets["cane_tip+lego"]["used"] is False
        assert sets["cane_tip+lego"]["in_progress"] == 0

    def test_a_full_round_repopulates_the_whole_list(self, client):
        """The rollover the experimenter never has to trigger: once every set has
        been run, they all come back unstruck as a new round."""
        for entry in study_protocol.task_sets():
            self._finish(client, task_order=entry["task_order"])

        body = client.get("/study/sets", headers=_auth()).get_json()
        assert body["round"] == 2
        assert body["remaining"] == 6
        assert not any(entry["used"] for entry in body["sets"]), (
            "a finished round should look like a fresh one"
        )
        assert all(entry["completed_count"] == 1 for entry in body["sets"])

    def test_a_partly_finished_second_round_strikes_only_what_it_ran(self, client):
        for entry in study_protocol.task_sets():
            self._finish(client, task_order=entry["task_order"])
        self._finish(client, task_order=["cane_tip", "lego"])

        body = client.get("/study/sets", headers=_auth()).get_json()
        sets = {entry["id"]: entry for entry in body["sets"]}
        assert body["round"] == 2
        assert body["remaining"] == 5
        assert sets["cane_tip+lego"]["used"] is True
        assert sets["cane_tip+lego"]["completed_count"] == 2
        assert sets["lego+cane_tip"]["used"] is False

    def test_a_deliberate_repeat_is_absorbed_rather_than_hidden(self, client):
        """An experimenter can pick a struck-through set -- a missing print, a
        participant who saw one of these in a pilot. The list has to keep telling
        the truth about what was run rather than silently levelling out."""
        self._finish(client, task_order=["cane_tip", "lego"])
        self._finish(client, task_order=["cane_tip", "lego"])
        sets = self._sets(client)
        assert sets["cane_tip+lego"]["completed_count"] == 2
        assert sets["cane_tip+lego"]["used"] is True
        assert client.get("/study/sets", headers=_auth()).get_json()["remaining"] == 5

    def test_sessions_on_retired_pairs_are_ignored_not_counted(self, client):
        """Old sessions in a real database name the coat rack. They belong to no
        current set and must not shift the round on."""
        study_db.create_participant("PX1")
        participant = study_db.get_participant_by_code("PX1")
        session = study_db.create_session(
            participant_id=int(participant["id"]),
            participant_code="PX1",
            session_number=1,
            task_order=["cane_tip", "coat_rack"],
            protocol_version="old",
        )
        study_db.complete_session(int(session["id"]))

        body = client.get("/study/sets", headers=_auth()).get_json()
        assert body["round"] == 1
        assert body["remaining"] == 6

    def test_a_set_names_its_models_in_words(self, client):
        """The panel reads these out; a key like pencil_holder is not a label."""
        sets = self._sets(client)
        assert sets["cane_tip+lego"]["labels"] == ["Cane tip", "Lego brick"]
        assert sets["cane_tip+lego"]["task_order"] == ["cane_tip", "lego"]

    def test_starting_a_session_on_a_chosen_set_uses_it(self, client):
        """The whole point: the panel sends the set, and that is what the session
        runs -- not whatever the rotation would have handed out."""
        rotation = study_protocol.assign_task_order(1)
        chosen = ["lego", "cane_tip"]
        assert chosen != rotation
        state = _start(client, task_order=chosen).get_json()["state"]
        assert state["task_order"] == chosen

    def test_a_set_named_with_a_retired_pair_is_refused(self, client):
        """A stale panel or a bookmarked call naming the coat rack used to start a
        session one task short, visible only as a task 2 that loaded nothing."""
        response = _start(client, task_order=["cane_tip", "coat_rack"])
        assert response.status_code == 400
        assert "coat_rack" in response.get_json()["message"]


class TestAbandonedSessionsAreClosed:
    """Sessions nobody closed.

    A session ends when the experimenter presses End, or when a solo
    participant finishes the last step. Neither happens if the panel tab is just
    closed, and on both deployed servers that is what usually happened: 12 of 14
    sessions were still 'active', one stuck at step 19 of 22 and several at step
    0 for days. Every one was reported to the next experimenter as a session
    running on that model set.
    """

    def _age_session(self, session_id, seconds):
        """Backdate everything that counts as activity for this session."""
        old = study_db._parse(study_db.now())
        assert old is not None
        stamp = (old - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        conn = study_db._get_conn()
        conn.execute(
            "UPDATE study_sessions SET started_at = ?, step_started_at = ? WHERE id = ?",
            (stamp, stamp, session_id),
        )
        conn.execute(
            "UPDATE study_events SET created_at = ? WHERE study_session_id = ?",
            (stamp, session_id),
        )
        conn.execute(
            "UPDATE study_renders SET created_at = ? WHERE study_session_id = ?",
            (stamp, session_id),
        )
        conn.commit()

    def test_a_long_idle_session_is_closed(self, client):
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        self._age_session(session_id, 13 * 3600)

        closed = study_module.sweep_idle_sessions(force=True)
        assert [int(c["id"]) for c in closed] == [session_id]
        assert study_db.get_study_session(session_id)["status"] == "abandoned"

    def test_it_is_abandoned_never_completed(self, client):
        """The distinction is not cosmetic. A completed session counts as having
        used its model set, so inventing one for a session nobody finished would
        quietly take a cell out of the counterbalanced design."""
        state = _start(client, task_order=["cane_tip", "lego"]).get_json()["state"]
        self._age_session(state["study_session_id"], 13 * 3600)
        study_module.sweep_idle_sessions(force=True)

        assert study_db.get_study_session(state["study_session_id"])["status"] == "abandoned"
        sets = {
            entry["id"]: entry
            for entry in client.get("/study/sets", headers=_auth()).get_json()["sets"]
        }
        assert sets["cane_tip+lego"]["used"] is False
        assert sets["cane_tip+lego"]["in_progress"] == 0

    def test_a_live_session_is_left_alone(self, client):
        """The one bug here that would cost real data is closing a session
        somebody is sitting in front of."""
        state = _start(client).get_json()["state"]
        assert study_module.sweep_idle_sessions(force=True) == []
        assert study_db.get_study_session(state["study_session_id"])["status"] == "active"

    def test_recent_activity_keeps_a_session_alive(self, client):
        """Liveness is the last thing that happened in the session, not when it
        started. A session running past the timeout must not be closed under the
        participant."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        self._age_session(session_id, 13 * 3600)
        # One interaction, now.
        client.post(
            f"/study/event?s={_key(session_id)}",
            json={"event_type": "keyboard", "event_data": {"key": "r"}},
        )

        assert study_module.sweep_idle_sessions(force=True) == []
        assert study_db.get_study_session(session_id)["status"] == "active"

    def test_closing_keeps_every_recorded_interaction(self, client):
        """Nothing is deleted. A session that ran for an hour before being
        abandoned keeps its whole record; all that changes is that it stops
        claiming to be in progress."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        for _ in range(5):
            client.post(
                f"/study/event?s={_key(session_id)}",
                json={"event_type": "keyboard", "event_data": {"key": "i"}},
            )
        before = study_db.session_counts(session_id)["events"]
        self._age_session(session_id, 13 * 3600)
        study_module.sweep_idle_sessions(force=True)

        after = study_db.session_counts(session_id)["events"]
        assert after >= before, "closing a session must not remove its events"
        keys = [
            e
            for e in study_db.export_session(session_id)["events"]
            if e["event_type"] == "keyboard"
        ]
        assert len(keys) == 5

    def test_the_closure_says_it_was_automatic(self, client):
        """A session closed by disuse must read differently in the log from one
        an experimenter deliberately ended."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        self._age_session(session_id, 13 * 3600)
        study_module.sweep_idle_sessions(force=True)

        ends = [
            e
            for e in study_db.export_session(session_id)["events"]
            if e["event_type"] == "session_end"
        ]
        assert ends, "no session_end recorded"
        assert ends[-1]["event_data"]["closed_automatically"] is True
        assert ends[-1]["source"] == "server"

    def test_the_sweep_is_throttled(self, client):
        """It runs from ordinary requests, so it must not rescan on every poll."""
        study_module.sweep_idle_sessions(force=True)
        state = _start(client).get_json()["state"]
        self._age_session(state["study_session_id"], 13 * 3600)
        # Not forced, and one just ran: this must be a no-op.
        assert study_module.sweep_idle_sessions() == []
        assert study_db.get_study_session(state["study_session_id"])["status"] == "active"

    def test_the_timeout_is_configurable(self, client, monkeypatch):
        monkeypatch.setenv("STUDY_SESSION_IDLE_HOURS", "2")
        assert study_module.idle_timeout_seconds() == 7200

    @pytest.mark.parametrize("bad", ["", "nonsense", "0", "-3"])
    def test_a_broken_timeout_falls_back_rather_than_closing_everything(
        self, client, monkeypatch, bad
    ):
        """A zero or unparseable setting must not mean "close every session"."""
        monkeypatch.setenv("STUDY_SESSION_IDLE_HOURS", bad)
        assert study_module.idle_timeout_seconds() == int(12 * 3600)

    def test_an_unreadable_timestamp_leaves_the_session_alone(self, client):
        """Closing a session because its clock is unreadable is the one failure
        here that would cost a live session."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        conn = study_db._get_conn()
        conn.execute(
            "UPDATE study_sessions SET started_at = ?, step_started_at = ? WHERE id = ?",
            ("not-a-timestamp", None, session_id),
        )
        conn.execute(
            "UPDATE study_events SET created_at = ? WHERE study_session_id = ?",
            ("not-a-timestamp", session_id),
        )
        conn.commit()

        assert study_module.sweep_idle_sessions(force=True) == []
        assert study_db.get_study_session(session_id)["status"] == "active"


class TestConfig:
    def test_config_reports_missing_models(self, client, monkeypatch):
        monkeypatch.setattr(study_module, "_model_list_provider", lambda: ["mug"])
        body = client.get("/study/config", headers=_auth()).get_json()
        assert "lego_2x3" in body["missing_models"]

    def test_config_carries_no_questionnaire_content(self, client):
        body = client.get("/study/config", headers=_auth()).get_json()
        assert "background_questions" not in body
        assert "likert_items" not in body


# ---------------------------------------------------------------------------
# Two sessions at once
#
# One deployment serves the whole team, so two experimenters in different cities
# can be running participants at the same moment. Each of these pins a failure
# that was reproduced against the single-session implementation: it abandoned
# the first session mid-task, re-pointed its participant's page at the second
# session's step, loaded a different model onto their braille display, logged
# their keypresses against the other participant, and silently dropped their
# renders.
# ---------------------------------------------------------------------------

class TestConcurrentSessions:
    @pytest.fixture()
    def two(self, client):
        """Two sessions running side by side, each at a different step."""
        first = _start(client, participant_code="AAA", task_order=["cane_tip", "lego"])
        first_state = first.get_json()["state"]
        # Looked up rather than a hard-coded index, so a protocol edit that
        # changes the step count ahead of task 1 does not silently retarget
        # this fixture onto a different step.
        steps = client.get(
            f"/study/state?study_session_id={first_state['study_session_id']}", headers=_auth()
        ).get_json()["steps"]
        target_index = next(s["index"] for s in steps if s["id"] == "task1.a.virtual")
        client.post(
            "/study/step/advance",
            json={"step_index": target_index, "study_session_id": first_state["study_session_id"]},
            headers=_auth(),
        )
        first_state["step_index"] = target_index
        second = _start(client, participant_code="BBB", task_order=["lego", "pencil_holder"])
        second_state = second.get_json()["state"]
        return client, first_state, second_state

    def test_both_stay_active(self, two):
        _, first, second = two
        assert study_db.get_study_session(first["study_session_id"])["status"] == "active"
        assert study_db.get_study_session(second["study_session_id"])["status"] == "active"

    def test_the_first_session_keeps_its_place(self, two):
        """Starting the second must not rewind or end the first."""
        _, first, _ = two
        assert study_db.get_study_session(first["study_session_id"])["step_index"] == first["step_index"]

    def test_each_session_gets_its_own_participant_key(self, two):
        _, first, second = two
        assert first["participant_key"]
        assert second["participant_key"]
        assert first["participant_key"] != second["participant_key"]

    def test_a_participant_reaches_their_own_session_by_key(self, two):
        client, first, second = two
        a = client.get(f"/study/state?s={first['participant_key']}").get_json()
        b = client.get(f"/study/state?s={second['participant_key']}").get_json()
        assert a["study_session_id"] == first["study_session_id"]
        assert b["study_session_id"] == second["study_session_id"]
        assert a["step_id"] != b["step_id"]

    def test_each_participant_is_shown_their_own_model(self, two):
        """The failure that matters most: a model loading onto the wrong
        participant's braille display mid-exploration."""
        client, first, second = two
        a = client.get(f"/study/state?s={first['participant_key']}").get_json()
        assert a["model"]["stem"] == "cane_tip_hook"  # AAA is at task 1 part A
        b = client.get(f"/study/state?s={second['participant_key']}").get_json()
        assert b["model"] is None  # BBB is still on the opening step

    def test_interactions_are_logged_against_the_right_participant(self, two):
        client, first, second = two
        client.post(
            f"/study/event?s={first['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "arrowup"}},
        )
        client.post(
            f"/study/event?s={second['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "pagedown"}},
        )

        def keys_for(session_id):
            return [
                event["event_data"]["key"]
                for event in study_db.export_session(session_id)["events"]
                if event["event_type"] == "keyboard"
            ]

        assert keys_for(first["study_session_id"]) == ["arrowup"]
        assert keys_for(second["study_session_id"]) == ["pagedown"]

    def test_a_ready_signal_reaches_only_its_own_session(self, two):
        client, first, second = two
        client.post(f"/study/step/ready?s={first['participant_key']}", json={})

        a = client.get(
            f"/study/state?study_session_id={first['study_session_id']}", headers=_auth()
        ).get_json()
        b = client.get(
            f"/study/state?study_session_id={second['study_session_id']}", headers=_auth()
        ).get_json()
        assert a["participant_ready"] is not None
        assert b["participant_ready"] is None

    def test_advancing_one_session_does_not_move_the_other(self, two):
        client, first, second = two
        before = study_db.get_study_session(second["study_session_id"])["step_index"]
        client.post(
            "/study/step/advance",
            json={"direction": "next", "study_session_id": first["study_session_id"]},
            headers=_auth(),
        )
        assert study_db.get_study_session(second["study_session_id"])["step_index"] == before

    def test_ending_one_session_leaves_the_other_running(self, two):
        client, first, second = two
        client.post(
            "/study/session/end",
            json={"study_session_id": first["study_session_id"]},
            headers=_auth(),
        )
        assert study_db.get_study_session(first["study_session_id"])["status"] == "completed"
        assert study_db.get_study_session(second["study_session_id"])["status"] == "active"

    def test_renders_are_attributed_per_session(self, two):
        client, first, second = two
        for session, stem in (
            (first["study_session_id"], "cane_tip_hook"),
            (second["study_session_id"], "mug"),
        ):
            with flask_app.test_request_context(
                "/render", headers={"X-Study-Session": str(session)}
            ):
                study_module.record_render_for_request(
                    {"depth": 50}, model_stem=stem, cache_hit=False
                )

        assert [r["model"] for r in study_db.export_session(first["study_session_id"])["renders"]] == [
            "cane_tip_hook"
        ]
        assert [r["model"] for r in study_db.export_session(second["study_session_id"])["renders"]] == [
            "mug"
        ]

    def test_each_session_writes_its_own_log_file(self, two):
        _, first, second = two
        a = study_db.get_study_session(first["study_session_id"])["log_path"]
        b = study_db.get_study_session(second["study_session_id"])["log_path"]
        assert a != b
        assert "AAA" in a and "BBB" in b

    def test_sequence_numbers_are_independent_per_session(self, two):
        """Ordering is per session, so two sessions logging at once cannot
        interleave each other's sequence numbers."""
        client, first, second = two
        for _ in range(3):
            client.post(f"/study/event?s={first['participant_key']}", json={"event_type": "keyboard"})
        client.post(f"/study/event?s={second['participant_key']}", json={"event_type": "keyboard"})

        for session_id in (first["study_session_id"], second["study_session_id"]):
            export = study_db.export_session(session_id)
            sequences = sorted(e["seq"] for e in export["events"])
            assert sequences == list(range(1, len(sequences) + 1))


class TestTheJoinCodeIsAlwaysRequired:
    """A participant's browser says which session it belongs to with a
    four-character code, whether one session is running or five.

    It used to fall back to "the single active session" when no code was given,
    so the instruction to a participant changed depending on how many sessions
    happened to be up -- and a page that attached that way held nothing of its
    own, so when its session ended it drifted onto the next one.
    """

    def test_an_event_without_a_code_is_not_recorded(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        before = len(study_db.export_session(session_id)["events"])
        response = client.post("/study/event", json={"event_type": "keyboard"})
        assert response.get_json()["status"] == "ignored"
        assert len(study_db.export_session(session_id)["events"]) == before

    def test_a_readiness_signal_without_a_code_is_refused(self, client):
        _start(client)
        assert client.post("/study/step/ready", json={}).status_code == 404

    def test_the_participant_view_asks_for_the_code(self, client):
        """One session running, no code: the page still asks. Same instruction
        every time."""
        _start(client)
        assert client.get("/study/state").get_json()["active"] is False

    def test_a_wrong_code_joins_nothing(self, client):
        _start(client)
        payload = client.get("/study/state?s=ZZZZ").get_json()
        assert payload["active"] is False
        assert payload.get("study_session_id") is None

    def test_the_right_code_joins_that_session(self, client):
        state = _start(client).get_json()["state"]
        payload = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["active"] is True
        assert payload["study_session_id"] == state["study_session_id"]

    def test_the_code_is_short_enough_to_read_out(self, client):
        assert len(_start(client).get_json()["state"]["participant_key"]) == 4

    def test_the_code_avoids_characters_that_sound_or_look_alike(self, client):
        """It is read aloud to someone who may not be able to see the screen."""
        for _ in range(20):
            state = _start(client).get_json()["state"]
            assert not (set(state["participant_key"]) & set("O0I1L5S2Z"))
            client.post("/study/session/end",
                        json={"study_session_id": state["study_session_id"]}, headers=_auth())

    def test_the_code_is_accepted_case_insensitively(self, client):
        """A participant typing it will not match the panel's capitals."""
        state = _start(client).get_json()["state"]
        payload = client.get(f"/study/state?s={state['participant_key'].lower()}").get_json()
        assert payload["active"] is True


class TestNothingIsWrittenBetweenSessions:
    """The boundary between one session and the next.

    A participant's browser does not close when their session ends. It keeps
    polling, keeps reporting keypresses, and keeps tagging renders. None of that
    may reach the record -- not its own session's, and above all not the next
    participant's.
    """

    def _run_and_end(self, client, code="AAA"):
        state = _start(client, participant_code=code).get_json()["state"]
        client.post(
            f"/study/event?s={state['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "during"}},
        )
        client.post(
            "/study/session/end",
            json={"study_session_id": state["study_session_id"]},
            headers=_auth(),
        )
        return state

    def _counts(self, session_id):
        export = study_db.export_session(session_id)
        return len(export["events"]), len(export["renders"])

    def test_a_finished_session_stops_accepting_events(self, client):
        state = self._run_and_end(client)
        before = self._counts(state["study_session_id"])

        response = client.post(
            f"/study/event?s={state['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "after-end"}},
        )
        assert response.get_json()["status"] == "ignored"
        assert self._counts(state["study_session_id"]) == before

    def test_a_finished_session_stops_accepting_renders(self, client):
        state = self._run_and_end(client)
        before = self._counts(state["study_session_id"])
        with flask_app.test_request_context(
            "/render", headers={"X-Study-Session": str(state["study_session_id"])}
        ):
            study_module.record_render_for_request({"depth": 50}, model_stem="mug", cache_hit=False)
        assert self._counts(state["study_session_id"]) == before

    def test_a_finished_session_stops_accepting_readiness(self, client):
        state = self._run_and_end(client)
        before = self._counts(state["study_session_id"])
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        assert self._counts(state["study_session_id"]) == before

    def test_the_last_event_of_a_session_is_its_end(self, client):
        state = self._run_and_end(client)
        client.post(
            f"/study/event?s={state['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "after-end"}},
        )
        events = study_db.export_session(state["study_session_id"])["events"]
        assert events[-1]["event_type"] == "session_end"

    def test_a_stale_key_does_not_reach_the_next_session(self, client):
        stale = self._run_and_end(client, code="AAA")
        nxt = _start(client, participant_code="BBB").get_json()["state"]

        client.post(
            f"/study/event?s={stale['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "stale"}},
        )
        keys = [
            e["event_data"].get("key")
            for e in study_db.export_session(nxt["study_session_id"])["events"]
            if e["event_type"] == "keyboard"
        ]
        assert "stale" not in keys

    def test_a_page_cannot_drift_onto_the_next_session(self, client):
        """A tab left open when its session ends keeps its own code, so it is
        refused rather than re-homed onto whatever is running now."""
        first = self._run_and_end(client, code="AAA")
        second = _start(client, participant_code="BBB").get_json()["state"]

        response = client.post(
            f"/study/event?s={first['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "from-the-old-tab"}},
        )
        assert response.get_json()["status"] == "ignored"

        leaked = [
            e for e in study_db.export_session(second["study_session_id"])["events"]
            if e["event_type"] == "keyboard"
            and e["event_data"].get("key") == "from-the-old-tab"
        ]
        assert not leaked, "an old participant page leaked into the next participant's record"

    def test_each_session_writes_only_its_own_participant_to_its_own_log(self, client):
        first = self._run_and_end(client, code="AAA")
        second = _start(client, participant_code="BBB").get_json()["state"]
        client.post(
            f"/study/event?s={second['participant_key']}",
            json={"event_type": "keyboard", "event_data": {"key": "b"}},
        )

        for state, expected in ((first, "AAA"), (second, "BBB")):
            path = study_db.get_study_session(state["study_session_id"])["log_path"]
            codes = {json.loads(line)["participant_code"] for line in open(path, encoding="utf-8")}
            assert codes == {expected}, f"{path} contains {codes}"


class TestConcurrentSessionsDoNotMuddleEachOther:
    """Several sessions logging at the same moment, from different threads.

    The sequential tests above prove the routing is right. These prove it stays
    right under simultaneous traffic, which is where a shared counter, a shared
    file handle or a shared "current" anything would show up.
    """

    SESSIONS = 4
    EVENTS = 40

    def test_no_event_lands_in_another_session(self, client):
        import threading

        sessions = []
        for i in range(self.SESSIONS):
            state = _start(client, participant_code=f"C{i}").get_json()["state"]
            sessions.append({"code": f"C{i}", "id": state["study_session_id"]})

        def hammer(session):
            # A client per thread: they are independent objects over one app.
            with flask_app.test_client() as own:
                for n in range(self.EVENTS):
                    own.post(
                        "/study/event",
                        json={
                            "event_type": "keyboard",
                            "event_data": {"src": session["code"], "n": n},
                            "participant_key": None,
                        },
                        headers={"X-Study-Key": _key_for(session["id"])},
                    )

        threads = [threading.Thread(target=hammer, args=(s,)) for s in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for session in sessions:
            export = study_db.export_session(session["id"])
            keyboard = [e for e in export["events"] if e["event_type"] == "keyboard"]
            assert len(keyboard) == self.EVENTS, (
                f"{session['code']} recorded {len(keyboard)} of {self.EVENTS} events"
            )
            foreign = [e for e in keyboard if e["event_data"]["src"] != session["code"]]
            assert not foreign, f"{session['code']} holds events from {foreign[:2]}"
            assert {e["participant_code"] for e in export["events"]} == {session["code"]}

    def test_sequence_numbers_stay_contiguous_per_session(self, client):
        """One counter per session, so simultaneous traffic must not leave gaps
        or repeats -- ordering within a session is what the analysis reads."""
        import threading

        ids = [
            _start(client, participant_code=f"D{i}").get_json()["state"]["study_session_id"]
            for i in range(self.SESSIONS)
        ]

        def hammer(session_id):
            for n in range(self.EVENTS):
                study_db.record_event(session_id, "keyboard", event_data={"n": n})

        threads = [threading.Thread(target=hammer, args=(i,)) for i in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for session_id in ids:
            export = study_db.export_session(session_id)
            sequences = sorted(
                [e["seq"] for e in export["events"]] + [r["seq"] for r in export["renders"]]
            )
            assert sequences == list(range(1, len(sequences) + 1)), (
                f"session {session_id} has gaps or repeats: {sequences[:20]}"
            )

    def test_no_log_file_receives_another_session_line(self, client):
        import threading

        sessions = []
        for i in range(self.SESSIONS):
            state = _start(client, participant_code=f"E{i}").get_json()["state"]
            sessions.append({"code": f"E{i}", "id": state["study_session_id"]})

        def hammer(session):
            for n in range(self.EVENTS):
                study_db.record_event(
                    session["id"], "keyboard", event_data={"src": session["code"], "n": n}
                )

        threads = [threading.Thread(target=hammer, args=(s,)) for s in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for session in sessions:
            path = study_db.get_study_session(session["id"])["log_path"]
            lines = [json.loads(line) for line in open(path, encoding="utf-8")]
            codes = {line["participant_code"] for line in lines}
            assert codes == {session["code"]}, f"{path} contains {codes}"
            sources = {
                line["event_data"].get("src")
                for line in lines
                if line["event_type"] == "keyboard"
            }
            assert sources == {session["code"]}, f"{path} carries events from {sources}"
            sequences = [line["seq"] for line in lines]
            assert sequences == sorted(sequences), "lines are out of sequence order"

    def test_the_same_step_resolves_to_each_session_own_model(self, client):
        """The sharpest check that nothing is shared: two sessions sitting on the
        same step id must load different models, because their Latin-square
        orders differ. A shared 'current step' or 'current model' would collapse
        them onto one."""
        first = _start(client, participant_code="F1", task_order=["cane_tip", "lego"])
        first_id = first.get_json()["state"]["study_session_id"]
        second = _start(client, participant_code="F2", task_order=["pencil_holder", "lego"])
        second_id = second.get_json()["state"]["study_session_id"]

        for session_id in (first_id, second_id):
            for _ in range(40):
                state = client.get(
                    f"/study/state?study_session_id={session_id}", headers=_auth()
                ).get_json()
                if state["step"]["id"] == "task1.a.virtual":
                    break
                client.post(
                    "/study/step/advance",
                    json={"direction": "next", "study_session_id": session_id},
                    headers=_auth(),
                )

        def autoloaded(session_id):
            return [
                e["event_data"]["model"]
                for e in study_db.export_session(session_id)["events"]
                if e["event_type"] == "model_autoload" and e["step_id"] == "task1.a.virtual"
            ]

        assert autoloaded(first_id) == ["cane_tip_hook"]
        assert autoloaded(second_id) == ["pencil_holder_2x2"]


def _key_for(session_id: int) -> str:
    return study_db.get_study_session(session_id)["participant_key"]


class TestIdentityIsAllocatedByTheDatabase:
    """The participant's identity is an id SQLite hands out; P01, P02 is a label
    derived from it.

    It used to be the other way round: the code was the primary key and the next
    one was found by reading every existing code and picking the first gap. Two
    experimenters enrolling at the same instant read the same gap, and five of
    six simultaneous enrolments failed on UNIQUE violations and a locked
    database. An id cannot be raced for.
    """

    def test_simultaneous_enrolments_all_succeed_with_distinct_identities(self, client):
        import threading

        count = 8
        barrier = threading.Barrier(count)
        results, lock = [], threading.Lock()

        def enrol():
            with flask_app.test_client() as own:
                barrier.wait()  # every request fires at the same instant
                response = own.post("/study/session/start", json={}, headers=_auth())
                with lock:
                    results.append((response.status_code, response.get_json()))

        threads = [threading.Thread(target=enrol) for _ in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        failures = [(code, body) for code, body in results if code != 200]
        assert not failures, f"{len(failures)} of {count} enrolments failed: {failures[:2]}"

        states = [body["state"] for _, body in results]
        assert len({s["participant_id"] for s in states}) == count, "participant ids collided"
        assert len({s["participant_code"] for s in states}) == count, "participant codes collided"
        assert len({s["study_session_id"] for s in states}) == count, "session ids collided"

    def test_the_code_is_derived_from_the_id(self, client):
        state = _start(client).get_json()["state"]
        assert state["participant_code"] == study_db.code_for(state["participant_id"])

    def test_a_typed_code_still_names_a_participant(self, client):
        """A returning participant keeps their code, and with it their assignment."""
        first = _start(client, participant_code="P01").get_json()["state"]
        client.post("/study/session/end",
                    json={"study_session_id": first["study_session_id"]}, headers=_auth())
        again = _start(
            client, participant_code="P01", session_number=2
        ).get_json()["state"]
        assert again["participant_id"] == first["participant_id"]
        assert again["task_order"] == first["task_order"]

    def test_the_latin_square_follows_the_id(self, client):
        """Counterbalancing indexes the identity, so it stays balanced even when
        participants are enrolled simultaneously."""
        orders = []
        for _ in range(6):
            state = _start(client).get_json()["state"]
            orders.append(tuple(state["task_order"]))
            client.post("/study/session/end",
                        json={"study_session_id": state["study_session_id"]}, headers=_auth())
        assert len(set(orders)) == 6, f"assignments repeated within one cycle: {orders}"

    def test_ids_are_never_reused_after_a_participant_is_removed(self, client):
        """AUTOINCREMENT, not max(id)+1: a deleted row must not hand its number to
        somebody else, or two participants share a code in the exported data."""
        first = _start(client).get_json()["state"]
        conn = study_db._get_conn()
        # Children first: the foreign keys correctly refuse to orphan them.
        for table in ("study_events", "study_renders"):
            conn.execute(
                f"DELETE FROM {table} WHERE study_session_id = ?", (first["study_session_id"],)
            )
        conn.execute("DELETE FROM study_sessions WHERE id = ?", (first["study_session_id"],))
        conn.execute("DELETE FROM participants WHERE id = ?", (first["participant_id"],))
        conn.commit()

        second = study_db.create_participant()
        assert second["id"] > first["participant_id"], (
            "a deleted participant's number was handed to somebody else, so two "
            "participants would share a code in the exported data"
        )

    def test_every_row_carries_the_numeric_identity(self, client):
        state = _start(client).get_json()["state"]
        client.post(f"/study/event?s={state['participant_key']}",
                    json={"event_type": "keyboard"})
        with flask_app.test_request_context(
            "/render", headers={"X-Study-Session": str(state["study_session_id"])}
        ):
            study_module.record_render_for_request({"depth": 50}, model_stem="mug", cache_hit=False)

        export = study_db.export_session(state["study_session_id"])
        for row in export["events"] + export["renders"]:
            assert row["participant_id"] == state["participant_id"]
            assert row["participant_code"] == state["participant_code"]

    def test_simultaneous_session_ends_each_keep_their_own_record(self, client):
        """What you get if two experimenters finish at the same moment."""
        import threading

        count = 6
        states = [_start(client).get_json()["state"] for _ in range(count)]
        for state in states:
            client.post(f"/study/event?s={state['participant_key']}",
                        json={"event_type": "keyboard",
                              "event_data": {"src": state["participant_code"]}})

        barrier = threading.Barrier(count)

        def finish(state):
            with flask_app.test_client() as own:
                barrier.wait()
                own.post("/study/session/end",
                         json={"study_session_id": state["study_session_id"]}, headers=_auth())

        threads = [threading.Thread(target=finish, args=(s,)) for s in states]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for state in states:
            session = study_db.get_study_session(state["study_session_id"])
            assert session["status"] == "completed", f"{state['participant_code']} did not close"
            export = study_db.export_session(state["study_session_id"])
            ends = [e for e in export["events"] if e["event_type"] == "session_end"]
            assert len(ends) == 1, f"{state['participant_code']} has {len(ends)} end events"
            keyboard = [e for e in export["events"] if e["event_type"] == "keyboard"]
            assert [e["event_data"]["src"] for e in keyboard] == [state["participant_code"]]
            sequences = sorted(
                [e["seq"] for e in export["events"]] + [r["seq"] for r in export["renders"]]
            )
            assert sequences == list(range(1, len(sequences) + 1))


class TestUpgradingToDatabaseAssignedIdentity:
    def _text_key_database(self, path):
        """The schema as it was when the participant code was the primary key."""
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE participants (
                code TEXT PRIMARY KEY, sequence_number INTEGER NOT NULL,
                first_session_at DATETIME, created_at DATETIME);
            CREATE TABLE study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_code TEXT NOT NULL REFERENCES participants(code),
                session_number INTEGER NOT NULL DEFAULT 1, participant_key TEXT,
                task_order TEXT NOT NULL, protocol_version TEXT,
                step_index INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
                log_path TEXT, started_at DATETIME, step_started_at DATETIME,
                completed_at DATETIME, UNIQUE(participant_code, session_number));
            CREATE TABLE study_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                study_session_id INTEGER NOT NULL REFERENCES study_sessions(id),
                participant_code TEXT NOT NULL, seq INTEGER NOT NULL, part_id TEXT,
                step_id TEXT, step_index INTEGER, event_type TEXT NOT NULL,
                source TEXT NOT NULL, client_id TEXT, event_data TEXT, client_time TEXT,
                created_at DATETIME, elapsed_ms INTEGER, step_elapsed_ms INTEGER);
            CREATE TABLE study_renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                study_session_id INTEGER NOT NULL REFERENCES study_sessions(id),
                participant_code TEXT NOT NULL, seq INTEGER NOT NULL, part_id TEXT,
                step_id TEXT, step_index INTEGER, model TEXT, view TEXT, render_mode TEXT,
                layout_mode TEXT, depth REAL, zoom REAL, input_source TEXT,
                cache_hit INTEGER, orientation TEXT, created_at DATETIME,
                elapsed_ms INTEGER, step_elapsed_ms INTEGER);
            INSERT INTO participants VALUES ('P01', 1, '2026-08-08T01:35:17.937Z', '2026-08-08T01:35:17.937Z');
            INSERT INTO participants VALUES ('P02', 2, '2026-08-08T02:12:28.779Z', '2026-08-08T02:12:28.779Z');
            INSERT INTO study_sessions
                (id, participant_code, session_number, task_order, protocol_version, step_index, status)
                VALUES (1, 'P01', 1, '["pencil_holder","cane_tip"]', '2026-08-04', 28, 'completed');
            INSERT INTO study_sessions
                (id, participant_code, session_number, task_order, protocol_version, step_index, status)
                VALUES (4, 'P02', 1, '["cane_tip","coat_rack"]', '2026-08-04', 1, 'active');
            INSERT INTO study_events
                (study_session_id, participant_code, seq, event_type, source, event_data, created_at)
                VALUES (1, 'P01', 1, 'session_start', 'experimenter', '{}', '2026-08-08T01:35:17.942Z');
            INSERT INTO study_renders
                (study_session_id, participant_code, seq, model, created_at)
                VALUES (1, 'P01', 2, 'mug', '2026-08-08T01:35:20.000Z');
            """
        )
        conn.commit()
        conn.close()

    def _upgraded(self, tmp_path, monkeypatch):
        legacy = tmp_path / "study.db"
        self._text_key_database(legacy)
        monkeypatch.setattr(study_db, "DB_PATH", legacy)
        monkeypatch.setattr(study_db, "LOG_DIR", tmp_path / "logs")
        study_db._local.__dict__.clear()
        study_db.init_db()
        return legacy

    def test_existing_participants_keep_their_code_and_their_position(self, tmp_path, monkeypatch):
        """The old sequence_number becomes the id, so a participant already run
        keeps the model pairs the Latin square gave them."""
        self._upgraded(tmp_path, monkeypatch)
        assert study_db.get_participant_by_code("P01")["id"] == 1
        assert study_db.get_participant_by_code("P02")["id"] == 2
        study_db._local.__dict__.clear()

    def test_existing_sessions_and_rows_are_relinked(self, tmp_path, monkeypatch):
        self._upgraded(tmp_path, monkeypatch)
        conn = study_db._get_conn()
        assert conn.execute("SELECT participant_id FROM study_sessions WHERE id=1").fetchone()[0] == 1
        assert conn.execute("SELECT participant_id FROM study_sessions WHERE id=4").fetchone()[0] == 2
        for table in ("study_events", "study_renders"):
            missing = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE participant_id IS NULL"
            ).fetchone()[0]
            assert missing == 0, f"{table} has {missing} rows with no participant id"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        study_db._local.__dict__.clear()

    def test_no_session_data_is_lost(self, tmp_path, monkeypatch):
        self._upgraded(tmp_path, monkeypatch)
        session = study_db.get_study_session(1)
        assert session["participant_code"] == "P01"
        assert session["step_index"] == 28
        assert session["status"] == "completed"
        export = study_db.export_session(1)
        assert len(export["events"]) == 1 and len(export["renders"]) == 1
        study_db._local.__dict__.clear()

    def test_the_next_participant_does_not_reuse_a_migrated_id(self, tmp_path, monkeypatch):
        self._upgraded(tmp_path, monkeypatch)
        created = study_db.create_participant()
        assert created["id"] == 3 and created["code"] == "P03"
        study_db._local.__dict__.clear()

    def test_the_upgrade_is_idempotent(self, tmp_path, monkeypatch):
        self._upgraded(tmp_path, monkeypatch)
        study_db.init_db()
        study_db.init_db()
        assert study_db.get_participant_by_code("P01")["id"] == 1
        study_db._local.__dict__.clear()

    def test_a_half_finished_upgrade_recovers(self, tmp_path, monkeypatch):
        """A rebuild that died between creating the new table and swapping it in
        would otherwise wedge every start after it."""
        legacy = tmp_path / "study.db"
        self._text_key_database(legacy)
        import sqlite3

        conn = sqlite3.connect(str(legacy))
        conn.execute("CREATE TABLE participants_rebuilt (id INTEGER PRIMARY KEY, code TEXT)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(study_db, "DB_PATH", legacy)
        study_db._local.__dict__.clear()
        study_db.init_db()
        assert study_db.get_participant_by_code("P01")["id"] == 1
        study_db._local.__dict__.clear()


class TestPanelAndParticipantAreToldApartWithoutAToken:
    """With the panel open by default, the token no longer says who is asking.

    A request identifies itself by what it carries: a participant has a join
    code, a panel names a session. Neither means participant -- the payload that
    withholds the answer key is the safe default when we cannot tell.
    """

    @pytest.fixture()
    def open_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(study_db, "DB_PATH", tmp_path / "study.db")
        monkeypatch.setattr(study_db, "LOG_DIR", tmp_path / "logs")
        monkeypatch.delenv("STUDY_CONTROL_TOKEN", raising=False)
        study_db._local.__dict__.clear()
        study_db.init_db()
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            yield c
        study_db._local.__dict__.clear()

    def test_a_bare_request_gets_the_participant_payload(self, open_client):
        """Not the experimenter one, and not a 409. This is what a participant's
        browser sends before they have typed their code, and it used to be
        treated as a panel request and answered with 'which session do you mean'
        -- so the page never got as far as asking for the code."""
        open_client.post("/study/session/start", json={})
        open_client.post("/study/session/start", json={})  # two running

        response = open_client.get("/study/state")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["active"] is False
        assert "step" not in payload, "a bare request must not get the experimenter payload"

    def test_a_code_gets_the_participant_payload(self, open_client):
        state = open_client.post("/study/session/start", json={}).get_json()["state"]
        payload = open_client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["active"] is True
        assert "script" not in payload
        assert "pair" not in payload

    def test_naming_a_session_gets_the_panel_payload(self, open_client):
        state = open_client.post("/study/session/start", json={}).get_json()["state"]
        payload = open_client.get(
            f"/study/state?study_session_id={state['study_session_id']}"
        ).get_json()
        assert payload["active"] is True
        assert payload["step"]["script"], "the panel needs its script"

    def test_two_running_sessions_do_not_break_a_participant_arriving(self, open_client):
        """The regression this class exists for: with two sessions up, a
        participant opening /study got a 409 instead of the code prompt."""
        open_client.post("/study/session/start", json={})
        second = open_client.post("/study/session/start", json={}).get_json()["state"]

        assert open_client.get("/study/state").status_code == 200
        joined = open_client.get(f"/study/state?s={second['participant_key']}").get_json()
        assert joined["study_session_id"] == second["study_session_id"]


class TestSingleDeviceMode:
    """One laptop between the experimenter and the participant.

    The panel is not reachable without taking the screen reader off someone
    mid-exploration, so the session moves on when the participant presses "I am
    ready to move on" instead of waiting for a Next button. Nothing else
    changes: the page they see is the ordinary participant view, and the record
    is the same one a two-machine session writes.
    """

    def _solo(self, client, **payload):
        state = _start(client, **payload).get_json()["state"]
        client.post(
            "/study/session/mode",
            json={"mode": "solo", "study_session_id": state["study_session_id"]},
            headers=_auth(),
        )
        return state

    def test_ready_advances_the_step(self, client):
        state = self._solo(client)
        before = study_db.get_study_session(state["study_session_id"])["step_index"]
        response = client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        assert response.get_json()["advanced"] is True
        after = study_db.get_study_session(state["study_session_id"])["step_index"]
        assert after == before + 1

    def test_ready_stays_advisory_on_two_machines(self, client):
        """The default is unchanged: a stray press must not skip a step and lose
        the exploration that was still in progress."""
        state = _start(client).get_json()["state"]
        before = study_db.get_study_session(state["study_session_id"])["step_index"]
        response = client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        assert response.get_json()["advanced"] is False
        assert study_db.get_study_session(state["study_session_id"])["step_index"] == before

    def test_the_mode_survives_a_reload(self, client):
        """It is a property of the session, not of the URL a browser happens to
        be sitting on."""
        state = self._solo(client)
        assert study_db.get_study_session(state["study_session_id"])["mode"] == "solo"
        payload = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["mode"] == "solo"

    def test_the_participant_page_is_unchanged(self, client):
        """Exactly /study. The mode changes what the ready button does, not what
        is on screen -- and it must not start leaking the script or answer key
        onto a page the participant's screen reader can read."""
        solo = self._solo(client, participant_code="SOLO")
        _advance_to(client, "task1.b.virtual")
        payload = client.get(f"/study/state?s={solo['participant_key']}").get_json()

        assert "script" not in payload
        assert "pair" not in payload
        assert "differences" not in json.dumps(payload)
        # Same keys as an ordinary participant payload, plus the mode.
        paired = _start(client, participant_code="PAIR").get_json()["state"]
        ordinary = client.get(f"/study/state?s={paired['participant_key']}").get_json()
        assert set(payload) == set(ordinary)

    def test_the_record_is_the_same_as_a_two_machine_session(self, client):
        """The point of this test: how a session was driven must not change what
        it wrote down. Same event types, same order, same step context."""

        def event_shape(session_id):
            return [
                (e["event_type"], e["step_id"], e["step_index"])
                for e in study_db.export_session(session_id)["events"]
            ]

        # Two machines: the panel advances.
        paired = _start(client, participant_code="PAIR").get_json()["state"]
        client.post(f"/study/step/ready?s={paired['participant_key']}", json={})
        client.post(
            "/study/step/advance",
            json={"direction": "next", "study_session_id": paired["study_session_id"]},
            headers=_auth(),
        )

        # One machine: the ready button advances.
        solo = self._solo(client, participant_code="SOLO")
        client.post(f"/study/step/ready?s={solo['participant_key']}", json={})

        paired_events = [e for e in event_shape(paired["study_session_id"])]
        solo_events = [
            e for e in event_shape(solo["study_session_id"]) if e[0] != "session_mode"
        ]
        assert solo_events == paired_events, (
            f"solo wrote {solo_events}, a two-machine session wrote {paired_events}"
        )

    def test_the_advance_is_attributed_to_the_participant(self, client):
        """They pressed the button, so the log should say so -- the only thing
        that legitimately differs between the two modes."""
        state = self._solo(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        advances = [
            e for e in study_db.export_session(state["study_session_id"])["events"]
            if e["event_type"] == "step_advance"
        ]
        assert [e["source"] for e in advances] == ["participant"]

    def test_switching_modes_is_recorded(self, client):
        state = self._solo(client)
        modes = [
            e["event_data"]["mode"]
            for e in study_db.export_session(state["study_session_id"])["events"]
            if e["event_type"] == "session_mode"
        ]
        assert modes == ["solo"]

    def test_ready_does_not_run_off_the_end_of_the_protocol(self, client):
        state = self._solo(client)
        steps = client.get(
            f"/study/state?study_session_id={state['study_session_id']}", headers=_auth()
        ).get_json()["step_count"]
        for _ in range(steps + 5):
            client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        assert study_db.get_study_session(state["study_session_id"])["step_index"] == steps - 1


class TestSingleDeviceBackCommand:
    """The participant's own "go back a step" command -- solo sessions only.

    Mirrors TestSingleDeviceMode's ready-signal tests: same reasoning, same
    exception (no separate panel to press Previous on), just the opposite
    direction.
    """

    def _solo(self, client, **payload):
        state = _start(client, **payload).get_json()["state"]
        client.post(
            "/study/session/mode",
            json={"mode": "solo", "study_session_id": state["study_session_id"]},
            headers=_auth(),
        )
        return state

    def test_back_moves_to_the_previous_step(self, client):
        state = self._solo(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        before = study_db.get_study_session(state["study_session_id"])["step_index"]
        response = client.post(f"/study/step/back?s={state['participant_key']}", json={})
        assert response.get_json()["moved"] is True
        after = study_db.get_study_session(state["study_session_id"])["step_index"]
        assert after == before - 1

    def test_back_is_refused_on_two_machines(self, client):
        """The token gate on /study/step/advance exists so a participant cannot
        rewind the protocol themselves; this route carries no token, so the
        mode check is the only thing standing in for it."""
        state = _start(client).get_json()["state"]
        client.post("/study/step/advance", json={"direction": "next"}, headers=_auth())
        before = study_db.get_study_session(state["study_session_id"])["step_index"]
        response = client.post(f"/study/step/back?s={state['participant_key']}", json={})
        assert response.status_code == 404
        assert study_db.get_study_session(state["study_session_id"])["step_index"] == before

    def test_back_does_not_run_off_the_start(self, client):
        state = self._solo(client)
        response = client.post(f"/study/step/back?s={state['participant_key']}", json={})
        assert response.get_json()["moved"] is False
        assert study_db.get_study_session(state["study_session_id"])["step_index"] == 0

    def test_back_without_a_session_is_a_clean_404(self, client):
        assert client.post("/study/step/back", json={}).status_code == 404

    def test_the_back_is_attributed_to_the_participant(self, client):
        state = self._solo(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        client.post(f"/study/step/back?s={state['participant_key']}", json={})
        advances = [
            e for e in study_db.export_session(state["study_session_id"])["events"]
            if e["event_type"] == "step_advance"
        ]
        assert [e["source"] for e in advances] == ["participant", "participant"]


class TestTheEndOfASingleDeviceSession:
    """The last step, with one laptop and no panel to close the session from.

    Everything was already being recorded, but the session simply stopped: no
    session_end, status left active, completed_at null, the database never
    checkpointed -- and the page told the participant to keep exploring until an
    experimenter moved them on.
    """

    def _solo_at_last_step(self, client):
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        client.post(
            "/study/session/mode",
            json={"mode": "solo", "study_session_id": session_id},
            headers=_auth(),
        )
        steps = client.get(
            f"/study/state?study_session_id={session_id}", headers=_auth()
        ).get_json()["step_count"]
        client.post(
            "/study/step/advance",
            json={"step_index": steps - 1, "study_session_id": session_id},
            headers=_auth(),
        )
        return state, steps

    def test_ready_on_the_last_step_ends_the_session(self, client):
        state, _ = self._solo_at_last_step(client)
        response = client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        assert response.get_json()["finished"] is True

        session = study_db.get_study_session(state["study_session_id"])
        assert session["status"] == "completed"
        assert session["completed_at"] is not None

    def test_the_end_is_recorded(self, client):
        state, _ = self._solo_at_last_step(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        events = study_db.export_session(state["study_session_id"])["events"]
        ends = [e for e in events if e["event_type"] == "session_end"]
        assert len(ends) == 1
        assert ends[0]["source"] == "participant"
        assert events[-1]["event_type"] == "session_end", "the end must be the last thing written"

    def test_the_database_is_self_contained_afterwards(self, client, study_env):
        """Ending checkpoints the WAL. A session that never ended left its rows
        in study.db-wal, so copying study.db alone lost them."""
        import shutil
        import sqlite3

        state, _ = self._solo_at_last_step(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})

        expected = len(study_db.export_session(state["study_session_id"])["events"])
        alone = study_env / "copied-alone.db"
        shutil.copy(study_db.DB_PATH, alone)
        conn = sqlite3.connect(str(alone))
        got = conn.execute("SELECT COUNT(*) FROM study_events").fetchone()[0]
        conn.close()
        assert got == expected

    def test_nothing_is_recorded_after_the_end(self, client):
        state, _ = self._solo_at_last_step(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        before = len(study_db.export_session(state["study_session_id"])["events"])

        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        client.post(
            f"/study/event?s={state['participant_key']}", json={"event_type": "keyboard"}
        )
        assert len(study_db.export_session(state["study_session_id"])["events"]) == before

    def test_the_participant_page_is_told_the_session_is_over(self, client):
        state, _ = self._solo_at_last_step(client)
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        payload = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["active"] is False
        assert payload["status"] == "completed"

    def test_a_no_op_advance_is_not_recorded(self, client):
        """Next on the last step, or Previous on the first, moves nothing. An
        advance from a step to itself in the log is a move that never happened,
        and "how many steps did this session take" would count it."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        before = len(study_db.export_session(session_id)["events"])

        client.post(
            "/study/step/advance",
            json={"direction": "previous", "study_session_id": session_id},
            headers=_auth(),
        )
        assert len(study_db.export_session(session_id)["events"]) == before

    def test_two_machine_sessions_still_end_from_the_panel(self, client):
        """Only the single-device case ends itself. With two machines the
        experimenter closes the session, and a ready press must not do it."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        steps = client.get(
            f"/study/state?study_session_id={session_id}", headers=_auth()
        ).get_json()["step_count"]
        client.post(
            "/study/step/advance",
            json={"step_index": steps - 1, "study_session_id": session_id},
            headers=_auth(),
        )

        response = client.post(f"/study/step/ready?s={state['participant_key']}", json={})
        assert response.get_json()["finished"] is False
        assert study_db.get_study_session(session_id)["status"] == "active"


class TestTheParticipantPageCanTellTheStatesApart:
    """Three ways a page can have no session, which need three different things
    said to whoever is reading the screen."""

    def _solo(self, client):
        state = _start(client).get_json()["state"]
        client.post(
            "/study/session/mode",
            json={"mode": "solo", "study_session_id": state["study_session_id"]},
            headers=_auth(),
        )
        return state

    def test_no_code_and_a_rejected_code_are_distinguishable(self, client):
        """They used to be byte-identical, so a mistyped or wiped-out code looked
        exactly like never having joined: the page showed the code prompt with no
        hint that what was entered had been refused."""
        _start(client)
        nothing_entered = client.get("/study/state").get_json()
        rejected = client.get("/study/state?s=ZZZZ").get_json()

        assert nothing_entered["unknown_code"] is False
        assert rejected["unknown_code"] is True
        assert nothing_entered != rejected

    def test_a_finished_session_is_distinguishable_from_both(self, client):
        state = self._solo(client)
        session_id = state["study_session_id"]
        steps = client.get(
            f"/study/state?study_session_id={session_id}", headers=_auth()
        ).get_json()["step_count"]
        client.post(
            "/study/step/advance",
            json={"step_index": steps - 1, "study_session_id": session_id},
            headers=_auth(),
        )
        client.post(f"/study/step/ready?s={state['participant_key']}", json={})

        finished = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert finished["active"] is False
        assert finished["status"] == "completed"
        assert finished.get("unknown_code") is None, "a known session is not an unknown code"

    def test_the_page_is_told_how_the_session_was_run(self, client):
        """So it can word things for whoever is actually reading: on one machine
        there is no second person to refer them to."""
        solo = self._solo(client)
        assert client.get(f"/study/state?s={solo['participant_key']}").get_json()["mode"] == "solo"

        paired = _start(client).get_json()["state"]
        assert client.get(f"/study/state?s={paired['participant_key']}").get_json()["mode"] == "paired"

    def test_the_participant_code_is_available_for_the_end_message(self, client):
        """On one machine the experimenter reads this screen and wants to know
        which session was just recorded."""
        state = _start(client).get_json()["state"]
        payload = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert payload["participant_code"] == state["participant_code"]

    def test_the_code_does_not_bring_the_answer_key_with_it(self, client):
        """Adding a field to the participant payload is exactly where a leak
        would get in."""
        state = _start(client, participant_code="P01").get_json()["state"]
        _advance_to(client, "task1.b.virtual")
        serialised = json.dumps(client.get(f"/study/state?s={state['participant_key']}").get_json())
        for leak in ("pencil holder", "compartments", "Aspect ratio", "script"):
            assert leak.lower() not in serialised.lower()


class TestReconnectingToAFinishedSession:
    def test_the_stream_says_the_session_ended_not_that_the_code_is_wrong(self, client):
        """The page asks twice on load -- once directly, once by opening the
        event stream -- and both answers have to agree. The stream used to use
        the strict lookup, so its opening message overwrote "the session has
        ended" with "that code was not recognised" a moment after the page had
        got it right."""
        state = _start(client).get_json()["state"]
        session_id = state["study_session_id"]
        client.post(
            "/study/session/mode",
            json={"mode": "solo", "study_session_id": session_id},
            headers=_auth(),
        )
        client.post("/study/session/end", json={"study_session_id": session_id}, headers=_auth())

        direct = client.get(f"/study/state?s={state['participant_key']}").get_json()
        assert direct["status"] == "completed"

        # The first message on the stream is the state it opens with.
        stream = client.get(f"/study/stream?s={state['participant_key']}")
        first = next(iter(stream.response)).decode()
        opening = json.loads(first[len("data: "):])
        stream.close()

        assert opening["status"] == "completed", (
            f"the stream opened with {opening}, contradicting the direct answer"
        )
        assert opening.get("unknown_code") is None


# Bases as the viewer sends them: right, up and forward, always axis aligned
# because rotation happens in whole quarter turns.
_IDENTITY_BASIS = {"scheme": "basis-v1", "right": [1, 0, 0], "up": [0, 1, 0], "forward": [0, 0, 1]}
_QUARTER_TURN_ABOUT_Z = {
    "scheme": "basis-v1", "right": [0, -1, 0], "up": [1, 0, 0], "forward": [0, 0, 1],
}
_QUARTER_TURN_ABOUT_Y = {
    "scheme": "basis-v1", "right": [0, 0, 1], "up": [0, 1, 0], "forward": [-1, 0, 0],
}


def _render(session_id, **overrides):
    """Record one render against a session, the way /render does."""
    params = {
        "model": "lego_2x4", "view": "x-", "render_mode": "Cut", "layout_mode": "single",
        "depth": 50, "zoom": 0, "input_source": "keyboard", "cache_hit": False,
    }
    params.update(overrides)
    return study_db.record_render(session_id, **params)


def _end(client, session_id, status="completed"):
    return client.post(
        "/study/session/end",
        json={"study_session_id": session_id, "status": status},
        headers=_auth(),
    )


def _long_csv(client, query=""):
    """The export, as (response, parsed rows)."""
    response = client.get(f"/study/export/long.csv{query}", headers=_auth())
    text = response.get_data(as_text=True)
    return response, list(csv.DictReader(io.StringIO(text)))


class TestLongExportCoversCompletedSessionsOnly:
    """The constraint the endpoint exists to enforce. An active session is still
    being written to, so exporting it gives a different file every time it is
    asked for; an abandoned one stopped partway with nothing recording why."""

    def test_an_active_session_is_not_in_the_file(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id)
        _, rows = _long_csv(client)
        assert rows == [], "a session still running must not reach the analysis file"

    def test_an_abandoned_session_is_not_in_the_file(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id)
        _end(client, session_id, status="abandoned")
        _, rows = _long_csv(client)
        assert rows == []

    def test_a_completed_session_is(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id)
        _end(client, session_id)
        _, rows = _long_csv(client)
        assert rows, "a finished session is the whole point of the export"
        assert {row["participant_code"] for row in rows} == {"P01"}

    def test_the_three_are_told_apart_in_one_file(self, client):
        """The case that matters on the real server, where twelve of fourteen
        sessions were sitting in a state nobody meant them to be in."""
        finished = _start(client, participant_code="P01").get_json()["state"]
        abandoned = _start(client, participant_code="P02").get_json()["state"]
        running = _start(client, participant_code="P03").get_json()["state"]
        for state in (finished, abandoned, running):
            _render(state["study_session_id"])
        _end(client, finished["study_session_id"])
        _end(client, abandoned["study_session_id"], status="abandoned")

        _, rows = _long_csv(client)
        assert {row["participant_code"] for row in rows} == {"P01"}

    def test_asking_for_one_unfinished_session_by_id_is_refused_and_says_why(self, client):
        session_id = _start(client).get_json()["state"]["study_session_id"]
        _render(session_id)
        response = client.get(f"/study/export/long.csv?session={session_id}", headers=_auth())
        assert response.status_code == 409
        # Named status, so the experimenter can tell a typo from a session that
        # nobody finished.
        assert "active" in response.get_json()["message"]

    def test_asking_for_one_finished_session_by_id_returns_just_that_one(self, client):
        first = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        second = _start(client, participant_code="P02").get_json()["state"]["study_session_id"]
        for session_id in (first, second):
            _render(session_id)
            _end(client, session_id)

        _, rows = _long_csv(client, f"?session={first}")
        assert {row["participant_code"] for row in rows} == {"P01"}

    def test_an_unknown_session_is_a_clean_404(self, client):
        assert client.get("/study/export/long.csv?session=999", headers=_auth()).status_code == 404

    def test_a_session_that_is_not_a_number_is_a_400(self, client):
        assert client.get("/study/export/long.csv?session=P01", headers=_auth()).status_code == 400

    def test_the_export_is_a_plain_address_with_no_token(self, client):
        """Opened in a browser, not fetched with a header. The fixture turns the
        optional gate on, and this route is open regardless: a token to look up
        and paste is the friction the panel's own token was removed for."""
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id)
        _end(client, session_id)

        response = client.get("/study/export/long.csv")
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        assert {row["participant_code"] for row in rows} == {"P01"}

    def test_one_session_by_id_needs_no_token_either(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id)
        _end(client, session_id)
        assert client.get(f"/study/export/long.csv?session={session_id}").status_code == 200

    def test_the_rest_of_the_panel_is_still_gated(self, client):
        """Opening this one route must not have opened the others."""
        session_id = _start(client).get_json()["state"]["study_session_id"]
        assert client.get(f"/study/sessions/{session_id}/export").status_code == 403
        assert client.get("/study/sessions").status_code == 403


class TestLongExportShape:
    def test_it_is_a_csv_download_with_a_header_even_when_empty(self, client):
        """An empty file looks like a failed download. A header says the request
        worked and no session has been finished yet."""
        response, rows = _long_csv(client)
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert "attachment" in response.headers["Content-Disposition"]
        assert rows == []
        header = response.get_data(as_text=True).splitlines()[0]
        assert header.split(",") == list(study_db.LONG_EXPORT_COLUMNS)

    def test_one_row_per_interaction_not_one_per_session(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        _render(session_id)
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        _end(client, session_id)

        _, rows = _long_csv(client)
        commands = [row["event_type"] for row in rows]
        assert commands.count("keyboard") == 2
        assert "session_start" in commands and "session_end" in commands
        assert any(row["event_type"] == "render" for row in rows)

    def test_rows_are_in_the_order_they_happened(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        _render(session_id)
        _end(client, session_id)

        _, rows = _long_csv(client)
        sequences = [int(row["seq"]) for row in rows]
        assert sequences == sorted(sequences)

    def test_the_phase_comes_through_so_onboarding_can_be_separated(self, client):
        """The reason the column exists: a rotation during onboarding is someone
        being taught the controls, not someone working out a model."""
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, part_id="onboarding", step_id="onboarding.depth", step_index=4)
        _render(session_id, part_id="task1", step_id="task1.a.virtual", step_index=9)
        _end(client, session_id)

        _, rows = _long_csv(client)
        phases = [row["phase"] for row in rows if row["event_type"] == "render"]
        assert phases == ["onboarding", "task1"]

    def test_the_model_and_render_mode_are_on_the_row(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, model="cane_tip_fitted", render_mode="Outline")
        _end(client, session_id)

        _, rows = _long_csv(client)
        render_row = next(row for row in rows if row["event_type"] == "render")
        assert render_row["model"] == "cane_tip_fitted"
        assert render_row["render_mode"] == "Outline"


class TestLongExportViewerState:
    def test_an_event_carries_the_state_of_the_last_render(self, client):
        """Only renders store viewer state, so an event row would otherwise have
        a blank where the analysis needs to know what was under their hands."""
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, model="pencil_holder_2x3", render_mode="Filled")
        client.post(f"/study/event?s={_key(session_id)}", json={"event_type": "keyboard"})
        _end(client, session_id)

        _, rows = _long_csv(client)
        keypress = next(row for row in rows if row["event_type"] == "keyboard")
        assert keypress["model"] == "pencil_holder_2x3"
        assert keypress["render_mode"] == "Filled"

    def test_state_before_the_first_render_is_blank_rather_than_guessed(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _end(client, session_id)

        _, rows = _long_csv(client)
        start_row = next(row for row in rows if row["event_type"] == "session_start")
        assert start_row["model"] == ""
        assert start_row["render_mode"] == ""

    def test_state_does_not_leak_from_one_session_into_the_next(self, client):
        """Carrying forward is per session. One participant's model appearing on
        another participant's first rows would be invisible and wrong."""
        first = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(first, model="lego_2x4")
        _end(client, first)

        second = _start(client, participant_code="P02").get_json()["state"]["study_session_id"]
        client.post(f"/study/event?s={_key(second)}", json={"event_type": "keyboard"})
        _end(client, second)

        _, rows = _long_csv(client)
        second_rows = [row for row in rows if row["participant_code"] == "P02"]
        assert second_rows, "the second session should be in the file"
        assert all(row["model"] == "" for row in second_rows)


class TestLongExportOrientation:
    def test_an_unturned_object_reads_as_zero_on_every_axis(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, orientation=_IDENTITY_BASIS)
        _end(client, session_id)

        _, rows = _long_csv(client)
        row = next(r for r in rows if r["event_type"] == "render")
        assert (row["orientation_x"], row["orientation_y"], row["orientation_z"]) == (
            "0.0", "0.0", "0.0",
        )

    def test_a_quarter_turn_reads_as_ninety_on_the_axis_it_was_turned_about(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, orientation=_QUARTER_TURN_ABOUT_Z)
        _render(session_id, orientation=_QUARTER_TURN_ABOUT_Y)
        _end(client, session_id)

        _, rows = _long_csv(client)
        turned = [r for r in rows if r["event_type"] == "render"]
        about_z, about_y = turned[0], turned[1]
        assert (about_z["orientation_x"], about_z["orientation_y"], about_z["orientation_z"]) == (
            "0.0", "0.0", "90.0",
        )
        assert (about_y["orientation_x"], about_y["orientation_y"], about_y["orientation_z"]) == (
            "0.0", "90.0", "0.0",
        )

    def test_the_raw_basis_is_kept_so_nothing_depends_on_the_decomposition(self, client):
        """Pitched to straight up, a turn about X and a turn about Z are the same
        motion and the angles cannot tell them apart. The vectors still can."""
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, orientation=_QUARTER_TURN_ABOUT_Y)
        _end(client, session_id)

        _, rows = _long_csv(client)
        row = next(r for r in rows if r["event_type"] == "render")
        assert json.loads(row["orientation_basis"])["forward"] == [-1, 0, 0]

    def test_a_render_with_no_orientation_leaves_the_columns_blank(self, client):
        """Blank, not zero. Zero is a measurement, and there was not one."""
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, orientation=None)
        _end(client, session_id)

        _, rows = _long_csv(client)
        row = next(r for r in rows if r["event_type"] == "render")
        assert row["orientation_x"] == ""
        assert row["orientation_y"] == ""
        assert row["orientation_z"] == ""

    def test_a_half_written_basis_is_blank_rather_than_invented(self, client):
        session_id = _start(client, participant_code="P01").get_json()["state"]["study_session_id"]
        _render(session_id, orientation={"scheme": "basis-v1", "forward": [0, 0, 1]})
        _end(client, session_id)

        _, rows = _long_csv(client)
        row = next(r for r in rows if r["event_type"] == "render")
        assert row["orientation_x"] == ""
