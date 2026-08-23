"""Tests for issue #186: model scale must not change when the model is rotated.

The key invariant: at a given zoom level, render() must produce the same
viewport extent (framing_bounds) regardless of how the model's projected
2D bounding box changes.  Scale must depend only on the 3D bounding box
and the zoom level — never on the model's current orientation.

Before the fix render() used the projected 2D extent (horizontal_dist /
vertical_dist from view_limits) as the reference for zoom scaling, so
rotating a long stick from vertical to horizontal caused the display to
expand significantly, making the model appear much smaller.

After the fix the viewport is anchored to the longest 3D bbox dimension along
the display's vertical extent, with horizontal following the display's aspect
ratio from that -- constant for a given model and display, so rotation cannot
affect the apparent size. This is unconditional: render() never resizes the
viewport to compensate for a projected extent that happens to be larger (see
test_framing_bounds_ignore_the_projected_extent_entirely below).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import app.cad_comparison_lib as cad_lib


SCREEN_W, SCREEN_H = 96, 40

BASE_PARAMS = {
    "view": "y-",
    "zoom": "0",
    "depth": 0,
    "renderMode": "Outline",
    "mode": "single",
    "shape": "after",
}


def _make_renderer(monkeypatch, view_limits_2x2, bbox):
    """Create a minimal fake CADComparisonRenderer for framing-bound tests.

    view_limits_2x2 is [[x_min, x_max], [y_min, y_max]] for the view
    corresponding to BASE_PARAMS["view"] (front / "y-" → index 1).
    """
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)
    renderer.screen_size = [SCREEN_W, SCREEN_H]
    renderer.shapes = [object(), object()]
    renderer.bbox = bbox
    xmin_b, ymin_b, zmin_b, xmax_b, ymax_b, zmax_b = bbox
    renderer.longest_3d_dim = max(xmax_b - xmin_b, ymax_b - ymin_b, zmax_b - zmin_b)
    # view_limits must be indexable as renderer.view_limits[view_index]
    renderer.view_limits = np.array([view_limits_2x2 for _ in range(6)])

    def fake_get_single_view(*args, screen_size=None, **kwargs):
        w, h = screen_size
        img = np.full((h, w, 4), 128, dtype=np.uint8)
        img[:, :, 3] = 255
        return img, None

    monkeypatch.setattr(cad_lib, "get_single_view", fake_get_single_view)
    return renderer


# ---------------------------------------------------------------------------
# Core invariant: viewport extent is the same regardless of projected shape
# ---------------------------------------------------------------------------

def test_framing_bounds_unchanged_when_projected_extent_changes(monkeypatch):
    """Simulates rotating a long stick (10 × 1 × 1 bbox) from a vertical
    to a horizontal projected orientation.

    'Tall' view_limits: projected 2D box is narrow and tall  (1.1 × 10.5).
    'Wide' view_limits: projected 2D box is wide and short   (10.5 × 1.1).

    Both must produce identical framing_bounds — the viewport the image is
    rendered into — confirming that model scale is locked to the 3D bbox."""
    bbox = [-0.5, -0.5, -5.0, 0.5, 0.5, 5.0]   # 1×1×10, longest_3d_dim = 10

    # Vertical orientation: stick spans most of the view height.
    tall_limits = [[-0.55, 0.55], [-5.25, 5.25]]
    # Horizontal orientation: stick spans most of the view width.
    wide_limits = [[-5.25, 5.25], [-0.55, 0.55]]

    result_tall = _make_renderer(monkeypatch, tall_limits, bbox).render(BASE_PARAMS)
    result_wide = _make_renderer(monkeypatch, wide_limits, bbox).render(BASE_PARAMS)

    x_span_tall = result_tall.framing_bounds[0][1] - result_tall.framing_bounds[0][0]
    x_span_wide = result_wide.framing_bounds[0][1] - result_wide.framing_bounds[0][0]
    y_span_tall = result_tall.framing_bounds[1][1] - result_tall.framing_bounds[1][0]
    y_span_wide = result_wide.framing_bounds[1][1] - result_wide.framing_bounds[1][0]

    assert math.isclose(x_span_tall, x_span_wide, rel_tol=1e-6), (
        f"Horizontal viewport span changed with rotation: "
        f"tall={x_span_tall:.4f}, wide={x_span_wide:.4f}"
    )
    assert math.isclose(y_span_tall, y_span_wide, rel_tol=1e-6), (
        f"Vertical viewport span changed with rotation: "
        f"tall={y_span_tall:.4f}, wide={y_span_wide:.4f}"
    )


def test_framing_bounds_aspect_ratio_matches_display(monkeypatch):
    """The fixed-scale viewport must always respect the display aspect ratio."""
    bbox = [-0.5, -0.5, -5.0, 0.5, 0.5, 5.0]
    limits = [[-0.55, 0.55], [-5.25, 5.25]]

    result = _make_renderer(monkeypatch, limits, bbox).render(BASE_PARAMS)

    x_span = result.framing_bounds[0][1] - result.framing_bounds[0][0]
    y_span = result.framing_bounds[1][1] - result.framing_bounds[1][0]
    computed_ar = x_span / y_span
    expected_ar = SCREEN_W / SCREEN_H  # 2.4

    assert math.isclose(computed_ar, expected_ar, rel_tol=1e-6), (
        f"Viewport aspect ratio {computed_ar:.4f} != display aspect {expected_ar:.4f}"
    )


def test_framing_bounds_scale_anchored_to_longest_3d_dim(monkeypatch):
    """At zoom=0 the longest 3D bbox dimension must exactly fill the shortest
    display dimension in model-space units."""
    # Use a non-cube shape so the longest dimension is unambiguous.
    # longest_3d_dim = zmax - zmin = 10, shortest display dim = screen_h = 40
    bbox = [-0.5, -0.5, -5.0, 0.5, 0.5, 5.0]
    longest_3d_dim = 10.0

    limits = [[-0.55, 0.55], [-5.25, 5.25]]
    result = _make_renderer(monkeypatch, limits, bbox).render(BASE_PARAMS)

    # y_span should equal longest_3d_dim when screen_h is the shorter dimension.
    y_span = result.framing_bounds[1][1] - result.framing_bounds[1][0]
    assert math.isclose(y_span, longest_3d_dim, rel_tol=1e-6), (
        f"Vertical viewport span {y_span:.4f} should equal longest 3D dim "
        f"{longest_3d_dim:.4f} at zoom=0."
    )


def test_zoom_in_halves_framing_bounds(monkeypatch):
    """zoom_level=1 (zoom_scale=0.5) must halve the viewport extent produced
    by the fixed-scale formula."""
    bbox = [-0.5, -0.5, -5.0, 0.5, 0.5, 5.0]
    limits = [[-0.55, 0.55], [-5.25, 5.25]]
    renderer = _make_renderer(monkeypatch, limits, bbox)

    result0 = renderer.render(dict(BASE_PARAMS, zoom="0"))
    result1 = renderer.render(dict(BASE_PARAMS, zoom="1"))

    y0 = result0.framing_bounds[1][1] - result0.framing_bounds[1][0]
    y1 = result1.framing_bounds[1][1] - result1.framing_bounds[1][0]

    assert math.isclose(y1, y0 * 0.5, rel_tol=1e-9), (
        f"zoom=1 y-span {y1:.4f} should be half of zoom=0 y-span {y0:.4f}"
    )


def test_framing_bounds_ignore_the_projected_extent_entirely(monkeypatch):
    """The fixed-scale viewport is unconditionally longest_3d_dim -- it is never
    resized based on view_limits, even when the projected extent (here
    deliberately much larger, as a non-90-degree orientation_basis sent
    straight to the API could produce) would exceed it. Scale changes only via
    an explicit user zoom action, never automatically to keep something in
    frame -- a real UI rotation can never actually reach this case, since
    roll/pitch/yaw are always 90-degree swaps of two basis axes."""
    bbox = [-0.5, -0.5, -0.5, 0.5, 0.5, 0.5]  # cube, longest_3d_dim = 1.0
    limits = [[-0.55, 0.55], [-0.825, 0.825]]  # projected extent (1.5) > longest_3d_dim

    result = _make_renderer(monkeypatch, limits, bbox).render(BASE_PARAMS)

    y_span = result.framing_bounds[1][1] - result.framing_bounds[1][0]
    assert math.isclose(y_span, 1.0, rel_tol=1e-6), (
        f"y-span {y_span:.4f} should stay exactly longest_3d_dim (1.0) "
        "regardless of the projected extent"
    )
