# Session isolation in the normal viewer

Answer to issue #123, with the measurements behind it and everything a server-side
refactor needs to know.

**Status:** investigation complete, no fix applied. Every line reference below was verified
against `a7c9818` (master). Measured 2026-07-31.

**Verdict:** `/viewer` is not independent across sessions. The only thing separating one
person's models from another's is a filter that runs in the browser. The server does not
filter at all: it offers every model to every visitor and will render any of them for
anyone who asks.

---

## 1. The two root causes

Almost every defect below reduces to one of these. Fix these two and most of the inventory
in section 3 closes on its own.

### Root cause A: a model is named by its position in a shared, mutable list

`_discover_models` (`app/server.py:427`) globs `MODEL_DIR` then `UPLOAD_DIR` and returns a
list. `AVAILABLE_MODELS` (`:450`) holds it, `_refresh_model_list_if_stale` (`:492`) rebuilds
it from disk at most every 2 seconds, and the browser refers to a model by its **integer
index into that list** (`static/js/viewer.js:237`, `:1237`, `:1930`).

Position is not identity. A session holding index 37 is not holding a model, it is holding
a number whose meaning changes the moment anyone uploads a file that sorts earlier.

Ordering detail the refactor must not get wrong: the list is **blocked by directory, then
by glob pattern, then sorted within each block**. It is not globally sorted. Patterns are
`("*.stl", "*.step", "*.STEP")` in that order (`:428`). Consequences:

- Built-ins always precede uploads, so an upload can no longer renumber a built-in. This is
  the half of issue #123 item 1 that PR #128 fixed.
- Uploads still renumber each other.
- Never mix extensions in an ordering test: the three patterns are sorted independently and
  concatenated.
- `*.STL` uppercase is absent from the glob, so such a file is unreachable but still
  reserves its stem via `_stem_is_taken` (`:476`). Acknowledged in the comment at `:420`.

### Root cause B: the server keeps one of something where it needs one per session

| Global | Declared | Should be |
|---|---|---|
| `state.current_model_index` | `:76`, instance at `:364` | per session |
| `renderers_by_model` (keyed by **int index**) | `:365` | keyed by resolved path |
| `CADComparisonRenderer.view_current_camera_center` | `app/cad_comparison_lib.py:152` | per session |
| `current_render` | `:366` | per session, or removed |
| `last_render_fingerprint` / `last_render_response` | `:377` / `:378` | key already includes model; single slot is a perf issue, not a correctness one |

Note the diagnostic value of what already works: view axis, slice depth, zoom and render
mode are **already per session**, because those parameters travel in each request. The
controls that leak are exactly the ones the server stores. That is the shape of the fix.

---

## 2. What was tested and why

Three layers. Each exists because the layer below it cannot see the mechanism in question.

| Layer | Tool | Why this layer |
|---|---|---|
| Server contract | pytest, `tests/test_concurrent_sessions.py` | Deterministic, fast, and lets the model list be controlled exactly. The quantity under measurement is a sorted directory, so the test must own that directory |
| Browser | `scripts/concurrent_session_probe.py` | The ownership filter and the 5 s poll only exist in JavaScript. `test_client` executes none |
| Scale | `scripts/multi_user_session_probe.py` | 24 users. Effects that look anecdotal at 2 sessions are unambiguous at 24, and distinct per-user geometry proves *whose* model came back |

### Running them

```bash
# Layer 1
python -m pytest tests/test_concurrent_sessions.py -v

# Layers 2 and 3 need a container built from the branch under test
docker compose up -d --build
python scripts/concurrent_session_probe.py
python scripts/multi_user_session_probe.py --users 24
```

Playwright is required for layers 2 and 3 and is **not** in `requirements-dev.txt`:

```bash
pip install playwright && playwright install chromium
```

Reset the container to a known state between runs. `docker compose down -v` also destroys
the database and any real uploads, so prefer:

```bash
docker exec cad-a11y-app-1 sh -c 'rm -f /project/data/uploads/*.stl /project/data/db/usage.db*'
docker compose restart
```

