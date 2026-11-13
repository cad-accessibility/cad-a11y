"""The study record carries the axis mode and the cut (#185).

Every render row now says which axis mode was on and where the cut was along
its axis: the axis it cut along, the side it was seen from, and how far along the
object from its lowest coordinate. The long CSV carries them onto every row,
like the rest of the viewer state. The comparison study ran in Turn mode; these
are what tell a later study's XYZ sessions apart.
"""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

import app.studies.engine as engine
import app.studies.store as store_module
from app.server import app as flask_app
from app.studies import registry, tokens
from app.studies.definition import Status
from app.studies.definitions import comparison_2026
from app.studies.registry import StudyRuntime
from app.studies.store import StudyStore

TOKEN = "test-token"
SLUG = "axis-test"
CONTROL = f"/studies/{SLUG}/control"
STUDY = dataclasses.replace(
    comparison_2026.STUDY,
    slug=SLUG,
    status=Status.OPEN,
    token_hash=tokens.hash_token(TOKEN),
    storage=None,
)
AXIS_COLUMNS = ["axis_mode", "cut_axis", "cut_side", "cut_percent"]

store: StudyStore


@pytest.fixture()
def study_env(tmp_path):
    global store
    store = StudyStore(tmp_path / "study.db", tmp_path / "logs")
    store.init_db()
    registry.install(StudyRuntime(study=STUDY, store=store, served_as=Status.OPEN))
    yield tmp_path
    registry.remove(SLUG)
    store._local.__dict__.clear()


@pytest.fixture()
def client(study_env):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _start(client):
    response = client.post(f"{CONTROL}/session/start", json={}, headers={"X-Study-Token": TOKEN})
    return response.get_json()["state"]["study_session_id"]


def _end(client, session_id):
    # From the last step, where End records 'completed' (#258 review).
    headers = {"X-Study-Token": TOKEN}
    state = client.get(f"{CONTROL}/state?study_session_id={session_id}", headers=headers).get_json()
    client.post(f"{CONTROL}/step/advance",
                json={"study_session_id": session_id, "step_index": state["step_count"] - 1}, headers=headers)
    client.post(f"{CONTROL}/session/end", json={"study_session_id": session_id, "status": "completed"},
                headers=headers)


