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
from collections import Counter
from pathlib import Path

import pytest

import app.db as db_module
from app import recording, usage_report, usage_routes
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
    usage_routes._failures.clear()
    yield
    tokens.forget_verified()
    usage_routes._failures.clear()


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
            event(DISTINCTIVE, "export", {"format": "pdf"}, "2026-01-01T09:07:00Z"),
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


def test_wrong_passwords_are_throttled_and_the_right_one_waits_too(
    client, usage_db, enabled, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(usage_routes, "_now", lambda: clock[0])
    for _ in range(usage_routes.FAILURE_LIMIT):
        assert client.get("/usage.json", headers=_basic("wrong")).status_code == 401

    verified = []
    monkeypatch.setattr(
        tokens, "verify", lambda *args: verified.append(args) or True
    )
    clock[0] += 0.6
    refused = client.get("/usage.json", headers=AUTH)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == str(usage_routes.FAILURE_WINDOW)
    assert refused.headers["Cache-Control"] == "no-store"
    assert verified == []

    clock[0] += usage_routes.FAILURE_WINDOW + 1
    assert client.get("/usage.json", headers=AUTH).status_code == 200


def test_wrong_passwords_sent_at_once_are_throttled_too(usage_db, enabled, monkeypatch):
    import threading

    release = threading.Event()
    entered = []

    def slow_wrong(*args):
        entered.append(args)
        release.wait(5)
        return False

    monkeypatch.setattr(tokens, "verify", slow_wrong)
    statuses = []

    def attempt():
        with flask_app.test_client() as c:
            statuses.append(c.get("/usage.json", headers=_basic("wrong")).status_code)

    threads = [
        threading.Thread(target=attempt)
        for _ in range(usage_routes.FAILURE_LIMIT + 1)
    ]
    for thread in threads:
        thread.start()
    for _ in range(500):
        if len(entered) + len(statuses) > usage_routes.FAILURE_LIMIT:
            break
        threading.Event().wait(0.01)
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(entered) == usage_routes.FAILURE_LIMIT
    assert sorted(statuses) == [401] * usage_routes.FAILURE_LIMIT + [429]


def test_a_request_without_a_password_is_not_a_failed_attempt(
    client, usage_db, enabled
):
    for _ in range(usage_routes.FAILURE_LIMIT + 5):
        assert client.get("/usage").status_code == 401
    assert client.get("/usage.json", headers=AUTH).status_code == 200


def test_every_usage_route_is_closed_to_demo_requests(client, usage_db, enabled):
    paths = [
        rule.rule
        for rule in flask_app.url_map.iter_rules()
        if rule.endpoint.startswith(usage_routes.usage_bp.name + ".")
    ]
    assert paths
    for path, send in itertools.product(paths, (client.get, client.post)):
        response = send(path, headers={**AUTH, recording.DEMO_HEADER: "1"})
        assert response.status_code == 404, path
        assert response.get_json()["message"] == "Not available in demo mode."


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
    assert _rows(_chart(interaction, "Event types"))["export"] == 3
    assert _rows(_chart(interaction, "Exports")) == {"pdf": 2}
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
# Building a database row by row
# ---------------------------------------------------------------------------

S1 = "11111111-1111-4111-8111-111111111111"
S2 = "22222222-2222-4222-8222-222222222222"
S3 = "33333333-3333-4333-8333-333333333333"
DEFAULT_AT = "2026-03-02T10:00:00Z"
_RENDER_COLUMNS = (
    "session_id",
    "view",
    "render_mode",
    "depth",
    "zoom",
    "layout_mode",
    "input_source",
    "created_at",
)


def _sessions(path, *ids):
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO sessions (id, consent_given, is_workshop, created_at) VALUES (?, 1, 0, ?)",
        [(session_id, DEFAULT_AT) for session_id in ids],
    )
    conn.commit()
    conn.close()


def _events(path, rows):
    """``rows`` are (session, event_type, data) or (session, event_type, data, created_at)."""
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO page_events (session_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?)",
        [
            (
                row[0],
                row[1],
                row[2] if isinstance(row[2], str) else json.dumps(row[2]),
                row[3] if len(row) > 3 else DEFAULT_AT,
            )
            for row in rows
        ],
    )
    conn.commit()
    conn.close()


