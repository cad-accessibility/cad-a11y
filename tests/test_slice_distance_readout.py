"""Where the slice is, in millimetres from the origin, next to the depth (#264).

"Depth 40%, 12mm", in the same words on every channel, for a model whose unit is
known (#234). A model whose unit is not known says the percentage only; "." says
so. The distance is signed, says "minus" rather than a sign, and the unit word
can be turned off in Settings ("Depth 40%, 12").
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import trimesh

ROOT = Path(__file__).resolve().parents[1]


def _js() -> str:
    return (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")


def _html() -> str:
    return (ROOT / "accessible-3d-viewer.html").read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", source)


def _function(name: str) -> str:
    js = _js()
    start = js.index(f"function {name}(")
    return _code_only(js[start:js.index("\n}\n", start)])


def test_the_extent_is_kept_with_the_model_it_belongs_to():
    """Each render brings its model's extent. Kept with the model's name, so
    after switching models nothing says a distance measured on the last one."""
    js = _code_only(_js())
    assert js.count("rememberSliceExtent(data);") == 2, "from /render and from the server's pushes"
    remember = _function("rememberSliceExtent")
    assert "data.bounds_mm" in remember and "data.model" in remember
    assert "extent && extent.model === shownModel()" in _function("currentSliceExtent")
    assert "currentSliceExtent()" in _function("sliceCoordinate")


def test_the_distance_is_signed_in_words_with_at_most_one_decimal():
    text = _function("distanceText")
    assert "Math.round(mm * 10) / 10" in text
    assert "'minus '" in text
    assert "viewerState.sayDistanceUnit ? 'mm' : ''" in text


def test_every_depth_readout_says_the_distance_after_the_percentage():
    announce = _function("announceDepthValue")
    assert "sliceCoordinate(to)" in announce
    assert "`Depth ${to}%${distance}`" in announce and "`${to}%${distance}`" in announce
    controls = _function("refreshDepthControls")
    assert "sliceDistanceText(depth)" in controls
    # The label names the slider, so the distance stays in its value only.
    assert "slicePercentage.textContent = `${viewerState.currentSliceDepth}%`;" in controls
    assert "sbDepth.textContent += sliceDistanceText(viewerState.currentSliceDepth);" in _function("refreshStatusBar")
    assert "sliceDistanceText(viewerState.currentSliceDepth)" in _function("applyRelativeRotation")


def test_where_am_i_says_the_slice_coordinate_or_that_the_unit_is_unknown():
    where = _function("announceWhereAmI")
    assert "Slice at ${axis} ${coordinate}." in where
    # Only once the model's own render has said so: before that it is not known yet.
    assert "} else if (viewerState.showSliceDistance && currentSliceExtent()) {" in where
    assert "'Slice distance: unit unknown.'" in where


def test_the_braille_line_says_the_depth_and_distance_together():
    """Each braille line is its own part of the text line, so a distance sent as
    a second line was never on the line with its depth (#293 review)."""
    announce = _function("announceDepthValue")
    assert "braille:" not in announce


def _pages(message: str, cells: int = 20) -> list[str]:
    js = (ROOT / "static" / "js" / "dotpad-integration.js").read_text(encoding="utf-8")
    start = js.index("function buildTactileTextPages(")
    function = js[start:js.index("\n}\n", start) + 2]
    script = f"{function}\nconsole.log(JSON.stringify(buildTactileTextPages({json.dumps(message)}, {cells})));"
    return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30,
                                     check=True).stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="runs the DotPad's own paging in node")
@pytest.mark.parametrize("message, pages", [
    ("Depth 40%, 8.7mm", ["Depth 40%, 8.7mm"]),
    ("Depth 40%, minus 8.7mm", ["Depth 40%,", "minus 8.7mm"]),
    ("Depth 100%, minus 120.5mm", ["Depth 100%,", "minus 120.5mm"]),
    ("Slice at X minus 43.6mm", ["Slice at X", "minus 43.6mm"]),
    ("a bc minus", ["a bc minus"]),
])
def test_a_part_of_the_text_line_never_ends_on_minus(message, pages):
    assert _pages(message) == pages


def test_the_unit_word_can_be_turned_off_and_stays_off():
    html = _html()
    assert re.search(r'<input type="checkbox" id="settings-distance-unit" checked>', html)
    js = _code_only(_js())
    assert "persistSetting(SETTINGS_DISTANCE_UNIT_KEY, this.checked);" in js
    init = _function("initializeAxisSettings")
    assert "viewerState.sayDistanceUnit = read(SETTINGS_DISTANCE_UNIT_KEY) !== '0';" in init


def test_an_upload_asks_for_the_unit():
    html = _html()
    choice = re.search(r'<select id="upload-units"[^>]*>(.*?)</select>', html, re.S)
    assert choice, "the unit choice is next to the upload button"
    values = re.findall(r'<option value="([^"]*)"', choice.group(1))
    assert values == ["unknown", "mm", "in", "m"], "unknown is the default, first"
    assert re.search(r'<label for="upload-units">', html), "the choice has a label"
    js = _code_only(_js())
    assert "formData.append('units', uploadUnits.value);" in js
    # Never carried over to the next file, and said back as the server kept it.
    assert "uploadUnits.value = 'unknown';" in js
    assert "UNIT_WORDS[data.units]" in js


