""".stp is a STEP file too, and the suffix many CAD tools save one with.

The upload used to answer "Unsupported file type '.stp'" to a file the loader
reads exactly as it reads a .step.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from app import db, server
from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
STEP_SOURCE = ROOT / "src" / "models" / "brep" / "cube.step"
TAB = "tab-" + "e" * 32


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


def test_discovery_finds_stp_whatever_its_case_and_after_the_stls(monkeypatch, tmp_path):
    models, uploads = tmp_path / "models", tmp_path / "uploads"
    models.mkdir()
    uploads.mkdir()
    for name in ("a_part.stp", "B_PART.STP", "c_mug.stl", "notes.txt"):
        (models / name).write_bytes(b"x")
    monkeypatch.setattr(server, "MODEL_DIR", models)
    monkeypatch.setattr(server, "UPLOAD_DIR", uploads)

    names = [path.name for path in server._discover_models()]
    assert names == ["c_mug.stl", "B_PART.STP", "a_part.stp"]


@pytest.mark.parametrize("name", ["bracket.stp", "BRACKET_CAPS.STP"])
def test_an_stp_upload_is_accepted_and_renders(client, name):
    response = _upload(client, name, STEP_SOURCE.read_bytes())
    assert response.status_code == 200, response.get_data(as_text=True)
    stem = response.get_json()["model_stem"]
    rendered = client.post(
        "/render",
        json={"current_model": stem, "view": "z+", "zoom": 0, "depth": 50, "renderMode": "Cut",
              "mode": "single", "target_pixel_width": 96, "target_pixel_height": 40},
        headers={"X-Upload-Session": TAB},
    )
    assert rendered.status_code == 200, rendered.get_data(as_text=True)
    assert rendered.get_json()["status"] == "success"


def test_an_unsupported_type_names_every_suffix_that_works(client):
    response = _upload(client, "part.obj", b"o part\n")
    assert response.status_code == 400
    assert ".stp" in response.get_json()["message"]


def test_the_file_chooser_offers_stp():
    html = (ROOT / "accessible-3d-viewer.html").read_text(encoding="utf-8")
    accept = re.search(r'id="upload-model-input" accept="([^"]+)"', html).group(1).split(",")
    assert {".stp", ".STP"} <= set(accept)
