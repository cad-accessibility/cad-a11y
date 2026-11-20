"""The skip link is the first Tab stop on the viewer and is built the same way on every page (#262).

The viewer moves focus to the page title on load (focusTopOfPage), so the first
Tab goes to whatever focusable element follows the title. That has to be the skip
link, or a keyboard user tabs past it into the menu. Stdlib parsing only, like the
other structural tests.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VIEWER_HTML = ROOT / "accessible-3d-viewer.html"
PAGES = [VIEWER_HTML, ROOT / "workshop-entry.html", ROOT / "study-control.html"]


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))


def _elements(path: Path) -> list[tuple[str, dict]]:
    collector = _Collector()
    collector.feed(path.read_text(encoding="utf-8"))
    return collector.elements


def _is_tabbable(tag: str, attrs: dict) -> bool:
    if "hidden" in attrs or "disabled" in attrs:
        return False
    tabindex = attrs.get("tabindex")
    if tabindex is not None:
        return int(tabindex) >= 0
    return (tag == "a" and "href" in attrs) or tag in {"button", "input", "select", "textarea"}


def _skip_links(path: Path) -> list[dict]:
    return [attrs for tag, attrs in _elements(path) if tag == "a" and attrs.get("href") == "#main-content"]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_one_shared_skip_link_without_inline_handlers(page: Path):
    links = _skip_links(page)
    assert len(links) == 1
    link = links[0]
    assert link.get("class") == "skip-link"
    assert not {"style", "onfocus", "onblur"} & link.keys()


def test_viewer_skip_link_is_first_tab_stop_after_title():
    elements = _elements(VIEWER_HTML)
    title_index = next(i for i, (_, attrs) in enumerate(elements) if attrs.get("id") == "page-title")
    tag, attrs = next((t, a) for t, a in elements[title_index + 1 :] if _is_tabbable(t, a))
    assert (tag, attrs.get("href")) == ("a", "#main-content")


def test_viewer_main_is_focusable_in_markup():
    main = next(attrs for tag, attrs in _elements(VIEWER_HTML) if attrs.get("id") == "main-content")
    assert main.get("tabindex") == "-1"


def test_on_demo_the_skip_link_is_the_first_tab_stop_from_the_top_too():
    """The demo banner sits above the header. Its recheck button came before the
    skip link, so tabbing from the top of the document (from the address bar, or
    Shift+Tab from the title) reached the button first (#296). The banner itself
    stays the first thing a screen reader reaches. Read with every hidden element
    shown, as /demo shows them."""
    elements = _elements(VIEWER_HTML)
    ids = [attrs.get("id") for _, attrs in elements]
    first = next((t, a) for t, a in elements if _is_tabbable(t, {k: v for k, v in a.items() if k != "hidden"}))
    assert (first[0], first[1].get("href")) == ("a", "#main-content")
    assert ids.index("demo-banner") < ids.index("page-title")
    assert ids.index("demo-recheck-btn") > ids.index("page-title")