def _renders(path, rows):
    """Each row names the render_stats columns it sets; the rest are an ordinary render."""
    plain = {
        "session_id": S1,
        "view": "y-",
        "render_mode": "Filled",
        "depth": 0.5,
        "zoom": 1.0,
        "layout_mode": "single",
        "input_source": "http_render",
        "created_at": DEFAULT_AT,
    }
    conn = sqlite3.connect(path)
    conn.executemany(
        f"INSERT INTO render_stats ({', '.join(_RENDER_COLUMNS)}) VALUES ({', '.join('?' * len(_RENDER_COLUMNS))})",
        [tuple({**plain, **row}[column] for column in _RENDER_COLUMNS) for row in rows],
    )
    conn.commit()
    conn.close()


def _build(path, **options):
    """The report as /usage.json serves it."""
    report = usage_report.build_report(path, **options)
    return json.loads(json.dumps(usage_report.report_to_json(report)))


def _labels(chart):
    return [row[0] for row in chart["rows"]]


# ---------------------------------------------------------------------------
# What the browser can make the report say
# ---------------------------------------------------------------------------


def test_a_free_form_value_is_shown_only_once_two_sessions_sent_it(usage_db):
    _sessions(usage_db, S1, S2)
    too_long = "x" * 61
    _events(
        usage_db,
        [
            (S1, "export", {"format": "stl"}),
            (S2, "export", {"format": "stl"}),
            (S1, "export", {"format": "Jane Doe thesis"}),
            (S1, "export", {"format": "Jane Doe thesis"}),
            (S1, "export", {"format": too_long}),
            (S2, "export", {"format": too_long}),
            (S1, "export", {"format": "bell\x07"}),
            (S2, "export", {"format": "bell\x07"}),
            (S1, "export", {"format": 7}),
            (S2, "export", {"format": 7}),
            (S1, "device_connect", {"device_type": "dotpad", "status": "connected"}),
            (S2, "device_connect", {"device_type": "dotpad", "status": "Jane's pad"}),
            (
                S1,
                "section_dwell",
                {"section_label": "Jane's notes", "duration_ms": 1000},
            ),
            (S1, "section_dwell", {"section_label": "Render", "duration_ms": 1000}),
            (S2, "section_dwell", {"section_label": "Render", "duration_ms": 1000}),
        ],
    )

    document = _build(usage_db)

    interaction = _section(document, "Interaction")
    assert _rows(_chart(interaction, "Exports")) == {"stl": 2, "Other": 8}
    devices = _chart(interaction, "Device connections")
    assert devices["columns"] == ["Device", "Other"]
    assert devices["rows"] == [["dotpad", 2]]
    assert _labels(_chart(interaction, "Time in each section")) == ["Render", "Other"]
    assert "Jane" not in json.dumps(document)


def test_a_value_typed_into_a_real_request_never_reaches_the_report(
    client, usage_db, enabled
):
    assert (
        client.post(
            "/session/identify", json={"email": None, "consent": True}
        ).status_code
        == 200
    )
    typed = "Jane Doe thesis draft"
    for body in (
        {"event_type": "export", "event_data": {"format": typed}},
        {
            "event_type": "device_connect",
            "event_data": {"device_type": typed, "status": typed},
        },
        {
            "event_type": "section_dwell",
            "event_data": {"section_label": typed, "duration_ms": 1200},
        },
        {"event_type": "keyboard_shortcut", "event_data": {"key": typed}},
        {"event_type": "model_select", "event_data": {"model_name": typed}},
    ):
        assert client.post("/events/track", json=body).status_code == 200
    conn = sqlite3.connect(usage_db)
    assert conn.execute("SELECT COUNT(*) FROM page_events").fetchone()[0] == 5
    conn.close()

    for path in ("/usage", "/usage.json"):
        body = client.get(path, headers=AUTH).get_data(as_text=True)
        assert "Jane" not in body, path
    interaction = _section(
        client.get("/usage.json", headers=AUTH).get_json(), "Interaction"
    )
    assert _rows(_chart(interaction, "Exports")) == {"Other": 1}
    assert _rows(_chart(interaction, "Keyboard shortcuts")) == {"Other": 1}
    assert _rows(_chart(interaction, "Models selected")) == {"Uploaded model": 1}


