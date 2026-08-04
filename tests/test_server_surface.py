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

    helper = re.search(r"def _save_print_if_requested\(.*?\n\n\n", SERVER_SOURCE, re.S)
    assert helper, "_save_print_if_requested not found"
    assert "result." in helper.group(0), "the filename is not built from the render result"
    assert "engine." not in helper.group(0), "still reading values off the shared engine"
