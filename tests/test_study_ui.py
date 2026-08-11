"""Structural guardrails on the two study interfaces.

The participant view is used by BLV adults on their own screen reader and braille
display, mid-session, with no chance to re-run it if something announces wrongly.
The experimenter panel is used by experimenters who include screen reader users.
Both are checked here for the properties that are invisible in review and
expensive to discover live.

Parsing is stdlib-only, matching test_live_regions.py and
test_main_menu_and_layout.py: no package.json, no JS runner, and bs4/lxml are not
in requirements.txt.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWER_HTML = ROOT / "accessible-3d-viewer.html"
CONTROL_HTML = ROOT / "study-control.html"
VIEWER_JS = ROOT / "static" / "js" / "viewer.js"
STUDY_JS = ROOT / "static" / "js" / "study.js"
CONTROL_JS = ROOT / "static" / "js" / "study-control.js"
VIEWER_CSS = ROOT / "static" / "css" / "viewer.css"
CONTROL_CSS = ROOT / "static" / "css" / "study-control.css"


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrib = dict(attrs)
        self.elements.append({"tag": tag, "attrs": attrib, "id": attrib.get("id")})


def _elements(path: Path) -> list[dict]:
    collector = _Collector()
    collector.feed(path.read_text(encoding="utf-8"))
    return collector.elements


def _by_id(path: Path) -> dict[str, dict]:
    return {el["id"]: el for el in _elements(path) if el["id"]}


def _css_rules(path: Path) -> str:
    """The stylesheet with /* comments */ stripped.

    Comments here explain why a property is absent, so they name the very
    properties these tests check for. Matching against them would fail on the
    explanation rather than on the rule.
    """
    return re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Participant view
# ---------------------------------------------------------------------------

class TestStudyRegionMarkup:
    def test_region_exists_and_is_hidden_by_default(self):
        """Present in the shared markup, revealed only at /study. Hidden rather
        than absent so the ordinary viewer parses identically."""
        region = _by_id(VIEWER_HTML).get("study-region")
        assert region is not None, "missing #study-region"
        assert "hidden" in region["attrs"]

    def test_region_is_labelled_by_its_heading(self):
        region = _by_id(VIEWER_HTML)["study-region"]
        assert region["attrs"].get("aria-labelledby") == "study-step-heading"

    def test_region_is_not_a_second_banner(self):
        """The page already has role="banner". A second one makes the landmark
        ambiguous in a screen reader's landmark list."""
        banners = [
            el for el in _elements(VIEWER_HTML) if el["attrs"].get("role") == "banner"
        ]
        assert len(banners) == 1

    def test_region_comes_before_the_viewer_controls(self):
        """A screen reader must meet the current step before the controls, and
        the skip link must land on it."""
        html = VIEWER_HTML.read_text(encoding="utf-8")
        assert html.index('id="study-region"') < html.index('id="left-col"')
        assert html.index('id="main-content"') < html.index('id="study-region"')

    def test_study_shortcuts_section_exists_and_is_hidden_by_default(self):
        """Otherwise H/? on the participant page opens the ordinary Keyboard
        Shortcuts dialog with no mention of N/B/C at all -- a participant has
        no way to discover the study commands."""
        section = _by_id(VIEWER_HTML).get("study-shortcuts-section")
        assert section is not None, "missing #study-shortcuts-section"
        assert "hidden" in section["attrs"]

    def test_study_shortcuts_section_lives_inside_the_shortcuts_dialog(self):
        html = VIEWER_HTML.read_text(encoding="utf-8")
        dialog_start = html.index('id="shortcuts-dialog"')
        dialog_end = html.index("</dialog>", dialog_start)
        assert dialog_start < html.index('id="study-shortcuts-section"') < dialog_end

    def test_study_shortcuts_documents_next_back_and_repeat(self):
        html = VIEWER_HTML.read_text(encoding="utf-8")
        start = html.index('id="study-shortcuts-section"')
        section = html[start:html.index("</div>", start)]
        for key in ("N", "B", "C"):
            assert f"<kbd>{key}</kbd>" in section, f"study shortcuts section does not document {key}"

    def test_study_js_reveals_the_shortcuts_section(self):
        js = STUDY_JS.read_text(encoding="utf-8")
        assert "studyShortcuts.hidden = false" in js

    def test_study_model_loads_do_not_announce_progress_chatter(self):
        """A study-sourced model load used to also announce "processing
        started", "generating render" and "loaded" on top of the one
        consolidated step announcement -- the same event read up to four
        times over. All three are now gated off for source === 'study'."""
        js = VIEWER_JS.read_text(encoding="utf-8")
        begin_fn = js.split("function beginModelLoadAnnouncement(")[1].split("\n}")[0]
        assert "if (source !== 'study') announce(" in begin_fn

        assert "if (activeModelLoadTask && activeModelLoadTask.source !== 'study') {" in js
        assert "if (activeModelLoadTask.source !== 'study') {" in js

    def test_heading_can_receive_focus_without_joining_the_tab_order(self):
        heading = _by_id(VIEWER_HTML)["study-step-heading"]
        assert heading["tag"] == "h2"
        assert heading["attrs"].get("tabindex") == "-1"

    def test_heading_is_not_its_own_live_region(self):
        """It used to be aria-live="polite" on its own, which meant a step
        change announced the title once from here and then again as part of
        the combined announcement below -- the same words twice. It is still a
        real heading (landmark navigation still works), just not auto-read."""
        heading = _by_id(VIEWER_HTML)["study-step-heading"]
        assert heading["tag"] == "h2"
        assert heading["attrs"].get("aria-live") is None

    def test_step_progress_is_not_its_own_live_region_either(self):
        """Same reasoning as the heading: role="status" here used to announce
        the step count on its own, on top of the combined announcement."""
        progress = _by_id(VIEWER_HTML)["study-step-progress"]
        assert progress["attrs"].get("role") is None

    def test_nothing_in_a_running_session_interrupts_the_participant(self):
        """No assertive live region on anything shown while the session is
        running. A participant is reading braille with both hands while the
        display refreshes, and an interruption mid-slice is the disruption the
        study is trying not to introduce.

        The join form is the exception, and it is only on screen before the
        session starts: a code that did not match has to be heard, there is no
        braille reading in progress to interrupt, and it is the same pattern the
        consent dialog's email error already uses."""
        html = VIEWER_HTML.read_text(encoding="utf-8")
        region = html[html.index('id="study-region"'):html.index('<div class="layout">')]
        join_form = region[region.index('id="study-join-form"'):region.index("</form>")]
        during_session = region.replace(join_form, "")

        assert 'aria-live="assertive"' not in during_session
        assert 'role="alert"' not in during_session
        # And the exception really is confined to the join form.
        assert 'role="alert"' in join_form

    def test_ready_button_is_labelled_in_full(self):
        """Voice control users speak the visible label, so "Ready" alone is
        ambiguous against everything else on the page."""
        html = VIEWER_HTML.read_text(encoding="utf-8")
        assert "I am ready to move on" in html

    def test_ready_button_starts_disabled(self):
        """Nothing to be ready for until the experimenter starts the session."""
        button = _by_id(VIEWER_HTML)["study-ready-btn"]
        assert "disabled" in button["attrs"]

    def test_study_script_is_loaded(self):
        html = VIEWER_HTML.read_text(encoding="utf-8")
        assert "/static/js/study.js" in html


