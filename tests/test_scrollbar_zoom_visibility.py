"""Scrollbars are hidden at base zoom (#150).

At zoom_level 0 the whole model extent is already on screen, so the
scrollbar thumb would always span the full track — it conveys nothing.
render() now suppresses compose_scrollbar in that case regardless of the
client's toggle, and lets it through again once zoomed in.

Uses the same bare-renderer + monkeypatched get_single_view pattern as
test_dotpad_cursor_rendering.py, so no real geometry needs to be loaded.
"""

from __future__ import annotations

import numpy as np

import app.cad_comparison_lib as cad_lib

BASE_RGB = 128


def _make_renderer(monkeypatch, width=30, height=20):
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)

    renderer.screen_size = [width, height]
    renderer.shapes = [object(), object()]
    renderer.bbox = None
    renderer.view_limits = [
        [[0, 10], [0, 10]] for _ in range(6)
    ]
    renderer.view_current_camera_center = [[5, 5] for _ in range(6)]

    def fake_get_single_view(*args, screen_size=None, **kwargs):
        w, h = screen_size
        image = np.full((h, w, 4), BASE_RGB, dtype=np.uint8)
        image[:, :, 3] = 255
        return image, None

    monkeypatch.setattr(cad_lib, "get_single_view", fake_get_single_view)
    return renderer


def _render_with_scrollbar(monkeypatch, zoom):
    renderer = _make_renderer(monkeypatch)
    params = {
        "view": "x+",
        "depth": 50,
        "zoom": zoom,
        "renderMode": "Outline",
        "mode": "single",
        "shape": "after",
        "compose_scrollbar": True,
        "compose_slicegraph": False,
    }
    return renderer.render(params)


def test_scrollbar_hidden_at_zero_zoom_even_when_requested(monkeypatch):
    image = _render_with_scrollbar(monkeypatch, zoom=0)

    assert np.all(image[:, -1, :3] == BASE_RGB)
    assert np.all(image[-1, :, :3] == BASE_RGB)


def test_scrollbar_shown_once_zoomed_in(monkeypatch):
    image = _render_with_scrollbar(monkeypatch, zoom=1)

    assert not np.all(image[:, -1, :3] == BASE_RGB)
    assert not np.all(image[-1, :, :3] == BASE_RGB)
