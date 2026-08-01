"""Guardrails for the persistent announcement message window (#144).

Issue #144 asks that announcements never use a popup, and instead go to a
persistent on-screen field that a screen reader/braille user can pick up
automatically by parking focus on it. Two things are worth locking down:

  * the old fading toast (#announcement-toast) does not come back.
  * #announcement-window exists as a real (readonly) textbox, not a div, and is
    NOT itself a live region — speech is handled entirely by the sr-live-* pair
    covered in test_live_regions.py; this field's job is the visible/braille-
    on-focus channel, and giving it aria-live too would double-speak.

Parsing is stdlib-only, matching test_live_regions.py: no package.json, no JS
runner, and bs4/lxml are not in requirements.txt.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"
VIEWER_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "viewer.js"


class _ElementCollector(HTMLParser):
    """Collect every start tag's id / tag / aria-live / readonly."""

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
                "readonly": "readonly" in attrib,
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
        "use a popup, only the persistent message window"
    )
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "showToast" not in js
    assert "toastDurationSec" not in js


def test_message_window_is_a_readonly_textbox_not_a_live_region():
    by_id = _by_id()
    field = by_id.get("announcement-window")
    assert field is not None, "expected a persistent #announcement-window field"
    assert field["tag"] == "textarea", (
        "must be a genuine form control, not a div — a focused control's value "
        "change is what a braille display picks up on its own, independent of "
        "aria-live (which browsers/AT do not reliably route to braille)"
    )
    assert field["readonly"], "must stay readonly: it mirrors state, it isn't user-editable"
    assert field["aria-live"] is None, (
        "must NOT carry aria-live — speech is already handled by the sr-live-* "
        "pair (test_live_regions.py); adding it here would double-speak every "
        "announcement"
    )


def test_history_is_hidden_by_default_behind_a_toggle():
    by_id = _by_id()
    content = by_id.get("announcement-history-content")
    toggle = by_id.get("announcement-history-toggle-btn")
    assert content is not None and content["hidden"], (
        "the scrollable history is a debug option and must start hidden"
    )
    assert toggle is not None, "expected a toggle button to show/hide the history"


def test_viewer_js_updates_the_message_window_on_every_announcement():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "function updateMessageWindow(message)" in js
    assert "updateMessageWindow(normalizedMessage);" in js
