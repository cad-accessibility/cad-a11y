"""Persistent storage for study sessions -- a database of its own, separate from
``app/db.py``.

What it stores
--------------
Interactions with the system, and their timings, linked to a participant code.
That is the whole scope. Every row is something the participant or the
experimenter *did to the application* -- a keypress, a render, a step advance, a
model load, a readiness signal -- stamped with when it happened.

What it deliberately does not store: anything the participant *said*, the
experimenter's notes, questionnaire answers, or any free text about a person.
Those are handled verbally and recorded on the experimenter's own sheet. Keeping
half of that here would produce two partial records of the same session with no
way to tell which one was complete, and would put personal detail in a file whose
reason to exist is machine-readable interaction data.

Why a second database file
--------------------------
``data/db/usage.db`` is product analytics: anonymous, consent-gated, disposable.
This is research data, and a study session cannot be re-run. The two have opposite
requirements almost everywhere:

* **Consent.** Analytics writes are gated on the cookie consent dialog and are
  discarded when it was declined. Study participants consent on paper before the
  session starts and are deliberately not shown that dialog, so gating study
  logging on the same flag would silently record nothing at all.
* **Blast radius.** Clearing or migrating the analytics database must never be
  able to touch a session that has already been run.
* **Failure reporting.** Analytics failures are swallowed on purpose -- telemetry
  must not break a render. Study logging failures are also swallowed (a dropped
  write must not interrupt a participant mid-exploration) but they are *counted*
  and surfaced, so the experimenter finds out during the session rather than
  during analysis. See ``logging_health``.

Every write also lands in an append-only JSONL file under ``data/logs/study/``,
carrying the full viewer state at that moment. That file, not this database, is
the authoritative record for reconstructing a session: it survives a schema
change, it is readable without SQLite, and it is written even when the database
write is the thing that failed.

Journal mode is WAL, for the same reason as the analytics database: readers never
block the writer. Ordering within a session comes from ``seq``, a per-session
counter assigned under ``_write_lock`` so two threads cannot interleave it.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "data" / "db" / "study.db"


def _default_log_dir() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "data" / "logs" / "study"


DB_PATH: Path = (
    Path(os.environ["STUDY_DB_PATH"]) if os.environ.get("STUDY_DB_PATH") else _default_db_path()
)
LOG_DIR: Path = (
    Path(os.environ["STUDY_LOG_DIR"]) if os.environ.get("STUDY_LOG_DIR") else _default_log_dir()
)

_local = threading.local()

# Serialises every study write. Study traffic is one participant on one machine,
# so contention is irrelevant, and it is what makes `seq` a reliable ordering.
_write_lock = threading.RLock()

# Counted, not raised. Surfaced through logging_health() so the control panel can
# show "logging degraded" while the session is still running.
_failures: dict[str, Any] = {
    "db_writes": 0,
    "db_reads": 0,
    "jsonl_writes": 0,
    "last_error": None,
}


_DDL = """
-- One row per participant code, ever. sequence_number is enrollment order and is
-- what the Latin square indexes, so re-enrolling an existing participant for a
-- second session keeps their original model assignment.
CREATE TABLE IF NOT EXISTS participants (
    code             TEXT PRIMARY KEY,
    sequence_number  INTEGER NOT NULL,
    first_session_at DATETIME,
    created_at       DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- One row per run of the protocol. task_order is stored because without it an
-- event naming "task 1" cannot be resolved to a model; it is a property of the
-- run, not of the person.
-- participant_key is how a participant's browser says which session it belongs
-- to. Several sessions can be active at once -- two experimenters in different
-- cities, one server -- and without a key per session a participant page can
-- only attach to "the" active one, which means the newest. That is how one
-- participant's keypresses end up logged against another participant, and how a
-- model loads onto the wrong braille display mid-task.
CREATE TABLE IF NOT EXISTS study_sessions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_code  TEXT NOT NULL REFERENCES participants(code),
    session_number    INTEGER NOT NULL DEFAULT 1,
    participant_key   TEXT,
    task_order        TEXT NOT NULL,
    protocol_version  TEXT,
    step_index        INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'active',
    log_path          TEXT,
    started_at        DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    step_started_at   DATETIME,
    completed_at      DATETIME,
    UNIQUE(participant_code, session_number)
);
CREATE INDEX IF NOT EXISTS idx_study_sessions_status
    ON study_sessions(status, started_at);
