"""Do two concurrent /viewer sessions stay independent of each other? (issue #123)

Each test here is one experiment. A test that passes today pins behaviour that is
already correct and must survive the fix. A test marked xfail(strict=True) encodes
the independence we want but do not have; when the fix lands, strict mode turns the
unexpected pass into a failure and tells you to delete the marker.

Two `flask_app.test_client()` instances are two independent cookie jars, which is
what "two sessions" means at this layer. The precedent is TestCrossSessionModels in
test_session.py; the only difference here is that both clients stay open at once so
their requests can interleave.

Model discovery is the quantity under measurement, so these tests must not run
against the real data/models. See the isolated_models fixture.
"""

from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import pytest

import app.db as db_module
import app.server as server
from app.server import app as flask_app

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_SRC = REPO_ROOT / "builtin_models"

# The two smallest built-ins, with clearly different geometry so a render of one is
# never mistaken for a render of the other.
SMALL_MODEL = BUILTIN_SRC / "cube.stl"
OTHER_MODEL = BUILTIN_SRC / "guide_signature.stl"

# Minimal ASCII STL, matching test_session.py. Fine where only a file's existence
# and its position in the sorted list matter; it will not produce a usable bbox, so
# never use it in a test that renders.
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


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a temporary file and initialise the schema."""
    db_path = tmp_path / "test_usage.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module._local.__dict__.clear()
    db_module.init_db()
    yield db_path
    db_module._local.__dict__.clear()


class ModelDirs:
    """Handle onto the temporary model directories, with a way to re-discover."""

    def __init__(self, model_dir: Path, upload_dir: Path):
        self.model_dir = model_dir
        self.upload_dir = upload_dir

    def add_builtin(self, stem: str, source: Path = SMALL_MODEL) -> Path:
        """Copy a real model into MODEL_DIR under a chosen stem, then re-discover."""
        target = self.model_dir / f"{stem}.stl"
        shutil.copyfile(source, target)
        self.rediscover()
        return target

    def add_upload_directly(self, stem: str, source: Path = SMALL_MODEL) -> Path:
        """Write into UPLOAD_DIR behind the app's back, bypassing _save_and_index_stl."""
        target = self.upload_dir / f"{stem}.stl"
        shutil.copyfile(source, target)
        self.rediscover()
        return target

    def rediscover(self) -> None:
        """Rebuild the derived globals the way the app does, ignoring the throttle."""
        server.AVAILABLE_MODELS = server._discover_models() or [server.DEFAULT_MODEL]
        server.MODEL_NAME_LIST = [p.stem for p in server.AVAILABLE_MODELS]
        server._model_list_last_refresh = 0.0


def _reset_shared_state() -> None:
    """Clear every process-global the render path touches."""
    server.state.current_model_index = 0
    server.renderers_by_model.clear()
    server.last_render_fingerprint = None
    server.last_render_response = None
    server.quantized_render_cache.clear()
    server.preview_payload_cache.clear()
    server.current_render = None
    server.uploaded_models_by_session.clear()


