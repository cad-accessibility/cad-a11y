"""Persistent SQLite storage for sessions, uploaded model records, and interaction analytics.

One thread-local connection per thread. The database runs in WAL (Write-Ahead
Logging) journal mode: rather than writing changes to a rollback journal and
blocking readers for the duration, SQLite appends committed changes to a separate
``-wal`` file and checkpoints them into the main database later. Readers never
block the writer and the writer never blocks readers, so this multi-threaded Flask
server can keep serving reads while a write is in flight, with better write
throughput and fewer fsyncs than the default rollback journal.

DB_PATH can be overridden by the DB_PATH environment variable or by
reassigning the module-level attribute before calling init_db() (useful in tests).

Contacts live in a file of their own
------------------------------------
Addresses from the consent dialog are in ``contacts.db``, beside this file unless
CONTACTS_DB_PATH says otherwise. Separate tables were not separation enough: a table shares its file's
pages, its write-ahead log and every backup of it, so a copy of usage.db made to
look at analytics carried the addresses too, and SQLite leaves deleted cells'
bytes in place until the file is rebuilt (#243 review). With the addresses in
their own file, usage.db can be backed up, shared or exported with none in it.

Analytics design
----------------
Two tables capture usage signals:
  render_stats  -- one row per successful /render call (server-side, structured)
  page_events   -- one row per client-side interaction (section dwell, shortcuts, device events)

render_stats uses structured columns so aggregation queries are plain SQL with no JSON
extraction (e.g. "SELECT render_mode, COUNT(*) FROM render_stats GROUP BY render_mode").
page_events uses a JSON blob for event_data because client-side event shapes vary widely.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

from . import email_address


def _default_db_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "data" / "db" / "usage.db"


DB_PATH: Path = Path(os.environ["DB_PATH"]) if os.environ.get("DB_PATH") else _default_db_path()
# None means beside DB_PATH, so anything that moves usage.db (a test, a demo
# station's scratch folder) moves the contacts with it.
CONTACTS_DB_PATH: Path | None = (
    Path(os.environ["CONTACTS_DB_PATH"]) if os.environ.get("CONTACTS_DB_PATH") else None
)


def contacts_db_path() -> Path:
    return CONTACTS_DB_PATH or Path(DB_PATH).with_name("contacts.db")

_local = threading.local()
_contacts_local = threading.local()

_DDL = """
-- identifier is a workshop participant's first name and nothing else (#237). An
-- email from the consent dialog is not kept on a session at all: see contacts.
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    identifier    TEXT,
    consent_given INTEGER,
    is_workshop   INTEGER DEFAULT 0,
    created_at    DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen_at  DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Schema ready for §3 (upload→DB wiring). Rows are inserted by register_model()
-- once upload_model() in server.py calls it.
CREATE TABLE IF NOT EXISTS uploaded_models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(id),
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_size     INTEGER,
    sha256        TEXT,
    uploaded_at   DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    deleted_at    DATETIME
);
CREATE INDEX IF NOT EXISTS idx_uploaded_models_session
    ON uploaded_models(session_id, deleted_at);

-- Supports the workshop's first-name lookup.
CREATE INDEX IF NOT EXISTS idx_sessions_identifier
    ON sessions(identifier) WHERE identifier IS NOT NULL;

-- One row per successful /render call. session_id is nullable: renders triggered
-- without a browser session (direct API calls) are still recorded for aggregate stats.
-- Structured columns enable plain GROUP BY queries without JSON extraction.
-- TODO(#62): migrate to Felix's structured rendering-state payload (likely a JSON
-- column mirroring page_events.event_data) once that code lands.
CREATE TABLE IF NOT EXISTS render_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT REFERENCES sessions(id),
    view         TEXT,
    render_mode  TEXT,
    depth        REAL,
    zoom         REAL,
    layout_mode  TEXT,
    input_source TEXT,
    created_at   DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_render_stats_mode ON render_stats(render_mode, created_at);
