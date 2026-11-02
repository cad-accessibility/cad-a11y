"""Behavior tests for the password-protected usage report at /usage."""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import app.db as db_module
from app import recording, usage_report
from app.server import app as flask_app
from app.studies import tokens

PASSWORD = "correct horse battery staple"
PASSWORD_HASH = tokens.hash_token(PASSWORD)
ENV = "CAD_A11Y_USAGE_PASSWORD_HASH"

DISTINCTIVE = "a1b2c3d4-distinctive-session-0001"
REGULAR = "e5f6a7b8-regular-session-0002"
WORKSHOP = "9f9f9f9f-workshop-session-0003"
IDLE = "0d0d0d0d-idle-session-0004"

PRIVATE_VALUES = (
    DISTINCTIVE,
    REGULAR,
    WORKSHOP,
    IDLE,
    "Alicia",
    "private-name",
    "private-deleted",
    "stored-model",
    "secret_upload",
    "not-for-report",
)


def _basic(password, username="anyone"):
    raw = f"{username}:{password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


AUTH = _basic(PASSWORD)


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    """The application's own schema in a temp file, with no rows yet."""
    path = tmp_path / "usage.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()
    db_module.init_db()
    # Dropping the app's connection closes it, so the seed below is the only
    # writer and the file's bytes settle once it commits.
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()
    yield path
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv(ENV, PASSWORD_HASH)
    tokens.forget_verified()
    yield
    tokens.forget_verified()


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _seed(path):
    conn = sqlite3.connect(path)
    conn.executemany(
        """INSERT INTO sessions
           (id, identifier, consent_given, is_workshop, created_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (DISTINCTIVE, None, 1, 0, "2026-01-01T08:00:00Z", "2026-01-02T10:00:00Z"),
            (REGULAR, None, 1, 0, "2026-01-02T09:00:00Z", "2026-01-02T11:00:00Z"),
            (WORKSHOP, "Alicia", 0, 1, "2026-01-02T09:30:00Z", "2026-01-02T12:00:00Z"),
            (IDLE, None, 0, 0, "2026-01-02T13:00:00Z", "2026-01-02T13:00:00Z"),
        ],
    )
    conn.executemany(
        """INSERT INTO render_stats
           (session_id, view, render_mode, depth, zoom, layout_mode, input_source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                DISTINCTIVE,
                "front",
                "Filled",
                0.2,
                1.0,
                "single",
                "keyboard",
                "2026-01-01T09:00:00Z",
            ),
            (
                DISTINCTIVE,
                "top",
                "Filled",
                0.4,
                1.5,
                "split",
                "keyboard",
                "2026-01-02T09:00:00Z",
            ),
            (
                REGULAR,
                "front",
                "Outline",
                0.8,
                2.0,
                "single",
                "slider",
                "2026-01-02T10:00:00Z",
            ),
            (
                WORKSHOP,
                "side",
                "Filled",
                0.6,
                1.0,
                "single",
                "keyboard",
                "2026-01-02T11:00:00Z",
            ),
        ],
    )

    def event(session, event_type, data, at):
        blob = data if isinstance(data, str) else json.dumps(data)
        return (session, event_type, blob, at)

    conn.executemany(
        "INSERT INTO page_events (session_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?)",
        [
            event(
                DISTINCTIVE,
                "section_dwell",
                {
                    "section_id": "render",
                    "section_label": "Render controls",
                    "duration_ms": 3000,
                },
                "2026-01-01T09:02:00Z",
            ),
            event(
                DISTINCTIVE,
                "keyboard_shortcut",
                {"key": "ArrowUp", "ctrl": False, "alt": False, "shift": True},
                "2026-01-01T09:03:00Z",
            ),
            event(
                WORKSHOP,
                "keyboard_shortcut",
                {"key": "ArrowUp", "ctrl": False, "alt": False, "shift": False},
                "2026-01-02T11:01:00Z",
            ),
            event(
                REGULAR,
                "device_connect",
                {"device_type": "dotpad", "status": "connected"},
                "2026-01-02T10:01:00Z",
            ),
            event(REGULAR, "export", {"format": "pdf"}, "2026-01-02T10:02:00Z"),
            event(
                REGULAR,
                "model_select",
                {"model_name": "secret_upload_ab12.stl"},
                "2026-01-02T10:03:00Z",
            ),
            event(
                REGULAR, "model_select", {"model_name": "mug"}, "2026-01-02T10:03:30Z"
            ),
            event(REGULAR, "export", "{bad json", "2026-01-02T10:04:00Z"),
            event(
                DISTINCTIVE,
                "tutorial",
                {"action": "loaded", "lesson": None},
                "2026-01-01T09:04:00Z",
            ),
            event(
                DISTINCTIVE,
                "tutorial",
                {"action": "lesson_completed", "lesson": "intro"},
                "2026-01-01T09:05:00Z",
            ),
            event(
                DISTINCTIVE,
                "tutorial",
                {"action": "completed", "lesson": None},
                "2026-01-01T09:06:00Z",
            ),
            event(
                WORKSHOP,
                "tutorial",
                {"action": "loaded", "lesson": None},
                "2026-01-02T11:02:00Z",
            ),
            event(
                WORKSHOP,
                "tutorial",
                {"action": "lesson_skipped", "lesson": "intro"},
                "2026-01-02T11:03:00Z",
            ),
            event(
                WORKSHOP,
                "tutorial",
                {"action": "completed", "lesson": None},
                "2026-01-02T11:04:00Z",
            ),
        ],
    )
    conn.executemany(
        """INSERT INTO uploaded_models
           (session_id, filename, original_name, file_size, sha256, uploaded_at, deleted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                DISTINCTIVE,
                "stored-model.step",
                "private-name.step",
                500_000,
                "not-for-report",
                "2026-01-02T09:30:00Z",
                None,
            ),
            (
                WORKSHOP,
                "stored-model-2.stl",
                "private-deleted.stl",
                2_000_000,
                "not-for-report-2",
                "2026-01-02T10:30:00Z",
                "2026-01-03T10:30:00Z",
            ),
        ],
    )
    conn.commit()
    # Folded into the file now: another test's thread may hold a connection
    # open, and then closing this one would leave the rows in the -wal file for
    # a later checkpoint to move, changing the file's bytes but not its content.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _files_digest(path):
    """The database and its write-ahead log, so a write lands in one of them."""
    digest = hashlib.sha256(path.read_bytes())
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        digest.update(wal.read_bytes())
    return digest.hexdigest()


def _section(document, title):
    return next(
        section for section in document["sections"] if section["title"] == title
    )


def _tile(section, label):
    return next(value for name, value in section["tiles"] if name == label)


def _chart(section, title):
    return next(chart for chart in section["charts"] if chart["title"] == title)


def _rows(chart):
    return {row[0]: row[1] for row in chart["rows"]}


# ---------------------------------------------------------------------------
# The password
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "configured", [None, "", "a-plaintext-password", "scrypt$1$2$3$4$5"]
)
def test_report_is_not_served_without_a_well_formed_hash(
    client, usage_db, monkeypatch, configured
):
    if configured is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, configured)

    for path in ("/usage", "/usage.json"):
        response = client.get(path, headers=_basic(configured or "x"))
        assert response.status_code == 404, path


@pytest.mark.parametrize(
    "headers",
    [
        {},
        _basic("wrong password"),
        {"Authorization": "Basic !!!"},
        {"Authorization": "Bearer x"},
        {
            "Authorization": "Basic "
            + base64.b64encode(b"\xff\xfe:\xff\xfe").decode("ascii")
        },
        {"Authorization": "Basic"},
    ],
    ids=["missing", "wrong", "bad-base64", "bearer", "not-utf8", "empty-basic"],
)
def test_bad_credentials_get_a_basic_challenge_and_no_report(
    client, usage_db, enabled, headers
):
    _seed(usage_db)
    for path in ("/usage", "/usage.json"):
        response = client.get(path, headers=headers)
        assert response.status_code == 401, path
        assert (
            response.headers["WWW-Authenticate"]
            == 'Basic realm="CAD A11y usage", charset="UTF-8"'
        )
        assert response.headers["Cache-Control"] == "no-store"
        body = response.get_data(as_text=True)
        assert "Render" not in body and "Sessions" not in body
        assert len(body) < 200


def test_the_username_is_ignored(client, usage_db, enabled):
    response = client.get("/usage.json", headers=_basic(PASSWORD, username=""))
    assert response.status_code == 200


def test_a_demo_station_does_not_register_the_report(tmp_path, data_env):
    """Decided at import, so checked by importing the way a station starts."""
    env = {
        "CAD_A11Y_DEMO": "1",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "MPLBACKEND": "Agg",
        **data_env,
    }
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.server import app; print([str(r) for r in app.url_map.iter_rules() if 'usage' in str(r)])",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[]"), result.stdout


def test_a_demo_request_is_refused(client, usage_db, enabled):
    _seed(usage_db)
    for path in ("/usage", "/usage.json"):
        response = client.get(path, headers={**AUTH, recording.DEMO_HEADER: "1"})
        assert response.status_code == 404, path
        assert "Render" not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_report_counts_usage(client, usage_db, enabled):
    _seed(usage_db)

    response = client.get("/usage.json", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Robots-Tag"] == "noindex"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    document = response.get_json()
    overview = _section(document, "Overview")
    assert _tile(overview, "Sessions") == 4
    assert _tile(overview, "Active sessions") == 3
    assert _tile(overview, "Renders") == 4
    assert _tile(overview, "Days with activity") == 2
    assert _rows(_chart(_section(document, "Rendering choices"), "Render mode")) == {
        "Filled": 3,
        "Outline": 1,
    }
    assert (
        _tile(
            _section(document, "Tutorial funnel"),
            "Sessions that completed the tutorial",
        )
        == 2
    )
    interaction = _section(document, "Interaction")
    assert _rows(_chart(interaction, "Keyboard shortcuts")) == {
        "Shift+ArrowUp": 1,
        "ArrowUp": 1,
    }
    assert _rows(_chart(interaction, "Models selected")) == {
        "Uploaded model": 1,
        "mug": 1,
    }
    assert _tile(interaction, "Malformed event data") == 1
    assert _rows(_chart(interaction, "Event types"))["export"] == 2
    assert _rows(_chart(interaction, "Exports")) == {"pdf": 1}
    engagement = _section(document, "Engagement")
    assert _tile(engagement, "Returning sessions") == 1


def test_excluding_workshops_drops_their_activity(client, usage_db, enabled):
    _seed(usage_db)

    document = client.get("/usage.json?workshop=exclude", headers=AUTH).get_json()

    overview = _section(document, "Overview")
    assert _tile(overview, "Sessions") == 3
    assert _tile(overview, "Renders") == 3
    assert _rows(_chart(_section(document, "Rendering choices"), "Render mode")) == {
        "Filled": 2,
        "Outline": 1,
    }
    assert (
        _tile(
            _section(document, "Tutorial funnel"),
            "Sessions that completed the tutorial",
        )
        == 1
    )
    assert _rows(_chart(_section(document, "Interaction"), "Keyboard shortcuts")) == {
        "Shift+ArrowUp": 1
    }
    html = client.get("/usage?workshop=exclude", headers=AUTH).get_data(as_text=True)
    assert 'href="/usage"' in html


def test_report_holds_no_identifying_values(client, usage_db, enabled):
    _seed(usage_db)

    for path in (
        "/usage",
        "/usage.json",
        "/usage?workshop=exclude",
        "/usage.json?workshop=exclude",
    ):
        body = client.get(path, headers=AUTH).get_data(as_text=True)
        leaked = [value for value in PRIVATE_VALUES if value in body]
        assert not leaked, f"{path} shows {leaked}"


def test_reading_the_report_leaves_the_database_untouched(client, usage_db, enabled):
    _seed(usage_db)
    before = _files_digest(usage_db)

    for path in ("/usage", "/usage.json", "/usage?workshop=exclude"):
        assert client.get(path, headers=AUTH).status_code == 200

    assert _files_digest(usage_db) == before


def test_every_chart_is_a_captioned_figure_with_a_table(client, usage_db, enabled):
    _seed(usage_db)

    html = client.get("/usage", headers=AUTH).get_data(as_text=True)

    assert '<html lang="en">' in html
    figures = re.findall(r"<figure\b.*?</figure>", html, flags=re.DOTALL)
    assert figures
    for figure in figures:
        assert re.search(r"<figcaption>[^<]+\S", figure), figure[:200]
        assert (
            "<table>" in figure
            and "<caption>" in figure
            and '<th scope="col">' in figure
        )
        for svg in re.findall(r"<svg\b[^>]*>", figure):
            assert 'aria-hidden="true"' in svg
    levels = [int(level) for level in re.findall(r"<h([1-6])\b", html)]
    assert levels[0] == 1
    assert all(later - earlier <= 1 for earlier, later in itertools.pairwise(levels))
    assert "<script" not in html and "http://" not in html and "https://" not in html


def test_an_empty_database_says_nothing_was_recorded(client, usage_db, enabled):
    response = client.get("/usage", headers=AUTH)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No renders recorded." in html
    assert "No interaction events recorded." in html
    assert "<figure" not in html


def test_a_missing_database_says_no_usage_was_recorded(
    client, tmp_path, monkeypatch, enabled
):
    missing = tmp_path / "nowhere" / "usage.db"
    monkeypatch.setattr(db_module, "DB_PATH", missing)

    html = client.get("/usage", headers=AUTH)
    document = client.get("/usage.json", headers=AUTH)

    assert html.status_code == 200 and document.status_code == 200
    assert "No usage has been recorded" in html.get_data(as_text=True)
    assert not missing.parent.exists()


def test_an_older_database_without_later_tables_still_reports(
    client, tmp_path, monkeypatch, enabled
):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, consent_given INTEGER, created_at DATETIME)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('old-session', 1, '2025-06-01T10:00:00Z')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_module, "DB_PATH", old)

    response = client.get("/usage.json?workshop=exclude", headers=AUTH)

    assert response.status_code == 200
    assert _tile(_section(response.get_json(), "Overview"), "Sessions") == 1
    assert client.get("/usage", headers=AUTH).status_code == 200


def test_the_html_is_built_from_the_same_report_as_the_json(usage_db):
    _seed(usage_db)

    report = usage_report.build_report(usage_db, exclude_workshop=True)

    document = usage_report.report_to_json(report)
    html = usage_report.render_html(report)
    assert _tile(_section(document, "Overview"), "Renders") == 3
    assert "Workshop sessions are excluded" in html


# ---------------------------------------------------------------------------
# Making the hash
# ---------------------------------------------------------------------------


def test_hash_command_prints_a_hash_of_the_typed_password(monkeypatch, capsys):
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(
        usage_report.getpass, "getpass", lambda prompt="": next(answers)
    )

    assert usage_report.main(["hash"]) == 0

    printed = capsys.readouterr().out.strip()
    assert tokens.is_well_formed(printed)
    assert tokens.verify(PASSWORD, printed)
    assert PASSWORD not in printed


def test_hash_command_refuses_a_mismatch_or_an_empty_password(monkeypatch, capsys):
    for typed in ([PASSWORD, "something else"], ["", ""]):
        answers = iter(typed)
        monkeypatch.setattr(
            usage_report.getpass,
            "getpass",
            lambda prompt="", typed=answers: next(typed),
        )

        assert usage_report.main(["hash"]) != 0
        assert capsys.readouterr().out == ""


def test_start_message_says_whether_the_report_is_on(monkeypatch):
    from app import usage_routes

    monkeypatch.delenv(ENV, raising=False)
    assert "off" in usage_routes.start_message()
    monkeypatch.setenv(ENV, PASSWORD_HASH)
    assert (
        "/usage" in usage_routes.start_message()
        and "off" not in usage_routes.start_message()
    )
