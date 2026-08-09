/**
 * Experimenter control panel for a study session.
 *
 * Drives the session: enrolls the participant, shows the script for the current
 * step, and moves through the protocol. The participant's view follows over the
 * same Server-Sent Events stream, so the two stay in step across two machines.
 *
 * The home screen is deliberately just an identity summary (dl.session-summary).
 * Everything else is a keyboard command: C shows the current step, S shows
 * strategy prompts, J jumps to a step, H or ? lists every command, and N/B/R/E
 * move through or end the session directly, without opening anything. Each
 * dialog follows the same accessible pattern as the participant viewer's Help/
 * About/Settings dialogs (native <dialog> + showModal(), heading focus on
 * open, Esc/backdrop to close) via makeInfoDialogController below.
 *
 * Accessibility is not optional here. Experimenters on this project include
 * screen reader users, so every change of state reaches a live region, the
 * participant's readiness signal is announced rather than only lit up, and a
 * degraded log says so in words.
 */
(function () {
    'use strict';

    // The panel is open unless the deployment sets STUDY_CONTROL_TOKEN. When it
    // does, the token rides in the URL; when it does not -- the default -- there
    // is nothing to find and nothing to paste.
    const TOKEN = new URLSearchParams(location.search).get('token') || '';

    const el = (id) => document.getElementById(id);

    const alertRegion = el('alert-region');
    const statusRegion = el('status-region');
    const startingSection = el('starting-section');
    const sessionSection = el('session-section');
    const mainContent = el('main-content');

    let config = null;
    let state = null;
    let lastStepId = null;
    let lastReadyAt = null;
    let lastLoggingSignature = null;
    let lastClientCount = null;

    // Which session this panel is driving. Several can run at once on one
    // deployment, so every request says which one it means rather than relying
    // on there being a single "active" session -- which is what used to make a
    // second experimenter's panel silently take over the first's session.
    // Remembered per browser tab so a reload stays on the same session.
    const SESSION_STORAGE = 'cadA11yStudyPanelSession';
    let boundSessionId = (function () {
        try {
            const stored = sessionStorage.getItem(SESSION_STORAGE);
            return stored ? Number(stored) : null;
        } catch (_) {
            return null;
        }
    })();

    function bindSession(id) {
        boundSessionId = id ? Number(id) : null;
        try {
            if (boundSessionId) sessionStorage.setItem(SESSION_STORAGE, String(boundSessionId));
            else sessionStorage.removeItem(SESSION_STORAGE);
        } catch (_) { /* a panel that cannot remember still works for this load */ }
    }

    /** Add the bound session to a URL, so the server never has to guess. */
    function scoped(path) {
        if (!boundSessionId) return path;
        return path + (path.includes('?') ? '&' : '?') + 'study_session_id=' + boundSessionId;
    }

    // -----------------------------------------------------------------------
    // Plumbing
    // -----------------------------------------------------------------------

    function api(path, options) {
        const opts = Object.assign({ headers: {} }, options || {});
        opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers);
        if (TOKEN) opts.headers['X-Study-Token'] = TOKEN;
        if (boundSessionId) opts.headers['X-Study-Session'] = String(boundSessionId);
        return fetch(path, opts).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (body) {
                if (!res.ok) throw new Error(body.message || `Request failed (${res.status})`);
                return body;
            });
        });
    }

    function announce(message) {
        if (alertRegion) alertRegion.textContent = message;
    }

    function status(message) {
        if (statusRegion) statusRegion.textContent = message;
    }

    function setList(node, items) {
        if (!node) return;
        node.innerHTML = '';
        (items || []).forEach(function (item) {
            const li = document.createElement('li');
            li.textContent = item;
            node.appendChild(li);
        });
    }

    // What each block kind is called on screen. The label is read out before the
    // text, so an experimenter using a screen reader hears whether to perform the
    // next sentence or speak it, rather than having to infer it.
    const BLOCK_LABELS = {
        say: 'Say',
        do: 'Do',
        ask: 'Ask',
        note: 'Note',
    };

    /** Render a step's script as a definition list: the kind as the term, the
     * text as the definition, with any question set nested underneath. */
    function renderScript(node, blocks) {
        if (!node) return;
        node.innerHTML = '';
        (blocks || []).forEach(function (block) {
            const kind = BLOCK_LABELS[block.kind] ? block.kind : 'note';

            const term = document.createElement('dt');
            term.textContent = BLOCK_LABELS[kind];
            term.className = `script-kind script-kind-${kind}`;
            node.appendChild(term);

            const detail = document.createElement('dd');
            detail.className = `script-block script-block-${kind}`;

            const text = document.createElement('p');
            text.className = 'script-text';
            // Spoken lines are quoted so it is unambiguous where the words the
            // participant hears begin and end.
            text.textContent = kind === 'say' ? `“${block.text}”` : block.text;
            detail.appendChild(text);

            if (block.note) {
                const note = document.createElement('p');
                note.className = 'script-subnote';
                note.textContent = block.note;
                detail.appendChild(note);
            }

            if (block.questions && block.questions.length) {
                const list = document.createElement('ol');
                list.className = 'script-questions';
                block.questions.forEach(function (question) {
                    const item = document.createElement('li');
                    item.textContent = question.text;
                    if (question.note) {
                        const qnote = document.createElement('p');
                        qnote.className = 'script-subnote';
                        qnote.textContent = question.note;
                        item.appendChild(qnote);
                    }
                    if (question.options && question.options.length) {
                        const options = document.createElement('ul');
                        options.className = 'script-options';
                        question.options.forEach(function (option) {
                            const optionItem = document.createElement('li');
                            optionItem.textContent = option;
                            options.appendChild(optionItem);
                        });
                        item.appendChild(options);
                    }
                    list.appendChild(item);
                });
                detail.appendChild(list);
            }

            node.appendChild(detail);
        });
    }

    // -----------------------------------------------------------------------
    // Dialogs — Current Step (C), Strategy Prompts (S), Jump to Step (J), and
    // Help (H or ?). Same accessible pattern as the participant viewer's Help/
    // About/Settings dialogs: native <dialog> + showModal() supplies focus
    // containment, background inertness, and Escape-to-close for free; the one
    // thing that isn't automatic is returning focus to whatever triggered the
    // dialog once it closes, which is what each controller's `trigger` is for.
    // See the ARIA APG pattern:
    // https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/examples/dialog/
    // -----------------------------------------------------------------------

    function makeInfoDialogController(dialog, headingEl) {
        let trigger = null;

        function open() {
            if (!dialog || !dialog.showModal || dialog.open) {
                return;
            }
            trigger = document.activeElement;
            if (mainContent) mainContent.setAttribute('aria-hidden', 'true');
            dialog.showModal();
            // showModal() defaults to focusing the first focusable element, which
            // here is the Close button at the very end of the content — reading
            // forward from there reads nothing. Per the ARIA APG dialog pattern, a
            // read-only dialog like this one should instead land on a static
            // element at the top (the heading, made focusable via tabindex="-1"),
            // so reading forward covers the whole dialog.
            if (headingEl) headingEl.focus({ preventScroll: true });
        }

        function close() {
            if (!dialog || !dialog.open) {
                return;
            }
            dialog.close();
        }

        function restoreAfterClose() {
            if (mainContent) mainContent.removeAttribute('aria-hidden');
            if (trigger && document.contains(trigger)) {
                trigger.focus();
            }
            trigger = null;
        }

        if (dialog) {
            // Cleanup after the dialog has actually closed. (Escape triggers
            // `cancel`, whose default action closes the dialog and then fires
            // `close`.)
            dialog.addEventListener('close', restoreAfterClose);
            // Clicking outside the dialog's own content (i.e. the backdrop, since
            // <dialog> occupies the full viewport once open) closes it the same
            // way Escape does.
            dialog.addEventListener('click', function (e) {
                if (e.target !== dialog) return;
                dialog.close();
            });
        }

        return { open, close };
    }

    const currentStepDialogController = makeInfoDialogController(el('current-step-dialog'), el('current-step-heading'));
    const strategyDialogController = makeInfoDialogController(el('strategy-dialog'), el('strategy-heading'));
    const jumpDialogController = makeInfoDialogController(el('jump-dialog'), el('jump-heading'));
    const helpDialogController = makeInfoDialogController(el('help-dialog'), el('help-heading'));

    el('current-step-close-btn')?.addEventListener('click', () => currentStepDialogController.close());
    el('strategy-close-btn')?.addEventListener('click', () => strategyDialogController.close());
    el('jump-close-btn')?.addEventListener('click', () => jumpDialogController.close());
    el('help-close-btn')?.addEventListener('click', () => helpDialogController.close());
    el('help-btn')?.addEventListener('click', () => helpDialogController.open());

    // -----------------------------------------------------------------------
    // Enrollment
    // -----------------------------------------------------------------------

    /** Opening this page starts a session. Every field the old enrolment form
     * asked for was already decided by the protocol -- the participant id comes
     * from the database, the model pairs from the Latin square, the session
     * number is 1 -- so it asked questions with only one answer and stood
     * between the experimenter and the session they came to run.
     *
     * A reload does not start another: the session is remembered per tab, so one
     * instance of this page owns exactly one session. */
    function startSession() {
        return api('/study/session/start', { method: 'POST', body: JSON.stringify({}) })
            .then(function (body) {
                bindSession(body.state.study_session_id);
                applyState(body.state);
                announce(
                    `Session started for ${body.state.participant_code}. `
                    + `Give your participant the code ${body.state.participant_key}.`
                );
            })
            .catch(function (error) {
                const errorNode = el('starting-error');
                if (errorNode) {
                    errorNode.hidden = false;
                    errorNode.textContent = `Could not start a session: ${error.message}`;
                }
                announce(`Could not start a session. ${error.message}`);
            });
    }

    // -----------------------------------------------------------------------
    // Active session
    // -----------------------------------------------------------------------

    function renderSession() {
        const step = state.step || {};
        const pair = step.pair;
        const model = step.model;

        const others = (state.active_sessions || []).filter(o => !o.is_current);
        const otherNotice = el('other-sessions-notice');
        if (otherNotice) {
            otherNotice.hidden = others.length === 0;
            otherNotice.textContent = others.length
                ? 'Also running on this server: '
                  + others.map(o => o.participant_code).join(', ')
                  + '. They are independent of this one.'
                : '';
        }

        const warning = el('missing-models-warning');
        if (warning) {
            const missing = state.missing_models || [];
            warning.hidden = missing.length === 0;
            warning.textContent = missing.length
                ? 'These protocol models are not on the server, and the steps that use '
                  + `them will not load anything: ${missing.join(', ')}.`
                : '';
        }

        el('summary-participant').textContent = state.participant_code || '--';
        el('summary-session-number').textContent = String(state.session_number || '--');
        el('summary-code').textContent = state.participant_key || '--';
        el('summary-tasks').textContent = (state.task_labels || []).join(', then ') || '--';

        const counts = state.counts || {};
        el('summary-counts').textContent =
            `${counts.events || 0} interactions, ${counts.renders || 0} renders`;

        // More than one attached view means every keypress, announcement and
        // render is recorded once per browser. The log stays separable by
        // client_id, but the totals above are inflated and nobody notices from a
        // bare number. A real session was logged twice this way.
        const clients = state.attached_participants || 0;
        const clientsNode = el('summary-clients');
        if (clients > 1) {
            clientsNode.textContent =
                `${clients} — every interaction is being recorded ${clients} times. `
                + `Close the extra tabs or windows on /study.`;
            clientsNode.classList.add('is-warning');
            if (clients !== lastClientCount) {
                announce(`Warning: ${clients} participant views are connected. `
                         + `Interactions are being recorded ${clients} times.`);
            }
        } else {
            clientsNode.textContent = clients === 0
                ? '0 — the participant view is not open'
                : '1';
            clientsNode.classList.remove('is-warning');
        }
        lastClientCount = clients;

        renderLoggingHealth();

        el('step-progress').textContent =
            `Step ${(state.step_index || 0) + 1} of ${state.step_count || 1}`;
        el('step-part').textContent = `${step.part_title || ''} — ${step.title || ''}`;
        renderScript(el('step-script'), step.script);

        const physicalBlock = el('physical-model-block');
        physicalBlock.hidden = !step.physical_model;
        if (step.physical_model) el('step-physical-model').textContent = step.physical_model;

        const modelBlock = el('model-block');
        modelBlock.hidden = !model;
        if (model) {
            el('step-model').textContent =
                `${model.label || model.model} (${model.model}) — loads automatically on this step.`;
            const missing = el('model-missing-warning');
            missing.hidden = state.model_available !== false;
            missing.textContent = state.model_available === false
                ? `The server has no model named ${model.model}, so nothing will load on `
                  + `the participant's display for this step.`
                : '';
        }

        const checklistBlock = el('checklist-block');
        checklistBlock.hidden = !(step.checklist && step.checklist.length);
        setList(el('step-checklist'), step.checklist);

        // The description is spoken; the answer key is not. Two blocks, so the
        // "do not read this out" warning cannot be read as covering both.
        const objectBlock = el('object-block');
        objectBlock.hidden = !pair;
        if (pair) {
            el('object-label').textContent = pair.label || '';
            el('object-description').textContent = pair.description ? `“${pair.description}”` : '';
        }

        const answerBlock = el('answer-key-block');
        answerBlock.hidden = !pair;
        if (pair) {
            setList(el('answer-key-differences'), pair.differences);
            setList(el('answer-key-unchanged'), pair.unchanged);
        }

        setList(el('facilitator-prompts'), step.facilitator_prompts || state.facilitator_prompts);
        setList(el('strategy-prompts'), step.strategy_prompts || state.strategy_prompts);

        renderJumpList();
        renderReadiness();
    }

    function renderLoggingHealth() {
        const logging = state.logging || {};
        const failures =
            (logging.db_write_failures || 0) + (logging.jsonl_write_failures || 0);
        const node = el('summary-logging');
        // Words, not a colour: this is the one field where being wrong means a
        // session's data is quietly incomplete.
        node.textContent = failures === 0
            ? 'Recording normally'
            : `Degraded — ${failures} write${failures === 1 ? '' : 's'} failed. ${logging.last_error || ''}`;

        const signature = String(failures);
        if (signature !== lastLoggingSignature) {
            if (lastLoggingSignature !== null && failures > 0) {
                announce('Warning: study logging is failing. Check the server before continuing.');
            }
            lastLoggingSignature = signature;
        }
    }

    function renderReadiness() {
        const indicator = el('ready-indicator');
        const ready = state.participant_ready;
        if (!ready) {
            indicator.classList.remove('is-ready');
            indicator.textContent = 'Participant has not signalled readiness on this step.';
            lastReadyAt = null;
            return;
        }
        indicator.classList.add('is-ready');
        const at = String(ready.at || '').replace('T', ' ').replace('Z', ' UTC');
        indicator.textContent = `Participant is ready to move on (signalled at ${at}).`;
        if (ready.at !== lastReadyAt) {
            lastReadyAt = ready.at;
            announce('Participant is ready to move on.');
        }
    }

    /** The Jump to Step dialog's list: one button per step. Rebuilt only when
     * the set of steps changes; the current step is marked (and disabled, since
     * jumping to where you already are is a no-op) on every render instead. */
    function renderJumpList() {
        const list = el('jump-step-list');
        if (!list) return;
        const steps = state.steps || [];
        const signature = steps.map(function (s) { return s.id; }).join('|');
        if (list.dataset.signature !== signature) {
            list.innerHTML = '';
            steps.forEach(function (s) {
                const item = document.createElement('li');
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.stepIndex = String(s.index);
                button.dataset.label = `${s.index + 1}. ${s.part_title} — ${s.title}`;
                button.addEventListener('click', function () {
                    const index = Number(this.dataset.stepIndex);
                    jumpDialogController.close();
                    advance({ step_index: index });
                });
                item.appendChild(button);
                list.appendChild(item);
            });
            list.dataset.signature = signature;
        }
        Array.prototype.forEach.call(list.querySelectorAll('button'), function (button) {
            const isCurrent = Number(button.dataset.stepIndex) === (state.step_index || 0);
            button.disabled = isCurrent;
            button.textContent = button.dataset.label + (isCurrent ? ' (current)' : '');
            if (isCurrent) button.setAttribute('aria-current', 'step');
            else button.removeAttribute('aria-current');
        });
    }

    /** Move to another step. No confirmation: N/B/J act immediately, without a
     * popup, on the theory that opening the Jump dialog (or pressing N/B) is
     * already the deliberate action. */
    function advance(payload) {
        api('/study/step/advance', { method: 'POST', body: JSON.stringify(payload) })
            .then(function (body) { applyState(body.state); })
            .catch(function (error) { announce(`Could not move step. ${error.message}`); });
    }

    el('next-step-btn')?.addEventListener('click', function () {
        advance({ direction: 'next' });
    });
    el('previous-step-btn')?.addEventListener('click', function () {
        advance({ direction: 'previous' });
    });

    el('run-here-btn')?.addEventListener('click', function () {
        if (!window.confirm(
            'Hand this window over to the participant?\n\n'
            + 'The session will move to the next step when they press '
            + '"I am ready to move on". You will not have the script on screen.'
        )) return;
        // The mode is set on the session, not carried in the URL, so a reload
        // keeps it and the log records how the session was run.
        api('/study/session/mode', { method: 'POST', body: JSON.stringify({ mode: 'solo' }) })
            .then(function (body) {
                location.href = `/study?s=${encodeURIComponent(body.state.participant_key)}`;
            })
            .catch(function (error) { announce(`Could not switch modes. ${error.message}`); });
    });

    el('end-session-btn')?.addEventListener('click', function () {
        if (!window.confirm('End this session? The record is closed and cannot be reopened.')) return;
        api('/study/session/end', { method: 'POST', body: JSON.stringify({ status: 'completed' }) })
            .then(function () {
                announce('Session ended and recorded.');
                refreshOnce();
            })
            .catch(function (error) { announce(`Could not end the session. ${error.message}`); });
    });

    // -----------------------------------------------------------------------
    // Keyboard commands — C (current step), S (strategy prompts), J (jump to a
    // step), N (next), B (back/previous), R (run study here), E (end session),
    // H or ? (this list). Mirrors the participant viewer's global shortcut
    // guard: not while typing in a field, and not while a dialog already owns
    // focus (Escape and Tab must stay scoped to it).
    // -----------------------------------------------------------------------

    document.addEventListener('keydown', function (e) {
        const target = e.target;
        const tagName = target && target.tagName ? target.tagName.toLowerCase() : '';
        const isTextEntryTarget = Boolean(
            target && (target.isContentEditable || tagName === 'textarea' || tagName === 'input')
        );
        if (isTextEntryTarget) return;

        if (document.querySelector('dialog[open]')) return;

        // Leave browser/app shortcuts untouched (Cmd/Ctrl/Alt combos).
        if (e.metaKey || e.ctrlKey || e.altKey) return;

        const rawKey = String(e.key || '');
        const key = rawKey.toLowerCase();

        if (key === 'h' || rawKey === '?') {
            e.preventDefault();
            helpDialogController.open();
            return;
        }

        // Everything else needs an active session: there is nothing to show or
        // move through before one exists, and the nav buttons for these are
        // hidden until then too.
        if (sessionSection.hidden) return;

        switch (key) {
            case 'c':
                e.preventDefault();
                currentStepDialogController.open();
                break;
            case 's':
                e.preventDefault();
                strategyDialogController.open();
                break;
            case 'j':
                e.preventDefault();
                jumpDialogController.open();
                break;
            case 'n':
                e.preventDefault();
                advance({ direction: 'next' });
                break;
            case 'b':
                e.preventDefault();
                advance({ direction: 'previous' });
                break;
            case 'r':
                e.preventDefault();
                el('run-here-btn')?.click();
                break;
            case 'e':
                e.preventDefault();
                el('end-session-btn')?.click();
                break;
            default:
                break;
        }
    });

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    function setNavButtonsVisible(visible) {
        ['run-here-btn', 'previous-step-btn', 'next-step-btn', 'end-session-btn'].forEach(function (id) {
            const button = el(id);
            if (button) button.hidden = !visible;
        });
    }

    function applyState(next) {
        if (!next) return;
        state = next;

        if (!state.active) {
            // The session this panel owns has ended. It does not silently start
            // another -- that would be a second participant record created by a
            // stray reload.
            startingSection.hidden = false;
            sessionSection.hidden = true;
            setNavButtonsVisible(false);
            lastStepId = null;
            const heading = el('starting-heading');
            if (heading) heading.textContent = 'This session has ended';
            const errorNode = el('starting-error');
            if (errorNode) {
                errorNode.hidden = false;
                errorNode.textContent =
                    'Open this page again in a new tab to run another participant.';
            }
            return;
        }

        startingSection.hidden = true;
        sessionSection.hidden = false;
        setNavButtonsVisible(true);
        renderSession();

        const stepId = (state.step || {}).id;
        if (stepId !== lastStepId) {
            lastStepId = stepId;
            status(`Step ${(state.step_index || 0) + 1} of ${state.step_count}: `
                   + `${(state.step || {}).title || ''}`);
        }
    }

    function refreshOnce() {
        api('/study/state').then(applyState).catch(function (error) {
            announce(`Could not reach the server. ${error.message}`);
        });
    }

    let eventSource = null;
    let reconnectDelay = 1000;

    function connect() {
        // EventSource cannot set headers, so the token goes in the query string
        // here. Same secret, same transport security; it is the only way to
        // authenticate an SSE subscription from the browser.
        try {
            eventSource = new EventSource(
                scoped(`/study/stream?token=${encodeURIComponent(TOKEN)}`)
            );
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

    // -----------------------------------------------------------------------
    // Boot
    // -----------------------------------------------------------------------

    api('/study/config').then(function (body) {
        config = body;
        el('protocol-version').textContent =
            `Protocol version ${config.version}. Each participant explores a practice `
            + `Lego brick, then ${config.tasks_per_session} of the three model pairs.`;

        // A reload continues the session this tab already owns; a fresh tab
        // starts one. Checked before starting, so refreshing mid-session cannot
        // strand a participant on a session nobody is driving.
        const resuming = boundSessionId
            ? api('/study/state').then(function (state) {
                  if (state && state.active) { applyState(state); return true; }
                  bindSession(null);
                  return false;
              }).catch(function () { bindSession(null); return false; })
            : Promise.resolve(false);

        return resuming.then(function (resumed) {
            const ready = resumed ? Promise.resolve() : startSession();
            return ready.then(connect);
        });
    }).catch(function (error) {
        el('protocol-version').textContent = `Could not load the protocol: ${error.message}`;
        announce(`Could not load the protocol. ${error.message}`);
    });
})();
