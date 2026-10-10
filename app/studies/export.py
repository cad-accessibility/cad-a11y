"""Getting a study's data out, and checking it on the way.

One function builds everything: ``build_archive`` makes a zip holding

  study.db             a consistent copy of the database, as the server had it
  logs/*.jsonl         every session log, the authoritative record
  long.csv             one row per interaction, completed sessions only
  long_incomplete.csv  the same columns, for the sessions long.csv leaves out
                       that nothing is still writing to
  long.json            the codebook for both
  sessions.csv         one row per session, every status
  sessions.json        the codebook for sessions.csv
  checks.json          the data checks below, and what they found
  manifest.json        what the archive is, the counts, and a SHA-256 per file
  README.txt           the same list, for whoever opens the archive in a year

The control panel serves it, and so does ``python -m app.studies export``, which
also reads a database restored from a backup. The codebooks follow the shape of
a BIDS sidecar: one entry per column, with a description, its levels where it
has a fixed set, and its units.

Reading leaves the original alone. The database is copied with SQLite's backup
API, which includes anything still in the write-ahead log, and that copy goes
into the archive untouched. Only then is the same copy brought up to the current
schema and everything else computed from it, so a database written by older code
exports the same columns as a new one, the original is never migrated, and one
copy is made rather than two. Rows are written straight to files as they are
read, a session at a time, so a large study is never held in memory whole
(#258 review).

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
import json
import re
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from . import protocol
from .definition import Status, Study
from .store import LONG_EXPORT_COLUMNS, StudyStore, idle_timeout_seconds
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


def _write_csv(handle: IO[str], columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> int:
    """Write the rows as they come, and say how many there were."""
    names = list(columns)
    writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore", lineterminator="\n")
    # A header even when there are no rows, so the file opens in a spreadsheet
    # and says what it would have contained, rather than being empty and looking
    # like the download failed.
    writer.writeheader()
    count = 0
    for row in rows:
        writer.writerow({name: spreadsheet_safe(row.get(name)) for name in names})
        count += 1
    return count


def write_long_csv(handle: IO[str], rows: Iterable[dict[str, Any]]) -> int:
    return _write_csv(handle, LONG_EXPORT_COLUMNS, rows)


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
    "cut_axis": {"Description": "The model axis the slices are taken along.", "Levels": {"x": "X", "y": "Y", "z": "Z"}},
    "cut_side": {
        "Description": "The side the slice is seen from.",
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
            "How far along the object the slice is, from its lowest coordinate on the "
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
    "view_convention",
    "events",
    "renders",
    "log_file",
)

SESSION_COLUMN_DOCS: dict[str, dict[str, Any]] = {
    "study_session_id": {"Description": "The session's id in this study's database."},
    "participant_code": {"Description": "The participant's code."},
    "session_number": {"Description": "Which of this participant's sessions, from 1."},
    "status": {
        "Description": (
            "How the session ended. long.csv has the completed sessions; long_incomplete.csv "
            "has the abandoned ones, and the active ones of a study that is no longer running."
        ),
        "Levels": {
            "completed": (
                "Reached the last step and was ended there, by the experimenter or by the participant "
                "finishing it in a one-device session. Before 2026-10, End also recorded 'completed' "
                "before the last step, so for earlier sessions step_index says how far each got"
            ),
            "abandoned": (
                "Ended before the last step: by the experimenter, or closed after a long time with no "
                "activity (STUDY_SESSION_IDLE_HOURS, 12 by default) while the study was open"
            ),
            "active": (
                "Not ended when this was exported. Still running in an open study; in a closed or "
                "retired one, left open when it stopped running, and nothing will end it now"
            ),
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
    "view_convention": {
        "Description": (
            "Which side of the orientation fix (#185) the session was recorded on, read from the "
            "forward vector of its renders in the front (y-), back (y+) and bottom (z-) views. "
            "Blank when it never used those views, which read the same on either side."
        ),
        "Levels": {
            "before #185": (
                "Depth in y-, y+ and z- measured from the far side (the same plane reads as 100 "
                "minus it after the fix), and those views' angles come from mirrored bases"
            ),
            "after #185": "Depth from the surface nearest the reader in every view",
            "mixed": "Rows of both kinds, which no single release records. Worth a look",
        },
    },
    "events": {"Description": "Event rows the database holds for the session."},
    "renders": {"Description": "Render rows the database holds for the session."},
    "log_file": {"Description": "The session's JSONL log, under logs/ in the archive."},
}


BEFORE_VIEW_FIX_NOTE = (
    "Some sessions here were recorded before the orientation fix (#185); checks.json and the "
    "view_convention column of sessions.csv say which. In those sessions the front (y-), back "
    "(y+) and bottom (z-) views measured depth from the far side, so the same plane reads as "
    "100 minus the depth recorded after the fix, and stored mirrored bases, so the orientation "
    "angles of those rows describe no turn that could have happened. Do not pool them with "
    "later sessions without converting; docs/STUDY_DATA_EXPORT.md in the repository explains."
)


def long_codebook(study: Study, *, before_view_fix: bool = False) -> dict[str, Any]:
    """The long CSVs' codebook, with this study's own phases and steps.

    ``before_view_fix`` says some sessions predate #185. The depth and angle
    entries then say so where they are read, since someone holding only the
    archive would otherwise read those sessions' depths backwards (#258 review).
    """
    book = {name: dict(LONG_COLUMN_DOCS[name]) for name in LONG_EXPORT_COLUMNS}
    if before_view_fix:
        for name in ("depth", "orientation_x", "orientation_y", "orientation_z", "orientation_basis"):
            book[name]["Description"] = f"{book[name]['Description']} {BEFORE_VIEW_FIX_NOTE}"
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


# Findings that describe the data rather than a fault in it. They are listed,
# and not counted as problems.
_INFORMATIONAL = frozenset({"still_active", "before_view_fix"})


def _stops_running(study: Study | None) -> bool:
    """Whether nothing will end this study's sessions now: a closed or retired
    study runs no idle sweep, and its database is only ever read."""
    return study is not None and study.status in (Status.CLOSED, Status.RETIRED)


def run_checks(
    store: StudyStore,
    log_dir: Path | None = None,
    *,
    study: Study | None = None,
    export_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Look for the problems a study's data is known to be able to have.

    Two come from the review of the comparison study's code (#183): sessions
    ended twice, and participants issued a code but never a session. Both bugs
    are fixed, and the checks stay because data recorded before the fixes can
    still carry them. The rest are what an analysis would otherwise trip over:
    sessions still running, sessions with no recorded end, rows after the end,
    a log file that disagrees with the database, sessions from before the
    orientation fix (#185), and steps whose object never reached the display.

    ``study`` says what the server does with the study, which changes what a
    session left running means, and lets the steps be checked against their
    models. ``export_failures`` lists sessions an export could not read; the
    archive passes it.

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
    if _stops_running(study):
        # The old explanation promised an idle sweep that a closed study never
        # runs, so these would have stayed out of every file for good (#258 review).
        active_explanation = (
            "The study stopped running while these were open, and nothing will end them now. "
            "long.csv has completed sessions only; their rows are in long_incomplete.csv, and "
            "step_index says how far each got."
        )
    else:
        hours = idle_timeout_seconds() / 3600
        active_explanation = (
            "Still being written to, so in neither long.csv nor long_incomplete.csv. A session "
            f"left open is closed as abandoned after {hours:g} hours without activity "
            "(STUDY_SESSION_IDLE_HOURS on the server), and its rows then go to long_incomplete.csv."
        )
    findings.append(
        _finding(
            "still_active",
            "Sessions still active",
            active_explanation,
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

    codes = {int(session["id"]): session.get("participant_code") for session in store.all_sessions()}
    findings.append(
        _finding(
            "before_view_fix",
            "Sessions recorded before the orientation fix (#185)",
            "In the front (y-), back (y+) and bottom (z-) views these measured depth from the far "
            "side, so the same plane reads as 100 minus the depth recorded after the fix, and they "
            "stored mirrored bases, so those rows' orientation angles describe no real turn. Told "
            "apart by the forward vector stored with each render in those views. Sessions not "
            "listed were recorded after the fix, or never used those views and read the same "
            "either way. docs/STUDY_DATA_EXPORT.md says how to convert.",
            [
                {"study_session_id": session_id, "participant_code": codes.get(session_id), "recorded": side}
                for session_id, side in sorted(view_conventions(store).items())
                if side != "after #185"
            ],
        )
    )

    if study is not None:
        findings.append(
            _finding(
                "step_model_never_shown",
                "Steps whose object never reached the display",
                "Every render in the step showed some other object. Usually the step's model was "
                "missing from the server, and the previous step's object stayed on the display: "
                "its rows are filed under this step with that object's name.",
                _steps_without_their_model(store, study),
            )
        )

    if export_failures is not None:
        findings.append(
            _finding(
                "export_failed",
                "Sessions the export could not read",
                "Missing from long.csv and long_incomplete.csv. study.db and the logs in the "
                "archive still hold them. A session used to be left out of the file without a word.",
                export_failures,
            )
        )

    return {
        "checked_at": _utc_now(),
        "problems": sum(1 for finding in findings if finding["count"] and finding["id"] not in _INFORMATIONAL),
        "findings": findings,
    }


# The views the orientation fix changed, and where their forward vector points
# after it: at the reader. Before it, the opposite way (docs/STUDY_DATA_EXPORT.md).
_FIXED_VIEW_FORWARD = {"y-": (0, -1, 0), "y+": (0, 1, 0), "z-": (0, 0, -1)}


def view_conventions(store: StudyStore) -> dict[int, str]:
    """Which side of the orientation fix (#185) each session was recorded on,
    read from its renders in the three views the fix changed. A session with no
    render there is left out: nothing it recorded reads differently."""
    sides: dict[int, set[str]] = {}
    rows = store.connection().execute(
        "SELECT study_session_id, view, orientation FROM study_renders "
        "WHERE view IN ('y-', 'y+', 'z-') AND orientation IS NOT NULL"
    )
    for row in rows:
        try:
            forward = json.loads(row["orientation"]).get("forward")
            vector = tuple(round(float(value)) for value in forward)
        except (TypeError, ValueError, AttributeError):
            continue
        after = _FIXED_VIEW_FORWARD[row["view"]]
        if vector == after:
            side = "after #185"
        elif vector == tuple(-value for value in after):
            side = "before #185"
        else:
            continue
        sides.setdefault(int(row["study_session_id"]), set()).add(side)
    return {session_id: "mixed" if len(found) > 1 else next(iter(found)) for session_id, found in sides.items()}


def _steps_without_their_model(store: StudyStore, study: Study) -> list[dict[str, Any]]:
    """Steps with renders, none of them of the step's own model (#258 review).
    A render of the previous object just after a step change is normal, which is
    why this asks whether the right object appeared at all."""
    conn = store.connection()
    items: list[dict[str, Any]] = []
    for session in store.all_sessions():
        try:
            steps = protocol.resolve_steps(study, session.get("task_order") or [])
        except Exception:  # noqa: BLE001, S112 - a task order this definition no longer resolves is checked no further
            continue
        expected = {
            step["id"]: (step.get("model") or {}).get("model")
            for step in steps
            if (step.get("model") or {}).get("model")
        }
        if not expected:
            continue
        shown: dict[str, set[str]] = {}
        for row in conn.execute(
            "SELECT step_id, model FROM study_renders WHERE study_session_id = ?", (session["id"],)
        ):
            if row["step_id"] in expected:
                shown.setdefault(row["step_id"], set()).add(row["model"] or "")
        for step_id, models in shown.items():
            if expected[step_id] not in models:
                items.append(
                    {
                        "study_session_id": session["id"],
                        "participant_code": session.get("participant_code"),
                        "step_id": step_id,
                        "expected_model": expected[step_id],
                        "models_shown": sorted(model for model in models if model),
                    }
                )
    return items


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
    """A copy of ``db_path`` brought up to the current schema, and a store over
    it, for reading. Deleted afterwards; the original is only ever read."""
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    with tempfile.TemporaryDirectory(prefix="cad-a11y-study-export-") as scratch:
        copy = Path(scratch) / "study.db"
        snapshot_database(Path(db_path), copy)
        store = StudyStore(copy, Path(log_dir))
        store.init_db()
        yield copy, store


def _session_rows(store: StudyStore, conventions: dict[int, str]) -> Iterator[dict[str, Any]]:
    for session in store.all_sessions():
        counts = store.session_counts(int(session["id"]))
        yield {
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
            "view_convention": conventions.get(int(session["id"])),
            "events": counts["events"],
            "renders": counts["renders"],
            "log_file": Path(str(session.get("log_path") or "")).name or None,
        }


def _readme(study: Study, *, before_view_fix: bool) -> str:
    lines = [
        f"{study.title} ({study.slug}), exported by cad-a11y {app_version()} at {_utc_now()}.",
        "",
        "study.db             A consistent copy of the study's SQLite database, unmodified.",
        "logs/                One JSONL file per session: every interaction with the viewer",
        "                     state at that moment. The authoritative record if the two disagree.",
        "long.csv             One row per interaction, completed sessions only.",
        "long_incomplete.csv  The same columns for the sessions long.csv leaves out that nothing",
        "                     is still writing to: the abandoned ones, and, once a study has",
        "                     stopped running, the ones left active. Kept apart so that using",
        "                     them is a decision; sessions.csv gives each one's status.",
        "long.json            What each column of the two long files means.",
        "sessions.csv         One row per session, every status.",
        "sessions.json        What each column of sessions.csv means.",
        "checks.json          Known problems looked for, and what was found.",
        "manifest.json        Counts, and a SHA-256 for every file here.",
        "",
        ESCAPING_NOTE,
        "",
    ]
    if before_view_fix:
        lines += [BEFORE_VIEW_FIX_NOTE, ""]
    return "\n".join(lines)


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
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    # Not a with block: the file is handed back open, and send_file closes it.
    archive = target if target is not None else tempfile.TemporaryFile()  # noqa: SIM115
    with tempfile.TemporaryDirectory(prefix="cad-a11y-study-export-") as scratch_dir:
        scratch = Path(scratch_dir)
        snapshot = scratch / "study.db"
        snapshot_database(Path(db_path), snapshot)
        manifest_files: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            # The copy as the server had it goes in first, untouched. Only then
            # is the same file brought up to the current schema to work out the
            # rest from.
            _add_file(bundle, snapshot, "study.db", manifest_files)
            store = StudyStore(snapshot, log_dir)
            store.init_db()

            failures: list[dict[str, Any]] = []

            def note_failure(session: dict[str, Any], error: Exception) -> None:
                failures.append(
                    {
                        "study_session_id": session.get("id"),
                        "participant_code": session.get("participant_code"),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )

            row_counts: dict[str, int] = {}
            for name, sessions in (
                ("long.csv", store.completed_sessions()),
                ("long_incomplete.csv", store.incomplete_sessions(include_active=_stops_running(study))),
            ):
                path = scratch / name
                with path.open("w", encoding="utf-8", newline="") as handle:
                    row_counts[name] = write_long_csv(handle, store.iter_long_rows(sessions, on_error=note_failure))
                _add_file(bundle, path, name, manifest_files)

            conventions = view_conventions(store)
            sessions_path = scratch / "sessions.csv"
            with sessions_path.open("w", encoding="utf-8", newline="") as handle:
                _write_csv(handle, SESSION_COLUMNS, _session_rows(store, conventions))
            _add_file(bundle, sessions_path, "sessions.csv", manifest_files)

            checks = run_checks(store, log_dir, study=study, export_failures=failures)
            before_view_fix = any(side != "after #185" for side in conventions.values())
            _add_bytes(bundle, "long.json", _json_bytes(long_codebook(study, before_view_fix=before_view_fix)), manifest_files)
            _add_bytes(bundle, "sessions.json", _json_bytes(session_codebook(study)), manifest_files)
            _add_bytes(bundle, "checks.json", _json_bytes(checks), manifest_files)
            _add_bytes(bundle, "README.txt", _readme(study, before_view_fix=before_view_fix).encode("utf-8"), manifest_files)

            log_files = sorted(log_dir.glob("*.jsonl")) if log_dir.is_dir() else []
            for path in log_files:
                _add_file(bundle, path, f"logs/{path.name}", manifest_files)

            conn = store.connection()
            statuses = {
                row["status"]: row["n"]
                for row in conn.execute("SELECT status, COUNT(*) AS n FROM study_sessions GROUP BY status")
            }
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
                    "long_csv_rows": row_counts["long.csv"],
                    "long_incomplete_csv_rows": row_counts["long_incomplete.csv"],
                    "sessions_not_exported": len(failures),
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


def _add_bytes(bundle: zipfile.ZipFile, name: str, data: bytes, manifest: dict[str, dict[str, Any]]) -> None:
    bundle.writestr(name, data)
    manifest[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _add_file(bundle: zipfile.ZipFile, path: Path, name: str, manifest: dict[str, dict[str, Any]]) -> None:
    """Put a file into the archive from disk, and hash it as it streams past:
    a database or a log is never read into memory whole (#258 review)."""
    bundle.write(path, name)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    manifest[name] = {"bytes": size, "sha256": digest.hexdigest()}
