"""Every radio group has a name of its own (#209).

Both mode groups used to take their name from the "Rendering Mode" heading
through aria-labelledby, which wins over a legend, so a screen reader announced
two groups that control different things with one name. The only way to tell
them apart was to try them.

Names are worked out here the way a browser does for a fieldset:
aria-labelledby when it is there, the legend otherwise. Parsing is stdlib-only,
like the other markup tests.
"""

from __future__ import annotations

import re
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"


def _radio_group_names() -> dict[str, str]:
    """The accessible name of each radio group, keyed by its radios' name."""
    html = VIEWER_HTML.read_text(encoding="utf-8")
    names = {}
    for match in re.finditer(r'<fieldset role="radiogroup"([^>]*)>(.*?)</fieldset>', html, re.DOTALL):
        attributes, body = match.groups()
        radios = re.search(r'type="radio" name="([\w-]+)"', body).group(1)
        labelled_by = re.search(r'aria-labelledby="([\w-]+)"', attributes)
        if labelled_by:
            label = re.search(rf'id="{labelled_by.group(1)}"[^>]*>([^<]*)<', html).group(1)
        else:
            label = re.search(r"<legend[^>]*>([^<]*)</legend>", body).group(1)
        names[radios] = " ".join(label.split())
    return names


def test_the_two_mode_groups_are_announced_by_different_names():
    names = _radio_group_names()
    assert names["render-mode"] == "Rendering Mode"
    assert names["view-mode"] == "Layout", "the status bar and '.' call this setting Layout"


def test_no_two_radio_groups_share_a_name():
    names = list(_radio_group_names().values())
    assert len(names) == len(set(names)), f"radio groups sharing a name: {names}"


def test_the_help_dialog_uses_the_same_word():
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert "<kbd>T</kbd> Cycle layout" in html
    assert "Cycle view mode" not in html
