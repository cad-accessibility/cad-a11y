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


def _identify(client, email=None, consent=True):
    """Establish a session the way the app now does, via the consent endpoint.

    /viewer no longer issues a cookie or creates a DB row; POST /session/identify is
    the first point at which a session row and cad_session cookie are created, so
    tests that need an active session call this instead of GET /viewer.
    """
    return client.post("/session/identify", json={"email": email, "consent": consent})


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
        sid = "test-render-sid"
        db_module.upsert_session(sid)
        db_module.save_session_identifier(sid, None, True)
        db_module.record_render(sid, "x+", "Outline", 0.5, 1.0, "single", "http_render")
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT view, render_mode FROM render_stats").fetchone()
        assert row == ("x+", "Outline")
        conn.close()

    def test_record_render_requires_consent(self, tmp_db):
        import sqlite3
        sid = "test-no-consent-sid"
        db_module.upsert_session(sid)
        db_module.record_render(sid, "x+", "Outline", 0.5, 1.0, "single", "http_render")
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT view FROM render_stats").fetchone()
        assert row is None
        conn.close()

    def test_record_page_event(self, tmp_db):
        import sqlite3
        sid = "test-event-sid"
        db_module.upsert_session(sid)
        db_module.save_session_identifier(sid, None, True)
        db_module.record_page_event(sid, "keyboard_shortcut", {"key": "ArrowUp"})
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT event_type, event_data FROM page_events").fetchone()
        assert row[0] == "keyboard_shortcut"
        assert json.loads(row[1])["key"] == "ArrowUp"
        conn.close()

    def test_record_page_event_requires_consent(self, tmp_db):
        import sqlite3
        db_module.record_page_event(None, "keyboard_shortcut", {"key": "ArrowUp"})
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT event_type FROM page_events").fetchone()
        assert row is None
        conn.close()


# ---------------------------------------------------------------------------
# /viewer — must NOT create a session before consent (GDPR)
# ---------------------------------------------------------------------------

class TestViewerNoSessionBeforeConsent:
    def test_viewer_serves_html_without_cookie(self, client):
        resp = client.get("/viewer")
        assert resp.status_code == 200
        assert "cad_session" not in resp.headers.get("Set-Cookie", "")

    def test_viewer_creates_no_session_row(self, client, tmp_db):
        import sqlite3
        client.get("/viewer")
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        assert rows == 0


# ---------------------------------------------------------------------------
# GET /session/me
# ---------------------------------------------------------------------------

class TestSessionMe:
    def test_no_cookie_returns_null(self, client):
        resp = client.get("/session/me")
        data = resp.get_json()
        assert data["session_id"] is None
        assert data["consent_given"] is None

    def test_reports_consent_after_identify(self, client):
        _identify(client, email=None, consent=True)
        resp = client.get("/session/me")
        data = resp.get_json()
        assert data["session_id"] is not None
        assert data["consent_given"] == 1
        assert data["model_count"] == 0


# ---------------------------------------------------------------------------
# POST /session/identify
# ---------------------------------------------------------------------------

