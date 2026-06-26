"""Integration tests for session persistence and analytics endpoints.

Runs against a real SQLite database in a temp directory; no mocks.
The Flask test client sets cookies automatically across requests when
`use_cookies=True` (the default), mimicking a real browser session.
"""

from __future__ import annotations

import json
import time

import pytest

import app.db as db_module
from app.server import app as flask_app


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a temporary file and initialise the schema."""
    db_path = tmp_path / "test_usage.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    # Reset any cached thread-local connections so they reopen against the new path.
    db_module._local.__dict__.clear()
    db_module.init_db()
    yield db_path
    db_module._local.__dict__.clear()


@pytest.fixture()
def client(tmp_db):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------

class TestDbInit:
    def test_creates_tables(self, tmp_db):
        import sqlite3
        conn = sqlite3.connect(str(tmp_db))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "sessions" in tables
        assert "uploaded_models" in tables
        assert "render_stats" in tables
        assert "page_events" in tables
        conn.close()

    def test_wal_mode(self, tmp_db):
        import sqlite3
        conn = sqlite3.connect(str(tmp_db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()


class TestDbSession:
    def test_upsert_creates_row(self, tmp_db):
        sid = "aaaaaaaa-0000-0000-0000-000000000001"
        db_module.upsert_session(sid)
        row = db_module.get_session(sid)
        assert row is not None
        assert row["id"] == sid
        assert row["consent_given"] is None
        assert row["identifier"] is None

    def test_upsert_updates_last_seen(self, tmp_db):
        sid = "aaaaaaaa-0000-0000-0000-000000000002"
        db_module.upsert_session(sid)
        first = db_module.get_session(sid)["last_seen_at"]
        time.sleep(1.1)
        db_module.upsert_session(sid)
        second = db_module.get_session(sid)["last_seen_at"]
        assert second >= first

    def test_save_identifier(self, tmp_db):
        sid = "aaaaaaaa-0000-0000-0000-000000000003"
        db_module.upsert_session(sid)
        db_module.save_session_identifier(sid, "user@example.com", True)
        row = db_module.get_session(sid)
        assert row["identifier"] == "user@example.com"
        assert row["consent_given"] == 1

    def test_get_session_none_for_unknown(self, tmp_db):
        assert db_module.get_session("does-not-exist") is None


class TestDbAnalytics:
    def test_record_render(self, tmp_db):
        import sqlite3
        db_module.record_render(None, "x+", "Outline", 0.5, 1.0, "single", "http_render")
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT view, render_mode FROM render_stats").fetchone()
        assert row == ("x+", "Outline")
        conn.close()

    def test_record_page_event(self, tmp_db):
        import sqlite3
        db_module.record_page_event(None, "keyboard_shortcut", {"key": "ArrowUp"})
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT event_type, event_data FROM page_events").fetchone()
        assert row[0] == "keyboard_shortcut"
        assert json.loads(row[1])["key"] == "ArrowUp"
        conn.close()


# ---------------------------------------------------------------------------
# /viewer — cookie issuance
# ---------------------------------------------------------------------------

class TestViewerCookie:
    def test_sets_cookie_on_first_visit(self, client):
        resp = client.get("/viewer")
        assert resp.status_code == 200
        assert "cad_session" in resp.headers.get("Set-Cookie", "")

    def test_cookie_is_httponly(self, client):
        resp = client.get("/viewer")
        cookie_header = resp.headers.get("Set-Cookie", "")
        assert "HttpOnly" in cookie_header

    def test_returns_same_cookie_on_return(self, client):
        client.get("/viewer")
        sid1 = client.get_cookie("cad_session")
        client.get("/viewer")
        sid2 = client.get_cookie("cad_session")
        assert sid1 is not None
        assert sid1.value == sid2.value


# ---------------------------------------------------------------------------
# GET /session/me
# ---------------------------------------------------------------------------

class TestSessionMe:
    def test_no_cookie_returns_null(self, client):
        resp = client.get("/session/me")
        data = resp.get_json()
        assert data["session_id"] is None
        assert data["consent_given"] is None

    def test_new_session_consent_null(self, client):
        client.get("/viewer")   # establishes cookie
        resp = client.get("/session/me")
        data = resp.get_json()
        assert data["session_id"] is not None
        assert data["consent_given"] is None
        assert data["model_count"] == 0


# ---------------------------------------------------------------------------
# POST /session/identify
# ---------------------------------------------------------------------------

class TestSessionIdentify:
    def test_stores_email_and_consent(self, client):
        client.get("/viewer")
        resp = client.post(
            "/session/identify",
            json={"email": "hello@example.com", "consent": True},
        )
        assert resp.status_code == 200
        me = client.get("/session/me").get_json()
        assert me["identifier"] == "hello@example.com"
        assert me["consent_given"] == 1

    def test_decline_stores_no_email(self, client):
        client.get("/viewer")
        client.post("/session/identify", json={"email": None, "consent": False})
        me = client.get("/session/me").get_json()
        assert me["identifier"] is None
        assert me["consent_given"] == 0

    def test_rejects_invalid_email(self, client):
        client.get("/viewer")
        resp = client.post("/session/identify", json={"email": "notanemail", "consent": True})
        assert resp.status_code == 400

    def test_no_session_returns_400(self, client):
        resp = client.post("/session/identify", json={"email": "x@y.com", "consent": True})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /session/models (empty until §3 wires upload→DB)
# ---------------------------------------------------------------------------

class TestSessionModels:
    def test_empty_before_upload_wired(self, client):
        client.get("/viewer")
        resp = client.get("/session/models")
        assert resp.status_code == 200
        assert resp.get_json()["models"] == []

    def test_no_cookie_returns_empty(self, client):
        resp = client.get("/session/models")
        assert resp.status_code == 200
        assert resp.get_json()["models"] == []


# ---------------------------------------------------------------------------
# POST /events/track
# ---------------------------------------------------------------------------

class TestEventsTrack:
    def test_valid_event_accepted(self, client):
        client.get("/viewer")
        resp = client.post(
            "/events/track",
            json={"event_type": "keyboard_shortcut", "event_data": {"key": "ArrowUp"}},
        )
        assert resp.status_code == 200

    def test_unknown_event_type_rejected(self, client):
        client.get("/viewer")
        resp = client.post(
            "/events/track",
            json={"event_type": "unknown_type", "event_data": {}},
        )
        assert resp.status_code == 400

    def test_invalid_event_data_rejected(self, client):
        client.get("/viewer")
        resp = client.post(
            "/events/track",
            json={"event_type": "section_dwell", "event_data": "not-an-object"},
        )
        assert resp.status_code == 400

    def test_event_without_session_still_recorded(self, client, tmp_db):
        import sqlite3
        resp = client.post(
            "/events/track",
            json={"event_type": "keyboard_shortcut", "event_data": {"key": "1"}},
        )
        assert resp.status_code == 200
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT session_id FROM page_events").fetchone()
        assert row[0] is None  # no session cookie was set
        conn.close()