-- The index on participant_key is created in _migrate, not here. CREATE TABLE
-- IF NOT EXISTS does nothing to a table that already exists, so on a database
-- written before that column existed this script would try to index a column
-- that is not there yet -- and take init_db down with it, on a database holding
-- sessions that cannot be re-run.

-- The interaction stream. event_data is JSON because every event type carries a
-- different payload; the columns beside it are what analysis groups by, so
-- "every keypress during task 1 part B" is plain SQL with no JSON extraction.
--
-- elapsed_ms and step_elapsed_ms are stored rather than derived. Both are
-- answerable from created_at with date arithmetic, but they are the two things
-- every timing question starts from, and having them as plain integers keeps
-- those queries readable and immune to how SQLite parses a timestamp string.
CREATE TABLE IF NOT EXISTS study_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    study_session_id INTEGER NOT NULL REFERENCES study_sessions(id),
    participant_code TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    part_id          TEXT,
    step_id          TEXT,
    step_index       INTEGER,
    event_type       TEXT NOT NULL,
    source           TEXT NOT NULL,
    client_id        TEXT,
    event_data       TEXT,
    client_time      TEXT,
    created_at       DATETIME,
    elapsed_ms       INTEGER,
    step_elapsed_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_study_events_session ON study_events(study_session_id, seq);
CREATE INDEX IF NOT EXISTS idx_study_events_participant ON study_events(participant_code, created_at);
CREATE INDEX IF NOT EXISTS idx_study_events_type ON study_events(study_session_id, event_type);
CREATE INDEX IF NOT EXISTS idx_study_events_step ON study_events(study_session_id, step_id);

-- Renders get structured columns rather than living in event_data, because
-- "which slice was under their fingers at that moment" is the single most queried
-- thing in the analysis and it should not need JSON extraction. Recorded for
-- cache hits too: the participant felt a display update either way, so leaving
-- them out would silently drop most of a fast arrow-key traversal.
CREATE TABLE IF NOT EXISTS study_renders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    study_session_id INTEGER NOT NULL REFERENCES study_sessions(id),
    participant_code TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    part_id          TEXT,
    step_id          TEXT,
    step_index       INTEGER,
    model            TEXT,
    view             TEXT,
    render_mode      TEXT,
    layout_mode      TEXT,
    depth            REAL,
    zoom             REAL,
    input_source     TEXT,
    cache_hit        INTEGER,
    orientation      TEXT,
    created_at       DATETIME,
    elapsed_ms       INTEGER,
    step_elapsed_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_study_renders_session ON study_renders(study_session_id, seq);
CREATE INDEX IF NOT EXISTS idx_study_renders_participant ON study_renders(participant_code, created_at);