def test_a_key_the_viewer_does_not_track_reads_other(usage_db):
    _sessions(usage_db, S1, S2)
    _events(
        usage_db,
        [
            (S1, "keyboard_shortcut", {"key": "q"}),
            (S2, "keyboard_shortcut", {"key": "q"}),
            (S1, "keyboard_shortcut", {"key": "PageUp", "ctrl": True}),
        ],
    )

    interaction = _section(_build(usage_db), "Interaction")

    assert _rows(_chart(interaction, "Keyboard shortcuts")) == {
        "Other": 2,
        "Ctrl+PageUp": 1,
    }


def test_rendering_choices_name_only_what_the_viewer_sends(usage_db):
    _sessions(usage_db, S1, S2)
    _renders(
        usage_db,
        [
            {"view": "z+", "render_mode": "Filled"},
            {"view": "y-", "render_mode": "filled", "session_id": S2},
            {"view": "front", "render_mode": "Shaded"},
            {
                "view": "Jane",
                "render_mode": "Jane",
                "layout_mode": "Jane",
                "input_source": "Jane",
                "session_id": S2,
            },
            {"view": None, "render_mode": " ", "layout_mode": "side-by-side"},
            {"view": " ", "render_mode": None},
        ],
    )

    choices = _section(_build(usage_db), "Rendering choices")

    assert _rows(_chart(choices, "View")) == {
        "Top (z+)": 1,
        "Front (y-)": 1,
        "Other": 2,
        "(not recorded)": 2,
    }
    assert _rows(_chart(choices, "Render mode")) == {
        "Filled": 3,
        "Other": 1,
        "(not recorded)": 2,
    }
    assert _rows(_chart(choices, "Layout")) == {
        "single": 4,
        "side-by-side": 1,
        "Other": 1,
    }
    assert _rows(_chart(choices, "Input source")) == {"http_render": 5, "Other": 1}


def test_event_types_the_report_does_not_read_are_counted_but_not_parsed(usage_db):
    _sessions(usage_db, S1)
    _events(
        usage_db,
        [
            (S1, "page_view", "{not json"),
            (S1, "export", "[" * 100_000 + "]" * 100_000),
        ],
    )

    document = _build(usage_db)

    assert _tile(_section(document, "Overview"), "Interaction events") == 2
    interaction = _section(document, "Interaction")
    assert _tile(interaction, "Malformed event data") == 1
    assert _rows(_chart(interaction, "Event types")) == {"export": 1, "Other": 1}


def test_the_shortcut_keys_are_the_ones_the_viewer_tracks():
    page = (
        Path(__file__).resolve().parents[1] / "accessible-3d-viewer.html"
    ).read_text()
    listener = re.search(r"const shortcuts = new Set\(\[(.*?)\]\)", page, re.DOTALL)
    assert listener, "the viewer's keydown listener moved"
    assert set(re.findall(r"'([^']*)'", listener.group(1))) == set(
        usage_report.SHORTCUT_KEYS
    )


def test_the_event_types_are_the_ones_the_server_accepts():
    from app import server

    assert frozenset(usage_report.EVENT_TYPES) == server._ALLOWED_EVENT_TYPES


def _renderer_mapping(method, name):
    import ast

    source = (
        Path(__file__).resolve().parents[1] / "app" / "cad_comparison_lib.py"
    ).read_text()
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == method
    )
    assignment = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign) and node.targets[0].id == name
    )
    return ast.literal_eval(assignment.value)


def test_the_views_and_render_modes_are_the_ones_the_renderer_reads():
    assert usage_report.RENDERER_VIEWS == _renderer_mapping(
        "_map_view_name", "view_mapping"
    )
    assert usage_report.RENDERER_MODES == _renderer_mapping(
        "_map_render_mode", "mode_mapping"
    )


def test_the_layouts_are_the_ones_the_viewer_sends():
    script = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "viewer.js"
    ).read_text()
    modes = re.search(r"const representationModes = \[(.*?)\];", script, re.DOTALL)
    assert modes, "viewer.js no longer lists its view modes there"
    assert tuple(re.findall(r"wire: '([^']+)'", modes.group(1))) == usage_report.LAYOUTS


# ---------------------------------------------------------------------------
# Tables stay small, whatever is sent
# ---------------------------------------------------------------------------


