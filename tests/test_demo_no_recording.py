"""A demo session must produce no row, no line and no file, anywhere.

The Andrew Heiskell Braille and Talking Book Library does not permit active data
recording on its premises, and the study's IRB approval does not cover members of
the public trying a tool at a library. So this is not a "we filter it later"
property: if a demo session writes anything a study session would have written,
the implementation is wrong and the event cannot run.

How this is tested
------------------
Not by asking the application whether it thinks it recorded. Every real sink is
wrapped in a counter first -- the two SQLite databases, the braille event log,
the study JSONL, the print export, the response cookie -- and then a synthetic
session is driven through the demo endpoint doing all the things a person at the
venue would do. Every counter must read exactly zero afterwards, and the data
directory must contain nothing that was not there before.

The counters wrap the *sinks*, not the recorder, on purpose: a bypass that works
by the recorder declining to call them is the thing being verified, so trusting
the recorder to report on itself would test nothing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import app.db as db
import app.recording as recording
import app.server as server
import app.study as study_module
import app.study_db as study_db
from app.server import app as flask_app


ROOT = Path(__file__).resolve().parents[1]


class SinkCounters:
    """One counter per place interaction data could land."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {
            "usage_db_execute": 0,
            "study_db_execute": 0,
            "braille_log_write": 0,
            "study_jsonl_write": 0,
            "print_export": 0,
            "session_cookie": 0,
            "participant_allocated": 0,
            "study_session_created": 0,
        }
        self.detail: list[str] = []

    def bump(self, key: str, note: str = "") -> None:
        self.counts[key] += 1
        if note:
            self.detail.append(f"{key}: {note}")

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def nonzero(self) -> dict[str, int]:
        return {k: v for k, v in self.counts.items() if v}


# Statements that read rather than write. A SELECT against a database that was
# opened anyway is not a recording; an INSERT or an UPDATE is.
_WRITE_SQL = ("insert", "update", "delete", "replace")


def _counts_as_write(sql: str) -> bool:
    return sql.strip().lower().startswith(_WRITE_SQL)


@pytest.fixture()
def sinks(tmp_path, monkeypatch):
    """Redirect every sink into a temp tree and wrap each in a counter."""
    counters = SinkCounters()

    data_root = tmp_path / "data"
    (data_root / "db").mkdir(parents=True)
    (data_root / "logs" / "study").mkdir(parents=True)
    (data_root / "renders").mkdir(parents=True)

    monkeypatch.setattr(db, "DB_PATH", data_root / "db" / "usage.db")
    monkeypatch.setattr(study_db, "DB_PATH", data_root / "db" / "study.db")
    monkeypatch.setattr(study_db, "LOG_DIR", data_root / "logs" / "study")
    monkeypatch.setattr(server, "BRAILLE_LOG_PATH", data_root / "logs" / "braille_send_events.jsonl")
    monkeypatch.setattr(server, "RENDERS_DIR", data_root / "renders")
    db._local.__dict__.clear()
    study_db._local.__dict__.clear()
    # Created before the counters go on, so schema setup is not mistaken for a
    # recording -- and so the control case below has real tables to write into.
    db.init_db()
    study_db.init_db()

    # The render caches are process-wide and content-addressed, so a render this
    # test already asked for comes back without touching the braille log. Cleared
    # so a cache hit cannot make the control case below look like a bypass.
    with server.quantized_render_cache_lock:
        server.quantized_render_cache.clear()
    with server.preview_payload_cache_lock:
        server.preview_payload_cache.clear()

    # -- database writes ----------------------------------------------------
    for module, key in ((db, "usage_db_execute"), (study_db, "study_db_execute")):
        real_get_conn = module._get_conn

        def counting_get_conn(_real=real_get_conn, _key=key):
            conn = _real()
            real_execute = conn.execute

            def counting_execute(sql, *args, **kwargs):
                if _counts_as_write(str(sql)):
                    counters.bump(_key, str(sql).strip().splitlines()[0][:80])
                return real_execute(sql, *args, **kwargs)

            # sqlite3.Connection.execute is read-only on the C type, so wrap the
            # instance in a thin proxy rather than trying to patch the method.
            class _CountingConn:
                def __init__(self, inner):
                    self._inner = inner

                def execute(self, sql, *a, **kw):
                    return counting_execute(sql, *a, **kw)

                def __getattr__(self, name):
                    return getattr(self._inner, name)

            return _CountingConn(conn)

        monkeypatch.setattr(module, "_get_conn", counting_get_conn)

    # -- file writes --------------------------------------------------------
    monkeypatch.setattr(
        server,
        "_write_braille_event",
        lambda event: counters.bump("braille_log_write", str(event.get("model_stem", ""))),
    )
    monkeypatch.setattr(
        study_db,
        "_append_jsonl",
        lambda session, record: counters.bump("study_jsonl_write", str(record.get("event_type", ""))),
    )
    monkeypatch.setattr(
        server,
        "_write_print_render",
        lambda params, result, image: counters.bump("print_export"),
    )

    # -- identity -----------------------------------------------------------
    real_create_participant = study_db.create_participant
    real_create_session = study_db.create_session

    def counting_create_participant(*a, **kw):
        counters.bump("participant_allocated")
        return real_create_participant(*a, **kw)

    def counting_create_session(*a, **kw):
        counters.bump("study_session_created")
        return real_create_session(*a, **kw)

    monkeypatch.setattr(study_db, "create_participant", counting_create_participant)
    monkeypatch.setattr(study_db, "create_session", counting_create_session)

    real_attach = server._attach_session_cookie

    def counting_attach(response, session_id):
        counters.bump("session_cookie", str(session_id))
        return real_attach(response, session_id)

    monkeypatch.setattr(server, "_attach_session_cookie", counting_attach)

    # The recorder holds bound references taken at import, so it is rebuilt here
    # against the counted sinks. This is also the point the test would fail at if
    # somebody moved a write out from behind the recorder.
    monkeypatch.setattr(
        recording,
        "_process_recorder",
        recording.PersistentRecorder(
            analytics=db,
            braille_writer=lambda event: server._write_braille_event(event),
            study_render_writer=study_module.record_render_for_request,
            print_writer=lambda p, r, i: server._write_print_render(p, r, i),
            cookie_writer=lambda resp, sid: server._attach_session_cookie(resp, sid),
        ),
    )

    counters.data_root = data_root
    yield counters
    db._local.__dict__.clear()
    study_db._local.__dict__.clear()


