/**
 * The first-run tutorial: fifteen short lessons on one practice mug, the last
 * three of them extras.
 *
 * It opens by itself on a first visit to /viewer and is otherwise one press of
 * the Tutorial button away, in the main menu and at the top of the Keyboard
 * Shortcuts dialog. Exit is on every step and loses nothing: it pauses, and
 * Resume comes back to the same lesson and step.
 *
 * The lessons themselves are data, served by GET /tutorial/lessons (see
 * app/tutorial_lessons.py). This file is the runner: it shows a step, applies a
 * lesson's starting view, watches what the viewer does, and moves on when a
 * step's check passes. It checks outcomes rather than intercepting keys: every
 * key still reaches the viewer and does its ordinary job, and the check reads
 * the viewer's state afterwards through window.cadStudy (viewer.js), the same
 * bridge the study driver uses.
 *
 * What it says goes through the viewer's alert window, the one its answers to
 * keys use, one utterance at a time, so the next key's answer replaces what the
 * tutorial was reading rather than waiting behind it (#245 review). It speaks a
 * short form of each step; the full text stays on the page to be read on
 * demand. Nothing here speaks or advances on a timer (WCAG 2.2.1): hints come
 * only when asked for, and a step waits as long as it takes.
 *
 * Inert on /study, which has its own onboarding and logs.
 */

// ---------------------------------------------------------------------------
// Where the tutorial starts by itself, where it is offered, and where Resume
// makes sense. Rewiring a route means changing its row.
//
//   autostart  opens by itself, once per browser, on the first visit
//   offered    the Tutorial button and the shortcuts dialog's section show
//   resume     Resume is offered (not on /demo, where nothing survives the tab)
//
// /demo is facilitated and its storage lasts one tab (demo-bootstrap.js), so
// "first visit" would be every visit. /workshop and ?ui=simple hide most of
// what the lessons teach. /study has its own onboarding and its own log.
// ?tutorial=start opens it on any offered route, for a facilitator, and
// ?tutorial=off keeps it closed for one load.
// ---------------------------------------------------------------------------
const TUTORIAL_ROUTES = [
    { route: '/viewer', autostart: true, offered: true, resume: true },
    { route: '/demo', autostart: false, offered: true, resume: false },
    { route: '/workshop', autostart: false, offered: false, resume: false },
    { route: '?ui=simple', autostart: false, offered: false, resume: false },
    { route: '/study', autostart: false, offered: false, resume: false },
];

// ---------------------------------------------------------------------------
// Step checks, one entry per check type a lesson may use (app/tutorial_lessons.py
// uses only these; tests/test_tutorial_ui.py holds the list). Each entry has
// any of:
//
//   start(check, ctx)             private state for one visit to the step
//   event(check, s, ctx, ev)      true once the step has passed. Called after
//                                 the action has taken effect: for each
//                                 interaction event, a microtask after it
//   proceed(check, s, ctx)        Continue was pressed: true passes, false
//                                 means the check has said why not
//   answer(check, s, ctx, value)  an answer button: true passes
//   atEntry                       also evaluated once as the step opens
//
// ev.type is "keyboard", "announcement", "ui_action", "device" or "render"
// (from viewer.js), or the runner's own "locate", "key_help", "test_pattern"
// and "entry". The runner hands in ctx, which reads the viewer and speaks, so
// nothing here touches the page.
// ---------------------------------------------------------------------------
const CHECKS = {
    manual: {
        proceed: () => true,
    },
    answer: {
        answer: (check, s, ctx, value) => ctx.answerIsCorrect(check.correct, value),
    },
    key: {
        start: () => ({ seen: new Set(), armed: false, wrongRun: 0 }),
        event: (check, s, ctx, ev) => ctx.keysDone([check.key], s, ev),
    },
    keys: {
        start: () => ({ seen: new Set(), armed: false, wrongRun: 0 }),
        event: (check, s, ctx, ev) => ctx.keysDone(check.keys || [], s, ev),
    },
    state: {
        event: (check, s, ctx) => ctx.fieldMatches(check, ctx.state()),
    },
    changed: {
        start: (check, ctx) => ({ was: JSON.stringify(ctx.entry[check.field]) }),
        event: (check, s, ctx) => JSON.stringify(ctx.state()[check.field]) !== s.was,
    },
    cycle: {
        start: (check, ctx) => ({ seen: new Set([ctx.norm(ctx.entry[check.field])]) }),
        event: (check, s, ctx) => {
            const now = ctx.norm(ctx.state()[check.field]);
            s.seen.add(now);
            return (check.values || []).every(value => s.seen.has(ctx.norm(value)))
                && now === ctx.norm(check.end);
        },
    },
    sweep: {
        start: (check, ctx) => {
            const cut = ctx.cutAt(ctx.entry);
            const onAxis = cut.axis === check.axis;
            return {
                min: onAxis ? cut.percent : Infinity,
                max: onAxis ? cut.percent : -Infinity,
                away: { last: onAxis ? 0 : 1, warned: false },
            };
        },
        event: (check, s, ctx) => {
            const cut = ctx.cutAt(ctx.state());
            const onAxis = cut.axis === check.axis;
            if (onAxis) {
                s.min = Math.min(s.min, cut.percent);
                s.max = Math.max(s.max, cut.percent);
            }
            // Away from the target here means cutting along another axis.
            ctx.warnIfMovedAway(s.away, onAxis ? 0 : 1);
            return s.min <= Number(check.from) && s.max >= Number(check.to);
        },
    },
    edge: {
        start: (check, ctx) => ({ away: { last: ctx.edgeDistance(ctx.entry), warned: false } }),
        event: (check, s, ctx) => {
            const distance = ctx.edgeDistance(ctx.state());
            ctx.warnIfMovedAway(s.away, distance);
            return distance <= 0.5;
        },
    },
    in_band: {
        event: (check, s, ctx) => ctx.bandDistance(check.axis, check.band) === 0,
    },
    mark_band: {
        start: (check, ctx) => ({ away: { last: ctx.bandDistance(check.axis, check.band), warned: false } }),
        event: (check, s, ctx) => {
            ctx.warnIfMovedAway(s.away, ctx.bandDistance(check.axis, check.band));
            return false;
        },
        proceed: (check, s, ctx) => {
            if (ctx.bandDistance(check.axis, check.band) === 0) return true;
            ctx.fail(ctx.whereAgainstBand(check.axis, check.band));
            return false;
        },
    },
    device: {
        atEntry: true,
        event: (check, s, ctx, ev) => ctx.deviceConnected(check.device, ev),
    },
    ui: {
        event: (check, s, ctx, ev) => ev.type === 'ui_action'
            && ev.data.name === check.name && ev.data.action === check.action,
    },
    settings_unchanged: {
        start: (check, ctx) => ({ before: ctx.settingsFingerprint() }),
        event: (check, s, ctx, ev) => {
            if (ev.type !== 'ui_action' || ev.data.name !== 'settings-dialog' || ev.data.action !== 'close') {
                return false;
            }
            if (ctx.settingsFingerprint() === s.before) return true;
            ctx.fail();
            return false;
        },
    },
    test_pattern: {
        start: () => ({ run: 0 }),
        event: (check, s, ctx, ev) => {
            if (ev.type !== 'test_pattern') return false;
            s.run = ev.data.correct ? s.run + 1 : 0;
            return s.run >= (Number(check.rounds) || 2);
        },
    },
    cursor_in: {
        event: (check, s, ctx, ev) => {
            if (ev.type !== 'locate') return false;
            const box = ctx.landmarkBox(check.landmark);
            const now = ctx.state();
            return Boolean(box) && now.cursor_state !== 'none'
                && now.cursor_col >= box[0] && now.cursor_col <= box[2]
                && now.cursor_row >= box[1] && now.cursor_row <= box[3];
        },
    },
    centred: {
        event: (check, s, ctx, ev) => {
            if (ev.type !== 'locate') return false;
            const mark = ctx.landmark(check.landmark);
            const width = ctx.gridWidth();
            if (!mark || !mark.visible || !Array.isArray(mark.centre) || !width) return false;
            const column = Number(mark.centre[0]);
            return column >= width / 3 && column <= (2 * width) / 3;
        },
    },
    zoom_by: {
        event: (check, s, ctx) => Number(ctx.state().zoom) - Number(ctx.entry.zoom)
            >= Number(check.min_increase) - 1e-9,
    },
    model_changed: {
        // Changed since the step opened, not just "not the mug": lesson 12 can
        // open on the person's own model, and must still wait for a choice.
        start: (check, ctx) => ({ was: ctx.entry.model }),
        event: (check, s, ctx) => {
            const model = ctx.state().model;
            return Boolean(model) && model !== ctx.practiceModel && model !== s.was;
        },
    },
    key_help: {
        start: (check, ctx) => ({ on: ctx.keyHelpOn(), described: 0 }),
        event: (check, s, ctx, ev) => {
            if (ev.type !== 'key_help') return false;
            if (ev.data.action === 'on') {
                s.on = true;
                s.described = 0;
            } else if (ev.data.action === 'described' && s.on) {
                s.described += 1;
            } else if (ev.data.action === 'off') {
                const passed = s.on && s.described >= (Number(check.presses) || 2);
                if (!passed) ctx.fail();
                s.on = false;
                return passed;
            }
            return false;
        },
    },
    slicegraph_ready: {
        event: (check, s, ctx, ev) => ev.type === 'render' && ev.data.slicegraph_ready === true
            && ctx.state().layout_mode === 'slice-graph',
    },
    all: {
        start: (check, ctx) => ({
            parts: (check.checks || []).map(sub => ({ sub, s: ctx.startCheck(sub), passed: false })),
        }),
        event: (check, s, ctx, ev) => {
            for (const part of s.parts) {
                if (!part.passed && ctx.eventCheck(part.sub, part.s, ev)) part.passed = true;
            }
            return s.parts.length > 0 && s.parts.every(part => part.passed);
        },
    },
};

