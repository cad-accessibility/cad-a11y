"""An upload is visible only to the browser that sent it (#237).

Each test runs from two clients at once. Two `flask_app.test_client()` instances
are two cookie jars, which is what two browsers are at this layer; a tab without
a cookie is a client that never calls /session/identify and names itself with
the X-Upload-Session header instead.

Adapted from the #123 investigation's tests on fix/concurrent-session-models,
which addressed models by position and were written as expected failures.

Model discovery is part of what is under test, so these run against temporary
model directories rather than the real data/models. See the isolated_models
fixture.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

import app.db as db_module
from app import server
from app.server import app as flask_app

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_SRC = REPO_ROOT / "builtin_models"

# The two smallest built-ins, with clearly different geometry, so a render of the
# upload is never mistaken for a render of the default.
DEFAULT_SOURCE = BUILTIN_SRC / "cube.stl"
PRIVATE_SOURCE = BUILTIN_SRC / "guide_signature.stl"

TAB_A = "tab-" + "a" * 32
TAB_B = "tab-" + "b" * 32


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_usage.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module._local.__dict__.clear()
    db_module.init_db()
    yield db_path
    db_module._local.__dict__.clear()


def _reset_shared_state() -> None:
    server.renderers_by_model.clear()
    server.quantized_render_cache.clear()
    server.preview_payload_cache.clear()
    with server.uploaded_models_lock:
        server.uploaded_models_by_session.clear()


def _rediscover() -> None:
    server.AVAILABLE_MODELS = server._discover_models() or [server.DEFAULT_MODEL]
    server.MODEL_NAME_LIST = [p.stem for p in server.AVAILABLE_MODELS]
    server._model_list_last_refresh = 0.0


@pytest.fixture()
def isolated_models(tmp_path):
    """Point model discovery at empty temporary directories for one test.

    Save-and-restore rather than monkeypatch, because monkeypatch undoes its
    patches after fixture teardown, and a teardown that re-discovered models then
    would do it against directories that are about to be deleted.
    """
    model_dir = tmp_path / "models"
    upload_dir = tmp_path / "uploads"
    model_dir.mkdir()
    upload_dir.mkdir()

    saved = {
        name: getattr(server, name)
        for name in (
            "MODEL_DIR",
            "UPLOAD_DIR",
            "DEFAULT_MODEL",
            # Read by _is_builtin. Patching MODEL_DIR alone would leave built-in
            # classification pointing at the real directory.
            "_MODEL_DIR_RESOLVED",
            "AVAILABLE_MODELS",
            "MODEL_NAME_LIST",
            "_model_list_last_refresh",
        )
    }

    server.MODEL_DIR = model_dir
    server.UPLOAD_DIR = upload_dir
    server._MODEL_DIR_RESOLVED = model_dir.resolve()
    default = model_dir / "aaa_default.stl"
    shutil.copyfile(DEFAULT_SOURCE, default)
    server.DEFAULT_MODEL = default
    _rediscover()
    _reset_shared_state()

    yield model_dir, upload_dir

    for name, value in saved.items():
        setattr(server, name, value)
    server._model_list_last_refresh = 0.0
    _reset_shared_state()


# The plain constructor, not the `with` form: two clients whose requests
# interleave would pop each other's request contexts.
@pytest.fixture()
def owner(tmp_db, isolated_models):
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


@pytest.fixture()
def stranger(tmp_db, isolated_models):
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _identify(client, email=None, consent=True):
    return client.post("/session/identify", json={"email": email, "consent": consent})


def _upload(client, name="private_part.stl", source=PRIVATE_SOURCE, tab=TAB_A):
    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(source.read_bytes()), name), "upload_session_id": tab},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _headers(tab):
    return {"X-Upload-Session": tab} if tab else {}


def _render_body(model, **overrides):
    body = {
        "current_model": model,
        "view": "y-",
        "zoom": "0",
        "depth": 0,
        "renderMode": "Filled",
        "mode": "single",
        "target_pixel_width": 96,
        "target_pixel_height": 40,
    }
    body.update(overrides)
    return body


def _post(client, path, model, tab=None, **overrides):
    response = client.post(path, json=_render_body(model, **overrides), headers=_headers(tab))
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _first_event(client, tab=None):
    """The event stream's first message, which carries the model list."""
    path = "/events" + (f"?upload_session_id={tab}" if tab else "")
    response = client.get(path, buffered=False)
    try:
        chunk = next(iter(response.response))
    finally:
        response.close()
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    return json.loads(text.removeprefix("data: ").strip())


def _every_model_list(client, tab=None):
    """Every place the server hands out a list of models, as the client asks."""
    headers = _headers(tab)
    return {
        "/models": client.get("/models", headers=headers).get_json()["model_list"],
        "/get_data": client.get("/get_data", headers=headers).get_json()["model_list"],
        "/render": _post(client, "/render", None, tab)["model_list"],
        "/events": _first_event(client, tab)["model_list"],
    }


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_a_stranger_sees_no_upload_but_their_own(owner, stranger):
    _identify(owner, email="a@example.com")
    stem = _upload(owner)["model_stem"]

    for where, listing in _every_model_list(stranger, TAB_B).items():
        assert stem not in listing, f"{where} listed another browser's upload"
        assert "aaa_default" in listing, f"{where} dropped the built-ins"

    for where, listing in _every_model_list(owner, TAB_A).items():
        assert stem in listing, f"{where} hid an upload from its owner"