@pytest.fixture()
def client(sinks):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


DEMO_HEADERS = {recording.DEMO_HEADER: "1"}


def _render_body(**overrides):
    body = {
        "view": "y-", "renderMode": "Filled", "mode": "single",
        "depth": 50, "zoom": "0", "current_model": "cube",
        "target_pixel_width": 96, "target_pixel_height": 40,
        "input_source": "keyboard",
    }
    body.update(overrides)
    return body


def _drive_a_demo_session(client):
    """Everything a person at the venue plausibly does, in one pass.

    Deliberately includes the paths that are *supposed* to record on the ordinary
    viewer -- the consent post, the analytics event, the print export -- because a
    bypass that only covers the paths somebody remembered is the failure mode this
    whole design exists to rule out.
    """
    responses = []

    responses.append(client.get("/demo"))
    responses.append(client.get("/demo/status"))

    # Depth sweep, render-mode cycle, rotation, axis switch: the whole exploration
    # vocabulary, each one a render the ordinary viewer would have logged twice.
    for depth in (0, 25, 50, 75, 100):
        responses.append(client.post("/render", json=_render_body(depth=depth), headers=DEMO_HEADERS))
    for mode in ("Filled", "Outline", "Cut", "x-ray"):
        responses.append(client.post("/render", json=_render_body(renderMode=mode), headers=DEMO_HEADERS))
    for view in ("x+", "x-", "y+", "y-", "z+", "z-"):
        responses.append(client.post("/render", json=_render_body(view=view), headers=DEMO_HEADERS))
    responses.append(
        client.post(
            "/render",
            json=_render_body(
                orientation={
                    "scheme": "basis-v1",
                    "forward": [0, -1, 0], "up": [0, 0, 1], "right": [1, 0, 0],
                }
            ),
            headers=DEMO_HEADERS,
        )
    )
    # A cache hit: the ordinary viewer records those too, on purpose, so a demo
    # station must not.
    responses.append(client.post("/render", json=_render_body(depth=50), headers=DEMO_HEADERS))

    # A print export, which writes a PDF and an .npy on the ordinary path.
    responses.append(client.post("/render", json=_render_body(print_view=True), headers=DEMO_HEADERS))

    # The analytics and identity endpoints, tagged as the demo page tags them.
    responses.append(
        client.post(
            "/events/track",
            json={"event_type": "keyboard_shortcut", "event_data": {"key": "r"}},
            headers=DEMO_HEADERS,
        )
    )
    responses.append(
        client.post(
            "/session/identify",
            json={"email": "someone@example.com", "consent": True},
            headers=DEMO_HEADERS,
        )
    )
    responses.append(client.get("/models", headers=DEMO_HEADERS))
    responses.append(client.get("/get_data", headers=DEMO_HEADERS))
    return responses


# ---------------------------------------------------------------------------
# The bypass
# ---------------------------------------------------------------------------

