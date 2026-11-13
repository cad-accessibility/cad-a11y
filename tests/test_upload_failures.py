"""An upload that fails says why, in words someone can act on (#239).

A file over the size limit used to come back as Flask's HTML 413 page, which the
viewer parsed as JSON and read out as "Unexpected token '<'". A file named in a
script secure_filename cannot spell in ASCII, such as 茶杯.stl, lost its whole
stem and its extension with it, and was refused as "Unsupported file type ''".
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
TAB = "tab-" + "f" * 32


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


def _upload(client, name, data):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(data), name), "upload_session_id": TAB},
        content_type="multipart/form-data",
    )


def test_a_file_over_the_limit_is_refused_in_json_naming_the_limit(client, monkeypatch):
    monkeypatch.setitem(flask_app.config, "MAX_CONTENT_LENGTH", 1024 * 1024)
    response = _upload(client, "big.stl", b"\0" * (2 * 1024 * 1024))
    assert response.status_code == 413
    body = response.get_json()
    assert body["status"] == "error"
    assert "1 MB" in body["message"]


@pytest.mark.parametrize("name", ["茶杯.stl", "Чашка.STL"])
def test_a_name_in_another_script_still_uploads_and_renders(client, name):
    response = _upload(client, name, MUG.read_bytes())
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    # A name already taken is renamed, and the rename lowercases the extension.
    assert Path(body["filename"]).suffix.lower() == Path(name).suffix.lower()
    assert Path(body["filename"]).stem, "it keeps a name to be listed under"
    rendered = client.post(
        "/render",
        json={"current_model": body["model_stem"], "view": "z+", "zoom": 0, "depth": 50,
              "renderMode": "Cut", "mode": "single", "target_pixel_width": 96, "target_pixel_height": 40},
        headers={"X-Upload-Session": TAB},
    )
    assert rendered.status_code == 200, rendered.get_data(as_text=True)


def test_a_latin_name_is_kept_as_before(client):
    response = _upload(client, "my mug.v2.STL", MUG.read_bytes())
    assert response.get_json()["filename"].startswith("my_mug.v2")
    assert response.get_json()["filename"].lower().endswith(".stl")


def test_a_name_with_no_extension_is_refused_as_unsupported(client):
    response = _upload(client, "茶杯", b"solid x\nendsolid x\n")
    assert response.status_code == 400
    assert "Unsupported file type" in response.get_json()["message"]


def test_the_viewer_says_why_when_the_answer_is_not_json():
    """A body far over the limit, or a proxy error, comes back as a page of
    HTML or plain text. The viewer says what happened instead of a parse error."""
    js = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    start = js.index("document.getElementById('upload-model-input').addEventListener('change'")
    handler = js[start:js.index("\n});\n", start)]
    assert "const data = await resp.json();" not in handler
    assert re.search(r"await resp\.json\(\)\.catch\(", handler)
    assert "resp.status === 413" in handler


def test_the_viewer_is_told_the_limit_and_checks_it_before_sending(client):
    """A 300 MB file used to be sent in full before the server refused it."""
    data = client.get("/get_data", headers={"X-Upload-Session": TAB}).get_json()
    assert data["max_upload_bytes"] == flask_app.config["MAX_CONTENT_LENGTH"]
    js = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    assert "uploadLimitBytes = data.max_upload_bytes;" in js
    start = js.index("document.getElementById('upload-model-input').addEventListener('change'")
    handler = js[start:js.index("\n});\n", start)]
    assert handler.index("file.size > uploadLimitBytes") < handler.index("fetch(`${SERVER_URL}/upload`")


@pytest.mark.parametrize("name, data", [
    ("junk.stl", b"this is a shopping list, not a mesh\n"),
    ("empty.stl", b"solid empty\nendsolid empty\n"),
    ("junk.step", b"ISO-10303-21;\nnot really\n"),
])
def test_a_file_that_is_not_a_model_is_refused_with_a_reason_and_not_kept(client, name, data):
    from app import server

    response = _upload(client, name, data)
    assert response.status_code == 400, response.get_data(as_text=True)
    assert "isn't a 3D model the viewer can read" in response.get_json()["message"]
    stem = Path(name).stem
    assert not [p for p in server.UPLOAD_DIR.glob(f"{stem}*") if p.suffix == Path(name).suffix]
    assert stem not in client.get("/get_data", headers={"X-Upload-Session": TAB}).get_json()["model_list"]


@pytest.mark.parametrize("model, watertight", [("rocking_chair", False), ("cube", True)])
def test_a_render_says_whether_the_model_has_holes(client, model, watertight):
    rendered = client.post(
        "/render",
        json={"current_model": model, "view": "z+", "zoom": 0, "depth": 50, "renderMode": "Cut",
              "mode": "single", "target_pixel_width": 96, "target_pixel_height": 40},
    ).get_json()
    assert rendered["watertight"] is watertight


def test_the_load_announcement_warns_of_holes():
    js = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    assert "data.watertight === false" in js
    assert "It has holes, so some slices may be wrong." in js
