"""Integration tests for session persistence and analytics endpoints.

Runs against a real SQLite database in a temp directory; no mocks.
The Flask test client sets cookies automatically across requests when
`use_cookies=True` (the default), mimicking a real browser session.
"""

from __future__ import annotations

import io
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
# GET /session/models
# ---------------------------------------------------------------------------

class TestSessionModels:
    def test_empty_without_uploads(self, client):
        client.get("/viewer")
        resp = client.get("/session/models")
        assert resp.status_code == 200
        assert resp.get_json()["models"] == []

    def test_no_cookie_returns_empty(self, client):
        resp = client.get("/session/models")
        assert resp.status_code == 200
        assert resp.get_json()["models"] == []


# ---------------------------------------------------------------------------
# POST /upload → uploaded_models DB registration (§3)
# ---------------------------------------------------------------------------

# Minimal valid ASCII STL (one degenerate triangle) for upload tests.
_MINIMAL_STL = (
    b"solid test\n"
    b"  facet normal 0 0 1\n"
    b"    outer loop\n"
    b"      vertex 0 0 0\n"
    b"      vertex 1 0 0\n"
    b"      vertex 0 1 0\n"
    b"    endloop\n"
    b"  endfacet\n"
    b"endsolid test\n"
)


class TestUploadRegistration:
    def _upload(self, client, filename="test.stl", content=_MINIMAL_STL):
        return client.post(
            "/upload",
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    def test_upload_returns_success(self, client):
        client.get("/viewer")
        resp = self._upload(client)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

    def test_upload_registers_model_in_db(self, client):
        client.get("/viewer")
        upload_resp = self._upload(client)
        filename = upload_resp.get_json()["filename"]

        sid = client.get_cookie("cad_session").value
        models = db_module.get_session_models(sid)
        assert len(models) == 1
        assert models[0]["filename"] == filename
        assert models[0]["original_name"] == "test.stl"
        assert models[0]["sha256"] is not None
        assert len(models[0]["sha256"]) == 64

    def test_session_models_endpoint_reflects_upload(self, client):
        client.get("/viewer")
        upload_resp = self._upload(client)
        filename = upload_resp.get_json()["filename"]

        resp = client.get("/session/models")
        assert resp.status_code == 200
        models = resp.get_json()["models"]
        assert len(models) == 1
        assert models[0]["filename"] == filename
        assert models[0]["available"] is True

    def test_upload_without_cookie_does_not_register(self, client):
        resp = self._upload(client)
        assert resp.status_code == 200
        # No cookie session was established, so no DB row should exist.
        import sqlite3
        import app.db as db
        conn = sqlite3.connect(str(db.DB_PATH))
        rows = conn.execute("SELECT COUNT(*) FROM uploaded_models").fetchone()[0]
        conn.close()
        assert rows == 0

    def test_delete_model_after_upload(self, client, tmp_path):
        client.get("/viewer")
        upload_resp = self._upload(client)
        filename = upload_resp.get_json()["filename"]

        del_resp = client.delete(f"/models/{filename}")
        assert del_resp.status_code == 200
        assert del_resp.get_json()["status"] == "success"

        sid = client.get_cookie("cad_session").value
        assert db_module.get_session_models(sid) == []

    def test_delete_nonexistent_returns_404(self, client):
        client.get("/viewer")
        resp = client.delete("/models/ghost.stl")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-session model aggregation by email identifier
# ---------------------------------------------------------------------------

class TestCrossSessionModels:
    """A user who provides the same email on a second device sees all their uploads."""

    def _upload(self, client, filename="test.stl"):
        return client.post(
            "/upload",
            data={"file": (io.BytesIO(_MINIMAL_STL), filename)},
            content_type="multipart/form-data",
        )

    def test_second_session_sees_first_session_models(self, tmp_db):
        flask_app.config["TESTING"] = True

        # Session A: upload a model then identify with email.
        with flask_app.test_client() as client_a:
            client_a.get("/viewer")
            upload_resp = self._upload(client_a, "model_a.stl")
            assert upload_resp.get_json()["status"] == "success"
            client_a.post("/session/identify", json={"email": "user@example.com", "consent": True})

        # Session B: fresh client (different cookie), same email.
        with flask_app.test_client() as client_b:
            client_b.get("/viewer")
            client_b.post("/session/identify", json={"email": "user@example.com", "consent": True})
            resp = client_b.get("/session/models")
            models = resp.get_json()["models"]
            filenames = [m["filename"] for m in models]
            assert any("model_a" in f for f in filenames), (
                f"Expected model_a in cross-session list, got {filenames}"
            )

    def test_second_session_can_delete_first_session_model(self, tmp_db):
        flask_app.config["TESTING"] = True
        filename_a = None

        with flask_app.test_client() as client_a:
            client_a.get("/viewer")
            upload_resp = self._upload(client_a, "shared.stl")
            filename_a = upload_resp.get_json()["filename"]
            client_a.post("/session/identify", json={"email": "user@example.com", "consent": True})

        with flask_app.test_client() as client_b:
            client_b.get("/viewer")
            client_b.post("/session/identify", json={"email": "user@example.com", "consent": True})
            del_resp = client_b.delete(f"/models/{filename_a}")
            assert del_resp.status_code == 200

            # Model should no longer appear for either session.
            models_b = client_b.get("/session/models").get_json()["models"]
            assert all(m["filename"] != filename_a for m in models_b)

    def test_anonymous_session_cannot_see_identified_session_models(self, tmp_db):
        flask_app.config["TESTING"] = True

        with flask_app.test_client() as client_a:
            client_a.get("/viewer")
            self._upload(client_a, "private.stl")
            client_a.post("/session/identify", json={"email": "user@example.com", "consent": True})

        # Session with no email: should see an empty list.
        with flask_app.test_client() as client_anon:
            client_anon.get("/viewer")
            models = client_anon.get("/session/models").get_json()["models"]
            assert models == []


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