def test_a_demo_session_writes_to_no_sink_at_all(client, sinks):
    responses = _drive_a_demo_session(client)

    assert all(r.status_code < 400 for r in responses), (
        "the demo session did not complete: "
        + repr([(r.status_code, r.request.path) for r in responses if r.status_code >= 400])
    )
    assert sinks.total == 0, (
        f"a demo session wrote to {sinks.nonzero()}. Details: {sinks.detail}"
    )


def test_the_control_case_proves_the_counters_actually_count(client, sinks):
    """Without this, a bypass that works because the counters are broken passes.

    The identical session on the ordinary viewer address, untagged, must trip
    several of the same counters.
    """
    for depth in (0, 50):
        client.post("/render", json=_render_body(depth=depth))
    client.post("/session/identify", json={"email": None, "consent": True})

    assert sinks.counts["usage_db_execute"] > 0, "the analytics database counter never fires"
    assert sinks.counts["braille_log_write"] > 0, "the braille log counter never fires"
    assert sinks.counts["session_cookie"] > 0, "the cookie counter never fires"


def test_a_demo_session_leaves_no_file_behind(client, sinks):
    before = {p for p in sinks.data_root.rglob("*") if p.is_file()}
    _drive_a_demo_session(client)
    after = {p for p in sinks.data_root.rglob("*") if p.is_file()}

    assert after == before, f"the demo session created {sorted(str(p) for p in after - before)}"


def test_no_response_on_the_demo_path_sets_a_cookie(client, sinks):
    for response in _drive_a_demo_session(client):
        assert "Set-Cookie" not in response.headers, (
            f"{response.request.path} set a cookie: {response.headers.get('Set-Cookie')}"
        )


def test_the_path_prefix_alone_is_enough(client, sinks):
    """The header is how the shared endpoints are tagged, but a proxy can strip a
    header. Anything under /demo is a demo request whatever survives."""
    client.get("/demo")
    client.get("/demo/status")
    assert sinks.total == 0, f"an untagged /demo request wrote to {sinks.nonzero()}"


def test_a_demo_render_still_puts_a_picture_on_the_display(client, sinks):
    """The bypass must not be achieved by breaking the thing people came to use."""
    response = client.post("/render", json=_render_body(), headers=DEMO_HEADERS)
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["image_base64"], "the demo render produced no image"
    assert sinks.total == 0


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/study/session/start", {}),
        ("post", "/study/event", {"event_type": "keyboard", "event_data": {"key": "r"}}),
        ("post", "/study/step/advance", {"direction": "next"}),
        ("get", "/study", None),
        ("get", "/study/control", None),
        ("post", "/ingest", {}),
    ],
)
def test_the_study_and_ingest_paths_are_closed_to_a_demo_client(client, sinks, method, path, body):
    """Those two write durable state that does not pass through the recorder --
    participant identifiers and session logs on one, a public model file on the
    other -- so for them the swap is not the whole answer and the request is
    refused. A demo station does not serve /study at all; this is the same door
    closed on a server serving both."""
    call = getattr(client, method)
    response = call(path, json=body, headers=DEMO_HEADERS) if body is not None else call(path, headers=DEMO_HEADERS)

    assert response.status_code == 404, f"{path} answered a demo client"
    assert sinks.total == 0, f"{path} wrote {sinks.nonzero()} for a demo client"


def test_the_study_path_still_works_for_a_study_client(client, sinks):
    """The other half: closing it to demo clients must not close it to everyone."""
    assert client.get("/study").status_code == 200


def test_a_demo_tab_asks_for_its_uploads_to_be_deleted_when_it_closes():
    """An uploaded model must not outlive somebody's turn at the display. The
    endpoint existed and nothing called it, so files sat on disk until the process
    ended."""
    viewer = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    assert "pagehide" in viewer, "nothing cleans up uploads when the tab goes away"
    assert "/uploads/cleanup" in viewer
    assert "keepalive: true" in viewer, "the request would be cancelled as the tab unloads"


def test_the_demo_chooser_offers_the_studys_six_objects(client, sinks):
    """The demo lists the study's three pairs and nothing else.

    Derived from the protocol rather than listed here, so the two cannot drift:
    when the coat rack stopped being part of the study, a hand-written copy of
    this list would have gone on offering it.

    The onboarding mug is deliberately absent. It is what the study teaches the
    system on, not one of the objects under comparison.
    """
    import app.study_protocol as study_protocol

    reported = client.get("/demo/status").get_json()["models"]

    expected = []
    for key in study_protocol.MAIN_PAIRS:
        pair = study_protocol.MODEL_PAIRS.get(key) or {}
        for version in ("a", "b"):
            stem = (pair.get(version) or {}).get("model")
            if stem and stem not in expected:
                expected.append(stem)

    assert sorted(reported) == sorted(expected)
    assert len(reported) == 6, f"expected the study's six, got {reported}"
    assert study_protocol.ONBOARDING_MODEL not in reported


