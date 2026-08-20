/**
 * Experimenter control panel for a study session.
 *
 * Drives the session: enrolls the participant, shows the script for the current
 * step, and moves through the protocol. The participant's view follows over the
 * same Server-Sent Events stream, so the two stay in step across two machines.
 *
 * The home screen is deliberately just an identity summary (dl.session-summary).
 * Everything else is a keyboard command: C shows the current step, S shows
 * strategy prompts, J jumps to a step, H or ? lists every command. N (next)
 * and B or P (back/previous) move the session on and then open the Current
 * Step dialog, so the new step's script is on screen immediately rather than
 * a separate C press away. R runs the study on this device; E ends the
 * session. Each dialog follows the same accessible pattern as the participant
 * viewer's Help/About/Settings dialogs (native <dialog> + showModal(),
 * heading focus on open, Esc/backdrop to close) via makeInfoDialogController
 * below.
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

    const currentStepDialog = el('current-step-dialog');
    const currentStepDialogController = makeInfoDialogController(currentStepDialog, el('current-step-heading'));
    const strategyDialogController = makeInfoDialogController(el('strategy-dialog'), el('strategy-heading'));
    const jumpDialogController = makeInfoDialogController(el('jump-dialog'), el('jump-heading'));
    const helpDialogController = makeInfoDialogController(el('help-dialog'), el('help-heading'));

    el('current-step-close-btn')?.addEventListener('click', () => currentStepDialogController.close());
    el('strategy-close-btn')?.addEventListener('click', () => strategyDialogController.close());
    el('jump-close-btn')?.addEventListener('click', () => jumpDialogController.close());
    el('help-close-btn')?.addEventListener('click', () => helpDialogController.close());
    el('help-btn')?.addEventListener('click', () => helpDialogController.open());

    // -----------------------------------------------------------------------
    // Choosing the model set, and enrolment
    //
    // Which two objects a participant gets is the one thing starting a session
    // needs a human to decide, so it is the first thing on screen. Everything
    // else the old enrolment form asked for was already settled -- the
    // participant id comes from the database, the session number is 1 -- so it
    // asked questions with only one answer and stood between the experimenter
    // and the session they came to run.
    // -----------------------------------------------------------------------

    const setSection = el('set-section');

    /** Fetch the six sets with their used/unused state and draw the picker. */
    function showSetPicker() {
        return api('/study/sets')
            .then(function (body) {
                renderSetPicker(body);
                startingSection.hidden = true;
                setSection.hidden = false;
                // The heading, not the first button: reading forward from here
                // covers the round summary as well as the choices, and landing
                // straight on a button says nothing about what is being chosen.
                el('set-heading')?.focus({ preventScroll: true });
            })
            .catch(function (error) {
                const heading = el('starting-heading');
                if (heading) heading.textContent = 'Could not load the model sets';
                const errorNode = el('starting-error');
                if (errorNode) {
                    errorNode.hidden = false;
                    errorNode.textContent = error.message;
                }
                announce(`Could not load the model sets. ${error.message}`);
            });
    }

    function renderSetPicker(sets) {
        const entries = sets.sets || [];
        const remaining = sets.remaining || 0;
        const perRound = sets.sets_per_round || entries.length;

        const summary = el('set-round');
        if (summary) {
            const anyRun = entries.some(function (entry) { return entry.completed_count > 0; });
            if (!anyRun) {
                summary.textContent =
                    `Round ${sets.round}. Nothing has been run yet, so all ${perRound} are available.`;
            } else if (remaining === perRound) {
                // Every set level with every other, which is what finishing a
                // round looks like. Said as a completed round rather than as
                // "an equal number of times", which is true on an empty
                // database too and reads like nothing has happened.
                summary.textContent =
                    `Round ${sets.round}. The previous round is complete, so all ${perRound} `
                    + `are available again.`;
            } else {
                summary.textContent =
                    `Round ${sets.round}. ${remaining} of ${perRound} sets still to run; `
                    + `the rest have been run already and are struck through.`;
            }
        }

        const list = el('set-list');
        if (!list) return;
        list.innerHTML = '';
        entries.forEach(function (entry) {
            const item = document.createElement('li');

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'set-choice' + (entry.used ? ' is-used' : '');
            button.dataset.setId = entry.id;

            const label = document.createElement('span');
            label.className = 'set-choice-label';
            label.textContent = (entry.labels || entry.task_order).join(', then ');
            button.appendChild(label);

            // The strikethrough is decoration and is not announced, so whatever
            // it means has to be in the text as well. Same rule as everywhere
            // else on this page: no state conveyed by looks alone.
            const notes = [];
            if (entry.used) {
                notes.push(entry.completed_count === 1
                    ? 'already run once this round'
                    : `already run ${entry.completed_count} times`);
            }
            if (entry.in_progress) {
                notes.push(entry.in_progress === 1
                    ? 'a session is running on it now'
                    : `${entry.in_progress} sessions are running on it now`);
            }
            if (notes.length) {
                const note = document.createElement('span');
                note.className = 'set-choice-note';
                // Capitalised for reading, joined so the button's accessible
                // name is one sentence rather than two fragments.
                const text = notes.join('; ');
                note.textContent = ' — ' + text.charAt(0).toUpperCase() + text.slice(1) + '.';
                button.appendChild(note);
            }

            // Used sets stay selectable. Striking one through says what it
            // costs; refusing it would mean an experimenter who has to deviate
            // -- a missing print, a participant who saw one of these in a pilot
            // -- has no way through the panel at all.
            button.addEventListener('click', function () {
                startSession(entry.task_order, label.textContent);
            });

            item.appendChild(button);
            list.appendChild(item);
        });
    }

    /** Start the session on the chosen set.
     *
     * A reload does not start another: the session is remembered per tab, so one
     * instance of this page owns exactly one session. */
    function startSession(taskOrder, setLabel) {
        // Cleared before the attempt, not only on success: #set-error is an
        // alert region, and leaving a failed attempt's message standing while
        // the next one runs reads as though this one failed too.
        const previousError = el('set-error');
        if (previousError) {
            previousError.hidden = true;
            previousError.textContent = '';
        }
        setSection.hidden = true;
        startingSection.hidden = false;
        const heading = el('starting-heading');
        if (heading) heading.textContent = 'Starting a session…';
        status(`Starting a session on ${setLabel}.`);

        return api('/study/session/start', {
            method: 'POST',
            body: JSON.stringify({ task_order: taskOrder }),
        })
            .then(function (body) {
                bindSession(body.state.study_session_id);
                applyState(body.state);
                // Subscribed only now, and only once: the stream is scoped to
                // the session named at subscribe time, so opening it while the
                // panel was still on the picker would attach it to nothing.
                connect();
                announce(
                    `Session started for ${body.state.participant_code}, on ${setLabel}. `
                    + `Give your participant the code ${body.state.participant_key}.`
                );
            })
            .catch(function (error) {
                // Back to the picker: the set was never consumed, so the
                // experimenter should be choosing again rather than stuck on a
                // dead page.
                startingSection.hidden = true;
                setSection.hidden = false;
                const errorNode = el('set-error');
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

        // The button says which of the two things it will do, so an
        // experimenter is not asked to infer from the step number that Next has
        // become the end of the session.
        const nextButton = el('next-step-btn');
        if (nextButton) nextButton.textContent = onLastStep() ? 'Finish session' : 'Next';

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

    /** Move to another step. No confirmation: N/B/P/J act immediately, on the
     * theory that pressing N/B/P or picking a step in the Jump dialog is
     * already the deliberate action. Callers that want the Current Step
     * dialog to follow the move do that themselves after calling this. */
    function advance(payload) {
        api('/study/step/advance', { method: 'POST', body: JSON.stringify(payload) })
            .then(function (body) { applyState(body.state); })
            .catch(function (error) { announce(`Could not move step. ${error.message}`); });
    }

    /** True when the session is sitting on the last step of the protocol. */
    function onLastStep() {
        if (!state || !state.step_count) return false;
        return (state.step_index || 0) >= state.step_count - 1;
    }

    /** Next, or Finish when there is nowhere further to go.
     *
     * Pressing Next on the last step used to do nothing at all, which left the
     * only way to close a session as a separate End button the last step's
     * script asks the experimenter to remember. They did not: across the two
     * deployed servers, twelve of fourteen sessions were still open, one of
     * them at step 19 of 22. Carrying on forwards is the gesture people
     * actually make, so it is the one that finishes the session.
     */
    function nextOrFinish() {
        if (onLastStep()) {
            endSession('That was the last step. End the session and close the record?');
            return;
        }
        advance({ direction: 'next' });
        currentStepDialogController.open();
    }

    el('next-step-btn')?.addEventListener('click', nextOrFinish);
    el('previous-step-btn')?.addEventListener('click', function () {
        advance({ direction: 'previous' });
        currentStepDialogController.open();
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

    function endSession(prompt) {
        if (!window.confirm(prompt)) return;
        api('/study/session/end', { method: 'POST', body: JSON.stringify({ status: 'completed' }) })
            .then(function () {
                announce('Session ended and recorded.');
                refreshOnce();
            })
            .catch(function (error) { announce(`Could not end the session. ${error.message}`); });
    }

    el('end-session-btn')?.addEventListener('click', function () {
        endSession('End this session? The record is closed and cannot be reopened.');
    });

    // -----------------------------------------------------------------------
    // Keyboard commands — C (current step), S (strategy prompts), J (jump to a
    // step), N (next), B or P (back/previous), R (run study here), E (end
    // session), H or ? (this list). Mirrors the participant viewer's global
    // shortcut guard: not while typing in a field, and not while a dialog
    // already owns focus (Escape and Tab must stay scoped to it) -- except N/
    // B/P, which work even with a dialog open. Advancing is exactly the
    // action that makes whatever dialog is currently showing stale, so rather
    // than being blocked, N/B/P replace it with the Current Step dialog for
    // the step just moved to.
    // -----------------------------------------------------------------------

    document.addEventListener('keydown', function (e) {
        const target = e.target;
        const tagName = target && target.tagName ? target.tagName.toLowerCase() : '';
        const isTextEntryTarget = Boolean(
            target && (target.isContentEditable || tagName === 'textarea' || tagName === 'input')
        );
        if (isTextEntryTarget) return;

        // Leave browser/app shortcuts untouched (Cmd/Ctrl/Alt combos).
        if (e.metaKey || e.ctrlKey || e.altKey) return;

        const rawKey = String(e.key || '');
        const key = rawKey.toLowerCase();
        const isStepMoveKey = key === 'n' || key === 'b' || key === 'p';

        const openDialog = document.querySelector('dialog[open]');
        if (openDialog && !isStepMoveKey) return;

        if (key === 'h' || rawKey === '?') {
            e.preventDefault();
            helpDialogController.open();
            return;
        }

        // Everything else needs an active session: there is nothing to show or
        // move through before one exists, and the nav buttons for these are
        // hidden until then too.
        if (sessionSection.hidden) return;

        if (isStepMoveKey) {
            e.preventDefault();
            // Close whatever else is open first -- Current Step is the "next
            // appropriate dialogue" for a step change, not an additional one
            // stacked on top. Already-open Current Step is left alone; its
            // content refreshes in place once the advance's state comes back.
            if (openDialog && openDialog !== currentStepDialog) openDialog.close();
            if (key === 'n') {
                // Same as the Next button, finish included: the keyboard route
                // through the protocol must not be the one that leaves sessions
                // open.
                nextOrFinish();
                return;
            }
            advance({ direction: 'previous' });
            currentStepDialogController.open();
            return;
        }

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
            // stray reload -- and it does not offer the set picker again either,
            // for the same reason.
            startingSection.hidden = false;
            setSection.hidden = true;
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
        setSection.hidden = true;
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
            `Protocol version ${config.version}. Each participant explores a mug, then ${config.tasks_per_session} of the three model pairs.`;

        // A reload continues the session this tab already owns; a fresh tab
        // asks which model set to run. Checked before offering the picker, so
        // refreshing mid-session cannot strand a participant on a session
        // nobody is driving.
        const resuming = boundSessionId
            ? api('/study/state').then(function (state) {
                  if (state && state.active) { applyState(state); return true; }
                  bindSession(null);
                  return false;
              }).catch(function () { bindSession(null); return false; })
            : Promise.resolve(false);

        return resuming.then(function (resumed) {
            // connect() subscribes to the session this panel is bound to, and a
            // panel still on the picker is bound to nothing. It is opened here
            // for the resumed case and after the session starts otherwise.
            if (resumed) { connect(); return; }
            return showSetPicker();
        });
    }).catch(function (error) {
        el('protocol-version').textContent = `Could not load the protocol: ${error.message}`;
        announce(`Could not load the protocol. ${error.message}`);
    });
})();
