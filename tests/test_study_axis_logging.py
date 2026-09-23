"""The study record carries the axis mode and the cut (#185).

Every render row now says which axis mode was on and where the cut was along
its axis: the axis it cut along, the side it was seen from, and how far along the
object from its lowest coordinate. The long CSV carries them onto every row,
like the rest of the viewer state. The study runs in Turn mode, so for now these
mostly say "turn"; they are what will tell a pilot's XYZ sessions apart.
"""

from __future__ import annotations

import sqlite3

import pytest

from app import study as study_module
from app import study_db
from app.server import app as flask_app

TOKEN = "test-token"
AXIS_COLUMNS = ["axis_mode", "cut_axis", "cut_side", "cut_percent"]


@pytest.fixture()
def study_env(tmp_path, monkeypatch):
    monkeypatch.setattr(study_db, "DB_PATH", tmp_path / "study.db")
    monkeypatch.setattr(study_db, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setenv("STUDY_CONTROL_TOKEN", TOKEN)
    study_db._local.__dict__.clear()
    study_module._ready_signals.clear()
    study_module._last_sweep_at = 0.0
    study_db.init_db()
    yield tmp_path
    study_db._local.__dict__.clear()


@pytest.fixture()
def client(study_env):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _start(client):
    response = client.post("/study/session/start", json={}, headers={"X-Study-Token": TOKEN})
    return response.get_json()["state"]["study_session_id"]


def _end(client, session_id):
    client.post("/study/session/end", json={"study_session_id": session_id, "status": "completed"},
                headers={"X-Study-Token": TOKEN})


def _rows():
    conn = sqlite3.connect(str(study_db.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT view, depth, {', '.join(AXIS_COLUMNS)} FROM study_renders ORDER BY seq")]
    finally:
        conn.close()


XYZ_CUT = {"axis_mode": "xyz", "cut_axis": "z", "cut_side": "above", "cut_percent": 40.0}


def test_a_render_row_records_the_axis_mode_and_the_cut(client):
    session_id = _start(client)
    study_db.record_render(
        session_id, model="part", view="z+", render_mode="Cut", layout_mode="single",
        depth=60, zoom=0, input_source="keyboard", cache_hit=False, **XYZ_CUT,
    )
    (row,) = _rows()
    assert {k: row[k] for k in AXIS_COLUMNS} == XYZ_CUT


def test_the_long_csv_carries_them_onto_every_row(client):
    session_id = _start(client)
    study_db.record_render(
        session_id, model="part", view="z+", render_mode="Cut", layout_mode="single",
        depth=60, zoom=0, input_source="keyboard", cache_hit=False, **XYZ_CUT,
    )
    client.post(f"/study/event?s={study_db.get_study_session(session_id)['participant_key']}",
                json={"event_type": "keyboard", "event_data": {"key": "arrowup"}})
    _end(client, session_id)

    assert set(AXIS_COLUMNS) <= set(study_db.LONG_EXPORT_COLUMNS)
    rows = [r for r in study_db.export_long_rows(session_id) if r["event_type"] in ("render", "keyboard")]
    assert rows, "nothing exported"
    for row in rows:
        assert {k: row[k] for k in AXIS_COLUMNS} == XYZ_CUT, f"{row['event_type']} row lost the cut"


def test_render_rows_from_before_this_have_blanks_not_zeros(client):
    session_id = _start(client)
    study_db.record_render(
        session_id, model="part", view="x-", render_mode="Cut", layout_mode="single",
        depth=50, zoom=0, input_source="keyboard", cache_hit=False,
    )
    (row,) = _rows()
    assert all(row[k] is None for k in AXIS_COLUMNS)


def test_a_database_from_before_this_gains_the_columns(tmp_path, monkeypatch):
    """CREATE TABLE IF NOT EXISTS never alters a table that is already there."""
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.execute(
        """CREATE TABLE study_renders (
               id INTEGER PRIMARY KEY AUTOINCREMENT, study_session_id INTEGER NOT NULL,
               participant_id INTEGER, participant_code TEXT NOT NULL, seq INTEGER NOT NULL,
               part_id TEXT, step_id TEXT, step_index INTEGER, model TEXT, view TEXT,
               render_mode TEXT, layout_mode TEXT, depth REAL, zoom REAL, input_source TEXT,
               cache_hit INTEGER, orientation TEXT, created_at DATETIME, elapsed_ms INTEGER,
               step_elapsed_ms INTEGER)"""
    )
    conn.execute(
        "INSERT INTO study_renders (study_session_id, participant_code, seq, view, depth) "
        "VALUES (1, 'P01', 1, 'x-', 50)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(study_db, "DB_PATH", legacy)
    monkeypatch.setattr(study_db, "LOG_DIR", tmp_path / "logs")
    study_db._local.__dict__.clear()
    try:
        study_db.init_db()
        conn = sqlite3.connect(str(legacy))
        columns = {r[1] for r in conn.execute("PRAGMA table_info(study_renders)")}
        kept = conn.execute("SELECT view, depth FROM study_renders").fetchall()
        conn.close()
    finally:
        study_db._local.__dict__.clear()
    assert set(AXIS_COLUMNS) <= columns
    assert kept == [("x-", 50.0)], "the row already there was lost"


def test_the_render_request_is_what_fills_them(client):
    """Recorded on the server from the render request, like the rest of the row,
    so a closed tab cannot take the record with it."""
    session_id = _start(client)
    response = client.post(
        "/render",
        json={
            "view": "z+", "renderMode": "Cut", "mode": "single", "depth": 60, "zoom": 0,
            "current_model": 0, "target_pixel_width": 96, "target_pixel_height": 40,
            "input_source": "keyboard", **XYZ_CUT,
        },
        headers={"X-Study-Session": str(session_id)},
    )
    assert response.status_code == 200
    rows = _rows()
    assert rows and {k: rows[-1][k] for k in AXIS_COLUMNS} == XYZ_CUT


@pytest.mark.parametrize("field,bad", [
    ("axis_mode", "spin"), ("cut_axis", "w"), ("cut_side", "sideways"),
    ("cut_percent", "40"), ("cut_percent", float("nan")), ("cut_percent", True),
])
def test_values_the_viewer_cannot_send_are_left_blank(field, bad):
    """A column is only worth grouping on if it holds the values the viewer
    actually sends."""
    assert study_module._axis_fields({**XYZ_CUT, field: bad})[field] is None
    assert study_module._axis_fields(XYZ_CUT) == XYZ_CUT


def test_the_csv_says_whether_shift_was_held(client):
    """Shift+Z is Z from the other side; `key` reads "z" for both."""
    session_id = _start(client)
    key = study_db.get_study_session(session_id)["participant_key"]
    for payload in ({"key": "z", "shift": True}, {"key": "z", "shift": False}, {"key": "z"}):
        client.post(f"/study/event?s={key}", json={"event_type": "keyboard", "event_data": payload})
    _end(client, session_id)
    assert "key_shift" in study_db.LONG_EXPORT_COLUMNS
    shifts = [r["key_shift"] for r in study_db.export_long_rows(session_id) if r["event_type"] == "keyboard"]
    assert shifts == [True, False, None], "a key recorded before key_shift existed must read blank"
