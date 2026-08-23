# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### 2026-08-19

#### Added
*   The study spreadsheet now says which key was pressed, not just that a key was pressed. Counting how often people rotated, changed the rendering, or read their position back is now a matter of counting a column, where before it had to be guessed at by looking for changes in what was on the display between one drawing and the next. The viewer has always recorded this, so it fills in for the sessions that have already been run.
*   A held-down key is marked as such, so a key held to move continuously is not counted as a person pressing it fifty times.

#### Changed
*   Rendering modes are written out in one consistent spelling. Three of them used to come out with a capital letter and one without, which meant a spreadsheet grouping on that column could count the same mode twice. Anything reading the old spellings will need updating, and the two names that were used before a rename now come out as the modes they became.
*   X-Ray is reported as the fourth rendering mode it is, rather than being folded into one of the other three. The study script teaches three, but pressing R cycles through four, so a participant can be in X-Ray without having been told about it. It is left as its own value so that the choice of what to do about it is made in the open rather than settled inside the export.

### 2026-08-17

#### Added
*   Study logs can now be downloaded as a single spreadsheet, covering every session that has been completed, at `/study/export/long.csv`. It is an ordinary address that can be opened in a browser, with nothing to find or paste first. It is one row per interaction rather than one per session, with the time, the phase of the study, the model, the render mode and the orientation on each row, so counting how much of a task was spent in each render mode is a matter of sorting a column rather than writing a program. It is one request, so it can be run again unchanged each time another participant is added.
*   The download covers completed sessions only. A session still running is being written to as it is read, so the file would differ every time it was asked for, and a session that was abandoned stopped partway with nothing recording why. Asking for one of those by name says which of the two it is rather than returning an empty file, so that a participant is never quietly missing from the analysis.
*   That address asks for no token, on a deployment that requires one everywhere else in the panel. Worth knowing what that means: anyone who can reach the server and knows the address can download the interaction data of every completed session. That data is keys pressed, what reached the display, and timings, recorded under participant codes. Names, anything a participant said, and the questionnaire answers are not in it, because the application does not store them anywhere.
*   Orientation, which the viewer keeps as the directions it is looking along, is written out as a rotation in degrees about each of the three axes. The original directions are kept in a column of their own, so the one case where angles are ambiguous, an object pitched to straight up or straight down, can still be read exactly.

### 2026-08-11

#### Fixed
*   Sessions now close when the experimenter reaches the end of them. "Next" on the final step used to do nothing at all, which left the only way to close a session as a separate "End" button that the last step asks you to remember. On the two deployed servers, twelve of fourteen sessions were still sitting open, one of them nineteen steps into a twenty-two step protocol. "Next" on the last step now reads "Finish session" and closes the record, after asking first.
*   Sessions that were started and walked away from no longer claim to be in progress forever. A session with no activity for twelve hours is marked as abandoned, which is what it is. Nothing is deleted: it keeps every interaction it recorded, and it simply stops being offered to the next experimenter as a session running on that set of models. The wait can be changed with `STUDY_SESSION_IDLE_HOURS`.
*   An abandoned session is never counted as having used its models. Only a session someone actually finished does, so a run that was cut short leaves its combination free for the next participant rather than quietly dropping it out of the balanced design.

### 2026-08-10

