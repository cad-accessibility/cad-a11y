"""A payload request at the size already rendered must not render again.

When a client names a target pixel size, the server renders a second time to
produce the payload. Nothing checked whether that size differed from the one just
rendered, so a device reporting the default grid paid for two identical renders on
every interaction. #114 hit this with the Monarch reporting the same 96x40 the
default already uses. It was worked around on the client by not reporting the size,
which left the server free to do it again for the next matching device (#117).

These count renders rather than inspecting the response, because the defect is
invisible in the output: both renders produce the same pixels. Only the count
distinguishes fixed from broken.
"""

from __future__ import annotations

import numpy as np
import pytest

import app.server as server


class _CountingEngine:
    """Stands in for the renderer, counting renders and the size of each."""

    def __init__(self):
        self.screen_size = [96, 40]
        self.renders: list[tuple[int, int]] = []
        self.last_render_debug = None

    def render(self, _params):
        width, height = int(self.screen_size[0]), int(self.screen_size[1])
        self.renders.append((width, height))
        return np.zeros((height, width, 4), dtype=np.uint8)


@pytest.fixture()
def engine(monkeypatch):
    counting = _CountingEngine()
    monkeypatch.setattr(server, "get_or_create_renderer", lambda *_a, **_k: counting)
    monkeypatch.setattr(server, "_normalize_model_index", lambda *_a, **_k: 0)
    monkeypatch.setattr(server, "_refresh_model_list_if_stale", lambda: None)
    monkeypatch.setattr(server, "_save_print_if_requested", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_send_to_braille_display", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(server.db, "record_render", lambda **_k: None)
    # Each test starts from an empty cache so a hit cannot be mistaken for the guard.
    with server.preview_payload_cache_lock:
        server.preview_payload_cache.clear()
    return counting


def _params(**overrides):
    base = {
        "view": "x+", "depth": 50, "renderMode": "Filled", "zoom": 0,
        "mode": "single", "current_model": 0,
    }
    base.update(overrides)
    return base


def test_matching_size_renders_once(engine):
    """The case from #114: the device reports exactly the default grid."""
    server._render_response(
        _params(output_device="monarch_hid", target_pixel_width=96, target_pixel_height=40),
        source="test",
    )
    assert len(engine.renders) == 1, (
        f"expected one render, got {len(engine.renders)}: {engine.renders}"
    )


def test_differing_size_still_renders_twice(engine):
    """The guard must not suppress a payload genuinely needed at another size."""
    server._render_response(
        _params(output_device="monarch_hid", target_pixel_width=60, target_pixel_height=40),
        source="test",
    )
    assert len(engine.renders) == 2, "a different size still needs its own render"
    assert (60, 40) in engine.renders


def test_no_target_size_renders_once(engine):
    server._render_response(_params(), source="test")
    assert len(engine.renders) == 1


def test_reused_payload_matches_the_size_asked_for(engine):
    """Reuse is only valid if the pixels really are at the requested size."""
    response = server._render_response(
        _params(output_device="monarch_hid", target_pixel_width=96, target_pixel_height=40),
        source="test",
    )
    height, width = response["image_shape"][0], response["image_shape"][1]
    assert (width, height) == (96, 40)


def test_reuse_populates_the_cache_for_the_follow_up_request(engine):
    """The dotpad-hex follow-up looks the size up; skipping the render must not
    leave that lookup empty, or the saving is undone by the next request."""
    params = _params(output_device="monarch_hid", target_pixel_width=96, target_pixel_height=40)
    server._render_response(params, source="test")

    cached = server._get_preview_payload_cached(
        server._build_preview_payload_cache_key(
            params, model_index=0, pixel_width=96, pixel_height=40
        )
    )
    assert cached is not None, "the follow-up request would render again"


def test_guard_compares_against_the_size_actually_rendered(engine):
    """A non-default grid must be compared against, not a hardcoded 96x40."""
    engine.screen_size = [60, 40]
    server._render_response(
        _params(output_device="monarch_hid", target_pixel_width=60, target_pixel_height=40),
        source="test",
    )
    assert len(engine.renders) == 1
    assert engine.renders[0] == (60, 40)


@pytest.mark.parametrize("width,height", [(0, 40), (96, 0), (-1, 40), ("wide", 40), (None, 40)])
def test_unusable_target_sizes_fall_back_to_one_render(engine, width, height):
    """A malformed size must not raise or trigger a second render."""
    server._render_response(
        _params(output_device="monarch_hid", target_pixel_width=width, target_pixel_height=height),
        source="test",
    )
    assert len(engine.renders) == 1


def test_screen_size_is_restored_after_rendering(engine):
    server._render_response(
        _params(output_device="monarch_hid", target_pixel_width=60, target_pixel_height=40),
        source="test",
    )
    assert engine.screen_size == [96, 40], "the engine was left at the temporary size"
