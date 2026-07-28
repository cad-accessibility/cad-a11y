"""Tests for the built-in / upload storage split.

Built-in models ship in ``builtin_models/`` and are seeded into ``MODEL_DIR``;
uploads go to ``UPLOAD_DIR``. Which directory a file sits in is what makes it
public or private, so these tests pin the invariants that classification depends
on. Regression cover for #102, where making ``MODEL_DIR`` writable collapsed the
two directories together and emptied the model dropdown.
"""

from __future__ import annotations

import importlib
import io
import sys

import pytest

from app.server import app as flask_app
from app.server import (
    BUILTIN_SOURCE_DIR,
    MODEL_DIR,
    UPLOAD_DIR,
    _builtin_model_stems,
    _is_builtin,
    _seed_builtin_models,
)
from scripts.cleanup_ingest_models import find_stale_models


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


# --- The invariant the whole scheme rests on ------------------------------


def test_upload_dir_is_not_the_model_dir():
    """If these ever collapse together, every upload becomes public."""
    assert UPLOAD_DIR.resolve() != MODEL_DIR.resolve()


def test_model_dir_is_not_an_upload_candidate(monkeypatch):
    """MODEL_DIR must never be chosen as the upload directory, writable or not.

    This is the actual #102 mechanism: MODEL_DIR used to be the first candidate,
    so making it writable silently reclassified every upload as a built-in.
    """
    import app.server as server

    monkeypatch.delenv("UPLOAD_MODEL_DIR", raising=False)
    resolved = server._resolve_upload_dir()
    assert resolved.resolve() != MODEL_DIR.resolve()


def test_equal_dirs_fail_at_import(monkeypatch):
    """Pointing UPLOAD_MODEL_DIR at the built-ins must fail loudly, not silently."""
    monkeypatch.setenv("UPLOAD_MODEL_DIR", str(MODEL_DIR))
    for name in [m for m in sys.modules if m.startswith("app.server")]:
        sys.modules.pop(name, None)
    with pytest.raises(RuntimeError, match="must not be the built-in model"):
        importlib.import_module("app.server")
    # Restore a clean module for any test that runs after this one.
    monkeypatch.delenv("UPLOAD_MODEL_DIR", raising=False)
    for name in [m for m in sys.modules if m.startswith("app.server")]:
        sys.modules.pop(name, None)
    importlib.import_module("app.server")


# --- Classification -------------------------------------------------------


def test_is_builtin_by_directory():
    assert _is_builtin(MODEL_DIR / "mug.stl")
    assert not _is_builtin(UPLOAD_DIR / "mug.stl")


def test_is_builtin_tracks_files_added_at_runtime():
    """The predicate must stay correct for a file that appears after startup.

    The old module-level BUILTIN_MODEL_STEMS was computed once at import while the
    model list kept refreshing, so a runtime addition was never classifiable.
    """
    late = MODEL_DIR / "added_after_startup.stl"
    late.write_bytes(b"solid x\nendsolid x\n")
    try:
        assert _is_builtin(late)
    finally:
        late.unlink(missing_ok=True)


def test_builtin_stems_are_not_empty():
    """The empty list is precisely what emptied and disabled the dropdown."""
    stems = _builtin_model_stems()
    assert stems
    assert "mug" in stems


def test_builtin_stems_exclude_uploads():
    upload = UPLOAD_DIR / "someone_elses_upload.stl"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"solid x\nendsolid x\n")
    try:
        assert "someone_elses_upload" not in _builtin_model_stems()
    finally:
        upload.unlink(missing_ok=True)


# --- Seeding --------------------------------------------------------------


def test_seed_is_idempotent():
    """Safe to run on every boot; a second pass copies nothing."""
    _seed_builtin_models()
    assert _seed_builtin_models() == 0


def test_seed_restores_a_missing_builtin():
    """Covers the Docker volume-seeding trap: a volume that predates a built-in."""
    victim = MODEL_DIR / "mug.stl"
    backup = victim.read_bytes()
    victim.unlink()
    try:
        assert _seed_builtin_models() == 1
        assert victim.exists()
    finally:
        if not victim.exists():
            victim.write_bytes(backup)


def test_every_shipped_builtin_reaches_the_model_dir():
    _seed_builtin_models()
    for source in BUILTIN_SOURCE_DIR.iterdir():
        if source.is_file() and not source.name.startswith("."):
            assert (MODEL_DIR / source.name).exists()


# --- Stem uniqueness across the split -------------------------------------


def test_upload_cannot_reuse_a_builtin_stem(client):
    """An upload named after a built-in must be renamed, not silently shadow it.

    Built-ins and uploads are in different directories now, so nothing collides on
    disk and a plain path check would let both exist as stem "mug". The client
    tells them apart by stem, so the upload would be shown to every visitor, and
    the stem would stop identifying one model.
    """
    payload = {"file": (io.BytesIO(b"solid x\nendsolid x\n"), "mug.stl")}
    response = client.post("/upload", data=payload, content_type="multipart/form-data")
    assert response.status_code == 200

    stored = response.get_json()["filename"]
    try:
        assert stored != "mug.stl", "upload was allowed to take the built-in's stem"
        assert stored.startswith("mug_")
        assert (UPLOAD_DIR / stored).exists()
        assert (MODEL_DIR / "mug.stl").exists(), "built-in must be untouched"
    finally:
        (UPLOAD_DIR / stored).unlink(missing_ok=True)


def test_stem_is_taken_spans_both_directories():
    """The uniqueness check must look in both directories, not just the target one."""
    import app.server as server

    assert server._stem_is_taken("mug"), "built-in stem must be reported as taken"
    assert not server._stem_is_taken("definitely_not_a_model_stem")

    upload = UPLOAD_DIR / "only_an_upload.stl"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"solid x\nendsolid x\n")
    try:
        assert server._stem_is_taken("only_an_upload"), (
            "a stem used only in the upload directory must still count as taken"
        )
    finally:
        upload.unlink(missing_ok=True)


# --- The endpoint the client actually reads -------------------------------


def test_get_data_reports_builtin_stems(client):
    payload = client.get("/get_data").get_json()
    stems = payload["builtin_model_stems"]
    assert stems, "an empty list is what disabled the dropdown in #102"
    assert "mug" in stems


# --- Cleanup script -------------------------------------------------------


def test_cleanup_spares_builtins_and_finds_strays(tmp_path):
    model_dir = tmp_path / "models"
    builtin_dir = tmp_path / "builtin"
    model_dir.mkdir()
    builtin_dir.mkdir()

    (builtin_dir / "mug.stl").write_bytes(b"x")
    (model_dir / "mug.stl").write_bytes(b"x")
    (model_dir / "participant_part.stl").write_bytes(b"x")
    (model_dir / "notes.txt").write_bytes(b"x")

    stale = find_stale_models(model_dir, builtin_dir)
    names = {p.name for p in stale}
    assert names == {"participant_part.stl"}, (
        "must spare shipped built-ins and ignore non-model files"
    )
