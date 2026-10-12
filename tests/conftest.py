"""Keep the test suite out of the repo's real data/ folder.

app.server decides where it keeps models, uploads, logs, databases and the slice
cache when it is first imported, and each of them defaults to the repo's own
data/ directory. The suite uploads and ingests models, renders and logs, so every
run used to leave its fixture models behind (by the thousand, filling the
/viewer model list with ingest_* stems) and append to the real braille log and
usage database.

This points all of them at one temporary directory per run. It is set here
because pytest imports conftest.py before any test module imports the app, and
it is set unconditionally: a developer's own settings pointing at real data are
exactly what must not reach a test run. A test that checks a default location
clears the variable it cares about itself.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

SCRATCH = Path(tempfile.mkdtemp(prefix="cad-a11y-tests-"))
atexit.register(shutil.rmtree, SCRATCH, True)

DATA_ENV = {
    "CAD_A11Y_MODEL_DIR": str(SCRATCH / "models"),
    "UPLOAD_MODEL_DIR": str(SCRATCH / "uploads"),
    "CAD_A11Y_PRECOMPUTE_DIR": str(SCRATCH / "precompute"),
    "BRAILLE_LOG_PATH": str(SCRATCH / "logs" / "braille_send_events.jsonl"),
    "DB_PATH": str(SCRATCH / "db" / "usage.db"),
    "STUDY_DB_PATH": str(SCRATCH / "db" / "study.db"),
    "STUDY_LOG_DIR": str(SCRATCH / "logs" / "study"),
}
os.environ.update(DATA_ENV)

# The empty folders a fresh checkout has under data/ (each holds a .gitkeep), so
# the app starts from the layout it would really find. /health opens the usage
# database directly and reports it broken when its folder is missing, which a
# checkout never is.
for folder in ("models", "uploads", "logs", "db"):
    (SCRATCH / folder).mkdir()


@pytest.fixture
def data_env():
    """The same folders, for a test that starts the app in a subprocess with an
    environment of its own. Without them that import seeds the built-in models
    into the repo's data/models."""
    return dict(DATA_ENV)
