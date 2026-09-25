"""Slice-graph precompute is computed once and reused across restarts.

Six views by a hundred and one depths of mesh boolean per model, so a cold run is
seconds per model and the whole built-in set is minutes. That is affordable only
if it survives a restart.

It did not. The cache was written to one filename shared by every model, with no
model in it, so each renderer overwrote the last. It was opened only for writing
and read back nowhere, and it lived inside the image rather than on the data
volume, so anything written vanished on the next build.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import numpy as np
import pytest

import app.cad_comparison_lib as cad_lib

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "builtin_models" / "cube.stl"


@pytest.fixture()
def renderer(tmp_path, monkeypatch):
    """A renderer whose cache lands in a temp directory."""
    built = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    built.cache_path = str(tmp_path / "cube.json.gz")
    return built


def _precompute(built):
    built.start_background_slice_precompute()
    assert built._precompute_done.wait(600), "precompute did not finish"


def test_the_cache_is_written_where_it_will_survive_a_restart():
    """Under data/, which is the mounted volume. The old path was inside the
    image, so every rebuild threw the work away."""
    built = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    parts = Path(built.cache_path).parts
    assert "data" in parts and "renders" in parts, built.cache_path
    assert "app" not in parts[:-1], "still writing inside the image"


def test_each_model_gets_its_own_file():
    """One shared filename meant the last model to finish overwrote every other."""
    cube = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    other_path = ROOT / "builtin_models" / "lego_2x3.stl"
    if not other_path.exists():
        pytest.skip("second built-in model not present")
    other = cad_lib.CADComparisonRenderer(str(other_path), str(other_path))
    assert cube.cache_path != other.cache_path


def test_a_second_run_reuses_the_result_instead_of_recomputing(renderer, tmp_path):
    _precompute(renderer)
    assert os.path.exists(renderer.cache_path)

    warm = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    warm.cache_path = renderer.cache_path

    def fail_if_called():
        raise AssertionError("recomputed despite a usable cache")

    warm._compute_slice_graphs = fail_if_called
    warm.start_background_slice_precompute()
    assert warm._precompute_done.wait(60)
    assert warm._slice_graphs_ready


def test_what_comes_back_is_what_went_in(renderer):
    """Both halves matter. The difference matrices alone leave the slice graph on
    its flat fallback, because the profile is computed from the cut polygons."""
    _precompute(renderer)

    warm = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    warm.cache_path = renderer.cache_path
    assert warm.load_precompute_cache()

    assert set(warm.view_diff_mats) == set(renderer.view_diff_mats)
    assert set(warm.view_cut_polygons) == set(renderer.view_cut_polygons)
    for view, matrix in renderer.view_diff_mats.items():
        assert np.array_equal(np.asarray(matrix), warm.view_diff_mats[view])
    for view, polygons in renderer.view_cut_polygons.items():
        assert len(warm.view_cut_polygons[view]) == len(polygons)
        assert all(a.equals(b) for a, b in zip(polygons, warm.view_cut_polygons[view]))


def test_a_changed_model_does_not_read_the_old_result(renderer, tmp_path):
    """Signature covers size and mtime, so an edited file misses rather than
    rendering somebody's stale slices."""
    _precompute(renderer)

    with gzip.open(renderer.cache_path, "rt", encoding="utf-8") as fp:
        payload = json.load(fp)
    payload["model_signature"]["after"]["size"] += 1
    with gzip.open(renderer.cache_path, "wt", encoding="utf-8") as fp:
        json.dump(payload, fp)

    warm = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    warm.cache_path = renderer.cache_path
    assert not warm.load_precompute_cache()


def test_an_older_cache_version_is_ignored(renderer):
    _precompute(renderer)
    with gzip.open(renderer.cache_path, "rt", encoding="utf-8") as fp:
        payload = json.load(fp)
    payload["cache_version"] = 1
    with gzip.open(renderer.cache_path, "wt", encoding="utf-8") as fp:
        json.dump(payload, fp)

    warm = cad_lib.CADComparisonRenderer(str(MODEL), str(MODEL))
    warm.cache_path = renderer.cache_path
    assert not warm.load_precompute_cache()


@pytest.mark.parametrize("content", [b"", b"not gzip at all", gzip.compress(b"{]")])
def test_a_damaged_cache_is_a_miss_not_a_crash(renderer, tmp_path, content):
    """A cache that lies is worse than no cache, and a truncated write must not
    stop the model rendering."""
    damaged = tmp_path / "damaged.json.gz"
    damaged.write_bytes(content)
    renderer.cache_path = str(damaged)
    assert renderer.load_precompute_cache() is False


def test_an_unwritable_location_does_not_break_rendering(renderer, tmp_path):
    """Best effort: a read-only or full disk costs speed, not the model."""
    renderer.cache_path = str(tmp_path / "nope" / "\0bad" / "x.json.gz")
    renderer.view_diff_mats = {"top": np.zeros((101, 101))}
    renderer.view_cut_polygons = {"top": []}
    renderer._save_precompute_cache(renderer._model_signature())


def test_nothing_is_left_behind_when_a_write_fails(renderer, tmp_path):
    """Written to a temporary name and renamed, so a reader never sees half a
    file and a failure leaves no litter."""
    renderer.cache_path = str(tmp_path / "out.json.gz")
    renderer.view_diff_mats = {"top": np.zeros((2, 2))}
    renderer.view_cut_polygons = {"top": []}
    renderer._save_precompute_cache(renderer._model_signature())
    assert not list(tmp_path.glob("*.tmp"))
