"""The server runs under waitress, not Flask's development server (#211).

Every open viewer tab holds an /events stream, and a study participant's page
holds /events and the study's /stream, each on one of waitress's threads, for as
long as the page is open. Once every thread holds a stream, every other request
waits, /health included, so the pool has to leave room for the pages.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from wsgiref.util import is_hop_by_hop

import pytest
import waitress

import app.db as db_module
from app import server


def test_main_serves_with_waitress(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "serve", lambda app, **kw: calls.append((app, kw)))

    def no_dev_server(*args, **kwargs):
        raise AssertionError("main() started Flask's development server")

    monkeypatch.setattr(server.app, "run", no_dev_server)
    for name in ("start_model_warmup", "initialize_default_braille_render", "open_viewer_in_browser",
                 "_reconcile_uploads"):
        monkeypatch.setattr(server, name, lambda *a, **k: {})
    monkeypatch.setattr(server.db, "init_db", lambda: None)
    monkeypatch.setenv("PORT", "7171")

    assert server.main() == 0

    assert len(calls) == 1
    app, kwargs = calls[0]
    assert app is server.app
    assert kwargs == {"host": "0.0.0.0", "port": 7171, **server.SERVER_OPTIONS}


def test_connections_leave_room_for_every_thread():
    # Each page keeps its streams open and polls on another connection.
    assert server.SERVER_OPTIONS["connection_limit"] >= 2 * server.SERVER_OPTIONS["threads"]


def test_an_oversized_body_is_bounded_but_flask_still_answers_just_over_the_limit():
    """Waitress stores a whole body before the app sees it. Its limit bounds that,
    and sits above Flask's, so a body just over Flask's still gets Flask's 413."""
    limit = server.SERVER_OPTIONS["max_request_body_size"]
    assert server.app.config["MAX_CONTENT_LENGTH"] < limit <= 4 * server.app.config["MAX_CONTENT_LENGTH"]


def test_the_event_stream_sets_no_hop_by_hop_header():
    """PEP 3333 leaves hop-by-hop headers to the server. Waitress answers 500
    to a response that sets one, where the development server let it through."""
    with server.app.test_client() as client:
        stream = client.get("/events")
        hop = [name for name in stream.headers.keys() if is_hop_by_hop(name)]
        stream.close()
    assert not hop


@pytest.fixture()
def through_waitress(tmp_path, monkeypatch):
    """The app behind a real waitress server, set up the way main() sets it up."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "usage.db")
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()
    db_module.init_db()
    # The production options with a small pool: 256 threads per test would pile up.
    options = {**server.SERVER_OPTIONS, "threads": 4}
    listener = waitress.create_server(server.app, host="127.0.0.1", port=0, **options)

    def run():
        try:
            listener.run()
        except OSError:
            pass  # select() on the sockets close() has just shut, at teardown

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    yield f"http://127.0.0.1:{listener.effective_port}"
    listener.close()
    # A stream's worker stays parked until its next heartbeat; don't wait for it.
    listener.task_dispatcher.shutdown(timeout=1)
    runner.join(timeout=5)
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()


def test_the_proxy_s_https_reaches_the_app(through_waitress):
    """Apache ends TLS and says so in X-Forwarded-Proto, which is what gives the
    session and study panel cookies Secure. Waitress drops proxy headers by default."""
    request = urllib.request.Request(
        f"{through_waitress}/session/identify",
        data=json.dumps({"consent": False}).encode(),
        headers={"Content-Type": "application/json", "X-Forwarded-Proto": "https"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        cookie = response.headers["Set-Cookie"]
    assert cookie and "Secure" in cookie


def test_the_event_stream_opens_through_waitress(through_waitress):
    with urllib.request.urlopen(f"{through_waitress}/events", timeout=10) as response:
        assert response.status == 200
        assert response.readline().startswith(b"data:")