#### Changed
*   The objects in the study have changed. The Lego brick is now one of the objects a participant is asked to explore and compare, rather than the warm-up it used to be, and the coat rack has been taken out. The three objects are now the Lego brick, the pencil holder and the cane tip, and each participant still gets two of them. The coat rack's models still ship with the app; they are simply not part of a session any more.
*   The experimenter now chooses which two objects a participant gets, instead of the panel deciding. Opening the control panel lists all six combinations, and picking one starts the session on it. This is the only thing the panel asks for before a session begins.
*   Combinations that have already been run are struck through in that list, and each one says in words that it has been run, so the list is as clear read aloud as it is on screen. It also says when a session is running on a combination right now, which is what stops two experimenters starting on the same one at the same time.
*   Once every combination has been run, the list comes back with all six available again as a new round. Nothing has to be reset, and nobody has to keep count: the list is worked out from the sessions that have actually been completed. A session that was started and then abandoned does not use up its combination, and a combination deliberately run twice is shown as run twice rather than quietly evened out.
*   A combination that has already been run can still be picked. It is marked, not blocked, so an experimenter can deviate when they have to, for a missing print or a participant who has seen one of the objects before.
*   Starting a session on an object the study no longer has is now refused outright. It used to be accepted with the unknown object silently dropped, which produced a session one task short, and the only sign of it was a second task that put nothing on the display.

### 2026-08-07

#### Added
*   A new `/study` page runs a study session from beginning to end. It is the same viewer participants are onboarded on, with the model list taken away and a small study area added at the top: the current step, how far through the session they are, and an "I am ready to move on" button. Models load themselves at each step, so nobody has to find one in a list while a participant waits.
*   An experimenter control panel at `/study/control` shows what to read aloud at each step, which printed model to hand over, and what actually differs between the two versions of the current object. Moving to the next step moves the participant's page and loads the next model onto their braille display. The two pages stay in step across two machines, so the experimenter can run the session from their own laptop.
*   Each run records the participant under a new participant ID, and every interaction with the application is recorded against it: which keys were pressed, what reached the braille display, what the screen reader was told, when each step began, and how long everything took. This goes to its own database, kept apart from the usage database so that ordinary maintenance can never reach a session that has already been run.
*   Alongside the database, each session is written to its own log file, one line per event, each line carrying the full state of the viewer at that moment. It is written independently of the database, so a session can still be reconstructed if the database write is the thing that failed.
*   The control panel says whether recording is actually working while the session is running. Previously the only way to find out that data had not been captured would have been to look for it afterwards.
*   Each participant explores a practice Lego brick, then two of the three model pairs. Which two, and in which order, follows a fixed balanced design rather than being drawn at random, which at this number of participants would routinely produce a lopsided set. The panel shows the whole assignment table, so it is clear in advance which objects to have printed and ready.
*   Every model loads into the same starting state: the object upright, cut through the middle, zoomed out and centred. The second version of an object therefore starts exactly where the first did, so what a participant notices is a difference between the models rather than between viewpoints.
*   Session data can be downloaded from the control panel as a single file, so analysis does not begin with getting a database off the server.
*   Running a session is now: open the panel, read two things out. Opening `/study/control` starts a session straight away — there is no form to fill in first, because the participant number, the model pairs and the session number are all decided by the protocol. The panel then shows the address for the participant to open and a four-character code for them to enter, and that is the whole setup.
*   The panel no longer needs a secret. It used to be reachable only with a token that had to be found in the server's own log, which was the most confusing part of running a session. Anyone who can reach the address can now open it. A deployment that wants the old behaviour can still require a token by setting one.
*   The code the participant enters is asked for every time, whether one session is running or several, so there is a single instruction to give rather than one that changes with circumstances. It avoids letters and numbers that sound or look alike, since it is usually read out to someone who cannot see the screen.
*   Each browser tab of the panel runs one session, so two people can each open the panel and run a participant at the same time without affecting each other.
*   Sessions can be run with only one computer. "Run the study on this device" hands the window over to the participant, and from then on the session moves to the next step when they say they are ready, instead of waiting for a control the experimenter can no longer reach without taking the screen reader away from them. What the participant sees is the ordinary participant view, unchanged, and the session is recorded exactly as it would be on two machines. Keep the protocol to hand, as the script is not shown on that page.

