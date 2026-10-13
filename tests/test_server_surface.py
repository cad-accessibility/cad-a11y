"""The endpoints the server offers, and the state it does not keep.

Endpoints were removed here because nothing called them and each one reached into
process-wide state that made one window's actions visible to another:
``/render/image`` and ``/render/base64`` served whichever frame any window rendered
last, and ``POST /models`` set a single "current model" for everyone.

The site's address opens the viewer, and the only public description of any
route is the integration API's spec, checked here against the real routing table
so a stale or missing entry is caught (#232).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import api
from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = (ROOT / "app" / "server.py").read_text(encoding="utf-8")


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _registered_rules() -> set[str]:
    return {rule.rule for rule in flask_app.url_map.iter_rules()}


# ---------------------------------------------------------------------------
# The site's address (#232)
# ---------------------------------------------------------------------------

def _viewer_page() -> bytes:
    return (ROOT / "accessible-3d-viewer.html").read_bytes()


def test_the_root_is_the_viewer(client):
    """It used to answer with a hand-written JSON list of endpoints, which is what
    anyone following a link to the site saw first. It now serves the viewer
    itself rather than redirecting to /viewer (#244 review)."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert response.get_data() == _viewer_page()


def test_the_root_keeps_the_query_for_the_page(client):
    """?ui=simple and ?tutorial=start are read by the page, so they must reach it."""
    response = client.get("/?ui=simple")
    assert response.status_code == 200
    assert response.get_data() == _viewer_page()


@pytest.mark.parametrize("path", ["/viewer", "/viewer/"])
def test_the_viewer_is_still_at_viewer(client, path):
    """Old links, bookmarks and the CI steps that name /viewer keep working, with
    or without a trailing slash."""
    response = client.get(path)
    assert response.status_code == 200
    assert response.get_data() == _viewer_page()


def test_head_and_options_at_the_root(client):
    head = client.head("/")
    assert head.status_code == 200 and head.get_data() == b""
    options = client.options("/")
    assert options.status_code == 200
    assert {"GET", "HEAD", "OPTIONS"} <= set(options.headers["Allow"].split(", "))


def test_a_demo_station_opens_the_demo_at_its_address(client, monkeypatch):
    """Never the recording viewer: someone who types a demo station's address
    gets the page that records nothing and says so, as the station's own
    shortcut opens (#244 review)."""
    from app import server

    monkeypatch.setattr(server, "DEMO_ONLY", True)
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/demo"
    assert client.get("/?tutorial=start").headers["Location"] == "/demo?tutorial=start"
    assert '"/demo" if DEMO_ONLY else "/"' in SERVER_SOURCE, "the station's shortcut opens somewhere else"


def test_the_api_is_closed_to_demo_clients(client):
    """A demo station takes nothing in, and the routes coming to /api take models
    in from other tools (#244 review)."""
    from app import recording

    response = client.get("/api/v1/openapi.json", headers={recording.DEMO_HEADER: "1"})
    assert response.status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 200


# ---------------------------------------------------------------------------
# The integration API (#236 builds on this)
# ---------------------------------------------------------------------------

def _spec() -> dict:
    return json.loads((ROOT / "app" / "openapi.json").read_text(encoding="utf-8"))


def _api_routes() -> dict[str, set[str]]:
    """Every route under /api/v1 but the spec itself, written the way OpenAPI
    writes paths: relative to the server URL, with {name} for a parameter."""
    routes: dict[str, set[str]] = {}
    for rule in flask_app.url_map.iter_rules():
        if not rule.rule.startswith(api.API_PREFIX + "/"):
            continue
        path = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule[len(api.API_PREFIX):])
        if path == "/openapi.json":
            continue
        routes[path] = {m.lower() for m in rule.methods} - {"head", "options"}
    return routes


def test_the_api_spec_is_served_to_anyone(client):
    """Public on purpose: a tool has to be able to integrate by reading it alone."""
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_json() == _spec()


def test_the_api_spec_and_the_routing_table_agree():
    """The hand-written list this replaces had drifted from the routes. Here a
    route cannot be added without being described, or described without existing."""
    spec = _spec()
    assert spec["servers"] == [{"url": api.API_PREFIX}]
    described = {path: set(operations) for path, operations in spec["paths"].items()}
    assert described == _api_routes()


def test_the_api_spec_describes_none_of_the_viewers_own_routes():
    text = json.dumps(_spec())
    for route in ("/ingest", "/workshop", "/study", "/render", "/get_data", "/events"):
        assert f'"{route}' not in text