-- There is deliberately no table for observations, experimenter notes or
-- questionnaire answers. Those are what the participant said and what the
-- experimenter thought, not interactions with the system, and they are recorded
-- verbally on the experimenter's own sheet.
"""


def _get_conn() -> sqlite3.Connection:
    """Return this thread's connection, reopening when DB_PATH changes (tests)."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    current_path = str(DB_PATH)
    if conn is None or getattr(_local, "conn_path", None) != current_path:
        if conn is not None:
            conn.close()
        Path(current_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(current_path, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.conn_path = current_path
    return conn


def init_db() -> None:
    """Create the schema. Safe to call repeatedly."""
    with _write_lock:
        conn = _get_conn()
        conn.executescript(_DDL)
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a table's first CREATE.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so a database
    holding sessions recorded before a column existed needs an explicit ALTER.
    Sessions already run keep their data; they simply have no participant key,
    which is right -- they were run when only one session could be active.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(study_sessions)")}
    if "participant_key" not in columns:
        conn.execute("ALTER TABLE study_sessions ADD COLUMN participant_key TEXT")
    # Unconditional and idempotent, so it lands on a freshly created database as
    # well as a migrated one. Partial index: sessions recorded before keys
    # existed have NULL there, and several NULLs must not collide.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_study_sessions_key "
        "ON study_sessions(participant_key) WHERE participant_key IS NOT NULL"
    )


_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def now() -> str:
    """Millisecond-precision UTC, the timestamp format used everywhere here.

    Second precision is not enough: a participant holding an arrow key produces
    several renders inside one second, and the order of those renders is the
    behavioural signal the analysis reads.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _elapsed_ms(since: str | None, until: str) -> int | None:
    start, end = _parse(since), _parse(until)
    if start is None or end is None:
        return None
    return int((end - start).total_seconds() * 1000)


def _note_failure(kind: str, error: Exception) -> None:
    _failures[kind] = int(_failures.get(kind, 0)) + 1
    _failures["last_error"] = f"{type(error).__name__}: {error}"


def note_external_failure(error: Exception) -> None:
    """Record a study-logging failure raised outside this module.

    Used by the /render hook, which must swallow everything: it runs inside the
    render request's own error handling, where an exception would be turned into
    a failed render and stop the participant's display updating.
    """
    _note_failure("db_writes", error)


def logging_health() -> dict[str, Any]:
    """Whether study logging is actually working, for the control panel.

    Reported rather than raised: a failed write must not interrupt the session,
    but the experimenter must not find out afterwards either.
    """
    return {
        "db_write_failures": _failures["db_writes"],
        "db_read_failures": _failures["db_reads"],
        "jsonl_write_failures": _failures["jsonl_writes"],
        "last_error": _failures["last_error"],
        "db_path": str(DB_PATH),
        "log_dir": str(LOG_DIR),
    }


# ---------------------------------------------------------------------------
# Participants and sessions
# ---------------------------------------------------------------------------

def next_participant_code(prefix: str = "P") -> str:
    """Suggest the next unused code, e.g. P03 when P01 and P02 exist.

    Every run gets a new participant id without the experimenter having to decide
    on one; they can still type their own at enrollment.
    """
    try:
        rows = _get_conn().execute("SELECT code FROM participants").fetchall()
    except Exception:
        rows = []
    used = {str(row["code"]) for row in rows}
    n = 1
    while f"{prefix}{n:02d}" in used:
        n += 1
    return f"{prefix}{n:02d}"


def get_participant(code: str) -> dict[str, Any] | None:
    row = _get_conn().execute(
        "SELECT code, sequence_number, first_session_at, created_at FROM participants WHERE code = ?",
        (code,),
    ).fetchone()
    return dict(row) if row else None


def highest_sequence_number() -> int:
    try:
        row = _get_conn().execute(
            "SELECT COALESCE(MAX(sequence_number), 0) AS n FROM participants"
        ).fetchone()
        return int(row["n"])
    except Exception:
        return 0


def get_or_create_participant(code: str) -> dict[str, Any]:
    """Return the participant row, creating it with the next sequence number.

    The sequence number is what the Latin square indexes. It is assigned once and
    never changes, so a participant returning for a second session keeps the model
    pairs they were assigned the first time.
    """
    with _write_lock:
        existing = get_participant(code)
        if existing:
            return existing
        conn = _get_conn()
        sequence_number = highest_sequence_number() + 1
        conn.execute(
            "INSERT INTO participants (code, sequence_number, first_session_at) VALUES (?, ?, ?)",
            (code, sequence_number, now()),
        )
        conn.commit()
        return get_participant(code) or {
            "code": code,
            "sequence_number": sequence_number,
            "first_session_at": None,
            "created_at": None,
        }


def _log_path_for(code: str, session_number: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return LOG_DIR / f"{code}_S{session_number}_{stamp}.jsonl"


def new_participant_key() -> str:
    """A short, unambiguous key identifying one session's participant view.

    Short because an experimenter may have to read the link out; unambiguous
    because they may have to read it to someone who cannot see the screen. The
    alphabet drops the characters that sound or look alike -- 0/O, 1/I/L, 5/S,
    2/Z -- so "did you say B or D" is the only confusion left to have.
    """
    alphabet = "ABCDEFGHJKMNPQRTUVWXY346789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def create_session(
    participant_code: str,
    session_number: int,
    task_order: list[str],
    protocol_version: str,
) -> dict[str, Any]:
    """Start a study session and return its row. Raises on conflict.

    Deliberately not swallowed: unlike an event write, failing to create the
    session is not something to carry on past -- there would be nothing to log
    against, and the experimenter needs to know before the participant starts.
    """
    with _write_lock:
        get_or_create_participant(participant_code)
        conn = _get_conn()
        log_path = _log_path_for(participant_code, session_number)
        timestamp = now()
        # Retry on the (vanishingly unlikely) key collision rather than letting a
        # unique-index violation surface as a failed enrolment.
        for _ in range(8):
            key = new_participant_key()
            try:
                cursor = conn.execute(
                    """INSERT INTO study_sessions
                       (participant_code, session_number, participant_key, task_order,
                        protocol_version, step_index, status, log_path, started_at,
                        step_started_at)
                       VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?, ?)""",
                    (
                        participant_code,
                        session_number,
                        key,
                        json.dumps(task_order),
                        protocol_version,
                        str(log_path),
                        timestamp,
                        timestamp,
                    ),
                )
                break
            except sqlite3.IntegrityError as error:
                if "participant_key" not in str(error):
                    raise  # a real conflict, e.g. this participant/session already exists
        else:
            raise RuntimeError("could not allocate a unique participant key")
        conn.commit()
        return get_study_session(int(cursor.lastrowid)) or {}


def _hydrate(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    session = dict(row)
    session["task_order"] = json.loads(session.get("task_order") or "[]")
    return session


def get_study_session(study_session_id: int) -> dict[str, Any] | None:
    """Look up a session, reporting rather than raising if the database is
    unreachable.

    Every request path starts with one of these two reads, so letting a database
    error escape would turn a storage problem into a 500 in front of a
    participant who is mid-exploration. Counted instead, and shown as degraded
    logging in the control panel.
    """
    try:
        return _hydrate(
            _get_conn()
            .execute("SELECT * FROM study_sessions WHERE id = ?", (study_session_id,))
            .fetchone()
        )
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        _note_failure("db_reads", error)
        return None


def list_active_sessions() -> list[dict[str, Any]]:
    """Every session currently running.

    More than one is normal: two experimenters in different cities share one
    deployment. Callers that need *a* session must say which, or handle the
    ambiguity -- see study.py's session resolution.
    """
    try:
        rows = _get_conn().execute(
            "SELECT * FROM study_sessions WHERE status = 'active' ORDER BY id"
        ).fetchall()
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        _note_failure("db_reads", error)
        return []
    return [session for session in (_hydrate(row) for row in rows) if session]


def get_session_by_key(participant_key: str) -> dict[str, Any] | None:
    """The session a participant's browser is bound to, by its key."""
    if not participant_key:
        return None
    try:
        return _hydrate(
            _get_conn()
            .execute("SELECT * FROM study_sessions WHERE participant_key = ?", (participant_key,))
            .fetchone()
        )
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        _note_failure("db_reads", error)
        return None


