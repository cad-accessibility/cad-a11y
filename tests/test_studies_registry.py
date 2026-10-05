"""What a server serves for a study, decided by its status alone.

The comparison study ran at /study with its panel open to anyone who found the
address. These are the tests that it, and every study after it, is served
exactly as its definition says and no more.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import sqlite3
import zipfile

import pytest

from app.server import app as flask_app
from app.studies import registry, tokens
from app.studies.definition import Status, Storage
from app.studies.definitions import comparison_2026, example
from app.studies.store import StudyStore

TOKEN = "registry-test-token"
TOKEN_HASH = tokens.hash_token(TOKEN)
AUTH = {"X-Study-Token": TOKEN}


def _definition(tmp_path, slug, status, **changes):
    """The example study under another slug and status, storing in tmp_path."""
    storage = Storage(db_path=tmp_path / "db" / f"{slug}.db", log_dir=tmp_path / "logs" / slug)
    return dataclasses.replace(
        example.STUDY, slug=slug, status=status, token_hash=TOKEN_HASH,
        storage=lambda: storage, **changes,
    )


@pytest.fixture(autouse=True)
def restore_registry():
    """Every test here loads its own studies; put the real ones back after."""
    yield
    registry.load()


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# The retired study, and /study
# ---------------------------------------------------------------------------


RETIRED = "/studies/comparison-2026"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/study"),
        ("get", "/study/control"),
        ("get", "/study/state"),
        ("get", "/study/config"),
        ("get", "/study/export/long.csv"),
        ("get", "/study/sessions/1/export"),
        ("post", "/study/session/start"),
        ("post", "/study/event"),
        ("get", RETIRED),
        ("get", f"{RETIRED}/state"),
        ("post", f"{RETIRED}/event"),
        ("get", f"{RETIRED}/control"),
        ("get", f"{RETIRED}/control/config"),
        ("get", f"{RETIRED}/control/export/long.csv"),
        ("get", f"{RETIRED}/control/export/archive.zip"),
        ("post", f"{RETIRED}/control/sign-in"),
        ("post", f"{RETIRED}/control/session/start"),
    ],
)
def test_the_comparison_study_answers_nowhere(client, method, path):
    """Under its old address and its new one, with or without a token."""
    registry.load()
    call = getattr(client, method)
    response = call(path, json={"token": TOKEN}, headers=AUTH) if method == "post" else call(path, headers=AUTH)
    assert response.status_code == 404, f"{path} answered {response.status_code}"


def test_a_retired_study_looks_the_same_as_one_that_never_existed(client):
    registry.load()
    retired = client.get(f"{RETIRED}/control")
    unknown = client.get("/studies/no-such-study/control")
    assert retired.status_code == unknown.status_code == 404
    assert retired.get_data() == unknown.get_data()


def test_by_default_this_codebase_serves_nothing():
    """The example is a draft and the comparison study is retired."""
    registry.load(environ={})
    assert registry.runtimes() == []


# ---------------------------------------------------------------------------
# Drafts and the environment
# ---------------------------------------------------------------------------


def test_a_draft_is_served_only_where_it_is_named(tmp_path):
    draft = _definition(tmp_path, "draft-study", Status.DRAFT)
    registry.load([draft], environ={})
    assert registry.runtime("draft-study") is None

    messages = registry.load([draft], environ={registry.OPEN_STUDIES_ENV: "draft-study"})
    served = registry.runtime("draft-study")
    assert served is not None and served.is_open
    assert any("a draft opened by" in message for message in messages)


@pytest.mark.parametrize("status", [Status.RETIRED, Status.CLOSED])
def test_the_environment_cannot_change_a_studys_status(tmp_path, status):
    """It opens drafts on a development machine. It does not reopen a closed
    study or bring a retired one back."""
    study = _definition(tmp_path, "settled-study", status)
    messages = registry.load([study], environ={registry.OPEN_STUDIES_ENV: "settled-study"})
    served = registry.runtime("settled-study")
    assert served is None or not served.is_open
    assert any("its status decides" in message for message in messages)


def test_naming_a_study_that_does_not_exist_is_reported():
    messages = registry.load([], environ={registry.OPEN_STUDIES_ENV: "typo"})
    assert any("typo, which is not a study" in message for message in messages)


# ---------------------------------------------------------------------------
# Refusals: never served open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token_hash,reason",
    [
        (None, "no token_hash"),
        ("", "no token_hash"),
        ("scrypt$not-a-hash", "not one python -m app.studies token made"),
        (example.STUDY.token_hash, "the example study's"),
        (tokens.hash_token(example.TOKEN), "the example study's"),
    ],
)
def test_an_open_study_without_a_token_of_its_own_is_refused(tmp_path, token_hash, reason):
    study = _definition(tmp_path, "unguarded", Status.OPEN)
    study = dataclasses.replace(study, token_hash=token_hash)
    messages = registry.load([study], environ={})
    assert registry.runtime("unguarded") is None
    assert any(reason in message for message in messages), messages


def test_an_invalid_definition_is_refused_not_raised(tmp_path):
    """One broken definition must not keep the viewer from starting."""
    broken = dataclasses.replace(_definition(tmp_path, "broken", Status.OPEN), steps=[])
    fine = _definition(tmp_path, "fine", Status.OPEN)
    messages = registry.load([broken, fine], environ={})
    assert registry.runtime("broken") is None
    assert registry.runtime("fine") is not None
    assert any("broken" in message and "no steps" in message for message in messages)


def test_two_definitions_with_one_slug_are_both_refused(tmp_path):
    first = _definition(tmp_path, "twice", Status.OPEN)
    second = dataclasses.replace(first, title="Another")
    messages = registry.load([first, second], environ={})
    assert registry.runtime("twice") is None
    assert any("used twice" in message for message in messages)


# ---------------------------------------------------------------------------
# Open and closed
# ---------------------------------------------------------------------------


def test_an_open_study_is_served_and_its_database_created(tmp_path, client):
    study = _definition(tmp_path, "running", Status.OPEN)
    registry.load([study], environ={})
    assert (tmp_path / "db" / "running.db").is_file()
    assert client.get("/studies/running").status_code == 200
    assert client.get("/studies/running/control/config", headers=AUTH).get_json()["status"] == "open"


def _seed(tmp_path, slug):
    """One completed session in a study's database, as an open study leaves it."""
    store = StudyStore(tmp_path / "db" / f"{slug}.db", tmp_path / "logs" / slug)
    store.init_db()
    _, session = store.enroll(
        code=None, session_number=1, choose_task_order=lambda _: ["chair", "washer", "cube"],
        protocol_version="1",
    )
    store.record_event(int(session["id"]), "keyboard", event_data={"key": "r"})
    store.end_session(int(session["id"]), status="completed", source="experimenter")
    store._local.__dict__.clear()


