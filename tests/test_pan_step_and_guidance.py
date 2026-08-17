"""Tests for the arrow-key pan step size and out-of-frame guidance (#153).

Before this fix, w/a/s/d moved the CAMERA by 50% of the active viewport per
press, which meant a pan could carry the object fully out of frame with no
way to tell -- short of trial and error -- which way to press to get it
back. This covers the two server-side pieces of the fix:

  * Step size: apply_pan_step moves the camera by exactly the caller-supplied
    scale times the active extent, and render() computes that scale as
    0.25 * zoom_scale, not 0.5 * zoom_scale, so 75% of what was visible
    stays visible after one press.
  * Guidance: compute_pan_guidance flags when the object has gone fully out
    of frame and reports which way to move the OBJECT to bring it back, so
    the client can announce that instead of leaving the reader stuck.

See tests/test_pan_direction_wording.py for the browser-side half: the w/a/s/d
handlers send the OPPOSITE literal camera_move from the direction they
announce (moving the camera down makes the object appear to move up), which
is why apply_pan_step itself does not need to know about "move object"
wording at all -- it only ever moves the camera.

These call the real functions render() uses (module-level, and via a real
CADComparisonRenderer on the builtin cube), not a hand-copied mirror.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

import app.cad_comparison_lib as cad_lib
from app.cad_comparison_lib import apply_pan_step, compute_pan_guidance

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "builtin_models" / "cube.stl"


# ---------------------------------------------------------------------------
# apply_pan_step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("camera_move,expected_axis_sign", [
    ("left", (-1.0, 0.0)),
    ("right", (1.0, 0.0)),
    ("up", (0.0, 1.0)),
    ("down", (0.0, -1.0)),
])
def test_each_direction_moves_the_expected_axis_the_expected_way(camera_move, expected_axis_sign):
    """left/right move x by +/-horizontal_dist, up/down move y by +/-vertical_dist,
    scaled independently by pan_step_scale -- never the wrong axis."""
    center = apply_pan_step([0.0, 0.0], camera_move, pan_step_scale=1.0,
                             horizontal_dist=2.0, vertical_dist=4.0)
    sign_x, sign_y = expected_axis_sign
    assert math.isclose(center[0], sign_x * 2.0)
    assert math.isclose(center[1], sign_y * 4.0)


def test_none_and_unrecognized_moves_leave_the_center_unchanged():
    for camera_move in ("none", "sideways", ""):
        center = apply_pan_step([1.5, -2.5], camera_move, pan_step_scale=1.0,
                                 horizontal_dist=10.0, vertical_dist=10.0)
        assert center == [1.5, -2.5]


def test_the_step_is_25_percent_of_the_active_viewport_not_50():
    """The regression this exists for (#153): the caller-supplied scale is
    0.25 * zoom_scale, not 0.5 * zoom_scale. apply_pan_step just applies
    whatever scale it's handed, so this pins the ratio between the two."""
    zoom_scale = 1.0
    horizontal_dist = 8.0
    old_buggy_scale = 0.5 * zoom_scale
    fixed_scale = 0.25 * zoom_scale

    old_center = apply_pan_step([0.0, 0.0], "right", old_buggy_scale, horizontal_dist, 1.0)
    fixed_center = apply_pan_step([0.0, 0.0], "right", fixed_scale, horizontal_dist, 1.0)

    assert math.isclose(fixed_center[0], old_center[0] / 2.0)
    assert math.isclose(fixed_center[0], 0.25 * horizontal_dist)


def test_four_presses_one_way_are_undone_by_four_the_other_way():
    center = [0.0, 0.0]
    for _ in range(4):
        center = apply_pan_step(center, "right", 0.25, horizontal_dist=6.0, vertical_dist=6.0)
    for _ in range(4):
        center = apply_pan_step(center, "left", 0.25, horizontal_dist=6.0, vertical_dist=6.0)
    assert math.isclose(center[0], 0.0, abs_tol=1e-9)
    assert math.isclose(center[1], 0.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# compute_pan_guidance
# ---------------------------------------------------------------------------


def _guidance(camera_center, half_w=5.0, half_h=5.0, obj_x=(-4.0, 4.0), obj_y=(-4.0, 4.0)):
    return compute_pan_guidance(
        obj_x_range=obj_x, obj_y_range=obj_y,
        viewport_center=camera_center,
        viewport_half_w=half_w, viewport_half_h=half_h,
    )


def test_object_fully_inside_the_viewport_is_not_out_of_frame():
    out_of_frame, directions = _guidance((0.0, 0.0))
    assert out_of_frame is False
    assert directions == ()


def test_object_only_partly_overlapping_is_not_out_of_frame():
    """viewport is [3, 13] here, object is [-4, 4]: they still share [3, 4].
    Reduced, but not lost, so "no pan" guidance must not fire yet."""
    out_of_frame, directions = _guidance((8.0, 0.0))
    assert out_of_frame is False
    assert directions == ()


def test_exactly_touching_the_edge_still_counts_as_in_frame():
    """viewport_x_min lands exactly on obj_x_max: one shared edge, not a gap."""
    out_of_frame, directions = compute_pan_guidance(
        obj_x_range=(-4.0, 4.0), obj_y_range=(-4.0, 4.0),
        viewport_center=(9.0, 0.0),  # viewport_x = [4, 14]
        viewport_half_w=5.0, viewport_half_h=5.0,
    )
    assert out_of_frame is False
    assert directions == ()


@pytest.mark.parametrize("camera_center,expected_direction", [
    # Panning the CAMERA a given way makes the object appear to drift the
    # opposite way on screen -- but the guidance is phrased in "move object"
    # terms (see #153), so the reported direction actually matches the way
    # the camera moved: it names the press that would undo the drift.
    ((20.0, 0.0), "right"),   # camera moved far +x -> object fell off the left edge
    ((-20.0, 0.0), "left"),   # camera moved far -x -> object fell off the right edge
    ((0.0, 20.0), "up"),      # camera moved far +y -> object fell off the bottom edge
    ((0.0, -20.0), "down"),   # camera moved far -y -> object fell off the top edge
])
def test_out_of_frame_on_one_axis_reports_a_single_recovery_direction(camera_center, expected_direction):
    out_of_frame, directions = _guidance(camera_center)
    assert out_of_frame is True
    assert directions == (expected_direction,)


def test_out_of_frame_on_both_axes_reports_both_directions_smallest_overhang_first():
    """Object is way off horizontally (overhang 91) and only slightly off
    vertically (overhang 11). Both are reported -- a pan on either axis
    alone would still leave the object out of frame on the other -- ordered
    smallest overhang first, so a reader who only acts on the first word
    still makes progress toward recovery."""
    out_of_frame, directions = compute_pan_guidance(
        obj_x_range=(-4.0, 4.0), obj_y_range=(-4.0, 4.0),
        viewport_center=(100.0, 20.0),
        viewport_half_w=5.0, viewport_half_h=5.0,
    )
    assert out_of_frame is True
    assert directions == ("up", "right")


# ---------------------------------------------------------------------------
# Wired into render(): 25% step size and RenderResult guidance fields
# ---------------------------------------------------------------------------

BASE_PARAMS = {
    "view": "y-",  # -> "front" via _map_view_name
    "zoom": "0",
    "depth": 0,
    "renderMode": "Outline",
    "mode": "single",
}


@pytest.fixture()
def renderer():
    return cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))