def list_sessions(limit: int = 100) -> list[dict[str, Any]]:
    rows = _get_conn().execute(
        """SELECT id, participant_code, session_number, task_order, status,
                  started_at, completed_at, step_index, log_path
           FROM study_sessions ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [session for session in (_hydrate(row) for row in rows) if session]


def set_step_index(study_session_id: int, step_index: int) -> None:
    """Move the session to a step and restart the per-step clock."""
    with _write_lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE study_sessions SET step_index = ?, step_started_at = ? WHERE id = ?",
            (int(step_index), now(), study_session_id),
        )
        conn.commit()


def checkpoint() -> None:
    """Fold the write-ahead log back into the database file.

    WAL mode means recent writes live in ``study.db-wal`` until SQLite decides to
    checkpoint. That is invisible while the server is reading through its own
    connection, and quietly destructive afterwards: copying ``study.db`` on its
    own -- the obvious way to get a session off the server for analysis -- yields
    a database missing everything still in the WAL. A real session lost about a
    third of its rows that way.

    Checkpointing at the end of a session makes the file self-contained, so the
    obvious thing to copy is also the correct thing to copy.
    """
    with _write_lock:
        try:
            _get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as error:  # noqa: BLE001 - counted, never fatal
            _note_failure("db_writes", error)


def complete_session(study_session_id: int, status: str = "completed") -> None:
    with _write_lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE study_sessions SET status = ?, completed_at = ? WHERE id = ?",
            (status, now(), study_session_id),
        )
        conn.commit()
    checkpoint()


# ---------------------------------------------------------------------------
# Interaction recording
# ---------------------------------------------------------------------------

def _next_seq(conn: sqlite3.Connection, study_session_id: int) -> int:
    """Next ordering number for this session, across events and renders together.

    One counter over both tables, so a keypress and the render it caused are
    ordered relative to each other rather than only within their own table.
    Called under ``_write_lock``.
    """
    row = conn.execute(
        """SELECT MAX(seq) AS m FROM (
               SELECT COALESCE(MAX(seq), 0) AS seq FROM study_events WHERE study_session_id = ?
               UNION ALL
               SELECT COALESCE(MAX(seq), 0) AS seq FROM study_renders WHERE study_session_id = ?
           )""",
        (study_session_id, study_session_id),
    ).fetchone()
    return int(row["m"] or 0) + 1


def _append_jsonl(session: dict[str, Any], record: dict[str, Any]) -> None:
    """Append one line to this session's reconstruction log.

    Separate from the database write and never conditional on it: if SQLite is the
    thing that is broken, this file is what the session is recovered from.
    """
    path_value = session.get("log_path")
    if not path_value:
        return
    try:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as error:  # noqa: BLE001 - counted, never fatal
        _note_failure("jsonl_writes", error)


def record_event(
    study_session_id: int,
    event_type: str,
    *,
    source: str = "participant",
    event_data: dict[str, Any] | None = None,
    part_id: str | None = None,
    step_id: str | None = None,
    step_index: int | None = None,
    client_id: str | None = None,
    client_time: str | None = None,
    viewer_state: dict[str, Any] | None = None,
) -> int | None:
    """Record one interaction in both the database and the JSONL log.

    ``viewer_state`` is the full state of the viewer at the moment it happened --
    model, view, depth, render mode, zoom. It goes into the JSONL line rather than
    into columns, because the point of that file is that every line is
    self-describing: reconstructing what was under the participant's fingers
    should not require joining against the render table.

    Returns the assigned ``seq``, or None if the session is gone.
    """
    session = get_study_session(study_session_id)
    if not session:
        return None

    timestamp = now()
    elapsed = _elapsed_ms(session.get("started_at"), timestamp)
    step_elapsed = _elapsed_ms(session.get("step_started_at"), timestamp)
    participant_code = str(session.get("participant_code") or "")
    seq: int | None = None
    # Both writes happen under the one lock. The JSONL append used to sit outside
    # it, which let two clients posting at the same moment take seq 10 and 11 and
    # then append in the other order -- a file whose whole point is that it is an
    # ordered append-only record. The data was complete either way, but a reader
    # trusting file order got it wrong.
    with _write_lock:
        try:
            conn = _get_conn()
            seq = _next_seq(conn, study_session_id)
            conn.execute(
                """INSERT INTO study_events
                   (study_session_id, participant_code, seq, part_id, step_id, step_index,
                    event_type, source, client_id, event_data, client_time, created_at,
                    elapsed_ms, step_elapsed_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    study_session_id,
                    participant_code,
                    seq,
                    part_id,
                    step_id,
                    step_index,
                    event_type,
                    source,
                    client_id,
                    json.dumps(event_data or {}, default=str),
                    client_time,
                    timestamp,
                    elapsed,
                    step_elapsed,
                ),
            )
            conn.commit()
        except Exception as error:  # noqa: BLE001 - counted, never fatal
            _note_failure("db_writes", error)

        # Still written when the database write failed: that is the case the
        # second record exists for.
        _append_jsonl(
            session,
            {
                "timestamp": timestamp,
                "elapsed_ms": elapsed,
                "step_elapsed_ms": step_elapsed,
                "seq": seq,
                "study_session_id": study_session_id,
                "participant_code": participant_code,
                "session_number": session.get("session_number"),
                "task_order": session.get("task_order"),
                "part_id": part_id,
                "step_id": step_id,
                "step_index": step_index,
                "event_type": event_type,
                "source": source,
                "client_id": client_id,
                "client_time": client_time,
                "viewer_state": viewer_state or {},
                "event_data": event_data or {},
            },
        )
    return seq


