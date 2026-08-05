"""Main menu, generic device connect, optional sensors, and footer (#145).

The page used to open straight onto a flat, always-visible list of every
control and every device section. This restructures the default main page to
what #145 asks for: a Main/About/Help/Settings menu, a single "Connect to
device" / "Disconnect" pair up front (proxying to whichever of Monarch/DotPad
is selected), the Trinkey Slider and WitMotion IMU sections hidden unless
turned on in Settings, and a footer with GitHub/bug-report links plus a
"Funded by" row.

Parsing is stdlib-only, matching test_live_regions.py / test_message_window.py:
no package.json, no JS runner, and bs4/lxml are not in requirements.txt.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parent.parent / "accessible-3d-viewer.html"
VIEWER_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "viewer.js"
DOTPAD_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "dotpad-integration.js"
MONARCH_HID_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "monarch-hid.js"


class _ElementCollector(HTMLParser):
    """Collect every start tag's id / tag / class / attrs, like test_live_regions.py."""

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
                "hidden": "hidden" in attrib,
                "href": attrib.get("href"),
                "tabindex": attrib.get("tabindex"),
            }
        )


def _by_id() -> dict[str, dict]:
    collector = _ElementCollector()
    collector.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return {el["id"]: el for el in collector.elements if el["id"]}


def _all_elements() -> list[dict]:
    collector = _ElementCollector()
    collector.feed(VIEWER_HTML.read_text(encoding="utf-8"))
    return collector.elements


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


def test_nav_has_about_help_settings_and_the_connect_pair():
    by_id = _by_id()
    for button_id in ("device-connect-btn", "device-disconnect-btn", "nav-about-btn", "nav-help-btn", "nav-settings-btn"):
        assert by_id.get(button_id) is not None, f"expected a #{button_id} in the main menu"
        assert by_id[button_id]["tag"] == "button"


def test_help_button_opens_the_same_dialog_as_pressing_h():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "navHelpBtn.addEventListener('click', openShortcutsDialog)" in js


def test_main_button_closes_dialogs_and_refocuses_main_content_if_present():
    # #nav-main-btn isn't currently in the markup (folded away when Connect/
    # Disconnect moved into the nav), but the wiring is guarded and harmless if
    # it's ever added back, so this only checks the JS side stays intact.
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "navMainBtn.addEventListener" in js
    assert "dialog[open]" in js
    assert "mainContent.focus();" in js


# ---------------------------------------------------------------------------
# About / Settings dialogs — same accessible pattern as the shortcuts dialog
# ---------------------------------------------------------------------------


def test_about_and_settings_dialogs_share_the_shortcuts_dialog_pattern():
    by_id = _by_id()
    for dialog_id, heading_id in (
        ("about-dialog", "about-heading"),
        ("settings-dialog", "settings-heading"),
        ("shortcuts-dialog", "shortcuts-heading"),
    ):
        dialog = by_id.get(dialog_id)
        assert dialog is not None, f"expected a #{dialog_id}"
        assert dialog["tag"] == "dialog"
        assert "info-dialog" in dialog["class"]

        heading = by_id.get(heading_id)
        assert heading is not None, f"expected a #{heading_id}"
        assert heading["tabindex"] == "-1", (
            "a read-only dialog should focus its own heading on open, not the "
            "first interactive control (ARIA APG dialog pattern)"
        )


def test_about_dialog_has_the_expected_copy():
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert "The CAD accessibility project is an open source effort" in html
    assert "github.com/cad-accessibility" in html


def test_settings_dialog_has_slider_and_cube_checkboxes():
    by_id = _by_id()
    for checkbox_id in ("settings-enable-slider", "settings-enable-cube"):
        checkbox = by_id.get(checkbox_id)
        assert checkbox is not None, f"expected a #{checkbox_id}"
        assert checkbox["tag"] == "input"


def test_shared_dialog_controller_is_used_for_all_three_dialogs():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "function makeInfoDialogController(" in js
    assert "makeInfoDialogController(shortcutsDialog, shortcutsHeading)" in js
    assert "makeInfoDialogController(aboutDialog, aboutHeading)" in js
    assert "makeInfoDialogController(settingsDialog, settingsHeading)" in js


# ---------------------------------------------------------------------------
# Optional add-on sensors (Slider / Cube) hidden unless enabled in Settings
# ---------------------------------------------------------------------------


def test_trinkey_and_witmotion_sections_start_hidden():
    by_id = _by_id()
    for section_id in ("trinkey-section", "witmotion-section"):
        section = by_id.get(section_id)
        assert section is not None, f"expected a #{section_id}"
        assert section["hidden"], f"#{section_id} should be hidden by default (#145)"


def test_settings_checkboxes_toggle_the_matching_section():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "settingsSliderCheckbox.addEventListener('change'" in js
    assert "settingsCubeCheckbox.addEventListener('change'" in js
    assert "trinkeySection" in js
    assert "witmotionSection" in js


def test_optional_sensor_visibility_is_persisted():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "SETTINGS_SLIDER_VISIBLE_KEY" in js
    assert "SETTINGS_CUBE_VISIBLE_KEY" in js
    assert "window.localStorage.setItem(storageKey" in js


def test_debug_panel_and_bbox_sections_start_hidden():
    by_id = _by_id()
    for section_id in ("debug-panel-section", "bbox-section"):
        section = by_id.get(section_id)
        assert section is not None, f"expected a #{section_id}"
        assert section["hidden"], f"#{section_id} should be hidden by default (#145)"


