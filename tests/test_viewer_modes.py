"""Guardrails against drift between the JS mode arrays and the HTML radio values.

The viewer stores its mode names in two places that nothing links: the
`renderModes` / `representationModes` arrays in static/js/viewer.js, and the
`value` attributes of the matching radio inputs in accessible-3d-viewer.html.
`syncRadios()` pairs them with a case-sensitive `===`, then fails silently when
no radio matches, leaving the radiogroup with nothing checked.

Two regressions shipped green because nothing checked this invariant:
  f50f3ce  radio value="Filled" against JS 'Shaded'
  2f8799b  radio value="x-ray"  against JS 'X-Ray'  (PR #64)

Parsing is stdlib-only on purpose: the repo has no package.json and no JS test
runner, and bs4/lxml are not in requirements.txt.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWER_JS = REPO_ROOT / "static" / "js" / "viewer.js"
VIEWER_HTML = REPO_ROOT / "accessible-3d-viewer.html"

# (radio group `name`, JS array holding the same modes)
MODE_GROUPS = [
    ("render-mode", "renderModes"),
    ("view-mode", "representationModes"),
]


class _RadioCollector(HTMLParser):
    """Collect radio inputs as {name: [(value, is_checked), ...]}."""

    def __init__(self) -> None:
        super().__init__()
        self.radios: dict[str, list[tuple[str | None, bool]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attrib = dict(attrs)
        if attrib.get("type") != "radio":
            return
        name = attrib.get("name")
        if name is None:
            return
        self.radios.setdefault(name, []).append(
            (attrib.get("value"), "checked" in attrib)
        )


def _js_string_array(source: str, name: str) -> list[str]:
    """Extract the string literals from `const <name> = ['a', 'b'];`."""
    match = re.search(
        rf"const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*;", source, re.DOTALL
    )
    # Assert the parse itself so regex brittleness fails loudly instead of
    # silently passing against an empty list.
    assert match, f"could not parse `const {name} = [...]` from {VIEWER_JS.name}"
    return re.findall(r"""['"]([^'"]+)['"]""", match.group(1))


@pytest.fixture(scope="module")
def radios() -> dict[str, list[tuple[str | None, bool]]]:
    collector = _RadioCollector()
    collector.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return collector.radios


@pytest.fixture(scope="module")
def js_source() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


@pytest.mark.parametrize(("radio_name", "js_array"), MODE_GROUPS)
def test_radio_values_are_known_js_modes(radios, js_source, radio_name, js_array):
    """Every radio value must be an element of its JS array.

    This is the invariant syncRadios() depends on and never verifies.
    """
    expected = _js_string_array(js_source, js_array)
    group = radios.get(radio_name)
    assert group, f"no radio inputs found with name={radio_name!r}"

    unknown = [value for value, _ in group if value not in expected]
    assert not unknown, (
        f"radio name={radio_name!r} has value(s) {unknown} absent from {js_array} "
        f"({expected}). syncRadios() compares case-sensitively, so these radios can "
        f"never be checked."
    )


@pytest.mark.parametrize(("radio_name", "js_array"), MODE_GROUPS)
def test_every_js_mode_has_exactly_one_radio(radios, js_source, radio_name, js_array):
    """Bijection between the JS array and the radio group: no orphans, no duplicates."""
    expected = _js_string_array(js_source, js_array)
    group = radios.get(radio_name)
    assert group, f"no radio inputs found with name={radio_name!r}"

    values = [value for value, _ in group]
    missing = [mode for mode in expected if mode not in values]
    assert not missing, (
        f"{js_array} entries {missing} have no radio in name={radio_name!r}"
    )

    duplicates = sorted({v for v in values if values.count(v) > 1})
    assert not duplicates, (
        f"radio name={radio_name!r} has duplicate value(s) {duplicates}"
    )


@pytest.mark.parametrize(("radio_name", "_js_array"), MODE_GROUPS)
def test_exactly_one_radio_checked_in_markup(radios, radio_name, _js_array):
    """A radiogroup must ship with exactly one selection before JS runs."""
    group = radios.get(radio_name)
    assert group, f"no radio inputs found with name={radio_name!r}"

    checked = [value for value, is_checked in group if is_checked]
    assert len(checked) == 1, (
        f"radio name={radio_name!r} has {len(checked)} checked inputs ({checked}); "
        f"expected exactly one."
    )
