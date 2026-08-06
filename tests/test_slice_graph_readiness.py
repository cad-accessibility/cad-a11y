"""The slice graph auto-refreshes until precompute is ready, then stops.

Slice-graph precompute (the pairwise cross-section difference matrix) kicks
off lazily on the first slice-graph request for a model, in a background
thread, so that request can only return a flat placeholder profile — there is
nothing real to plot yet. In locked mode (the default) nothing else would
naturally trigger a second render, so without a deliberate refresh the graph
just sits there looking broken until the user happens to do something else
(reported: moving the depth slider "fixes" it, because that sends a new
render after precompute has had time to finish).

Three bugs, found in that order while chasing this live:

1. _get_zoom_filtered_slice_profile sets engine.slicegraph_ready to flag
   which kind of profile it just returned; the server reports this in the
   /render response — but nothing acted on it, so a caller had no way to
   know to look again.
2. The obvious fix — have the client retry the real render on a timer — is
   self-defeating: that render is a genuine matplotlib/shapely computation
   under render_lock, so retrying it competes with the very background
   precompute thread it's waiting on for CPU, and can starve precompute badly
   enough under a constrained host that it never finishes at all (confirmed
   live). /render/status only reads a flag the background thread already
   set; it never renders and never touches render_lock, so it can't compete
   with the thing it's checking on.
3. Even polling status correctly, the graph still never updated: /render's
   response cache is keyed only on request params, but a slice-graph response
   isn't a pure function of its params — the same params legitimately produce
   a different (correct) answer once background precompute finishes, which
   the cache has no way to know. It was serving the not-ready response
   forever. Slice-graph requests now bypass both cache layers entirely.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

import app.cad_comparison_lib as cad_lib
import app.server as server_module

VIEWER_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "viewer.js"
SERVER_PY = Path(__file__).resolve().parent.parent / "app" / "server.py"


@pytest.fixture()
def client(monkeypatch):
    server_module.app.config["TESTING"] = True
    # This module's render-cache tests care about a clean slate: both cache
    # layers are plain module globals, shared across every test in the
    # process, not scoped to a client instance.
    monkeypatch.setattr(server_module, "last_render_fingerprint", None)
    monkeypatch.setattr(server_module, "last_render_response", None)
    server_module.quantized_render_cache.clear()
    with server_module.app.test_client() as c:
        yield c


def _bare_renderer(monkeypatch):
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)
    renderer.view_cut_polygons = {}
    renderer.view_diff_mats = {}
    renderer.slicegraph_ready = True
    # Real precompute needs loaded geometry (self.shapes) and spawns a thread;
    # neither is relevant to what this test is checking, so no-op it.
    monkeypatch.setattr(renderer, "start_background_slice_precompute", lambda: None)
    return renderer


def test_profile_flags_not_ready_while_precompute_is_still_pending(monkeypatch):
    renderer = _bare_renderer(monkeypatch)

    profile = renderer._get_zoom_filtered_slice_profile("top", 50, [[0.0, 1.0], [0.0, 1.0]])

    assert renderer.slicegraph_ready is False
    assert np.array_equal(profile, np.zeros(101))


def test_profile_flags_ready_once_precompute_data_exists(monkeypatch):
    renderer = _bare_renderer(monkeypatch)
    renderer.view_cut_polygons["top"] = [box(0, 0, 1, 1) for _ in range(101)]
    renderer.view_diff_mats["top"] = np.zeros((101, 101))
    renderer.slicegraph_ready = False  # simulate a prior not-ready render

    profile = renderer._get_zoom_filtered_slice_profile("top", 50, [[0.0, 1.0], [0.0, 1.0]])

    assert renderer.slicegraph_ready is True
    assert len(profile) == 101


def test_server_only_reports_not_ready_when_a_slice_graph_was_actually_requested():
    source = SERVER_PY.read_text(encoding="utf-8")
    assert 'if params.get("compose_slicegraph") and not getattr(engine, "slicegraph_ready", True):' in source
    assert 'response["slicegraph_ready"] = False' in source



def test_render_status_endpoint_reads_the_flag_without_rendering(client, monkeypatch):
    fake_engine = type("FakeEngine", (), {"_slice_graphs_ready": True})()
    monkeypatch.setitem(server_module.renderers_by_model, 0, fake_engine)

    resp = client.post("/render/status", json={"current_model": 0})

    assert resp.status_code == 200
    assert resp.get_json() == {"slice_graphs_ready": True}


def test_render_status_endpoint_reports_not_ready_before_precompute_finishes(client, monkeypatch):
    fake_engine = type("FakeEngine", (), {"_slice_graphs_ready": False})()
    monkeypatch.setitem(server_module.renderers_by_model, 0, fake_engine)

    resp = client.post("/render/status", json={"current_model": 0})

    assert resp.get_json() == {"slice_graphs_ready": False}


def test_render_status_endpoint_does_not_construct_a_renderer(client, monkeypatch):
    # A status check for a model nobody has rendered yet must not itself be
    # the thing that constructs a renderer (and so triggers a mesh load) —
    # "not ready" is simply the correct answer for that case.
    monkeypatch.setattr(server_module, "renderers_by_model", {})
    calls = []
    monkeypatch.setattr(server_module, "get_or_create_renderer", lambda *a, **k: calls.append(1))

    resp = client.post("/render/status", json={"current_model": 0})

    assert resp.get_json() == {"slice_graphs_ready": False}
    assert calls == []


# ---------------------------------------------------------------------------
# Bug #3, the actual root cause: /render's response cache does not know a
# slice-graph response is time-dependent, so it kept serving the not-ready
# response forever once one had been cached — even after the client correctly
# detected precompute had finished and asked again. _render_response itself is
# stubbed out here (it needs a loaded model) so these tests isolate exactly
# the caching decision in render_view(), independent of what rendering does.
# ---------------------------------------------------------------------------


def _stub_render_response(monkeypatch):
    calls = {"n": 0}

    def fake(params, *, source):
        calls["n"] += 1
        return {"status": "success", "image_base64": "", "image_shape": [1, 1], "model_list": []}

    monkeypatch.setattr(server_module, "_render_response", fake)
    return calls


def test_slice_graph_requests_bypass_the_render_cache(client, monkeypatch):
    calls = _stub_render_response(monkeypatch)
    payload = {
        "view": "x+", "depth": 50, "zoom": 0, "renderMode": "Filled", "mode": "slice-graph",
        "compose_scrollbar": False, "compose_slicegraph": True, "current_model": 0,
    }

    client.post("/render", json=payload)
    client.post("/render", json=payload)

    assert calls["n"] == 2, (
        "identical slice-graph requests must each render fresh — the second "
        "call's answer can legitimately differ from the first's once "
        "background precompute finishes, which a params-keyed cache can't know"
    )


def test_non_slice_graph_requests_still_use_the_render_cache(client, monkeypatch):
    # Contrast case: caching identical non-slice-graph requests is correct and
    # intentional (a real perf win), so the fix must not have broken it.
    calls = _stub_render_response(monkeypatch)
    payload = {
        "view": "x+", "depth": 50, "zoom": 0, "renderMode": "Filled", "mode": "single",
        "compose_scrollbar": True, "compose_slicegraph": False, "current_model": 0,
    }

    client.post("/render", json=payload)
    client.post("/render", json=payload)

    assert calls["n"] == 1, "identical non-slice-graph requests should hit the exact-match cache"