(function () {
    'use strict';

    // -----------------------------------------------------------------------
    // The hooks the viewer and the device scripts call. Created first and
    // always, so their calls are safe whatever happens below, including on
    // /study where the rest of this file does nothing.
    // -----------------------------------------------------------------------
    const cadTutorial = {
        // True while the tutorial is about to put its mug on the display, so the
        // page's own first render (viewer.js, DOMContentLoaded) holds back.
        ownsFirstRender: false,
        // True while the test pattern is on the pins: renders go ahead but are
        // not sent to either display (viewer.js, the render response).
        holdDisplay: false,
        // Key help mode: keys and device buttons are described, not acted on.
        keyHelpActive: false,
        // The model chooser, upload and layout radios are locked for the lesson.
        locked: false,
        // The tutorial is on screen (a lesson or the lesson list).
        running: false,
        captureDeviceKey: () => false,
        describeKey: () => {},
    };
    window.cadTutorial = cadTutorial;

    const path = location.pathname.replace(/\/+$/, '') || '/';
    const params = new URLSearchParams(location.search);
    // A query row wins over a path row: /viewer?ui=simple is the workshop UI.
    const route = TUTORIAL_ROUTES.find(row => row.route.startsWith('?')
            && params.get(row.route.slice(1).split('=')[0]) === row.route.split('=')[1])
        || TUTORIAL_ROUTES.find(row => row.route === path)
        || { route: path, autostart: false, offered: true, resume: true };

    const study = window.cadStudy;
    if (path === '/study' || !study || typeof study.getState !== 'function') return;

    const $ = (id) => document.getElementById(id);
    const region = $('tutorial-region');
    const heading = $('tutorial-heading');
    const navButton = $('nav-tutorial-btn');
    if (!region || !heading) return;

    const el = {
        lessonView: $('tutorial-lesson-view'),
        menuView: $('tutorial-menu-view'),
        progress: $('tutorial-progress'),
        stepText: $('tutorial-step-text'),
        note: $('tutorial-note'),
        last: $('tutorial-last-text'),
        answers: $('tutorial-answers'),
        answerButtons: $('tutorial-answer-buttons'),
        answersLabel: $('tutorial-answers-label'),
        hints: $('tutorial-hints'),
        continueBtn: $('tutorial-continue-btn'),
        backBtn: $('tutorial-back-btn'),
        repeatBtn: $('tutorial-repeat-btn'),
        hintBtn: $('tutorial-hint-btn'),
        showMeBtn: $('tutorial-show-me-btn'),
        restartBtn: $('tutorial-restart-lesson-btn'),
        keyHelpBtn: $('tutorial-keyhelp-btn'),
        lessonsBtn: $('tutorial-lessons-btn'),
        skipBtn: $('tutorial-skip-btn'),
        skipLessonBtn: $('tutorial-skip-lesson-btn'),
        keysBtn: $('tutorial-keys-btn'),
        keysHelp: $('tutorial-keys-help'),
        keysHelpList: $('tutorial-keys-help-list'),
        connectMonarchBtn: $('tutorial-connect-monarch-btn'),
        connectDotpadBtn: $('tutorial-connect-dotpad-btn'),
        exitBtn: $('tutorial-exit-btn'),
        prints: $('tutorial-prints'),
        menuResume: $('tutorial-menu-resume'),
        resumeBtn: $('tutorial-resume-btn'),
        menuGroups: $('tutorial-menu-groups'),
        startOverBtn: $('tutorial-start-over-btn'),
        menuExitBtn: $('tutorial-menu-exit-btn'),
        dialogSection: $('tutorial-shortcuts-section'),
        dialogResumeBtn: $('tutorial-dialog-resume-btn'),
        dialogStartOverBtn: $('tutorial-dialog-start-over-btn'),
        dialogLessonsBtn: $('tutorial-dialog-lessons-btn'),
        dialogKeyHelpBtn: $('tutorial-dialog-keyhelp-btn'),
    };

    // Where it is not offered, the button and the dialog section go at once,
    // rather than waiting for viewer.js to add body.simple-ui (the CSS that
    // hides them) at DOMContentLoaded.
    if (!route.offered) {
        if (navButton) navButton.hidden = true;
        if (el.dialogSection) el.dialogSection.hidden = true;
    }

    const PRACTICE_MODEL = 'tutorial_mug';
    const PRACTICE_LABEL = 'Tutorial mug';

    // The lessons' default starting view, the one app/tutorial_lessons.py gives
    // its default-pose lessons: the mug upright from X ("X from plus" in XYZ
    // mode, with the handle on the display's left edge), cut at 50%, Cut,
    // single layout, zoomed out and centred. Used when a run begins on a lesson
    // that sets no view of its own, so the mug is on the display whichever
    // lesson it is.
    const DEFAULT_POSE = {
        model: PRACTICE_MODEL, view: 'x-', axis_mode: 'keep', depth: 50, render_mode: 'cut',
        representation_mode: 'single', compose_scrollbar: true, zoom: 0.0, reset_pan: true,
    };

    // Every lesson can be skipped, so none is called optional; the three for
    // the slice graph, the cube and the slider come last, as extras (#245
    // review).
    const PARTS = [
        { key: 'setup', label: 'Setup' },
        { key: 'core', label: 'Core' },
        { key: 'wrapup', label: 'Wrap-up' },
        { key: 'extras', label: 'Extras' },
    ];

    // -----------------------------------------------------------------------
    // Saved progress: localStorage, in try/catch like the viewer's settings, so
    // a browser that refuses storage still runs the tutorial, it just cannot
    // remember it. The settings* keys cannot mark a returning visitor, since the
    // viewer writes them on every load, so this record is written the moment a
    // first visit is seen.
    // -----------------------------------------------------------------------
    const STORAGE_KEY = 'cadA11yTutorial';

    function readRecord() {
        try {
            const raw = window.localStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : null;
        } catch (_) {
            return null;
        }
    }

    function writeRecord() {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
        } catch (_) {
            // Private browsing, a full quota, or blocked storage. The tutorial
            // still runs; it just starts afresh next time.
        }
    }

    function normalizeRecord(raw) {
        const out = raw && typeof raw === 'object' ? { ...raw } : {};
        if (typeof out.status !== 'string') out.status = 'new';
        if (!out.lessons || typeof out.lessons !== 'object') out.lessons = {};
        if (!out.answers || typeof out.answers !== 'object') out.answers = {};
        if (!Number.isInteger(out.step)) out.step = 0;
        if (typeof out.lesson !== 'string') out.lesson = null;
        if (typeof out.version !== 'number') out.version = 0;
        return out;
    }

    const storedAtLoad = readRecord();
    const recordWasNew = storedAtLoad === null;
    let record = normalizeRecord(storedAtLoad);

    // A first visit on a route that opens the tutorial by itself: written now,
    // synchronously, before anything else on the page could write storage.
    if (recordWasNew && route.autostart) {
        record = normalizeRecord({ status: 'pending', origin: 'first-run' });
        writeRecord();
    }

    // Another tab of the viewer wrote the record. Without this, whichever tab
    // wrote last would put back its own older copy of what is done.
    window.addEventListener('storage', (event) => {
        if (event.key !== STORAGE_KEY) return;
        const fresh = normalizeRecord(readRecord());
        if (!runner.runActive) {
            record = fresh;
            syncDialogSection();
            return;
        }
        record.lessons = { ...record.lessons, ...fresh.lessons };
        if (fresh.status === 'completed') record.status = 'completed';
    });

    const tutorialParam = params.get('tutorial');
    const forcedStart = tutorialParam === 'start' && route.offered;
    const suppressed = tutorialParam === 'off';
    // A run that began as the first-run tutorial comes back by itself after a
    // closed tab; one opened from the menu does not, since it was someone looking
    // at a lesson, not taking the tutorial (#245 review).
    const mayAutostart = !suppressed && (forcedStart
        || (route.autostart && (record.status === 'pending'
            || (record.status === 'in-progress' && record.origin === 'first-run'))));
    // Set now, before viewer.js reaches its first render. Always handed back,
    // by starting (the mug is the first render) or by releaseFirstRender().
    cadTutorial.ownsFirstRender = mayAutostart;

    // -----------------------------------------------------------------------
    // Reading the viewer
    // -----------------------------------------------------------------------
    function getState() {
        try {
            return study.getState() || {};
        } catch (error) {
            console.warn('Tutorial could not read the viewer state:', error);
            return {};
        }
    }

    function norm(value) {
        return String(value === null || value === undefined ? '' : value)
            .toLowerCase().trim().replace(/[\s_-]+/g, ' ');
    }

    function sentence(text) {
        const trimmed = String(text || '').trim();
        if (!trimmed) return '';
        return /[.!?:]$/.test(trimmed) ? trimmed : `${trimmed}.`;
    }

    // -----------------------------------------------------------------------
    // Speaking. The viewer's alert field, the one its answers to keys use, so a
    // key pressed while the tutorial is talking replaces what it was saying
    // (#245 review: Jen pressed "." and the step kept being read). Two writes to
    // it in one tick lose the first (viewer.js, updateMessageWindow), so
    // everything said in one tick goes out as one utterance, and a viewer
    // answer written in the same tick is kept at its front rather than
    // overwritten. A modal dialog makes the field inert, so anything said while
    // one is open waits for its close. The same words twice in a row are varied
    // slightly, or a screen reader may not read them again.
    // -----------------------------------------------------------------------
    const speech = { parts: [], braille: null, scheduled: false, last: '', again: false };
    // Viewer events waiting for their microtask (see queueEvent below).
    let eventBatch = [];
    let speakingNow = false;
    let viewerAlertThisTick = null;

    function say(text, braille) {
        const line = sentence(text);
        if (!line) return;
        speech.parts.push(line);
        if (braille) speech.braille = braille;
        if (!speech.scheduled) {
            speech.scheduled = true;
            queueMicrotask(flushSpeech);
        }
    }

    function flushSpeech() {
        // Events still queued in this tick may add to what is said (a pass after
        // "Handle: left"), so wait for them: still within the tick, and still one
        // write to the field.
        if (eventBatch.length) {
            queueMicrotask(flushSpeech);
            return;
        }
        speech.scheduled = false;
        if (!speech.parts.length) return;
        if (document.querySelector('dialog[open]')) return;
        let message = speech.parts.join(' ');
        if (viewerAlertThisTick) message = `${sentence(viewerAlertThisTick)} ${message}`;
        if (message === speech.last) {
            message = `${speech.again ? 'Once more' : 'Again'}: ${message}`;
            speech.again = !speech.again;
        } else {
            speech.last = message;
            speech.again = false;
        }
        const braille = speech.braille;
        speech.parts = [];
        speech.braille = null;
        speakingNow = true;
        try {
            const speak = typeof study.announceAlert === 'function' ? study.announceAlert : study.announce;
            speak(message, braille ? { braille } : {});
        } finally {
            speakingNow = false;
        }
    }

    // A viewer answer written to the alert field in this tick. Cleared on the
    // next task: this timer forgets a message, it never says or advances
    // anything.
    function noteViewerMessage(data) {
        if (!data || data.politeness !== 'assertive') return;
        viewerAlertThisTick = String(data.message || '');
        setTimeout(() => { viewerAlertThisTick = null; }, 0);
    }

    function setLast(text) {
        if (el.last) el.last.textContent = sentence(text) || 'nothing yet.';
    }

    // -----------------------------------------------------------------------
    // Analytics: coarse, consented, never on /demo. Through the page's own
    // helper (the inline script in accessible-3d-viewer.html), which also
    // refuses on /demo; the server refuses without consent as well.
    // -----------------------------------------------------------------------
    let consentGiven = false;

    function track(action, lessonId) {
        if (window.CAD_DEMO_MODE || consentGiven !== true) return;
        if (typeof window.cadTrackEvent !== 'function') return;
        window.cadTrackEvent('tutorial', { action, lesson: lessonId || null });
    }

    // -----------------------------------------------------------------------
    // The lessons
    // -----------------------------------------------------------------------
    let payload = null;
    let payloadDisplay = null;
    let payloadRequest = null;
    const LESSONS_TIMEOUT_MS = 15000;

    function displayForLessons() {
        const answer = norm(record.answers.display);
        if (answer.includes('monarch')) return 'monarch';
        if (answer.includes('dotpad')) return 'dotpad';
        if (answer) return 'none';
        const now = getState();
        if (now.monarch_connected) return 'monarch';
        if (now.dotpad_connected) return 'dotpad';
        return 'none';
    }

    async function ensurePayload() {
        const display = displayForLessons();
        if (payload && payloadDisplay === display) return payload;
        if (payloadRequest && payloadRequest.display === display) return payloadRequest.promise;
        const promise = (async () => {
            // Given up on after LESSONS_TIMEOUT_MS: the viewer never waits for
            // it (the mug is the first render either way), but a hung request
            // would otherwise leave the tutorial silently not starting.
            const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
                ? AbortSignal.timeout(LESSONS_TIMEOUT_MS) : undefined;
            const res = await fetch(`/tutorial/lessons?display=${encodeURIComponent(display)}`, { signal });
            if (!res.ok) throw new Error(`the lessons did not load (HTTP ${res.status})`);
            const data = await res.json();
            if (!data || !Array.isArray(data.lessons) || data.lessons.length === 0) {
                throw new Error('the lessons arrived empty');
            }
            payload = data;
            payloadDisplay = display;
            return data;
        })();
        payloadRequest = { display, promise };
        try {
            return await promise;
        } finally {
            if (payloadRequest && payloadRequest.promise === promise) payloadRequest = null;
        }
    }

    const lessons = () => (payload && payload.lessons) || [];
    const practiceModel = () => (payload && payload.model) || PRACTICE_MODEL;
    const landmarks = () => (payload && payload.landmarks) || null;

    function lessonIndexById(id) {
        return lessons().findIndex(lesson => lesson.id === id);
    }

    // -----------------------------------------------------------------------
    // Words that depend on the viewer at the moment they are shown or said
    // -----------------------------------------------------------------------

    /** A key's name for this display, if the server left a placeholder: the
     * same rule app/tutorial_lessons.py resolves them by. */
    function keyName(name) {
        const key = payload && payload.keys && payload.keys[name];
        if (!key) return name;
        const device = payloadDisplay === 'monarch' ? key.monarch : payloadDisplay === 'dotpad' ? key.dotpad : null;
        if (key.keyboard && device) return `${device} or ${key.keyboard}`;
        return key.keyboard || device || name;
    }

    /** Where the handle points on the display, from the landmarks' handle
     * direction and the orientation the viewer is in. The orientation payload's
     * vectors are model directions: `right` is the display's right edge, `up`
     * its top edge, and `forward` points out of the display at the reader
     * (viewer.js, VIEW_BASIS: depth = right x up, the reader on the +depth side). */
    function handleSide(state = getState()) {
        const direction = landmarks() && landmarks().handle_direction;
        const o = state.orientation;
        if (!Array.isArray(direction) || !o || !o.right || !o.up || !o.forward) return null;
        const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
        const right = dot(direction, o.right);
        const up = dot(direction, o.up);
        const toward = dot(direction, o.forward);
        if (Math.abs(right) >= Math.abs(up) && Math.abs(right) >= Math.abs(toward)) {
            return right > 0 ? 'right' : 'left';
        }
        if (Math.abs(up) >= Math.abs(toward)) return up > 0 ? 'top' : 'bottom';
        return toward > 0 ? 'toward you' : 'away from you';
    }

    /** Which way to move the object so the handle comes to the middle, from the
     * last /tutorial/locate answer, or the handle's side if there is none yet. */
    function panTowardHandle() {
        const mark = located && located.landmarks && located.landmarks.handle;
        const side = String((mark && mark.side) || handleSide() || '').replace(/^off-/, '');
        const moves = {
            left: ['pan_right', 'right'],
            right: ['pan_left', 'left'],
            top: ['pan_down', 'down'],
            bottom: ['pan_up', 'up'],
        };
        const move = moves[side];
        if (!move) return 'nothing: the handle is already in the middle';
        return `${keyName(move[0])}, which moves the object ${move[1]}`;
    }

    function axisModeName() {
        return getState().axis_mode === 'xyz' ? 'XYZ' : 'Turn';
    }

    function resolveText(text) {
        return String(text || '')
            .replace(/\{key:([A-Za-z0-9_]+)\}/g, (match, name) => keyName(name))
            .replace(/\{handle_side\}/g, () => handleSide() || 'not known yet')
            .replace(/\{pan_toward_handle\}/g, () => panTowardHandle())
            .replace(/\{axis_mode_name\}/g, () => axisModeName());
    }

    // -----------------------------------------------------------------------
    // Requirements: a lesson or step that cannot run here yet
    // -----------------------------------------------------------------------
    function saidNoDisplay() {
        const answer = norm(record.answers.display);
        return Boolean(answer) && !answer.includes('monarch') && !answer.includes('dotpad');
    }

    /** Why a lesson or step cannot run now, or null if it can. */
    function unmetRequirement(requires) {
        const now = getState();
        for (const need of requires || []) {
            if (need === 'display' && !now.display_connected) {
                return saidNoDisplay()
                    ? 'it needs a tactile display, a Monarch or a DotPad'
                    : 'connect your display first; lesson 2, Connect, does that';
            }
            if (need === 'cube' && !('bluetooth' in navigator)) {
                return 'the cube needs Web Bluetooth, which this browser does not have. Chrome and Edge do';
            }
            if (need === 'slider' && !('serial' in navigator)) {
                return 'the slider needs Web Serial, which this browser does not have. Chrome and Edge do';
            }
            if (need === 'keys' && now.single_key_shortcuts === false) {
                return 'single-key shortcuts are off in Settings';
            }
        }
        return null;
    }

    function stepApplies(step) {
        if (step.when && step.when.stored) {
            if (norm(record.answers[step.when.stored]) !== norm(step.when.equals)) return false;
        }
        if (Array.isArray(step.requires) && step.requires.length && unmetRequirement(step.requires)) {
            return false;
        }
        return true;
    }

    // -----------------------------------------------------------------------
    // Runner state
    // -----------------------------------------------------------------------
    const runner = {
        view: 'idle',          // idle, lesson or menu
        runActive: false,      // a lesson has been opened since the tutorial last closed
        lessonIndex: -1,
        stepIndex: -1,
        phase: 'step',         // step, done (waiting for Continue) or blocked
        checkState: null,
        entry: null,
        hintsShown: 0,
        demoFrame: 0,
        lastNarrated: null,
        blockedReason: null,
        pendingNotes: [],      // said with the next lesson: lessons skipped on the way
        // Steps already passed or skipped in this run, as "lesson:step". Back
        // into one of them, Next goes straight on rather than asking for it
        // again (#245 review).
        passed: new Set(),
        showMeFrame: 0,        // which band Show me is on, in a sweep
        // Bumped whenever a step opens or closes. An event counts only for the
        // step that was open when it happened, so the tail of the action that
        // passed one step cannot pass the next.
        stepSerial: 0,
    };

    // Settings this run changed, to put back on exit unless the person chose to
    // keep them: the axis mode, and the Cube and Slider sections.
    const touched = { axisMode: undefined, sections: new Set() };
    let panMoved = false;
    let announceNextMugRender = false;
    let located = null;
    let locateSeq = 0;
    let lastLocatedPose = null;
    // Whether this lesson has already said that /tutorial/locate did not answer.
    let locateFailureSaid = false;

    const currentLesson = () => lessons()[runner.lessonIndex] || null;
    const currentStep = () => {
        const lesson = currentLesson();
        return lesson && lesson.steps ? lesson.steps[runner.stepIndex] || null : null;
    };
    const currentCheck = () => {
        const step = currentStep();
        return (step && step.check) || { type: 'manual' };
    };

    function applicableSteps(lesson = currentLesson()) {
        if (!lesson || !Array.isArray(lesson.steps)) return [];
        return lesson.steps.map((step, index) => ({ step, index })).filter(({ step }) => stepApplies(step))
            .map(({ index }) => index);
    }

    function lessonNumberText(index = runner.lessonIndex) {
        return `Lesson ${index + 1} of ${lessons().length}`;
    }

    // The heading names the region too (aria-labelledby), so it says what the
    // region is: "Tutorial, lesson 4 of 15: Meet the mug". A landmark list or
    // a first focus that said only "Lesson 4 of 15" never said "tutorial".
    function headingText(lesson) {
        return `Tutorial, lesson ${runner.lessonIndex + 1} of ${lessons().length}: ${lesson.title}`;
    }

    function stepNumberText() {
        const steps = applicableSteps();
        const position = steps.indexOf(runner.stepIndex);
        return `Step ${position < 0 ? 1 : position + 1} of ${Math.max(steps.length, 1)}`;
    }

    // -----------------------------------------------------------------------
    // What the checks are handed
    // -----------------------------------------------------------------------
    function checkContext() {
        let cached = null;
        const state = () => {
            if (!cached) cached = getState();
            return cached;
        };
        const ctx = {
            entry: runner.entry || {},
            practiceModel: practiceModel(),
            state,
            norm,
            fail: (extra) => sayFailure(extra),
            answerIsCorrect: (correct, value) => answerIsCorrect(correct, value),
            fieldMatches,
            cutAt: (s) => ({ axis: s.cut_axis, percent: Number(s.cut_percent) }),
            // Distance to the far side in reader depth (0 nearest the reader,
            // 100 the far side, in both axis modes). cut_percent would be the
            // wrong measure: from the right, the back or above, deeper lowers
            // it, so "whichever end is closer" called a correct press a mistake.
            edgeDistance: (s) => {
                const depth = Number(s.reader_depth);
                return Number.isFinite(depth) ? 100 - depth : 100;
            },
            bandDistance: (axis, name) => bandDistance(axis, name, state()),
            whereAgainstBand: (axis, name) => whereAgainstBand(axis, name, state()),
            warnIfMovedAway,
            deviceConnected: (device, ev) => deviceConnected(device, ev, state()),
            settingsFingerprint,
            landmark: (name) => (located && located.landmarks && located.landmarks[name]) || null,
            landmarkBox: (name) => {
                const mark = located && located.landmarks && located.landmarks[name];
                return mark && Array.isArray(mark.box) ? mark.box : null;
            },
            gridWidth: () => (located && located.grid && Number(located.grid.width)) || 0,
            keyHelpOn: () => cadTutorial.keyHelpActive,
            keysDone: (keys, s, ev) => keysDone(keys, s, ev, state),
            startCheck: (sub) => startCheck(sub, ctx),
            eventCheck: (sub, s, ev) => {
                const impl = CHECKS[sub && sub.type];
                return Boolean(impl && impl.event && impl.event(sub, s, ctx, ev));
            },
        };
        return ctx;
    }

    function startCheck(check, ctx) {
        const impl = CHECKS[check && check.type];
        return impl && impl.start ? impl.start(check, ctx) : {};
    }

    function fieldMatches(check, state) {
        const value = state[check.field];
        const tolerance = Number(check.tolerance) || 0;
        if (check.equals !== undefined && check.equals !== null) {
            if (typeof check.equals === 'number') return Math.abs(Number(value) - check.equals) <= tolerance;
            return norm(value) === norm(check.equals);
        }
        if (check.lte !== undefined && check.lte !== null) return Number(value) <= Number(check.lte) + tolerance;
        if (check.gte !== undefined && check.gte !== null) return Number(value) >= Number(check.gte) - tolerance;
        return false;
    }

    function answerIsCorrect(correct, value) {
        if (correct === null || correct === undefined) return true;
        let wanted = correct;
        if (correct === 'computed:handle_side') wanted = handleSide();
        else if (correct === 'computed:cut_axis') wanted = getState().cut_axis;
        // If the right answer cannot be worked out here (no landmarks), the
        // answer is taken rather than the step made impossible.
        if (wanted === null || wanted === undefined) return true;
        if (norm(value) === norm(wanted)) return true;
        sayFailure();
        return false;
    }

    // Keys that do nothing in some states: a key only counts as having acted
    // when it could have. The viewer refuses the other mode's keys, and G and
    // V outside the slice graph.
    function keyCouldAct(key, state) {
        if (['g', 'v'].includes(key)) return state.layout_mode === 'slice-graph';
        if (['x', 'y', 'z'].includes(key)) return state.axis_mode === 'xyz';
        if (['u', 'o', 'i', 'k', 'j', 'l'].includes(key)) return state.axis_mode !== 'xyz';
        return true;
    }

    /** The key and keys checks. The keyboard event arrives before the key acts
     * (viewer.js reports it first), so a wanted key arms the check and the pass
     * comes with what the key did next: its own announcement, or the dialog it
     * opened (H). A run of three other keys says the step's on_fail. */
    function keysDone(keys, s, ev, state) {
        const wanted = keys.map(key => String(key).toLowerCase());
        if (ev.type === 'keyboard') {
            const key = String(ev.data.key || '').toLowerCase();
            if (wanted.includes(key) && keyCouldAct(key, state())) {
                s.seen.add(key);
                s.wrongRun = 0;
                s.armed = wanted.every(k => s.seen.has(k));
            } else if (!wanted.includes(key)) {
                s.wrongRun += 1;
                if (s.wrongRun >= 3) {
                    s.wrongRun = 0;
                    sayFailure();
                }
            }
            return false;
        }
        return s.armed && (ev.type === 'announcement' || (ev.type === 'ui_action' && ev.data.action === 'open'));
    }

    // The landmarks file names bands with their axis ("x.handle_loop"); the
    // lessons name them within the axis they already give ("handle_loop").
    // Everything here compares and speaks the short form, so a band is never
    // silently missed over the prefix.
    function shortBandName(name) {
        return String(name || '').replace(/^[xyz]\./, '');
    }

    function band(axis, name) {
        const axes = landmarks() && landmarks().axes;
        const bands = axes && Array.isArray(axes[axis]) ? axes[axis] : [];
        const wanted = shortBandName(name);
        return bands.find(b => shortBandName(b.name) === wanted) || null;
    }

    function bandAt(axis, percent) {
        const axes = landmarks() && landmarks().axes;
        const bands = axes && Array.isArray(axes[axis]) ? axes[axis] : [];
        return bands.find(b => percent >= Number(b.from) && percent <= Number(b.to)) || null;
    }

    /** 0 inside the band, otherwise how far outside it in percent; 1000 when
     * the cut is along another axis. */
    function bandDistance(axis, name, state) {
        const target = band(axis, name);
        if (!target) return 1000;
        if (state.cut_axis !== axis) return 1000;
        const percent = Number(state.cut_percent);
        if (percent < Number(target.from)) return Number(target.from) - percent;
        if (percent > Number(target.to)) return percent - Number(target.to);
        return 0;
    }

    function bandLabel(target) {
        return shortBandName(target.name || 'that part').replace(/_/g, ' ');
    }

    function whereAgainstBand(axis, name, state) {
        const target = band(axis, name);
        const letter = String(axis).toUpperCase();
        if (!target) return '';
        if (state.cut_axis !== axis) {
            return `You are cutting along ${String(state.cut_axis || '').toUpperCase()}; the ${bandLabel(target)} is along ${letter}.`;
        }
        const percent = Number(state.cut_percent);
        // Turn mode speaks depth from the reader, which from the right, the back
        // or above runs the opposite way to the axis. Speak the same numbers
        // the viewer just said, or the person is sent to the wrong end.
        if (state.axis_mode !== 'xyz') {
            const depth = Number(state.reader_depth);
            const flipped = Math.abs((100 - percent) - depth) < Math.abs(percent - depth);
            const toDepth = (p) => (flipped ? 100 - p : p);
            const low = Math.min(toDepth(Number(target.from)), toDepth(Number(target.to)));
            const high = Math.max(toDepth(Number(target.from)), toDepth(Number(target.to)));
            return `You are at depth ${Math.round(depth)} percent; the ${bandLabel(target)} is from depth `
                + `${Math.ceil(low)} to ${Math.floor(high)} percent.`;
        }
        // XYZ mode reads the cut out from the model's origin (#235), so the
        // numbers here are too: the band's are along the object, from its lowest
        // coordinate, like cut_percent. Whole percents inside the band only:
        // 40.5 to 59.5 is "41 to 59", so a position the check refuses is never
        // spoken as inside the range.
        const origin = Number(state.cut_origin_percent);
        const offset = state.cut_origin_percent !== null && Number.isFinite(origin) ? origin : 0;
        const said = (value) => (value < 0 ? `minus ${-value}` : String(value));
        return `You are at ${letter} ${said(Math.round(percent - offset))} percent; the ${bandLabel(target)} runs from `
            + `${letter} ${said(Math.ceil(Number(target.from) - offset))} to ${said(Math.floor(Number(target.to) - offset))} percent.`;
    }

    /** Say the step's on_fail once when the cut moves away from where it needs
     * to go, and not again until it has come closer. `tracker` holds the last
     * distance; larger is further away. */
    function warnIfMovedAway(tracker, distance) {
        if (!Number.isFinite(distance) || distance === tracker.last) return;
        if (distance > tracker.last && !tracker.warned) {
            tracker.warned = true;
            sayFailure();
        } else if (distance < tracker.last) {
            tracker.warned = false;
        }
        tracker.last = distance;
    }

    function cubeConnected() {
        const button = $('witmotion-disconnect-btn');
        return Boolean(button && !button.disabled);
    }

    function sliderConnected() {
        const button = $('trinkey-disconnect-btn');
        return Boolean(button && !button.disabled);
    }

    function deviceConnected(device, ev, state) {
        const wanted = String(device || 'any');
        const matches = (name) => wanted === name || (wanted === 'any' && (name === 'monarch' || name === 'dotpad'));
        if (ev && ev.type === 'device') {
            return Boolean(ev.data.connected) && matches(String(ev.data.device));
        }
        if (wanted === 'any') return Boolean(state.display_connected);
        if (wanted === 'monarch') return Boolean(state.monarch_connected);
        if (wanted === 'dotpad') return Boolean(state.dotpad_connected);
        if (wanted === 'cube') return cubeConnected();
        if (wanted === 'slider') return sliderConnected();
        return false;
    }

    const OPTIONAL_SECTION_CHECKBOXES = [
        'settings-enable-slider', 'settings-enable-cube', 'settings-enable-debug-panel', 'settings-enable-bbox',
    ];

    /** The settings lesson 10's tour must leave as they were. */
    function settingsFingerprint() {
        const now = getState();
        return JSON.stringify({
            axisMode: now.axis_mode,
            singleKeyShortcuts: now.single_key_shortcuts,
            outputDevice: now.output_device,
            sections: OPTIONAL_SECTION_CHECKBOXES.map(id => Boolean($(id) && $(id).checked)),
        });
    }

    // Jen's words (#245 review): "This lesson continues when you do it" left
    // people unsure what to do, or whether they had done it.
    const NOT_YET = 'Not quite. Try Hint to find out how to complete this, or Skip step to move on.';

    function sayFailure(extra) {
        const step = currentStep();
        const parts = [step && step.on_fail ? resolveText(step.on_fail) : '', extra || ''].filter(Boolean);
        if (!parts.length) parts.push(NOT_YET);
        say(parts.map(sentence).join(' '));
    }

    // -----------------------------------------------------------------------
    // Training wheels: the model chooser, the upload and the layout radios are
    // locked during lessons that ask for it, with a note saying so. viewer.js
    // keeps the chooser locked across its rebuilds (updateModelList).
    // -----------------------------------------------------------------------
    function addToken(node, attribute, token) {
        const tokens = new Set(String(node.getAttribute(attribute) || '').split(/\s+/).filter(Boolean));
        tokens.add(token);
        node.setAttribute(attribute, [...tokens].join(' '));
    }

    function removeToken(node, attribute, token) {
        const tokens = String(node.getAttribute(attribute) || '').split(/\s+/).filter(t => t && t !== token);
        if (tokens.length) node.setAttribute(attribute, tokens.join(' '));
        else node.removeAttribute(attribute);
    }

    function setLocked(locked) {
        cadTutorial.locked = locked;
        const dropdown = $('model-list-dropdown');
        const upload = $('upload-model-input');
        const uploadLabel = $('upload-model-label');
        const modelNote = $('model-lock-note');
        const layoutNote = $('layout-lock-note');
        const radios = document.querySelectorAll('input[name="view-mode"]');
        if (dropdown) {
            // Unlocked, it is disabled only when there is nothing to choose.
            const nothingListed = [...dropdown.options].every(option => option.value === '');
            dropdown.disabled = locked || nothingListed;
            (locked ? addToken : removeToken)(dropdown, 'aria-describedby', 'model-lock-note');
        }
        if (upload) {
            upload.disabled = locked;
            (locked ? addToken : removeToken)(upload, 'aria-describedby', 'model-lock-note');
        }
        if (uploadLabel) {
            if (locked) uploadLabel.setAttribute('aria-disabled', 'true');
            else uploadLabel.removeAttribute('aria-disabled');
        }
        radios.forEach((radio) => {
            radio.disabled = locked;
            (locked ? addToken : removeToken)(radio, 'aria-describedby', 'layout-lock-note');
        });
        if (modelNote) modelNote.hidden = !locked;
        if (layoutNote) layoutNote.hidden = !locked;
    }

    // -----------------------------------------------------------------------
    // Lesson poses: the view a lesson starts from, applied only when the viewer
    // is not already in it, since a load resets the pan and redraws.
    // -----------------------------------------------------------------------
    function sameVector(a, b) {
        return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => Number(v) === Number(b[i]));
    }

    /** Whether the orientation is the named view straight on, not turned. */
    function squaredUp(state, view) {
        const basis = typeof VIEW_BASIS === 'object' && VIEW_BASIS ? VIEW_BASIS[view] : null;
        const o = state.orientation;
        if (!basis || !o) return false;
        return sameVector(o.forward, basis.depth) && sameVector(o.up, basis.up) && sameVector(o.right, basis.right);
    }

    function poseMatches(pose, state) {
        const wantedAxisMode = pose.axis_mode && pose.axis_mode !== 'keep' ? pose.axis_mode : state.axis_mode;
        return state.model === (pose.model || practiceModel())
            && state.view === pose.view
            && squaredUp(state, pose.view)
            && state.axis_mode === wantedAxisMode
            && Number(state.depth) === Number(pose.depth)
            && state.render_mode === pose.render_mode
            && state.layout_mode === pose.representation_mode
            && Boolean(state.compose_scrollbar) === Boolean(pose.compose_scrollbar)
            && Number(state.zoom) === Number(pose.zoom)
            && !(pose.reset_pan && panMoved);
    }

    /** Put the viewer in `pose`. Returns true when it changed anything. */
    function applyPose(pose, { force = false, overrides = null } = {}) {
        const wanted = { ...DEFAULT_POSE, ...(pose || {}), ...(overrides || {}) };
        const before = getState();
        if (!force && poseMatches(wanted, before)) return false;
        const defaults = {
            view: wanted.view,
            depth: wanted.depth,
            render_mode: wanted.render_mode,
            representation_mode: wanted.representation_mode,
            compose_scrollbar: wanted.compose_scrollbar,
            zoom: wanted.zoom,
            reset_pan: wanted.reset_pan,
        };
        if (wanted.axis_mode === 'xyz' || wanted.axis_mode === 'turn') {
            defaults.axis_mode = wanted.axis_mode;
            if (wanted.axis_mode !== before.axis_mode && touched.axisMode === undefined) {
                touched.axisMode = before.axis_mode;
            }
        }
        const model = wanted.model || practiceModel();
        if (before.model !== model) announceNextMugRender = model === practiceModel();
        study.loadModel(model, PRACTICE_LABEL, defaults, 'tutorial');
        panMoved = false;
        return true;
    }

    /** The lessons that set something up beyond their pose, by id: the runner
     * steps the contract describes in section 8. Each returns words to say, or
     * nothing. */
    const LESSON_SETUP = {
        // "The runner changes the axis and cut without saying which."
        mug_detective: (lesson) => {
            const views = { x: 'x-', y: 'y-', z: 'z+' };
            const axes = Object.keys(views);
            const axis = axes[Math.floor(Math.random() * axes.length)];
            // Along X, never inside the handle loop: the next step asks the
            // person to find it, and starting there left nothing to do (#245
            // review). From the right, depth d is 100 - d along X, and the loop
            // is about 40 to 60, so the cut goes to 25 to 35 or 65 to 75.
            const depth = axis === 'x'
                ? (Math.random() < 0.5 ? 25 : 65) + Math.floor(Math.random() * 11)
                : 30 + Math.floor(Math.random() * 41);
            applyPose(lesson.pose, { force: true, overrides: { view: views[axis], depth } });
            return 'The tutorial has picked an axis and a cut for you, without saying which.';
        },
        // "Pose default, then the runner zooms to 0.3 and pans once, and says so."
        reset_and_fit: (lesson) => {
            applyPose(lesson.pose, { force: true, overrides: { zoom: 0.3 } });
            window.setPendingInputSource?.('tutorial');
            if (typeof study.moveObject === 'function') study.moveObject('left');
            panMoved = true;
            return 'The tutorial has zoomed in to 30% and moved the mug to the left, so there is something to put right.';
        },
    };

    function revealSectionsFor(requires) {
        const sections = { cube: ['witmotion-section'], slider: ['trinkey-section'] };
        for (const need of requires || []) {
            for (const id of sections[need] || []) {
                const section = $(id);
                if (section && section.hidden) {
                    // Shown without saving it: Settings still says what it said,
                    // and exit puts it back.
                    section.hidden = false;
                    touched.sections.add(id);
                }
            }
        }
    }

    function restoreSections() {
        const checkboxes = { 'witmotion-section': 'settings-enable-cube', 'trinkey-section': 'settings-enable-slider' };
        for (const id of touched.sections) {
            const section = $(id);
            const checkbox = $(checkboxes[id]);
            if (section) section.hidden = !(checkbox && checkbox.checked);
        }
        touched.sections.clear();
    }

    function storedAxisMode() {
        try {
            // Unset is XYZ, the viewer's default since #235.
            const stored = window.localStorage.getItem('settingsAxisMode');
            return stored === 'turn' ? 'turn' : 'xyz';
        } catch (_) {
            return null;
        }
    }

    // -----------------------------------------------------------------------
    // Rendering the region
    // -----------------------------------------------------------------------
    function showRegion() {
        region.hidden = false;
        cadTutorial.running = true;
        syncDialogSection();
    }

    function hideRegion() {
        region.hidden = true;
        cadTutorial.running = false;
        syncDialogSection();
    }

    function syncDialogSection() {
        // Known from the record alone, before the lessons have been fetched.
        const canResume = route.resume && Boolean(record.lesson) && record.status !== 'completed';
        if (el.dialogResumeBtn) el.dialogResumeBtn.hidden = !canResume;
        // Only with a lesson on screen, where its own toggle is too: from the
        // lesson list there would be no way back to turn it off.
        if (el.dialogKeyHelpBtn) el.dialogKeyHelpBtn.hidden = !(cadTutorial.running && runner.view === 'lesson');
    }

    /** Run a change to the region, and if it removed or hid the control that
     * had focus, put focus on the heading rather than leave it nowhere. */
    function keepingFocus(change) {
        const active = document.activeElement;
        const inside = active && region.contains(active) && active !== heading;
        change();
        if (!inside) return;
        const gone = !document.contains(active) || active.closest('[hidden]') || active.hidden || active.disabled;
        if (gone) heading.focus();
    }

    function renderLesson() {
        const lesson = currentLesson();
        if (!lesson) return;
        keepingFocus(() => {
            el.lessonView.hidden = false;
            el.menuView.hidden = true;
            heading.textContent = headingText(lesson);
            const step = currentStep();
            const check = currentCheck();

            if (runner.phase === 'blocked') {
                el.progress.textContent = 'Cannot start yet';
                el.stepText.textContent = `This lesson cannot start yet: ${runner.blockedReason}. Next skips it.`;
            } else if (runner.phase === 'done') {
                el.progress.textContent = 'Lesson done';
                el.stepText.textContent = lessonDoneText();
            } else {
                el.progress.textContent = stepNumberText();
                el.stepText.textContent = step ? resolveText(step.text) : '';
            }

            const note = runner.phase === 'step' ? stepNote(step) : '';
            el.note.textContent = note;
            el.note.hidden = !note;

            renderAnswers(runner.phase === 'step' ? check : null, step);
            renderHints(runner.phase === 'step' ? step : null);

            // Lesson 1 carries the per-screen-reader help for keys that do not
            // reach the page, from the key step's hints.
            const keysHints = keysHelpHints(lesson);
            el.keysBtn.hidden = keysHints.length === 0;
            if (keysHints.length === 0) {
                el.keysHelp.hidden = true;
                el.keysBtn.setAttribute('aria-expanded', 'false');
            }
            el.keysHelpList.replaceChildren(...keysHints.map((hint) => {
                const item = document.createElement('li');
                item.textContent = resolveText(hint);
                return item;
            }));

            const connecting = runner.phase === 'step' && check.type === 'device'
                && ['any', 'monarch', 'dotpad'].includes(String(check.device || 'any'));
            const display = displayForLessons();
            el.connectMonarchBtn.hidden = !(connecting && display !== 'dotpad');
            el.connectDotpadBtn.hidden = !(connecting && display !== 'monarch');

            // Any lesson can be skipped, not only the ones that used to be
            // called optional (#245 review).
            el.skipLessonBtn.hidden = !(runner.phase === 'blocked' || runner.phase === 'step');
            el.showMeBtn.hidden = !(runner.phase === 'step' && showMeMoves(step, lesson));
            el.prints.hidden = lesson.id !== 'your_own_model';
            updateHintButton(step);
        });
    }

    function stepNote(step) {
        if (!step) return '';
        const notes = [];
        if (stepWasPassed()) notes.push('You have done this step. Next moves on, or do it again.');
        if (step.key_only && getState().single_key_shortcuts === false) {
            notes.push('Single-key shortcuts are off in Settings, so this step cannot be done from the keyboard. Skip step moves on.');
        }
        const frames = step.demo && Array.isArray(step.demo.frames) ? step.demo.frames : [];
        if (frames.length && runner.demoFrame > 0) {
            const frame = frames[runner.demoFrame - 1];
            notes.push(`Example ${runner.demoFrame} of ${frames.length}: ${resolveText(frame.say)}`);
        } else if (frames.length) {
            notes.push(`Next shows the example, one cut at a time (${frames.length} in all).`);
        }
        return notes.map(sentence).join(' ');
    }

    function keysHelpHints(lesson) {
        if (!lesson || lessonIndexById(lesson.id) !== 0) return [];
        const keyStep = (lesson.steps || []).find(step => step.key_only || (step.check && step.check.type === 'key'));
        return keyStep && Array.isArray(keyStep.hints) ? keyStep.hints : [];
    }

    function renderAnswers(check, step) {
        let choices = [];
        let label = 'Answers';
        if (check && check.type === 'answer' && step && Array.isArray(step.answers)) {
            choices = step.answers.map(a => ({ label: a.label, value: a.value === undefined ? a.label : a.value }));
        } else if (check && check.type === 'test_pattern') {
            choices = [{ label: 'Left', value: 'left' }, { label: 'Right', value: 'right' }];
            label = 'Which side is the raised block on?';
        }
        el.answersLabel.textContent = label;
        el.answerButtons.replaceChildren(...choices.map((choice) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = choice.label;
            button.addEventListener('click', () => chooseAnswer(choice.value, choice.label));
            return button;
        }));
        el.answers.hidden = choices.length === 0;
    }

    function renderHints(step) {
        const hints = step && Array.isArray(step.hints) ? step.hints.slice(0, runner.hintsShown) : [];
        el.hints.replaceChildren(...hints.map((hint) => {
            const item = document.createElement('li');
            item.textContent = resolveText(hint);
            return item;
        }));
        el.hints.hidden = hints.length === 0;
    }

    function updateHintButton(step) {
        const total = step && Array.isArray(step.hints) ? step.hints.length : 0;
        const next = Math.min(runner.hintsShown + 1, Math.max(total, 1));
        el.hintBtn.textContent = total ? `Hint ${next} of ${total}` : 'Hint';
    }

    /** The words on the page again, after the lessons were fetched for another
     * display, without rebuilding a button that may have focus. */
    function refreshTexts() {
        if (runner.view !== 'lesson') return;
        const lesson = currentLesson();
        if (!lesson) return;
        heading.textContent = headingText(lesson);
        refreshStepText();
        if (runner.phase === 'step') {
            const note = stepNote(currentStep());
            el.note.textContent = note;
            el.note.hidden = !note;
            renderHints(currentStep());
        }
        el.keysHelpList.replaceChildren(...keysHelpHints(lesson).map((hint) => {
            const item = document.createElement('li');
            item.textContent = resolveText(hint);
            return item;
        }));
    }

    /** Step text again after the viewer changed, for the runtime words in it
     * (which side the handle is on, and so on). Not said: it is on the page. */
    function refreshStepText() {
        if (runner.view !== 'lesson' || runner.phase !== 'step') return;
        const step = currentStep();
        if (!step) return;
        const text = resolveText(step.text);
        if (el.stepText.textContent !== text) el.stepText.textContent = text;
    }

    // -----------------------------------------------------------------------
    // Moving through lessons and steps
    // -----------------------------------------------------------------------
    function spokenStep(step) {
        return resolveText(step.sr || step.text);
    }

    function stepKey(lesson = currentLesson(), index = runner.stepIndex) {
        return lesson ? `${lesson.id}:${index}` : '';
    }

    /** Whether the current step was passed or skipped already: earlier in this
     * run, or as part of a lesson finished before. */
    function stepWasPassed() {
        const lesson = currentLesson();
        if (!lesson) return false;
        return runner.passed.has(stepKey(lesson)) || record.lessons[lesson.id] === 'done';
    }

    function saveProgress() {
        const lesson = currentLesson();
        if (!lesson) return;
        record.lesson = lesson.id;
        record.step = runner.stepIndex;
        if (record.status !== 'completed') record.status = 'in-progress';
        writeRecord();
    }

    function leaveStep() {
        endTestPattern();
        runner.checkState = null;
        runner.stepSerial += 1;
        runner.showMeFrame = 0;
        // A /tutorial/locate answer still on its way is for the step being left:
        // it used to be spoken in whatever lesson came next (#245 review).
        locateSeq += 1;
    }

    /** Open step `index` of the current lesson. `lead` is said first (what the
     * last step did, or which lesson this is); `silent` leaves it unsaid, for
     * the first lesson, where focus on the heading says it. */
    function enterStep(index, { lead = [], silent = false } = {}) {
        leaveStep();
        runner.stepIndex = index;
        runner.phase = 'step';
        runner.hintsShown = 0;
        runner.demoFrame = 0;
        const step = currentStep();
        const check = currentCheck();
        runner.entry = getState();
        runner.checkState = startCheck(check, checkContext());
        runner.lastNarrated = narrationKey(step, check, runner.entry);
        saveProgress();
        renderLesson();

        if (!silent) {
            const note = stepNote(step);
            say([...lead, `${stepNumberText()}.`, spokenStep(step), note].filter(Boolean).join(' '), step.braille);
        }

        if (check.type === 'test_pattern') beginTestPattern();
        if (lessonUsesLocate(currentLesson())) locateHandle();
        const impl = CHECKS[check.type];
        if (impl && impl.atEntry) queueEvent({ type: 'entry', data: {} });
    }

    function lessonDoneText() {
        const lesson = currentLesson();
        const next = lessons()[runner.lessonIndex + 1];
        if (!next) return 'That was the last lesson. Next (N) finishes the tutorial.';
        if (next.part === 'extras' && lesson && lesson.part !== 'extras') {
            const extras = lessons().filter(l => l.part === 'extras').map(l => l.title).join(', ');
            return `${lessonNumberText()} is done, and that is the end of the tutorial. Next (N) goes on to `
                + `the extras: ${extras}. Exit tutorial leaves them for another time; they are in the list of lessons.`;
        }
        return `${lessonNumberText()} is done. Next (N) goes on to lesson ${runner.lessonIndex + 2}, ${next.title}.`;
    }

    function passStep(lead = null) {
        const step = currentStep();
        const done = lead !== null ? lead : (step && step.done ? resolveText(step.done) : 'Done.');
        runner.passed.add(stepKey());
        leaveStep();
        const steps = applicableSteps();
        const position = steps.indexOf(runner.stepIndex);
        const nextIndex = position >= 0 ? steps[position + 1] : steps.find(i => i > runner.stepIndex);
        if (nextIndex !== undefined) {
            enterStep(nextIndex, { lead: [sentence(done)] });
            return;
        }
        const lesson = currentLesson();
        runner.phase = 'done';
        record.lessons[lesson.id] = 'done';
        saveProgress();
        // Resume, a reload or the Lessons list should go on from here, not
        // reopen the last step of a lesson that is done.
        const next = lessons()[runner.lessonIndex + 1];
        if (next) {
            record.lesson = next.id;
            record.step = 0;
        }
        // The tutorial is finished when its last lesson is, and so is the main
        // tutorial when the extras are all that is left: a reload or a closed
        // tab must not reopen it at its end. The record used to stay on the
        // final step (#245 review).
        if (!next || (next.part === 'extras' && lesson.part !== 'extras')) {
            record.status = 'completed';
            if (payload && typeof payload.version === 'number') record.version = payload.version;
        }
        writeRecord();
        track('lesson_completed', lesson.id);
        renderLesson();
        say(`${sentence(done)} ${lessonDoneText()}`, `Lesson ${runner.lessonIndex + 1} done`);
    }

    function lessonUsesLocate(lesson) {
        if (!lesson) return false;
        const types = new Set();
        const collect = (check) => {
            if (!check) return;
            types.add(check.type);
            (check.checks || []).forEach(collect);
        };
        (lesson.steps || []).forEach(step => collect(step.check));
        return types.has('cursor_in') || types.has('centred')
            || JSON.stringify(lesson.steps || []).includes('{pan_toward_handle}');
    }

    /** Open lesson `index`. `stepIndex` is a step to resume at, or "last" when
     * going back into it. `direction` is where a skipped lesson leads. */
    function enterLesson(index, {
        stepIndex = null, lead = [], silent = false, focus = false, forcePose = false, direction = 1,
    } = {}) {
        if (index < 0) {
            say('This is the first step of the tutorial.');
            return;
        }
        leaveStep();
        const all = lessons();
        if (index >= all.length) {
            completeTutorial();
            return;
        }
        runner.view = 'lesson';
        runner.runActive = true;
        runner.lessonIndex = index;
        runner.blockedReason = null;
        lastLocatedPose = null;
        located = null;
        locateFailureSaid = false;
        showRegion();
        // "Last:" describes this lesson's actions, not the previous lesson's.
        setLast('');
        const lesson = all[index];

        // Someone who said they have no display skips the display lessons, with
        // a word, rather than meeting "Cannot start yet" three times.
        const needsDisplay = (lesson.requires || []).includes('display') || lesson.id === 'connect';
        if (needsDisplay && saidNoDisplay() && !getState().display_connected) {
            record.lessons[lesson.id] = 'skipped';
            writeRecord();
            runner.pendingNotes.push(`Lesson ${index + 1}, ${lesson.title}, needs a display, so it is skipped.`);
            enterLesson(index + direction, { stepIndex: direction < 0 ? 'last' : null, lead, silent, focus, direction });
            return;
        }

        const intro = `${lessonNumberText(index)}: ${lesson.title}.`;
        const notes = runner.pendingNotes.splice(0);
        const reason = unmetRequirement(lesson.requires);
        setLocked(Boolean(lesson.lock));
        if (reason) {
            runner.phase = 'blocked';
            runner.blockedReason = reason;
            runner.stepIndex = -1;
            // Keep the step within this lesson, so connecting the display and
            // coming back lands where the person was.
            if (record.lesson !== lesson.id) record.step = 0;
            record.lesson = lesson.id;
            writeRecord();
            renderLesson();
            if (focus) heading.focus();
            if (!silent) {
                say([...lead, ...notes, focus ? '' : intro,
                    `It cannot start yet: ${reason}. Next skips it; Lessons lists every lesson.`]
                    .filter(Boolean).join(' '), 'Cannot start yet');
            }
            return;
        }

        revealSectionsFor(lesson.requires);
        const steps = applicableSteps(lesson);
        let target = steps[0];
        if (stepIndex === 'last') target = steps[steps.length - 1];
        else if (Number.isInteger(stepIndex) && steps.includes(stepIndex)) target = stepIndex;
        else if (Number.isInteger(stepIndex)) target = steps.find(i => i >= stepIndex) ?? steps[0];

        // Coming back (Back, Resume, a reload) into a step that belongs to the
        // axis mode the person chose opens in that mode, not the lesson's.
        const targetWhen = target !== undefined && lesson.steps[target] ? lesson.steps[target].when : null;
        const chosenMode = targetWhen && targetWhen.stored === 'axis_choice' && record.answers.axis_choice
            ? (norm(record.answers.axis_choice) === 'turn' ? 'turn' : 'xyz') : null;
        let poseNote = '';
        if (LESSON_SETUP[lesson.id]) {
            poseNote = LESSON_SETUP[lesson.id](lesson) || '';
        } else if (lesson.pose) {
            const overrides = chosenMode ? { axis_mode: chosenMode } : null;
            if (applyPose(lesson.pose, { force: forcePose, overrides }) && !announceNextMugRender) {
                poseNote = 'The mug is back in this lesson\'s starting view.';
            }
        } else if (getState().model !== practiceModel() && lesson.id !== 'your_own_model') {
            // A lesson with no view of its own still needs the mug.
            applyPose(DEFAULT_POSE);
        }

        if (target === undefined) {
            // Nothing in it applies here (every step needs something absent).
            record.lessons[lesson.id] = 'skipped';
            runner.pendingNotes.push(...notes, `Lesson ${index + 1}, ${lesson.title}, has nothing to do here, so it is skipped.`);
            enterLesson(index + direction, { lead, silent, focus, direction });
            return;
        }
        // With focus moving to the heading, the heading says which lesson this
        // is, so the announcement does not say it again.
        enterStep(target, { lead: [...lead, ...notes, focus ? '' : intro, poseNote].filter(Boolean), silent });
        if (focus) heading.focus();
    }

    // -----------------------------------------------------------------------
    // Show me (#245 review: every step of the X, Y and Z lesson was hard). On a
    // step that waits for the cut to reach a place, the tutorial puts it there,
    // the viewer says where it is as it would for a key, the step's own words
    // say what is there, and the step counts as done. Not on a step that
    // teaches a key, asks a question or needs the cube or the slider: there the
    // doing is the lesson.
    // -----------------------------------------------------------------------

    /** The moves Show me makes for a step, in order, or null when it has none. */
    function showMeMoves(step, lesson = currentLesson()) {
        if (!step || !lesson) return null;
        if ((lesson.requires || []).some(need => need === 'cube' || need === 'slider')) return null;
        const moves = [];
        const add = (check) => {
            if (!check) return false;
            if (check.type === 'all') return (check.checks || []).every(add);
            if (check.type === 'state' && check.field === 'cut_axis') {
                moves.push({ axis: check.equals });
                return true;
            }
            if (check.type === 'in_band' || check.type === 'mark_band') {
                moves.push({ axis: check.axis, band: check.band });
                return true;
            }
            if (check.type === 'edge') {
                moves.push({ edge: true });
                return true;
            }
            if (check.type === 'sweep') {
                moves.push({ axis: check.axis, frames: sweepFrames(step, check) });
                return true;
            }
            return false;
        };
        return add(step.check) && moves.length ? moves : null;
    }

    /** Where Show me stops on a sweep, one press each: the bands the step
     * narrates, from the low end of the axis, then the far end of the sweep if
     * the bands stop short of it. */
    function sweepFrames(step, check) {
        const frames = Object.entries(step.narrate || {})
            .map(([name, line]) => ({ target: band(check.axis, name), line }))
            .filter(frame => frame.target)
            .sort((a, b) => Number(a.target.from) - Number(b.target.from))
            .map(frame => ({ percent: (Number(frame.target.from) + Number(frame.target.to)) / 2, line: frame.line }));
        if (!frames.length || frames[0].percent > Number(check.from)) frames.unshift({ percent: Number(check.from), line: '' });
        if (frames[frames.length - 1].percent < Number(check.to)) frames.push({ percent: Number(check.to), line: '' });
        return frames;
    }

    /** Cut along `axis`, as its key would in XYZ mode, or by turning to its
     * view in Turn mode, which has no axis keys. Nothing if already there. */
    function showMeAxis(axis) {
        if (getState().cut_axis === axis || typeof XYZ_AXES !== 'object' || !XYZ_AXES[axis]) return;
        const home = XYZ_AXES[axis].views[0];
        window.setPendingInputSource?.('tutorial');
        if (getState().axis_mode === 'xyz') showXyzView(home, window.announceAlert);
        else updateView(home);
    }

    /** Put the cut at `percent` along `axis`, from the object's low end, and
     * let the viewer say it in the mode's own terms. */
    function showMeCut(axis, percent) {
        const before = getState();
        window.setPendingInputSource?.('tutorial');
        const xyz = before.axis_mode === 'xyz';
        window.setCutPosition(axis, percent / 100, window.announceAlert, { announce: xyz });
        if (!xyz && typeof window.announceDepthValue === 'function') {
            window.announceDepthValue(getState().depth, before.depth, window.announceAlert);
        }
    }

    function showMe() {
        if (runner.view !== 'lesson' || runner.phase !== 'step') return;
        const step = currentStep();
        const moves = showMeMoves(step);
        if (!moves) {
            say('Show me has nothing to show on this step.');
            return;
        }
        for (const move of moves) {
            if (move.axis) showMeAxis(move.axis);
            if (move.edge && typeof window.goToSliceEnd === 'function') {
                window.setPendingInputSource?.('tutorial');
                window.goToSliceEnd(true, window.announceAlert);
            }
            if (move.band) {
                const target = band(move.axis, move.band);
                if (target) showMeCut(move.axis, (Number(target.from) + Number(target.to)) / 2);
            }
            if (move.frames) {
                // One stop per press, so each slice can be felt before the next.
                const frame = move.frames[runner.showMeFrame];
                runner.showMeFrame += 1;
                showMeCut(move.axis, frame.percent);
                // Said here, with its place in the sweep, so the check's own
                // narration does not say it a second time.
                runner.lastNarrated = narrationKey(step, currentCheck(), getState());
                if (runner.showMeFrame < move.frames.length) {
                    say(`${frame.line ? sentence(resolveText(frame.line)) : ''} Show me ${runner.showMeFrame} of `
                        + `${move.frames.length}; press it again for the next.`);
                    return;
                }
                if (frame.line) say(resolveText(frame.line));
            }
        }
        passStep();
    }

    // -----------------------------------------------------------------------
    // The buttons and N, B, C
    // -----------------------------------------------------------------------
    function continueAction() {
        if (runner.view !== 'lesson') return;
        if (runner.phase === 'done') {
            enterLesson(runner.lessonIndex + 1);
            return;
        }
        if (runner.phase === 'blocked') {
            // Continue keeps its place in the row, so focus stays on it.
            skipLesson({ focus: false });
            return;
        }
        const step = currentStep();
        const check = currentCheck();
        // Back to a step already done, and Next again: straight on, without
        // doing it a second time (#245 review).
        if (stepWasPassed()) {
            passStep('');
            return;
        }
        const frames = step && step.demo && Array.isArray(step.demo.frames) ? step.demo.frames : [];
        if (runner.demoFrame < frames.length) {
            showDemoFrame(frames, step);
            return;
        }
        const impl = CHECKS[check.type];
        if (impl && impl.proceed) {
            const passed = impl.proceed(check, runner.checkState, checkContext());
            if (passed) passStep();
            return;
        }
        if (check.type === 'answer') {
            const labels = (step.answers || []).map(a => a.label).join(', ');
            say(`Choose one of the answers: ${labels}.`);
            return;
        }
        if (check.type === 'test_pattern') {
            say('Answer with dot 1 for left or dot 4 for right, or the Left and Right buttons.');
            return;
        }
        say(NOT_YET);
    }

    function showDemoFrame(frames, step) {
        const frame = frames[runner.demoFrame];
        runner.demoFrame += 1;
        const axis = (step.check && step.check.axis) || getState().cut_axis;
        const percent = Number(frame.cut_percent);
        if (axis && Number.isFinite(percent) && typeof window.setCutPosition === 'function') {
            window.setPendingInputSource?.('tutorial');
            window.setCutPosition(axis, percent / 100, window.announce, { announce: false });
        }
        renderLesson();
        const more = runner.demoFrame < frames.length ? '' : ' That was the last one; Next goes on.';
        // The braille line uses the viewer's own numbers: depth in Turn mode,
        // and in XYZ mode the position along the axis, from the origin (#235).
        const now = getState();
        const origin = now.cut_origin_percent === null ? 0 : Number(now.cut_origin_percent) || 0;
        const brailleLine = now.axis_mode === 'xyz'
            ? `${String(axis || '').toUpperCase()} ${Math.round(percent - origin)}%`
            : `Depth ${Math.round(Number(now.reader_depth))}%`;
        say(`Example ${runner.demoFrame} of ${frames.length}. ${resolveText(frame.say)}${more}`, brailleLine);
    }

    function back() {
        if (runner.view !== 'lesson') return;
        if (runner.phase === 'done') {
            const steps = applicableSteps();
            if (steps.length) {
                enterStep(steps[steps.length - 1], { lead: ['Back.'] });
                return;
            }
        }
        if (runner.phase === 'step') {
            const steps = applicableSteps();
            const position = steps.indexOf(runner.stepIndex);
            if (position > 0) {
                enterStep(steps[position - 1], { lead: ['Back.'] });
                return;
            }
        }
        enterLesson(runner.lessonIndex - 1, { stepIndex: 'last', lead: ['Back.'], direction: -1 });
    }

    function repeat() {
        if (runner.view !== 'lesson') return;
        const lesson = currentLesson();
        if (!lesson) return;
        const intro = `${lessonNumberText()}: ${lesson.title}.`;
        if (runner.phase === 'blocked') {
            say(`${intro} It cannot start yet: ${runner.blockedReason}. Next skips it.`);
        } else if (runner.phase === 'done') {
            say(`${intro} ${lessonDoneText()}`);
        } else {
            const step = currentStep();
            say([intro, `${stepNumberText()}.`, spokenStep(step), stepNote(step)].filter(Boolean).join(' '), step.braille);
        }
    }

    function hint() {
        if (runner.view !== 'lesson') return;
        const step = currentStep();
        const hints = runner.phase === 'step' && step && Array.isArray(step.hints) ? step.hints : [];
        if (!hints.length) {
            say(runner.phase === 'blocked' ? 'No hints here. Next skips this lesson.' : 'No hints here. Next goes on.');
            return;
        }
        if (runner.hintsShown < hints.length) runner.hintsShown += 1;
        renderLesson();
        say(`Hint ${runner.hintsShown} of ${hints.length}: ${resolveText(hints[runner.hintsShown - 1])}`);
    }

    function skipStep() {
        if (runner.view !== 'lesson') return;
        if (runner.phase !== 'step') {
            continueAction();
            return;
        }
        passStep('Skipped.');
    }

    /** Skip the whole lesson. From its own button focus goes to the next
     * lesson's heading, since the button may not be there in it. */
    function skipLesson({ focus = true } = {}) {
        if (runner.view !== 'lesson') return;
        const lesson = currentLesson();
        if (!lesson) return;
        if (record.lessons[lesson.id] !== 'done') record.lessons[lesson.id] = 'skipped';
        writeRecord();
        track('lesson_skipped', lesson.id);
        enterLesson(runner.lessonIndex + 1, { lead: [`Lesson ${runner.lessonIndex + 1} skipped.`], focus });
    }

    function restartLesson() {
        if (runner.view !== 'lesson') return;
        enterLesson(runner.lessonIndex, { lead: ['Back to the start of this lesson.'], forcePose: true });
    }

    function chooseAnswer(value, label) {
        if (runner.view !== 'lesson' || runner.phase !== 'step') return;
        const check = currentCheck();
        const step = currentStep();
        if (check.type === 'test_pattern') {
            answerTestPattern(norm(value));
            return;
        }
        if (check.type !== 'answer') return;
        setLast(`You answered ${label}`);
        const passed = CHECKS.answer.answer(check, runner.checkState, checkContext(), value);
        if (!passed) return;
        if (step.store) {
            record.answers[step.store] = value;
            writeRecord();
            useStoredAnswer(step.store, value);
        }
        passStep();
    }

    /** Answers that change what the tutorial does next. */
    function useStoredAnswer(name, value) {
        if (name === 'display') {
            // The lesson text names keys for the display chosen, so fetch it
            // again for that display. The lessons are the same; only words change.
            ensurePayload().then(refreshTexts)
                .catch((error) => console.warn('Tutorial lessons did not reload:', error));
        }
        if (name === 'axis_choice' && typeof window.setAxisMode === 'function') {
            // The person's own choice, so it is saved, and not put back on exit.
            const mode = norm(value) === 'turn' ? 'turn' : 'xyz';
            // Only when the mode changes: setAxisMode sends no render otherwise,
            // and the tag would land on the person's next key instead.
            if (getState().axis_mode !== mode) window.setPendingInputSource?.('tutorial');
            window.setAxisMode(mode, { announce: false, persist: true });
            touched.axisMode = undefined;
            // The Turn-mode steps ask where the handle goes after a turn, so the
            // cut has to go through it. Each axis keeps its own cut, and the
            // earlier steps leave Z at the rim, so a pitch would otherwise land
            // on an empty slice. Keep the view; put every cut back in the middle.
            if (mode === 'turn') {
                applyPose(currentLesson().pose, { force: true, overrides: { view: getState().view, axis_mode: 'turn' } });
            }
        }
    }

    // -----------------------------------------------------------------------
    // The test pattern (lesson 3): a frame and a raised block in one corner,
    // held on the pins while the person answers which side the block is on.
    // -----------------------------------------------------------------------
    const CORNERS = ['tl', 'tr', 'bl', 'br'];
    const CORNER_WORDS = { tl: 'top left', tr: 'top right', bl: 'bottom left', br: 'bottom right' };
    const testPattern = { active: false, awaiting: false, corner: null, round: 0, seq: 0 };

    function beginTestPattern() {
        testPattern.active = true;
        testPattern.round = 0;
        nextTestPatternRound();
    }

    /** Draw the next pattern and ask. `lead` is what to say first (how the last
     * answer went), in the same utterance as the question: said on its own, it
     * would be overwritten by the question a moment later. */
    async function nextTestPatternRound(lead = '') {
        const seq = ++testPattern.seq;
        testPattern.round += 1;
        testPattern.awaiting = false;
        testPattern.corner = CORNERS[Math.floor(Math.random() * CORNERS.length)];
        const grid = typeof window.activeTactileGrid === 'function' ? window.activeTactileGrid() : null;
        if (!grid) {
            // Nothing to draw it on, so nothing to answer: dots 1 and 4 go back
            // to moving the cut instead of waiting for an answer (#245 review).
            testPattern.active = false;
            say(`${lead} The test pattern could not be drawn. Skip step moves on.`);
            return;
        }
        cadTutorial.holdDisplay = true;
        let data = null;
        try {
            const res = await fetch('/tutorial/test-pattern', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ corner: testPattern.corner, width: grid.pixelWidth, height: grid.pixelHeight }),
            });
            data = res.ok ? await res.json() : null;
        } catch (error) {
            console.warn('Tutorial test pattern failed:', error);
        }
        if (seq !== testPattern.seq || !testPattern.active) return;
        if (!data || data.status !== 'success') {
            releaseDisplay();
            // As above: with no pattern up, the dots move the cut again.
            testPattern.active = false;
            say(`${lead} The test pattern could not be drawn. Skip step moves on.`);
            return;
        }
        // Only to the display the grid is for: the two are different sizes.
        const now = getState();
        if (grid.type === 'Monarch' && now.monarch_connected && typeof window._monarchHidOnRender === 'function') {
            window._monarchHidOnRender(data.monarch_cells_hex);
        } else if (grid.type === 'DotPad' && now.dotpad_connected && typeof window._dotpadShowHex === 'function') {
            window._dotpadShowHex(data.dotpad_graphic_hex);
        }
        testPattern.awaiting = true;
        say(`${lead} Pattern ${testPattern.round}. Feel the frame and find the raised block. Which side is it on? `
            + 'Dot 1 for left, dot 4 for right, or the Left and Right buttons.', 'Block: dot1 L dot4 R');
    }

    function answerTestPattern(side) {
        if (!testPattern.active || !testPattern.awaiting) return;
        testPattern.awaiting = false;
        const corner = testPattern.corner;
        const wanted = corner === 'tl' || corner === 'bl' ? 'left' : 'right';
        const correct = side === wanted;
        setLast(`You answered ${side}; the block was ${CORNER_WORDS[corner]}`);
        if (evaluate({ type: 'test_pattern', data: { correct } })) return;
        const step = currentStep();
        const lead = correct
            ? `Yes, ${CORNER_WORDS[corner]}. Lift your hands; here is the next one.`
            : [step && step.on_fail ? sentence(resolveText(step.on_fail)) : 'Not that side.',
                `That one was ${CORNER_WORDS[corner]}. Lift your hands; here is another.`].join(' ');
        nextTestPatternRound(lead);
    }

    function releaseDisplay() {
        if (!cadTutorial.holdDisplay) return;
        cadTutorial.holdDisplay = false;
        // The live view goes back on the pins.
        window.setPendingInputSource?.('tutorial');
        if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
    }

    function endTestPattern() {
        if (!testPattern.active && !cadTutorial.holdDisplay) return;
        testPattern.active = false;
        testPattern.awaiting = false;
        testPattern.seq += 1;
        releaseDisplay();
    }

    // -----------------------------------------------------------------------
    // Where the handle is on the pins, for lesson 7, Zoom and move: the server
    // works it out for the render on the display (/tutorial/locate).
    // After each render whose view changed it is said, since people lose their
    // place on a pan or zoom.
    // -----------------------------------------------------------------------
    const SIDE_WORDS = {
        left: 'left', right: 'right', top: 'top', bottom: 'bottom', centre: 'in the middle',
        'off-left': 'off the left edge', 'off-right': 'off the right edge',
        'off-top': 'off the top edge', 'off-bottom': 'off the bottom edge',
    };

    /** ", left of the mug" when the handle sits wholly beside the body: at zoom
     * 0 the mug is in the middle of the pins, so "in the middle" alone would
     * leave the handle's side unsaid. Nothing when the two overlap. */
    function handleAgainstBody(mark, body) {
        if (!body || body.present === false || !Array.isArray(body.box) || !Array.isArray(mark.centre)) return '';
        const [col, row] = mark.centre.map(Number);
        const [c0, r0, c1, r1] = body.box.map(Number);
        if (col < c0) return ', left of the mug';
        if (col > c1) return ', right of the mug';
        if (row < r0) return ', above the mug';
        if (row > r1) return ', below the mug';
        return '';
    }

    async function locateHandle() {
        const now = getState();
        const body = now.last_render_state;
        // The server answers only for the mug, in the single and slice-graph
        // layouts; asking otherwise would be an error in the console for nothing.
        if (!body || body.model !== practiceModel() || now.layout_mode === 'side-by-side') return;
        // A render for a newer view is on its way; its render event asks again.
        const drawn = (s) => JSON.stringify([s.model, s.view, s.orientation, Number(s.zoom), Number(s.depth)]);
        if (drawn(body) !== drawn(now)) return;
        const seq = ++locateSeq;
        let data = null;
        try {
            const res = await fetch('/tutorial/locate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            data = res.ok ? await res.json() : null;
        } catch (error) {
            console.warn('Tutorial locate failed:', error);
        }
        if (seq !== locateSeq || runner.view !== 'lesson') return;
        if (!data || data.status !== 'success') {
            // Said once per lesson: without an answer the steps that need the
            // handle's place cannot pass, and silence would leave the person
            // waiting for a "Handle:" line that never comes.
            if (!locateFailureSaid) {
                locateFailureSaid = true;
                say('The tutorial cannot tell where the handle is on this server. Skip step moves on.');
            }
            return;
        }
        located = data;
        const pose = JSON.stringify([
            body.view, body.orientation, body.zoom, body.camera_center, body.depth, body.renderMode, body.mode,
        ]);
        if (pose !== lastLocatedPose) {
            lastLocatedPose = pose;
            const mark = data.landmarks && data.landmarks.handle;
            say(mark && mark.present !== false && mark.side
                ? `Handle: ${SIDE_WORDS[mark.side] || mark.side}${handleAgainstBody(mark, data.landmarks.body)}.`
                : 'Handle: not in this cut.');
        }
        refreshStepText();
        queueEvent({ type: 'locate', data: {} });
    }

    // -----------------------------------------------------------------------
    // Events from the viewer
    // -----------------------------------------------------------------------
    function queueEvent(ev) {
        ev.serial = runner.stepSerial;
        eventBatch.push(ev);
        if (eventBatch.length === 1) queueMicrotask(processEvents);
    }

    function processEvents() {
        const batch = eventBatch;
        eventBatch = [];
        for (const ev of batch) handleEvent(ev);
    }

    function handleEvent(ev) {
        // A dialog closing frees the announcement field; say what waited.
        if (ev.type === 'ui_action' && ev.data.action === 'close') flushSpeech();
        if (runner.view !== 'lesson') return;
        // A lesson waiting for a display starts as soon as one connects, from
        // the tutorial or from the main menu's Connect button.
        if (runner.phase === 'blocked' && ev.type === 'device' && ev.data.connected
                && !unmetRequirement(currentLesson().requires)) {
            enterLesson(runner.lessonIndex, { stepIndex: record.step, lead: ['Connected.'] });
            return;
        }
        if (ev.type === 'render') onRender(ev);
        if (runner.phase !== 'step' || ev.serial !== runner.stepSerial) return;
        evaluate(ev);
    }

    function onRender(ev) {
        if (announceNextMugRender && ev.data.model === practiceModel()) {
            announceNextMugRender = false;
            say('The practice mug is loaded.');
        }
        // Only while a step is waiting: after the lesson is done, "Handle: left"
        // on every render would talk over the next thing the person does.
        if (lessonUsesLocate(currentLesson()) && runner.phase === 'step') locateHandle();
    }

    /** Offer an event to the current step's check. Returns whether it passed. */
    function evaluate(ev) {
        if (runner.view !== 'lesson' || runner.phase !== 'step') return false;
        const step = currentStep();
        const check = currentCheck();
        const impl = CHECKS[check.type];
        if (!step || !impl) return false;
        let passed = false;
        if (impl.event) {
            try {
                passed = Boolean(impl.event(check, runner.checkState, checkContext(), ev));
            } catch (error) {
                console.warn('Tutorial check failed:', error);
            }
        }
        narrate(step, check);
        refreshStepText();
        if (passed) passStep();
        return passed;
    }

    /** Which narrate line applies now: the checked field's value (a render mode
     * in the cycle), or else the band the cut is in along the check's axis. */
    function narrationKey(step, check, state) {
        if (!step || !step.narrate) return null;
        if (check.field) return String(state[check.field]);
        const axis = check.axis || ((check.checks || []).find(sub => sub.axis) || {}).axis || state.cut_axis;
        if (state.cut_axis !== axis) return null;
        const found = bandAt(axis, Number(state.cut_percent));
        return found ? shortBandName(found.name) : null;
    }

    function narrate(step, check) {
        if (!step || !step.narrate) return;
        const key = narrationKey(step, check, getState());
        if (key === null || key === runner.lastNarrated) return;
        runner.lastNarrated = key;
        const line = step.narrate[key];
        if (line) say(resolveText(line));
    }

    study.onInteraction.push(function (eventType, eventData) {
        const data = eventData || {};
        if (eventType === 'announcement') {
            // The tutorial's own words come back through here too.
            if (speakingNow) return;
            noteViewerMessage(data);
            if (runner.view === 'lesson') setLast(data.message);
        }
        if (eventType === 'keyboard' && ['w', 'a', 's', 'd', 'f'].includes(String(data.key))) panMoved = true;
        if (eventType === 'keyboard' && data.key === '0') panMoved = false;
        if (runner.view === 'idle' && !(eventType === 'ui_action' && data.action === 'close')) return;
        queueEvent({ type: eventType, data });
    });

    // -----------------------------------------------------------------------
    // Key help mode
    // -----------------------------------------------------------------------
    const KEY_WORDS = {
        arrowup: 'Arrow Up', arrowdown: 'Arrow Down', pageup: 'Page Up', pagedown: 'Page Down',
        home: 'Home', end: 'End', escape: 'Escape', '.': 'Period', ',': 'Comma',
        '[': 'Left bracket', ']': 'Right bracket', '?': 'Question mark',
    };

    function describeCode(code) {
        const help = payload && payload.key_help;
        if (help && help[code]) return help[code];
        const name = KEY_WORDS[code] || String(code).toUpperCase();
        return `${name}: does nothing in the viewer.`;
    }

    function described(text) {
        setLast(text);
        say(text);
        queueEvent({ type: 'key_help', data: { action: 'described' } });
    }

    cadTutorial.describeKey = function (code, { inactive = false } = {}) {
        if (!cadTutorial.keyHelpActive) return;
        // Described like any other key, saying it does nothing just now (#245
        // review: with single-key shortcuts off, letters were never described).
        const note = inactive ? ' Single-key shortcuts are off in Settings, so it does nothing now.' : '';
        described(`${sentence(describeCode(String(code)))}${note}`);
    };

    function setKeyHelp(on, { silent = false } = {}) {
        if (cadTutorial.keyHelpActive === on) return;
        cadTutorial.keyHelpActive = on;
        for (const button of [el.keyHelpBtn, el.dialogKeyHelpBtn]) {
            if (button) button.setAttribute('aria-pressed', on ? 'true' : 'false');
        }
        queueEvent({ type: 'key_help', data: { action: on ? 'on' : 'off' } });
        if (silent) return;
        say(on
            ? 'Key help on. Press any viewer key, or a button on your display, to hear what it does. Nothing else happens until you press Key help again.'
            : 'Key help off. Keys work again.', on ? 'Key help on' : 'Key help off');
    }

    // Device presses: the test pattern's answers, and Key help's descriptions.
    cadTutorial.captureDeviceKey = function (cmd) {
        if (!cmd || !cadTutorial.running) return false;
        const name = String(cmd.name || 'other');
        if (cadTutorial.keyHelpActive) {
            const help = payload && payload.key_help;
            const key = `${cmd.device}:${name}`;
            described(help && help[key] ? help[key] : 'That button does nothing in the viewer.');
            return true;
        }
        // Dots 1 and 4 are the answers while the pattern is up, and do nothing
        // while the next one is on its way, rather than moving the cut.
        if (testPattern.active && (name === 'dot1' || name === 'dot4')) {
            if (testPattern.awaiting) answerTestPattern(name === 'dot1' ? 'left' : 'right');
            return true;
        }
        return false;
    };

    // -----------------------------------------------------------------------
    // Opening, the lesson list, exit and completion
    // -----------------------------------------------------------------------

    /** The person's model and view, when a run starts from the menu, so exit
     * can put them back. A first run keeps the mug instead. The orientation, the
     * cut on every axis and the pan come too: without them exit brought back the
     * model and the side but reset the other cuts and moved the view (#245
     * review). */
    function captureSaved() {
        const now = getState();
        record.saved = now.model ? {
            model: now.model,
            view: now.view,
            depth: now.depth,
            render_mode: now.render_mode,
            representation_mode: now.layout_mode,
            compose_scrollbar: now.compose_scrollbar,
            zoom: now.zoom,
            axis_mode: now.axis_mode,
            view_state: typeof study.captureView === 'function' ? study.captureView() : null,
        } : null;
    }

    /** A lesson opened from the menu or the dialog. */
    function startFromMenu(index, { how = 'start' } = {}) {
        if (!runner.runActive) {
            record.origin = 'help';
            captureSaved();
        }
        const lesson = lessons()[index];
        if (how === 'redo' && lesson) track('redo', lesson.id);
        if (how === 'resume' && lesson) track('resumed', lesson.id);
        const stepIndex = how === 'resume' && lesson && record.lesson === lesson.id ? record.step : null;
        enterLesson(index, { stepIndex, focus: true, lead: how === 'resume' ? ['Resumed.'] : [] });
    }

    function resumeIndex() {
        const index = record.lesson ? lessonIndexById(record.lesson) : -1;
        return index;
    }

    async function openMenu({ focus = true } = {}) {
        try {
            await ensurePayload();
        } catch (error) {
            console.warn('Tutorial lessons did not load:', error);
            say('The tutorial could not be loaded. Try the Tutorial button again in a moment.');
            return;
        }
        leaveStep();
        setKeyHelp(false, { silent: true });
        // No lesson is on, so nothing is locked: the model chooser, the upload
        // and the layout radios stayed disabled on the list (#245 review).
        setLocked(false);
        runner.view = 'menu';
        showRegion();
        renderMenu();
        if (focus) heading.focus();
    }

    function lessonStatus(lesson) {
        const saved = record.lessons[lesson.id];
        if (record.lesson === lesson.id && saved !== 'done' && (record.status === 'in-progress'
            || record.status === 'paused' || runner.runActive)) {
            return { text: 'In progress', button: route.resume || runner.runActive ? 'Resume' : 'Start' };
        }
        if (saved === 'done') return { text: 'Done', button: 'Redo' };
        const reason = unmetRequirement(lesson.requires);
        if (reason) return { text: `Cannot start yet: ${reason}`, button: 'Start' };
        if (saved === 'skipped') return { text: 'Skipped', button: 'Start' };
        return { text: 'Not started', button: 'Start' };
    }

    function renderMenu() {
        const all = lessons();
        const doneCount = all.filter(lesson => record.lessons[lesson.id] === 'done').length;
        keepingFocus(() => {
            el.lessonView.hidden = true;
            el.menuView.hidden = false;
            heading.textContent = `Tutorial lessons: ${doneCount} of ${all.length} done`;

            const index = resumeIndex();
            const canResume = index >= 0 && (route.resume || runner.runActive) && record.status !== 'completed';
            el.menuResume.hidden = !canResume;
            if (canResume) el.resumeBtn.textContent = `Resume lesson ${index + 1}, ${all[index].title}`;

            const groups = [];
            for (const part of PARTS) {
                const inPart = all.map((lesson, i) => ({ lesson, i })).filter(({ lesson }) => lesson.part === part.key);
                if (!inPart.length) continue;
                const title = document.createElement('h3');
                title.textContent = part.label;
                const list = document.createElement('ul');
                list.className = 'tutorial-lesson-list';
                for (const { lesson, i } of inPart) {
                    const status = lessonStatus(lesson);
                    const item = document.createElement('li');
                    const name = document.createElement('span');
                    name.className = 'tutorial-lesson-name';
                    name.textContent = `${i + 1}. ${lesson.title}`;
                    const state = document.createElement('span');
                    state.className = 'tutorial-lesson-status';
                    state.textContent = status.text;
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.textContent = status.button;
                    // The visible word first, so a voice command of "Start"
                    // still matches (WCAG 2.5.3), then which lesson it is.
                    button.setAttribute('aria-label', `${status.button} lesson ${i + 1}, ${lesson.title}`);
                    button.addEventListener('click', () => startFromMenu(i, {
                        how: status.button === 'Redo' ? 'redo' : status.button === 'Resume' ? 'resume' : 'start',
                    }));
                    item.append(name, ' ', state, ' ', button);
                    list.append(item);
                }
                groups.push(title, list);
            }
            el.menuGroups.replaceChildren(...groups);
        });
    }

    function startOver() {
        record.lessons = {};
        record.answers = {};
        runner.passed.clear();
        record.lesson = null;
        record.step = 0;
        if (record.status === 'completed') record.status = 'in-progress';
        writeRecord();
        track('redo', null);
        startFromMenu(0, { how: 'start' });
    }

    function restoreAfterRun() {
        setKeyHelp(false, { silent: true });
        endTestPattern();
        setLocked(false);
        restoreSections();
        const saved = record.origin === 'help' ? record.saved : null;
        const axisMode = touched.axisMode !== undefined ? (storedAxisMode() || touched.axisMode) : null;
        touched.axisMode = undefined;
        // A run from the menu gives back the model and view it found, but only
        // in place of the practice mug the tutorial put there. A model the
        // person chose during the run (the last lesson asks for exactly that)
        // is theirs now, and swapping it back would undo their own choice.
        // An upload is per tab and a reload deletes it, so a saved model the
        // server no longer lists cannot come back: loading it would put the
        // default model on the pins while saying "your model is back".
        const savedStillThere = Boolean(saved && saved.model) && [...(($('model-list-dropdown') || {}).options || [])]
            .some(option => option.value === saved.model);
        if (savedStillThere && getState().model === practiceModel()) {
            study.loadModel(saved.model, saved.model, {
                view: saved.view,
                depth: saved.depth,
                render_mode: saved.render_mode,
                representation_mode: saved.representation_mode,
                compose_scrollbar: saved.compose_scrollbar,
                zoom: saved.zoom,
                reset_pan: !saved.view_state,
                // With nothing of the tutorial's own to undo, the mode now is
                // the person's: the one they had, or the one lesson 8 asked
                // them to choose and saved.
                axis_mode: axisMode || getState().axis_mode,
                restore: saved.view_state || undefined,
            }, 'tutorial');
            return true;
        }
        if (axisMode && axisMode !== getState().axis_mode && typeof window.setAxisMode === 'function') {
            window.setPendingInputSource?.('tutorial');
            window.setAxisMode(axisMode, { announce: false, persist: false });
        }
        return false;
    }

    function closeRegion() {
        // Every way out turns Key help off: once the region is gone there is
        // no toggle left, and every viewer key would stay silent.
        setKeyHelp(false, { silent: true });
        runner.view = 'idle';
        runner.runActive = false;
        hideRegion();
        if (navButton && !navButton.hidden) navButton.focus();
    }

    function exitTutorial() {
        const wasRunning = runner.runActive;
        const lesson = wasRunning ? currentLesson() : null;
        leaveStep();
        const restored = wasRunning ? restoreAfterRun() : false;
        if (wasRunning) {
            if (record.status !== 'completed') record.status = 'paused';
            record.saved = null;
            writeRecord();
            track('exited', lesson ? lesson.id : null);
        }
        closeRegion();
        if (lesson) {
            const canResume = route.resume && record.status !== 'completed';
            say(`Tutorial paused at lesson ${runner.lessonIndex + 1}, ${lesson.title}.${restored ? ' Your model and view are back.' : ''} `
                + (canResume ? 'Resume any time from the Tutorial button.' : 'Open it again from the Tutorial button.'),
            'Tutorial paused');
        } else {
            say('Tutorial closed. It is always in the Tutorial button.', 'Tutorial closed');
        }
    }

    function completeTutorial() {
        leaveStep();
        const restored = restoreAfterRun();
        record.status = 'completed';
        record.version = payload && typeof payload.version === 'number' ? payload.version : record.version;
        record.saved = null;
        writeRecord();
        track('completed', null);
        closeRegion();
        say(`That is the whole tutorial.${restored ? ' Your model and view are back.' : ''} `
            + 'Any lesson can be redone from the Tutorial button.', 'Tutorial complete');
    }

    // -----------------------------------------------------------------------
    // Starting by itself: after the page is set up and the consent dialog has
    // settled, since the dialog is modal and the page's own set-up would
    // overwrite the lesson's view.
    // -----------------------------------------------------------------------
    let viewerReady = false;
    let inViewerReady = false;
    let mugRenderedFirst = false;
    const whenViewerReady = [];

    function releaseFirstRender() {
        if (!cadTutorial.ownsFirstRender) return;
        cadTutorial.ownsFirstRender = false;
        // The mug already went out as the first render: nothing to hand back.
        if (mugRenderedFirst) return;
        // During the viewer's own dispatch its next line renders; before it, it
        // renders when it gets there. Only after it is the render ours to send.
        if (viewerReady && !inViewerReady) {
            window.setPendingInputSource?.('init');
            if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
        }
    }

    /** On a visit the tutorial may open on, the practice mug is the page's first
     * render, straight away (#245 review). It used to wait for the consent dialog
     * and the lessons, so until someone answered the dialog the display stayed
     * blank and the status bar named a model that was not on it. Nothing is said
     * about it, so the consent dialog's own confirmation is not talked over: the
     * mug is simply what is there, whether or not the tutorial then starts. */
    function renderMugFirst() {
        applyPose(DEFAULT_POSE, { force: true });
        announceNextMugRender = false;
        mugRenderedFirst = true;
    }

    document.addEventListener('cad:viewer-ready', function () {
        viewerReady = true;
        inViewerReady = true;
        try {
            // ownsFirstRender stays set through the dispatch, so the viewer's
            // next line skips its own render of the default model.
            if (cadTutorial.ownsFirstRender) renderMugFirst();
            whenViewerReady.splice(0).forEach(run => run());
        } finally {
            inViewerReady = false;
        }
    });

    function afterViewerReady(run) {
        if (viewerReady) run();
        else whenViewerReady.push(run);
    }

    let consentSeen = false;
    document.addEventListener('cad:consent-settled', function (event) {
        if (consentSeen) return;
        consentSeen = true;
        const detail = (event && event.detail) || {};
        consentGiven = detail.consent_given === true;
        onConsentSettled(detail);
    });

    function onConsentSettled(detail) {
        if (route.offered && !forcedStart && route.autostart && recordWasNew && detail.reason === 'decided') {
            // Consent was decided on an earlier visit, so this is someone who
            // has used the viewer before this tutorial existed: tell them once,
            // and never open it on them. Checked before ?tutorial=off, or the
            // "pending" written at load would open it on their next visit.
            record.status = 'offered';
            writeRecord();
            releaseFirstRender();
            if (!suppressed) say('New: a tutorial is available from the Tutorial button.');
            return;
        }
        if (!route.offered || suppressed) {
            releaseFirstRender();
            return;
        }
        if (forcedStart) {
            afterViewerReady(() => startAtLoad(record.lesson && record.status !== 'completed' ? 'resume' : 'fresh'));
            return;
        }
        if (mayAutostart) {
            afterViewerReady(() => startAtLoad(record.status === 'in-progress' ? 'resume' : 'fresh'));
            return;
        }
        releaseFirstRender();
        if (route.autostart && record.status === 'completed') afterViewerReady(offerNewLessons);
    }

    /** Someone who finished hears once about lessons added since. */
    async function offerNewLessons() {
        try {
            await ensurePayload();
        } catch (_) {
            return;
        }
        if (!(Number(payload.version) > Number(record.version || 0))) return;
        const fresh = lessons().map((lesson, i) => ({ lesson, i })).filter(({ lesson }) => !record.lessons[lesson.id]);
        record.version = Number(payload.version);
        writeRecord();
        if (!fresh.length) return;
        const names = fresh.map(({ lesson, i }) => `lesson ${i + 1}, ${lesson.title}`).join('; ');
        say(`New in the tutorial: ${names}. Open it from the Tutorial button.`);
    }

    async function startAtLoad(how) {
        try {
            await ensurePayload();
        } catch (error) {
            // Never at the cost of the viewer: it renders as it would have.
            console.warn('Tutorial did not start:', error);
            releaseFirstRender();
            return;
        }
        if (runner.view !== 'idle') {
            releaseFirstRender();
            return;
        }
        if (!record.origin || how === 'fresh') record.origin = window.CAD_DEMO_MODE ? 'help' : 'first-run';
        if (record.origin === 'help' && !record.saved) captureSaved();
        let index = how === 'resume' ? resumeIndex() : 0;
        if (index < 0) index = 0;
        const lesson = lessons()[index];
        track(how === 'resume' ? 'resumed' : 'loaded', lesson ? lesson.id : null);
        if (how === 'resume') {
            enterLesson(index, { stepIndex: record.step, silent: true });
            // Focus on the heading reads the lesson number and title, so this
            // says only what is new: it is a return, and what they can do. A
            // lesson that cannot start says so, since Continue would skip it.
            say(runner.phase === 'blocked'
                ? `Welcome back to the tutorial. This lesson cannot start yet: ${runner.blockedReason}. `
                    + 'Next skips it, and Back goes to the lesson before.'
                : 'Welcome back to the tutorial. Next carries on where you stopped, or exit the tutorial.', 'Welcome back');
        } else {
            enterLesson(index, { silent: true });
        }
        releaseFirstRender();
        heading.focus();
    }

    // -----------------------------------------------------------------------
    // Wiring
    // -----------------------------------------------------------------------
    el.continueBtn.addEventListener('click', continueAction);
    el.backBtn.addEventListener('click', back);
    el.repeatBtn.addEventListener('click', repeat);
    el.hintBtn.addEventListener('click', hint);
    el.showMeBtn.addEventListener('click', showMe);
    el.restartBtn.addEventListener('click', restartLesson);
    el.skipBtn.addEventListener('click', skipStep);
    el.skipLessonBtn.addEventListener('click', () => skipLesson({ focus: true }));
    el.lessonsBtn.addEventListener('click', () => openMenu({ focus: true }));
    el.exitBtn.addEventListener('click', exitTutorial);
    el.menuExitBtn.addEventListener('click', exitTutorial);
    el.keyHelpBtn.addEventListener('click', () => setKeyHelp(!cadTutorial.keyHelpActive));
    el.keysBtn.addEventListener('click', () => {
        const open = el.keysHelp.hidden;
        el.keysHelp.hidden = !open;
        el.keysBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    // Inside the click, so the browser's device chooser counts it as the
    // person's own gesture.
    el.connectMonarchBtn.addEventListener('click', () => {
        if (typeof window.connectMonarchHid === 'function') window.connectMonarchHid();
        else say('Connecting a Monarch needs Chrome or Edge, which have Web HID.');
    });
    el.connectDotpadBtn.addEventListener('click', () => {
        if (typeof window.connectDotpad === 'function') window.connectDotpad();
        else say('Connecting a DotPad needs Chrome or Edge, which have Web Bluetooth.');
    });
    el.resumeBtn.addEventListener('click', () => {
        const index = resumeIndex();
        if (index >= 0) startFromMenu(index, { how: 'resume' });
    });
    el.startOverBtn.addEventListener('click', startOver);

    if (navButton) {
        navButton.addEventListener('click', () => {
            if (!route.offered) return;
            openMenu({ focus: true });
        });
    }

    /** A button in the shortcuts dialog: close the dialog, then act once it has
     * closed and returned focus, so what it does has the last word on focus. */
    function afterShortcutsDialog(action) {
        const dialog = $('shortcuts-dialog');
        if (!dialog || !dialog.open) {
            action();
            return;
        }
        dialog.addEventListener('close', () => action(), { once: true });
        if (typeof window.closeShortcutsDialog === 'function') window.closeShortcutsDialog();
        else dialog.close();
    }

    if (el.dialogResumeBtn) {
        el.dialogResumeBtn.addEventListener('click', () => afterShortcutsDialog(async () => {
            try {
                await ensurePayload();
            } catch (error) {
                console.warn('Tutorial lessons did not load:', error);
                return;
            }
            const index = resumeIndex();
            if (index >= 0) startFromMenu(index, { how: 'resume' });
            else openMenu({ focus: true });
        }));
    }
    if (el.dialogStartOverBtn) {
        el.dialogStartOverBtn.addEventListener('click', () => afterShortcutsDialog(async () => {
            try {
                await ensurePayload();
            } catch (error) {
                console.warn('Tutorial lessons did not load:', error);
                return;
            }
            startOver();
        }));
    }
    if (el.dialogLessonsBtn) {
        el.dialogLessonsBtn.addEventListener('click', () => afterShortcutsDialog(() => openMenu({ focus: true })));
    }
    if (el.dialogKeyHelpBtn) {
        el.dialogKeyHelpBtn.addEventListener('click', () => afterShortcutsDialog(() => {
            if (cadTutorial.running) setKeyHelp(!cadTutorial.keyHelpActive);
        }));
    }
    syncDialogSection();

    // N, B and C while a lesson is showing. Not in any form control but a
    // button: a focused list takes letters to jump to an option, and the last lesson
    // asks for the model list, where N, B and C used to go to the tutorial
    // instead (#245 review). Not under a dialog, not with Ctrl, Alt or Cmd, and
    // not when single-key shortcuts are off, as for the viewer's own keys
    // (viewer.js). The buttons always work, and there is no Escape binding:
    // screen readers take Escape.
    document.addEventListener('keydown', function (e) {
        if (runner.view !== 'lesson') return;
        const target = e.target;
        const tagName = target && target.tagName ? target.tagName.toLowerCase() : '';
        const inputType = tagName === 'input' ? String(target.type || '').toLowerCase() : '';
        const formControl = Boolean(target && (target.isContentEditable
            || tagName === 'textarea' || tagName === 'select'
            || (tagName === 'input' && !['button', 'submit', 'reset'].includes(inputType))
            || (typeof target.closest === 'function' && target.closest('[role="listbox"], [role="combobox"]'))));
        if (formControl) return;
        if (document.querySelector('dialog[open]')) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const key = String(e.key || '').toLowerCase();
        if (key !== 'n' && key !== 'b' && key !== 'c') return;
        // Key help describes them even with single-key shortcuts off, as the
        // viewer does its own keys (#245 review), once per press.
        if (cadTutorial.keyHelpActive) {
            e.preventDefault();
            if (!e.repeat) cadTutorial.describeKey(key, { inactive: getState().single_key_shortcuts === false });
            return;
        }
        if (getState().single_key_shortcuts === false) return;
        e.preventDefault();
        if (e.repeat) return;
        if (key === 'n') continueAction();
        else if (key === 'b') back();
        else repeat();
    });
})();