Do **not** use `localoverrides.yml` for these probes. It bind-mounts the host `data/models`,
which carries hundreds of files of test residue and destroys the controlled ordering.

---

## 3. Complete defect inventory

Ordered by severity. "Concurrency" means whether a second simultaneous user is required.

### D1. Ownership is recorded but never enforced (no concurrency needed)

The database has a real owner column: `uploaded_models.session_id` (`app/db.py:60`), written
by `register_model` (`app/db.py:266`, called from `app/server.py:1757`).

`session_owns_model` (`app/db.py:283`) has **exactly one call site**: `app/server.py:2009`,
inside `DELETE /models/<filename>`. Nothing on the read, render, preview, export or braille
paths consults it.

Measured: 22 of 22 users in the 24-user run rendered another user's model and received
**that user's exact bounding box**, with 22 distinct geometries in play. Two-session run: a
session with no cookie received the owner's braille frame **byte for byte**.

Endpoints that ignore the session cookie entirely: `/render`, `/render/preview`,
`/render/export-source`, `/render/dotpad-hex`, `/render/fit-view`, `/render/image`,
`/render/base64`, `GET /models`, `POST /models`, `/ingest`, `/get_data`, `/events`,
`/command`, `/uploads/cleanup`.

### D2. `GET /models` is unfiltered and discloses filesystem paths (no concurrency needed)

`app/server.py:1580-1597`. Returns the full `model_list` **and** `model_paths` containing
real absolute paths, to any caller, with no cookie.

Measured: 504 of 506 cross-user pairs exposed at 24 users. Only the 2 same-email pairs were
legitimate. An unauthenticated `curl` against the container returns
`/project/data/models/cane_tip_fitted.stl`.

The client never reads `model_paths`. Verify with a grep before removing, then remove it.

### D3. `/render/export-source` ignores its own `current_model` argument

`app/server.py:2131-2174`. It does **not** call `_prepare_render_params`. It builds
`merged_params` by hand (`:2139`) and then calls `get_or_create_renderer()` with **no
argument** (`:2146`), so `_normalize_model_index(None)` returns the process-global
`state.current_model_index`. The posted `current_model` lands in `merged_params` and is
never read: `CADComparisonRenderer.render()` has zero references to that key.

Measured: session A asked for model 1 twice and got two different images, `25b0c32b0ce0a645`
then `99cb404b45cf215e`, because session B rendered in between.

`/render/preview` (`:2183`) and `/render/dotpad-hex` (`:2216`) both **do** go through
`_prepare_render_params` and honour the posted model. Do not group them with this defect;
they ignore the cookie, not the index.

### D4. Any page load resets every other session's current model

`static/js/viewer.js:391` initialises `let currentModel = "none"` and sends it verbatim
(`:237`). `_normalize_model_index` (`app/server.py:506`) hits its `ValueError` branch and
returns `0` (`:513`), and `/render` writes that into the shared object at `:1424`.

Measured: A selected model 1, B opened a viewer, A read back 0.

`state.current_model_index` is written at `:1424` (`/render`), `:1498` (`/command`), `:1603`
(`POST /models`), and clamped to 0 at `:1694` and `:2028`.

### D5. Pan state lives on a renderer shared between sessions

`view_current_camera_center` is mutable per-instance state on `CADComparisonRenderer`,
assigned at `app/cad_comparison_lib.py:828` and incremented at `:836-842`. Two sessions
viewing the same model get the same renderer object from `get_or_create_renderer`.

A render that omits `camera_center` inherits wherever the other session last panned. When
the client does send `camera_center`, `:826` overwrites the instance state, which is why
this does not fire on every request.

Measured: one user panned, 8 of 8 watchers moved. A control cycle with identical steps and
nobody panning moved 0 of 8.

`render_lock` (`app/server.py:373`) serialises calls but does not isolate state, and the
comment at `:370` says so.

### D6. The renderer cache is keyed by index and survives a reorder