def _front_horizontal_dist(built):
    view_name = built._map_view_name(BASE_PARAMS["view"])
    view_index = built._get_view_index(view_name)
    limits = built._limits_for_orientation(None, view_index, view_name)
    return limits, abs(limits[0][1] - limits[0][0])


def test_render_actually_uses_a_25_percent_step(renderer):
    """Regression test at the render() level, not just the pure function:
    proves render() computes pan_step_scale as 0.25 * zoom_scale rather than
    silently keeping the old 0.5 factor around."""
    limits, horizontal_dist = _front_horizontal_dist(renderer)
    start_center = renderer._default_camera_center(limits)

    result = renderer.render(dict(BASE_PARAMS, move_camera_center="right"))

    # zoom_level 0 -> zoom_scale 1.0, so the expected step is exactly 25% of
    # horizontal_dist.
    expected_delta = 0.25 * horizontal_dist
    assert math.isclose(result.camera_center[0] - start_center[0], expected_delta, rel_tol=1e-6)


def test_a_single_pan_at_zero_zoom_does_not_lose_the_object(renderer):
    result = renderer.render(dict(BASE_PARAMS, move_camera_center="left"))
    assert result.object_out_of_frame is False
    assert result.pan_guidance_directions == []


def test_panning_far_enough_flags_the_object_out_of_frame_with_a_recovery_direction(renderer):
    """At zoom_level 0 (zoom_scale=1), each press moves the camera by 25% of
    the extent; the viewport and object stop overlapping once the camera has
    moved more than 0.5*(1+zoom_scale) = 1.0x the extent, i.e. after more
    than 4 presses. This holds for any extent, so it isn't tied to the
    cube's specific size."""
    result = renderer.render(dict(BASE_PARAMS, move_camera_center="left"))
    for _ in range(5):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="left",
                                       camera_center=result.camera_center))

    assert result.object_out_of_frame is True
    assert result.pan_guidance_directions == ["left"]


