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
from app.cad_comparison_lib import DEFAULT_SCREEN_SIZE

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
    assert (_MONARCH_COLS * CELL_W, _MONARCH_LINES * CELL_H) == DEFAULT_SCREEN_SIZE


def test_the_named_default_is_what_a_renderer_actually_starts_at():
    """The server falls back to DEFAULT_SCREEN_SIZE whenever an engine reports no
    size of its own, so the two have to agree. They were four separate copies of
    the same literal, across two files, with nothing tying them together."""
    assert tuple(DEFAULT_SCREEN_SIZE) == (96, 40), (
        "changing this changes what every caller that names no size renders at"
    )

    lib = (Path(__file__).resolve().parents[1] / "app" / "cad_comparison_lib.py").read_text()
    assert "self.screen_size = list(DEFAULT_SCREEN_SIZE)" in lib, (
        "the renderer sets its own default from a literal again"
    )
    assert "self.screen_size = [96,40]" not in lib
    assert "self.screen_size = [96, 40]" not in lib


def test_the_server_keeps_no_copy_of_the_default_grid():
    """A second literal is how the fallback and the renderer drift apart."""
    source = (Path(__file__).resolve().parents[1] / "app" / "server.py").read_text()
    assert "[96, 40]" not in source, "server.py still hardcodes the default grid"
    assert "DEFAULT_SCREEN_SIZE" in source


def test_the_clients_no_device_default_is_a_different_thing():
    """78x40 is the client's answer to 'nothing is connected'. It deliberately
    differs from the renderer's own default, so the two must not be merged."""
    viewer = (Path(__file__).resolve().parents[1] / "static" / "js" / "viewer.js").read_text()
    block = re.search(r"const DEFAULT_TACTILE_GRID = Object\.freeze\(\{(.*?)\}\)", viewer, re.S)
    assert block, "DEFAULT_TACTILE_GRID not found"
    assert "pixelWidth: 78" in block.group(1)
    assert tuple(DEFAULT_SCREEN_SIZE) != (78, 40)


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


def test_the_active_grid_follows_the_display_that_receives_the_frame():
    """The output-device setting is a preference, not a statement about what is
    plugged in: it defaults to the Monarch whether or not one is attached, while a
    connected DotPad is sent every frame regardless of it. Keying only on the
    preference meant a DotPad plugged in under the default setting received frames
    shaped for something else.
    """
    source = _viewer_source()
    block = re.search(r"function activeTactileGrid\(\).*?\n\}", source, re.DOTALL)
    assert block, "activeTactileGrid not found"
    body = block.group(0)
    assert "getEffectiveOutputDevice()" in body, "the selected device should win when connected"
    # The fallback that makes a lone connected display count.
    assert "length === 1" in body, (
        "a single connected display must be used even when it is not the selected one"
    )


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


# --- The default grid, and when the caption may name it --------------------


def test_default_grid_sits_between_the_two_displays():
    """With nothing connected there is no right answer, so the default should not
    favour either display. It also has to be a whole number of braille cells,
    which are two pixels wide."""
    source = _viewer_source()
    block = re.search(r"const DEFAULT_TACTILE_GRID = Object\.freeze\(\{(.*?)\}\)", source, re.DOTALL)
    assert block, "DEFAULT_TACTILE_GRID not found"
    body = block.group(1)
    width = int(re.search(r"pixelWidth:\s*(\d+)", body).group(1))
    height = int(re.search(r"pixelHeight:\s*(\d+)", body).group(1))

    monarch_w = _MONARCH_COLS * CELL_W
    dotpad_w = DOTPAD_GRID[0]
    assert dotpad_w < width < monarch_w, f"{width} does not sit between {dotpad_w} and {monarch_w}"
    assert width % CELL_W == 0, f"{width} is not a whole number of braille cells"
    assert height == 40


def test_the_default_grid_drives_the_render_not_just_the_caption():
    """If the caption said one size and the render used another, this whole change
    would have reintroduced the mismatch it exists to remove."""
    source = _viewer_source()
    block = re.search(r"target_pixel_width:(.*?)target_pixel_height:(.*?)\n", source, re.DOTALL)
    assert block, "the render request does not name a grid"
    assert "activeTactileGrid()" in block.group(1)