class TestStudyModeBehaviour:
    def test_consent_dialog_is_skipped_on_study(self):
        """Study participants consent on paper before the session. A modal in
        front of a participant mid-onboarding would be both redundant and
        disruptive."""
        html = VIEWER_HTML.read_text(encoding="utf-8")
        assert "'/study'" in html and "return" in html

    def test_model_chooser_is_hidden_in_study_mode(self):
        """The model is decided by the protocol step; a participant browsing to a
        later task's model would spoil it."""
        css = VIEWER_CSS.read_text(encoding="utf-8")
        assert 'body.study-ui section[aria-labelledby="model-list-heading"]' in css
        assert "display: none" in css.split("body.study-ui")[1][:120]

    def test_viewer_never_reveals_the_model_name_in_study_mode(self):
        """The status bar shows a neutral label. Reading the stem out answers the
        question the participant is being asked to work out by touch."""
        js = VIEWER_JS.read_text(encoding="utf-8")
        assert "studyModelLabel" in js
        assert "if (studyMode) {" in js

    def test_render_requests_are_tagged_with_the_session(self):
        js = VIEWER_JS.read_text(encoding="utf-8")
        assert "X-Study-Session" in js

    def test_page_load_does_not_render_in_study_mode(self):
        """The first render belongs to the protocol step, not to page load;
        otherwise an arbitrary model reaches the braille display."""
        js = VIEWER_JS.read_text(encoding="utf-8")
        tail = js[js.index("// Send initial state to server") - 600:]
        assert "if (studyMode) return;" in tail

    def test_study_defaults_are_applied_on_every_model_load(self):
        js = VIEWER_JS.read_text(encoding="utf-8")
        assert "function applyStudyDefaults" in js
        assert "applyStudyDefaults(defaults)" in js

    def test_study_driver_reports_interactions(self):
        js = STUDY_JS.read_text(encoding="utf-8")
        assert "/study/event" in js
        assert "onInteraction.push(report)" in js

    def test_study_driver_sends_the_ready_signal(self):
        js = STUDY_JS.read_text(encoding="utf-8")
        assert "/study/step/ready" in js

    def test_the_model_stem_is_used_to_load_and_never_to_display(self):
        """The stem has to reach the browser -- a model is addressed by name now,
        because a list position means a different file after anyone uploads one
        (#123). So the protection is not that the name is absent, it is that the
        name is never rendered: it goes straight into the load call and nowhere
        near the DOM."""
        js = STUDY_JS.read_text(encoding="utf-8")
        assert "model.stem" in js, "study.js should load by stem"

        # Every use of the stem in the driver: stored for change detection, and
        # handed to the loader. Nothing else.
        stem_uses = [line.strip() for line in js.splitlines() if "model.stem" in line]
        assert len(stem_uses) <= 3, f"unexpected uses of the model stem: {stem_uses}"
        for line in stem_uses:
            assert "textContent" not in line and "innerHTML" not in line, (
                f"the model name is being written to the page: {line}"
            )

        # What the participant is told the object is called.
        assert "model.label" in js

    def test_the_status_bar_shows_the_label_rather_than_the_stem(self):
        js = VIEWER_JS.read_text(encoding="utf-8")
        # In study mode the model list never rebuilds the chooser and the status
        # bar is fed the neutral label.
        assert "sbModel.textContent = studyModelLabel" in js
        # And the loader sets it from the label, not from the stem it was given.
        loader = js.split("function loadStudyModel")[1].split("\n}")[0]
        assert "sbModel.textContent = studyModelLabel" in loader
        assert "sbModel.textContent = stem" not in loader

    def test_next_back_and_repeat_are_keyboard_commands(self):
        """N/B/C so a participant never has to find a control with a screen
        reader mid-exploration."""
        js = STUDY_JS.read_text(encoding="utf-8")
        keydown = js[js.index("document.addEventListener('keydown'"):]
        assert "key === 'n'" in keydown and "signalReady();" in keydown
        assert "key === 'b'" in keydown and "goBack();" in keydown
        assert "key === 'c'" in keydown and "repeatStep();" in keydown

    def test_repeat_is_read_only(self):
        """C only re-announces; it must never reach the network."""
        js = STUDY_JS.read_text(encoding="utf-8")
        repeat_fn = js[js.index("function repeatStep("):]
        repeat_fn = repeat_fn[:repeat_fn.index("\n    }")]
        assert "fetch(" not in repeat_fn

    def test_back_is_refused_client_side_outside_solo_mode(self):
        """The server also refuses it (a paired session has no token to send),
        but the client should not even try, and should say why."""
        js = STUDY_JS.read_text(encoding="utf-8")
        back_fn = js[js.index("function goBack("):]
        back_fn = back_fn[:back_fn.index("\n    }")]
        assert "if (!isSolo())" in back_fn
        assert "Only your experimenter can move back a step." in back_fn

    def test_a_step_change_is_announced_as_one_utterance(self):
        """The heading, the step counter and the step text used to update as
        three separately-timed live regions (and the step text was not a live
        region at all), which is what made the announcement inconsistent."""
        js = STUDY_JS.read_text(encoding="utf-8")
        # Either name reaches the same function (window.cadStudy exposes both,
        # see test_the_bridge_exposes_a_polite_announcer).
        assert "window.cadStudy.announce" in js
        step_change = js[js.index("if (stepChanged) {"):]
        step_change = step_change[:step_change.index("\n    }\n")]
        assert "announce(" in step_change

    def test_every_step_change_uses_one_consolidated_announcement(self):
        """Self-triggered and experimenter-driven changes used to get
        different announcements (full title+content vs. a step-and-title-only
        version); that distinction was simplified away in favor of always
        announcing the step number plus the participant-facing text (falling
        back to the title for a step with none)."""
        js = STUDY_JS.read_text(encoding="utf-8")
        step_fn = js[js.index("function stepAnnouncement("):]
        step_fn = step_fn[:step_fn.index("\n    }")]
        assert "state.text" in step_fn
        assert "state.title" in step_fn

    def test_the_bridge_exposes_a_polite_announcer(self):
        js = VIEWER_JS.read_text(encoding="utf-8")
        cad_study = js.split("window.cadStudy = {")[1].split("\n};")[0]
        assert "announcePolite" in cad_study


