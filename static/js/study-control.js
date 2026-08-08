/**
 * Experimenter control panel for a study session.
 *
 * Drives the session: enrolls the participant, shows the script for the current
 * step, and moves through the protocol. The participant's view follows over the
 * same Server-Sent Events stream, so the two stay in step across two machines.
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

        const linkNode = el('participant-link');
        if (linkNode) linkNode.textContent = `${location.origin}/study`;
        const codeNode = el('participant-code');
        if (codeNode) codeNode.textContent = state.participant_key || '--';

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

        renderStepJump();
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

    function renderStepJump() {
        const select = el('step-jump');
        if (!select) return;
        const signature = (state.steps || []).map(function (s) { return s.id; }).join('|');
        if (select.dataset.signature !== signature) {
            select.innerHTML = '';
            (state.steps || []).forEach(function (s) {
                const option = document.createElement('option');
                option.value = String(s.index);
                option.textContent = `${s.index + 1}. ${s.part_title} — ${s.title}`;
                select.appendChild(option);
            });
            select.dataset.signature = signature;
        }
        select.value = String(state.step_index || 0);
    }

    function advance(payload, description) {
        if (el('confirm-advance') && el('confirm-advance').checked) {
            if (!window.confirm(`${description}?`)) return;
        }
        api('/study/step/advance', { method: 'POST', body: JSON.stringify(payload) })
            .then(function (body) { applyState(body.state); })
            .catch(function (error) { announce(`Could not move step. ${error.message}`); });
    }

    el('next-step-btn')?.addEventListener('click', function () {
        advance({ direction: 'next' }, 'Move to the next step');
    });
    el('previous-step-btn')?.addEventListener('click', function () {
        advance({ direction: 'previous' }, 'Go back to the previous step');
    });
    el('step-jump')?.addEventListener('change', function () {
        advance({ step_index: Number(this.value) }, `Jump to step ${Number(this.value) + 1}`);
    });

    el('toggle-strategy-btn')?.addEventListener('click', function () {
        const block = el('strategy-prompts-block');
        const nowHidden = !block.hidden;
        block.hidden = nowHidden;
        this.setAttribute('aria-expanded', String(!nowHidden));
        this.textContent = nowHidden ? 'Show strategy prompts' : 'Hide strategy prompts';
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
    // State
    // -----------------------------------------------------------------------

    function applyState(next) {
        if (!next) return;
        state = next;

        if (!state.active) {
            // The session this panel owns has ended. It does not silently start
            // another -- that would be a second participant record created by a
            // stray reload.
            startingSection.hidden = false;
            sessionSection.hidden = true;
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
        renderSession();

        const stepId = (state.step || {}).id;
        if (stepId !== lastStepId) {
            const wasFirstRender = lastStepId === null;
            lastStepId = stepId;
            status(`Step ${(state.step_index || 0) + 1} of ${state.step_count}: `
                   + `${(state.step || {}).title || ''}`);
            // Move focus to the step heading on a real advance, so a screen
            // reader user lands on the new script instead of hunting for it.
            // Here that is right: the experimenter pressed Next, so the change
            // is theirs and the focus should follow it. Not on the first render,
            // which would yank focus away from the page heading the moment the
            // panel loaded.
            //
            // Called directly, not through requestAnimationFrame: the heading is
            // already laid out, and rAF does not run in a background tab -- the
            // panel can easily be behind the participant's window, and the move
            // would then fire whenever it next came forward.
            if (!wasFirstRender) {
                const target = el('current-step-heading');
                if (target) target.focus();
            }
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
