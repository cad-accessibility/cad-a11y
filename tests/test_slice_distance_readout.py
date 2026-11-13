"""Where the slice is, in millimetres from the origin, next to the depth (#264).

"Depth 40%, 12mm", in the same words on every channel, for a model whose unit is
known (#234). A model whose unit is not known says the percentage only; "." says
so. The distance is signed, says "minus" rather than a sign, and the unit word
can be turned off in Settings ("Depth 40%, 12").
"""

from __future__ import annotations

import re
from pathlib import Path

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
    coordinate = _function("sliceCoordinate")
    assert "extent.model !== shownModel()" in coordinate
    assert "planePositionFromDepth(" in coordinate


def test_the_distance_is_signed_in_words_with_at_most_one_decimal():
    text = _function("distanceText")
    assert "Math.round(mm * 10) / 10" in text
    assert "'minus '" in text
    assert "viewerState.sayDistanceUnit ? 'mm' : ''" in text


def test_every_depth_readout_says_the_distance_after_the_percentage():
    announce = _function("announceDepthValue")
    assert "sliceDistanceText(to)" in announce
    assert "`Depth ${to}%${distance}`" in announce and "`${to}%${distance}`" in announce
    controls = _function("refreshDepthControls")
    assert "sliceDistanceText(depth)" in controls
    assert "sbDepth.textContent += sliceDistanceText(viewerState.currentSliceDepth);" in _function("refreshStatusBar")
    assert "sliceDistanceText(viewerState.currentSliceDepth)" in _function("applyRelativeRotation")


def test_where_am_i_says_the_slice_coordinate_or_that_the_unit_is_unknown():
    where = _function("announceWhereAmI")
    assert "Slice at ${axis} ${coordinate}." in where
    assert "'Unit unknown.'" in where


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
    assert "formData.append('units', uploadUnits.value);" in _code_only(_js())
