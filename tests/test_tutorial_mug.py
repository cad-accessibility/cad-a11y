"""The tutorial's practice mug and its landmarks belong together.

builtin_models/tutorial_mug.stl and app/tutorial_mug.landmarks.json are both
written by scripts/build_tutorial_mug.py. The lessons check a person's cut
against the landmarks' bands ("stop inside the handle loop"), and
/tutorial/locate sorts handle from body with the radius profile stored there,
so a mug edited without rebuilding the landmarks, or landmarks edited by hand,
would mark right answers wrong. These tests rebuild both from their sources and
compare, and check the facts the lesson text states about the mug.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import build_tutorial_mug as build

ROOT = Path(__file__).resolve().parents[1]
STL = ROOT / "builtin_models" / "tutorial_mug.stl"
LANDMARKS_PATH = ROOT / "app" / "tutorial_mug.landmarks.json"
LANDMARKS = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))

REQUIRED_BANDS = ("x.handle_loop", "y.handle_arms", "z.floor", "z.above_floor", "z.handle_beside_ring")


@pytest.fixture(scope="module")
def mesh():
    return build.load_mesh_as_renderer_does(STL)


def test_the_landmarks_describe_the_committed_mug():
    assert hashlib.sha256(STL.read_bytes()).hexdigest() == LANDMARKS["stl_sha256"]


def test_the_committed_mug_is_what_the_script_builds_from_mug_stl():
    """So the mug can be rebuilt, and nobody has edited it by hand."""
    assert build.build_mug() == STL.read_bytes()


def test_the_landmarks_are_what_the_script_measures_on_the_mug():
    """Every band recomputed from the STL, so a hand edit to the file, or a
    change to how bands are found that was not rebuilt, shows up here."""
    rebuilt = build.build_landmarks(STL)
    assert json.dumps(rebuilt, indent=2) + "\n" == LANDMARKS_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_the_bands_meet_and_cover_the_whole_axis(axis):
    bands = LANDMARKS["axes"][axis]
    assert bands[0]["from"] == 0.0
    assert bands[-1]["to"] == 100.0
    for before, after in itertools.pairwise(bands):
        assert before["to"] == after["from"], f"{before['name']} and {after['name']} do not meet"
    low, high = LANDMARKS["bbox_mm"][axis]
    for band in bands:
        assert band["from"] < band["to"]
        assert band["name"].startswith(f"{axis}.")
        # Millimetres are the same stretch, measured from the axis minimum.
        assert band["from_mm"] == pytest.approx(low + (high - low) * band["from"] / 100.0, abs=0.01)
        assert band["to_mm"] == pytest.approx(low + (high - low) * band["to"] / 100.0, abs=0.01)
    names = [band["name"] for band in bands]
    assert len(names) == len(set(names)), f"a band name repeats along {axis}"


def test_the_bands_the_lessons_name_exist():
    names = {band["name"] for bands in LANDMARKS["axes"].values() for band in bands}
    missing = [name for name in REQUIRED_BANDS if name not in names]
    assert not missing, f"lessons refer to bands the mug does not have: {missing}"


def test_every_band_has_a_short_spoken_description():
    for bands in LANDMARKS["axes"].values():
        for band in bands:
            text = band["description"]
            assert text and text.endswith(".") and len(text) <= 100, band["name"]
            assert "\u2014" not in text and "\u2013" not in text, band["name"]


def test_the_mug_is_95_mm_tall_and_stands_on_z_0(mesh):
    low, high = mesh.bounds
    assert low[2] == pytest.approx(0.0, abs=1e-6)
    assert high[2] - low[2] == pytest.approx(95.0, abs=0.01)
    assert LANDMARKS["units"] == "mm"
    assert LANDMARKS["bbox_mm"]["z"] == [pytest.approx(0.0), pytest.approx(95.0)]


def test_the_bodys_axis_is_on_x_0_y_0(mesh):
    """The body, not the bounding box: the handle makes the box lopsided in Y."""
    low, high = mesh.bounds
    radius = (high[0] - low[0]) / 2.0
    assert (low[0] + high[0]) / 2.0 == pytest.approx(0.0, abs=0.01)
    assert high[1] == pytest.approx(radius, abs=0.01)


def test_the_handle_points_toward_minus_y(mesh):
    """Everything outside the body's wall is on the -Y side, and reaches well
    past it. The lessons and the handle-side answers depend on this."""
    assert LANDMARKS["handle_direction"] == [0, -1, 0]
    vertices = np.asarray(mesh.vertices)
    wall = np.interp(vertices[:, 2], LANDMARKS["body_outer_radius_mm"]["z_mm"], LANDMARKS["body_outer_radius_mm"]["r_mm"])
    outside = np.hypot(vertices[:, 0], vertices[:, 1]) > wall + 1.0
    assert outside.sum() > 100, "no handle found outside the body"
    assert (vertices[outside, 1] < 0).all(), "part of the handle is on the +Y side"
    low, high = mesh.bounds
    assert -low[1] > 1.5 * high[1]


def test_the_handle_loop_is_in_the_middle_of_x(mesh):
    """The handle sits on the Y axis, so the X cut through its finger hole is
    around 50%, which is where the default pose cuts."""
    loop = next(band for band in LANDMARKS["axes"]["x"] if band["name"] == "x.handle_loop")
    assert loop["from"] < 50.0 < loop["to"]


def test_the_wall_is_thicker_than_the_handle_bar_on_the_pins():
    """Lesson 15 asks which is thicker, and the answer is the wall."""
    pins = LANDMARKS["pins_at_zoom0"]
    assert set(pins) == {"wall", "floor", "handle_bar"}
    assert pins["wall"] > pins["handle_bar"] >= 1
    assert LANDMARKS["thickness_mm"]["wall"] > LANDMARKS["thickness_mm"]["handle_bar"]


def test_the_study_mug_is_left_alone():
    """The study's onboarding still uses mug.stl, in its own units and frame."""
    study_mug = build.load_mesh_as_renderer_does(ROOT / "builtin_models" / "mug.stl")
    low, high = study_mug.bounds
    assert high[2] - low[2] == pytest.approx(0.183, abs=0.001), "mug.stl was rescaled"
    assert low[2] < -0.03, "mug.stl was moved"