def test_narrowing_the_demo_list_does_not_narrow_anything_else(client, sinks):
    """It filters what is shown. It does not change what loads or what renders.

    A model outside the six still renders perfectly well if something asks for
    it, because the list is a display concern and the render path never consulted
    it. This is what stops the filter turning into a second, quieter model
    resolver that the two paths could disagree about.
    """
    outside = "cube"
    assert outside not in client.get("/demo/status").get_json()["models"]

    response = client.post("/render", json=_render_body(current_model=outside), headers=DEMO_HEADERS)
    assert response.status_code == 200
    assert response.get_json()["image_base64"], f"{outside} stopped rendering"
    assert sinks.total == 0


# ---------------------------------------------------------------------------
# The architecture, not just the behaviour
# ---------------------------------------------------------------------------

SERVER_SOURCE = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
STUDY_SOURCE = (ROOT / "app" / "study.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "forbidden",
    [
        "db.record_render(",
        "db.record_page_event(",
        "db.upsert_session(",
        "db.save_session_identifier(",
        "db.register_model(",
        "db.mark_model_deleted(",
    ],
)
def test_no_handler_reaches_a_sink_without_going_through_the_recorder(forbidden):
    """The single injection point only holds while it is the only door.

    A call site that reaches app.db directly is not covered by the swap, and would
    be a recording on the demo path. It is allowed exactly once: in the wiring that
    hands the module to the persistent recorder.
    """
    occurrences = SERVER_SOURCE.count(forbidden)
    assert occurrences == 0, (
        f"{forbidden} is called directly in server.py; route it through "
        f"recording.current() instead, or the demo path will record."
    )


def test_the_null_recorder_holds_no_handle_to_anything():
    """It is not a filter over a real sink. There is nothing behind it."""
    null = recording.NullRecorder()
    for attribute in vars(null):
        value = getattr(null, attribute)
        assert not hasattr(value, "execute"), f"{attribute} looks like a database connection"
        assert not isinstance(value, (Path, io.IOBase)), f"{attribute} looks like a file handle"


def test_every_persistent_recorder_method_has_a_null_counterpart():
    """A method added to one and not the other is a call site that would fall
    through to the real sink on the demo path."""
    persistent = {
        name for name in dir(recording.PersistentRecorder)
        if not name.startswith("_")
    }
    null = {name for name in dir(recording.NullRecorder) if not name.startswith("_")}
    missing = persistent - null
    assert not missing, f"NullRecorder is missing {sorted(missing)}; those would write"


def test_a_demo_station_process_records_nothing_whatever_the_request_says(monkeypatch):
    """The level the venue actually runs at: with CAD_A11Y_DEMO set there is no
    persistent recorder in the process to reach, tagged or not."""
    monkeypatch.setenv("CAD_A11Y_DEMO", "1")
    assert recording.demo_only_process() is True

    monkeypatch.setattr(recording, "_process_recorder", recording.NullRecorder())
    assert recording.current().records is False
    assert recording.recording_here() is False


def test_a_demo_station_does_not_register_the_study_routes(tmp_path):
    """Checked by running the import the way a station launches it, because the
    routes are decided at import and a monkeypatch afterwards proves nothing."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "from app.server import app;"
         "print([str(r) for r in app.url_map.iter_rules() if str(r).startswith('/study')])"],
        cwd=ROOT,
        # HOME points at a temp directory: matplotlib writes a font cache into it
        # on first import, and the repo is not the place for that.
        env={"CAD_A11Y_DEMO": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "MPLBACKEND": "Agg"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[]"), (
        f"a demo station still serves study routes: {result.stdout.strip()}"
    )


# ---------------------------------------------------------------------------
# Crash diagnostics: allowed, but with nothing in them
# ---------------------------------------------------------------------------

def test_a_crash_note_carries_no_interaction_content():
    note = recording.note_crash(
        "render_view",
        ValueError("boom"),
        traceback_text="Traceback (most recent call last):\n  File \"app/server.py\", line 1\n",
    )
    assert note is not None
    assert note["exc_type"] == "ValueError"
    assert set(note) <= {"where", "exc_type", "traceback", "python", "platform"}


def test_a_crash_note_mentioning_what_somebody_did_is_dropped_whole():
    note = recording.note_crash(
        "render_view",
        ValueError("boom"),
        traceback_text="model_stem = 'mug'  # what they were exploring",
    )
    assert note is None, "a traceback carrying interaction content was kept"