def test_a_flood_of_distinct_labels_is_cut_to_the_largest_and_other(usage_db):
    _sessions(usage_db, S1, S2)
    rows = []
    for i in range(40):
        for session_id in (S1, S2):
            rows.append(
                (
                    session_id,
                    "device_connect",
                    {"device_type": f"device {i}", "status": f"status {i}"},
                )
            )
            rows.append(
                (
                    session_id,
                    "section_dwell",
                    {"section_label": f"section {i}", "duration_ms": 1000 + i},
                )
            )
    _events(usage_db, rows)

    report = usage_report.build_report(usage_db)

    interaction = _section(usage_report.report_to_json(report), "Interaction")
    devices = _chart(interaction, "Device connections")
    assert len(devices["rows"]) == 11 and devices["rows"][-1][0] == "Other"
    assert len(devices["columns"]) == 8 and devices["columns"][-1] == "Other"
    assert sum(sum(row[1:]) for row in devices["rows"]) == 80
    dwell = _chart(interaction, "Time in each section")
    assert len(dwell["rows"]) == 16 and dwell["rows"][-1][0] == "Other"
    assert sum(row[2] for row in dwell["rows"]) == 80
    assert len(usage_report.render_html(report)) < 100_000


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def test_a_histogram_counts_renders_by_range(usage_db):
    _sessions(usage_db, S1)
    _renders(usage_db, [{"depth": float(depth)} for depth in range(0, 101, 10)])

    depth = _chart(_section(_build(usage_db), "Rendering choices"), "Depth")

    assert depth["rows"] == [
        [f"{low} to {low + 10}", 2 if low == 90 else 1] for low in range(0, 100, 10)
    ]


def test_histogram_edges_are_told_apart_when_three_digits_cannot(usage_db):
    _sessions(usage_db, S1)
    _renders(usage_db, [{"depth": 1000.0}, {"depth": 1000.5}])

    depth = _chart(_section(_build(usage_db), "Rendering choices"), "Depth")

    edges = [label.split(" to ") for label in _labels(depth)]
    assert len(edges) == 10 and all(low != high for low, high in edges)
    assert len({label for label in _labels(depth)}) == 10


def test_values_that_are_not_finite_numbers_are_left_out(usage_db):
    _sessions(usage_db, S1)
    _renders(
        usage_db,
        [
            {"zoom": float("inf")},
            {"zoom": 1.0},
            {"zoom": 2.0},
            {"depth": -1e308},
            {"depth": 1e308},
        ],
    )

    report = usage_report.build_report(usage_db)

    choices = _section(usage_report.report_to_json(report), "Rendering choices")
    zoom = _chart(choices, "Zoom")
    assert sum(_rows(zoom).values()) == 4
    assert "1 render" in zoom["caption"] and "left out" in zoom["caption"]
    depth = _chart(choices, "Depth")
    assert len(depth["rows"]) == 10
    assert depth["rows"][0][1] == 1 and depth["rows"][-1][1] == 1
    assert sum(_rows(depth).values()) == 5
    assert "<svg" in usage_report.render_html(report)


def test_values_that_read_the_same_are_counted_together(usage_db):
    _sessions(usage_db, S1, S2)
    _renders(
        usage_db,
        [{"input_source": None}, {"input_source": " "}, {"input_source": "  "}],
    )

    choices = _section(_build(usage_db), "Rendering choices")

    assert _rows(_chart(choices, "Input source")) == {"(not recorded)": 3}


def test_buckets_take_out_of_range_values_and_leave_out_non_finite_ones():
    rows = usage_report._bucket_rows(
        [-1.0, 0.5, float("inf"), float("nan")], usage_report._MINUTE_BUCKETS
    )

    assert dict(rows) == {
        "under 1 min": 1,
        "1-5 min": 0,
        "5-15 min": 0,
        "15-60 min": 0,
        "60+ min": 1,
    }


def test_a_value_in_a_table_cell_is_escaped():
    chart = usage_report.Chart("T", "Caption.", "table", (("row", "<b>bold</b>"),), "V")
    report = usage_report.Report(
        "now", False, (usage_report.Section("S", "Summary.", (chart,)),)
    )

    html = usage_report.render_html(report)

    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html