# ---------------------------------------------------------------------------
# Experimenter panel
# ---------------------------------------------------------------------------

class TestControlPanelAccessibility:
    def test_page_has_one_h1_and_a_main_landmark(self):
        elements = _elements(CONTROL_HTML)
        assert sum(1 for el in elements if el["tag"] == "h1") == 1
        assert any(el["id"] == "main-content" for el in elements)

    def test_skip_link_targets_main(self):
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert 'href="#main-content"' in html

    def test_has_both_an_alert_and_a_status_region(self):
        """Experimenters running sessions include screen reader users, so state
        changes have to reach a live region rather than only being visible."""
        by_id = _by_id(CONTROL_HTML)
        assert by_id["alert-region"]["attrs"].get("role") == "alert"
        assert by_id["status-region"]["attrs"].get("role") == "status"

    def test_every_form_control_has_a_label(self):
        elements = _elements(CONTROL_HTML)
        labelled = {
            el["attrs"].get("for") for el in elements if el["tag"] == "label"
        }
        for el in elements:
            if el["tag"] not in ("input", "select", "textarea"):
                continue
            control_id = el["id"]
            attrs = el["attrs"]
            if attrs.get("type") in ("submit", "button", "hidden"):
                continue
            assert control_id, f"unlabelled {el['tag']} with no id"
            has_label = (
                control_id in labelled
                or "aria-label" in attrs
                or "aria-labelledby" in attrs
            )
            assert has_label, f"#{control_id} has no accessible name"

    def test_every_section_is_labelled(self):
        for el in _elements(CONTROL_HTML):
            if el["tag"] != "section":
                continue
            attrs = el["attrs"]
            assert "aria-labelledby" in attrs or "aria-label" in attrs, (
                f"section {el['id']} is an unlabelled landmark"
            )

    def test_the_four_dialogs_share_the_viewers_info_dialog_pattern(self):
        """Current Step (C), Strategy Prompts (S), Jump to Step (J) and Help (H
        or ?) all follow the same accessible-dialog pattern established for the
        participant viewer's Help/About/Settings dialogs: native <dialog>, the
        shared .info-dialog chrome, and a heading that takes focus on open
        rather than the first interactive control (ARIA APG dialog pattern)."""
        by_id = _by_id(CONTROL_HTML)
        for dialog_id, heading_id in (
            ("current-step-dialog", "current-step-heading"),
            ("strategy-dialog", "strategy-heading"),
            ("jump-dialog", "jump-heading"),
            ("help-dialog", "help-heading"),
        ):
            dialog = by_id.get(dialog_id)
            assert dialog is not None, f"expected a #{dialog_id}"
            assert dialog["tag"] == "dialog"
            assert "info-dialog" in dialog["attrs"].get("class", "")
            assert "aria-modal" not in dialog["attrs"]

            heading = by_id.get(heading_id)
            assert heading is not None, f"expected a #{heading_id}"
            assert heading["attrs"].get("tabindex") == "-1"

    def test_shared_dialog_controller_is_used_for_all_four_dialogs(self):
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "function makeInfoDialogController(" in js
        # current-step-dialog is kept in its own named variable (it needs to be
        # compared against elsewhere, to tell whether it's the dialog already
        # open when N/B/P fire), so it is constructed slightly differently.
        assert "makeInfoDialogController(currentStepDialog, el('current-step-heading'))" in js
        for dialog_id, heading_id in (
            ("strategy-dialog", "strategy-heading"),
            ("jump-dialog", "jump-heading"),
            ("help-dialog", "help-heading"),
        ):
            assert f"makeInfoDialogController(el('{dialog_id}'), el('{heading_id}'))" in js

    def test_help_dialog_documents_every_command(self):
        html = CONTROL_HTML.read_text(encoding="utf-8")
        help_block = html[html.index('id="help-dialog"'):html.index("</dialog>", html.index('id="help-dialog"'))]
        for key in ("C", "S", "J", "N", "B", "R", "E"):
            assert f"<kbd>{key}</kbd>" in help_block, f"help dialog does not document {key}"

    def test_current_step_heading_can_receive_focus(self):
        """Focus moves here on every advance, so a screen reader user lands on the
        new script instead of hunting for it."""
        heading = _by_id(CONTROL_HTML)["current-step-heading"]
        assert heading["attrs"].get("tabindex") == "-1"

    def test_the_script_box_is_not_a_second_scroll_region(self):
        """A scrollable box inside a page that already scrolls means two
        scrollbars: the wheel moves whichever is under the pointer, and part of
        the script can sit hidden below the fold of the inner one. The
        experimenter is reading this aloud mid-session, so the page must be the
        only thing that scrolls."""
        css = _css_rules(CONTROL_CSS)
        block = css.split(".script-box {")[1].split("}")[0]
        assert "overflow" not in block, ".script-box scrolls on its own"
        assert "max-height" not in block, ".script-box is height-capped, so it will scroll"

        # And with no scrolling to do, it must not be a focus stop either.
        box = _by_id(CONTROL_HTML)["step-script"]
        assert "tabindex" not in box["attrs"]

    def test_no_element_in_the_panel_declares_its_own_scrolling(self):
        """Guards the general case, not just the script box."""
        offenders = [
            line.strip()
            for line in _css_rules(CONTROL_CSS).splitlines()
            if "overflow" in line and "hidden" not in line
        ]
        assert not offenders, f"panel declares nested scroll regions: {offenders}"


