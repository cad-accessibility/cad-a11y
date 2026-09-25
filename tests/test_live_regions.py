"""Guardrails on the screen-reader live-region structure in the viewer markup.

The announcement layer has exactly two message windows, and nothing else
should be a live region beyond the small set declared in
EXPECTED_LIVE_ELEMENTS below:

  * #announcement-window: role="alert" (implies aria-live="assertive"), with
    no explicit aria-live attribute alongside it (redundant, and double-speaks
    on some AT). Used for anything the user just did directly (a keyboard
    shortcut, a hardware device button), or an error they need to act on.
  * #announcement-window-polite: role="status" (implies aria-live="polite"),
    same reasoning as above — no explicit aria-live alongside it. Background/
    system events not tied to an immediate action.

Also locks down a failure mode seen before: the debug stage list regaining
aria-live and flooding on every render.

Parsing is stdlib-only: no package.json, no JS runner, and bs4/lxml are not in
requirements.txt.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"

# Roles that imply their own live-region behavior per the ARIA spec, so an
# element carrying one of these is a live region even without aria-live.
IMPLICIT_LIVE_ROLES = {"alert", "status", "log", "alertdialog", "marquee", "timer"}

# The complete set of elements the viewer is allowed to use as live regions,
# and exactly how each announces. Anything else with aria-live (or one of the
# IMPLICIT_LIVE_ROLES) is a regression.
EXPECTED_LIVE_ELEMENTS = {
    "announcement-window": {"aria-live": None, "role": "alert"},
    # --- Demo mode (/demo). Present in the markup on every page, revealed only
    # on the demo path. role="status" rather than "alert": it is revealed once,
    # during page setup, before anybody has touched a control, so there is
    # nothing in progress for it to interrupt -- and viewer.js separately
    # announces the same sentence assertively through the announcement layer, so
    # a reader who is mid-sentence hears it there rather than twice here.
    "demo-banner": {"aria-live": None, "role": "status"},
    "announcement-window-polite": {"aria-live": None, "role": "status"},
    # Inline form-validation error in the session-consent dialog — unrelated to
    # the announcement layer, hidden until the email field actually fails to
    # validate.
    "consent-email-error": {"aria-live": None, "role": "alert"},
    # --- Study mode (/study). Present in the markup on every page but revealed
    # only by body.study-ui, so on the ordinary viewer these are hidden and never
    # written to.
    #
    # study-step-heading and study-step-progress used to each be their own live
    # region (an aria-live h2 and a role="status" paragraph), which meant a step
    # change was announced three times over: the title alone, then the step
    # count alone, then the combined utterance below repeating both plus the
    # instructions. Both are now plain text -- still real content for on-demand
    # navigation (the heading is still a real h2, still a landmark to jump to),
    # just not auto-announced. The single announcement lives in the shared
    # announcement-window-polite instead (see stepAnnouncement() in study.js).
    #
    # The ready-button confirmation is the one still-live piece specific to this
    # page: written only in response to something the participant just did.
    "study-ready-status": {"aria-live": None, "role": "status"},
    # Shown only before the session starts, while the participant is entering
    # their code. Assertive on purpose: a code that did not match has to be
    # heard, and there is no braille reading in progress to interrupt yet. Same
    # pattern as the consent dialog's email error above.
    "study-join-error": {"aria-live": None, "role": "alert"},
}

# Divs demoted to plain visual text; each must stay free of aria-live and of
# the implicit live-region roles.
#
# monarch-hid-status and dotpad-status were dropped from this list (#145):
# the standalone Monarch USB / DotPad sections they lived in were removed from
# the page when Connect/Disconnect became the single generic pair in the nav.
DEMOTED_STATUS_IDS = [
    "slice-graph-lock-status",
    "upload-model-status",
    "trinkey-status",
    "witmotion-status",
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


def _is_live(el: dict) -> bool:
    return el["aria-live"] is not None or el["role"] in IMPLICIT_LIVE_ROLES


def test_exactly_the_expected_live_elements_exist():
    """The only live-region elements are the three expected ones, configured as declared."""
    live = {
        el["id"]: {"aria-live": el["aria-live"], "role": el["role"]}
        for el in _elements()
        if el["id"] and _is_live(el)
    }
    assert live == EXPECTED_LIVE_ELEMENTS, (
        f"live-region elements are {live}, expected exactly {EXPECTED_LIVE_ELEMENTS}. "
        f"A new live region, or a changed politeness/role, will change how (or "
        f"whether) users are interrupted."
    )


def test_alert_window_carries_no_explicit_aria_live():
    """role=alert already implies assertive; pairing it with aria-live double-speaks on some AT."""
    by_id = _by_id()
    el = by_id.get("announcement-window")
    assert el is not None, "missing #announcement-window"
    assert el["role"] == "alert"
    assert el["aria-live"] is None, (
        '#announcement-window combines role="alert" with an explicit aria-live '
        "attribute; role already implies assertive, and the pairing double-speaks "
        "on some AT."
    )


def test_polite_window_carries_no_explicit_aria_live():
    """role=status already implies polite; pairing it with aria-live is redundant."""
    by_id = _by_id()
    el = by_id.get("announcement-window-polite")
    assert el is not None, "missing #announcement-window-polite"
    assert el["role"] == "status"
    assert el["aria-atomic"] == "true"
    assert el["aria-live"] is None, (
        '#announcement-window-polite combines role="status" with an explicit '
        "aria-live attribute; role already implies polite."
    )


def test_no_status_panel_combines_aria_live_with_status_or_alert_role():
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


def test_demoted_status_panels_have_no_live_region_behavior():
    """The visual status panels and the debug dump must not be live regions."""
    by_id = _by_id()
    for status_id in DEMOTED_STATUS_IDS:
        el = by_id.get(status_id)
        assert el is not None, f"expected element #{status_id} in the markup"
        assert not _is_live(el), (
            f"#{status_id} is a live region (aria-live={el['aria-live']!r}, "
            f"role={el['role']!r}); it must be plain visual text so it does not "
            f"announce (or, for the debug dump, flood) on update."
        )
