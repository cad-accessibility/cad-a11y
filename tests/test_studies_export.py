"""Getting a study's data out: escaping, codebooks, data checks, the archive,
and the command line.

The comparison study's data has to come off two servers nobody has shell access
to, written by older code than this, and land somewhere the IRB protocol names
with nothing lost and nothing a spreadsheet would run. These check each step.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from app.studies import export, tokens
from app.studies.__main__ import main as cli
from app.studies.definitions import example
from app.studies.store import LONG_EXPORT_COLUMNS, StudyStore

STUDY = example.STUDY


@pytest.fixture()
def store(tmp_path):
    made = StudyStore(tmp_path / "study.db", tmp_path / "logs")
    made.init_db()
    yield made
    made._local.__dict__.clear()


def _session(store, code=None, order=("chair", "washer", "cube")):
    _, session = store.enroll(
        code=code, session_number=1, choose_task_order=lambda _: list(order), protocol_version="1"
    )
    return int(session["id"])


def _render(store, session_id, **overrides):
    params = {
        "model": "rocking_chair", "view": "y-", "render_mode": "Cut", "layout_mode": "single",
        "depth": 50, "zoom": 0, "input_source": "keyboard", "cache_hit": False,
    }
    params.update(overrides)
    return store.record_render(session_id, **params)


def _complete(store, session_id):
    assert store.end_session(session_id, status="completed", source="experimenter")


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"),
        ("+cmd", "'+cmd"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tleading tab", "'\tleading tab"),
        ("\rleading return", "'\rleading return"),
        ("=", "'="),
        ("-", "'-"),
        # Plain numbers are numbers to a spreadsheet, not formulas.
        ("-12.5", "-12.5"),
        ("+3", "+3"),
        ("-1e-3", "-1e-3"),
        (-12.5, -12.5),
        (0, 0),
        (True, True),
        (None, None),
        ("arrowup", "arrowup"),
        ('{"right": [0, -1, 0]}', '{"right": [0, -1, 0]}'),
    ],
)
def test_cells_a_spreadsheet_would_run_are_escaped(value, expected):
    assert export.spreadsheet_safe(value) == expected


def test_the_long_csv_escapes_what_a_participants_browser_sent(store):
    """``key`` and the render state are whatever the page sent, and the
    participant code is whatever the experimenter typed."""
    session_id = _session(store, code="=1+1")
    for key in ("=", "@", "+", "-", "arrowup"):
        store.record_event(session_id, "keyboard", event_data={"key": key})
    _render(store, session_id, view="=cmd|' /C calc'!A0", depth=-5.0)
    _complete(store, session_id)

    buffer = io.StringIO()
    export.write_long_csv(buffer, store.export_long_rows())
    rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
    assert {row["participant_code"] for row in rows} == {"'=1+1"}
    keys = [row["key"] for row in rows if row["event_type"] == "keyboard"]
    assert keys == ["'=", "'@", "'+", "'-", "arrowup"]
    render = next(row for row in rows if row["event_type"] == "render")
    assert render["view"].startswith("'=")
    assert render["depth"] == "-5.0", "a negative number must stay a number"


# ---------------------------------------------------------------------------
# Codebooks
# ---------------------------------------------------------------------------


def test_every_long_column_is_in_the_codebook():
    book = export.long_codebook(STUDY)
    assert list(book) == list(LONG_EXPORT_COLUMNS)
    assert all(entry.get("Description") for entry in book.values())


def test_every_session_column_is_in_its_codebook():
    book = export.session_codebook(STUDY)
    assert list(book) == list(export.SESSION_COLUMNS)
    assert book["task_order"]["Levels"] == {"chair": "Rocking chair", "washer": "Sleeve washer", "cube": "Labelled cube"}


def test_the_codebook_carries_the_studys_own_phases_and_steps():
    book = export.long_codebook(STUDY)
    assert book["phase"]["Levels"]["task1"] == "Task 1"
    assert book["step_id"]["Levels"]["task1.explore"] == "Explore the object"
    assert "rocking_chair" in book["model"]["Levels"]


def test_the_view_token_that_reads_backwards_is_said_to():
    assert "opposite" in export.LONG_COLUMN_DOCS["view"]["Levels"]["x-"]


# ---------------------------------------------------------------------------
# Data checks
# ---------------------------------------------------------------------------


def _finding(report, check_id):
    return next(finding for finding in report["findings"] if finding["id"] == check_id)


def test_clean_data_has_no_problems(store):
    session_id = _session(store)
    store.record_event(session_id, "keyboard", event_data={"key": "r"})
    _render(store, session_id)
    _complete(store, session_id)
    report = export.run_checks(store)
    assert report["problems"] == 0, export.summarise_checks(report)


def test_each_known_problem_is_found_and_traced_to_its_session(store):
    ended_twice = _session(store, code="P-TWICE")
    _complete(store, ended_twice)
    store.record_event(ended_twice, "session_end", source="server", event_data={"status": "abandoned"})

    orphan = store.create_participant("P-ORPHAN")

    no_end = _session(store, code="P-NOEND")
    store.complete_session(no_end, status="completed")

    running = _session(store, code="P-RUNNING")

    after = _session(store, code="P-AFTER")
    _complete(store, after)
    store.record_event(after, "keyboard", event_data={"key": "r"})

    duplicated = _session(store, code="P-DUP")
    store.record_event(duplicated, "keyboard")
    store.connection().execute(
        "INSERT INTO study_renders (study_session_id, participant_id, participant_code, seq) "
        "SELECT study_session_id, participant_id, participant_code, MAX(seq) FROM study_events "
        "WHERE study_session_id = ?",
        (duplicated,),
    )
    store.connection().commit()

    report = export.run_checks(store)
    assert [i["study_session_id"] for i in _finding(report, "ended_twice")["items"]] == [ended_twice]
    assert [i["participant_code"] for i in _finding(report, "participants_without_sessions")["items"]] == [orphan["code"]]
    assert [i["study_session_id"] for i in _finding(report, "ended_without_event")["items"]] == [no_end]
    assert running in [i["study_session_id"] for i in _finding(report, "still_active")["items"]]
    after_items = {i["study_session_id"]: i["rows"] for i in _finding(report, "rows_after_end")["items"]}
    assert after_items.get(after) == 1
    assert [i["study_session_id"] for i in _finding(report, "duplicate_seq")["items"]] == [duplicated]
    # The inserted render went to the database only, so its log disagrees.
    assert duplicated in [i["study_session_id"] for i in _finding(report, "log_mismatch")["items"]]
    assert report["problems"] >= 6


def test_a_missing_log_file_is_found(store):
    session_id = _session(store)
    store.record_event(session_id, "keyboard")
    for path in store.log_dir.glob("*.jsonl"):
        path.unlink()
    items = _finding(export.run_checks(store), "log_mismatch")["items"]
    assert items == [
        {"study_session_id": session_id, "log_file": items[0]["log_file"], "database_rows": 1, "log_lines": None}
    ]


def test_sessions_still_running_are_reported_but_are_not_a_problem(store):
    _session(store)
    report = export.run_checks(store)
    assert _finding(report, "still_active")["count"] == 1
    assert report["problems"] == 0


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def _archive(store):
    target = io.BytesIO()
    export.build_archive(STUDY, store.db_path, store.log_dir, target=target)
    return zipfile.ZipFile(io.BytesIO(target.getvalue()))


def test_the_archive_holds_everything_with_a_checksum_for_each(store):
    session_id = _session(store)
    store.record_event(session_id, "keyboard", event_data={"key": "r"})
    _render(store, session_id)
    _complete(store, session_id)

    with _archive(store) as bundle:
        names = set(bundle.namelist())
        assert {"study.db", "long.csv", "long.json", "sessions.csv", "sessions.json",
                "checks.json", "README.txt", "manifest.json"} <= names
        logs = [name for name in names if name.startswith("logs/")]
        assert len(logs) == 1

        manifest = json.loads(bundle.read("manifest.json"))
        for name, described in manifest["files"].items():
            data = bundle.read(name)
            assert described["bytes"] == len(data)
            assert described["sha256"] == hashlib.sha256(data).hexdigest(), name
        assert set(manifest["files"]) == names - {"manifest.json"}

        assert manifest["study"]["slug"] == "example"
        assert manifest["counts"]["participants"] == 1
        assert manifest["counts"]["sessions"] == {"completed": 1}
        assert manifest["counts"]["long_csv_rows"] == len(
            list(csv.DictReader(io.StringIO(bundle.read("long.csv").decode("utf-8"))))
        )
        sessions = list(csv.DictReader(io.StringIO(bundle.read("sessions.csv").decode("utf-8"))))
        assert sessions[0]["status"] == "completed"
        assert sessions[0]["task_order"] == "chair+washer+cube"


def test_the_database_in_the_archive_includes_what_was_still_in_the_wal(store):
    """Copying study.db on its own lost a third of a real session's rows once:
    they were still in the write-ahead log."""
    session_id = _session(store)
    for _ in range(25):
        store.record_event(session_id, "keyboard")
    with _archive(store) as bundle:
        data = bundle.read("study.db")
    copy = store.db_path.parent / "from-archive.db"
    copy.write_bytes(data)
    conn = sqlite3.connect(str(copy))
    try:
        assert conn.execute("SELECT COUNT(*) FROM study_events").fetchone()[0] == 25
    finally:
        conn.close()


def _legacy_database(path):
    """A database as a server running the comparison study left it: before the
    axis columns of #185 and the version columns of this change."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE participants (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE,
            first_session_at DATETIME, created_at DATETIME);
        CREATE TABLE study_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL, participant_code TEXT NOT NULL,
            session_number INTEGER NOT NULL DEFAULT 1, participant_key TEXT,
            mode TEXT NOT NULL DEFAULT 'paired', task_order TEXT NOT NULL,
            protocol_version TEXT, step_index INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active', log_path TEXT, started_at DATETIME,
            step_started_at DATETIME, completed_at DATETIME);
        CREATE TABLE study_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_session_id INTEGER NOT NULL, participant_id INTEGER NOT NULL,
            participant_code TEXT NOT NULL, seq INTEGER NOT NULL, part_id TEXT, step_id TEXT,
            step_index INTEGER, event_type TEXT NOT NULL, source TEXT NOT NULL, client_id TEXT,
            event_data TEXT, client_time TEXT, created_at DATETIME, elapsed_ms INTEGER,
            step_elapsed_ms INTEGER);
        CREATE TABLE study_renders (id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_session_id INTEGER NOT NULL, participant_id INTEGER NOT NULL,
            participant_code TEXT NOT NULL, seq INTEGER NOT NULL, part_id TEXT, step_id TEXT,
            step_index INTEGER, model TEXT, view TEXT, render_mode TEXT, layout_mode TEXT,
            depth REAL, zoom REAL, input_source TEXT, cache_hit INTEGER, orientation TEXT,
            created_at DATETIME, elapsed_ms INTEGER, step_elapsed_ms INTEGER);
        INSERT INTO participants (id, code) VALUES (1, 'P01');
        INSERT INTO study_sessions (id, participant_id, participant_code, task_order, status,
            log_path, started_at, completed_at)
            VALUES (1, 1, 'P01', '["lego", "cane_tip"]', 'completed',
                    '/project/data/logs/study/P01_S1_20260810.jsonl',
                    '2026-08-10T10:00:00.000Z', '2026-08-10T11:00:00.000Z');
        INSERT INTO study_events (study_session_id, participant_id, participant_code, seq,
            event_type, source, event_data, created_at)
            VALUES (1, 1, 'P01', 1, 'keyboard', 'participant', '{"key": "i"}', '2026-08-10T10:01:00.000Z');
        INSERT INTO study_renders (study_session_id, participant_id, participant_code, seq,
            model, view, render_mode, depth, created_at)
            VALUES (1, 1, 'P01', 2, 'lego_2x3', 'x-', 'Cut', 50, '2026-08-10T10:01:00.100Z');
        INSERT INTO study_events (study_session_id, participant_id, participant_code, seq,
            event_type, source, event_data, created_at)
            VALUES (1, 1, 'P01', 3, 'session_end', 'experimenter', '{"status": "completed"}',
                    '2026-08-10T11:00:00.000Z');
        """
    )
    conn.commit()
    conn.close()


def test_data_written_by_the_comparison_studys_code_exports_without_being_changed(tmp_path):
    db_path = tmp_path / "study.db"
    _legacy_database(db_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "P01_S1_20260810.jsonl").write_text('{"seq": 1}\n{"seq": 2}\n{"seq": 3}\n', encoding="utf-8")
    original = db_path.read_bytes()

    target = io.BytesIO()
    export.build_archive(example.STUDY, db_path, logs, target=target)

    assert db_path.read_bytes() == original, "the export changed the database it read"
    with zipfile.ZipFile(io.BytesIO(target.getvalue())) as bundle:
        rows = list(csv.DictReader(io.StringIO(bundle.read("long.csv").decode("utf-8"))))
        assert [row["event_type"] for row in rows] == ["keyboard", "render", "session_end"]
        assert rows[0]["key"] == "i"
        assert rows[1]["axis_mode"] == "", "a column that did not exist yet reads blank"
        checks = json.loads(bundle.read("checks.json"))
        assert checks["problems"] == 0, checks
        # The copy in the archive is the database as it was, columns and all.
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(bundle.read("study.db"))
    conn = sqlite3.connect(str(snapshot))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(study_renders)")}
    finally:
        conn.close()
    assert "axis_mode" not in columns


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_list_names_every_study(capsys):
    assert cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "comparison-2026  (closed)" in out
    assert "example  (draft)" in out


def test_token_prints_a_token_and_the_hash_that_matches_it(capsys):
    assert cli(["token"]) == 0
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    token = lines[1]
    token_hash = lines[3]
    assert tokens.verify(token, token_hash)


def test_check_reads_a_database_restored_anywhere(tmp_path, capsys):
    db_path = tmp_path / "restored.db"
    _legacy_database(db_path)
    assert cli(["check", "comparison-2026", "--db", str(db_path), "--logs", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    # No log files were restored beside it, so the one session's log is missing.
    assert "FIND Sessions whose log and database disagree: 1" in out


def test_export_writes_a_zip_and_will_not_overwrite_one(tmp_path):
    db_path = tmp_path / "restored.db"
    _legacy_database(db_path)
    out = tmp_path / "out.zip"
    args = ["export", "comparison-2026", "--db", str(db_path), "--logs", str(tmp_path), "--out", str(out)]
    assert cli(args) == 0
    assert zipfile.is_zipfile(out)
    before = out.read_bytes()
    assert cli(args) == 2, "a second export must not replace the first"
    assert out.read_bytes() == before


def test_an_unknown_study_or_missing_database_is_said_plainly(tmp_path, capsys):
    assert cli(["check", "no-such-study"]) == 2
    assert cli(["check", "example", "--db", str(tmp_path / "absent.db")]) == 2
    err = capsys.readouterr().err
    assert "No study called no-such-study" in err
    assert "No database at" in err
