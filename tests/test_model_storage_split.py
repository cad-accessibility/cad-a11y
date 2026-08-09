"""Tests for the built-in / upload storage split.

Built-in models ship in ``builtin_models/`` and are seeded into ``MODEL_DIR``;
uploads go to ``UPLOAD_DIR``. Which directory a file sits in is what makes it
public or private, so these tests pin the invariants that classification depends
on. Regression cover for #102, where making ``MODEL_DIR`` writable collapsed the
two directories together and emptied the model dropdown.
"""

from __future__ import annotations

import io
import os
import subprocess
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
from scripts.cleanup_ingest_models import REPO_ROOT as ROOT
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


def test_equal_dirs_fail_at_import():
    """Pointing UPLOAD_MODEL_DIR at the built-ins must fail loudly, not silently.

    Run in a subprocess: reimporting app.server in-process would replace the
    module object every later test holds a reference to, so they would then
    monkeypatch a different module than the app actually routes through.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import app.server"],
        cwd=ROOT,
        env={**os.environ, "UPLOAD_MODEL_DIR": str(MODEL_DIR)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "server started with uploads pointed at the built-ins"
    assert "must not be the built-in model" in result.stderr


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


# --- Deployment self-check ------------------------------------------------


def test_health_reports_the_storage_invariant(client):
    """We have no shell access to the servers, so the app has to report its own
    state. These are the facts #124 asks someone to confirm on the box."""
    response = client.get("/health")
    assert response.status_code == 200, "a correct local run must report healthy"
    checks = response.get_json()["checks"]

    assert checks["storage_separated"] is True
    # Either is fine here: a fresh checkout has no schema until the server's own
    # startup creates it, and this test is about the storage layout.
    assert checks["database"] in ("ok", "uninitialised")
    assert checks["builtin_models_shipped"] > 0
    assert checks["public_models"] >= checks["builtin_models_shipped"]
    assert all(checks["writable"].values())


def test_health_leaks_no_paths_or_model_names():
    """It is served on a public deployment, so it must carry no filesystem
    detail: counts and booleans only."""
    import json

    from app.server import app as flask_app

    flask_app.config["TESTING"] = True
    body = json.dumps(flask_app.test_client().get("/health").get_json())

    assert "/" not in body.replace("\\/", ""), "no paths may appear in the payload"
    for leaky in ("mug", "cane_tip", str(MODEL_DIR), str(UPLOAD_DIR)):
        assert leaky not in body, f"{leaky!r} must not be exposed"


def test_health_degrades_when_storage_collapses(client, monkeypatch):
    """The whole point: a deployment where uploads became public must not pass.

    Reported as 503 so the container healthcheck and the deploy gate both fail,
    rather than the root page answering and the deploy looking fine.
    """
    import app.server as server

    monkeypatch.setattr(server, "UPLOAD_DIR", MODEL_DIR)
    response = client.get("/health")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "degraded"
    assert payload["checks"]["storage_separated"] is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits this relies on")
def test_health_reports_the_real_log_dir_not_the_braille_fallback(client, monkeypatch, tmp_path):
    """An unwritable data/logs is degraded even once braille has fallen back.

    The fallback is not equivalent to the real directory. /tmp/cad-a11y/logs
    lives inside the container and is discarded on every redeploy, and
    study_db writes participant session logs to data/logs/study with no
    fallback at all, so those fail outright. Reporting the resolved path here
    would turn /health green on a deployment that is quietly losing study
    data, which is the one thing the endpoint exists to catch. The entrypoint
    repairs the ownership that causes this, so it is now a real fault to
    surface rather than a permanent condition to route around.
    """
    import app.server as server

    unwritable = tmp_path / "data" / "logs"
    unwritable.mkdir(parents=True)
    unwritable.chmod(0o500)
    fallback = tmp_path / "tmp-cad-a11y" / "logs"
    fallback.mkdir(parents=True)

    monkeypatch.setattr(server, "STUDY_LOG_DIR", unwritable)
    monkeypatch.setattr(server, "BRAILLE_LOG_PATH", fallback / "braille_send_events.jsonl")
    server._writability_cache.clear()

    try:
        response = client.get("/health")

        assert response.status_code == 503
        assert response.get_json()["checks"]["writable"]["logs"] is False
    finally:
        # Restore so pytest's tmp_path cleanup can remove it.
        unwritable.chmod(0o700)
        server._writability_cache.clear()