class TestSessionIdentify:
    def test_creates_session_and_sets_cookie(self, client):
        # No prior cookie: identify is the first point a session is created.
        resp = client.post("/session/identify", json={"email": None, "consent": True})
        assert resp.status_code == 200
        cookie_header = resp.headers.get("Set-Cookie", "")
        assert "cad_session" in cookie_header
        assert "HttpOnly" in cookie_header

    def test_accept_creates_row_with_consent(self, client, tmp_db):
        import sqlite3
        client.post("/session/identify", json={"email": None, "consent": True})
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT consent_given FROM sessions").fetchall()
        conn.close()
        assert rows == [(1,)]

    def test_decline_creates_row_with_consent_zero(self, client, tmp_db):
        import sqlite3
        client.post("/session/identify", json={"email": None, "consent": False})
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT consent_given FROM sessions").fetchall()
        conn.close()
        assert rows == [(0,)]

    def test_stores_email_and_consent(self, client):
        resp = client.post(
            "/session/identify",
            json={"email": "hello@example.com", "consent": True},
        )
        assert resp.status_code == 200
        me = client.get("/session/me").get_json()
        assert me["identifier"] == "hello@example.com"
        assert me["consent_given"] == 1

    def test_decline_stores_no_email(self, client):
        client.post("/session/identify", json={"email": None, "consent": False})
        me = client.get("/session/me").get_json()
        assert me["identifier"] is None
        assert me["consent_given"] == 0

    def test_rejects_invalid_email(self, client):
        resp = client.post("/session/identify", json={"email": "notanemail", "consent": True})
        assert resp.status_code == 400

    def test_invalid_email_creates_no_orphan_session(self, client, tmp_db):
        import sqlite3
        resp = client.post("/session/identify", json={"email": "notanemail", "consent": True})
        assert resp.status_code == 400
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        assert rows == 0  # validation runs before the row is created

    def test_reuses_existing_cookie(self, client):
        client.post("/session/identify", json={"email": None, "consent": False})
        sid1 = client.get_cookie("cad_session")
        client.post("/session/identify", json={"email": "later@example.com", "consent": True})
        sid2 = client.get_cookie("cad_session")
        assert sid1 is not None
        assert sid1.value == sid2.value  # same session upgraded, not a new one


# ---------------------------------------------------------------------------
# GET /session/models
# ---------------------------------------------------------------------------

class TestSessionModels:
    def test_empty_without_uploads(self, client):
        _identify(client)
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
        _identify(client)
        resp = self._upload(client)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

    def test_upload_registers_model_in_db(self, client):
        _identify(client)
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
        _identify(client)
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
        _identify(client)
        upload_resp = self._upload(client)
        filename = upload_resp.get_json()["filename"]

        del_resp = client.delete(f"/models/{filename}")
        assert del_resp.status_code == 200
        assert del_resp.get_json()["status"] == "success"

        sid = client.get_cookie("cad_session").value
        assert db_module.get_session_models(sid) == []

    def test_delete_nonexistent_returns_404(self, client):
        _identify(client)
        resp = client.delete("/models/ghost.stl")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# External-tool /ingest and the /workshop word-code retrieval flow
# ---------------------------------------------------------------------------

