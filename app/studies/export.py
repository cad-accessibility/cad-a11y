"""Getting a study's data out, and checking it on the way.

One function builds everything: ``build_archive`` makes a zip holding

  study.db        a consistent copy of the database, as the server had it
  logs/*.jsonl    every session log, the authoritative record
  long.csv        one row per interaction, completed sessions only
  long.json       the codebook for long.csv
  sessions.csv    one row per session, every status
  sessions.json   the codebook for sessions.csv
  checks.json     the data checks below, and what they found
  manifest.json   what the archive is, the counts, and a SHA-256 per file
  README.txt      the same list, for whoever opens the archive in a year

The control panel serves it, and so does ``python -m app.studies export``, which
also reads a database restored from a backup. The codebooks follow the shape of
a BIDS sidecar: one entry per column, with a description, its levels where it
has a fixed set, and its units.

Reading leaves the original alone. The database is copied with SQLite's backup
API, which includes anything still in the write-ahead log; the copy in the
archive is that snapshot, untouched. Everything derived from it is computed from
a second copy brought up to the current schema, so a database written by older
code exports the same columns as a new one without the original being migrated
in place.

Formulas
--------
A CSV opened in a spreadsheet runs any cell that starts with ``=``, ``+``,
``-`` or ``@`` as a formula, and a tab or carriage return at the start can do
the same. Several columns hold text a participant's browser sent, so the export
follows OWASP's advice and puts a single quote in front of any such cell. Plain
numbers are left alone: ``-12.5`` is a number to a spreadsheet, not a formula,
and quoting it would turn a numeric column into text. Remove the leading quote
to get back the value that was recorded.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from . import protocol
from .definition import Study
from .store import LONG_EXPORT_COLUMNS, StudyStore
from .version import app_version

# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

ESCAPING_NOTE = (
    "Text cells that begin with =, +, -, @, a tab or a carriage return, and are not "
    "plain numbers, have a single quote (') added in front so a spreadsheet shows "
    "them rather than running them as a formula. Remove the first character to get "
    "the recorded value."
)


def spreadsheet_safe(value: Any) -> Any:
    """``value`` as it may safely appear in a CSV cell. See the module docstring."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    if text.startswith(_FORMULA_TRIGGERS) and not _PLAIN_NUMBER.match(text):
        return "'" + text
    return text