CREATE INDEX IF NOT EXISTS idx_render_stats_view ON render_stats(view, created_at);
CREATE INDEX IF NOT EXISTS idx_render_stats_session ON render_stats(session_id, created_at);

-- Flexible client-side event log. event_type is an enum-like string; event_data is a
-- JSON blob so each event type can carry its own payload without schema changes.
--
-- Expected event_type values (non-exhaustive):
--   section_dwell    {section_id, duration_ms}          how long user stayed in a UI section
--   keyboard_shortcut {key, action}                     which shortcuts are used most
--   device_connect   {device_type, status}              Monarch/DotPad/GoDice/Slider/WitMotion
--   export           {format, view, render_mode, depth, zoom}
--   model_select     {model_name, model_index}
CREATE TABLE IF NOT EXISTS page_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT REFERENCES sessions(id),
    event_type   TEXT NOT NULL,
    event_data   TEXT,
    created_at   DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_page_events_session ON page_events(session_id, event_type);
CREATE INDEX IF NOT EXISTS idx_page_events_type ON page_events(event_type, created_at);
"""


def _get_conn() -> sqlite3.Connection:
    """Return this thread's SQLite connection, opening a new one when DB_PATH changes."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    current_path = str(DB_PATH)
    if conn is None or getattr(_local, "conn_path", None) != current_path:
        if conn is not None:
            conn.close()
        Path(current_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(current_path, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        # WAL (Write-Ahead Logging): concurrent readers and one writer without mutual
        # blocking, plus better write throughput than the default rollback journal.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.conn_path = current_path
    return conn


_CONTACTS_DDL = """
-- Addresses given in the consent dialog, to send project updates to, and nothing
-- else: no session, no time and no order of arrival, so no address can be matched
-- to the usage data recorded under a session. WITHOUT ROWID stores rows in
-- address order, not in the order they came in.
CREATE TABLE IF NOT EXISTS contacts (
    email TEXT PRIMARY KEY COLLATE NOCASE
) WITHOUT ROWID;
"""


def _get_contacts_conn() -> sqlite3.Connection:
    """This thread's connection to contacts.db, reopened when the path changes.

    The default rollback journal rather than WAL, so the file is the whole of it,
    with no -wal file holding old pages beside it; and secure_delete, so a removed
    address is overwritten rather than left in a free page.
    """
    conn: sqlite3.Connection | None = getattr(_contacts_local, "conn", None)
    current_path = str(contacts_db_path())
    if conn is None or getattr(_contacts_local, "conn_path", None) != current_path:
        if conn is not None:
            conn.close()
        Path(current_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(current_path, check_same_thread=True)
        conn.execute("PRAGMA secure_delete=ON")
        conn.executescript(_CONTACTS_DDL)
        _contacts_local.conn = conn
        _contacts_local.conn_path = current_path
    return conn


# usage.db's user_version while addresses have been moved out of it but their old
# bytes not yet scrubbed. Set in the same transaction as the move, so a start that
# stops in between leaves the next one a scrub to finish rather than nothing to
# notice: the move itself is done by then and finds nothing to do.
_SCRUB_PENDING = 1


def init_db() -> None:
    """Create all tables and indexes. Safe to call multiple times (CREATE IF NOT EXISTS)."""
    conn = _get_conn()
    conn.executescript(_DDL)
    _get_contacts_conn()
    _migrate(conn)
    _backfill_render_mode_labels(conn)
    conn.commit()
    if conn.execute("PRAGMA user_version").fetchone()[0] == _SCRUB_PENDING:
        _scrub_after_moving_addresses(conn)


def _migrate(conn: sqlite3.Connection) -> int:
    """Add columns introduced after a table's first CREATE. CREATE IF NOT EXISTS never
    alters an existing table, so new columns need an explicit ALTER on older databases.
    Returns how many addresses were moved out of this file."""
    session_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "is_workshop" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN is_workshop INTEGER DEFAULT 0")
    return _move_emails_to_contacts(conn, session_cols)


def _move_emails_to_contacts(conn: sqlite3.Connection, session_cols: set[str]) -> int:
    """Move every address the consent dialog stored in usage.db into contacts.db,
    and with that drop its link to the session it sat beside.

    identifier used to hold both a viewer's email and a workshop participant's
    first name, so /workshop?name=<email> found a viewer's session and handed it
    over (#237), and the address sat next to the session its usage data is
    recorded under (#220). Workshop rows are written with is_workshop = 1, but
    nothing ever checked that a row without it held an address, and a local
    database turned up a non-workshop row whose identifier was "abc123". So only
    what passes the same check a new address does is moved; anything else is left
    exactly where it is, since moving it would be both wrong and impossible to
    undo (#243 review). A pre-release build of #237 kept addresses in a
    sessions.email column, and one kept them in a contacts table in this file;
    both are emptied the same way.

    The addresses go into contacts.db and are committed there first, and only
    then leave this file, so a run interrupted in between repeats rather than
    loses them. Idempotent: nothing writes an address here any more.
    """
    moves: list[tuple[str, str]] = []
    for row in conn.execute(
        """SELECT id, identifier FROM sessions
           WHERE COALESCE(is_workshop, 0) = 0 AND identifier IS NOT NULL"""
    ):
        try:
            moves.append((row["id"], email_address.normalise(row["identifier"])))
        except email_address.InvalidEmail:
            continue

    column_addresses: list[str] = []
    if "email" in session_cols:
        for row in conn.execute("SELECT email FROM sessions WHERE email IS NOT NULL"):
            try:
                column_addresses.append(email_address.normalise(row["email"]))
            except email_address.InvalidEmail:
                continue

    table_addresses: list[str] = []
    has_old_table = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'contacts'"
        ).fetchone()
    )
    if has_old_table:
        for row in conn.execute("SELECT email FROM contacts"):
            try:
                table_addresses.append(email_address.normalise(row["email"]))
            except email_address.InvalidEmail:
                continue

    addresses = [address for _, address in moves] + column_addresses + table_addresses
    if not moves and "email" not in session_cols and not has_old_table:
        return 0

    contacts = _get_contacts_conn()
    contacts.executemany(
        "INSERT OR IGNORE INTO contacts (email) VALUES (?)", [(a,) for a in addresses]
    )
    contacts.commit()

    # Overwrite rather than free: with secure_delete on, the cells these updates
    # empty are zeroed. The VACUUM that follows does the rest.
    conn.execute("PRAGMA secure_delete=ON")
    conn.executemany(
        "UPDATE sessions SET identifier = NULL WHERE id = ?", [(session_id,) for session_id, _ in moves]
    )
    moved = len(moves)
    if "email" in session_cols:
        moved += conn.execute("UPDATE sessions SET email = NULL WHERE email IS NOT NULL").rowcount
    if has_old_table:
        # Every row counts, kept or not: dropping the table frees their bytes,
        # and freed bytes are what the scrub is for.
        moved += conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        conn.execute("DROP TABLE contacts")
    if moved:
        conn.execute(f"PRAGMA user_version = {_SCRUB_PENDING}")
    return moved


def _scrub_after_moving_addresses(conn: sqlite3.Connection) -> None:
    """Leave no copy of a moved address anywhere in usage.db.

    Setting a column to NULL does not erase it: SQLite leaves the old bytes in the
    freed cell, in stale index entries, and in the write-ahead log's copies of the
    pages from before (#243 review found addresses in all three). VACUUM rebuilds
    the file from the live rows only, and checkpointing with TRUNCATE folds the
    rebuilt pages in and empties the log. Runs only while the scrub is pending,
    which is once. VACUUM rewrites the whole file and locks it while it does: 0.3 s
    for a 92 MB file on a laptop's SSD, so seconds at most for any usage.db these
    servers hold, and it runs before the server takes a request.

    Backups taken before this ran still hold the old pairing.
    """
    conn.execute("VACUUM")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _backfill_render_mode_labels(conn: sqlite3.Connection) -> None:
    """Rewrite render_mode values the viewer no longer sends.

    The viewer used to send "Shaded" for the mode its UI has always labelled
    "Filled", and render_stats stores whatever it was sent. Both names render
    identically server-side, so the split was cosmetic, but it fragments
    "SELECT render_mode, COUNT(*) ... GROUP BY render_mode" into two series
    either side of the rename.

    Idempotent: the second run matches no rows.
    """
    conn.execute("UPDATE render_stats SET render_mode = 'Filled' WHERE render_mode = 'Shaded'")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def upsert_session(session_id: str) -> dict[str, Any]:
    """Insert new session or refresh last_seen_at for a returning visitor. Returns the row."""
    conn = _get_conn()
    conn.execute("INSERT OR IGNORE INTO sessions (id) VALUES (?)", (session_id,))
    conn.execute(
        "UPDATE sessions SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    return get_session(session_id) or {}


def get_session(session_id: str) -> dict[str, Any] | None:
    row = _get_conn().execute(
        "SELECT id, identifier, consent_given, is_workshop, created_at, last_seen_at"
        " FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def save_session_identifier(
    session_id: str, identifier: str | None, consent_given: bool, is_workshop: bool = False
) -> None:
    """Bind a workshop participant's first name to their session."""
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET identifier = ?, consent_given = ?, is_workshop = ? WHERE id = ?",
        (identifier, 1 if consent_given else 0, 1 if is_workshop else 0, session_id),
    )
    conn.commit()


def save_session_consent(session_id: str, consent_given: bool) -> None:
    """Record the consent dialog's answer to analytics on the session."""
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET consent_given = ? WHERE id = ?",
        (1 if consent_given else 0, session_id),
    )
    conn.commit()


