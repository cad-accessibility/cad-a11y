"""A reload keeps the person's uploads; only the demo station clears them (#239).

The viewer asked the server to delete the tab's uploads on every pagehide, which
fires on a reload as well as on closing the tab, so reloading lost the model
someone had just uploaded. The tab's id is in sessionStorage, which a reload
keeps, so the server goes on listing the upload to that tab once nothing deletes it.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from app import db
from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
MUG = ROOT / "builtin_models" / "tutorial_mug.stl"
TAB = "tab-" + "d" * 32


def _pagehide_handler() -> str:
    js = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    start = js.index("window.addEventListener('pagehide'")
    return js[start:js.index("\n});\n", start)]


def test_only_the_demo_page_deletes_uploads_when_it_goes_away():
    handler = _pagehide_handler()
    assert "/uploads/cleanup" in handler
    guard = re.search(r"if \(!demoMode\) return;", handler)
    assert guard and guard.start() < handler.index("/uploads/cleanup")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "usage.db")
    db._local.__dict__.clear()
    db._contacts_local.__dict__.clear()
    db.init_db()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
    db._local.__dict__.clear()
    db._contacts_local.__dict__.clear()


def test_without_a_cookie_the_reloaded_tab_still_lists_its_upload(client):
    uploaded = client.post(
        "/upload",
        data={"file": (io.BytesIO(MUG.read_bytes()), "kept.stl"), "upload_session_id": TAB},
        content_type="multipart/form-data",
    ).get_json()
    assert uploaded["status"] == "success"
    # What the reloaded page asks for, with the same tab id and no cookie.
    listed = client.get("/models", headers={"X-Upload-Session": TAB}).get_json()
    assert uploaded["model_stem"] in listed["model_list"]
