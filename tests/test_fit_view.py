"""Tests for fitting the current cut slice to the tactile display.

These tests avoid loading real CAD files. They exercise CADComparisonRenderer
with a small fake slice mesh so the fit math can be checked directly.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

import app.cad_comparison_lib as cad_lib


def _renderer_for_fit():
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)
    renderer.shapes = [object(), object()]
    renderer.bbox = [0, 0, 0, 10, 10, 10]
    renderer.longest_3d_dim = 10.0
    renderer.screen_size = [40, 40]
    renderer.view_limits = np.array(
        [
            [[0.0, 10.0], [0.0, 10.0]],  # top
            [[0.0, 10.0], [0.0, 10.0]],  # front
            [[0.0, 10.0], [0.0, 10.0]],  # left
            [[0.0, 10.0], [0.0, 10.0]],  # bottom
            [[0.0, 10.0], [0.0, 10.0]],  # back
            [[0.0, 10.0], [0.0, 10.0]],  # right
        ],
        dtype=float,
    )
    renderer.view_default_camera_center = np.array(
        [[5.0, 5.0]] * 6,
        dtype=float,
    )
    return renderer


def test_fit_view_uses_only_vertices_referenced_by_slice_faces(monkeypatch):
    renderer = _renderer_for_fit()

    # The first four vertices make a small 1x1 current slice.
    # The final vertex is far away but unused by faces. If compute_fit_view uses
    # all vertices, it will think the slice is huge and return little/no zoom.
    fake_slice = SimpleNamespace(
        vertices=np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
                [0.0, 1.0, 0.0],
                [100.0, 100.0, 0.0],
            ],
            dtype=float,
        ),
        faces=np.array(
            [
                [0, 1, 2],
                [0, 2, 3],
            ],
            dtype=int,
        ),
    )

    monkeypatch.setattr(cad_lib, "get_cut_faces", lambda *args, **kwargs: fake_slice)

    fit = renderer.compute_fit_view(
        {
            "view": "z+",
            "depth": 50,
            "shape": "after",
            "mode": "single",
            "compose_scrollbar": False,
        }
    )

    assert fit["camera_center"] == [0.5, 0.5]
    assert fit["zoom"] > 1.0


def test_fit_view_falls_back_when_slice_has_no_faces(monkeypatch):
    renderer = _renderer_for_fit()
    fake_slice = SimpleNamespace(
        vertices=np.zeros((0, 3), dtype=float),
        faces=np.zeros((0, 3), dtype=int),
    )

    monkeypatch.setattr(cad_lib, "get_cut_faces", lambda *args, **kwargs: fake_slice)

    fit = renderer.compute_fit_view(
        {
            "view": "z+",
            "depth": 50,
            "zoom": 2.5,
        }
    )

    assert fit["zoom"] == 2.5
    assert fit["camera_center"] == [5.0, 5.0]


def test_fit_view_passes_current_depth_to_slice_helper(monkeypatch):
    renderer = _renderer_for_fit()
    captured = {}

    fake_slice = SimpleNamespace(
        vertices=np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
            ],
            dtype=float,
        ),
        faces=np.array([[0, 1, 2]], dtype=int),
    )

    def fake_get_cut_faces(shape, view_name, cut_depth, bbox, orientation_basis=None):
        captured["view_name"] = view_name
        captured["cut_depth"] = cut_depth
        captured["orientation_basis"] = orientation_basis
        return fake_slice

    monkeypatch.setattr(cad_lib, "get_cut_faces", fake_get_cut_faces)

    renderer.compute_fit_view({"view": "z+", "depth": 25})

    assert captured["view_name"] == "top"
    assert math.isclose(captured["cut_depth"], 0.75)

# --- #173: fit measures what render() draws -----------------------------------

FRONT_BASIS = {"scheme": "basis-v1", "forward": [0, -1, 0], "up": [0, 0, 1], "right": [1, 0, 0]}


def test_fit_view_cuts_and_projects_along_the_orientation_sent(monkeypatch):
    """Once the model has been turned the view token no longer says which way
    the display faces; the basis does. Fitting used to slice and project by the
    token alone, so after a turn it measured a slice through a different axis."""
    renderer = _renderer_for_fit()
    captured = {}
    # A 1 x 1 square standing in the XZ plane, off to one side: what a cut seen
    # from the front would find. Seen from the top it would be a line.
    fake_slice = SimpleNamespace(
        vertices=np.array(
            [[2.0, 5.0, 3.0], [3.0, 5.0, 3.0], [3.0, 5.0, 4.0], [2.0, 5.0, 4.0]],
            dtype=float,
        ),
        faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=int),
    )

    def fake_get_cut_faces(shape, view_name, cut_depth, bbox, orientation_basis=None):
        captured["orientation_basis"] = orientation_basis
        return fake_slice

    monkeypatch.setattr(cad_lib, "get_cut_faces", fake_get_cut_faces)

    fit = renderer.compute_fit_view({"view": "z+", "depth": 50, "orientation": FRONT_BASIS})

    assert captured["orientation_basis"] == FRONT_BASIS, "the cut ignored the orientation"
    # Front puts X to the right and Z up, so the square is centred at (x, z).
    assert fit["camera_center"] == [2.5, 3.5]


def test_the_fitted_zoom_frames_the_slice_in_renders_own_window(monkeypatch):
    """render() sizes its zoom-0 window from the model's longest dimension, not
    from the view's projected extent. Fit used the extent, so for any model
    longer along the viewing axis than across it, the zoom it returned framed a
    window of the wrong size: here, twice the slice."""
    renderer = _renderer_for_fit()
    renderer.longest_3d_dim = 20.0  # deeper than it is wide: view extent 10, longest 20
    fake_slice = SimpleNamespace(
        vertices=np.array([[4.0, 4.0, 0.0], [6.0, 4.0, 0.0], [6.0, 6.0, 0.0], [4.0, 6.0, 0.0]]),
        faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=int),
    )
    monkeypatch.setattr(cad_lib, "get_cut_faces", lambda *args, **kwargs: fake_slice)

    fit = renderer.compute_fit_view({"view": "z+", "depth": 50}, screen_size=[40, 40])

    # What render() would draw at that zoom and centre, on a 40 x 40 display.
    x_lim, y_lim = cad_lib.compute_imposed_zoom_limits(
        20.0, 20.0, fit["camera_center"][0], fit["camera_center"][1], fit["zoom"], 40, 40
    )
    assert x_lim == [pytest.approx(4.0), pytest.approx(6.0)]
    assert y_lim == [pytest.approx(4.0), pytest.approx(6.0)]
