"""Tests for the imposed-zoom-limits calculation in CADComparisonRenderer.

Issue #47: The model appeared very small in the 96×40 tactile display because
the initial view window was set to 2× the model's bounding box, leaving the
model at ~50 % of the display height before aspect-ratio correction—and even
smaller after it.

The fix halves the initial window (0.5 × zoom_scale × dist on each side) so the
model fills the display at zoom_level=0, and applies the same 0.5 factor to the
aspect-ratio correction formulas to keep the output ratio correct.

These tests call compute_imposed_zoom_limits() directly (the same function
render() uses) rather than a hand-copied mirror of the formula, so they can't
silently drift from the real implementation.
"""

from __future__ import annotations

import math

from app.cad_comparison_lib import compute_imposed_zoom_limits

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Standard 96×40 Dot Pad screen used throughout the app.
SCREEN_W, SCREEN_H = 96, 40

# Simulated mug bounding box (after diagonal normalization + 5 % matplotlib margin).
# normalize_shapes_diagonal() scales vertices so the 3-D bbox diagonal = 1.0 unit.
# For a mug that is ~0.5 units wide and ~0.9 units tall in 2-D projection,
# matplotlib's auto-limits add ~5 % on each side, giving the values below.
MUG_HDIST = 0.55   # horizontal extent of the matplotlib axis limits
MUG_VDIST = 0.99   # vertical extent of the matplotlib axis limits
MUG_CX, MUG_CY = 0.0, 0.0  # centred at origin after normalization


def test_model_fills_at_least_80pct_of_display_height_at_zero_zoom():
    """At zoom_level=0 the mug should nearly fill the display height.

    Before the fix the model occupied ≈ 45 % of the display height.  After the
    fix it should reach ≥ 80 % (the mug is ~0.9 units tall, the Y window is
    ~0.99 units → ~91 % fill).
    """
    x_lim, y_lim = compute_imposed_zoom_limits(
        horizontal_dist=MUG_HDIST,
        vertical_dist=MUG_VDIST,
        center_x=MUG_CX,
        center_y=MUG_CY,
        zoom_level=0.0,
        screen_w=SCREEN_W,
        screen_h=SCREEN_H,
    )
    # Model actual vertical span ≈ 0.9 (without matplotlib's 5 % margin).
    model_height = 0.9
    view_height = y_lim[1] - y_lim[0]
    fill_fraction = model_height / view_height

    assert fill_fraction >= 0.80, (
        f"Model fills only {fill_fraction:.1%} of the display height at zoom=0; "
        f"expected ≥ 80 %.  View Y range: {y_lim}, model height: {model_height}"
    )


def test_aspect_ratio_is_preserved_after_correction():
    """The final X/Y range must match the screen's aspect ratio."""
    x_lim, y_lim = compute_imposed_zoom_limits(
        horizontal_dist=MUG_HDIST,
        vertical_dist=MUG_VDIST,
        center_x=MUG_CX,
        center_y=MUG_CY,
        zoom_level=0.0,
        screen_w=SCREEN_W,
        screen_h=SCREEN_H,
    )
    x_range = x_lim[1] - x_lim[0]
    y_range = y_lim[1] - y_lim[0]
    computed_ar = x_range / y_range
    expected_ar = SCREEN_W / SCREEN_H  # 2.4

    assert math.isclose(computed_ar, expected_ar, rel_tol=1e-6), (
        f"Aspect ratio mismatch: got {computed_ar:.4f}, expected {expected_ar:.4f}"
    )


def test_zoom_in_reduces_view_window():
    """zoom_level=1 should show half the model (2× zoom-in)."""
    _, y_lim_0 = compute_imposed_zoom_limits(
        horizontal_dist=MUG_HDIST,
        vertical_dist=MUG_VDIST,
        center_x=MUG_CX,
        center_y=MUG_CY,
        zoom_level=0.0,
        screen_w=SCREEN_W,
        screen_h=SCREEN_H,
    )
    _, y_lim_1 = compute_imposed_zoom_limits(
        horizontal_dist=MUG_HDIST,
        vertical_dist=MUG_VDIST,
        center_x=MUG_CX,
        center_y=MUG_CY,
        zoom_level=1.0,
        screen_w=SCREEN_W,
        screen_h=SCREEN_H,
    )
    view_height_0 = y_lim_0[1] - y_lim_0[0]
    view_height_1 = y_lim_1[1] - y_lim_1[0]

    # zoom_level=1 → zoom_scale=0.5 → view should be half of zoom_level=0.
    assert math.isclose(view_height_1, view_height_0 * 0.5, rel_tol=1e-9), (
        f"zoom_level=1 Y range {view_height_1:.4f} should be half of "
        f"zoom_level=0 range {view_height_0:.4f}"
    )


def test_wide_model_aspect_ratio_preserved():
    """A wide model (wider than screen) must also have correct aspect ratio."""
    # Simulate a wide flat plate: 0.9 × 0.3 (wide × tall).
    x_lim, y_lim = compute_imposed_zoom_limits(
        horizontal_dist=0.99,   # 0.9 * 1.1 margin
        vertical_dist=0.33,     # 0.3 * 1.1 margin
        center_x=0.0,
        center_y=0.0,
        zoom_level=0.0,
        screen_w=SCREEN_W,
        screen_h=SCREEN_H,
    )
    x_range = x_lim[1] - x_lim[0]
    y_range = y_lim[1] - y_lim[0]
    computed_ar = x_range / y_range
    expected_ar = SCREEN_W / SCREEN_H  # 2.4

    assert math.isclose(computed_ar, expected_ar, rel_tol=1e-6), (
        f"Wide-model aspect ratio: got {computed_ar:.4f}, expected {expected_ar:.4f}"
    )
