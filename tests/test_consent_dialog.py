"""The welcome dialog promises only what the app does (#220).

It is the privacy text people actually agree to, and it is read out whole, as
the dialog's description, the moment the dialog opens.
"""

from __future__ import annotations

import re
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"


def _dialog_description() -> str:
    html = VIEWER_HTML.read_text(encoding="utf-8")
    match = re.search(r'<p id="consent-dialog-desc">(.*?)</p>', html, re.DOTALL)
    assert match, "the consent dialog's description is missing"
    return " ".join(match.group(1).split())


def test_the_dialog_does_not_promise_to_keep_uploads():
    """Uploads are deleted when the page reloads (#239). Until they survive one,
    the dialog must not say the cookie remembers them."""
    assert "upload" not in _dialog_description().lower()


def test_the_dialog_still_says_what_the_cookie_is_for():
    assert "We store a cookie to remember your visit." in _dialog_description()
