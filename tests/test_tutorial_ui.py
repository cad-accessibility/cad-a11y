"""Structural guardrails on the first-run tutorial's page side.

static/js/tutorial.js runs the lessons in the ordinary viewer, for people on a
screen reader and a braille display with nobody beside them. What is checked
here is what review cannot see and a live session would find too late: that the
region is where a screen reader meets it first and is never a live region or a
dialog, that every control is a real button and Exit is always one of them,
that the tutorial opens only where TUTORIAL_ROUTES says, that every check type
the lessons may use is implemented, and that the hooks it needs in viewer.js,
the page and the device scripts are in place and in the right order.

Parsing is stdlib-only, matching test_study_ui.py and
test_main_menu_and_layout.py: no package.json, no JS runner, and bs4/lxml are
not in requirements.txt.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWER_HTML = ROOT / "accessible-3d-viewer.html"
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
TUTORIAL_JS = ROOT / "static" / "js" / "tutorial.js"
MONARCH_JS = ROOT / "static" / "js" / "monarch-hid.js"
DOTPAD_JS = ROOT / "static" / "js" / "dotpad-integration.js"
TRINKEY_JS = ROOT / "static" / "js" / "trinkey-slider.js"
WITMOTION_JS = ROOT / "static" / "js" / "witmotion-imu.js"
VIEWER_CSS = ROOT / "static" / "css" / "viewer.css"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

# Every check type a lesson may use (the tutorial contract, section 4). The
# lessons test holds app/tutorial_lessons.py to this list from the other side.
CHECK_TYPES = {
    "manual", "answer", "key", "keys", "state", "changed", "cycle", "sweep", "edge",
    "in_band", "mark_band", "device", "ui", "settings_unchanged", "test_pattern",
    "cursor_in", "centred", "zoom_by", "model_changed", "key_help", "slicegraph_ready", "all",
}

IMPLICIT_LIVE_ROLES = {"alert", "status", "log", "alertdialog", "marquee", "timer"}


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict] = []
        self.text_by_id: dict[str, str] = {}
        self._open: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrib = dict(attrs)
        self.elements.append({"tag": tag, "attrs": attrib, "id": attrib.get("id")})
        if tag not in {"input", "img", "br", "meta", "link", "hr"}:
            self._open.append(attrib.get("id"))
            if attrib.get("id"):
                self.text_by_id.setdefault(attrib["id"], "")

    def handle_endtag(self, tag: str) -> None:
        if self._open:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        for element_id in self._open:
            if element_id:
                self.text_by_id[element_id] += data


def _parse(markup: str) -> _Collector:
    collector = _Collector()
    collector.feed(markup)
    return collector


def _html() -> str:
    return VIEWER_HTML.read_text(encoding="utf-8")


def _js(path: Path = TUTORIAL_JS) -> str:
    return path.read_text(encoding="utf-8")


def _by_id(markup: str) -> dict[str, dict]:
    return {el["id"]: el for el in _parse(markup).elements if el["id"]}


def _region_markup() -> str:
    html = _html()
    start = html.index('<section id="tutorial-region"')
    return html[start:html.index("</section>", start)]


def _text(element_id: str, markup: str | None = None) -> str:
    return " ".join(_parse(markup or _html()).text_by_id.get(element_id, "").split())


def _function(source: str, signature: str) -> str:
    """The body of a top-level or nested function, up to its closing brace at
    the same indentation as the line it starts on."""
    start = source.index(signature)
    line_start = source.rfind("\n", 0, start) + 1
    indent = source[line_start:start]
    indent = indent[:len(indent) - len(indent.lstrip())]
    end = source.index(f"\n{indent}}}", start)
    return source[start:end]


def _css_rules() -> str:
    return re.sub(r"/\*.*?\*/", "", VIEWER_CSS.read_text(encoding="utf-8"), flags=re.DOTALL)


# ---------------------------------------------------------------------------
# The region
# ---------------------------------------------------------------------------

class TestRegion:
    def test_region_is_a_hidden_labelled_section_styled_like_the_study_region(self):
        region = _by_id(_html()).get("tutorial-region")
        assert region is not None, "missing #tutorial-region"
        assert region["tag"] == "section"
        assert "hidden" in region["attrs"], "it opens only when the tutorial runs"
        assert region["attrs"].get("aria-labelledby") == "tutorial-heading"
        assert "study-region" in region["attrs"].get("class", "").split()

    def test_region_is_first_in_main_right_after_the_study_region(self):
        """The skip link lands on main, and a screen reader meets the lesson
        before any control. Right after #study-region, which is hidden off /study."""
        html = _html()
        study_end = html.index("</section>", html.index('id="study-region"'))
        region = html.index('id="tutorial-region"')
        assert html.index('id="main-content"') < html.index('id="study-region"') < study_end < region
        assert region < html.index('<div class="layout">')
        between = html[study_end + len("</section>"):html.index("<section", study_end)]
        assert "<button" not in between and "<input" not in between

    def test_the_first_heading_is_the_focusable_h2(self):
        headings = [el for el in _parse(_region_markup()).elements if re.fullmatch(r"h[1-6]", el["tag"])]
        first = headings[0]
        assert first["tag"] == "h2" and first["id"] == "tutorial-heading"
        assert first["attrs"].get("tabindex") == "-1", "focus moves here without joining the tab order"

    def test_nothing_in_the_region_is_a_live_region_a_form_or_a_dialog(self):
        """One polite announcement per change goes through the page's shared
        window (tests/test_live_regions.py pins that set). A dialog would switch
        off every viewer key the lessons teach, and a form would submit on Enter."""
        for el in _parse(_region_markup()).elements:
            assert "aria-live" not in el["attrs"], f"{el} is a live region"
            assert el["attrs"].get("role") not in IMPLICIT_LIVE_ROLES, f"{el} is a live region"
            assert el["tag"] not in {"form", "dialog"}, f"{el['tag']} inside the tutorial region"

    def test_the_last_action_line_is_plain_text(self):
        last = _by_id(_region_markup())["tutorial-last"]
        assert "role" not in last["attrs"] and "aria-live" not in last["attrs"]
        # Named for what it is: Jen could not tell what "Last:" meant (#245 review).
        assert _text("tutorial-last").startswith("Last thing said:")

    def test_every_control_is_a_real_button(self):
        """Buttons rather than radios for answers too: the viewer takes Arrow Up
        and Down for depth on anything that is not a text field."""
        elements = _parse(_region_markup()).elements
        buttons = [el for el in elements if el["tag"] == "button"]
        assert buttons
        for button in buttons:
            assert button["attrs"].get("type") == "button", f"#{button['id']} is not type=button"
        assert not [el for el in elements if el["tag"] in {"input", "select", "textarea"}]

    def test_the_lesson_controls_are_in_the_agreed_order(self):
        region = _region_markup()
        order = [
            "tutorial-continue-btn", "tutorial-back-btn", "tutorial-repeat-btn", "tutorial-hint-btn",
            "tutorial-show-me-btn", "tutorial-restart-lesson-btn", "tutorial-keyhelp-btn",
            "tutorial-lessons-btn", "tutorial-exit-btn",
        ]
        positions = [region.index(f'id="{element_id}"') for element_id in order]
        assert positions == sorted(positions)
        labels = {
            # "Next (N)": Continue was not obvious for N (#245 review).
            "tutorial-continue-btn": "Next (N)",
            "tutorial-show-me-btn": "Show me",
            "tutorial-back-btn": "Back (B)",
            "tutorial-repeat-btn": "Repeat (C)",
            "tutorial-hint-btn": "Hint 1 of 3",
            "tutorial-restart-lesson-btn": "Return to lesson start",
            "tutorial-keyhelp-btn": "Key help",
            "tutorial-lessons-btn": "Lessons",
            "tutorial-exit-btn": "Exit tutorial",
        }
        for element_id, label in labels.items():
            assert _text(element_id, region) == label

    def test_exit_is_in_both_views_and_never_hidden(self):
        """Named on the first screen and one press from every step: Escape
        cannot be the only way out, since NVDA takes it."""
        by_id = _by_id(_region_markup())
        for element_id in ("tutorial-exit-btn", "tutorial-menu-exit-btn"):
            assert "hidden" not in by_id[element_id]["attrs"]
            assert _text(element_id) == "Exit tutorial"
        region = _region_markup()
        lesson_view = region[region.index('id="tutorial-lesson-view"'):region.index('id="tutorial-menu-view"')]
        assert 'id="tutorial-exit-btn"' in lesson_view
        assert 'id="tutorial-menu-exit-btn"' in region[region.index('id="tutorial-menu-view"'):]
        js = _js()
        assert "exitBtn.hidden" not in js and "menuExitBtn.hidden" not in js

    def test_key_help_is_a_toggle_and_keys_help_a_disclosure(self):
        by_id = _by_id(_region_markup())
        assert by_id["tutorial-keyhelp-btn"]["attrs"].get("aria-pressed") == "false"
        keys = by_id["tutorial-keys-btn"]["attrs"]
        assert keys.get("aria-expanded") == "false" and keys.get("aria-controls") == "tutorial-keys-help"
        assert _text("tutorial-keys-btn") == "Keys aren't working"

    def test_the_lock_notes_exist_and_start_hidden(self):
        by_id = _by_id(_html())
        for element_id in ("model-lock-note", "layout-lock-note"):
            note = by_id[element_id]
            assert "hidden" in note["attrs"]
            assert _text(element_id).startswith("Locked during the tutorial.")


