"""Guardrails for the persistent announcement message windows 

The two message windows (assertive/polite — see
test_live_regions.py for their exact aria configuration) are standard native
ARIA live-region <div>s updated via textContent, which is the pattern with the
widest, most reliable support across screen readers.

Parsing is stdlib-only, matching test_live_regions.py: no package.json, no JS
runner, and bs4/lxml are not in requirements.txt.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"
VIEWER_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "viewer.js"


class _ElementCollector(HTMLParser):
    """Collect every start tag's id / tag / class / role / hidden."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrib = dict(attrs)
        self.elements.append(
            {
                "tag": tag,
                "id": attrib.get("id"),
                "class": attrib.get("class", ""),
                "role": attrib.get("role"),
                "hidden": "hidden" in attrib,
            }
        )


def _by_id() -> dict[str, dict]:
    collector = _ElementCollector()
    collector.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return {el["id"]: el for el in collector.elements if el["id"]}




