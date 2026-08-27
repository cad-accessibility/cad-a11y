"""The endpoints the server offers, and the state it does not keep.

Endpoints were removed here because nothing called them and each one reached into
process-wide state that made one window's actions visible to another:
``/render/image`` and ``/render/base64`` served whichever frame any window rendered
last, and ``POST /models`` set a single "current model" for everyone.

The index route is checked against the real routing table because it is the only
description of this API that exists, so a stale entry is a documentation bug that
nothing else would catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = (ROOT / "app" / "server.py").read_text(encoding="utf-8")


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _advertised_endpoints() -> set[str]:
    block = re.search(r'"endpoints":\s*\{(.*?)\n\s*\}', SERVER_SOURCE, re.S)
    assert block, "the index route no longer lists endpoints"
    return set(re.findall(r'"(/[^"]*)":', block.group(1)))


def _registered_rules() -> set[str]:
    return {rule.rule for rule in flask_app.url_map.iter_rules()}


def test_the_index_only_advertises_endpoints_that_exist():
    """A removed endpoint left in this list is a promise the server cannot keep."""
    registered = _registered_rules()
    missing = sorted(name for name in _advertised_endpoints() if name not in registered)
    assert not missing, f"advertised but not routed: {missing}"


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
