"""Guardrails on the screen-reader live-region structure in the viewer markup.

The announcement layer has two politeness tiers, each a pair of swap slots, and
nothing else should be a live region. Two failure modes are worth locking down:

  * a status panel left as role="status" aria-live="assertive" — self
    contradictory (status implies polite) and an interruption source; this is
    what made the hardware panels and debug dump re-read over live speech.
  * the debug stage list regaining aria-live and flooding on every render.

Parsing is stdlib-only: no package.json, no JS runner, and bs4/lxml are not in
requirements.txt.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"

# The complete set of live regions the viewer is allowed to have, and the
# politeness each must carry. Anything else with aria-live is a regression.
EXPECTED_LIVE_REGIONS = {
    "sr-live-polite-a": "polite",
    "sr-live-polite-b": "polite",
    "sr-live-assertive-a": "assertive",
    "sr-live-assertive-b": "assertive",
}

# Divs demoted to plain visual text; each must stay free of aria-live.
DEMOTED_STATUS_IDS = [
    "slice-graph-lock-status",
    "upload-model-status",
    "monarch-hid-status",
    "trinkey-status",
    "witmotion-status",
    "dotpad-status",
    "debug-stage-list",
]


class _ElementCollector(HTMLParser):
    """Collect every start tag's id / aria-live / role / aria-atomic."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrib = dict(attrs)
        self.elements.append(
            {
                "tag": tag,
                "id": attrib.get("id"),
                "aria-live": attrib.get("aria-live"),
                "role": attrib.get("role"),
                "aria-atomic": attrib.get("aria-atomic"),
            }
        )


def _elements() -> list[dict]:
    collector = _ElementCollector()
    collector.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return collector.elements


def _by_id() -> dict[str, dict]:
    return {el["id"]: el for el in _elements() if el["id"]}


def test_exactly_the_expected_live_regions_exist():
    """The only aria-live elements are the four tier regions, with right politeness."""
    live = {
        el["id"]: el["aria-live"] for el in _elements() if el["aria-live"] is not None
    }
    assert live == EXPECTED_LIVE_REGIONS, (
        f"aria-live elements are {live}, expected exactly {EXPECTED_LIVE_REGIONS}. "
        f"A new live region, or a changed politeness, will interrupt users."
    )


def test_tier_regions_are_atomic_and_roleless():
    """Each tier region reads as a whole and carries no role.

    role="alert"/"status" alongside aria-live is redundant and double-speaks on
    some AT; the polite tier cannot carry role="alert" at all.
    """
    by_id = _by_id()
    for region_id in EXPECTED_LIVE_REGIONS:
        el = by_id.get(region_id)
        assert el is not None, f"missing live region {region_id!r}"
        assert el["aria-atomic"] == "true", f"{region_id} must be aria-atomic"
        assert el["role"] is None, f"{region_id} must not combine aria-live with a role"


def test_no_element_combines_aria_live_with_status_or_alert_role():
    """role=status/alert plus an explicit aria-live is the self-contradiction we removed."""
    offenders = [
        el
        for el in _elements()
        if el["aria-live"] is not None and el["role"] in {"status", "alert"}
    ]
    assert not offenders, (
        f"elements combine aria-live with a role: {offenders}. role implies its own "
        f"politeness; pairing it with aria-live is contradictory and double-speaks."
    )


def test_demoted_status_panels_have_no_aria_live():
    """The visual status panels and the debug dump must not be live regions."""
    by_id = _by_id()
    for status_id in DEMOTED_STATUS_IDS:
        el = by_id.get(status_id)
        assert el is not None, f"expected element #{status_id} in the markup"
        assert el["aria-live"] is None, (
            f"#{status_id} has aria-live={el['aria-live']!r}; it must be plain visual "
            f"text so it does not announce (or, for the debug dump, flood) on update."
        )