# ---------------------------------------------------------------------------
# Where it is offered
# ---------------------------------------------------------------------------

class TestEntryPoints:
    def test_tutorial_button_sits_after_help_in_the_main_menu(self):
        html = _html()
        nav = html[html.index('<nav aria-label="Main menu"'):html.index("</nav>")]
        assert nav.index('id="nav-help-btn"') < nav.index('id="nav-tutorial-btn"') < nav.index('id="nav-settings-btn"')
        button = _by_id(html)["nav-tutorial-btn"]
        assert button["tag"] == "button" and button["attrs"].get("type") == "button"
        assert _text("nav-tutorial-btn") == "Tutorial"
        # Help still opens the shortcuts dialog directly.
        assert "navHelpBtn.addEventListener('click', openShortcutsDialog)" in _js(VIEWER_JS)

    def test_shortcuts_dialog_opens_with_a_tutorial_section(self):
        html = _html()
        start = html.index('id="shortcuts-dialog"')
        dialog = html[start:html.index("</dialog>", start)]
        section = dialog.index('id="tutorial-shortcuts-section"')
        assert section < dialog.index('id="study-shortcuts-section"') < dialog.index('id="turn-shortcuts-section"')
        by_id = _by_id(dialog)
        labels = {
            "tutorial-dialog-resume-btn": "Resume",
            "tutorial-dialog-start-over-btn": "Start over",
            "tutorial-dialog-lessons-btn": "Choose a lesson",
            "tutorial-dialog-keyhelp-btn": "Key help",
        }
        for element_id, label in labels.items():
            assert by_id[element_id]["attrs"].get("type") == "button"
            assert _text(element_id, dialog) == label
        assert by_id["tutorial-dialog-keyhelp-btn"]["attrs"].get("aria-pressed") == "false"

    def test_dialog_buttons_act_once_the_dialog_has_closed(self):
        """The dialog returns focus to its trigger on close; acting after that
        is what lets the tutorial put focus on its own heading."""
        assert "dialog.addEventListener('close', () => action(), { once: true });" in _js()

    def test_not_offered_on_study_or_the_workshop_viewer(self):
        css = _css_rules()
        for page in ("study-ui", "simple-ui"):
            for target in ("#nav-tutorial-btn", "#tutorial-shortcuts-section", "#tutorial-region"):
                selector = f"body.{page} {target}"
                assert selector in css, f"{selector} is not hidden"
                rule = css[css.index(selector):]
                assert re.match(r"[^{]*\{\s*display:\s*none;", rule), f"{selector} is not display: none"

    def test_resume_is_hidden_on_demo(self):
        css = _css_rules()
        assert re.search(r"body\.demo-ui \.tutorial-resume\s*\{\s*display:\s*none;", css)
        by_id = _by_id(_html())
        assert "tutorial-resume" in by_id["tutorial-dialog-resume-btn"]["attrs"].get("class", "")
        assert "tutorial-resume" in by_id["tutorial-menu-resume"]["attrs"].get("class", "")

    def test_script_is_classic_and_loads_between_study_and_the_consent_script(self):
        """Classic and not deferred: it must decide before the viewer's first
        render whether that render is its own, and be listening before the
        consent script reports."""
        html = _html()
        tags = re.findall(r"<script\b([^>]*)>", html)
        srcs = [re.search(r'src="([^"]+)"', attrs).group(1) if "src=" in attrs else None for attrs in tags]
        tutorial = srcs.index("/static/js/tutorial.js")
        assert srcs.index("/static/js/viewer.js") < srcs.index("/static/js/study.js") < tutorial
        first_inline = srcs.index(None)
        assert tutorial < first_inline
        attrs = tags[tutorial]
        for forbidden in ("defer", "async", "module"):
            assert forbidden not in attrs

    def test_the_route_table(self):
        js = _js()
        block = re.search(r"const TUTORIAL_ROUTES = \[(.*?)\n\];", js, re.DOTALL)
        assert block, "TUTORIAL_ROUTES not found"
        rows = {}
        for route, autostart, offered, resume in re.findall(
            r"\{\s*route:\s*'([^']+)',\s*autostart:\s*(true|false),\s*offered:\s*(true|false),\s*resume:\s*(true|false)\s*\}",
            block.group(1),
        ):
            rows[route] = (autostart == "true", offered == "true", resume == "true")
        assert rows == {
            # The viewer is served at the site's address too (#244).
            "/": (True, True, True),
            "/viewer": (True, True, True),
            "/demo": (False, True, False),
            "/workshop": (False, False, False),
            "?ui=simple": (False, False, False),
            "/study": (False, False, False),
        }

    def test_start_and_off_parameters(self):
        js = _js()
        assert "params.get('tutorial')" in js
        assert "tutorialParam === 'start' && route.offered" in js
        assert "tutorialParam === 'off'" in js


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