*   Each step tells the experimenter what to do, what to say, and what to ask, labelled separately rather than run together as one block of text to interpret mid-session. Spoken lines are shown in quotes; actions and reminders are not.
*   The background questions, the rating scale after each object, and the closing discussion questions are all in the panel to be read from, so there is no second document to keep open. They are asked out loud and the answers are written on the experimenter's own sheet: the panel offers nowhere to type them and records none of them.

*   Two experimenters can now run participants at the same time on the same server. Sessions are independent: each keeps its own place in the protocol, loads its own models, writes its own log, and drives only its own participant's page. The panel says when another session is already running, and warns if more than one participant view is connected to yours, since that would record every interaction twice.

The participant is never shown the model's name, since a name like "2x4 brick" answers the question they are being asked to work out by touch. Consent is not part of the session: it is given beforehand, so the first step is about settling the participant in and the second about setting up the machine and the display.

### 2026-07-28

#### Fixed
*   The model list no longer empties itself. It appeared fully populated, then collapsed to "No models found" and became unselectable a moment after the page settled, leaving no way to choose a model. It now stays populated and usable.
*   The models that ship with the app are the ones you actually get. A Docker deployment was serving a different set from the one in the project, so familiar models such as the mug, cane tip, rocking chair and lego bricks were missing.
*   A model you add to the project now reaches a server that has been running for a while. Previously new models only ever appeared on a freshly created deployment.

#### Changed
*   Models you upload are now kept separately from the ones that ship with the app, which is what makes it possible to show them only to you. Uploads previously shared a directory with the built-in models, so the app could not tell the two apart.
*   An upload named after a built-in model is stored under a slightly different name instead of shadowing it, so both stay reachable.
*   Added a maintenance script that reports, and on request removes, files left in the built-in model directory by earlier versions. See the README section on where models are stored.
*   A deploy that does not come up now fails instead of reporting success. The pipeline finished as soon as the container was created, so a site returning an error looked like a healthy release and was found by a person rather than by the deploy.
*   The deployment guide now covers where data is stored, how to back it up and restore it, and what to check when the site is unreachable.
*   Added a script that checks a running server against the problems that have actually caused outages here, so the state of a deployment can be confirmed rather than assumed.

### 2026-07-23

#### Changed
*   The simplified workshop viewer now offers the Zoom controls, so a participant can zoom in on part of a model and feel its detail on the braille display.
*   The simplified workshop viewer no longer shows the output-device chooser. The braille display connected at the station already receives the model, so the choice was redundant and could be set wrong.
*   The simplified workshop viewer now opens on the y+ view in X-Ray rendering mode, so a session starts from the orientation and rendering participants work with. The full viewer is unchanged and still opens on x+ in Filled.

### 2026-07-22

#### Changed
*   Uploaded models, the usage database, and render and log output are now stored in Docker-managed volumes rather than in a folder on the server. This makes deployments work without depending on file permissions being configured just so on the university file share, which is what had been breaking them.
*   Because of that move, this data is no longer in its old location on the server and is not covered by whatever backs that location up. `docker compose down -v` now erases uploaded models and the usage database. The deployment guide explains how to back them up and restore them.

### 2026-07-15

#### Added
*   Another tool can now send a 3D model straight into cad-a11y. A new endpoint receives an STL file, together with the participant's first name, and returns a link that opens the model in a workshop-ready viewer.
*   A simplified workshop viewer at `/workshop` shows only the controls that matter during a session: View, Depth, Rendering Mode, the output-device selector, and the Monarch and DotPad connection panels, alongside the tactile preview.
*   Participants open their model by entering their first name on an accessible page. Each participant is given a stable id, so every model they send is saved and their in-app actions are recorded together, while only their most recent model is shown. No email address or account is required.

### 2026-07-13

#### Changed
*   The first-visit consent dialog now decides what is stored *before* anything is saved. No session cookie or record is created until you respond to the dialog, and dismissing it with the Escape key stores nothing at all. Previously a cookie was set the moment the page loaded, before you had made a choice.
*   The consent dialog is clearer about what each choice does. The buttons are now "Allow analytics" and "Don't track me" (previously "Accept & Continue" and "Continue without email", which behaved almost identically when no email was entered), and sharing your email is now independent of the analytics choice.

