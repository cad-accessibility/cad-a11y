"""Which studies this process serves.

``load`` reads every definition, decides from its status which ones this
process serves and how, and refuses any that cannot be served safely. The
answer is fixed at start-up: changing what a server runs is a change to a
definition, deployed like any other.

A study is refused, and logged as refused, when it

* fails ``definition.validate``,
* has no ``token_hash``, or one that cannot be checked. An open panel is what
  this replaced, so there is no such thing as a study served without a token;
* uses the example's published token. Copying ``example.py`` without making a
  new token would otherwise open a study whose password is in the repository.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import tokens
from .definition import Status, Study, validate
from .definitions import ALL, example
from .store import StudyStore

OPEN_STUDIES_ENV = "CAD_A11Y_OPEN_STUDIES"

# How long one sign-in to a panel lasts. A working day of sessions, so an
# experimenter signs in once in the morning rather than once per participant.
PANEL_SESSION_SECONDS = 12 * 3600


@dataclass
class StudyRuntime:
    """One served study: its definition, its store, and what serving it needs
    to remember between requests."""

    study: Study
    store: StudyStore
    served_as: Status
    # One queue per connected browser, for the Server-Sent Events fan-out.
    subscribers: list[dict[str, Any]] = field(default_factory=list)
    subscribers_lock: threading.Lock = field(default_factory=threading.Lock)
    # Transient, per-step readiness signals, by session id. Kept in memory rather
    # than in the session row because it is a notification about the current
    # step, not session state, and it must not survive a step change. The
    # durable record is the logged event.
    ready_signals: dict[int, dict[str, Any]] = field(default_factory=dict)
    last_sweep_at: float = 0.0
    sweep_lock: threading.Lock = field(default_factory=threading.Lock)
    # Signed-in panels: cookie value to expiry. In memory, so a restart signs
    # everyone out, which costs one more paste of the token.
    panel_sessions: dict[str, float] = field(default_factory=dict)
    panel_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def slug(self) -> str:
        return self.study.slug

    @property
    def is_open(self) -> bool:
        return self.served_as is Status.OPEN

    def start_panel_session(self) -> str:
        value = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.panel_lock:
            # Expired entries are dropped here rather than by a timer: sign-ins
            # are rare, and this keeps the table from growing without one.
            for key in [k for k, expiry in self.panel_sessions.items() if expiry <= now]:
                del self.panel_sessions[key]
            self.panel_sessions[value] = now + PANEL_SESSION_SECONDS
        return value

    def panel_session_valid(self, value: str | None) -> bool:
        if not value:
            return False
        with self.panel_lock:
            expiry = self.panel_sessions.get(value)
            if expiry is None:
                return False
            if expiry <= time.monotonic():
                del self.panel_sessions[value]
                return False
            return True

    def end_panel_session(self, value: str | None) -> None:
        if value:
            with self.panel_lock:
                self.panel_sessions.pop(value, None)


_runtimes: dict[str, StudyRuntime] = {}
_runtimes_lock = threading.Lock()


def opened_by_environment(environ: dict[str, str] | None = None) -> set[str]:
    """The draft slugs this machine was told to serve."""
    raw = (environ if environ is not None else os.environ).get(OPEN_STUDIES_ENV, "")
    return {slug.strip() for slug in raw.split(",") if slug.strip()}


def serving_status(study: Study, opened: set[str]) -> Status | None:
    """How this process serves a study, or None when it does not."""
    if study.status is Status.OPEN:
        return Status.OPEN
    if study.status is Status.CLOSED:
        return Status.CLOSED
    if study.status is Status.DRAFT and study.slug in opened:
        return Status.OPEN
    return None


def refusal(study: Study) -> str | None:
    """Why a study cannot be served, or None when it can."""
    problems = validate(study)
    if problems:
        return "; ".join(problems)
    if not study.token_hash:
        return "it has no token_hash. Make one with: python -m app.studies token"
    if not tokens.is_well_formed(study.token_hash):
        return "its token_hash is not one python -m app.studies token made"
    if study.slug != example.STUDY.slug and tokens.verify(example.TOKEN, study.token_hash):
        return (
            "its token is the example study's, which is published. Make a new one with: "
            "python -m app.studies token"
        )
    return None


def find_definition(slug: str, definitions: Iterable[Study] = ALL) -> Study | None:
    return next((study for study in definitions if study.slug == slug), None)


def load(
    definitions: Iterable[Study] = ALL, *, environ: dict[str, str] | None = None
) -> list[str]:
    """Build a runtime for every study this process serves.

    Returns what it decided, as lines for the start-up log: which studies are
    served and where, and which were refused and why. Refusing is logged rather
    than raised, so one broken definition cannot keep the viewer itself from
    starting.
    """
    opened = opened_by_environment(environ)
    messages: list[str] = []
    slugs = [study.slug for study in definitions]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        messages.append(f"Studies not served: slugs used twice: {', '.join(duplicates)}")

    loaded: dict[str, StudyRuntime] = {}
    for study in definitions:
        if study.slug in duplicates:
            continue
        served_as = serving_status(study, opened)
        if served_as is None:
            continue
        problem = refusal(study)
        if problem:
            messages.append(f"Study {study.slug} ({study.status.value}) is not served: {problem}")
            continue
        storage = study.resolve_storage()
        store = StudyStore(storage.db_path, storage.log_dir)
        if served_as is Status.OPEN:
            store.init_db()
        loaded[study.slug] = StudyRuntime(study=study, store=store, served_as=served_as)
        how = "open" if served_as is Status.OPEN else "closed: panel and exports only"
        if study.status is Status.DRAFT:
            how += f", a draft opened by {OPEN_STUDIES_ENV}"
        messages.append(f"Study {study.slug} ({how}): /studies/{study.slug}/control")

    for slug in sorted(opened):
        study = find_definition(slug, definitions)
        if study is None:
            messages.append(f"{OPEN_STUDIES_ENV} names {slug}, which is not a study")
        elif study.status is not Status.DRAFT:
            messages.append(
                f"{OPEN_STUDIES_ENV} names {slug}, which is {study.status.value}, not a draft; "
                "its status decides"
            )

    with _runtimes_lock:
        _runtimes.clear()
        _runtimes.update(loaded)
    return messages


def runtime(slug: str) -> StudyRuntime | None:
    with _runtimes_lock:
        return _runtimes.get(slug)


def runtimes() -> list[StudyRuntime]:
    with _runtimes_lock:
        return list(_runtimes.values())


def install(served: StudyRuntime) -> StudyRuntime:
    """Serve a runtime built by hand. For tests, which run studies the
    definitions would not."""
    with _runtimes_lock:
        _runtimes[served.slug] = served
    return served


def remove(slug: str) -> None:
    with _runtimes_lock:
        _runtimes.pop(slug, None)
