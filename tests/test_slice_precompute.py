"""Tests for the lazy slice-graph precompute trigger and its not-ready fallback.

The precompute is expensive and now runs lazily (kicked off the first time a
slice-graph profile is requested) instead of eagerly for every model. These
tests lock in that behavior so a regression can't silently re-enable eager
precompute or reintroduce the KeyError the old fallback raised while precompute
was still in flight.
"""

import numpy as np

import app.cad_comparison_lib as cad_lib


ZOOM = [[0.0, 1.0], [0.0, 1.0]]


def _bare_renderer():
    """A renderer instance without loading any model files."""
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)
    renderer.view_cut_polygons = {}
    renderer.view_diff_mats = {}
    renderer.view_slice_pixel_counts = {}
    return renderer


def test_slice_profile_kicks_off_precompute_lazily(monkeypatch):
    """Requesting a slice-graph profile is what starts the deferred precompute."""
    renderer = _bare_renderer()
    calls = []
    monkeypatch.setattr(renderer, "start_background_slice_precompute", lambda: calls.append(1))

    renderer._get_zoom_filtered_slice_profile("top", 50, ZOOM)

    assert calls, "expected _get_zoom_filtered_slice_profile to start precompute lazily"


def test_slice_profile_returns_flat_profile_when_nothing_ready(monkeypatch):
    """Before precompute finishes there is no data for the view; the profile must
    degrade to a flat 101-length array instead of raising KeyError/IndexError."""
    renderer = _bare_renderer()
    monkeypatch.setattr(renderer, "start_background_slice_precompute", lambda: None)

    profile = renderer._get_zoom_filtered_slice_profile("top", 50, ZOOM)

    assert isinstance(profile, np.ndarray)
    assert profile.shape == (101,)
    assert not profile.any()  # all zeros, and crucially no crash


def test_slice_profile_falls_back_to_diff_row_when_polygons_missing(monkeypatch):
    """If the pairwise diff matrix is ready but the per-slice polygons are not,
    the profile returns the anchor row of the diff matrix."""
    renderer = _bare_renderer()
    monkeypatch.setattr(renderer, "start_background_slice_precompute", lambda: None)
    diff = np.zeros((101, 101))
    diff[50] = np.linspace(0.0, 1.0, 101)
    renderer.view_diff_mats = {"top": diff}

    profile = renderer._get_zoom_filtered_slice_profile("top", 50, ZOOM)

    np.testing.assert_array_equal(profile, diff[50])


def test_slice_area_profile_kicks_off_precompute_lazily(monkeypatch):
    """Slice Area mode is a slice-graph consumer too, so requesting its profile
    must also start the deferred precompute, not only the Difference path."""
    renderer = _bare_renderer()
    calls = []
    monkeypatch.setattr(renderer, "start_background_slice_precompute", lambda: calls.append(1))

    renderer._get_slice_pixel_count_profile("top")

    assert calls, "expected _get_slice_pixel_count_profile to start precompute lazily"


# ---------------------------------------------------------------------------
# Slice Area rasterization
#
# _count_raised_pixels reimplements get_single_view's pixel placement instead of
# sharing it, so the two can drift apart silently: the graph would keep producing
# plausible numbers that no longer describe what is on the display. Comparing
# against a real get_single_view render is not practical here (it goes through
# matplotlib at dpi=800, so the comparison would be slow and sensitive to
# anti-aliasing), so these pin the part that actually encodes the coupling: the
# set_aspect('equal') correction, exercised with window aspects deliberately far
# from the 96x40 grid in both directions.
# ---------------------------------------------------------------------------

from shapely.geometry import box as shapely_box

GRID = [96, 40]  # grid aspect 2.4


def _counter(x_limits, y_limits, screen=GRID):
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)
    renderer.screen_size = screen
    renderer.view_limits = np.array([[list(x_limits), list(y_limits)]] * 6)
    renderer._get_view_index = lambda _key: 0
    return renderer


def test_count_raised_pixels_matches_rendered_output():
    """A square window is taller than the grid, so the window widens, not the shape.

    Window 1x1 on a 96x40 grid: data aspect 1.0 is below the grid's 2.4, so
    set_aspect('equal') grows the x window to 2.4 and keeps it centred. The unit
    square then spans the full 40 rows and 40 of the 96 columns, giving 41x40
    filled pixels once PIL's inclusive boundary is counted.

    Without the correction the square would stretch across the whole grid at
    3840, which is the failure this pins.
    """
    count = _counter((0.0, 1.0), (0.0, 1.0))._count_raised_pixels(
        shapely_box(0, 0, 1, 1), "top"
    )
    assert count == 41 * 40
    assert count < GRID[0] * GRID[1] / 2, "shape was stretched to the grid aspect"