def test_health_degrades_when_the_database_cannot_be_opened(client, monkeypatch, tmp_path):
    """The 2026-07-22 outage was the app being unable to open its database."""
    import app.db as db_module

    unopenable = tmp_path / "no-such-dir" / "usage.db"
    monkeypatch.setattr(db_module, "DB_PATH", unopenable)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["checks"]["database"] == "error"


def test_health_accepts_a_database_that_has_no_schema_yet(client, monkeypatch, tmp_path):
    """A fresh database is openable but empty until the server's startup runs.

    Treating that as a failure reported a perfectly good deployment as broken,
    which is what this test exists to stop. Openability is the outage condition;
    the schema is a separate fact and is reported separately.
    """
    import app.db as db_module

    fresh = tmp_path / "fresh.db"
    monkeypatch.setattr(db_module, "DB_PATH", fresh)
    response = client.get("/health")
    assert response.status_code == 200, "an openable but empty database is not a failure"
    assert response.get_json()["checks"]["database"] == "uninitialised"


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


# --- Review follow-ups ----------------------------------------------------


def test_stem_is_taken_ignores_extension_case():
    """A stem is how a model is named to the client, so it has to be unique
    whatever spelling the file on disk happens to use. The check listed
    ".stl", ".step" and ".STEP", so an upload could take the name of an
    existing .STL on a case-sensitive filesystem."""
    import app.server as server

    shouty = UPLOAD_DIR / "SHOUTY_EXTENSION.STL"
    shouty.parent.mkdir(parents=True, exist_ok=True)
    shouty.write_bytes(b"solid x\nendsolid x\n")
    try:
        assert server._stem_is_taken("SHOUTY_EXTENSION")
    finally:
        shouty.unlink(missing_ok=True)


def test_stem_is_taken_ignores_files_that_are_not_models():
    """Globbing the stem could otherwise let a stray note or render reserve a
    name no model actually uses."""
    import app.server as server

    note = UPLOAD_DIR / "just_a_note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_bytes(b"not a model")
    try:
        assert not server._stem_is_taken("just_a_note")
    finally:
        note.unlink(missing_ok=True)


def test_the_writability_probe_is_cached(monkeypatch, tmp_path):
    """/health is unauthenticated by design and polled every 30s, and each probe
    writes and unlinks a file. Repeated calls must not repeatedly touch disk."""
    import app.server as server

    calls = []
    monkeypatch.setattr(server, "_writability_cache", {})
    monkeypatch.setattr(server, "_is_writable_directory",
                        lambda path: (calls.append(path), True)[1])

    for _ in range(5):
        assert server._is_writable_directory_cached(tmp_path) is True
    assert len(calls) == 1, f"probed the disk {len(calls)} times for 5 calls"


def test_the_cache_expires_so_a_broken_mount_is_still_reported(monkeypatch, tmp_path):
    """Caching must not hide a mount going read-only for longer than one
    healthcheck interval."""
    import app.server as server

    answers = iter([True, False])
    monkeypatch.setattr(server, "_writability_cache", {})
    monkeypatch.setattr(server, "_is_writable_directory", lambda _p: next(answers))

    clock = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])

    assert server._is_writable_directory_cached(tmp_path) is True
    clock[0] += server._WRITABILITY_CACHE_TTL + 0.1
    assert server._is_writable_directory_cached(tmp_path) is False

    assert server._WRITABILITY_CACHE_TTL < 30, (
        "the cache must expire within one healthcheck interval"
    )


def test_each_directory_is_cached_separately(monkeypatch, tmp_path):
    """One shared entry would report every directory with the first one's answer."""
    import app.server as server

    monkeypatch.setattr(server, "_writability_cache", {})
    monkeypatch.setattr(server, "_is_writable_directory",
                        lambda path: path.name != "readonly")

    assert server._is_writable_directory_cached(tmp_path / "writable") is True
    assert server._is_writable_directory_cached(tmp_path / "readonly") is False


def test_the_image_healthcheck_uses_the_same_endpoint_as_compose():
    """Compose overrides the image's HEALTHCHECK, so this is what anyone running
    the image directly gets. Pointing it at the root reinstates exactly the
    failure this PR fixes: the root answers even when storage is broken."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    healthcheck = [line for line in dockerfile.splitlines() if "curl" in line and "6969" in line]

    assert healthcheck, "no HEALTHCHECK curl found in the Dockerfile"
    for line in healthcheck:
        assert "/health" in line, f"image healthcheck does not use /health: {line.strip()}"
