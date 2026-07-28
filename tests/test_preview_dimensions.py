"""Both previews must describe the display that is actually connected (#52).

The tactile preview already reported the device's size, because the frame is
rendered at that size. The high-fidelity preview took its aspect from the
renderer's default instead, so with a DotPad attached the two previews disagreed
with each other and one of them disagreed with the device.

The Monarch was excluded from the device-size path entirely. These pin why that
exclusion was wrong: a Monarch *is* the default grid, and excluding it was what
sent it down a second-render path.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

import app.server as server
from app.braille_display import _MONARCH_COLS, _MONARCH_LINES

ROOT = Path(__file__).resolve().parents[1]
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
DOTPAD_JS = ROOT / "static" / "js" / "dotpad-integration.js"

# A braille cell is 2 pixels wide and 4 tall.
CELL_W, CELL_H = 2, 4
DOTPAD_GRID = (60, 40)  # 30x10 cells


def _params(**overrides):
    base = {
        "view": "x+", "depth": 50, "renderMode": "Filled", "zoom": 0,
        "mode": "single", "current_model": 0,
    }
    base.update(overrides)
    return base


# --- The grid the client names -------------------------------------------


def test_monarch_grid_is_the_default_grid():
    """The whole reason excluding the Monarch was pointless."""
    assert (_MONARCH_COLS * CELL_W, _MONARCH_LINES * CELL_H) == (96, 40)


@pytest.mark.parametrize(
    "params,expected",
    [
        (_params(target_pixel_width=60, target_pixel_height=40), (60, 40)),
        (_params(target_pixel_width=96, target_pixel_height=40), (96, 40)),
        (_params(), None),
        (_params(target_pixel_width=0, target_pixel_height=40), None),
        (_params(target_pixel_width=-5, target_pixel_height=40), None),
        (_params(target_pixel_width="wide", target_pixel_height=40), None),
        (_params(target_pixel_width=96), None),
    ],
)
def test_target_grid_reads_one_way(params, expected):
    """One reader, so the render and both previews cannot parse it differently."""
    assert server._target_grid(params) == expected


def test_a_malformed_size_does_not_reach_the_renderer():
    """A zero or negative size used to slip past into a second render, where it
    would have been used as a pixel dimension."""
    assert server._target_grid(_params(target_pixel_width=0, target_pixel_height=0)) is None


# --- The Monarch is no longer a special case ------------------------------


def test_monarch_is_not_excluded_from_the_device_size_path():
    """Excluding it left render_size unset, which is exactly what sent it down
    the second-render branch the exclusion was meant to avoid."""
    source = server.__file__ and Path(server.__file__).read_text()
    assert "is_monarch" not in source, "the Monarch special case is back"


def test_monarch_and_default_ask_for_the_same_thing():
    monarch = server._target_grid(_params(target_pixel_width=96, target_pixel_height=40))
    assert monarch == (96, 40)


# --- Both previews describe the same render -------------------------------


def test_hifi_preview_follows_the_named_grid(monkeypatch):
    """It used to take its aspect from the renderer default, so with a DotPad
    attached it silently kept showing a 96x40-shaped preview."""
    captured = {}

    def fake_payload(_params, *, model_index, pixel_width, pixel_height, use_cache=True):
        captured["size"] = (pixel_width, pixel_height)
        return np.zeros((pixel_height, pixel_width), dtype=np.uint8)

    monkeypatch.setattr(server, "get_or_create_renderer", lambda *_a, **_k: type("E", (), {"screen_size": [96, 40]})())
    monkeypatch.setattr(server, "_get_braille_payload_at_size", fake_payload)

    server._make_hifi_preview(
        _params(target_pixel_width=60, target_pixel_height=40), 0, preview_width=600
    )
    width, height = captured["size"]
    assert width == 600
    # 60x40 is 3:2, so a 600px-wide preview is 400 tall. The old code produced
    # 250, the 96x40 default aspect, regardless of what was connected.
    assert height == 400


def test_hifi_preview_falls_back_to_the_renderer_default(monkeypatch):
    captured = {}

    def fake_payload(_params, *, model_index, pixel_width, pixel_height, use_cache=True):
        captured["size"] = (pixel_width, pixel_height)
        return np.zeros((pixel_height, pixel_width), dtype=np.uint8)

    monkeypatch.setattr(server, "get_or_create_renderer", lambda *_a, **_k: type("E", (), {"screen_size": [96, 40]})())
    monkeypatch.setattr(server, "_get_braille_payload_at_size", fake_payload)

    server._make_hifi_preview(_params(), 0, preview_width=960)
    assert captured["size"] == (960, 400)


# --- The client side ------------------------------------------------------


def _viewer_source():
    return VIEWER_JS.read_text(encoding="utf-8")


def test_the_single_shared_slot_is_gone():
    """One global for two devices is why disconnecting one wiped the other."""
    for path in (VIEWER_JS, DOTPAD_JS):
        assert "connectedTactileDisplay" not in path.read_text(encoding="utf-8"), path.name


def test_devices_are_registered_under_their_own_keys():
    dotpad = DOTPAD_JS.read_text(encoding="utf-8")
    assert "setTactileDisplay?.('dotpad'" in dotpad
    # Clearing must name the device, so a co-connected one survives.
    assert "setTactileDisplay?.('dotpad', null)" in dotpad


def test_registering_a_display_triggers_a_render():
    """Otherwise the size only reaches the server on the user's next action, which
    is why connecting a display appeared to do nothing until you moved."""
    source = _viewer_source()
    block = re.search(r"function setTactileDisplay\(.*?\n\}", source, re.DOTALL)
    assert block, "setTactileDisplay not found"
    assert "sendStateToServer" in block.group(0)


def test_the_active_grid_follows_the_effective_output_device():
    """Reading a device-agnostic global is what let the previews describe a
    display that was not the one receiving output."""
    source = _viewer_source()
    block = re.search(r"function activeTactileGrid\(\).*?\n\}", source, re.DOTALL)
    assert block, "activeTactileGrid not found"
    assert "getEffectiveOutputDevice()" in block.group(0)


def test_both_previews_use_one_caption_formatter():
    """They printed the same numbers in opposite orders, which is how the flipped
    dimensions went unnoticed."""
    source = _viewer_source()
    # Call sites only; the definition also contains the name.
    assert source.count("= previewCaption(shape)") == 2
    block = re.search(r"function previewCaption\(shape\).*?\n\}", source, re.DOTALL)
    assert block, "previewCaption not found"
    body = block.group(0)
    # numpy reports [height, width]; the caption reads width x height.
    assert "shape[1]" in body and "shape[0]" in body
    assert body.index("shape[1]") < body.index("shape[0]"), "dimensions are the wrong way round"