`renderers_by_model: dict[int, ...]` (`:365`); `get_or_create_renderer` (`:519`) only checks
`if index not in renderers_by_model` (`:522`). `_refresh_model_list_if_stale` (`:492`)
rebinds `AVAILABLE_MODELS` **without clearing the renderer cache** (`:498-503`).

The code partly knows this: `_save_and_index_stl` clears it at `:1772` with a comment
explaining exactly this hazard. The 2 second refresh path does not.

Measured: after a file was dropped into `MODEL_DIR` out of band and took index 0, rendering
index 0 still returned the previous model's bounding box.

Fix falls out of keying by resolved path instead of index.

### D7. The ingest broadcast reaches every session

`_push_sse` (`:399`) iterates every queue in `_sse_clients` (`:387`) with no session key.
`GET /events` (`:2076`) requires no cookie. `/ingest` pushes `{"load_model": stem}` at
`:1904`.

Client acts at `static/js/viewer.js:1813-1821` with only an index-identity guard. No
ownership check, no session check.

Measured: 2 `load_model` frames reached an uninvolved session's event stream. No tab moved
in that run, **but that is timing, not protection**: a fresh ingest mints a new hashed stem
the receiving tab has not polled yet, and the guard at `:1814` only fires for stems already
in `lastFullModelList`. Pushing a stem the tab already knew moved it from index 0 to 1
immediately.

**Do not scope the SSE channel wholesale.** See section 4.

### D8. A `/viewer` upload has no owner (no concurrency needed)

`GET /viewer` (`:1201-1209`) deliberately mints no cookie and creates no DB row, citing
GDPR and ePrivacy. The cookie is minted only by `POST /session/identify` (`:1976`) and
`GET /workshop?name=` (`:1257`).

So `POST /upload` from a plain visitor passes `session_id=None` and `register_model` is
never reached. The file is listed and renderable by index to everyone, and is absent from
its own uploader's dropdown.

Measured: at 24 users, exactly the 2 anonymous uploaders could not see their own uploads.

This is the decision in section 5.

### D9. The browser filter is off for the first few seconds

`_visibleModelEntries` (`static/js/viewer.js:1534`) returns the **full unfiltered list**
when `builtinModelStems` is falsy (`:1536`). That variable is assigned once and latches:
`if (data.builtin_model_stems && !builtinModelStems)` (`:1796`), never replaced without a
page reload.

`builtin_model_stems` is absent from the SSE initial frame (`app/server.py:2091`) and from
every `/render` response (`:1064`). It arrives only on the first `/get_data` poll, and
`/get_data` runs on a 5000 ms interval (`static/js/viewer.js:1861`).

Measured: another session's upload was listed from **0.03 s to 4.88 s** on page load,
reproducing at 0.04 s to 4.87 s. Dropdown option count went 16 to 17 to 16.

Also check `/viewer?model=<stem>`: the bootstrap at `static/js/viewer.js:2740` fetches
`/get_data` and reads only `model_list`, discarding `builtin_model_stems` from the very
response that carries it.

### D10. Ingested files are classified public forever

`/ingest` passes `public=True` (`app/server.py:1876-1880`), so `_save_and_index_stl` writes
into `MODEL_DIR`. `_is_builtin` (`:456`) classifies by directory, so every participant file
becomes a built-in and is never hidden by the ownership filter.

Measured: 4 probe ingests moved `/health`'s `unexpected_public_models` from 0 to 4.
`scripts/cleanup_ingest_models.py --apply` reclaims it.

Note from that script's own docstring: `/ingest` names files from the caller's `?filename=`
or uploaded filename, so on a real server participant files are named after the
participant's model, not after the endpoint. Do not pattern-match on `ingest_*`.

### D11. Lower priority

- `POST /uploads/cleanup` (`:2054`) requires no cookie and no ownership proof, deletes
  files, and flushes `renderers_by_model` for everyone. Nothing in the shipped client calls
  it.
