# Studies

How to define a study, run its sessions, get its data out, and retire it. For
what the exported columns mean, see [STUDY_DATA_EXPORT.md](STUDY_DATA_EXPORT.md).

A study is a guided session on the real viewer. The participant uses the ordinary
viewer with a study region at the top, and models load themselves at each step.
The experimenter drives the session from a control panel, usually on a second
machine, with the script for each step, what to hand over, and the answer key.
Every keypress, render and step change is logged against the participant's
code.

## Where things are

| | |
|---|---|
| `app/studies/definitions/` | One file per study, plus `__init__.py`, which lists them all |
| `app/studies/definitions/example.py` | A short study to copy when starting a new one |
| `app/studies/definitions/comparison_2026.py` | The comparison study, closed until its data is stored |
| `/studies/<slug>` | The participant's page |
| `/studies/<slug>/control` | The control panel |
| `python -m app.studies` | The command line: list studies, make tokens, check and export data. It runs in the project's conda environment (`environment.yml`) and needs nothing else |

## Status

Every study has one of four statuses, set in its definition. The server serves
exactly what the status allows, and changing a status is a change to the
repository, reviewed and deployed like any other. The one way round it is
`CAD_A11Y_OPEN_STUDIES`, which opens a draft for piloting on a development
machine. No server sets it, and the example study needs a second variable as well
(see [Development and CI](#development-and-ci)).

| Status | Participant page | Control panel | Data downloads | New sessions |
|---|---|---|---|---|
| `draft` | no | no | no | no |
| `open` | yes | yes | yes | yes |
| `closed` | no | yes | yes | no |
| `retired` | no | no | no | no |

* **draft** is for writing a study. A development machine can serve one by naming
  it in `CAD_A11Y_OPEN_STUDIES`. No server sets that variable.
* **open** is for collecting data.
* **closed** is for after collection. The panel offers only the downloads, so the
  data can come off a server nobody has shell access to. Its database is only
  read: never created, migrated or written. A server that does not have it
  answers every download with "This server has no data for this study."
* **retired** is for after the data has been exported, checked and stored. The
  study's addresses answer exactly as a study that never existed would. The
  definition stays, because it is the record of what every session in the data
  was asked to do, and the command line can still export the data.

A study the server should serve is refused at start-up, and the reason logged,
if its definition is invalid, if it has no token, if its token is the example's,
if it is the example opened without `CAD_A11Y_ALLOW_EXAMPLE_STUDY=1`, or if its
database cannot be prepared. Refusing one study never keeps the viewer from
starting.

## Starting a new study

1. Copy `app/studies/definitions/example.py` to a new file, and add its `STUDY`
   to the list in `definitions/__init__.py`.
2. Give it a new `slug` (lowercase words joined by hyphens, which becomes its
   address), a `title`, a `version`, and a one-line `summary` for the panel.
3. Make a token, and put the hash it prints in `token_hash`:

   ```bash
   python -m app.studies token
   ```

   Give the token to the experimenters privately. It is not stored anywhere, and
   the hash is safe in the repository. Copying the example's hash does not work:
   that token is published, and any other study using it is refused.
4. Write the steps and tasks (below). Run the tests: `tests/test_studies_definitions.py`
   checks every definition, and says what is wrong with one.
5. Try it locally:

   ```bash
   CAD_A11Y_OPEN_STUDIES=<slug> docker compose up --build
   ```

   then open `http://localhost:8635/studies/<slug>/control`.
6. When it should run on the servers, set `status=Status.OPEN` in a pull request.
   Staging serves it once that merges; production, once it is in a release.

## Writing steps

`steps` is a list, one entry per screen of the session, in order. A step can
carry:

| Field | What it is |
|---|---|
| `id` | Unique within the study. It is the `step_id` column of the export |
| `part_id`, `part_title` | Which part of the session it is in. `part_id` is the `phase` column, which is what keeps practice from being counted as task work |
| `title` | Shown to both the experimenter and the participant |
| `participant_text` | What the participant's page says. Nothing else in the step reaches the participant's browser |
| `script` | For the experimenter: a list of blocks, below |
| `model` | What loads on the display. A step with no model leaves the display as it was |
| `physical_model` | What to hand over |
| `checklist` | What to have done before moving on |
| `task_slot` | Ties a step without a model, such as a questionnaire after a task, to that task's answer key |
| `facilitator_prompts`, `strategy_prompts` | Override the study's own for this step |

A script block is one of four kinds, made with the helpers in `protocol.py`. The
panel labels each one, so a screen reader announces whether to read it out or do
it.

| Helper | Kind | Means |
|---|---|---|
| `say(...)` | Say | Read this to the participant. Shown in quotes |
| `do(...)` | Do | An action the experimenter performs |
| `ask(..., questions=[...])` | Ask | Questions to ask aloud, answered on the experimenter's own sheet |
| `note(...)` | Note | Context or a reminder. Never spoken |

A model is a reference rather than a file name, because which model a step loads
can depend on which tasks the participant was given:

| Reference | Loads |
|---|---|
| `fixed_model("mug")` | Always `mug` |
| `task_model(1)` | Role `"a"` of the participant's first task |
| `task_model(2, "b")` | Role `"b"` of their second task |

Every model a study loads must ship in `builtin_models/`. The tests check this.

## Tasks and counterbalancing

`tasks` are what participants work on, by key. Each has a `label` and an optional
`description` for the experimenter (a script can say `{label}` or
`{description}`), and one entry per role naming its `model`, a `label`, and the
`physical` object to hand over. A comparison task has two roles, `"a"` and
`"b"`, and an answer key: `differences` and `unchanged`, shown in the panel and
never sent to the participant. A task to explore on its own has one role.

The participant never sees a model's name. The status bar and every announcement
use the study's `model_labels` instead, such as "First object", because a name
like `lego_2x4` answers the question the participant is working out by touch.

`design` is the counterbalancing: one row per cell, each an order of task keys. A
session runs the first `tasks_per_session` keys of its row, or all of them when
that is 0. Two helpers build the usual designs:

* `latin_square(keys)`: every task once in every position.
* `balanced_latin_square(keys)`: the Williams design. Every task is in every
  position equally often and follows every other task equally often, so a
  carryover from one task to the next is balanced too. An odd number of tasks
  needs twice as many rows, which it returns.

The panel opens on a list of the rows. Rows already run in the current round are
struck through and said to be used, and the round starts again once every row
has been run. An experimenter can pick a used row when they have to; the list
keeps counting what was actually run.

## Running a session

1. Open `/studies/<slug>/control` and sign in with the study's token. The
   sign-in lasts the working day in that browser.
2. Choose the task set. The panel shows the participant's code (P01, P02, ...)
   and a four-character join code.
3. The participant opens `/studies/<slug>` in Chrome, which is what connects
   their braille display, and enters the join code.
4. Work through the steps. N moves on and loads the next model on the
   participant's display; H lists every command. The panel says when the
   participant presses "I am ready to move on", and says in words if logging
   stops working.
5. N on the last step finishes the session and records it as completed. End
   before the last step records it as not finished (abandoned), and its task set
   stays available for another participant; the panel says which before it asks.

One panel tab owns one session. A reload stays on it, and a new tab starts
another, which is how two experimenters run participants at once.

With one computer, choose **Run the study on this device**. That window becomes
the participant's page, and the session moves on when they press "I am ready to
move on". The script is not shown there, because that is where the participant's
screen reader is.

A session nobody ends is closed as abandoned after twelve hours without activity
(`STUDY_SESSION_IDLE_HOURS` changes this), while the study is open. A closed study
ends nothing: a session it left active stays active, and its rows are in
`long_incomplete.csv`.

## What is recorded

Interactions and their timings, under the participant's code: keypresses,
renders, step changes, model loads, readiness signals, announcements and device
connections. Each session also records the study's version, a fingerprint of the
protocol it ran, and the release of the app.

Not recorded: anything the participant says, the experimenter's notes, or
questionnaire answers. Those go on the experimenter's own sheet. A study that
needs to keep answers in the app needs that in its consent first.

Each study has its own database and logs, on the same Docker volumes as the rest
of the app's data, so the existing backup instructions in
[DEPLOYMENT.md](DEPLOYMENT.md) cover them:

* `data/db/studies/<slug>.db`
* `data/logs/studies/<slug>/<participant>_S<n>_<date>.jsonl`, one line per
  interaction with the full viewer state. This is the authoritative record if the
  two disagree, and it is written even when the database write fails.

## Getting the data out

The panel's **Data** section, shown whenever no session is running in that tab,
has the downloads. **Everything, as one zip** is the one to keep:

| File | What it is |
|---|---|
| `study.db` | A consistent copy of the database, including anything still in its write-ahead log |
| `logs/` | Every session's JSONL log |
| `long.csv` | One row per interaction, completed sessions only |
| `long_incomplete.csv` | The same columns for the sessions `long.csv` leaves out that nothing is still writing to: the abandoned ones, and once a study is closed, the ones left active. Kept apart so that using them is a decision |
| `long.json` | The codebook for both |
| `sessions.csv`, `sessions.json` | One row per session, every status, and its codebook |
| `checks.json` | Known problems looked for, and what was found |
| `manifest.json` | What this is, the counts, and a SHA-256 for every file |

The codebooks describe every column, with its levels and units, in the shape of
a BIDS sidecar. Any text cell a spreadsheet would run as a formula starts with a
`'`; remove it to get the recorded value.

The checks look for sessions ended twice, participant codes with no session,
sessions marked ended with no end recorded, rows recorded after the end, order
numbers used twice, logs that disagree with the database, steps whose object
never reached the display, and sessions the export could not read. They also
list, without counting them as problems, the sessions still active and the
sessions recorded before the orientation fix (#185), whose depths in three views
read the other way round; the codebook and README say so too when there are any.
Each finding names the sessions it found, so it can be traced rather than only
counted.

From a terminal, `scripts/download_study_data.sh` downloads the zip with the
token, and refuses to save anything that is not one:

```bash
STUDY_TOKEN=... HOST=prod scripts/download_study_data.sh <slug>
```

The command line does the same from a copy of the data, such as a restored
backup, without changing it:

```bash
python -m app.studies check <slug> --db path/to/study.db --logs path/to/logs
python -m app.studies export <slug> --db path/to/study.db --logs path/to/logs --out <slug>.zip
```

Both exit 1 when the checks find a problem, and 2 when they cannot run, such as a
`--logs` folder that is not there.

## Ending a study

1. Set its status to `closed` once the last session has run. The panel stays, with
   only the downloads.
2. Download the zip from each server that ran sessions. Run the checks, compare
   the manifest's session count with the experimenters' own records, and store
   the zip where the IRB protocol says study data lives.
3. Set its status to `retired`, and set `instrument_tag` to the git tag of the
   last commit that served it, so the exact code can be found again.

Retiring a study removes nothing. Its data stays on the volumes until someone
deletes it there deliberately.

## The comparison study

The comparison study ran in 2026 at `/study`, and the last code that served it
is tagged `study-instrument-2026`. Its definition is
`app/studies/definitions/comparison_2026.py`, unchanged since it ran except for
its status and naming Turn as its axis mode, the only one there was then. Two
things it cannot settle: the code that served it read
`data/study/protocol.json` instead of the built-in protocol when that file
existed, and its sessions carry no protocol fingerprint, which came later.

It is closed, so `/study` is gone and only its data downloads are served, behind
its own token. Its data is where that code wrote it, from before studies had
directories of their own: `data/db/study.db` and `data/logs/study/`, on both
servers. Nobody on the team has shell access to them, so it comes off them
through the app:

1. Download the zip from `/studies/comparison-2026/control` on staging, which
   serves it once this change is on `master`, and on production after the next
   release. The zip reads a database from before #185 as it is, without changing
   it. Most of its sessions were left active when the panel was closed, so expect
   them in `long_incomplete.csv` rather than `long.csv`, with `step_index` in
   `sessions.csv` saying how far each got.
2. Check both: run the data checks, and compare each manifest's session count with
   the experimenters' records. Store them where the IRB protocol says study data
   lives.
3. Set its status to `retired` and remove its `token_hash`. Until then the
   downloads stay up behind the token, which costs nothing but the token's
   secrecy.

Where there is shell access, the command line does the same without a deploy:

```bash
docker compose exec app conda run --no-capture-output -n cad-a11y \
    python -m app.studies export comparison-2026 --out /tmp/comparison-2026.zip
docker compose cp app:/tmp/comparison-2026.zip .
```

## Development and CI

* The example study's panel token, `example-panel-token`, is published in its
  definition so it works out of the box. That is why naming it in
  `CAD_A11Y_OPEN_STUDIES` is not enough: it also needs
  `CAD_A11Y_ALLOW_EXAMPLE_STUDY=1`, which `docker-compose.yml` never passes, so a
  server cannot run it. On a development machine:

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.example-study.yml up --build
  ```

* CI's accessibility job opens the example through the same file and checks the panel
  before sign-in, choosing a set and running a session, and the participant page
  waiting for a code and in a session.
* `STUDIES_DB_DIR` and `STUDIES_LOG_DIR` move where studies keep their data; the
  test suite points them at a temporary folder.