#### Fixed
*   Entering an invalid email address in the consent dialog now shows an inline error and keeps the dialog open, instead of quietly failing and re-showing the dialog on your next visit.

### 2026-06-29

#### Added
*   Cross-device model access. Providing the same email address on a new browser or device immediately shows all models uploaded in previous sessions — no re-uploading needed.

#### Fixed
*   First-time deployments failed with "unable to open database file" because the database directory was absent from the image. The container now starts cleanly on a fresh deploy.

### 2026-06-28

#### Added
*   Integration tests covering upload persistence, model deletion, and cross-session model access were added to the test suite.

#### Fixed
*   The model dropdown was showing uploads from other active sessions. Now only built-in models and your own uploads are visible.

### 2026-06-27

#### Added
*   Uploaded models reappear in the model dropdown when you return to the viewer. Your uploads are now saved to the database under your session and restored automatically on every subsequent visit.
*   A "Remove uploaded model" button permanently deletes any of your uploads. The model disappears from the dropdown immediately and does not return on future visits.

### 2026-06-26

#### Added
*   An accessible consent dialog appears on first visit. You can optionally provide your email to enable cross-device model access and opt in to usage analytics. Both choices are remembered for future visits.
*   Integration tests covering all session persistence and analytics endpoints were added to the test suite.

### 2026-06-25

#### Added
*   Session data (uploaded models, visit history, consent) is now stored in a SQLite database. Sessions persist across browser restarts and server redeployments.
*   A session cookie is set on first visit and used to associate uploads and preferences with future visits. Five new API endpoints support reading session state, updating identity, managing uploaded models, and recording interaction events.
*   Render analytics are recorded per render call (view, render mode, depth, zoom, layout, input source). Client-side events (section dwell, keyboard shortcuts, device connections) are collected when consent is given.
*   Operators can now set `DATA_DIR` in `.env` to point all persistent data (uploaded models, database, renders, logs) to a network share. Data survives container redeployments without any manual backup step. See `.env.example` for details.

#### Changed
*   The container now runs as UID 48 (apache user) to match the write permissions granted by university NFS servers. Named Docker volumes have been replaced with `DATA_DIR`-driven bind mounts.

### 2026-06-24

#### Fixed
*   Tags pushed to the GitHub repository were silently dropped and never forwarded to the GitLab deployment mirror. Tags are now correctly propagated.

### 2026-06-23

#### Added
*   OSS contribution infrastructure: branch protection rules, pull request template, and issue templates for bugs, accessibility reports, and feature requests.
*   GitHub Actions CI pipeline running lint, type checks, and the full test suite on every pull request.
*   Automated deployment mirror from GitHub to the UW GitLab instance via GitHub Actions.
*   Deployment documentation covering Docker setup, environment variables, and first-run instructions (`docs/DEPLOYMENT.md`).
*   Dependabot for automated dependency updates across pip, conda, and GitHub Actions.
*   Stale issue and pull request automation to keep the backlog manageable.
*   Conventional Commits enforcement on pull request titles via GitHub Actions.
*   Integration test scaffolding using the Flask test client with pytest.

#### Changed
*   Upgraded runtime from Python 3.9 (end-of-life) to Python 3.12.
*   Updated all dependencies unlocked by the Python upgrade: `flask-cors >=5.0.1`, `numpy >=2.5.0`, `requests >=2.34.2`, `bleak >=3.0.2`, `ruff >=0.14.14`, `mamba-org/setup-micromamba v3`, `actions/github-script v9`.

#### Fixed
*   A broken pip install step in the Dockerfile silently swallowed errors for all packages, meaning the container could start without Flask installed. The `|| true` fallback now covers only the optional packages (polyscope).