class TestAClosedStudy:
    """Finished collecting: its data can still be downloaded, since nobody has
    shell access to the servers, and nothing else is served."""

    @pytest.fixture()
    def closed(self, tmp_path):
        _seed(tmp_path, "finished")
        registry.load([_definition(tmp_path, "finished", Status.CLOSED)], environ={})
        return "/studies/finished"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", ""),
            ("get", "/state"),
            ("get", "/stream"),
            ("post", "/event"),
            ("post", "/step/ready"),
            ("get", "/control/sets"),
            ("post", "/control/session/start"),
            ("post", "/control/step/advance"),
            ("post", "/control/session/end"),
        ],
    )
    def test_no_session_can_run(self, client, closed, method, path):
        call = getattr(client, method)
        response = call(closed + path, json={}, headers=AUTH) if method == "post" else call(closed + path, headers=AUTH)
        assert response.status_code == 404

    def test_its_panel_says_it_is_closed(self, client, closed):
        assert client.get(f"{closed}/control").status_code == 200
        assert client.get(f"{closed}/control/config", headers=AUTH).get_json()["status"] == "closed"

    def test_its_exports_still_need_the_token(self, client, closed):
        assert client.get(f"{closed}/control/export/archive.zip").status_code == 401

    def test_its_data_can_be_downloaded(self, client, closed):
        rows = client.get(f"{closed}/control/export/long.csv", headers=AUTH).get_data(as_text=True)
        assert "keyboard" in rows

        response = client.get(f"{closed}/control/export/archive.zip", headers=AUTH)
        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as bundle:
            assert "study.db" in bundle.namelist()
            assert any(name.startswith("logs/") for name in bundle.namelist())

    def test_its_database_is_not_written_to(self, tmp_path, client, closed):
        db_path = tmp_path / "db" / "finished.db"
        before = db_path.stat().st_mtime_ns
        client.get(f"{closed}/control/export/archive.zip", headers=AUTH)
        client.get(f"{closed}/control/export/long.csv", headers=AUTH)
        assert db_path.stat().st_mtime_ns == before