class TestRunner:
    def test_checks_registry_has_every_contract_type(self):
        block = re.search(r"^const CHECKS = \{\n(.*?)\n\};", _js(), re.DOTALL | re.MULTILINE)
        assert block, "no top-level const CHECKS = { ... };"
        types = set(re.findall(r"^    (\w+): \{", block.group(1), re.MULTILINE))
        assert types == CHECK_TYPES

    def test_progress_is_kept_in_local_storage_inside_try_catch(self):
        js = _js()
        assert "const STORAGE_KEY = 'cadA11yTutorial';" in js
        for name in ("readRecord()", "writeRecord()"):
            body = _function(js, f"function {name}")
            assert "try {" in body and "catch (_)" in body

    def test_a_first_visit_is_written_at_load_not_read_from_settings(self):
        """The viewer writes its settings* keys on every load, so they cannot
        tell a first visit from a return; the record is written the moment it
        is missing, before the first render decision."""
        js = _js()
        write = js.index("record = normalizeRecord({ status: 'pending', origin: 'first-run' });")
        owns = js.index("cadTutorial.ownsFirstRender = mayAutostart;")
        assert write < owns
        record_code = js[js.index("function readRecord()"):owns]
        assert "settings" not in record_code

    def test_answers_are_buttons_never_radios(self):
        js = _js()
        answers = _function(js, "function renderAnswers(")
        assert "document.createElement('button')" in answers and "button.type = 'button'" in answers
        assert "radio" not in answers
        assert "'radio'" not in js

    def test_nothing_speaks_or_advances_on_a_timer(self):
        """WCAG 2.2.1: no re-prompts and no auto-advance. The one timer only
        forgets which viewer message was written in this tick."""
        js = _js()
        assert "setInterval" not in js
        timers = re.findall(r"setTimeout\((.*?)\);", js)
        assert timers == ["() => { viewerAlertThisTick = null; }, 0"]
        # The lessons request gives up by itself rather than on a timer here.
        assert "AbortSignal.timeout(LESSONS_TIMEOUT_MS)" in js

    def test_it_speaks_through_the_viewers_alert_window(self):
        """The same window as the viewer's answers to keys, so a key pressed while
        the tutorial is reading replaces it (#245 review: Jen pressed "." and the
        step went on being read). A viewer answer in the same tick is kept at the
        front of the tutorial's words, not overwritten."""
        js = _js()
        assert "study.announceAlert === 'function' ? study.announceAlert : study.announce" in js
        assert "data.politeness !== 'assertive'" in _function(js, "function noteViewerMessage(")
        assert "if (viewerAlertThisTick) message = `${sentence(viewerAlertThisTick)} ${message}`;" in js
        assert "aria-live" not in js
        bridge = _js(VIEWER_JS).split("window.cadStudy = {")[1].split("\n};")[0]
        assert "announceAlert: announceAlert," in bridge

    def test_n_b_c_share_the_viewers_guards_and_never_run_on_study(self):
        js = _js()
        handler = js[js.index("document.addEventListener('keydown', function (e) {"):]
        for guard in (
            "if (runner.view !== 'lesson') return;",
            "if (formControl) return;",
            "if (document.querySelector('dialog[open]')) return;",
            "if (e.metaKey || e.ctrlKey || e.altKey) return;",
            "if (getState().single_key_shortcuts === false) return;",
        ):
            assert guard in handler
        assert "'escape'" not in handler.lower()
        # Inert on /study before anything is wired.
        assert js.index("if (path === '/study'") < js.index("study.onInteraction.push(")

    def test_hooks_object_is_created_before_anything_can_return(self):
        js = _js()
        assert js.index("window.cadTutorial = cadTutorial;") < js.index("if (path === '/study'")
        for field in ("ownsFirstRender", "holdDisplay", "keyHelpActive", "captureDeviceKey", "describeKey"):
            assert f"{field}:" in js[:js.index("window.cadTutorial = cadTutorial;")]

    def test_the_first_render_is_always_handed_back(self):
        js = _js()
        release = _function(js, "function releaseFirstRender(")
        assert "setPendingInputSource?.('init')" in release and "sendStateToServer()" in release
        start = _function(js, "async function startAtLoad(")
        assert start.count("releaseFirstRender();") >= 2, "released on failure and after starting"

    def test_analytics_only_with_consent_and_never_on_demo(self):
        track = _function(_js(), "function track(")
        assert "window.CAD_DEMO_MODE || consentGiven !== true" in track
        assert "window.cadTrackEvent('tutorial'" in track
        assert "window.cadTrackEvent = trackEvent;" in _html()

    def test_training_wheels_lock_the_model_upload_and_layout(self):
        lock = _function(_js(), "function setLocked(")
        for target in ("model-list-dropdown", "upload-model-input", "input[name=\"view-mode\"]", "model-lock-note", "layout-lock-note"):
            assert target in lock
        viewer = _js(VIEWER_JS)
        assert "dropdown.disabled = tutorialLocksControls();" in _function(viewer, "function updateModelList(")

    def test_axis_mode_is_changed_without_saving_it_and_put_back(self):
        js = _js()
        restore = _function(js, "function restoreAfterRun(")
        assert "persist: false" in restore
        assert "storedAxisMode()" in restore


