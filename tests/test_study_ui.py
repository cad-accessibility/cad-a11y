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

    def test_heading_can_receive_focus_without_joining_the_tab_order(self):
        heading = _by_id(VIEWER_HTML)["study-step-heading"]
        assert heading["tag"] == "h2"
        assert heading["attrs"].get("tabindex") == "-1"

    def test_heading_is_polite_never_assertive(self):
        """Participants read braille with both hands while the display refreshes.
        An assertive interruption mid-slice is the disruption this study is
        trying not to introduce."""
        heading = _by_id(VIEWER_HTML)["study-step-heading"]
        assert heading["attrs"].get("aria-live") == "polite"

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

    def test_disclosure_button_declares_its_state(self):
        toggle = _by_id(CONTROL_HTML)["toggle-strategy-btn"]
        assert toggle["attrs"].get("aria-expanded") == "false"
        assert toggle["attrs"].get("aria-controls") == "strategy-prompts-block"

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

    def test_opening_the_panel_starts_a_session(self):
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "function startSession()" in js
        assert "enrollment-form" not in js, "the enrolment form should be gone"
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert 'id="enrollment-form"' not in html
        assert "Start session" not in html

    def test_a_reload_does_not_start_a_second_session(self):
        """One instance of the panel owns one session. A refresh mid-session must
        not create a second participant record and strand the first."""
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "const resuming = boundSessionId" in js
        assert "cadA11yStudyPanelSession" in js

    def test_the_panel_shows_the_code_to_read_out(self):
        html = CONTROL_HTML.read_text(encoding="utf-8")
        assert 'id="participant-code"' in html
        assert "Read this to your participant" in html
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "state.participant_key" in js

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
        of it because the experimenter advanced a step. The heading is a polite
        live region, so the new step is read out wherever they are."""
        js = STUDY_JS.read_text(encoding="utf-8")
        step_change = js.split("if (stepChanged) {")[-1].split("}")[0]
        assert "focus()" not in step_change, (
            "the participant page steals focus on a step change it did not cause"
        )
        heading = _by_id(VIEWER_HTML)["study-step-heading"]
        assert heading["attrs"].get("aria-live") == "polite", (
            "with no focus move, the live region is the only thing announcing the step"
        )

    def test_the_panel_moves_focus_to_the_new_step(self):
        js = CONTROL_JS.read_text(encoding="utf-8")
        assert "target.focus();" in js
        assert _by_id(CONTROL_HTML)["current-step-heading"]["attrs"].get("tabindex") == "-1"

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

    def test_the_study_pages_are_checked_in_a_real_session(self):
        """An empty panel and a participant page with no session exercise almost
        none of the markup that a session actually renders."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "/study/session/start" in ci, "axe should run against a live session"
        assert "participant_key" in ci
