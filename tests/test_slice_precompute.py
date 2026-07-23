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