# ---------------------------------------------------------------------------
# Hooks in the viewer, the page and the device scripts
# ---------------------------------------------------------------------------

def _keydown_handler() -> str:
    js = _js(VIEWER_JS)
    start = js.index("document.addEventListener('keydown', function(e) {")
    return js[start:js.index("\n});", start)]


class TestViewerHooks:
    def test_interactions_reach_listeners_on_every_page(self):
        body = _function(_js(VIEWER_JS), "function reportStudyInteraction(")
        assert "studyMode" not in body
        assert "if (!window.cadStudy) return;" in body

    def test_render_events_are_reported_but_not_on_study(self):
        """study.py's event allowlist has no "render", and a refused event is a
        console error on the page CI checks."""
        js = _js(VIEWER_JS)
        assert "if (!studyMode) {\n                reportStudyInteraction('render', {" in js

    def test_dialogs_and_export_report_ui_actions(self):
        js = _js(VIEWER_JS)
        controller = _function(js, "function makeInfoDialogController(")
        assert "reportStudyInteraction('ui_action', { name: dialog.id, action: 'open' });" in controller
        assert "reportStudyInteraction('ui_action', { name: dialog.id, action: 'close' });" in controller
        # The three callers are unchanged.
        for pair in ("shortcutsDialog, shortcutsHeading", "aboutDialog, aboutHeading", "settingsDialog, settingsHeading"):
            assert f"makeInfoDialogController({pair})" in js
        assert "reportStudyInteraction('ui_action', { name: 'export', action: 'click' });" in js

    def test_devices_report_connecting_and_dropping(self):
        js = _js(VIEWER_JS)
        assert "reportDeviceConnection('monarch', connected);" in _function(js, "function setMonarchHidConnected(")
        assert "reportDeviceConnection('dotpad', connected);" in _function(js, "function setDotpadConnected(")
        assert "window.reportDeviceConnection = reportDeviceConnection;" in js
        for path, device in ((TRINKEY_JS, "slider"), (WITMOTION_JS, "cube")):
            source = _js(path)
            assert f"window.reportDeviceConnection?.('{device}', true);" in source
            assert f"window.reportDeviceConnection?.('{device}', false);" in source

    def test_get_state_and_the_load_source(self):
        js = _js(VIEWER_JS)
        bridge = js.split("window.cadStudy = {")[1].split("\n};")[0]
        assert "getState: tutorialStateSnapshot," in bridge
        snapshot = _function(js, "function tutorialStateSnapshot(")
        for field in (
            "model:", "cursor_col:", "cursor_row:", "compose_scrollbar:", "compose_slicegraph:",
            "slicegraph_locked:", "display_connected:", "monarch_connected:", "dotpad_connected:",
            "single_key_shortcuts:", "last_render_state:",
        ):
            assert field in snapshot, f"getState() lacks {field}"
        assert "function loadStudyModel(stem, label, defaults, source)" in js
        assert "pendingInputSource = source ? String(source) : 'study';" in js

    def test_tutorial_renders_are_tagged(self):
        js = _js(VIEWER_JS)
        assert "if (state.input_source === 'tutorial') renderHeaders['X-CAD-Tutorial'] = '1';" in js

    def test_the_tutorial_owns_the_first_render_after_the_study_return(self):
        js = _js(VIEWER_JS)
        comment = js.index("// Send initial state to server")
        assert "if (studyMode) return;" in js[comment - 600:comment]
        tail = js[comment:]
        ready = tail.index("document.dispatchEvent(new CustomEvent('cad:viewer-ready'));")
        owns = tail.index("if (window.cadTutorial && window.cadTutorial.ownsFirstRender) return;")
        render = tail.index("pendingInputSource = 'init';\n    sendStateToServer();")
        assert ready < owns < render

    def test_the_test_pattern_holds_the_display(self):
        js = _js(VIEWER_JS)
        held = js.index("const displayHeld = Boolean(window.cadTutorial && window.cadTutorial.holdDisplay);")
        assert held < js.index("if (!displayHeld && typeof window._dotpadOnRender === 'function')")
        assert held < js.index("if (!displayHeld && typeof window._monarchHidOnRender === 'function'")
        # No unguarded send left behind.
        assert "if (typeof window._dotpadOnRender === 'function')" not in js
        assert "if (typeof window._monarchHidOnRender === 'function'" not in js

    def test_key_help_comes_before_the_guards_and_the_report(self):
        """Before the single-key guard, so a letter is described with single-key
        shortcuts off, saying it does nothing now, and a held key is described
        once (#245 review)."""
        handler = _keydown_handler()
        key_help = handler.index("window.cadTutorial.keyHelpActive")
        assert handler.index("if (!supportedShortcuts.has(normalizedKey))") < key_help
        assert key_help < handler.index("!viewerState.singleKeyShortcuts && normalizedKey.length === 1")
        assert key_help < handler.index("if (e.repeat && !repeatableShortcuts.has(normalizedKey))")
        assert key_help < handler.index("reportStudyInteraction('keyboard', {")
        block = handler[key_help:handler.index("reportStudyInteraction('keyboard', {")]
        assert "if (e.repeat) return;" in block
        assert "inactive: !viewerState.singleKeyShortcuts && normalizedKey.length === 1" in block

    def test_focus_goes_to_the_tutorial_and_pageshow_only_refocuses_from_the_cache(self):
        js = _js(VIEWER_JS)
        assert "#tutorial-region:not([hidden]) h2" in _function(js, "function focusTopOfPage(")
        assert "window.addEventListener('pageshow', function(event) {\n    if (event.persisted) focusTopOfPage();" in js

    def test_fit_uses_the_display_grid_and_does_not_read_out_a_device_key(self):
        fit = _function(_js(VIEWER_JS), "async function fitCurrentViewToDevice(")
        assert "target_pixel_width: grid.pixelWidth," in fit
        assert "target_pixel_height: grid.pixelHeight," in fit
        assert "'View fitted to the display'" in fit
        assert "payload.output_device}" not in fit

    def test_the_demo_list_keeps_the_mug_while_the_tutorial_runs(self):
        entries = _function(_js(VIEWER_JS), "function _visibleModelEntries(")
        assert "window.cadTutorial.running" in entries and "TUTORIAL_MODEL_STEM" in entries


