/**
 * Study session driver for the participant's view (/study).
 *
 * Inert on every other page. On /study it does three things:
 *
 *   1. Keeps the study region in sync with the experimenter, over the
 *      Server-Sent Events channel, and loads the model each protocol step calls
 *      for.
 *   2. Sends the "I am ready to move on" signal. Advisory only -- it lights up
 *      in the experimenter's panel and does not advance the session.
 *   3. Reports interactions to the study log, with the viewer state attached to
 *      each one.
 *
 * It never learns the name of the model on the display. The server sends an
 * index into its model list plus a neutral label, so there is nothing here for a
 * screen reader to read out that would answer the question the participant is
 * being asked. See app/study.py.
 */
(function () {
    'use strict';

    if (!window.cadStudy || !window.cadStudy.isStudyMode()) return;

    const region = document.getElementById('study-region');
    const heading = document.getElementById('study-step-heading');
    const progressText = document.getElementById('study-step-progress-text');
    const stepText = document.getElementById('study-step-text');
    const readyBtn = document.getElementById('study-ready-btn');
    const readyStatus = document.getElementById('study-ready-status');
    const joinForm = document.getElementById('study-join-form');
    const joinInput = document.getElementById('study-join-code');
    const joinError = document.getElementById('study-join-error');

    if (region) region.hidden = false;
    // The N/B/C section of the Keyboard Shortcuts dialog (H or ?) -- otherwise
    // that dialog only lists the ordinary viewer shortcuts, and a participant
    // has no way to discover the study commands at all.
    const studyShortcuts = document.getElementById('study-shortcuts-section');
    if (studyShortcuts) studyShortcuts.hidden = false;

    // Identifies this browser in the log. Sessions on a public deployment can in
    // principle pick up a stray visitor at /study; tagging every event means
    // their activity is separable in analysis rather than silently mixed in, and
    // the experimenter panel shows how many views are attached.
    const CLIENT_ID_KEY = 'cadA11yStudyClientId';
    let clientId = null;
    try {
        clientId = sessionStorage.getItem(CLIENT_ID_KEY);
        if (!clientId) {
            clientId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
            sessionStorage.setItem(CLIENT_ID_KEY, clientId);
        }
    } catch (_) {
        clientId = String(Date.now());
    }

    let currentStepId = null;
    let currentModelStem = null;
    let sessionActive = false;
    // The last state applied, so the repeat command (C) can re-announce the
    // current step without keeping its own separate copy of the same fields.
    let lastState = null;

    // Whether the step change about to arrive over SSE is a consequence of a
    // request this browser just made (N or B), as opposed to the experimenter
    // changing it from the panel. There is no id to correlate a broadcast with
    // the request that caused it, so this is a short-lived guess instead: set
    // just before sending the request, and it expires on its own if nothing
    // moved (the paired-mode ready signal is advisory and never does).
    let expectingOwnStepChange = false;
    let expectingOwnStepChangeTimer = null;

    function expectOwnStepChange() {
        expectingOwnStepChange = true;
        if (expectingOwnStepChangeTimer) clearTimeout(expectingOwnStepChangeTimer);
        expectingOwnStepChangeTimer = setTimeout(function () {
            expectingOwnStepChange = false;
        }, 4000);
    }

    /** Consume the flag: true at most once per request that set it. */
    function consumeExpectingOwnStepChange() {
        const value = expectingOwnStepChange;
        expectingOwnStepChange = false;
        if (expectingOwnStepChangeTimer) {
            clearTimeout(expectingOwnStepChangeTimer);
            expectingOwnStepChangeTimer = null;
        }
        return value;
    }

    // How this session is being run, remembered across reloads. In 'solo' the
    // experimenter and the participant share this laptop, so there is no second
    // person at another screen to refer them to -- "your experimenter will give
    // you a code" is nonsense said to the person holding the computer. Kept
    // client-side as well as on the session so the wording is still right on a
    // page that cannot reach its session to ask.
    const MODE_STORAGE = 'cadA11yStudyMode';
    let sessionMode = (function () {
        try { return sessionStorage.getItem(MODE_STORAGE) || 'paired'; } catch (_) { return 'paired'; }
    })();
    const isSolo = () => sessionMode === 'solo';

    function rememberMode(mode) {
        if (!mode || mode === sessionMode) return;
        sessionMode = mode;
        try { sessionStorage.setItem(MODE_STORAGE, mode); } catch (_) {}
    }

    let participantCode = '';

    // Which session this browser belongs to. Several can run at once on one
    // deployment, so a plain /study is only unambiguous while exactly one is
    // active; the key in the link is what makes it certain. Kept in
    // sessionStorage so a reload, or the participant's screen reader restarting
    // the page, does not lose it.
    const KEY_STORAGE = 'cadA11yStudyKey';
    let participantKey = (function () {
        const fromUrl = (new URLSearchParams(location.search).get('s') || '').trim().toUpperCase();
        try {
            if (fromUrl) {
                sessionStorage.setItem(KEY_STORAGE, fromUrl);
                return fromUrl;
            }
            return sessionStorage.getItem(KEY_STORAGE) || '';
        } catch (_) {
            return fromUrl;
        }
    })();

    /** Remember the code this browser joined with, so a reload does not ask for
     * it again and a page whose session has ended is refused rather than being
     * re-homed onto whatever is running now. */
    function rememberKey(key) {
        if (!key) return;
        participantKey = String(key).trim().toUpperCase();
        try {
            sessionStorage.setItem(KEY_STORAGE, participantKey);
        } catch (_) { /* still bound for this page's lifetime */ }
    }

    function forgetKey() {
        participantKey = '';
        try { sessionStorage.removeItem(KEY_STORAGE); } catch (_) {}
    }

    /** Show or hide the code prompt, and keep it out of the tab order once the
     * participant is in a session. */
    function showJoinPrompt(show) {
        if (!joinForm) return;
        joinForm.hidden = !show;
        if (!show && joinError) { joinError.hidden = true; joinError.textContent = ''; }
    }

    if (joinForm) {
        joinForm.addEventListener('submit', function (event) {
            event.preventDefault();
            const typed = (joinInput.value || '').trim().toUpperCase();
            if (!typed) return;
            if (joinError) { joinError.hidden = true; joinError.textContent = ''; }
            rememberKey(typed);
            fetch(withKey('/study/state'))
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (state) {
                    if (state && state.active) {
                        applyState(state);
                        // Reconnect the stream, which subscribed without a code.
                        if (eventSource) { eventSource.close(); eventSource = null; }
                        connect();
                        return;
                    }
                    forgetKey();
                    if (joinError) {
                        joinError.hidden = false;
                        joinError.textContent =
                            'That code did not match a session that is running. '
                            + 'Check it with your experimenter and try again.';
                    }
                    joinInput.focus();
                })
                .catch(function () {
                    forgetKey();
                    if (joinError) {
                        joinError.hidden = false;
                        joinError.textContent = 'Could not reach the server. Please try again.';
                    }
                });
        });
    }

    /** Append the session key to a study URL, when this browser has one. */
    function withKey(path) {
        if (!participantKey) return path;
        return path + (path.includes('?') ? '&' : '?') + 's=' + encodeURIComponent(participantKey);
    }

    // -----------------------------------------------------------------------
    // Reporting
    // -----------------------------------------------------------------------

    /** Fire and forget. A logging request must never block or fail a keypress:
     * the participant is mid-exploration and a stalled fetch would be felt. */
    function report(eventType, eventData, viewerState) {
        if (!sessionActive) return;
        try {
            fetch(withKey('/study/event'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    event_type: eventType,
                    event_data: eventData || {},
                    viewer_state: viewerState || window.cadStudy.snapshot(),
                    client_id: clientId,
                    client_time: new Date().toISOString(),
                    participant_key: participantKey || undefined,
                }),
                keepalive: true,
            }).catch(function () {});
        } catch (_) { /* never surfaces to the participant */ }
    }

    window.cadStudy.onInteraction.push(report);

    // -----------------------------------------------------------------------
    // Step rendering
    // -----------------------------------------------------------------------

    /** Title, step number and content as one utterance, so a screen reader
     * reads them together rather than as separately-timed live regions (which
     * is what made the announcement inconsistent: the heading, the step
     * counter and the step text used to update independently, and the step
     * text was not a live region at all, so it was never read out). */
    function stepAnnouncement(state) {
        const stepNumber = `Step ${(state.step_index || 0) + 1} of ${state.step_count || 1}`;
        const title = state.title || 'Study step';
        const text = state.text || '';
        return text ? `${stepNumber}: ${text}` : `${stepNumber}: ${title}.`;
    }

    /** Step number and title only -- no content. Used when the experimenter
     * changed the step, not the participant: they did not ask for this, so the
     * full instructions would be a lot to have read out over whatever they
     * were already doing. Press C for the rest. */
    function briefStepAnnouncement(state) {
        const stepNumber = `Step ${(state.step_index || 0) + 1} of ${state.step_count || 1}`;
        return `${stepNumber}: ${state.title || 'Study step'}.`;
    }

    function applyState(state) {
        if (!state) return;
        if (state.active) lastState = state;

        rememberKey(state.participant_key);
        rememberMode(state.mode);
        if (state.participant_code) participantCode = state.participant_code;

        const wasActive = sessionActive;
        sessionActive = Boolean(state.active);
        window.cadStudy.setSessionId(sessionActive ? state.study_session_id : null);

        if (!sessionActive) {
            currentStepId = null;
            currentModelStem = null;
            const finished = state.status === 'completed' || state.status === 'abandoned';
            if (finished) {
                setHeading('The session has ended');
                if (progressText) progressText.textContent = 'Finished';
                if (stepText) {
                    // On one machine the experimenter is reading this too, and
                    // what they need to know is that it is safely recorded and
                    // they can stop. On two, this is the participant's screen
                    // and the experimenter has their own.
                    stepText.textContent = isSolo()
                        ? 'The session is complete and everything has been recorded. '
                          + 'You can close this window.'
                        : 'That is everything. Thank you.';
                }
                showJoinPrompt(false);
            } else if (state.unknown_code) {
                setHeading('That session code was not recognised');
                if (progressText) progressText.textContent = 'Not connected';
                if (stepText) {
                    stepText.textContent = isSolo()
                        ? 'This session is no longer running. Open the control panel '
                          + 'and start a new one.'
                        : 'Check the code with your experimenter and enter it again.';
                }
                showJoinPrompt(!isSolo());
            } else {
                setHeading('Enter your session code');
                if (progressText) progressText.textContent = 'Not connected';
                if (stepText) {
                    stepText.textContent = isSolo()
                        ? 'This window is not attached to a session. Open the control '
                          + 'panel and use "Run the study on this device".'
                        : 'Your experimenter will give you a four-character code. '
                          + 'Enter it below to join your session.';
                }
                showJoinPrompt(!isSolo());
            }
            if (readyBtn) readyBtn.disabled = true;
            return;
        }

        showJoinPrompt(false);

        if (!wasActive) {
            report('page_load', { user_agent: navigator.userAgent, url: location.href });
        }

        const stepChanged = state.step_id !== currentStepId;
        currentStepId = state.step_id;

        if (progressText) {
            progressText.textContent =
                `Step ${(state.step_index || 0) + 1} of ${state.step_count || 1}`;
        }
        if (stepText) stepText.textContent = state.text || '';
        if (readyBtn) readyBtn.disabled = false;
        if (readyStatus && stepChanged) readyStatus.textContent = '';

        // Load before moving focus, so the display is already being redrawn while
        // the screen reader reads the new step.
        const model = state.model;
        if (model && (stepChanged || model.stem !== currentModelStem)) {
            currentModelStem = model.stem;
            const loaded = window.cadStudy.loadModel(
                model.stem, model.label, state.viewer_defaults
            );
            if (loaded) {
                report('model_loaded', { label: model.label, step_id: state.step_id });
            }
        }

        if (stepChanged) {
            // Announce, but do not steal focus.
            //
            // The announcement below is what reads the new step out wherever
            // the participant happens to be. Moving focus as well would take
            // them out of the depth slider they were exploring with, and they
            // would have to find their way back -- and it is the experimenter
            // who advanced the step, not them. Focus belongs to whoever
            // caused the change, and here that is not the person whose hands
            // are on the display.
            //
            // This also used to be wrapped in requestAnimationFrame, which does
            // not run in a background tab: the move silently did not happen, and
            // then fired the moment the window came forward.
            setHeading(state.title || 'Study step');
            // The heading/counter/text above are plain text now (#180): they
            // used to each be their own live region, which read the title,
            // then the step count, then this announcement repeating both plus
            // the instructions -- the same content three times over. This is
            // the one thing actually read out, as a single utterance. Full
            // title-and-content on first joining a session or when this
            // browser just asked to move on (N/B) -- both are the
            // participant's own action, and they need the content
            // immediately. Step-and-title only when the experimenter changed
            // it: that was not asked for, so the full instructions would be
            // unwelcome on top of whatever the participant was doing. Press C
            // for the full announcement either way.
            const ownStepChange = consumeExpectingOwnStepChange();
            if (window.cadStudy.announcePolite) {
                window.cadStudy.announcePolite(stepAnnouncement(state));
            }
        }
    }

    /** Rewrite the live heading only when the text actually changes.
     * Assigning identical text to a live region is a no-op in most screen
     * readers, and rewriting it on every broadcast would re-announce the step
     * each time an unrelated field updated. */
    function setHeading(text) {
        if (!heading || heading.textContent === text) return;
        heading.textContent = text;
    }

    // -----------------------------------------------------------------------
    // Ready / Back / Repeat -- available from the button (Ready only) and from
    // the keyboard (N / B / C), so a participant never has to find a control
    // with a screen reader mid-exploration to do any of these.
    // -----------------------------------------------------------------------

    /** "I am ready to move on." Advisory in a paired session -- it notifies the
     * experimenter and does not move anything -- and the actual advance signal
     * in a solo one, where there is no experimenter to notify. Same request
     * either way; the server decides which it is. */
    function signalReady() {
        if (!sessionActive || (readyBtn && readyBtn.disabled)) return;
        if (readyBtn) readyBtn.disabled = true;
        // Only actually moves the step in a solo session; in a paired one this
        // is advisory and the flag simply expires unused a few seconds from now.
        expectOwnStepChange();
        fetch(withKey('/study/step/ready'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                client_id: clientId,
                client_time: new Date().toISOString(),
                viewer_state: window.cadStudy.snapshot(),
                participant_key: participantKey || undefined,
            }),
        }).then(function (res) {
            return res.ok ? res.json().catch(function () { return {}; }) : null;
        }).then(function (body) {
            // Say what actually happened. The message used to be the same
            // either way, so in a single-device session -- where the button
            // does move the session on -- it told the participant to keep
            // exploring until an experimenter moved them, and on the last
            // step it said that while nothing at all had happened.
            if (!readyStatus) return;
            if (!body) {
                readyStatus.textContent =
                    'Could not send that. Please tell your experimenter you are ready.';
            } else if (body.finished) {
                readyStatus.textContent = participantCode
                    ? `Session ${participantCode} is complete and has been recorded.`
                    : 'The session is complete and has been recorded.';
            } else if (body.advanced) {
                // The new step is already announced by applyState (stepAnnouncement);
                // a second message here would be read out on top of it.
                readyStatus.textContent = '';
            } else {
                readyStatus.textContent =
                    'Your experimenter has been told you are ready. '
                    + 'Keep exploring until they move on.';
            }
        }).catch(function () {
            if (readyStatus) {
                readyStatus.textContent = 'Could not send that. Please tell your experimenter you are ready.';
            }
        }).finally(function () {
            // Re-enabled so a participant can signal again if the experimenter
            // did not notice the first time.
            readyBtn.disabled = !sessionActive;
        });
    }

    if (readyBtn) readyBtn.addEventListener('click', signalReady);

    /** Go back a step. Solo sessions only -- in a paired session the
     * experimenter paces the protocol, and the server refuses this request
     * outright rather than the client deciding not to send it. */
    function goBack() {
        if (!sessionActive) return;
        if (!isSolo()) {
            window.cadStudy.announcePolite('Only your experimenter can move back a step.');
            return;
        }
        expectOwnStepChange();
        fetch(withKey('/study/step/back'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                client_id: clientId,
                client_time: new Date().toISOString(),
                participant_key: participantKey || undefined,
            }),
        }).catch(function () {
            window.cadStudy.announcePolite('Could not go back. Please try again.');
        });
    }

    /** Re-announce the current step. Read-only: nothing here changes session
     * state, so it is always available regardless of mode. */
    function repeatStep() {
        if (!sessionActive || !lastState) return;
        window.cadStudy.announcePolite(stepAnnouncement(lastState));
    }

    document.addEventListener('keydown', function (e) {
        if (!sessionActive) return;

        const target = e.target;
        const tagName = target && target.tagName ? target.tagName.toLowerCase() : '';
        const isTextEntryTarget = Boolean(
            target && (target.isContentEditable || tagName === 'textarea' || tagName === 'input')
        );
        if (isTextEntryTarget) return;

        // A modal dialog (e.g. the session consent dialog) makes the rest of
        // the page inert -- same guard as viewer.js's own shortcut handler.
        if (document.querySelector('dialog[open]')) return;

        // Leave browser/app shortcuts untouched (Cmd/Ctrl/Alt combos).
        if (e.metaKey || e.ctrlKey || e.altKey) return;

        const key = String(e.key || '').toLowerCase();
        if (key === 'n') {
            e.preventDefault();
            signalReady();
        } else if (key === 'b') {
            e.preventDefault();
            goBack();
        } else if (key === 'p') {
            e.preventDefault();
            goBack();
        } else if (key === 'c') {
            e.preventDefault();
            repeatStep();
        }
    });

    // -----------------------------------------------------------------------
    // Sync
    // -----------------------------------------------------------------------

    let eventSource = null;
    let reconnectDelay = 1000;

    function connect() {
        try {
            eventSource = new EventSource(withKey('/study/stream'));
        } catch (_) {
            scheduleReconnect();
            return;
        }
        eventSource.onmessage = function (event) {
            reconnectDelay = 1000;
            try {
                applyState(JSON.parse(event.data));
            } catch (error) {
                console.warn('Study state parse failed:', error);
            }
        };
        eventSource.onerror = function () {
            // EventSource reconnects on its own, but not after the server closes
            // the stream outright. Closing and rebuilding covers both, with a
            // backoff so a server restart mid-session does not become a request
            // storm.
            if (eventSource) eventSource.close();
            eventSource = null;
            scheduleReconnect();
        };
    }

    function scheduleReconnect() {
        setTimeout(function () {
            refreshOnce();
            connect();
        }, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    }

    /** One-shot state fetch. Covers the gap before the stream is up, and the
     * case where a proxy refuses to hold an SSE connection at all. */
    function refreshOnce() {
        fetch(withKey('/study/state'))
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(applyState)
            .catch(function () {});
    }

    window.addEventListener('pagehide', function () {
        report('page_unload', {});
    });

    refreshOnce();
    connect();
})();
