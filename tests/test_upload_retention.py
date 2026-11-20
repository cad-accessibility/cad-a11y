"""Uploads are kept for a stated period and then deleted (#239).

Once a reload stopped deleting a tab's uploads, nothing removed an upload made
with a cookie at all. Each upload is now deleted UPLOAD_RETENTION_DAYS after it
was saved, the same way the delete button removes one, and the viewer says how
long that is next to the upload button.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest

from app import db, server
from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
MUG = ROOT / "builtin_models" / "tutorial_mug.stl"
TAB = "tab-" + "a1" * 16
DAY = 24 * 60 * 60


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "usage.db")
    db._local.__dict__.clear()
    db._contacts_local.__dict__.clear()
    db.init_db()
    # Its own upload directory, so the count of expired files is this test's.
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(server, "UPLOAD_DIR", uploads)
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
    with server.uploaded_models_lock:
        server.uploaded_models_by_session.pop(TAB, None)
    with server.models_lock:
        server.AVAILABLE_MODELS = server._discover_models() or [server.DEFAULT_MODEL]
        server.MODEL_NAME_LIST = [p.stem for p in server.AVAILABLE_MODELS]
    db._local.__dict__.clear()
    db._contacts_local.__dict__.clear()


def _upload(client, name: str) -> Path:
    body = client.post(
        "/upload",
        data={"file": (io.BytesIO(MUG.read_bytes()), name), "upload_session_id": TAB},
        content_type="multipart/form-data",
    ).get_json()
    assert body["status"] == "success", body
    return server.UPLOAD_DIR / body["filename"]


def _age(path: Path, days: float) -> None:
    then = time.time() - days * DAY
    os.utime(path, (then, then))


@pytest.fixture()
def started_long_ago(client):
    """Expiry has been running on this server for a year."""
    marker = server.UPLOAD_DIR / server._RETENTION_START
    marker.touch()
    _age(marker, 365)


def _listed(client) -> list[str]:
    return client.get("/get_data", headers={"X-Upload-Session": TAB}).get_json()["model_list"]


def test_an_upload_past_the_period_is_deleted_and_one_within_it_kept(client, started_long_ago, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_RETENTION_DAYS", 30.0)
    old, new = _upload(client, "old_part.stl"), _upload(client, "new_part.stl")
    _age(old, 31)
    _age(new, 29)

    assert server._expire_uploads() == 1

    assert not old.exists() and new.exists()
    assert old.stem not in _listed(client) and new.stem in _listed(client)
    with server.uploaded_models_lock:
        held = set().union(*server.uploaded_models_by_session.values())
    assert str(old.resolve()) not in held, "the tab would go on claiming a deleted file"


def test_a_period_of_zero_keeps_every_upload(client, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_RETENTION_DAYS", 0.0)
    path = _upload(client, "kept_part.stl")
    _age(path, 3650)
    assert server._expire_uploads() == 0
    assert path.exists()


def test_the_viewer_s_poll_runs_the_expiry_at_most_hourly(client, started_long_ago, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_RETENTION_DAYS", 30.0)
    monkeypatch.setattr(server, "_uploads_last_expired", float("-inf"))
    first = _upload(client, "polled_part.stl")
    _age(first, 31)
    _listed(client)
    assert not first.exists()

    second = _upload(client, "too_soon_part.stl")
    _age(second, 31)
    _listed(client)
    assert second.exists(), "expired again within the hour"


def test_the_viewer_states_the_period_next_to_the_upload_button(client, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_RETENTION_DAYS", 30.0)
    assert client.get("/get_data").get_json()["upload_retention_days"] == 30.0
    html = (ROOT / "accessible-3d-viewer.html").read_text(encoding="utf-8")
    assert 'aria-describedby="upload-model-retention"' in html
    assert '<div id="upload-model-retention" class="help-text"></div>' in html
    js = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    assert "Uploaded models are deleted after ${days} day${days === 1 ? '' : 's'}." in js


def test_uploads_already_there_get_the_full_period_from_the_first_run(client, monkeypatch):
    """The first deploy must not delete every upload older than the period at once."""
    monkeypatch.setattr(server, "UPLOAD_RETENTION_DAYS", 30.0)
    path = _upload(client, "from_last_year.stl")
    _age(path, 365)
    assert server._expire_uploads() == 0
    assert path.exists()
    assert (server.UPLOAD_DIR / server._RETENTION_START).exists()


def test_an_expiry_started_by_a_demo_request_still_forgets_the_rows(client, started_long_ago, monkeypatch):
    """A demo request is bound to the null recorder, and the rows are not the demo's."""
    from flask import g

    from app import recording

    monkeypatch.setattr(server, "UPLOAD_RETENTION_DAYS", 30.0)
    client.post("/session/identify", json={"email": None, "consent": True})
    path = _upload(client, "owned_part.stl")
    assert db.filename_has_live_owner(path.name)
    _age(path, 31)
    with flask_app.test_request_context("/get_data"):
        g._cad_recorder = recording.null_recorder()
        assert server._expire_uploads() == 1
    assert not db.filename_has_live_owner(path.name)


@pytest.mark.parametrize("raw", ["nan", "inf", "-1", "30d"])
def test_a_period_that_is_not_a_number_of_days_stops_the_server(monkeypatch, raw):
    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", raw)
    with pytest.raises(RuntimeError, match="UPLOAD_RETENTION_DAYS"):
        server._retention_days_from_env()


def test_the_deployed_container_can_be_given_the_period():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'UPLOAD_RETENTION_DAYS: "${UPLOAD_RETENTION_DAYS:-30}"' in compose


def test_the_demo_page_does_not_state_the_period():
    """Its uploads go when the tab closes."""
    js = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    assert "days > 0 && !demoMode" in js