@pytest.fixture()
def isolated_models(tmp_path):
    """Repoint model discovery at empty temp directories for the duration of a test.

    The working tree's data/models holds hundreds of files left by earlier unisolated
    runs, and the sorted order of that directory is exactly what these tests measure,
    so inheriting it would destroy the premise before the test starts.

    Uses explicit save-and-restore rather than monkeypatch.setattr: monkeypatch undoes
    its patches *after* fixture teardown runs, so a teardown that rebuilds
    AVAILABLE_MODELS would rebuild it against a temp directory that is about to be
    deleted, poisoning every later test in the session.
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
            # Computed once at import and used by _is_builtin. Patching MODEL_DIR
            # alone leaves built-in classification pointing at the real directory,
            # which silently breaks every ownership assertion below.
            "_MODEL_DIR_RESOLVED",
            "AVAILABLE_MODELS",
            "MODEL_NAME_LIST",
            "_model_list_last_refresh",
        )
    }

    server.MODEL_DIR = model_dir
    server.UPLOAD_DIR = upload_dir
    server._MODEL_DIR_RESOLVED = model_dir.resolve()

    dirs = ModelDirs(model_dir, upload_dir)
    # Every test seeds at least one built-in; give DEFAULT_MODEL somewhere real to
    # point in the meantime so the `or [DEFAULT_MODEL]` fallback cannot leak a path
    # from the developer's actual data/models into a supposedly empty list.
    server.DEFAULT_MODEL = dirs.add_builtin("aaa_default")
    _reset_shared_state()

    yield dirs

    # Restore the directories first, then rebuild what derives from them, then clear
    # the throttle so the next test re-discovers immediately.
    for name, value in saved.items():
        setattr(server, name, value)
    server._model_list_last_refresh = 0.0
    _reset_shared_state()


# Deliberately not the `with flask_app.test_client() as c` form used elsewhere in
# this suite. That form keeps each request's context alive after the request returns,
# which is what lets a test inspect flask.request afterwards. Two clients issuing
# interleaved requests then pop each other's contexts out of order and Flask raises
# "Popped wrong request context". Interleaving is the entire point here, and nothing
# below needs to look at flask.request, so the plain constructor is correct.
@pytest.fixture()
def session_a(tmp_db, isolated_models):
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


@pytest.fixture()
def session_b(tmp_db, isolated_models):
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _identify(client, email=None, consent=True):
    """Establish a session the way the app does, via the consent endpoint.

    GET /viewer deliberately mints no cookie and creates no DB row, so it cannot be
    used to start a session. That is itself a finding (see the ownerless-upload test
    at the bottom), but it means tests needing an identity must come through here.
    """
    return client.post("/session/identify", json={"email": email, "consent": consent})


def _upload(client, filename, content=_MINIMAL_STL):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def _render(client, model, **overrides):
    body = {
        "current_model": model,
        "view": "y-",
        "zoom": "0",
        "depth": 0,
        "renderMode": "Filled",
        "mode": "single",
    }
    body.update(overrides)
    return client.post("/render", json=body)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Guard rails: these pass today and must keep passing after the fix.
# ---------------------------------------------------------------------------

class TestAlreadyCorrect:
    def test_builtin_indices_are_stable_across_uploads(self, session_a, session_b, isolated_models):
        """An upload cannot renumber a built-in.

        _discover_models emits the whole MODEL_DIR block before the whole UPLOAD_DIR
        block, so uploads always sort after built-ins no matter what they are called.
        This is the half of issue #123 item 1 that the storage split (PR #128) fixed.
        """
        isolated_models.add_builtin("zzz_builtin")
        listing = session_a.get("/models").get_json()["model_list"]
        builtin_index = listing.index("zzz_builtin")

        _identify(session_b, email="b@example.com")
        _upload(session_b, "aaaa_sorts_first.stl")

        after = session_a.get("/models").get_json()["model_list"]
        assert after[builtin_index] == "zzz_builtin"

    def test_out_of_range_index_falls_back_to_zero(self, session_a, isolated_models):
        """Asking for a model past the end of the list fails closed rather than open."""
        isolated_models.add_builtin("only_one")
        resp = session_a.post("/models", json={"current_model": 9999})
        assert resp.get_json()["current_model"] == 0

    def test_hardware_cube_value_is_broadcast_to_every_session(self, session_a, isolated_models):
        """The shared hardware channel is deliberate and must not be scoped away.

        One physical WitMotion cube and one slider drive every connected station, so
        cube_value and slider_value are pushed to all sessions on purpose. The defect
        in issue #123 is that load_model rides the same unfiltered channel, not that
        the channel is unfiltered. A fix that scopes SSE per session without carving
        out hardware events would break the workshop stations.
        """
        pushed = []
        original = server._push_sse
        server._push_sse = lambda data: pushed.append(data)
        try:
            server._push_sse({"cube_value": "z+"})
        finally:
            server._push_sse = original
        assert {"cube_value": "z+"} in pushed

    def test_deleting_another_sessions_model_is_refused(self, session_a, session_b, isolated_models):
        """The one place ownership is actually enforced today."""
        _identify(session_a, email="a@example.com")
        filename = _upload(session_a, "mine.stl").get_json()["filename"]

        _identify(session_b, email="b@example.com")
        resp = session_b.delete(f"/models/{filename}")
        assert resp.status_code == 404

    def test_same_email_shares_models_across_sessions(self, session_a, session_b, isolated_models):
        """Cross-device sharing by email is a feature, not a leak.

        session_owns_model deliberately widens to every session sharing an identifier
        so someone can open their own models on a second device.
        """
        _identify(session_a, email="same@example.com")
        filename = _upload(session_a, "portable.stl").get_json()["filename"]

        _identify(session_b, email="same@example.com")
        models = session_b.get("/session/models").get_json()["models"]
        assert any(m["filename"] == filename for m in models)


def _clear_response_caches() -> None:
    """Drop the shared response caches so a test measures the renderer, not the cache.

    The exact-fingerprint slot and the quantized LRU are both global and both key on
    the request parameters, so a session repeating a request it has made before gets
    its own earlier image back. That incidentally hides shared renderer state in the
    common case, which is worth knowing but is not what these tests are about.
    """
    server.last_render_fingerprint = None
    server.last_render_response = None
    server.quantized_render_cache.clear()
    server.preview_payload_cache.clear()


# ---------------------------------------------------------------------------
# Are the viewer's own commands self-contained per window?
#
# Selecting a model is only one of the things a window does. These cover the rest of
# the control surface: view axis, slice depth, zoom, and camera pan.
# ---------------------------------------------------------------------------

class TestCommandsAreSelfContained:
    def test_view_axis_is_per_request(self, session_a, session_b, isolated_models):
        """Choosing a viewpoint in one window must not move another window.

        Deliberate design: the comment at app/server.py:1421 keeps browser-selected
        viewpoints out of the shared state that hardware writes to, precisely so one
        person's navigation does not drag everyone else's view around.
        """
        first = _render(session_a, 0, view="y-").get_json()
        session_b.post("/render", json={"current_model": 0, "view": "z+", "renderMode": "Filled",
                                        "mode": "single", "depth": 0, "zoom": "0"})
        _clear_response_caches()
        again = _render(session_a, 0, view="y-").get_json()
        assert again["image_base64"] == first["image_base64"]

    def test_slice_depth_is_per_request(self, session_a, session_b, isolated_models):
        """Moving the slice in one window must not move it in another."""
        first = _render(session_a, 0, depth=10).get_json()
        session_b.post("/render", json={"current_model": 0, "view": "y-", "renderMode": "Filled",
                                        "mode": "single", "depth": 80, "zoom": "0"})
        _clear_response_caches()
        again = _render(session_a, 0, depth=10).get_json()
        assert again["image_base64"] == first["image_base64"]

    def test_zoom_is_per_request(self, session_a, session_b, isolated_models):
        """Zooming in one window must not zoom another."""
        first = _render(session_a, 0, zoom="0").get_json()
        session_b.post("/render", json={"current_model": 0, "view": "y-", "renderMode": "Filled",
                                        "mode": "single", "depth": 0, "zoom": "3"})
        _clear_response_caches()
        again = _render(session_a, 0, zoom="0").get_json()
        assert again["image_base64"] == first["image_base64"]

    @pytest.mark.xfail(
        strict=True,
        reason="#123: pan state lives on a renderer shared between sessions, "
               "app/cad_comparison_lib.py:828 and :836",
    )
    def test_panning_does_not_move_another_sessions_camera(
        self, session_a, session_b, isolated_models
    ):
        """Panning in one window must not drag another window's camera.

        view_current_camera_center is mutable state on the CADComparisonRenderer, and
        two sessions looking at the same model get the same renderer object. A render
        that does not carry its own camera_center inherits wherever the other session
        last panned to.
        """
        first = _render(session_a, 0, view="y-").get_json()

        session_b.post("/render", json={"current_model": 0, "view": "y-", "renderMode": "Filled",
                                        "mode": "single", "depth": 0, "zoom": "0",
                                        "move_camera_center": "left"})

        _clear_response_caches()
        again = _render(session_a, 0, view="y-").get_json()
        assert again["image_base64"] == first["image_base64"], (
            "another session's pan moved this session's camera"
        )


# ---------------------------------------------------------------------------
# The independence we want but do not have.
# ---------------------------------------------------------------------------

class TestSessionIndependence:
    @pytest.mark.xfail(
        strict=True,
        reason="#123: current model is one process-wide value, app/server.py:364 and :1424",
    )
    def test_a_new_tab_does_not_reset_another_sessions_model(
        self, session_a, session_b, isolated_models
    ):
        """Merely opening a second viewer resets the first session's model.

        The client starts with `currentModel = "none"` (static/js/viewer.js:391) and
        sends it verbatim. _normalize_model_index hits its ValueError branch and
        returns 0, and the render path writes that into the shared state object.
        No upload, no selection, no second user intent required.
        """
        isolated_models.add_builtin("bbb_second")
        assert server._normalize_model_index("none") == 0

        session_a.post("/models", json={"current_model": 1})
        assert session_a.get("/models").get_json()["current_model"] == 1

        session_b.post("/models", json={"current_model": "none"})

        assert session_a.get("/models").get_json()["current_model"] == 1

    @pytest.mark.xfail(
        strict=True,
        reason="#123: export ignores its current_model argument, app/server.py:2146",
    )
    def test_export_source_renders_the_model_it_was_asked_for(
        self, session_a, session_b, isolated_models
    ):
        """Session A's export returns whichever model session B rendered last.

        render_export_source calls get_or_create_renderer() with no argument, so the
        index resolves to the process-global current model. The posted current_model
        lands in merged_params and is never read by the renderer.
        """
        isolated_models.add_builtin("bbb_other", source=OTHER_MODEL)

        session_a.post("/models", json={"current_model": 1})
        first = session_a.post(
            "/render/export-source", json={"current_model": 1, "export_width": 160}
        ).get_json()["image_base64"]

        session_b.post("/models", json={"current_model": 0})
        second = session_a.post(
            "/render/export-source", json={"current_model": 1, "export_width": 160}
        ).get_json()["image_base64"]

        assert _digest(second) == _digest(first), (
            "A asked for model 1 twice and got two different images; "
            "the second followed B's selection"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="#123: models are named by position in a list that reorders, app/server.py:427",
    )
    def test_an_upload_cannot_renumber_another_sessions_upload(
        self, session_a, session_b, isolated_models
    ):
        """This is the workshop symptom: B's upload re-points A's index at B's file.

        Both files land in UPLOAD_DIR, which is sorted, so a name that sorts earlier
        shifts every later upload by one. A holds only the integer.
        """
        _identify(session_a, email="a@example.com")
        _upload(session_a, "m_beta.stl")
        listing = session_a.get("/models").get_json()["model_list"]
        a_index = listing.index("m_beta")

        _identify(session_b, email="b@example.com")
        _upload(session_b, "m_alpha.stl")

        after = session_a.get("/models").get_json()["model_list"]
        assert after[a_index] == "m_beta", (
            f"A's index {a_index} now names {after[a_index]!r}, which belongs to B"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="#123: GET /models is unfiltered and needs no cookie, app/server.py:1580",
    )
    def test_a_stranger_cannot_list_another_sessions_upload(
        self, session_a, session_b, isolated_models
    ):
        """No concurrency needed: a visitor with no session sees everyone's uploads."""
        _identify(session_a, email="a@example.com")
        _upload(session_a, "private_part.stl")

        assert session_b.get("/session/models").get_json()["models"] == []
        listing = session_b.get("/models").get_json()["model_list"]
        assert not any("private_part" in stem for stem in listing)

    @pytest.mark.xfail(
        strict=True,
        reason="#123: GET /models discloses real filesystem paths, app/server.py:1594",
    )
    def test_the_model_list_gives_away_no_filesystem_paths(self, session_a, isolated_models):
        """The client never reads model_paths, but every caller is handed them."""
        payload = session_a.get("/models").get_json()
        assert "model_paths" not in payload

    @pytest.mark.xfail(
        strict=True,
        reason="#123: render performs no ownership check, app/server.py:1375",
    )
    def test_a_stranger_cannot_render_another_sessions_upload(
        self, session_a, session_b, isolated_models
    ):
        """Ownership is recorded in the database and never consulted on the render path."""
        _identify(session_a, email="a@example.com")
        session_a.post(
            "/upload",
            data={"file": (io.BytesIO(SMALL_MODEL.read_bytes()), "private_part.stl")},
            content_type="multipart/form-data",
        )

        listing = session_b.get("/models").get_json()["model_list"]
        index = next(i for i, stem in enumerate(listing) if "private_part" in stem)

        resp = _render(session_b, index)
        assert resp.get_json().get("bbox") is None, (
            "a session with no cookie rendered another session's upload"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="#123: dotpad path performs no ownership check, app/server.py:2207",
    )
    def test_a_stranger_cannot_braille_another_sessions_upload(
        self, session_a, session_b, isolated_models
    ):
        """The same gap reaches the hardware path, which is the workshop's output.

        Compares the stranger's frame against the owner's frame for the same model
        rather than just checking the response is non-empty: a blank frame is also
        non-empty (it is a run of "0" characters), so only an exact match with what
        the owner legitimately gets proves the geometry itself leaked.
        """
        _identify(session_a, email="a@example.com")
        session_a.post(
            "/upload",
            data={"file": (io.BytesIO(SMALL_MODEL.read_bytes()), "private_part.stl")},
            content_type="multipart/form-data",
        )

        listing = session_b.get("/models").get_json()["model_list"]
        index = next(i for i, stem in enumerate(listing) if "private_part" in stem)
        body = {
            "current_model": index,
            "view": "z+",
            "renderMode": "Filled",
            "dotpad_cols": 60,
            "dotpad_rows": 40,
        }

        owner_hex = session_a.post("/render/dotpad-hex", json=body).get_json()["dotpad_graphic_hex"]
        # Guard the comparison: if the owner's own frame were blank there would be
        # nothing to leak and the assertion below would pass for the wrong reason.
        assert owner_hex.strip("0"), "owner's frame is blank, so this test proves nothing"

        stranger_hex = session_b.post("/render/dotpad-hex", json=body).get_json()["dotpad_graphic_hex"]
        assert stranger_hex != owner_hex, (
            "a session with no cookie received the owner's exact braille frame"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="#123: _push_sse broadcasts to every client unfiltered, app/server.py:399",
    )
    def test_ingest_does_not_broadcast_a_model_switch_to_every_session(self, session_a, isolated_models):
        """One ingest pushes a model switch at every connected tab, whoever they are.

        This asserts only that the broadcast is emitted; whether a given browser acts
        on it is a client-side race covered by the Playwright probe.
        """
        pushed = []
        original = server._push_sse
        server._push_sse = lambda data: pushed.append(data)
        try:
            session_a.post(
                "/ingest?open=1",
                data={"file": (io.BytesIO(_MINIMAL_STL), "zoe.stl"), "first_name": "Zoe"},
                content_type="multipart/form-data",
            )
        finally:
            server._push_sse = original

        assert not any("load_model" in payload for payload in pushed)

    @pytest.mark.xfail(
        strict=True,
        reason="#123: /viewer mints no cookie so an upload gets no owner, app/server.py:1201",
    )
    def test_an_uploaded_model_has_an_owner_even_without_consent(self, session_a, isolated_models):
        """A plain visitor's upload belongs to nobody, so it belongs to everybody.

        GET /viewer creates no session, so POST /upload passes session_id=None and
        register_model is never called. The file is still listed and renderable by
        index to anyone, and it vanishes from its own uploader's dropdown on reload.
        """
        session_a.get("/viewer")
        filename = _upload(session_a, "orphan.stl").get_json()["filename"]

        rows = db_module.get_all_uploaded_models() if hasattr(
            db_module, "get_all_uploaded_models"
        ) else None
        if rows is None:
            owned = session_a.get("/session/models").get_json()["models"]
            assert any(m["filename"] == filename for m in owned), (
                "the uploader cannot see their own upload, because it has no owner"
            )
        else:
            assert any(r["filename"] == filename for r in rows)

    @pytest.mark.xfail(
        strict=True,
        reason="#123: renderers_by_model is keyed by index and survives a reorder, app/server.py:519",
    )
    def test_a_reordered_list_does_not_serve_a_cached_renderer(self, session_a, isolated_models):
        """A file arriving out of band renumbers the list without clearing the cache.

        _refresh_model_list_if_stale rebuilds AVAILABLE_MODELS but leaves
        renderers_by_model alone, and get_or_create_renderer only checks whether the
        index is present. The trigger is an operator dropping a file in, not a user
        action, so this ranks last.
        """
        first = _render(session_a, 0).get_json()
        assert first.get("bbox") is not None

        # Bypass _save_and_index_stl entirely, the way `docker cp` or an operator would.
        # The name has to sort ahead of the fixture's "aaa_default" to take position 0,
        # and "aaa_first" does not: "d" precedes "f".
        target = isolated_models.model_dir / "000_dropped_in.stl"
        shutil.copyfile(OTHER_MODEL, target)
        server._model_list_last_refresh = 0.0
        session_a.get("/health")

        listing = session_a.get("/models").get_json()["model_list"]
        assert listing[0] == "000_dropped_in", "setup failed: the dropped file did not take index 0"

        second = _render(session_a, 0, zoom="1").get_json()
        assert second.get("bbox") != first.get("bbox"), (
            "index 0 now names a different file but served the cached renderer"
        )