class TestControlPanelBehaviour:
    def test_the_panel_needs_no_token_by_default(self):
        """Running a session should be: open the app, start. A secret to find in
        a server log was the single biggest piece of friction in this flow."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "if (TOKEN) opts.headers['X-Study-Token'] = TOKEN;" in js, (
            "the panel should send a token only when the deployment sets one"
        )
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert 'id="token' not in html

    def test_opening_the_panel_asks_for_the_model_set_then_starts(self):
        """Which two objects the participant gets is the one thing starting a
        session needs a human to answer, so it is the whole of the opening
        screen. The old enrolment form asked for things the protocol had already
        decided; none of it should have come back with the picker."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "function showSetPicker()" in js
        assert "function startSession(taskOrder" in js
        assert "'/study/sets'" in js
        assert "enrollment-form" not in js, "the enrolment form should be gone"
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert 'id="enrollment-form"' not in html
        assert "Start session" not in html

    def test_the_chosen_set_is_what_the_session_starts_on(self):
        """A picker that did not send the choice would look right and quietly
        run the rotation's set instead."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "JSON.stringify({ task_order: taskOrder })" in js
        assert "startSession(entry.task_order" in js

    def test_used_sets_say_so_rather_than_only_being_struck_through(self):
        """A line through text is decoration: it reaches nobody using a screen
        reader. Whatever it means has to be in the button's text too."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "already run once this round" in js
        assert "set-choice-note" in js
        css = _css_rules(CONTROL_CSS)
        assert ".set-choice.is-used .set-choice-label" in css
        assert "line-through" in css

    def test_a_used_set_is_still_selectable(self):
        """An experimenter who has to deviate -- a missing print, a participant
        who saw one of these in a pilot -- must not be locked out by the panel."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        picker = js[js.index("function renderSetPicker("):js.index("/** Start the session")]
        assert "disabled" not in picker, "used sets should be marked, not disabled"

    def test_the_picker_heading_takes_focus_rather_than_the_first_choice(self):
        """Landing on a button says nothing about what is being chosen; the
        heading reads forward over the round summary and the whole list."""
        heading = _by_id(CONTROL_HTML)["set-heading"]
        assert heading["attrs"].get("tabindex") == "-1"
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "el('set-heading')?.focus({ preventScroll: true })" in js

    def test_the_round_is_reported_in_words(self):
        """Where the round has got to is the reason the list looks the way it
        does, so it has to be readable rather than inferred from which entries
        happen to be struck through."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "sets still to run" in js
        assert "Nothing has been run yet" in js
        assert "The previous round is complete" in js

    def test_a_reload_does_not_start_a_second_session(self):
        """One instance of the panel owns one session. A refresh mid-session must
        not create a second participant record and strand the first."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "const resuming = boundSessionId" in js
        assert "cadA11yStudyPanelSession" in js

    def test_the_panel_shows_the_code_to_read_out(self):
        """The home screen is just the identity dl (#180); the join code lives
        there as its own row rather than in a separate block."""
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert 'id="summary-code"' in html
        assert "Study code" in html
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "state.participant_key" in js

    def test_the_home_screen_is_just_the_identity_summary(self):
        """Everything else moved behind a keyboard command; the always-visible
        content in the active session is the dl and nothing more."""
        html = CONTROL_HTML.read_text(encoding="utf-8")
        session_section = html[html.index('id="session-section"'):html.index("</section>", html.index('id="session-section"'))]
        assert "<dl" in session_section
        assert "<dialog" not in session_section, "a dialog should not be nested inside the section"
        for removed in ("toggle-strategy-btn", "step-jump", "confirm-advance", "participant-link-block"):
            assert removed not in session_section, f"#{removed} should have moved out of the home screen"

    def test_nav_has_the_five_study_commands(self):
        by_id = _by_id(CONTROL_HTML)
        for button_id in (
            "run-here-btn", "previous-step-btn", "next-step-btn", "help-btn", "end-session-btn",
        ):
            assert by_id.get(button_id) is not None, f"expected a #{button_id} in the nav"
            assert by_id[button_id]["tag"] == "button"

    def test_session_dependent_nav_buttons_start_hidden(self):
        """Run/Back/Next/End need an active session; Help does not."""
        by_id = _by_id(CONTROL_HTML)
        for button_id in ("run-here-btn", "previous-step-btn", "next-step-btn", "end-session-btn"):
            assert "hidden" in by_id[button_id]["attrs"], f"#{button_id} should start hidden"
        assert "hidden" not in by_id["help-btn"]["attrs"]

    def test_keyboard_commands_map_to_the_documented_keys(self):
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "currentStepDialogController.open();" in js
        assert "strategyDialogController.open();" in js
        assert "jumpDialogController.open();" in js
        assert "helpDialogController.open();" in js
        assert "advance({ direction: 'next' });" in js
        assert "advance({ direction: 'previous' });" in js
        assert "el('run-here-btn')?.click();" in js
        assert "el('end-session-btn')?.click();" in js

    def test_most_keyboard_commands_are_ignored_while_a_dialog_is_open(self):
        """Same guard as the participant viewer for everything except N/B/P:
        a modal dialog makes the rest of the page inert, so Escape and Tab
        must stay scoped to it."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "if (openDialog && !isStepMoveKey) return;" in js

    def test_next_and_back_work_even_with_a_dialog_open(self):
        """Advancing is exactly what makes a currently-open dialog stale, so
        N/B/P are the one exception to the guard above: they replace whatever
        is open with the Current Step dialog rather than being blocked."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "const isStepMoveKey = key === 'n' || key === 'b' || key === 'p';" in js
        assert "if (openDialog && openDialog !== currentStepDialog) openDialog.close();" in js

    def test_logging_health_is_reported_in_words(self):
        """A degraded log must not be conveyed by colour alone."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "Recording normally" in js
        assert "Degraded" in js

    def test_readiness_is_announced_not_only_shown(self):
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "announce('Participant is ready to move on.')" in js

    def test_ending_a_session_is_confirmed(self):
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "window.confirm" in js

    def test_questions_are_shown_but_never_captured(self):
        """The questions are read from the panel so the experimenter has one place
        to look, but every answer is written on their own sheet. The panel must
        therefore render them as text and offer no field to type an answer into."""
        html = CONTROL_HTML.read_text(encoding="utf-8")
        by_id = _by_id(CONTROL_HTML)

        # The script area, which is where questions are rendered, is a definition
        # list -- not a form.
        assert by_id["step-script"]["tag"] == "dl"
        assert "<form" not in html.split('id="session-section"')[1]

        js = CONTROL_JS.read_text(encoding="utf-8")
        # Questions are rendered as list items and options as plain text; nothing
        # in the renderer creates an input, and nothing posts an answer.
        for forbidden in ("createElement('input')", "createElement('textarea')", "createElement('select')"):
            assert forbidden not in js.replace(" ", ""), f"the script renderer builds {forbidden}"
        for endpoint in ("/study/likert", "/study/survey", "/study/observation"):
            assert endpoint not in js, f"panel posts answers to {endpoint}"

    def test_script_blocks_declare_their_kind_to_a_screen_reader(self):
        """"Say this" and "do this" must not be told apart by styling alone."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "BLOCK_LABELS" in js
        for label in ("Say", "Do", "Ask", "Note"):
            assert f"'{label}'" in js
        # The label is a <dt>, so it is announced before the text it describes.
        assert "createElement('dt')" in js
        assert "createElement('dd')" in js

    def test_the_object_description_is_not_under_the_answer_key(self):
        """The description is read to the participant; the answer key must not be.
        Putting the first under a heading that says "do not read this out" is how
        an experimenter skips it."""
        html = CONTROL_HTML.read_text(encoding="utf-8")
        description_at = html.index('id="object-description"')
        answer_key_at = html.index('id="answer-key-block"')
        assert description_at < answer_key_at, "the description sits inside the answer key"

        answer_key_block = html[answer_key_at:html.index('id="answer-key-unchanged"')]
        assert "description" not in answer_key_block.lower()

    def test_the_object_block_says_the_description_is_spoken(self):
        html = CONTROL_HTML.read_text(encoding="utf-8")
        block = html[html.index('id="object-block"'):html.index('id="answer-key-block"')]
        assert "Read the description to the participant" in block

    def test_script_heading_does_not_claim_everything_is_spoken(self):
        """Some blocks are actions. A heading of "Read aloud" would have the
        experimenter performing sentences."""
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert "Experimenter notes" in html
        assert "Read aloud" not in html


class TestFocusIsMovedOnlyByWhoeverCausedTheChange:
    """Who a step change belongs to decides whether focus follows it.

    On the panel the experimenter pressed Next, so focus moves to the new script.
    On the participant's page the change came from someone else's machine, so it
    is announced and their hands are left where they were.
    """

    def test_the_participant_is_announced_to_not_pulled_away(self):
        """A participant exploring with the depth slider must not be yanked out
        of it because the experimenter advanced a step. The shared polite
        announcement window is what reads the new step out wherever they are;
        nothing here should also move focus."""
        js = STUDY_JS.read_text(encoding="utf-8")
        step_change = js.split("if (stepChanged) {")[-1].split("}")[0]
        assert "focus()" not in step_change, (
            "the participant page steals focus on a step change it did not cause"
        )
        assert "announce(" in step_change, (
            "with no focus move, the announcement is the only thing telling the participant"
        )

    def test_advancing_shows_the_current_step_dialog(self):
        """N/B/P (keyboard and the nav buttons) move the step and then show
        its script immediately, rather than leaving the experimenter to press
        C separately for a step they just navigated to."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "advance(payload)" in js
        assert "function advance(" in js and "confirm(" not in js[
            js.index("function advance("):js.index("\n    }\n", js.index("function advance("))
        ], "advance() itself must not show a popup -- callers decide that"

        next_btn = js[js.index("el('next-step-btn')"):js.index("el('previous-step-btn')")]
        assert "currentStepDialogController.open();" in next_btn

        previous_btn = js[js.index("el('previous-step-btn')"):js.index("el('run-here-btn')")]
        assert "currentStepDialogController.open();" in previous_btn

    def test_opening_a_dialog_moves_focus_to_its_own_heading(self):
        """Same ARIA APG pattern as the viewer's Help/About/Settings dialogs:
        a read-only dialog focuses its own heading, not the first control."""
        by_id = _by_id(CONTROL_HTML)
        for heading_id in ("current-step-heading", "strategy-heading", "jump-heading", "help-heading"):
            heading = by_id.get(heading_id)
            assert heading is not None, f"expected a #{heading_id}"
            assert heading["attrs"].get("tabindex") == "-1"

    def test_focus_is_never_deferred_to_an_animation_frame(self):
        """requestAnimationFrame does not run in a background tab. The move
        silently did not happen, and then fired the moment the window came
        forward -- which for the panel is whenever the experimenter switches
        back to it."""
        for path in (STUDY_JS, CONTROL_JS):
            js = path.read_text(encoding="utf-8")
            for line in js.splitlines():
                if "requestAnimationFrame" in line and "//" not in line.split("requestAnimationFrame")[0]:
                    assert "focus" not in line, f"{path.name}: focus deferred to an animation frame"


class TestBothStudyPagesAreCheckedByAxe:
    def test_ci_runs_axe_on_the_study_pages_too(self):
        """The automated check only covered /viewer, so neither study page had
        ever been through it."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "/study (in a session)" in ci
        assert "/study/control" in ci
        assert "/study (waiting for a code)" in ci

    def test_both_panel_states_are_checked(self):
        """The panel opens on the model-set picker and only reaches the session
        by being used, so checking the page it lands on covers roughly none of
        the markup a session renders."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "/study/control (choosing a model set)" in ci
        assert "/study/control (running a session)" in ci
        assert "page.click('#set-list button')" in ci

    def test_the_study_pages_are_checked_in_a_real_session(self):
        """An empty panel and a participant page with no session exercise almost
        none of the markup that a session actually renders."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "/study/session/start" in ci, "axe should run against a live session"
        assert "participant_key" in ci