def test_debug_panel_and_bbox_checkboxes_have_unique_ids():
    # Regression check: these two were previously copy-pasted from the Slider/Cube
    # checkboxes above them and kept the same ids, so getElementById could only
    # ever find the first pair and the Debugging checkboxes did nothing at all.
    by_id = _by_id()
    for checkbox_id in ("settings-enable-debug-panel", "settings-enable-bbox"):
        checkbox = by_id.get(checkbox_id)
        assert checkbox is not None, f"expected a #{checkbox_id}"
        assert checkbox["tag"] == "input"

    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert html.count('id="settings-enable-slider"') == 1
    assert html.count('id="settings-enable-cube"') == 1
    assert html.count('id="settings-enable-debug-panel"') == 1
    assert html.count('id="settings-enable-bbox"') == 1


def test_debug_panel_and_bbox_checkboxes_toggle_the_matching_section():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "settingsDebugPanelCheckbox.addEventListener('change'" in js
    assert "settingsBboxCheckbox.addEventListener('change'" in js
    assert "debugPanelSection" in js
    assert "bboxSection" in js
    assert "SETTINGS_DEBUG_PANEL_VISIBLE_KEY" in js
    assert "SETTINGS_BBOX_VISIBLE_KEY" in js


# ---------------------------------------------------------------------------
# Output Device — moved from the main interface into Settings
# ---------------------------------------------------------------------------


def test_output_device_radios_live_inside_the_settings_dialog():
    html = VIEWER_HTML.read_text(encoding="utf-8")
    settings_start = html.index('id="settings-dialog"')
    settings_end = html.index("</dialog>", settings_start)
    settings_markup = html[settings_start:settings_end]
    for radio_id in ("output-device-monarch", "output-device-dotpad"):
        assert f'id="{radio_id}"' in settings_markup, f"expected #{radio_id} inside the Settings dialog"

    assert "output-device-heading" not in html, (
        "the old standalone Output Device section should be gone from the main interface"
    )


def test_output_device_has_no_auto_option():
    # There's no browser API that can "auto-detect" which device to connect to
    # (Monarch is Web HID, DotPad is Web Bluetooth) — connecting to one *is* the
    # choice now, so Auto was removed (#145) in favor of a successful connect
    # selecting its own radio directly.
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert "output-device-auto" not in html
    assert 'value="auto"' not in html

    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "currentOutputDevice = 'monarch';" in js
    assert "currentOutputDevice = 'dotpad';" in js


# ---------------------------------------------------------------------------
# Generic "Connect to device" / "Disconnect"
# ---------------------------------------------------------------------------


def test_device_connect_section_has_a_hidden_disconnect_button():
    by_id = _by_id()
    connect_btn = by_id.get("device-connect-btn")
    disconnect_btn = by_id.get("device-disconnect-btn")
    assert connect_btn is not None and connect_btn["tag"] == "button"
    assert disconnect_btn is not None and disconnect_btn["tag"] == "button"
    assert disconnect_btn["hidden"], "Disconnect should be hidden until a device connects (#145)"


def test_generic_connect_calls_device_logic_directly_for_the_selected_output_device():
    # There is no per-device Connect button in the page anymore (Monarch USB and
    # DotPad each removed their standalone sections), so the generic button must
    # call the device modules' exposed connect functions directly rather than
    # proxy through a .click() on a button that no longer exists.
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "deviceConnectBtn.addEventListener('click'" in js
    assert "window.connectDotpad?.()" in js
    assert "window.connectMonarchHid?.()" in js


def test_generic_disconnect_calls_device_logic_directly_for_whichever_is_connected():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "deviceDisconnectBtn.addEventListener('click'" in js
    assert "window.disconnectMonarchHid?.()" in js
    assert "window.disconnectDotpad?.()" in js


def test_monarch_hid_js_exposes_connect_and_disconnect_directly():
    js = MONARCH_HID_JS.read_text(encoding="utf-8")
    assert "window.connectMonarchHid = connectMonarchHid;" in js
    assert "window.disconnectMonarchHid = disconnectMonarchHid;" in js
    # No DOM button to depend on anymore — regression check against reintroducing
    # an unconditional addEventListener on a since-removed element.
    assert "getElementById('monarch-hid-connect-btn')" not in js
    assert "getElementById('monarch-hid-disconnect-btn')" not in js


def test_dotpad_integration_js_exposes_connect_and_disconnect_directly():
    js = DOTPAD_JS.read_text(encoding="utf-8")
    assert "window.connectDotpad = connectDotpad;" in js
    assert "window.disconnectDotpad = disconnectDotpad;" in js
    assert "getElementById('dotpad-scan-ble-btn')" not in js
    assert "getElementById('dotpad-disconnect-btn')" not in js


def test_dotpad_integration_reports_connection_state_to_viewer():
    js = DOTPAD_JS.read_text(encoding="utf-8")
    assert js.count("window.setDotpadConnected?.(") >= 3, (
        "expected the DotPad module to report connect, manual disconnect, and "
        "unexpected disconnect to the generic connect/disconnect UI"
    )


def test_monarch_connect_state_updates_the_generic_ui():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "function setMonarchHidConnected(connected) {" in js
    assert "updateGenericDeviceConnectUI();" in js


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def test_footer_links_to_github_and_bug_report():
    elements = _all_elements()
    footer_present = any(el["tag"] == "footer" for el in elements)
    assert footer_present, "expected a <footer> element"

    hrefs = [el["href"] for el in elements if el["tag"] == "a"]
    assert "https://github.com/cad-accessibility" in hrefs
    assert "https://github.com/cad-accessibility/cad-a11y/issues" in hrefs


def test_footer_funded_by_row_links_to_nsf_and_create():
    html = VIEWER_HTML.read_text(encoding="utf-8")
    assert "Funded by" in html

    elements = _all_elements()
    hrefs = [el["href"] for el in elements if el["tag"] == "a"]
    assert "https://nsf.gov" in hrefs
    assert "https://create.uw.edu" in hrefs