- `GET /render/image` (`:1455`) and `/render/base64` (`:1465`) return `current_render`
  (`:366`), the most recent render from any session, to any caller.
- `_normalize_model_index` reads `len(AVAILABLE_MODELS)` (`:514`) outside `models_lock`, and
  `_render_and_send` indexes `AVAILABLE_MODELS[model_index]` (`:690`) outside any lock. A
  concurrent rebind to a shorter list is an `IndexError`. Narrow window, reasoned from code,
  not reproduced.

---

## 4. What must NOT change

A refactor that breaks any of these has made things worse. Each has a passing guard-rail
test in `tests/test_concurrent_sessions.py::TestAlreadyCorrect`.

**Hardware SSE broadcast is deliberate.** `cube_value` (`:1190`) and `slider_value`
(`:1142`) are pushed to every session on purpose: one physical WitMotion cube and one slider
drive every station in the room. The comment at `:1421-1423` is explicit that
browser-selected viewpoints are kept out of global state for exactly this reason. **The
defect is `load_model` riding the same unfiltered channel, not the channel being
unfiltered.** Scoping SSE per session without carving out hardware events breaks the
workshop. Guarded by `test_hardware_cube_value_is_broadcast_to_every_session`.

**Email-based cross-device sharing is deliberate.** `get_session_models` (`app/db.py:221`)
and `session_owns_model` (`:283`) widen to every session sharing an `identifier`, which is
what lets someone open their models on a second device. Measured working: two sessions with
distinct cookies and the same email saw both models. Guarded by
`test_same_email_shares_models_across_sessions` and by `TestCrossSessionModels` in
`tests/test_session.py`.

**Built-in indices are already stable across uploads.** Guarded by
`test_builtin_indices_are_stable_across_uploads`.

**Out-of-range index returns 0, which fails closed.** Do not file this alongside the leaks.
Guarded by `test_out_of_range_index_falls_back_to_zero`. Note the desired behaviour after
the fix is to fall back to the caller's **own** first model, not to global index 0.

**Delete already enforces ownership.** Guarded by
`test_deleting_another_sessions_model_is_refused`.

**View, depth, zoom and render mode are already per session.** Guarded by
`TestCommandsAreSelfContained`. Measured at 24 users: 0 of 24 saw their own repeated request
change under concurrent load, with 19 distinct images in play so the test had the resolution
to detect a swap.

---

## 5. Decision already taken

**`/viewer` should mint a functional session cookie on first page load.**

Server-side ownership filtering is impossible for a visitor with no identity, which is D8.
The reasoning accepted: a cookie whose only job is keeping your own upload private to you is
strictly necessary to provide the service the user asked for, and strictly necessary cookies
do not require prior consent under ePrivacy. Consent continues to govern what is *recorded
about a person*: their email address, and whether their use is logged.

Consequences the refactor must handle:

- The consent dialog copy needs to distinguish the functional cookie from analytics consent.
  The dialog markup starts at `accessible-3d-viewer.html:526`; the inline script that decides
  when to show it, and that calls `initSessionModels`, runs at `:581-782`. The dialog is
  skipped entirely for `/workshop` and `?ui=simple` (`:591-593`).
- Two existing tests pin the opposite and must be updated to pin the new intent:
  `test_viewer_serves_html_without_cookie` and `test_viewer_creates_no_session_row`
  (`tests/test_session.py:185-196`).
- Do **not** copy the `arrive()` helper from abandoned commit `214c10d`. It assumed
  `GET /viewer` mints a cookie, which was false at the time it was written.

---

## 6. Requirements

Two requirements that are not defects in themselves but that the refactor must satisfy. The
second one constrains *how* per-session state may be stored, so read it before choosing a
storage mechanism in section 7.

### 6.1 Validate model references at the request boundary

**Requirement: every endpoint accepting a model reference must validate it explicitly at the
edge, and any fallback must be a deliberate, logged decision rather than a swallowed
exception.**

