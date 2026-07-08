"""Persistent SQLite storage for sessions, uploaded model records, and interaction analytics.

One thread-local connection per thread; WAL mode for concurrent readers.
DB_PATH can be overridden by the DB_PATH environment variable or by
reassigning the module-level attribute before calling init_db() (useful in tests).

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


def _default_db_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "data" / "db" / "usage.db"


DB_PATH: Path = Path(os.environ["DB_PATH"]) if os.environ.get("DB_PATH") else _default_db_path()

_local = threading.local()

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    identifier    TEXT,
    consent_given INTEGER,
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

-- Supports cross-session model lookup and deletion by email identifier.
CREATE INDEX IF NOT EXISTS idx_sessions_identifier
    ON sessions(identifier) WHERE identifier IS NOT NULL;

-- One row per successful /render call. session_id is nullable: renders triggered
-- without a browser session (direct API calls) are still recorded for aggregate stats.
-- Structured columns enable plain GROUP BY queries without JSON extraction.
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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.conn_path = current_path
    return conn


def init_db() -> None:
    """Create all tables and indexes. Safe to call multiple times (CREATE IF NOT EXISTS)."""
    conn = _get_conn()
    conn.executescript(_DDL)
    conn.commit()


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
        "SELECT id, identifier, consent_given, created_at, last_seen_at FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def save_session_identifier(session_id: str, email: str | None, consent_given: bool) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET identifier = ?, consent_given = ? WHERE id = ?",
        (email, 1 if consent_given else 0, session_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Uploaded model tracking (§3 extensibility hooks)
# ---------------------------------------------------------------------------

def get_session_models(session_id: str) -> list[dict[str, Any]]:
    """Return non-deleted uploaded_models rows for this session.

    When the session has a known identifier (email), models from every session
    sharing that identifier are included so a returning user on a new device
    sees their full upload history.
    """
    session = get_session(session_id)
    conn = _get_conn()
    if session and session["identifier"]:
        rows = conn.execute(
            """SELECT um.filename, um.original_name, um.file_size, um.sha256, um.uploaded_at
               FROM uploaded_models um
               JOIN sessions s ON um.session_id = s.id
               WHERE s.identifier = ? AND um.deleted_at IS NULL
               ORDER BY um.uploaded_at""",
            (session["identifier"],),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT filename, original_name, file_size, sha256, uploaded_at
               FROM uploaded_models
               WHERE session_id = ? AND deleted_at IS NULL
               ORDER BY uploaded_at""",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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
    """Return True if this session (or any session sharing its identifier) owns the file."""
    session = get_session(session_id)
    conn = _get_conn()
    if session and session["identifier"]:
        row = conn.execute(
            """SELECT COUNT(*) FROM uploaded_models
               WHERE filename = ? AND deleted_at IS NULL
               AND session_id IN (SELECT id FROM sessions WHERE identifier = ?)""",
            (filename, session["identifier"]),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT COUNT(*) FROM uploaded_models
               WHERE session_id = ? AND filename = ? AND deleted_at IS NULL""",
            (session_id, filename),
        ).fetchone()
    return row[0] > 0


def mark_model_deleted(session_id: str, filename: str) -> bool:
    """Soft-delete a model row. Returns True if a row was updated.

    When the session has a known identifier (email), deletion is allowed for
    any model uploaded by any session sharing that identifier — not just the
    current session UUID — so a user on a new device can remove their own files.
    """
    session = get_session(session_id)
    conn = _get_conn()
    if session and session["identifier"]:
        cursor = conn.execute(
            """UPDATE uploaded_models
               SET deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE filename = ? AND deleted_at IS NULL
               AND session_id IN (SELECT id FROM sessions WHERE identifier = ?)""",
            (filename, session["identifier"]),
        )
    else:
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
        if not session or session.get("consent_given") != 1:
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
        if not session or session.get("consent_given") != 1:
            return
        conn = _get_conn()
        conn.execute(
            "INSERT INTO page_events (session_id, event_type, event_data) VALUES (?, ?, ?)",
            (session_id, event_type, json.dumps(event_data) if event_data else None),
        )
        conn.commit()
    except Exception:
        pass