def add_contact(email: str) -> None:
    """Keep an address to send project updates to, in contacts.db, with nothing
    that ties it to a session or to anything recorded under one (#220).

    Checked here as well as at /session/identify, by the same function, so no
    route can store an address that one of them would refuse. Raises
    ``email_address.InvalidEmail``.
    """
    address = email_address.normalise(email)
    conn = _get_contacts_conn()
    conn.execute("INSERT OR IGNORE INTO contacts (email) VALUES (?)", (address,))
    conn.commit()


def list_contacts() -> list[str]:
    """Every address on the list, in address order."""
    return [row[0] for row in _get_contacts_conn().execute("SELECT email FROM contacts")]


def get_session_id_for_identifier(identifier: str) -> str | None:
    """Return the most recent workshop session bound to this first name, or None.

    Used to reuse a session across workshop uploads sharing the same first name.
    Only workshop sessions match, so nothing typed into /workshop?name= can reach
    a viewer's session.
    """
    conn = _get_conn()
    row = conn.execute(
        """SELECT id FROM sessions
           WHERE identifier = ? AND is_workshop = 1
           ORDER BY last_seen_at DESC LIMIT 1""",
        (identifier,),
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Uploaded model tracking (§3 extensibility hooks)
# ---------------------------------------------------------------------------

def get_session_models(session_id: str) -> list[dict[str, Any]]:
    """Return non-deleted uploaded_models rows for this session.

    This session's only. Uploads used to be shared across every session that had
    typed the same email, which let anyone who typed someone else's address list
    and delete their files (#237).
    """
    rows = _get_conn().execute(
        """SELECT filename, original_name, file_size, sha256, uploaded_at
           FROM uploaded_models
           WHERE session_id = ? AND deleted_at IS NULL
           ORDER BY uploaded_at""",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest_model_for_identifier(identifier: str) -> str | None:
    """Return the filename of the most recently uploaded, non-deleted model for
    any workshop session bound to this first name, or None. Backs /workshop?name=."""
    conn = _get_conn()
    row = conn.execute(
        """SELECT um.filename
           FROM uploaded_models um
           JOIN sessions s ON um.session_id = s.id
           WHERE s.identifier = ? AND s.is_workshop = 1 AND um.deleted_at IS NULL
           ORDER BY um.uploaded_at DESC, um.id DESC
           LIMIT 1""",
        (identifier,),
    ).fetchone()
    return row["filename"] if row else None


def register_model(
    session_id: str,
    filename: str,
    original_name: str,
    file_size: int,
    sha256: str,
) -> None:
    """Record an uploaded model. Called from upload_model() once §3 is wired up."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO uploaded_models (session_id, filename, original_name, file_size, sha256)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, filename, original_name, file_size, sha256),
    )
    conn.commit()


def session_owns_model(session_id: str, filename: str) -> bool:
    """Return True if this session uploaded the file and has not deleted it."""
    row = _get_conn().execute(
        """SELECT COUNT(*) FROM uploaded_models
           WHERE session_id = ? AND filename = ? AND deleted_at IS NULL""",
        (session_id, filename),
    ).fetchone()
    return row[0] > 0


def mark_file_deleted(filename: str) -> int:
    """Soft-delete every live row for a file that is gone, whoever's it was.

    A row outliving its file is what let a later upload of the same name be
    claimed by the earlier owner: the name was free on disk, the new file took
    it, and the old row still said it belonged to the first session (#243
    review). Called whenever a file in the upload directory is removed.
    """
    conn = _get_conn()
    cursor = conn.execute(
        """UPDATE uploaded_models
           SET deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE filename = ? AND deleted_at IS NULL""",
        (filename,),
    )
    conn.commit()
    return cursor.rowcount


def filename_has_live_owner(filename: str) -> bool:
    """Whether any session still holds a live row for this filename."""
    row = _get_conn().execute(
        "SELECT 1 FROM uploaded_models WHERE filename = ? AND deleted_at IS NULL LIMIT 1",
        (filename,),
    ).fetchone()
    return row is not None


def live_upload_filenames() -> set[str]:
    """Every filename some session still holds a live row for."""
    rows = _get_conn().execute(
        "SELECT DISTINCT filename FROM uploaded_models WHERE deleted_at IS NULL"
    ).fetchall()
    return {row["filename"] for row in rows}


def mark_model_deleted(session_id: str, filename: str) -> bool:
    """Soft-delete a model row this session uploaded. Returns True if a row was updated."""
    conn = _get_conn()
    cursor = conn.execute(
        """UPDATE uploaded_models
           SET deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE session_id = ? AND filename = ? AND deleted_at IS NULL""",
        (session_id, filename),
    )
    conn.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def record_render(
    session_id: str | None,
    view: str,
    render_mode: str,
    depth: float,
    zoom: float,
    layout_mode: str,
    input_source: str,
) -> None:
    """Append one row to render_stats. Errors are swallowed — analytics must not break renders."""
    try:
        if not session_id:
            return
        session = get_session(session_id)
        if not session or (session.get("consent_given") != 1 and session.get("is_workshop") != 1):
            return
        conn = _get_conn()
        conn.execute(
            """INSERT INTO render_stats
               (session_id, view, render_mode, depth, zoom, layout_mode, input_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, view, render_mode, float(depth), float(zoom), layout_mode, input_source),
        )
        conn.commit()
    except Exception:
        pass


def record_page_event(
    session_id: str | None,
    event_type: str,
    event_data: dict[str, Any] | None = None,
) -> None:
    """Append one client-side interaction event. Errors are swallowed."""
    try:
        if not session_id:
            return
        session = get_session(session_id)
        if not session or (session.get("consent_given") != 1 and session.get("is_workshop") != 1):
            return
        conn = _get_conn()
        conn.execute(
            "INSERT INTO page_events (session_id, event_type, event_data) VALUES (?, ?, ?)",
            (session_id, event_type, json.dumps(event_data) if event_data else None),
        )
        conn.commit()
    except Exception:
        pass