def test_axis_lines_reach_three_to_one_against_the_background():
    def luminance(hex_colour):
        if len(hex_colour) == 4:
            hex_colour = "#" + "".join(digit * 2 for digit in hex_colour[1:])
        channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = usage_report._CSS.split("@media")
    for block in (light, dark):
        colours = dict(re.findall(r"--(bg|line):(#[0-9a-f]{6}|#[0-9a-f]{3})\b", block))
        lighter, darker = sorted(
            (luminance(colours["bg"]), luminance(colours["line"])), reverse=True
        )
        assert (lighter + 0.05) / (darker + 0.05) >= 3, colours


# ---------------------------------------------------------------------------
# Sections the seed above does not reach
# ---------------------------------------------------------------------------


def test_a_span_over_ninety_days_is_shown_by_week(usage_db):
    _sessions(usage_db, S1)
    _renders(
        usage_db,
        [
            {"created_at": "2026-01-05T10:00:00Z"},
            {"created_at": "2026-05-01T10:00:00Z"},
        ],
    )

    section = _section(_build(usage_db), "Activity over time")

    chart = section["charts"][0]
    assert chart["title"] == "Active sessions per week"
    assert chart["rows"][0][0] == "2026-W02" and chart["rows"][-1][0] == "2026-W18"
    assert len(chart["rows"]) == 17
    assert "ISO week" in section["summary"]


def test_section_dwell_noise_is_left_out(usage_db):
    _sessions(usage_db, S1, S2)
    _events(
        usage_db,
        [
            (S1, "section_dwell", {"section_label": "Render", "duration_ms": -5}),
            (
                S1,
                "section_dwell",
                {"section_label": "Render", "duration_ms": 7 * 3600 * 1000},
            ),
            (S1, "section_dwell", {"section_label": "Render", "duration_ms": "1500"}),
            (S1, "section_dwell", {"section_label": "Render", "duration_ms": True}),
            (S1, "section_dwell", {"section_label": "Render", "duration_ms": 1500}),
            (S2, "section_dwell", {"section_label": "Render", "duration_ms": 4500}),
        ],
    )

    interaction = _section(_build(usage_db), "Interaction")

    assert _chart(interaction, "Time in each section")["rows"] == [
        ["Render", 0.1, 2, 3.0]
    ]
    assert "4 section dwell times" in interaction["summary"]


def test_activity_is_charted_by_hour_and_weekday(usage_db):
    _sessions(usage_db, S1)
    _renders(
        usage_db,
        [
            {"created_at": "2026-03-02T10:15:00Z"},
            {"created_at": "2026-03-02T10:45:00Z"},
            {"created_at": "2026-03-04T23:00:00Z"},
        ],
    )

    section = _section(_build(usage_db), "When people use it")

    hours = _chart(section, "Activity by hour of day (UTC)")
    assert len(hours["rows"]) == 24
    assert {k: v for k, v in _rows(hours).items() if v} == {"10:00": 2, "23:00": 1}
    weekdays = _chart(section, "Activity by weekday (UTC)")
    assert _labels(weekdays)[0] == "Monday" and len(weekdays["rows"]) == 7
    assert {k: v for k, v in _rows(weekdays).items() if v} == {
        "Monday": 2,
        "Wednesday": 1,
    }


def test_the_session_span_is_named_for_what_it_measures(usage_db):
    _seed(usage_db)

    engagement = _section(_build(usage_db), "Engagement")

    assert _tile(engagement, "Median minutes from first to last activity") is not None


# ---------------------------------------------------------------------------
# The tutorial
# ---------------------------------------------------------------------------


def test_a_tutorial_started_from_the_menu_counts_as_entered(usage_db):
    _sessions(usage_db, S1, S2)
    _events(
        usage_db,
        [
            (S1, "tutorial", {"action": "lesson_completed", "lesson": "connect"}),
            (S1, "tutorial", {"action": "completed", "lesson": None}),
            (S2, "tutorial", {"action": "loaded", "lesson": None}),
        ],
    )

    funnel = _section(_build(usage_db), "Tutorial funnel")

    assert _chart(funnel, "Tutorial progress")["rows"] == [
        ["Entered the tutorial", 2],
        ["Completed a lesson", 1],
        ["Completed the tutorial", 1],
    ]
    assert _tile(funnel, "Completion rate") == "50%"