def _older_database(path):
    """A database as the comparison study's code left it on a server that never
    ran #185: no axis columns, and none of the columns added since."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE participants (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE,
            first_session_at DATETIME, created_at DATETIME);
        CREATE TABLE study_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL, participant_code TEXT NOT NULL,
            session_number INTEGER NOT NULL DEFAULT 1, participant_key TEXT,
            mode TEXT NOT NULL DEFAULT 'paired', task_order TEXT NOT NULL,
            protocol_version TEXT, step_index INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active', log_path TEXT, started_at DATETIME,
            step_started_at DATETIME, completed_at DATETIME);
        CREATE TABLE study_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_session_id INTEGER NOT NULL, participant_id INTEGER NOT NULL,
            participant_code TEXT NOT NULL, seq INTEGER NOT NULL, part_id TEXT, step_id TEXT,
            step_index INTEGER, event_type TEXT NOT NULL, source TEXT NOT NULL, client_id TEXT,
            event_data TEXT, client_time TEXT, created_at DATETIME, elapsed_ms INTEGER,
            step_elapsed_ms INTEGER);
        CREATE TABLE study_renders (id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_session_id INTEGER NOT NULL, participant_id INTEGER NOT NULL,
            participant_code TEXT NOT NULL, seq INTEGER NOT NULL, part_id TEXT, step_id TEXT,
            step_index INTEGER, model TEXT, view TEXT, render_mode TEXT, layout_mode TEXT,
            depth REAL, zoom REAL, input_source TEXT, cache_hit INTEGER, orientation TEXT,
            created_at DATETIME, elapsed_ms INTEGER, step_elapsed_ms INTEGER);
        INSERT INTO participants (id, code) VALUES (1, 'P01');
        INSERT INTO study_sessions (id, participant_id, participant_code, task_order, status,
            started_at, completed_at)
            VALUES (1, 1, 'P01', '["chair", "washer", "cube"]', 'completed',
                    '2026-08-10T10:00:00.000Z', '2026-08-10T11:00:00.000Z');
        INSERT INTO study_events (study_session_id, participant_id, participant_code, seq,
            event_type, source, event_data, created_at)
            VALUES (1, 1, 'P01', 1, 'keyboard', 'participant', '{"key": "i"}', '2026-08-10T10:01:00.000Z');
        INSERT INTO study_renders (study_session_id, participant_id, participant_code, seq,
            model, view, render_mode, depth, created_at)
            VALUES (1, 1, 'P01', 2, 'rocking_chair', 'y-', 'Cut', 50, '2026-08-10T10:01:00.100Z');
        INSERT INTO study_events (study_session_id, participant_id, participant_code, seq,
            event_type, source, event_data, created_at)
            VALUES (1, 1, 'P01', 3, 'session_end', 'experimenter', '{"status": "completed"}',
                    '2026-08-10T11:00:00.000Z');
        """
    )
    conn.commit()
    conn.close()


def test_a_closed_study_written_by_older_code_still_downloads_whole(tmp_path, client):
    """How the comparison study's data comes off production: closed for one
    deploy, over a database that predates the axis columns and is never
    migrated in place."""
    db_path = tmp_path / "db" / "older.db"
    _older_database(db_path)
    registry.load([_definition(tmp_path, "older", Status.CLOSED)], environ={})
    control = "/studies/older/control"

    rows = list(csv.DictReader(io.StringIO(
        client.get(f"{control}/export/long.csv", headers=AUTH).get_data(as_text=True)
    )))
    assert [row["event_type"] for row in rows] == ["keyboard", "render", "session_end"]
    assert rows[1]["model"] == "rocking_chair"
    assert rows[1]["axis_mode"] == "", "a column that did not exist yet reads blank"

    sessions = client.get(f"{control}/sessions", headers=AUTH).get_json()["sessions"]
    assert [session["participant_code"] for session in sessions] == ["P01"]
    assert client.get(f"{control}/export/checks.json", headers=AUTH).get_json()["problems"] == 1, (
        "the one session's log file was never written here, which the checks report"
    )
    assert client.get(f"{control}/export/archive.zip", headers=AUTH).status_code == 200

    conn = sqlite3.connect(str(db_path))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(study_renders)")}
    finally:
        conn.close()
    assert "axis_mode" not in columns, "the closed study's database was migrated in place"


def test_a_closed_study_with_no_data_here_says_so(tmp_path, client):
    """A study that ran on one server is closed on both. Where it never ran
    there is nothing to export, and no empty database is made to export."""
    registry.load([_definition(tmp_path, "elsewhere", Status.CLOSED)], environ={})
    response = client.get("/studies/elsewhere/control/export/archive.zip", headers=AUTH)
    assert response.status_code == 404
    assert "no data" in response.get_json()["message"]
    assert not (tmp_path / "db" / "elsewhere.db").exists()


# ---------------------------------------------------------------------------
# Two studies at once
# ---------------------------------------------------------------------------


def test_two_open_studies_keep_separate_sessions(tmp_path, client):
    first = _definition(tmp_path, "first-study", Status.OPEN)
    second = _definition(tmp_path, "second-study", Status.OPEN)
    registry.load([first, second], environ={})

    a = client.post("/studies/first-study/control/session/start", json={}, headers=AUTH).get_json()["state"]
    b = client.post("/studies/second-study/control/session/start", json={}, headers=AUTH).get_json()["state"]
    # Each study numbers its own participants and sessions.
    assert a["participant_code"] == b["participant_code"] == "P01"
    assert a["study_session_id"] == b["study_session_id"] == 1

    # A join code belongs to one study: the other does not know it. (Unless the
    # two four-character codes happened to come out the same.)
    assert client.get(f"/studies/first-study/state?s={a['participant_key']}").get_json()["active"] is True
    if a["participant_key"] != b["participant_key"]:
        other = client.get(f"/studies/second-study/state?s={a['participant_key']}").get_json()
        assert other["active"] is False and other["unknown_code"] is True


def test_comparison_definition_is_still_known_to_the_command_line():
    """Retired is not deleted: the export reads it by slug."""
    assert registry.find_definition("comparison-2026") is comparison_2026.STUDY