def _write_csv(handle: IO[str], columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    names = list(columns)
    writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore", lineterminator="\n")
    # A header even when there are no rows, so the file opens in a spreadsheet
    # and says what it would have contained, rather than being empty and looking
    # like the download failed.
    writer.writeheader()
    for row in rows:
        writer.writerow({name: spreadsheet_safe(row.get(name)) for name in names})


def write_long_csv(handle: IO[str], rows: Iterable[dict[str, Any]]) -> None:
    _write_csv(handle, LONG_EXPORT_COLUMNS, rows)


# ---------------------------------------------------------------------------
# Codebooks
# ---------------------------------------------------------------------------

_VIEW_LEVELS = {
    "z+": "Seen from above (+Z), OpenSCAD Top",
    "z-": "Seen from below (-Z), OpenSCAD Bottom",
    "y-": "Seen from the front (-Y), OpenSCAD Front",
    "y+": "Seen from the back (+Y), OpenSCAD Back",
    "x-": "Seen from the right, the +X side, OpenSCAD Right. The token names the opposite side",
    "x+": "Seen from the left, the -X side, OpenSCAD Left. The token names the opposite side",
}

LONG_COLUMN_DOCS: dict[str, dict[str, Any]] = {
    "participant_code": {"Description": "The participant's code, P01, P02, ... Never a name."},
    "session_number": {"Description": "Which of this participant's sessions, from 1."},
    "study_session_id": {"Description": "The session's id in this study's database."},
    "seq": {
        "Description": (
            "Order within the session, one counter across events and renders, so a "
            "keypress and the render it caused sort in the order they happened."
        )
    },
    "timestamp": {"Description": "When it was recorded, UTC, millisecond precision (ISO 8601)."},
    "elapsed_ms": {"Description": "Time since the session started.", "Units": "ms"},
    "step_elapsed_ms": {"Description": "Time since the current step started.", "Units": "ms"},
    "phase": {"Description": "The part of the protocol: the step's part_id."},
    "step_id": {"Description": "The protocol step that was current."},
    "step_index": {"Description": "The step's position in the protocol, from 0."},
    "event_type": {
        "Description": (
            "What happened. 'render' means the display updated; anything else is an "
            "event such as 'keyboard', 'step_advance' or 'session_end'."
        )
    },
    "source": {
        "Description": "Who caused it.",
        "Levels": {
            "participant": "The participant's page",
            "experimenter": "The control panel",
            "server": "The server: renders, automatic model loads, sessions closed for inactivity",
        },
    },
    "key": {
        "Description": (
            "The key pressed, lowercase, on keyboard rows; blank otherwise. Count "
            "commands with this column rather than by diffing viewer state."
        )
    },
    "key_repeat": {
        "Description": "True for a keypress generated by holding the key down. Blank on rows that are not keys."
    },
    "key_shift": {
        "Description": "Whether Shift was held. Blank on rows that are not keys, and on keys recorded before it was."
    },
    "input_source": {"Description": "On render rows, what triggered the render."},
    "model": {
        "Description": (
            "The model on the display, by file stem. On rows that are not renders, "
            "the most recent render's value in the same session; blank before the first."
        )
    },
    "view": {
        "Description": "The viewer's view token, carried forward like model.",
        "Levels": _VIEW_LEVELS,
    },
    "render_mode": {
        "Description": "How the slice was drawn, carried forward like model.",
        "Levels": {
            "filled": "Filled",
            "outline": "Outline",
            "cut": "Cut",
            "x-ray": "X-Ray, a fourth mode R cycles through. Not folded into the others",
        },
    },
    "layout_mode": {"Description": "The view mode: single, side-by-side or slice graph."},
    "depth": {
        "Description": "How far into the object the slice is, from the surface nearest the reader.",
        "Units": "percent",
    },
    "zoom": {"Description": "The zoom level the viewer sent."},
    "cache_hit": {"Description": "On render rows, whether the render came from the cache."},
    "orientation_x": {
        "Description": "Rotation about X derived from orientation_basis, a multiple of 90.",
        "Units": "degrees",
    },
    "orientation_y": {"Description": "Rotation about Y, as orientation_x.", "Units": "degrees"},
    "orientation_z": {
        "Description": (
            "Rotation about Z, as orientation_x. Reported as 0 when orientation_y is 90 "
            "or 270, where a turn about X and one about Z cannot be told apart."
        ),
        "Units": "degrees",
    },
    "orientation_basis": {
        "Description": "The right, up and forward vectors the viewer stored, as JSON. The raw form of the three angles."
    },
    "axis_mode": {
        "Description": "The viewer's axis mode.",
        "Levels": {"turn": "Turn: pitch, roll and yaw", "xyz": "XYZ: pick an axis and a side"},
    },
    "cut_axis": {"Description": "The model axis the slices are cut along.", "Levels": {"x": "X", "y": "Y", "z": "Z"}},
    "cut_side": {
        "Description": "The side the cut is seen from.",
        "Levels": {
            "above": "Z, from above",
            "below": "Z, from below",
            "front": "Y, from the front",
            "back": "Y, from the back",
            "right": "X, from the right",
            "left": "X, from the left",
        },
    },
    "cut_percent": {
        "Description": (
            "How far along the object the cut is, from its lowest coordinate on the "
            "axis (0) to its highest (100). Unlike depth, it does not change when the "
            "same plane is seen from the other side."
        ),
        "Units": "percent",
    },
}

SESSION_COLUMNS: tuple[str, ...] = (
    "study_session_id",
    "participant_code",
    "session_number",
    "status",
    "mode",
    "task_order",
    "step_index",
    "started_at",
    "completed_at",
    "protocol_version",
    "protocol_hash",
    "app_version",
    "events",
    "renders",
    "log_file",
)

SESSION_COLUMN_DOCS: dict[str, dict[str, Any]] = {
    "study_session_id": {"Description": "The session's id in this study's database."},
    "participant_code": {"Description": "The participant's code."},
    "session_number": {"Description": "Which of this participant's sessions, from 1."},
    "status": {
        "Description": "How the session ended.",
        "Levels": {
            "completed": "Ended by the experimenter, or by the participant finishing the last step of a one-device session",
            "abandoned": "Ended without finishing, by the experimenter or after a long time with no activity",
            "active": "Still running when this was exported",
        },
    },
    "mode": {
        "Description": "How it was run.",
        "Levels": {
            "paired": "The experimenter drove it from the panel on a second machine",
            "solo": "One machine; the participant's ready button moved it on",
        },
    },
    "task_order": {"Description": "The tasks the session ran, in order, joined with +."},
    "step_index": {"Description": "The last step it was on, from 0."},
    "started_at": {"Description": "When it started, UTC (ISO 8601)."},
    "completed_at": {"Description": "When it ended, UTC (ISO 8601). Blank while active."},
    "protocol_version": {"Description": "The study's version when the session ran."},
    "protocol_hash": {
        "Description": (
            "A fingerprint of the protocol the session ran. Two sessions with the same "
            "version and different fingerprints ran different protocols. Blank on "
            "sessions recorded before it was."
        )
    },
    "app_version": {
        "Description": "The release or commit of the app that ran it. Blank on sessions recorded before it was."
    },
    "events": {"Description": "Event rows the database holds for the session."},
    "renders": {"Description": "Render rows the database holds for the session."},
    "log_file": {"Description": "The session's JSONL log, under logs/ in the archive."},
}


def long_codebook(study: Study) -> dict[str, Any]:
    """The long CSV's codebook, with this study's own phases and steps."""
    book = {name: dict(LONG_COLUMN_DOCS[name]) for name in LONG_EXPORT_COLUMNS}
    phases: dict[str, str] = {}
    steps: dict[str, str] = {}
    for step in study.steps:
        if step.get("part_id"):
            phases.setdefault(str(step["part_id"]), str(step.get("part_title") or step["part_id"]))
        if step.get("id"):
            steps[str(step["id"])] = str(step.get("title") or step["id"])
    if phases:
        book["phase"]["Levels"] = phases
    if steps:
        book["step_id"]["Levels"] = steps
    stems = protocol.required_models(study)
    if stems:
        book["model"]["Levels"] = {stem: stem for stem in stems}
    return book


def session_codebook(study: Study) -> dict[str, Any]:
    book = {name: dict(SESSION_COLUMN_DOCS[name]) for name in SESSION_COLUMNS}
    if study.tasks:
        book["task_order"]["Levels"] = {key: protocol.task_label(study, key) for key in study.tasks}
    return book


# ---------------------------------------------------------------------------
# Data checks
# ---------------------------------------------------------------------------


def _finding(check_id: str, title: str, explanation: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": check_id, "title": title, "explanation": explanation, "count": len(items), "items": items}


def run_checks(store: StudyStore, log_dir: Path | None = None) -> dict[str, Any]:
    """Look for the problems a study's data is known to be able to have.

    Two come from the review of the comparison study's code (#183): sessions
    ended twice, and participants issued a code but never a session. Both bugs
    are fixed, and the checks stay because data recorded before the fixes can
    still carry them. The rest are what an analysis would otherwise trip over:
    sessions still running, sessions with no recorded end, rows after the end,
    and a log file that disagrees with the database.

    Each finding lists what it found, so a problem can be traced to a session
    rather than only counted.
    """
    conn = store.connection()
    log_dir = log_dir if log_dir is not None else store.log_dir
    findings: list[dict[str, Any]] = []

    rows = conn.execute(
        """SELECT s.id, s.participant_code, s.status, COUNT(e.id) AS ends
           FROM study_sessions s
           JOIN study_events e ON e.study_session_id = s.id AND e.event_type = 'session_end'
           GROUP BY s.id HAVING COUNT(e.id) > 1 ORDER BY s.id"""
    ).fetchall()
    findings.append(
        _finding(
            "ended_twice",
            "Sessions with more than one session_end",
            "The idle sweep could end a session an experimenter had just ended, which "
            "added a second session_end and could overwrite 'completed' with "
            "'abandoned'. Check the status against the first session_end.",
            [{"study_session_id": r["id"], "participant_code": r["participant_code"],
              "status": r["status"], "session_end_events": r["ends"]} for r in rows],
        )
    )

    rows = conn.execute(
        """SELECT p.id, p.code FROM participants p
           WHERE NOT EXISTS (SELECT 1 FROM study_sessions s WHERE s.participant_id = p.id)
           ORDER BY p.id"""
    ).fetchall()
    findings.append(
        _finding(
            "participants_without_sessions",
            "Participant codes with no session",
            "A start request that was then refused could still issue a code, and each "
            "one moved later participants one place along the rotation. Nobody was run "
            "under these codes.",
            [{"participant_id": r["id"], "participant_code": r["code"]} for r in rows],
        )
    )

    rows = conn.execute(
        """SELECT s.id, s.participant_code, s.status FROM study_sessions s
           WHERE s.status IN ('completed', 'abandoned')
             AND NOT EXISTS (SELECT 1 FROM study_events e
                             WHERE e.study_session_id = s.id AND e.event_type = 'session_end')
           ORDER BY s.id"""
    ).fetchall()
    findings.append(
        _finding(
            "ended_without_event",
            "Ended sessions with no session_end",
            "The status says the session ended, but nothing recorded when or how.",
            [{"study_session_id": r["id"], "participant_code": r["participant_code"],
              "status": r["status"]} for r in rows],
        )
    )

    rows = conn.execute(
        """SELECT id, participant_code, started_at, step_index FROM study_sessions
           WHERE status = 'active' ORDER BY id"""
    ).fetchall()
    findings.append(
        _finding(
            "still_active",
            "Sessions still running",
            "Not in long.csv, which has completed sessions only. A session left open "
            "is closed as abandoned after twelve hours without activity.",
            [{"study_session_id": r["id"], "participant_code": r["participant_code"],
              "started_at": r["started_at"], "step_index": r["step_index"]} for r in rows],
        )
    )

    rows = conn.execute(
        """WITH ends AS (
               SELECT study_session_id, MIN(seq) AS end_seq FROM study_events
               WHERE event_type = 'session_end' GROUP BY study_session_id
           ),
           rows_after AS (
               SELECT e.study_session_id FROM study_events e
               JOIN ends ON ends.study_session_id = e.study_session_id
               WHERE e.seq > ends.end_seq AND e.event_type != 'session_end'
               UNION ALL
               SELECT r.study_session_id FROM study_renders r
               JOIN ends ON ends.study_session_id = r.study_session_id
               WHERE r.seq > ends.end_seq
           )
           SELECT study_session_id, COUNT(*) AS n FROM rows_after
           GROUP BY study_session_id ORDER BY study_session_id"""
    ).fetchall()
    findings.append(
        _finding(
            "rows_after_end",
            "Rows recorded after a session ended",
            "Interactions logged after the first session_end. Usually a second end "
            "from the problem above; otherwise worth a look before analysis.",
            [{"study_session_id": r["study_session_id"], "rows": r["n"]} for r in rows],
        )
    )

    rows = conn.execute(
        """SELECT study_session_id, seq, COUNT(*) AS n FROM (
               SELECT study_session_id, seq FROM study_events
               UNION ALL
               SELECT study_session_id, seq FROM study_renders
           ) GROUP BY study_session_id, seq HAVING COUNT(*) > 1
           ORDER BY study_session_id, seq"""
    ).fetchall()
    findings.append(
        _finding(
            "duplicate_seq",
            "Order numbers used twice",
            "seq should be unique within a session. A duplicate means two rows cannot "
            "be put in order from the data alone.",
            [{"study_session_id": r["study_session_id"], "seq": r["seq"], "rows": r["n"]} for r in rows],
        )
    )

    mismatches: list[dict[str, Any]] = []
    for session in store.all_sessions():
        counts = store.session_counts(int(session["id"]))
        in_db = counts["events"] + counts["renders"]
        name = Path(str(session.get("log_path") or "")).name
        path = log_dir / name if name else None
        if path is None or not path.is_file():
            if in_db:
                mismatches.append({"study_session_id": session["id"], "log_file": name or None,
                                   "database_rows": in_db, "log_lines": None})
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = sum(1 for line in handle if line.strip())
        if lines != in_db:
            mismatches.append({"study_session_id": session["id"], "log_file": name,
                               "database_rows": in_db, "log_lines": lines})
    findings.append(
        _finding(
            "log_mismatch",
            "Sessions whose log and database disagree",
            "Every interaction is written to both. More log lines than rows means "
            "database writes failed and the log has what is missing; fewer means log "
            "writes failed. A missing file is shown with no line count.",
            mismatches,
        )
    )

    return {
        "checked_at": _utc_now(),
        "problems": sum(1 for finding in findings if finding["count"] and finding["id"] != "still_active"),
        "findings": findings,
    }


def summarise_checks(report: dict[str, Any]) -> str:
    """The checks as plain lines, for the command line."""
    lines = []
    for finding in report["findings"]:
        mark = "ok  " if finding["count"] == 0 else "FIND"
        lines.append(f"{mark} {finding['title']}: {finding['count']}")
        for item in finding["items"][:20]:
            lines.append("       " + ", ".join(f"{k}={v}" for k, v in item.items()))
        if finding["count"] > 20:
            lines.append(f"       ... and {finding['count'] - 20} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Copies and the archive
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_for_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def snapshot_database(source: Path, target: Path) -> None:
    """A consistent copy of ``source``, including what is still in its WAL.

    Opened read-only where SQLite allows it. A WAL database in a directory the
    reader cannot write to can refuse that, and then a normal connection is
    used; it reads the same way and writes nothing but SQLite's own index file.
    """
    try:
        reader = sqlite3.connect(f"{Path(source).resolve().as_uri()}?mode=ro", uri=True)
        reader.execute("SELECT 1 FROM sqlite_master").fetchone()
    except sqlite3.OperationalError:
        reader = sqlite3.connect(str(source))
    try:
        writer = sqlite3.connect(str(target))
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()


@contextmanager
def readable_copy(db_path: Path, log_dir: Path) -> Iterator[tuple[Path, StudyStore]]:
    """A snapshot of ``db_path``, and a store over a second copy brought up to
    the current schema. Both are deleted afterwards."""
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    with tempfile.TemporaryDirectory(prefix="cad-a11y-study-export-") as scratch:
        snapshot = Path(scratch) / "study.db"
        snapshot_database(Path(db_path), snapshot)
        working = Path(scratch) / "working.db"
        shutil.copyfile(snapshot, working)
        store = StudyStore(working, Path(log_dir))
        store.init_db()
        yield snapshot, store


def _session_rows(store: StudyStore) -> list[dict[str, Any]]:
    rows = []
    for session in store.all_sessions():
        counts = store.session_counts(int(session["id"]))
        rows.append(
            {
                "study_session_id": session.get("id"),
                "participant_code": session.get("participant_code"),
                "session_number": session.get("session_number"),
                "status": session.get("status"),
                "mode": session.get("mode"),
                "task_order": protocol.set_id(session.get("task_order") or []),
                "step_index": session.get("step_index"),
                "started_at": session.get("started_at"),
                "completed_at": session.get("completed_at"),
                "protocol_version": session.get("protocol_version"),
                "protocol_hash": session.get("protocol_hash"),
                "app_version": session.get("app_version"),
                "events": counts["events"],
                "renders": counts["renders"],
                "log_file": Path(str(session.get("log_path") or "")).name or None,
            }
        )
    return rows


def _readme(study: Study) -> str:
    return "\n".join(
        [
            f"{study.title} ({study.slug}), exported by cad-a11y {app_version()} at {_utc_now()}.",
            "",
            "study.db       A consistent copy of the study's SQLite database, unmodified.",
            "logs/          One JSONL file per session: every interaction with the viewer state",
            "               at that moment. The authoritative record if the two disagree.",
            "long.csv       One row per interaction, completed sessions only.",
            "long.json      What each column of long.csv means.",
            "sessions.csv   One row per session, every status.",
            "sessions.json  What each column of sessions.csv means.",
            "checks.json    Known problems looked for, and what was found.",
            "manifest.json  Counts, and a SHA-256 for every file here.",
            "",
            ESCAPING_NOTE,
            "",
        ]
    )


def build_archive(
    study: Study,
    db_path: Path,
    log_dir: Path,
    *,
    target: IO[bytes] | None = None,
) -> IO[bytes]:
    """Everything about a study, as a zip written to ``target`` (a temporary
    file when omitted), positioned at its start and ready to send."""
    log_dir = Path(log_dir)
    archive = target if target is not None else tempfile.TemporaryFile()
    with readable_copy(Path(db_path), log_dir) as (snapshot, store):
        files: dict[str, bytes] = {}

        long_buffer = io.StringIO()
        write_long_csv(long_buffer, store.export_long_rows())
        files["long.csv"] = long_buffer.getvalue().encode("utf-8")
        files["long.json"] = _json_bytes(long_codebook(study))

        sessions = _session_rows(store)
        session_buffer = io.StringIO()
        _write_csv(session_buffer, SESSION_COLUMNS, sessions)
        files["sessions.csv"] = session_buffer.getvalue().encode("utf-8")
        files["sessions.json"] = _json_bytes(session_codebook(study))

        checks = run_checks(store, log_dir)
        files["checks.json"] = _json_bytes(checks)
        files["README.txt"] = _readme(study).encode("utf-8")

        log_files = sorted(log_dir.glob("*.jsonl")) if log_dir.is_dir() else []
        conn = store.connection()
        statuses = {
            row["status"]: row["n"]
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM study_sessions GROUP BY status")
        }

        manifest_files: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, "study.db")
            manifest_files["study.db"] = _describe(snapshot.read_bytes())
            for path in log_files:
                data = path.read_bytes()
                bundle.writestr(f"logs/{path.name}", data)
                manifest_files[f"logs/{path.name}"] = _describe(data)
            for name, data in files.items():
                bundle.writestr(name, data)
                manifest_files[name] = _describe(data)
            manifest = {
                "study": {
                    "slug": study.slug,
                    "title": study.title,
                    "status": study.status.value,
                    "version": study.version,
                    "protocol_hash": protocol.protocol_hash(study),
                    "instrument_tag": study.instrument_tag,
                },
                "exported_at": _utc_now(),
                "exported_by": {"app": "cad-a11y", "version": app_version()},
                "counts": {
                    "participants": conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0],
                    "sessions": statuses,
                    "events": conn.execute("SELECT COUNT(*) FROM study_events").fetchone()[0],
                    "renders": conn.execute("SELECT COUNT(*) FROM study_renders").fetchone()[0],
                    "long_csv_rows": max(0, files["long.csv"].count(b"\n") - 1),
                    "log_files": len(log_files),
                },
                "problems_found": checks["problems"],
                "escaping": ESCAPING_NOTE,
                "files": manifest_files,
            }
            bundle.writestr("manifest.json", _json_bytes(manifest))
    archive.seek(0)
    return archive


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def _describe(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