class TestPageHooks:
    def test_consent_settled_is_dispatched_once_from_every_ending(self):
        html = _html()
        assert html.count("new CustomEvent('cad:consent-settled'") == 1
        settle = html[html.index("function settleConsent("):]
        settle = settle[:settle.index("\n        }")]
        assert "if (consentSettled) return;" in settle and "consentSettled = true;" in settle
        assert "detail: { reason: reason, consent_given: consentGiven }" in settle
        init = html[html.index("async function initSession()"):html.index("dialog.showModal();")]
        returns = [line.strip() for line in init.splitlines() if re.search(r"\breturn\b", line) and "function" not in line]
        assert returns, "no early returns found"
        for line in returns:
            assert "settleConsent(" in line, f"an early return does not settle consent: {line}"
        assert "settleConsent('submitted', action === 'accept');" in html
        assert "dialog.addEventListener('close', function () { settleConsent('dismissed', null); });" in html


class TestDeviceHooks:
    def test_the_monarch_offers_each_press_to_the_tutorial_first(self):
        body = _function(_js(MONARCH_JS), "function handleMonarchCommand(")
        capture = body.index("captureDeviceKey")
        assert capture < body.index("if (!command) return;") < body.index("command.type")

    def test_the_dotpad_offers_each_press_to_the_tutorial_first(self):
        body = _function(_js(DOTPAD_JS), "function onKey(")
        capture = body.index("captureDeviceKey")
        assert capture < body.index("if (letter === 'x'") < body.index("window.stepSliceDepth(-100/n)")

    def test_the_dotpad_can_show_a_ready_made_graphic(self):
        assert "window._dotpadShowHex = function (graphicHex) {" in _js(DOTPAD_JS)

    def test_device_names_match_the_contract(self):
        names = {"dot1", "dot4", "cursor", "axis-x", "axis-y", "axis-z", "move-left", "move-right", "move-up", "move-down", "other"}
        for path in (MONARCH_JS, DOTPAD_JS):
            found = set(re.findall(r"'(dot[14]|cursor|axis-[xyz]|move-(?:left|right|up|down)|other)'", _js(path)))
            found |= {f"axis-{a}" for a in "xyz"} if "`axis-${command.axis}`" in _js(path) else set()
            assert found == names, f"{path.name}: {sorted(names - found)} missing"