D4 exists entirely because this is missing. `static/js/viewer.js:391` sends the literal
string `"none"` on a viewer's first render. `_normalize_model_index` (`app/server.py:506`)
catches the `ValueError` at `:512` and returns `0`, with no error, no log, and no signal to
the caller that the request was nonsense. That silent `0` is then written to shared state at
`:1424` and resets every other session's model.

The bug is not the coercion. It is that an unparseable reference and a deliberate request for
model 0 are indistinguishable by the time anything acts on them.

What the resolver introduced in step 2 must do:

- **Declare the accepted wire types.** After step 2 the canonical form is the stem, a string.
  A bare integer index stays accepted for one release so a tab open across a deploy keeps
  working. Anything else is invalid input, not a request for model 0.
- **Distinguish the three cases**: a valid reference, a reference this session may not see,
  and malformed input. They currently collapse into one return value.
- **Make the fallback explicit.** Falling back to the caller's own first model is correct
  behaviour (it fails closed), but it should be a named branch that logs, not the bottom of
  an `except`.
- **Never let a malformed reference reach shared state.** This is what turned a client-side
  initialisation quirk into a cross-session defect.

Apply the same treatment to the other numeric parameters that arrive from the browser and are
currently coerced with silent defaults: `export_width` (`:2144`), `dotpad_cols` and
`dotpad_rows` (`:2217-2218`), `preview_width` (`:2186`), plus `depth` and `zoom` in
`_prepare_render_params`.

On tooling: **pydantic is not currently a dependency** of this project (not in
`requirements.txt` or `environment.yml`), so this requirement does not oblige you to add one.
Explicit parsing in the resolver satisfies it completely. If a schema library is introduced
later it should be a deliberate choice on its own merits, not a side effect of this fix.

### 6.2 Run a production server, and fix state ownership before scaling out

**Finding: production currently runs the Flask development server.** The container entrypoint
is `python -m app.server` (`Dockerfile:72`), which reaches
`app.run(debug=False, host="0.0.0.0", port=6969, threaded=True)` at `app/server.py:2289`.
There is no gunicorn, uvicorn, waitress or uwsgi in `requirements.txt` or `environment.yml`.
Werkzeug's development server is explicitly not intended for production use.

**Requirement: put a real WSGI server in front of the app.** That is a small change on its
own. The reason it belongs in this document is the ordering constraint below.

**Constraint: every global in section 1 is per-process, so adding workers multiplies them
rather than sharing them.** Under `--workers N` each worker gets its own copy of:

| Global | Consequence of splitting it |
|---|---|
| `AVAILABLE_MODELS` / `MODEL_NAME_LIST` (`:450`, `:451`) | model indices disagree *between workers*, so the same index means different files depending on which worker answers |
| `state` (`:364`) | current model differs per worker |
| `renderers_by_model` (`:365`) | duplicated renderers, N times the memory for the same models |
| `current_render` (`:366`) | `/render/image` returns whatever that worker last drew |
| `quantized_render_cache`, `preview_payload_cache` (`:380`, `:383`) | hit rate divided by N |
| `_sse_clients` (`:387`) | **a broadcast reaches only the tabs connected to the worker that sent it** |

That last row is the sharp one, and it cuts against section 4. The hardware broadcast
(`cube_value`, `slider_value`) is *supposed* to reach every station in the room. Under
multiple workers it would reach only a subset, and which subset would depend on how the load
balancer happened to distribute the SSE connections. A workshop would see some stations
follow the physical cube and others not.

Therefore, in order:

1. **Fix state ownership first** (section 7). Doing it in the other order turns the defects in
   section 3 from reproducible into intermittent and load-balancer-dependent, which is much
   harder to debug and much harder to prove fixed.
2. **Then add a production server, single worker with threads** (for example
   `gunicorn --workers 1 --threads 8`). This preserves today's concurrency semantics exactly,
   since `threaded=True` is already one process with many threads, so nothing in section 3
   changes behaviour.
3. **Only then consider multiple workers**, and only with the understanding that anything
   per-session can no longer live in a process-local dict. It needs the database, or a shared
   store, and the SSE registry needs a cross-process channel or sticky sessions.