def test_lessons_are_listed_in_the_tutorials_order(usage_db):
    from app import tutorial_lessons

    order = [lesson["id"] for lesson in tutorial_lessons.LESSONS]
    _sessions(usage_db, S1, S2, S3)
    _events(
        usage_db,
        [
            (S1, "tutorial", {"action": "lesson_completed", "lesson": order[4]}),
            (S2, "tutorial", {"action": "lesson_skipped", "lesson": order[1]}),
            (S3, "tutorial", {"action": "lesson_completed", "lesson": "not_a_lesson"}),
            (S1, "tutorial", {"action": "exited", "lesson": order[0]}),
            (S2, "tutorial", {"action": "lesson_completed", "lesson": order[1]}),
        ],
    )

    funnel = _section(_build(usage_db), "Tutorial funnel")

    assert _chart(funnel, "Lessons")["rows"] == [
        [order[0], 0, 0, 1],
        [order[1], 1, 1, 0],
        [order[4], 1, 0, 0],
        ["Other", 1, 0, 0],
    ]
    assert _rows(_chart(funnel, "Tutorial progress"))["Completed a lesson"] == 3


# ---------------------------------------------------------------------------
# A hash that cannot be checked
# ---------------------------------------------------------------------------

HUGE_N_HASH = "scrypt$18446744073709551616$1$1$YQ$" + base64.urlsafe_b64encode(
    b"k" * 32
).decode("ascii").rstrip("=")


def test_a_hash_whose_cost_cannot_be_computed_turns_the_report_off(
    client, usage_db, monkeypatch
):
    monkeypatch.setenv(ENV, HUGE_N_HASH)

    for path in ("/usage", "/usage.json"):
        assert client.get(path, headers=AUTH).status_code == 404, path


# ---------------------------------------------------------------------------
# Making the hash
# ---------------------------------------------------------------------------


def test_the_hash_command_needs_none_of_the_apps_dependencies():
    blocked = ("flask", "numpy", "trimesh", "shapely", "PIL", "matplotlib", "OCC")
    code = (
        "import getpass, runpy, sys\n"
        f"for name in {blocked!r}: sys.modules[name] = None\n"
        "getpass.getpass = lambda prompt='': ''\n"
        "sys.argv = ['usage_report', 'hash']\n"
        "runpy.run_module('app.usage_report', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert "nothing printed" in result.stderr and "Error" not in result.stderr


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


def test_an_existing_other_bucket_does_not_take_a_named_slot():
    counts = Counter({f"device{i}": 50 - i for i in range(10)})
    counts["Other"] = 100
    rows = usage_report._ranked(counts, limit=10)
    assert [row[0] for row in rows[:10]] == [f"device{i}" for i in range(10)]
    assert rows[-1] == ("Other", 100)


def test_a_database_sqlite_cannot_read_reports_that_instead_of_failing(tmp_path):
    unreadable = tmp_path / "usage.db"
    unreadable.write_bytes(b"not a database" * 100)

    overview = _section(_build(unreadable), "Overview")

    assert overview["summary"] == "The usage database could not be read (DatabaseError)."


def test_a_tutorial_event_that_cannot_be_read_does_not_count_as_entered(usage_db):
    _sessions(usage_db, S1, S2)
    _events(
        usage_db,
        [
            (S1, "tutorial", "{not json"),
            (S2, "tutorial", {"action": "completed", "lesson": None}),
        ],
    )

    funnel = _section(_build(usage_db), "Tutorial funnel")

    assert _chart(funnel, "Tutorial progress")["rows"][0] == [
        "Entered the tutorial",
        1,
    ]
    assert _tile(funnel, "Completion rate") == "100%"


@pytest.mark.parametrize(
    "low, high",
    [
        (-sys.float_info.max, sys.float_info.max),
        (sys.float_info.max * 0.9, sys.float_info.max),
        (sys.float_info.max * (1 - 2**-50), sys.float_info.max),
    ],
)
def test_histogram_edges_near_the_largest_double_stay_finite(usage_db, low, high):
    _sessions(usage_db, S1)
    _renders(usage_db, [{"depth": low}, {"depth": high}])

    depth = _chart(_section(_build(usage_db), "Rendering choices"), "Depth")

    assert sum(row[1] for row in depth["rows"]) == 2
    assert not any("inf" in label for label in _labels(depth))