def _rows():
    conn = sqlite3.connect(str(store.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT view, depth, {', '.join(AXIS_COLUMNS)} FROM study_renders ORDER BY seq")]
    finally:
        conn.close()


XYZ_CUT = {"axis_mode": "xyz", "cut_axis": "z", "cut_side": "above", "cut_percent": 40.0}


def test_a_render_row_records_the_axis_mode_and_the_cut(client):
    session_id = _start(client)
    store.record_render(
        session_id, model="part", view="z+", render_mode="Cut", layout_mode="single",
        depth=60, zoom=0, input_source="keyboard", cache_hit=False, **XYZ_CUT,
    )
    (row,) = _rows()
    assert {k: row[k] for k in AXIS_COLUMNS} == XYZ_CUT


def test_the_long_csv_carries_them_onto_every_row(client):
    session_id = _start(client)
    store.record_render(
        session_id, model="part", view="z+", render_mode="Cut", layout_mode="single",
        depth=60, zoom=0, input_source="keyboard", cache_hit=False, **XYZ_CUT,
    )
    client.post(f"/studies/{SLUG}/event?s={store.get_study_session(session_id)['participant_key']}",
                json={"event_type": "keyboard", "event_data": {"key": "arrowup"}})
    _end(client, session_id)

    assert set(AXIS_COLUMNS) <= set(store_module.LONG_EXPORT_COLUMNS)
    rows = [r for r in store.export_long_rows(session_id) if r["event_type"] in ("render", "keyboard")]
    assert rows, "nothing exported"
    for row in rows:
        assert {k: row[k] for k in AXIS_COLUMNS} == XYZ_CUT, f"{row['event_type']} row lost the cut"


def test_render_rows_from_before_this_have_blanks_not_zeros(client):
    session_id = _start(client)
    store.record_render(
        session_id, model="part", view="x-", render_mode="Cut", layout_mode="single",
        depth=50, zoom=0, input_source="keyboard", cache_hit=False,
    )
    (row,) = _rows()
    assert all(row[k] is None for k in AXIS_COLUMNS)


def test_a_database_from_before_this_gains_the_columns(tmp_path):
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

    legacy_store = StudyStore(legacy, tmp_path / "logs")
    try:
        legacy_store.init_db()
        conn = sqlite3.connect(str(legacy))
        columns = {r[1] for r in conn.execute("PRAGMA table_info(study_renders)")}
        kept = conn.execute("SELECT view, depth FROM study_renders").fetchall()
        conn.close()
    finally:
        legacy_store._local.__dict__.clear()
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
        headers={"X-Study": SLUG, "X-Study-Key": store.get_study_session(session_id)["participant_key"]},
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
    assert engine._axis_fields({**XYZ_CUT, field: bad})[field] is None
    assert engine._axis_fields(XYZ_CUT) == XYZ_CUT


def test_the_csv_says_whether_shift_was_held(client):
    """Shift+Z is Z from the other side; `key` reads "z" for both."""
    session_id = _start(client)
    key = store.get_study_session(session_id)["participant_key"]
    for payload in ({"key": "z", "shift": True}, {"key": "z", "shift": False}, {"key": "z"}):
        client.post(f"/studies/{SLUG}/event?s={key}", json={"event_type": "keyboard", "event_data": payload})
    _end(client, session_id)
    assert "key_shift" in store_module.LONG_EXPORT_COLUMNS
    shifts = [r["key_shift"] for r in store.export_long_rows(session_id) if r["event_type"] == "keyboard"]
    assert shifts == [True, False, None], "a key recorded before key_shift existed must read blank"


# Where the slice was in millimetres from the model's origin (#264), for a model
# whose unit is known (#234). The server works it out from the cut the viewer
# reported and the model it rendered, so a value the viewer sends is ignored.
SLICE_COLUMNS = ["cut_value", "cut_units"]


def _slice_rows():
    conn = sqlite3.connect(str(store.db_path))
    try:
        return conn.execute(f"SELECT {', '.join(SLICE_COLUMNS)} FROM study_renders ORDER BY seq").fetchall()
    finally:
        conn.close()


def _render_in_session(client, session_id, model, **cut):
    response = client.post(
        "/render",
        json={
            "view": "z+", "renderMode": "Cut", "mode": "single", "depth": 60, "zoom": 0,
            "current_model": model, "target_pixel_width": 96, "target_pixel_height": 40,
            "input_source": "keyboard", **cut,
        },
        headers={"X-Study": SLUG, "X-Study-Key": store.get_study_session(session_id)["participant_key"]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def test_the_server_records_where_the_slice_was_in_millimetres(client):
    """The tutorial mug is 95 mm tall from z = 0, so 40% along Z is 38 mm."""
    session_id = _start(client)
    _render_in_session(client, session_id, "tutorial_mug", **XYZ_CUT, cut_value=999.0, cut_units="mm")
    assert _slice_rows()[-1] == pytest.approx((38.0, "mm"))


def test_a_cut_on_an_axis_whose_low_end_is_negative_counts_from_there(client):
    """The mug spans X from -43.65 mm to 43.65 mm, so 25% along X is -21.8 mm.
    Rendered twice, so the second row comes from the cache."""
    session_id = _start(client)
    cut = {**XYZ_CUT, "cut_axis": "x", "cut_side": "right", "cut_percent": 25.0}
    _render_in_session(client, session_id, "tutorial_mug", **cut)
    _render_in_session(client, session_id, "tutorial_mug", **cut)
    rows = _slice_rows()[-2:]
    assert [row[0] for row in rows] == pytest.approx([-21.83, -21.83], abs=0.02)


def test_a_model_with_no_known_unit_leaves_them_blank(client):
    session_id = _start(client)
    _render_in_session(client, session_id, "cane_tip_fitted", **XYZ_CUT, cut_value=999.0, cut_units="mm")
    assert _slice_rows()[-1] == (None, None)


def test_the_long_csv_carries_the_millimetres_after_every_older_column(client):
    session_id = _start(client)
    store.record_render(
        session_id, model="part", view="z+", render_mode="Cut", layout_mode="single",
        depth=60, zoom=0, input_source="keyboard", cache_hit=False, **XYZ_CUT,
        cut_value=-12.5, cut_units="mm",
    )
    _end(client, session_id)
    assert list(store_module.LONG_EXPORT_COLUMNS[-2:]) == SLICE_COLUMNS
    (row,) = [r for r in store.export_long_rows(session_id) if r["event_type"] == "render"]
    assert (row["cut_value"], row["cut_units"]) == (-12.5, "mm")


def test_a_database_from_before_the_millimetres_gains_them(tmp_path):
    legacy = tmp_path / "legacy.db"
    StudyStore(legacy, tmp_path / "logs").init_db()
    conn = sqlite3.connect(str(legacy))
    conn.execute("ALTER TABLE study_renders DROP COLUMN cut_units")
    conn.execute("ALTER TABLE study_renders DROP COLUMN cut_value")
    conn.commit()
    conn.close()
    reopened = StudyStore(legacy, tmp_path / "logs")
    try:
        reopened.init_db()
        conn = sqlite3.connect(str(legacy))
        columns = {r[1] for r in conn.execute("PRAGMA table_info(study_renders)")}
        conn.close()
    finally:
        reopened._local.__dict__.clear()
    assert set(SLICE_COLUMNS) <= columns


@pytest.mark.parametrize("field,bad", [("cut_value", "12"), ("cut_value", float("inf")), ("cut_units", "in")])
def test_millimetre_values_the_server_cannot_produce_are_left_blank(field, bad):
    assert engine._slice_coordinate_fields({"cut_value": 1.5, "cut_units": "mm", field: bad})[field] is None