def test_count_raised_pixels_grows_the_short_axis_the_other_way():
    """A window wider than the grid grows in y instead, so the shape loses rows."""
    counter = _counter((0.0, 10.0), (0.0, 1.0))
    count = counter._count_raised_pixels(shapely_box(0, 0, 10, 1), "top")

    # x fills all 96 columns; y occupies 1 of the corrected 10/2.4 window height.
    expected_rows = round(1.0 / (10.0 / 2.4) * GRID[1])
    assert abs(count - GRID[0] * expected_rows) <= GRID[0], (
        f"expected roughly {GRID[0] * expected_rows} pixels, got {count}"
    )
    assert count < GRID[0] * GRID[1], "shape filled the grid; aspect was ignored"


def test_count_raised_pixels_scales_with_shape_area():
    """A shape covering a fifth of the window's width covers a fifth of its columns."""
    counter = _counter((0.0, 1.0), (0.0, 1.0))
    full = counter._count_raised_pixels(shapely_box(0, 0, 1, 1), "top")
    fifth = counter._count_raised_pixels(shapely_box(0.4, 0.0, 0.6, 1.0), "top")
    assert fifth < full
    assert abs(fifth / full - 0.2) < 0.05, f"{fifth} is not about a fifth of {full}"


def test_count_raised_pixels_handles_empty_and_degenerate_geometry():
    """Razor-thin slices produce lines rather than polygons; they must not raise."""
    from shapely.geometry import GeometryCollection, LineString, Polygon

    counter = _counter((0.0, 1.0), (0.0, 1.0))
    assert counter._count_raised_pixels(None, "top") == 0
    assert counter._count_raised_pixels(Polygon(), "top") == 0
    assert counter._count_raised_pixels(
        GeometryCollection([LineString([(0, 0), (1, 1)])]), "top"
    ) == 0


def test_slice_area_profile_flags_its_placeholder(monkeypatch):
    """Before the counts exist the profile is a flat placeholder, and says so, the
    same way the Difference profile does. The client refreshes only on
    slicegraph_ready being false, so without this a Slice Area graph opened while
    precompute runs would stay a flat line."""
    renderer = _bare_renderer()
    monkeypatch.setattr(renderer, "start_background_slice_precompute", lambda: None)

    profile = renderer._get_slice_pixel_count_profile("top")
    assert not profile.any()
    assert renderer.slicegraph_ready is False

    renderer.view_slice_pixel_counts = {"top": [0, 5, 10]}
    profile = renderer._get_slice_pixel_count_profile("top")
    np.testing.assert_array_equal(profile, [0.0, 0.5, 1.0])
    assert renderer.slicegraph_ready is True


# ---------------------------------------------------------------------------
# The counts in the precompute cache
#
# Slice Area reads the counts and nothing else, so a cache that restores the
# difference matrices and cut polygons without them brings the Difference graph
# back after a restart while Slice Area stays flat.
# ---------------------------------------------------------------------------

import gzip
import json
from pathlib import Path

CUBE = Path(__file__).resolve().parents[1] / "builtin_models" / "cube.stl"


def _cold_renderer(tmp_path):
    cold = cad_lib.CADComparisonRenderer(str(CUBE), str(CUBE))
    cold.cache_path = str(tmp_path / "cube.json.gz")
    cold.start_background_slice_precompute()
    assert cold._precompute_done.wait(600), "precompute did not finish"
    assert cold.view_slice_pixel_counts, "precompute produced no counts"
    return cold


def _warm_renderer(cache_path):
    warm = cad_lib.CADComparisonRenderer(str(CUBE), str(CUBE))
    warm.cache_path = cache_path
    return warm


def test_precompute_cache_brings_the_slice_pixel_counts_back(tmp_path):
    cold = _cold_renderer(tmp_path)

    warm = _warm_renderer(cold.cache_path)
    assert warm.load_precompute_cache()
    assert warm.view_slice_pixel_counts == cold.view_slice_pixel_counts


def test_a_cache_without_the_counts_is_a_miss(tmp_path):
    """A version-3 file, from before the counts were stored, must be recomputed
    rather than half restored. So must a current one that has lost them."""
    cold = _cold_renderer(tmp_path)
    with gzip.open(cold.cache_path, "rt", encoding="utf-8") as fp:
        payload = json.load(fp)
    assert payload["cache_version"] > 3, (
        "adding the counts without bumping the version leaves version-3 files "
        "readable but missing them"
    )
    del payload["slice_pixel_counts"]

    for version in (3, payload["cache_version"]):
        payload["cache_version"] = version
        with gzip.open(cold.cache_path, "wt", encoding="utf-8") as fp:
            json.dump(payload, fp)
        assert not _warm_renderer(cold.cache_path).load_precompute_cache(), version