NODE = shutil.which("node")


def _view_bases() -> dict[str, dict[str, list[float]]]:
    block = re.search(r"const VIEW_BASIS = \{(.*?)\n\};", _js(), re.S)
    found = re.findall(
        r"'([^']+)':\s*\{\s*right:\s*(\[[^\]]*\]),\s*up:\s*(\[[^\]]*\]),\s*depth:\s*(\[[^\]]*\])",
        block.group(1),
    )
    return {name: {"right": json.loads(r), "up": json.loads(u), "depth": json.loads(d)} for name, r, u, d in found}


def _viewer_coordinates(cases: list[dict], bounds: list[float], shown: bool = True) -> list[float]:
    js = _js()
    names = ("activeSliceAxis", "planePositionFromDepth", "shownModel", "currentSliceExtent", "sliceCoordinate")
    functions = "\n".join(js[js.index(f"function {n}("):js.index("\n}\n", js.index(f"function {n}(")) + 2]
                          for n in names)
    script = f"""
let lastShownModel = 'part';
const viewerState = {{ currentModel: 'part', showSliceDistance: {json.dumps(shown)},
                       sliceExtent: {{ model: 'part', mm: {json.dumps(bounds)} }} }};
{functions}
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map(c => {{
    viewerState.orientationDepth = c.depthVector;
    return sliceCoordinate(c.depth);
}})));
"""
    run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="runs the viewer's own functions in node")
def test_the_coordinate_said_is_where_the_renderer_cuts_in_every_view():
    """The viewer works the coordinate out on its own; the renderer cuts where
    depth_peeling_single_depth_with_bbox puts the plane. They have to agree for
    every view and depth, or a blind reader is told the wrong place."""
    from src.converter.plane_intersection_utils import depth_peeling_single_depth_with_bbox
    from src.converter.single_view_stl import _get_view_basis

    low, high = np.array([-3.0, -5.0, 1.0]), np.array([4.0, 2.0, 9.0])
    box = trimesh.creation.box(bounds=[low, high])
    bounds = [*low.tolist(), *high.tolist()]
    views = _view_bases()
    assert len(views) == 6
    cases = []
    for view, basis in views.items():
        depth_vector = basis["depth"]
        # What every render request carries (getOrientationPayload): the viewer's
        # own basis, with its depth vector as "forward".
        sent = {"scheme": "basis-v1", "forward": depth_vector, "up": basis["up"], "right": basis["right"]}
        normal = np.asarray(_get_view_basis(view, orientation_basis=sent)[2], dtype=float)
        axis = int(np.argmax(np.abs(depth_vector)))
        for depth in (10, 35, 90):
            _, plane_point = depth_peeling_single_depth_with_bbox(box.copy(), normal, depth=1 - depth / 100, bbox=bounds)
            cases.append({"view": view, "depthVector": depth_vector, "depth": depth,
                          "expected": float(np.asarray(plane_point)[axis])})
    said = _viewer_coordinates(cases, bounds)
    for case, coordinate in zip(cases, said):
        assert coordinate == pytest.approx(case["expected"], abs=1e-9), case


def test_a_study_can_turn_the_readout_off():
    """A study running across a release would otherwise give later participants
    the distance and earlier ones not. The protocol decides, at every model load."""
    defaults = _function("applyStudyDefaults")
    assert "if (typeof wanted.slice_distance === 'boolean') viewerState.showSliceDistance = wanted.slice_distance;" in defaults
    assert "!viewerState.showSliceDistance" in _function("sliceCoordinate")
    assert "} else if (viewerState.showSliceDistance && currentSliceExtent()) {" in _function("announceWhereAmI")


def test_the_example_study_shows_where_the_switch_is():
    from app.studies.definitions import example

    assert example.STUDY.viewer_defaults["slice_distance"] is True


@pytest.mark.skipif(NODE is None, reason="runs the viewer's own functions in node")
def test_with_the_readout_off_no_distance_is_given():
    case = {"depthVector": [0, 0, 1], "depth": 40}
    assert _viewer_coordinates([case], [0, 0, 0, 10, 10, 10], shown=False) == [None]
    assert _viewer_coordinates([case], [0, 0, 0, 10, 10, 10]) == [6.0]


def test_a_step_upload_says_its_unit_comes_from_the_file():
    """Every STEP file is in metres to the viewer, whatever unit was chosen, so
    "in metres" told someone who chose millimetres that it misread their file."""
    js = _code_only(_js())
    assert "unit read from the file" in js


def test_a_failed_upload_keeps_the_unit_chosen_for_the_retry():
    js = _js()
    start = js.index("document.getElementById('upload-model-input').addEventListener('change'")
    handler = js[start:js.index("\n});\n", start)]
    reset = handler.index("uploadUnits.value = 'unknown';")
    # In the success branch, before the failure one, and so not in finally.
    assert handler.index("if (data.status === 'success')") < reset < handler.index("`Upload failed: ")