def test_a_tab_without_a_cookie_sees_its_own_uploads(owner, stranger):
    """Escape on the consent dialog leaves no cookie. The tab id is the owner."""
    data = _upload(owner, tab=TAB_A)
    stem = data["model_stem"]
    assert stem in data["model_list"], "the upload response left out the upload"

    for where, listing in _every_model_list(owner, TAB_A).items():
        assert stem in listing, f"{where} hid a cookieless tab's own upload"
    for where, listing in _every_model_list(stranger, TAB_B).items():
        assert stem not in listing, f"{where} listed another tab's upload"
    for where, listing in _every_model_list(stranger).items():
        assert stem not in listing, f"{where} listed an upload to a request naming no tab"


def test_the_model_list_gives_away_no_filesystem_paths(stranger):
    payload = stranger.get("/models").get_json()
    assert "model_paths" not in payload
    assert not any("/" in stem for stem in payload["model_list"])


# ---------------------------------------------------------------------------
# Rendering, braille, preview, fit and export
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path, field",
    [
        ("/render", "image_base64"),
        ("/render/preview", "render_preview_base64"),
        ("/render/dotpad-hex", "dotpad_graphic_hex"),
        ("/render/export-source", "image_base64"),
        ("/render/fit-view", None),
    ],
)
def test_a_stranger_gets_the_default_model_not_the_upload(owner, stranger, path, field):
    """Every endpoint that takes a model answers a stranger as it would a name
    that does not exist, so the answer cannot tell anyone an upload is there."""
    _identify(owner, email="a@example.com")
    stem = _upload(owner)["model_stem"]
    extra = {"export_width": 160, "dotpad_cols": 30, "dotpad_rows": 10}

    def answer(client, model, tab):
        body = _post(client, path, model, tab, **extra)
        body.pop("debug", None)
        body.pop("model_list", None)
        return body[field] if field else body

    owners = answer(owner, stem, TAB_A)
    default = answer(stranger, None, TAB_B)
    assert owners != default, "the upload renders like the default, so this proves nothing"

    assert answer(stranger, stem, TAB_B) == default, "a stranger got the upload"
    assert answer(stranger, stem, None) == default, "a request naming no tab got the upload"
    assert answer(stranger, "no_such_model", TAB_B) == default


def test_a_position_in_the_list_cannot_reach_someone_elses_upload(owner, stranger):
    """Numeric model positions count within what the caller can see."""
    _identify(owner, email="a@example.com")
    stem = _upload(owner)["model_stem"]
    index = server.MODEL_NAME_LIST.index(stem)

    default = _post(stranger, "/render", None, TAB_B)["image_base64"]
    assert _post(stranger, "/render", index, TAB_B)["image_base64"] == default


def test_the_owner_renders_their_upload_by_cookie_from_a_second_tab(owner):
    """Two tabs in one browser share the cookie, and the cookie owns the upload."""
    _identify(owner, email="a@example.com")
    stem = _upload(owner, tab=TAB_A)["model_stem"]

    first_tab = _post(owner, "/render", stem, TAB_A)["image_base64"]
    other_tab = _post(owner, "/render", stem, "tab-" + "c" * 32)["image_base64"]
    assert other_tab == first_tab


# ---------------------------------------------------------------------------
# The email is not a key
# ---------------------------------------------------------------------------

def test_typing_someone_elses_email_shows_and_deletes_nothing(owner, stranger, isolated_models):
    _, upload_dir = isolated_models
    _identify(owner, email="a@example.com")
    filename = _upload(owner)["filename"]

    _identify(stranger, email="a@example.com")
    assert stranger.get("/session/models").get_json()["models"] == []
    listing = stranger.get("/models", headers=_headers(TAB_B)).get_json()["model_list"]
    assert Path(filename).stem not in listing

    response = stranger.delete(f"/models/{filename}")
    assert response.status_code == 404
    assert (upload_dir / filename).exists(), "a stranger deleted the upload"

    assert owner.delete(f"/models/{filename}").status_code == 200


def test_the_workshop_does_not_match_an_email(owner, stranger):
    """/workshop?name= used to hand over the session of any viewer who had typed
    that address, with their uploads and their delete button."""
    _identify(owner, email="a@example.com")
    _upload(owner)

    response = stranger.get("/workshop?name=a@example.com")
    assert response.status_code == 200
    assert b"could not find a model for that name" in response.data
    assert "cad_session" not in response.headers.get("Set-Cookie", "")


def test_the_workshop_still_finds_a_participant_by_first_name(stranger):
    ingest = stranger.post(
        "/ingest",
        data={"file": (io.BytesIO(PRIVATE_SOURCE.read_bytes()), "zoe.stl"), "first_name": "Zoe"},
        content_type="multipart/form-data",
    ).get_json()

    response = stranger.get("/workshop?name=zoe")
    assert response.status_code == 302
    assert f"model={ingest['model_stem']}" in response.headers["Location"]