class TestContinuousIntegration:
    def test_axe_checks_the_tutorial_lesson_and_the_lesson_list(self):
        ci = CI_YML.read_text(encoding="utf-8")
        pages = ci[ci.index("const pages = ["):]
        assert pages.index("{ name: '/viewer', url: `${base}/viewer` },") < pages.index("/viewer (tutorial lesson)")
        assert pages.index("/viewer (tutorial lesson)") < pages.index("/viewer (tutorial lesson list)")
        assert "await page.click('#consent-decline');" in ci
        assert "await page.click('#tutorial-lessons-btn');" in ci


class TestReviewRegressions:
    """Each of these was a defect found by review and seen or traced in a browser.
    The runner has no JavaScript test harness in this repo, so they pin the code
    that fixes each one, with the reason written next to it."""

    def test_a_missing_slicegraph_ready_means_ready(self):
        # The server only ever sends slicegraph_ready: false while the graph is
        # being computed and leaves the key out once it is ready, so lesson 10's
        # "graph is ready" step never passed while this read `=== true`.
        source = _js(VIEWER_JS)
        assert "slicegraph_ready: Boolean(state.compose_slicegraph) && data.slicegraph_ready !== false," in source
        assert "slicegraph_ready: data.slicegraph_ready === true" not in source

    def test_consent_from_the_database_reaches_the_tutorial_as_a_boolean(self):
        # sessions.consent_given is 1 or 0; the tutorial tracks only on `=== true`.
        html = VIEWER_HTML.read_text(encoding="utf-8")
        assert "settleConsent('decided', Boolean(sessionData.consent_given))" in html

    def test_every_way_out_turns_key_help_off(self):
        source = _js()
        close = source[source.index("function closeRegion() {"):]
        close = close[:close.index("\n    }\n")]
        assert "setKeyHelp(false, { silent: true });" in close
        # And the dialog's toggle only shows with a lesson on screen, where the
        # region's own toggle is too.
        assert "!(cadTutorial.running && runner.view === 'lesson')" in source

    def test_the_heading_and_so_the_region_says_tutorial(self):
        assert "`Tutorial, lesson ${runner.lessonIndex + 1} of ${lessons().length}: ${lesson.title}`" in _js()

    def test_the_dialogs_tutorial_buttons_are_a_labelled_group(self):
        html = VIEWER_HTML.read_text(encoding="utf-8")
        assert '<h3 id="tutorial-shortcuts-heading">Tutorial</h3>' in html
        assert 'role="group" aria-labelledby="tutorial-shortcuts-heading"' in html

    def test_band_feedback_speaks_only_positions_inside_the_band(self):
        source = _js()
        where = source[source.index("function whereAgainstBand("):]
        where = where[:where.index("\n    }\n")]
        assert "Math.ceil(Number(target.from) - offset)" in where and "Math.floor(Number(target.to) - offset)" in where
        # In XYZ mode the numbers are the viewer's, from the model's origin (#235).
        assert "state.cut_origin_percent" in where
        # In Turn mode it speaks the depth the viewer just announced.
        assert "state.axis_mode !== 'xyz'" in where and "reader_depth" in where

    def test_the_far_side_is_measured_in_reader_depth(self):
        assert "reader_depth: Math.round(depthFromPlanePosition(" in _js(VIEWER_JS)
        assert "return Number.isFinite(depth) ? 100 - depth : 100;" in _js()

    def test_band_names_are_compared_without_the_axis_prefix(self):
        assert "shortBandName(b.name) === wanted" in _js()

    def test_restore_keeps_a_model_the_person_chose_and_their_axis_mode(self):
        source = _js()
        restore = source[source.index("function restoreAfterRun() {"):]
        restore = restore[:restore.index("\n    }\n")]
        assert "savedStillThere && getState().model === practiceModel()" in restore
        assert "axis_mode: axisMode || getState().axis_mode," in restore

    def test_a_turn_mode_step_reopens_in_turn_mode(self):
        source = _js()
        enter = source[source.index("function enterLesson("):]
        enter = enter[:enter.index("\n    }\n")]
        assert enter.index("const targetWhen") < enter.index("applyPose(lesson.pose, { force: forcePose, overrides })")

    def test_lesson_16_waits_for_an_actual_change_of_model(self):
        source = _js()
        block = source[source.index("    model_changed: {"):]
        block = block[:block.index("\n    },\n")]
        assert "start: (check, ctx) => ({ was: ctx.entry.model })" in block
        assert "model !== s.was" in block

    def test_a_returning_visitor_is_marked_offered_before_tutorial_off_is_honoured(self):
        source = _js()
        settled = source[source.index("function onConsentSettled(detail) {"):]
        assert settled.index("record.status = 'offered';") < settled.index("if (!route.offered || suppressed) {")

    def test_exit_promises_resume_only_where_there_is_one(self):
        assert "canResume ? 'Resume any time from the Tutorial button.' : 'Open it again from the Tutorial button.'" in _js()

    def test_another_tabs_progress_is_taken_not_overwritten(self):
        source = _js()
        assert "window.addEventListener('storage', (event) => {" in source
        assert "record.lessons = { ...record.lessons, ...fresh.lessons };" in source

    def test_a_blocked_lesson_starts_when_a_display_connects(self):
        source = _js()
        assert "runner.phase === 'blocked' && ev.type === 'device' && ev.data.connected" in source