class TestIngestWorkshop:
    def _ingest(self, client, filename="ingest.stl", content=_MINIMAL_STL, code=None):
        data = {"file": (io.BytesIO(content), filename)}
        if code is not None:
            data["code"] = code
        return client.post("/ingest", data=data, content_type="multipart/form-data")

    def test_ingest_without_code_is_anonymous(self, client):
        resp = self._ingest(client)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["code"] is None
        assert "/workshop?model=" in data["workshop_url"]
        assert data["workshop_entry_url"].endswith("/workshop")

    def test_ingest_with_code_registers_model(self, client):
        # The calling tool generates the code and sends it; we normalise + store it.
        data = self._ingest(client, code="Cedar Mango").get_json()
        assert data["code"] == "cedar-mango"
        assert data["code_display"] == "CEDAR MANGO"
        assert db_module.get_latest_model_for_identifier("cedar-mango") == data["filename"]

    def test_ingest_raw_body_with_code(self, client):
        resp = client.post(
            "/ingest?filename=raw.stl&code=blue-otter",
            data=_MINIMAL_STL,
            content_type="application/octet-stream",
        )
        assert resp.status_code == 200
        assert resp.get_json()["code"] == "blue-otter"

    def test_ingest_rejects_bad_extension(self, client):
        assert self._ingest(client, filename="notes.png").status_code == 400

    def test_ingest_model_is_discoverable(self, client):
        data = self._ingest(client).get_json()
        gd = client.get("/get_data").get_json()
        assert data["model_stem"] in gd["model_list"]

    def test_workshop_code_redirects_case_insensitive(self, client):
        data = self._ingest(client, code="cedar mango").get_json()
        resp = client.get("/workshop?code=CEDAR%20MANGO")
        assert resp.status_code == 302
        assert f"model={data['model_stem']}" in resp.headers["Location"]

    def test_workshop_unknown_code_shows_entry_page(self, client):
        resp = client.get("/workshop?code=no-such-code")
        assert resp.status_code == 200
        assert b"Enter your code" in resp.data

    def test_workshop_blank_code_shows_notice(self, client):
        # Submitted but blank: explain it, rather than silently re-rendering the form.
        resp = client.get("/workshop?code=%20%20")
        assert resp.status_code == 200
        assert b"could not find a model for that code" in resp.data

    def test_workshop_without_code_param_has_no_notice(self, client):
        resp = client.get("/workshop")
        assert resp.status_code == 200
        assert b"could not find a model for that code" not in resp.data

    def test_workshop_model_serves_viewer(self, client):
        data = self._ingest(client).get_json()
        resp = client.get(f"/workshop?model={data['model_stem']}")
        assert resp.status_code == 200
        assert b"Accessible 3D Model Viewer" in resp.data

    def test_workshop_entry_page_has_code_input(self, client):
        resp = client.get("/workshop")
        assert resp.status_code == 200
        assert b'name="code"' in resp.data


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

        # Session A: identify with email first (creates the session), then upload.
        with flask_app.test_client() as client_a:
            client_a.post("/session/identify", json={"email": "user@example.com", "consent": True})
            upload_resp = self._upload(client_a, "model_a.stl")
            assert upload_resp.get_json()["status"] == "success"

        # Session B: fresh client (different cookie), same email.
        with flask_app.test_client() as client_b:
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
            client_a.post("/session/identify", json={"email": "user@example.com", "consent": True})
            upload_resp = self._upload(client_a, "shared.stl")
            filename_a = upload_resp.get_json()["filename"]

        with flask_app.test_client() as client_b:
            client_b.post("/session/identify", json={"email": "user@example.com", "consent": True})
            del_resp = client_b.delete(f"/models/{filename_a}")
            assert del_resp.status_code == 200

            # Model should no longer appear for either session.
            models_b = client_b.get("/session/models").get_json()["models"]
            assert all(m["filename"] != filename_a for m in models_b)

    def test_anonymous_session_cannot_see_identified_session_models(self, tmp_db):
        flask_app.config["TESTING"] = True

        with flask_app.test_client() as client_a:
            client_a.post("/session/identify", json={"email": "user@example.com", "consent": True})
            self._upload(client_a, "private.stl")

        # A different session with no email must not see the identified user's models.
        with flask_app.test_client() as client_anon:
            client_anon.post("/session/identify", json={"email": None, "consent": True})
            models = client_anon.get("/session/models").get_json()["models"]
            assert models == []


# ---------------------------------------------------------------------------
# POST /events/track
# ---------------------------------------------------------------------------

class TestEventsTrack:
    def test_valid_event_accepted(self, client):
        _identify(client, consent=True)
        resp = client.post(
            "/events/track",
            json={"event_type": "keyboard_shortcut", "event_data": {"key": "ArrowUp"}},
        )
        assert resp.status_code == 200

    def test_unknown_event_type_rejected(self, client):
        _identify(client, consent=True)
        resp = client.post(
            "/events/track",
            json={"event_type": "unknown_type", "event_data": {}},
        )
        assert resp.status_code == 400

    def test_invalid_event_data_rejected(self, client):
        _identify(client, consent=True)
        resp = client.post(
            "/events/track",
            json={"event_type": "section_dwell", "event_data": "not-an-object"},
        )
        assert resp.status_code == 400

    def test_event_without_consent_not_recorded(self, client, tmp_db):
        import sqlite3
        resp = client.post(
            "/events/track",
            json={"event_type": "keyboard_shortcut", "event_data": {"key": "1"}},
        )
        assert resp.status_code == 200
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT session_id FROM page_events").fetchone()
        assert row is None  # consent not given; event must not be persisted
        conn.close()