def test_panning_out_on_both_axes_reports_both_recovery_directions(renderer):
    """Pan far enough left AND far enough up (via successive presses along
    each axis) that the object is lost on both, and confirm render() reports
    both directions rather than just one -- the actual case the two-arrow /
    "pan X and Y" behavior exists for."""
    result = renderer.render(dict(BASE_PARAMS, move_camera_center="left"))
    for _ in range(5):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="left",
                                       camera_center=result.camera_center))
    for _ in range(6):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="up",
                                       camera_center=result.camera_center))

    assert result.object_out_of_frame is True
    assert set(result.pan_guidance_directions) == {"left", "up"}
    assert len(result.pan_guidance_directions) == 2


def test_two_out_of_frame_directions_draw_two_arrows_not_one_diagonal_one(renderer):
    """Visual counterpart to the guidance-list test above: out on both axes
    should paint an arrow near BOTH the affected edges (top, for "up", and
    left, for "left"), not a single diagonal marker -- the controls are
    strictly cardinal, so a diagonal arrow wouldn't correspond to any single
    keypress. Also checks the edges with no guidance stay clear."""
    result = renderer.render(dict(BASE_PARAMS, move_camera_center="left"))
    for _ in range(5):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="left",
                                       camera_center=result.camera_center))
    for _ in range(6):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="up",
                                       camera_center=result.camera_center))
    assert set(result.pan_guidance_directions) == {"left", "up"}

    arrow_color = np.array([220, 30, 30])
    is_arrow_pixel = np.all(result.image[:, :, 0:3] == arrow_color, axis=-1)

    assert is_arrow_pixel[:4, :].any(), "expected an 'up' arrow near the top edge"
    assert is_arrow_pixel[:, :4].any(), "expected a 'left' arrow near the left edge"
    assert not is_arrow_pixel[-4:, :].any(), "no 'down' guidance was reported; nothing should be drawn there"
    assert not is_arrow_pixel[:, -4:].any(), "no 'right' guidance was reported; nothing should be drawn there"


def test_panning_back_the_other_way_clears_the_out_of_frame_flag(renderer):
    """Confirms the flag reflects the current render, not sticky state left
    over from a previous one -- it must not live as an instance attribute on
    the renderer, which is shared by every window on a model (see the
    RenderResult docstring)."""
    result = renderer.render(dict(BASE_PARAMS, move_camera_center="left"))
    for _ in range(5):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="left",
                                       camera_center=result.camera_center))
    assert result.object_out_of_frame is True

    for _ in range(6):
        result = renderer.render(dict(BASE_PARAMS, move_camera_center="right",
                                       camera_center=result.camera_center))

    assert result.object_out_of_frame is False
    assert result.pan_guidance_directions == []
