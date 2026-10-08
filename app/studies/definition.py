"""What a study is: one definition per study, kept in the repository.

A definition says what a session consists of (its steps, its tasks and the order
participants get them in), how the viewer starts, where its data is kept, and
whether the server may run it at all. It is code rather than a file on the
server so it is reviewed like code, versioned with the viewer it ran on, and
covered by the tests. Nobody has shell access to the servers, so a definition
the server reads from its own disk would also be one nobody could check.

Status
------
A study moves through four states, and the server's behaviour follows from the
state alone:

  draft    being written. Not served, except on a machine that names it in
           ``CAD_A11Y_OPEN_STUDIES``, which is for development and CI.
  open     collecting data. The participant page, the control panel and the
           exports are served.
  closed   finished collecting. The control panel and the exports are served,
           so the data can be downloaded without shell access; no session can
           start and the participant page is gone.
  retired  nothing is served. The definition stays as the record of what ran,
           and its data stays where it was until someone removes it. The
           command line can still export it.

Moving a study from one state to the next is a one-line change to its
definition, reviewed and deployed like any other.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# Lowercase words joined by single hyphens. It becomes a URL path segment and a
# file name, so nothing that needs escaping in either.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX_LENGTH = 40


class Status(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"
    RETIRED = "retired"


def repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Storage:
    """Where one study's data lives: a SQLite database and a directory of
    per-session JSONL logs.

    The two sit on different Docker volumes, ``db`` and ``logs``, which is where
    the deployment already keeps (and backs up) everything durable. A new volume
    would be one the backup instructions, the entrypoint's ownership repair and
    the deploy check would all have to learn about first.
    """

    db_path: Path
    log_dir: Path


def studies_db_dir() -> Path:
    raw = os.environ.get("STUDIES_DB_DIR", "").strip()
    return Path(raw) if raw else repo_root() / "data" / "db" / "studies"


def studies_log_dir() -> Path:
    raw = os.environ.get("STUDIES_LOG_DIR", "").strip()
    return Path(raw) if raw else repo_root() / "data" / "logs" / "studies"


def default_storage(slug: str) -> Storage:
    return Storage(db_path=studies_db_dir() / f"{slug}.db", log_dir=studies_log_dir() / slug)


@dataclass(frozen=True)
class Study:
    """One study.

    ``steps`` is the protocol: a list of dicts, each one screen of the session.
    See ``protocol.py`` for what a step may carry and ``definitions/example.py``
    for one of each.

    ``tasks`` are what a participant is given to work on, keyed by a short name.
    Each may carry a ``label`` and ``description`` for the experimenter, one
    entry per model role (``"a"``, ``"b"``, ...) naming the model and the printed
    object to hand over, and an answer key (``differences``, ``unchanged``) the
    panel shows and the participant never receives.

    ``design`` is the counterbalancing: one row per cell, each an order of task
    keys. A session runs the first ``tasks_per_session`` keys of the row the
    experimenter picks (all of them when it is 0). ``protocol.latin_square`` and
    ``protocol.balanced_latin_square`` build the usual ones.

    ``token_hash`` gates the control panel and the exports. A study the server
    would serve without one is refused at load, never served open.
    """

    slug: str
    title: str
    status: Status
    version: str
    summary: str
    steps: list[dict[str, Any]]
    token_hash: str | None = None
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    design: tuple[tuple[str, ...], ...] = ()
    tasks_per_session: int = 0
    viewer_defaults: dict[str, Any] = field(default_factory=dict)
    # What the participant hears for a model, by role. The model's real name
    # would answer the question the participant is working out by touch.
    model_labels: dict[str, str] = field(default_factory=dict)
    facilitator_prompts: list[str] = field(default_factory=list)
    strategy_prompts: list[str] = field(default_factory=list)
    # Overrides the per-slug default. Only the comparison study needs it: its
    # data was written before studies had directories of their own.
    storage: Callable[[], Storage] | None = None
    # The git tag of the last code that ran this study, once it is retired.
    instrument_tag: str | None = None

    def resolve_storage(self) -> Storage:
        return self.storage() if self.storage else default_storage(self.slug)

    def session_tasks(self, row: tuple[str, ...] | list[str]) -> list[str]:
        keys = [str(key) for key in row]
        return keys[: self.tasks_per_session] if self.tasks_per_session else keys


class DefinitionError(ValueError):
    """A definition that cannot be served as written."""


def validate(study: Study) -> list[str]:
    """Everything wrong with a definition, as sentences. Empty when it is fine.

    Run on every definition at load and by the test suite, so a broken one fails
    a pull request rather than a session.
    """
    problems: list[str] = []
    if not SLUG_PATTERN.match(study.slug) or len(study.slug) > SLUG_MAX_LENGTH:
        problems.append(
            f"slug {study.slug!r} must be lowercase letters and digits joined by "
            f"single hyphens, at most {SLUG_MAX_LENGTH} characters"
        )
    if study.status not in tuple(Status):
        problems.append(f"status {study.status!r} is not one of {[s.value for s in Status]}")
    if not study.version:
        problems.append("version is empty; every session records it")
    if not study.steps:
        problems.append("the protocol has no steps")

    seen: set[str] = set()
    for index, step in enumerate(study.steps):
        step_id = step.get("id")
        if not step_id:
            problems.append(f"step {index} has no id")
        elif step_id in seen:
            problems.append(f"step id {step_id!r} is used twice")
        else:
            seen.add(step_id)
        ref = step.get("model")
        if isinstance(ref, dict) and ref.get("kind") == "task":
            slot = ref.get("slot")
            longest = max((len(study.session_tasks(row)) for row in study.design), default=0)
            if not isinstance(slot, int) or not 1 <= slot <= max(longest, 1):
                problems.append(f"step {step_id!r} loads task slot {slot!r}, which no row fills")

    for row in study.design:
        unknown = [key for key in row if key not in study.tasks]
        if unknown:
            problems.append(f"design row {list(row)} names unknown tasks {unknown}")
        if len(set(row)) != len(row):
            problems.append(f"design row {list(row)} repeats a task")
    if study.tasks and not study.design:
        problems.append("tasks are defined but the design has no rows to assign them")
    if study.tasks_per_session < 0:
        problems.append("tasks_per_session cannot be negative")
    return problems