**Practical consequence for step 5 of the refactor:** when you move current model and camera
position off the shared object, prefer keying them by session id in a structure that could
later be backed by the database, rather than by anything that assumes a single process. That
keeps the door to multi-worker open without requiring it now. Note also that
`render_lock` (`:373`) only serialises within a process, so multi-worker would allow genuinely
concurrent matplotlib calls, which is a separate correctness problem to solve before scaling.

---

## 7. Suggested refactor order

Each step is independently reviewable. Ordering minimises rework.

**Step 1: make export honour its argument.** `render_export_source` (`:2131`) should go
through `_prepare_render_params` the way `/render/preview` (`:2183`) does. Smallest change,
highest severity, no dependencies. Closes D3.

**Step 2: stable model identity.** Introduce a resolver that maps a client reference to a
`Path`, accepting the stem (the stable name) and, for one release, a legacy integer index so
a tab open across a deploy keeps working. Key `renderers_by_model` by resolved path string
rather than index. Closes D6, and defuses D1 by making a model nameable only if visible.

Client side: `option.value = i` (`static/js/viewer.js:1592`) becomes the stem, `currentModel`
holds a stem, and the `load_model` handler (`:1813-1821`) compares stems directly, which
removes the `indexOf` race in D7 entirely.

**Step 3: stop the browser path writing global state.** The browser sends an explicit model
on every request, so `/render` does not need `state.current_model_index = model_index`
(`:1424`). Closes D4 without touching the cookie question.

**Step 4: server-side ownership.** Gated on section 5. One function decides what a session
may see (built-ins plus own uploads); every path that resolves a client model reference goes
through it, so a model a visitor cannot list is one they cannot render, preview, export or
braille. Drop `model_paths` from `GET /models`. Unknown or stale references fall back to the
caller's own first model. Closes D1, D2, D8.

**Step 5: per-session camera.** Move `view_current_camera_center` off the shared renderer, or
require every render to carry its own camera state. Closes D5.

**Step 6: scope the ingest broadcast.** Give `_push_sse` an optional target so
`/ingest?open=1` reaches only the requesting station. Hardware events stay global, per
section 4. Closes D7.

**Step 7: housekeeping.** Ownership check on `/uploads/cleanup`; scope or remove
`/render/image` and `/render/base64`. Closes D11.

Not covered by any step, and worth a separate decision: D10, whether workshop ingest should
keep writing into the public directory at all.

---

## 8. The test suite

`tests/test_concurrent_sessions.py` is 19 tests: 8 passing guard rails and 11
`xfail(strict=True)`. Runs in about 4 seconds.

`strict=True` is the point. When a fix makes one of the 11 pass, the unexpected pass **fails
the build** and tells you to delete the marker. The suite is a checklist that closes itself.
Work through it top to bottom and finish when no `xfail` markers remain.

The `isolated_models` fixture is what makes any of this measurable. Read it before changing
it. Two traps it exists to avoid:

1. **`_MODEL_DIR_RESOLVED` (`app/server.py:453`) is computed once at import** and is what
   `_is_builtin` (`:466`) actually compares against. Patching `MODEL_DIR` alone leaves
   built-in classification pointing at the real directory and silently breaks every
   ownership assertion.
2. **Use explicit save-and-restore, not `monkeypatch.setattr`.** monkeypatch undoes its
   patches *after* fixture teardown, so a teardown that rebuilds `AVAILABLE_MODELS` rebuilds
   it against a temp directory that is about to be deleted, poisoning every later test in the
   session.

---

## 9. Traps that produced false results during this investigation

Recorded because a refactoring agent will hit the same ones.

**Two Flask test clients cannot both use the `with` form.** `with flask_app.test_client()`
keeps each request's context alive after the request returns, so interleaved requests pop
each other's contexts and Flask raises "Popped wrong request context". Use the plain
constructor when interleaving.

