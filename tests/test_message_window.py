"""Guardrails for the persistent announcement message windows (#144).

Issue #144 asks that announcements never use a popup, and instead go to a
persistent on-screen field. The two message windows (assertive/polite — see
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


def test_toast_is_gone():
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert "announcement-toast" not in html, (
        "the fading toast should not exist — #144 asks that announcements never "
        "use a popup, only the persistent message windows"
    )
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "showToast" not in js
    assert "toastDurationSec" not in js


def test_message_windows_are_plain_divs():
    by_id = _by_id()
    for field_id in ("announcement-window", "announcement-window-polite"):
        field = by_id.get(field_id)
        assert field is not None, f"expected a persistent #{field_id} field"
        assert field["tag"] == "div", (
            f"#{field_id} must be a plain native live-region div updated via "
            f"textContent — the standard, most widely supported ARIA pattern"
        )


def test_history_is_an_sr_only_log():
    """The history list is a permanent, screen-reader-only audit trail (role="log").

    It carries no visible UI (no show/hide toggle, no clear button) — it exists
    for AT users and for debugging via the accessibility tree/announcements,
    not as an on-screen panel.
    """
    by_id = _by_id()
    history = by_id.get("announcement-history")
    assert history is not None, "expected #announcement-history"
    assert history["role"] == "log"
    assert "sr-only" in history["class"].split()


def test_viewer_js_updates_the_message_window_on_every_announcement():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "function updateMessageWindow(message" in js
    assert "field.textContent = message" in js
    assert "updateMessageWindow(normalizedMessage, politeness);" in js