def record_render(
    study_session_id: int,
    *,
    model: str | None,
    view: str | None,
    render_mode: str | None,
    layout_mode: str | None,
    depth: float | None,
    zoom: float | None,
    input_source: str | None,
    cache_hit: bool,
    orientation: dict[str, Any] | None = None,
    part_id: str | None = None,
    step_id: str | None = None,
    step_index: int | None = None,
) -> int | None:
    """Record one render request against the active study session.

    Recorded on the server rather than reported by the client, so a browser that
    crashes or a tab that is closed cannot take the record of what was displayed
    with it.
    """
    session = get_study_session(study_session_id)
    if not session:
        return None

    timestamp = now()
    elapsed = _elapsed_ms(session.get("started_at"), timestamp)
    step_elapsed = _elapsed_ms(session.get("step_started_at"), timestamp)
    participant_code = str(session.get("participant_code") or "")
    seq: int | None = None
    # One lock over both writes, so the file stays in seq order. See record_event.
    with _write_lock:
        try:
            conn = _get_conn()
            seq = _next_seq(conn, study_session_id)
            conn.execute(
                """INSERT INTO study_renders
                   (study_session_id, participant_code, seq, part_id, step_id, step_index,
                    model, view, render_mode, layout_mode, depth, zoom, input_source,
                    cache_hit, orientation, created_at, elapsed_ms, step_elapsed_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    study_session_id,
                    participant_code,
                    seq,
                    part_id,
                    step_id,
                    step_index,
                    model,
                    view,
                    render_mode,
                    layout_mode,
                    None if depth is None else float(depth),
                    None if zoom is None else float(zoom),
                    input_source,
                    1 if cache_hit else 0,
                    json.dumps(orientation) if orientation else None,
                    timestamp,
                    elapsed,
                    step_elapsed,
                ),
            )
            conn.commit()
        except Exception as error:  # noqa: BLE001 - counted, never fatal
            _note_failure("db_writes", error)

        _append_jsonl(
            session,
            {
                "timestamp": timestamp,
                "elapsed_ms": elapsed,
                "step_elapsed_ms": step_elapsed,
                "seq": seq,
                "study_session_id": study_session_id,
                "participant_code": participant_code,
                "session_number": session.get("session_number"),
                "part_id": part_id,
                "step_id": step_id,
                "step_index": step_index,
                "event_type": "render",
                "source": "server",
                "viewer_state": {
                    "model": model,
                    "view": view,
                    "render_mode": render_mode,
                    "layout_mode": layout_mode,
                    "depth": depth,
                    "zoom": zoom,
                    "orientation": orientation,
                },
                "event_data": {"input_source": input_source, "cache_hit": bool(cache_hit)},
            },
        )
    return seq


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def session_counts(study_session_id: int) -> dict[str, int]:
    """Row counts for the control panel, so the experimenter can see logging is
    alive without opening the database."""
    conn = _get_conn()
    try:
        events = conn.execute(
            "SELECT COUNT(*) AS n FROM study_events WHERE study_session_id = ?",
            (study_session_id,),
        ).fetchone()["n"]
        renders = conn.execute(
            "SELECT COUNT(*) AS n FROM study_renders WHERE study_session_id = ?",
            (study_session_id,),
        ).fetchone()["n"]
    except Exception:
        return {"events": 0, "renders": 0}
    return {"events": int(events), "renders": int(renders)}


def export_session(study_session_id: int) -> dict[str, Any] | None:
    """Everything recorded for one session, as one JSON document.

    Exists so the analysis does not begin with someone working out how to get a
    SQLite file off a Docker volume.
    """
    session = get_study_session(study_session_id)
    if not session:
        return None
    conn = _get_conn()
    events = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM study_events WHERE study_session_id = ? ORDER BY seq",
            (study_session_id,),
        )
    ]
    for event in events:
        try:
            event["event_data"] = json.loads(event.get("event_data") or "{}")
        except Exception:
            pass
    renders = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM study_renders WHERE study_session_id = ? ORDER BY seq",
            (study_session_id,),
        )
    ]
    return {
        "session": session,
        "participant": get_participant(str(session.get("participant_code"))),
        "events": events,
        "renders": renders,
    }