def test_an_error_under_the_api_is_json_in_the_documented_shape(client):
    required = set(_spec()["components"]["schemas"]["Error"]["required"])

    missing = client.get("/api/v1/no-such-thing")
    assert missing.status_code == 404
    assert missing.is_json
    assert set(missing.get_json()) == required
    assert missing.get_json()["code"] == "not_found"

    wrong_method = client.post("/api/v1/openapi.json")
    assert wrong_method.status_code == 405
    assert wrong_method.get_json()["code"] == "method_not_allowed"
    assert "GET" in wrong_method.headers["Allow"]


def test_errors_outside_the_api_are_left_alone(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert not response.is_json
    assert client.post("/viewer").status_code == 405


@pytest.mark.parametrize("path", ["/render/image", "/render/base64", "/commands",
                                  "/commands/clear", "/commands/stats", "/command"])
def test_the_removed_endpoints_are_gone(path):
    """Each of these read or wrote process-wide state shared by every window."""
    assert path not in _registered_rules()


def test_selecting_a_model_is_not_a_server_side_action(client):
    """A render names the model it wants. POST /models set one value for the whole
    server, so one window choosing a model changed what another rendered next."""
    assert client.post("/models", json={"current_model": 0}).status_code == 405
    assert client.get("/models").status_code == 200


def test_the_server_keeps_no_last_rendered_frame():
    """It was written outside the render lock and served to any caller."""
    assert "current_render: np.ndarray" not in SERVER_SOURCE
    assert "global current_render" not in SERVER_SOURCE


def test_no_dead_debug_hook():
    """last_render_debug was read here and assigned nowhere, so the client's
    camera round-trip silently never happened."""
    assert "last_render_debug" not in SERVER_SOURCE


def test_a_print_names_its_file_from_the_render_that_made_it():
    """These four values used to be left on the renderer for the print helper to
    read afterwards. On an instance shared by every window that meant a print
    could be named after somebody else's render, and a print before any
    single-mode render raised AttributeError because nothing had set them."""
    lib = (ROOT / "app" / "cad_comparison_lib.py").read_text(encoding="utf-8")
    for attribute in ("current_cut_depth", "view_current_axis",
                      "current_render_mode", "view_current_view_limits"):
        assert f"self.{attribute}" not in lib, f"{attribute} is still renderer state"

    # _save_print_if_requested decides *whether* to export; _write_print_render is
    # the write itself, reached through the recorder so a demo station produces no
    # file (see app/recording.py). The name is built in the writer.
    helper = re.search(r"def _write_print_render\(.*?\n\n\n", SERVER_SOURCE, re.S)
    assert helper, "_write_print_render not found"
    assert "result." in helper.group(0), "the filename is not built from the render result"
    assert "engine." not in helper.group(0), "still reading values off the shared engine"


def test_the_render_cache_key_covers_everything_render_reads():
    """The quantized render key is a hand-maintained list of the params that change
    the image, and the bug this PR fixed was four of them missing from it. Catch
    the next omission by construction: every param render() reads must be in the
    key, or in the small set deliberately excluded below with a reason.

    Only render()'s own source is scanned, so a param read solely inside a helper
    it calls would slip through; the direct surface is where this drift happened.
    """
    import inspect

    import app.cad_comparison_lib as cad_lib

    def _param_keys(source: str) -> set[str]:
        return set(re.findall(r'params(?:\.get\(|\[)["\'](\w+)["\']', source))

    render_reads = _param_keys(inspect.getsource(cad_lib.CADComparisonRenderer.render))

    key_fn = re.search(r"def _build_quantized_render_key\(.*?\n(?=\ndef )", SERVER_SOURCE, re.S)
    assert key_fn, "could not locate _build_quantized_render_key"
    keyed = _param_keys(key_fn.group(0))

    # Deliberately not in the key, each with why leaving it out is safe.
    EXCLUDED = {
        # The pan verb. It resolves to a new camera_center (which IS keyed), and a
        # separate guard stops a panned frame being written under an unpanned key,
        # so keying the verb itself would only prevent legitimate cache reuse.
        "move_camera_center",
    }

    missing = render_reads - keyed - EXCLUDED
    assert not missing, (
        f"render() reads these params but the quantized cache key ignores them, so "
        f"changing any would serve a stale cached image: {sorted(missing)}. Add each "
        f"to _build_quantized_render_key, or to EXCLUDED with a reason."
    )