def test_the_caption_describes_the_render_on_screen_not_the_live_connection():
    """Connecting a display updates the registry immediately, but the render it
    describes arrives later. Naming the new display against the old dimensions is
    what made the caption briefly self-contradictory."""
    source = _viewer_source()
    caption = re.search(r"function previewCaption\(shape\).*?\n\}", source, re.DOTALL).group(0)
    assert "lastRenderedGrid" in caption, "caption reads live state instead of the applied render"
    assert "activeTactileGrid()" not in caption

    # And the applied render's grid is recorded as the response is applied.
    assert "lastRenderedGrid = gridForSize(" in source


# --- Caching must not serve a frame of the wrong size ----------------------


def test_render_caches_distinguish_the_target_size():
    """A request for 60x40 and one for 78x40 are different renders.

    Both caches ignored the target size, so connecting a display returned the
    previous size's frame and the preview then reported that stale size against
    the new display's name. Harmless while the size rarely changed; not once it
    drives the whole render.
    """
    base = _params(view="x+", depth=50)
    small = dict(base, target_pixel_width=60, target_pixel_height=40)
    large = dict(base, target_pixel_width=78, target_pixel_height=40)

    _, _, _, fp_small = server._prepare_render_params(small)
    _, _, _, fp_large = server._prepare_render_params(large)
    assert fp_small != fp_large, "the exact-render cache would serve the wrong size"

    key_small = server._build_quantized_render_key(small, model_index=0)
    key_large = server._build_quantized_render_key(large, model_index=0)
    assert key_small != key_large, "the coarse cache would serve the wrong size"


def test_the_same_size_still_shares_a_cache_entry():
    """The keys must distinguish sizes without defeating caching entirely."""
    a = _params(view="x+", depth=50, target_pixel_width=60, target_pixel_height=40)
    b = _params(view="x+", depth=50, target_pixel_width=60, target_pixel_height=40)
    assert server._build_quantized_render_key(a, model_index=0) == server._build_quantized_render_key(b, model_index=0)


# --- Robustness of the display registry -----------------------------------


def _viewer_source() -> str:
    return (Path(__file__).resolve().parents[1] / "static" / "js" / "viewer.js").read_text()


def test_the_registry_is_keyed_the_same_way_it_is_read():
    """Normalising only where entries are written would file a device under a key
    nothing looks up, which is the mismatch this is meant to prevent."""
    source = _viewer_source()

    write = re.search(r"function setTactileDisplay\(deviceKey, info\) \{(.*?)\n\}", source, re.S)
    assert write and "displayKey(deviceKey)" in write.group(1), (
        "setTactileDisplay does not normalise the key it stores under"
    )

    read = re.search(r"function activeTactileGrid\(\) \{(.*?)\n\}", source, re.S)
    assert read and "displayKey(getEffectiveOutputDevice())" in read.group(1), (
        "activeTactileGrid looks up a key it did not normalise"
    )


def test_the_two_integrations_register_under_keys_the_lookup_can_find():
    """The keys are internal literals, so this is the check that they still line
    up with what getEffectiveOutputDevice returns."""
    root = Path(__file__).resolve().parents[1] / "static" / "js"
    registered = set()
    for name in ("dotpad-integration.js", "monarch-hid.js"):
        registered |= set(re.findall(r"setTactileDisplay\?\.\('([^']+)'", (root / name).read_text()))

    assert registered == {"dotpad", "monarch_hid"}, registered
    for key in registered:
        assert key == key.lower(), f"{key} would only be found after normalisation"


def test_sizes_are_matched_as_numbers():
    """A device reporting "60" instead of 60 would match nothing and read as no
    display connected, which looks like a hardware fault."""
    body = re.search(r"function gridForSize\(width, height\) \{(.*?)\n\}", _viewer_source(), re.S)
    assert body, "gridForSize not found"
    assert "Number(entry.pixelWidth)" in body.group(1)
    assert "Number(entry.pixelHeight)" in body.group(1)


def test_the_caption_never_prints_undefined_for_a_missing_label():
    """Entries come from integrations that all set a label, but the registry is
    exported on window and takes whatever it is given."""
    body = re.search(r"function previewCaption\(shape\) \{(.*?)\n\}", _viewer_source(), re.S)
    assert body, "previewCaption not found"
    assert "lastRenderedGrid.label" in body.group(1)
    assert "||" in body.group(1), "no fallback when a device registers without a label"