class TestSecondReview:
    """The #245 review's findings, each pinned where it was fixed. As above, the
    runner has no JavaScript harness here; each was also checked in a browser."""

    def test_the_mug_is_the_first_render_at_once(self):
        # Rendered as the viewer becomes ready, while ownsFirstRender still holds
        # the viewer's own render back, and not announced, so the consent
        # dialog's confirmation is not talked over.
        js = _js()
        ready = js[js.index("document.addEventListener('cad:viewer-ready', function () {"):]
        ready = ready[:ready.index("\n    });")]
        assert "if (cadTutorial.ownsFirstRender) renderMugFirst();" in ready
        mug = _function(js, "function renderMugFirst(")
        assert "applyPose(DEFAULT_POSE, { force: true });" in mug
        assert "announceNextMugRender = false;" in mug
        assert "if (mugRenderedFirst) return;" in _function(js, "function releaseFirstRender(")
        assert "cadTutorial.ownsFirstRender = false;" not in _function(js, "function applyPose(")
        assert "applyPose(DEFAULT_POSE);" not in _function(js, "async function startAtLoad(")

    def test_only_a_first_run_reopens_by_itself(self):
        assert "(record.status === 'in-progress' && record.origin === 'first-run')" in _js()

    def test_the_lesson_list_unlocks_the_controls(self):
        assert "setLocked(false);" in _function(_js(), "async function openMenu(")

    def test_a_test_pattern_that_cannot_be_drawn_lets_the_dots_go(self):
        body = _function(_js(), "async function nextTestPatternRound(")
        assert body.count("testPattern.active = false;") == 2

    def test_a_late_locate_answer_is_dropped_when_the_step_changes(self):
        assert "locateSeq += 1;" in _function(_js(), "function leaveStep(")

    def test_the_end_of_the_tutorial_is_saved_as_finished(self):
        body = _function(_js(), "function passStep(")
        assert "if (!next || (next.part === 'extras' && lesson.part !== 'extras')) {" in body
        assert "record.status = 'completed';" in body

    def test_next_goes_straight_on_from_a_step_already_done(self):
        js = _js()
        body = _function(js, "function continueAction(")
        assert body.index("if (stepWasPassed()) {") < body.index("showDemoFrame(frames, step);")
        assert "runner.passed.add(stepKey());" in _function(js, "function passStep(")
        assert "runner.passed.clear();" in _function(js, "function startOver(")

    def test_not_quite_is_in_jens_words(self):
        assert "'Not quite. Try Hint to find out how to complete this, or Skip step to move on.'" in _js()
        assert "This step finishes when you do it" not in _js()

    def test_show_me_is_only_for_steps_that_wait_for_the_cut(self):
        by_id = _by_id(_region_markup())
        assert by_id["tutorial-show-me-btn"]["attrs"].get("type") == "button"
        assert "hidden" in by_id["tutorial-show-me-btn"]["attrs"]
        moves = _function(_js(), "function showMeMoves(")
        assert "need === 'cube' || need === 'slider'" in moves
        for kind in ("'in_band'", "'mark_band'", "'edge'", "'sweep'", "check.field === 'cut_axis'"):
            assert kind in moves
        for kind in ("'key'", "'keys'", "'answer'"):
            assert kind not in moves

    def test_n_b_and_c_leave_a_focused_list_alone(self):
        js = _js()
        handler = js[js.index("document.addEventListener('keydown', function (e) {"):]
        assert "tagName === 'select'" in handler
        assert handler.index("if (formControl) return;") < handler.index("if (key === 'n') continueAction();")

    def test_the_viewers_letters_leave_a_focused_list_alone(self):
        handler = _keydown_handler()
        guard = handler.index("const ownsTypeAhead")
        assert "target.closest('select, [role=\"listbox\"]')" in handler[guard:guard + 300]
        assert guard < handler.index("switch(normalizedKey)")

    def test_unset_axis_mode_is_xyz_now(self):
        assert "return stored === 'turn' ? 'turn' : 'xyz';" in _function(_js(), "function storedAxisMode(")

    def test_exit_gives_back_the_whole_view(self):
        js = _js()
        assert "view_state: typeof study.captureView === 'function' ? study.captureView() : null," in js
        assert "restore: saved.view_state || undefined," in _function(js, "function restoreAfterRun(")
        viewer = _js(VIEWER_JS)
        assert "if (wanted.restore) restoreViewState(wanted.restore);" in viewer
        assert "viewerState.currentMoveCamera = defaults && defaults.restore ? 'none' : 'reset';" in viewer
        restore = _function(viewer, "function restoreViewState(")
        for part in ("orientationRight", "slicePlanes", "cameraCenterByViewOrientation", "currentWorldCameraCenter"):
            assert part in restore

    def test_the_study_log_keeps_to_its_own_events(self):
        study = _js(ROOT / "static" / "js" / "study.js")
        assert ("const STUDY_EVENT_TYPES = new Set(['keyboard', 'announcement', 'model_loaded', "
                "'page_load', 'page_unload', 'error']);") in study
        assert "if (!sessionActive || !STUDY_EVENT_TYPES.has(eventType)) return;" in study
