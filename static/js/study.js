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

    function applyState(state) {
        if (!state) return;

        rememberKey(state.participant_key);

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
                if (stepText) stepText.textContent = 'That is everything. Thank you.';
                showJoinPrompt(false);
            } else {
                setHeading('Enter your session code');
                if (progressText) progressText.textContent = 'Not connected';
                if (stepText) {
                    stepText.textContent =
                        'Your experimenter will give you a four-character code. '
                        + 'Enter it below to join your session.';
                }
                showJoinPrompt(true);
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
            setHeading(state.title || 'Study step');
            // Focus follows the step. Without it a participant who was in the
            // depth slider stays there and never hears the new instruction; the
            // heading is tabindex="-1" precisely so it can receive focus without
            // joining the tab order.
            if (heading) {
                requestAnimationFrame(function () {
                    try { heading.focus({ preventScroll: true }); } catch (_) { heading.focus(); }
                });
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
    // Ready button
    // -----------------------------------------------------------------------

    if (readyBtn) {
        readyBtn.addEventListener('click', function () {
            if (!sessionActive) return;
            readyBtn.disabled = true;
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
                    readyStatus.textContent = 'That is the end of the session. Thank you.';
                } else if (body.advanced) {
                    // The heading changes and focus moves to it, which is the
                    // confirmation; a second message would be read out on top.
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
        });
    }

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
