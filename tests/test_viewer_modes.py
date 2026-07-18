"""Guardrails against drift between the JS mode tables and the HTML radio values.

The viewer stores its mode names in two places that nothing links: the
`renderModes` / `representationModes` tables in static/js/viewer.js, and the
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

# (radio group `name`, JS table holding the same modes)
MODE_GROUPS = [
    ("render-mode", "renderModes"),
    ("view-mode", "representationModes"),
]

_ENTRY_RE = re.compile(r"\{([^}]*)\}")
_FIELD_RE = re.compile(r"(\w+)\s*:\s*'([^']*)'")
_STRING_RE = re.compile(r"'([^']*)'")


class _RadioCollector(HTMLParser):
    """Collect radio inputs as {group name: [{value, checked, label}]}.

    `label` is the visible text inside the wrapping <label>, which is what a
    user actually reads or hears for that option.
    """

    def __init__(self) -> None:
        super().__init__()
        self.radios: dict[str, list[dict]] = {}
        self._pending: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "label":
            self._pending = None
            return
        if tag != "input":
            return
        attrib = dict(attrs)
        if attrib.get("type") != "radio" or not attrib.get("name"):
            return
        self._pending = {
            "name": attrib["name"],
            "value": attrib.get("value"),
            "checked": "checked" in attrib,
            "label": "",
        }

    def handle_data(self, data: str) -> None:
        if self._pending is not None:
            self._pending["label"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag != "label" or self._pending is None:
            return
        self._pending["label"] = " ".join(self._pending["label"].split())
        self.radios.setdefault(self._pending["name"], []).append(self._pending)
        self._pending = None


def _js_mode_entries(source: str, name: str) -> list[dict]:
    """Parse `const <name> = [...]` into a list of field dicts.

    Handles both the mode-table shape (`{ key: 'filled', label: 'Filled', ... }`)
    and a plain string array, which stays valid while a group is mid-migration.
    """
    match = re.search(
        rf"const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*;", source, re.DOTALL
    )
    # Assert the parse itself so regex brittleness fails loudly instead of
    # silently passing against an empty list.
    assert match, f"could not parse `const {name} = [...]` from {VIEWER_JS.name}"
    body = match.group(1)

    entries = _ENTRY_RE.findall(body)
    if entries:
        return [dict(_FIELD_RE.findall(entry)) for entry in entries]
    return [{"key": value} for value in _STRING_RE.findall(body)]


@pytest.fixture(scope="module")
def radios() -> dict[str, list[dict]]:
    collector = _RadioCollector()
    collector.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return collector.radios


@pytest.fixture(scope="module")
def js_source() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


@pytest.mark.parametrize(("radio_name", "js_table"), MODE_GROUPS)
def test_radio_values_are_known_js_modes(radios, js_source, radio_name, js_table):
    """Every radio value must be a key in its JS table.

    This is the invariant syncRadios() depends on and never verifies.
    """
    keys = [entry["key"] for entry in _js_mode_entries(js_source, js_table)]
    group = radios.get(radio_name)
    assert group, f"no radio inputs found with name={radio_name!r}"

    unknown = [r["value"] for r in group if r["value"] not in keys]
    assert not unknown, (
        f"radio name={radio_name!r} has value(s) {unknown} absent from {js_table} "
        f"({keys}). syncRadios() compares case-sensitively, so these radios can "
        f"never be checked."
    )


@pytest.mark.parametrize(("radio_name", "js_table"), MODE_GROUPS)
def test_every_js_mode_has_exactly_one_radio(radios, js_source, radio_name, js_table):
    """Bijection between the JS table and the radio group: no orphans, no duplicates."""
    keys = [entry["key"] for entry in _js_mode_entries(js_source, js_table)]
    group = radios.get(radio_name)
    assert group, f"no radio inputs found with name={radio_name!r}"

    values = [r["value"] for r in group]
    missing = [key for key in keys if key not in values]
    assert not missing, (
        f"{js_table} keys {missing} have no radio in name={radio_name!r}"
    )

    duplicates = sorted({v for v in values if values.count(v) > 1})
    assert not duplicates, (
        f"radio name={radio_name!r} has duplicate value(s) {duplicates}"
    )


@pytest.mark.parametrize(("radio_name", "_js_table"), MODE_GROUPS)
def test_exactly_one_radio_checked_in_markup(radios, radio_name, _js_table):
    """A radiogroup must ship with exactly one selection before JS runs."""
    group = radios.get(radio_name)
    assert group, f"no radio inputs found with name={radio_name!r}"

    checked = [r["value"] for r in group if r["checked"]]
    assert len(checked) == 1, (
        f"radio name={radio_name!r} has {len(checked)} checked inputs ({checked}); "
        f"expected exactly one."
    )


@pytest.mark.parametrize(("radio_name", "js_table"), MODE_GROUPS)
def test_radio_label_matches_mode_label(radios, js_source, radio_name, js_table):
    """The visible radio text must match the table's label.

    This is the issue #57 class of bug: the radio reads "Filled" while the
    announcement said "shaded". Skipped for groups not yet migrated to a table
    with labels.
    """
    entries = _js_mode_entries(js_source, js_table)
    if not any("label" in entry for entry in entries):
        pytest.skip(f"{js_table} has no label field yet")

    labels = {entry["key"]: entry["label"] for entry in entries}
    for radio in radios[radio_name]:
        expected = labels.get(radio["value"])
        assert radio["label"] == expected, (
            f"radio value={radio['value']!r} reads {radio['label']!r} but {js_table} "
            f"labels it {expected!r}. Users would hear one and see the other."
        )


@pytest.mark.parametrize(("_radio_name", "js_table"), [MODE_GROUPS[0]])
def test_wire_values_are_accepted_by_the_server(js_source, _radio_name, js_table):
    """Every `wire` value must be a key the server's render-mode mapping knows.

    Nothing else checks this JS-to-Python contract.
    """
    # Imported lazily: app.cad_comparison_lib pulls in the heavy optional renderer
    # deps (pythonocc-core via OCC.Core). Skipping here keeps the JS/HTML consistency
    # tests in this file runnable in a pip-only environment without that stack.
    CADComparisonRenderer = pytest.importorskip("app.cad_comparison_lib").CADComparisonRenderer

    entries = _js_mode_entries(js_source, js_table)
    wires = [entry["wire"] for entry in entries if "wire" in entry]
    assert wires, f"{js_table} has no wire values to check"

    for wire in wires:
        mapped = CADComparisonRenderer._map_render_mode(None, wire)
        # The mapping falls back to "outline" for anything unrecognised, so an
        # unknown wire value would silently render the wrong mode.
        assert mapped != "outline" or wire.lower() == "outline", (
            f"wire value {wire!r} is not recognised by _map_render_mode and would "
            f"silently fall back to 'outline'."
        )