**pytest elides the middle of long strings.** A braille hex payload was read as "all zeros"
and dismissed as a blank frame. It was not: the same request returns 272 nonzero cells in
Outline and 3080 in Filled. Assert against a known-good comparison, never against
non-emptiness.

**Sort order is by full path.** An experiment used `aaa_first.stl` expecting it to take
position 0 ahead of `aaa_default.stl`. It does not: `d` precedes `f`. The experiment silently
never ran.

**Driving HTTP outside the page desynchronises client state.** Calling `/session/identify`
and `/upload` through Playwright's `context.request` never refreshes the page's
`sessionOwnedModels`, making all 22 uploaders falsely appear unable to see their own model.
Reload the page, or act through it.

**The braille frame is 40x96, so images collapse.** Synthetic boxes with the same proportions
render identically once fitted to the display, giving 5 distinct images across 24 users and
hiding any swap between users sharing an image. Use the shipped built-ins, which gave 19.
Use the bounding box, not the image, when the question is *whose model is this*.

**`tests/test_session.py` writes into the real `data/models` and `data/uploads`,** adding
roughly 10 models and 6 uploads per run. Both directories are gitignored, which is why this
went unnoticed; the host copy reached 594 and 42 files during this work. It matters here
because a growing sorted directory is the exact mechanism under study. Worth its own issue.

**Verify that render parameters actually reach the server** before trusting any comparison.
A form-encoded body makes `request.get_json` return `None`, every render falls back to
identical defaults, and an experiment comparing images reports independence it never
established. `scripts/multi_user_session_probe.py` has a self-check for this.

---

## 10. Corrections to the issue #123 description

- **Item 4 has the failure direction backwards.** An empty built-in set does not "show
  everything". `[]` is truthy in JavaScript, so `!builtinModelStems` at
  `static/js/viewer.js:1536` is false, the early return does not fire, and the dropdown
  collapses to `['No models found']` with `disabled=True`. The real exposure is D9.
- **Item 4's premise is out of date.** PR #128 made `UPLOAD_DIR != MODEL_DIR` a hard startup
  invariant (`app/server.py:183-190`), so uploads no longer share a folder with built-ins.
  D10 is what keeps participant files public.
- **Item 1 is now half true.** Uploads can no longer renumber a built-in. They still renumber
  each other.

---

## 11. Baseline measurements

Reproduce these after the fix. Every one should invert except the "already correct" rows.

| Measurement | Before fix |
|---|---|
| Cross-user pairs exposed by `GET /models` (24 users) | 504 / 506 |
| Cross-user pairs exposed in the dropdown, after settle | 0 / 506 |
| Users who rendered another user's exact geometry | 22 / 22 |
| Watchers moved by one user's pan (control: 0/8) | 8 / 8 |
| Users whose own repeated request drifted under load | 0 / 24 |
| Distinct images across 24 users (test sensitivity) | 19 |
| Anonymous uploaders who cannot see their own upload | 2 / 2 |
| Window where another session's upload is listed | 0.03 s to 4.88 s |
| `/health` `unexpected_public_models`, fresh container | 0 |
| `/health` after 4 workshop ingests | 4 |

Container baseline for comparison runs: 16 built-in models, 0 uploads, empty database,
`storage_separated: true`, status `ok`.

---

## 12. Not covered

- The fix itself. An abandoned first attempt exists at commit `214c10d` on
  `origin/fix/session-model-isolation`, with no open PR. Its premise (that `/viewer` mints a
  cookie) was false against master at the time. Treat as reference only.
- Issue #123 item 5, first-name collisions in `/workshop`. Two participants sharing a first
  name are treated as the same person by design; the docstring at `app/server.py:316`
  acknowledges it. Separate question.
- Deployment health checking. The container found running during this investigation predated
  PR #128, mounted 4 volumes with no `uploads` volume, and had its healthcheck pointed at
  the root rather than `/health`, so it reported healthy while `/health` returned 404.
  That is the failure mode the comment in `docker-compose.yml` warns about, and it is a
  separate issue from session isolation.
