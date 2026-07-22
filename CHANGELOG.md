# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
