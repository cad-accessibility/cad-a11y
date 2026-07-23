"""Tests for fitting the current cut slice to the tactile display.

These tests avoid loading real CAD files. They exercise CADComparisonRenderer
with a small fake slice mesh so the fit math can be checked directly.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

import app.cad_comparison_lib as cad_lib


def _renderer_for_fit():
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)
    renderer.shapes = [object(), object()]
    renderer.bbox = [0, 0, 0, 10, 10, 10]
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
    renderer.view_current_camera_center = np.array(
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

    def fake_get_cut_faces(shape, view_name, cut_depth, bbox):
        captured["view_name"] = view_name
        captured["cut_depth"] = cut_depth
        return fake_slice

    monkeypatch.setattr(cad_lib, "get_cut_faces", fake_get_cut_faces)

    renderer.compute_fit_view({"view": "z+", "depth": 25})

    assert captured["view_name"] == "top"
    assert math.isclose(captured["cut_depth"], 0.75)