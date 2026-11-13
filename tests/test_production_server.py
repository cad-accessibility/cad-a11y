"""The server runs under waitress, not Flask's development server (#211).

Every open viewer tab holds an /events stream, and with it one of waitress's
threads, for as long as the tab is open. Once every thread holds a stream, all
other requests wait, so the pool has to leave room for the tabs.
"""

from __future__ import annotations

from wsgiref.util import is_hop_by_hop

from app import server


def test_main_serves_with_waitress(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "serve", lambda app, **kw: calls.append((app, kw)))

    def no_dev_server(*args, **kwargs):
        raise AssertionError("main() started Flask's development server")

    monkeypatch.setattr(server.app, "run", no_dev_server)
    for name in ("start_model_warmup", "initialize_default_braille_render", "open_viewer_in_browser"):
        monkeypatch.setattr(server, name, lambda *a, **k: None)
    monkeypatch.setenv("PORT", "7171")

    assert server.main() == 0

    assert len(calls) == 1
    app, kwargs = calls[0]
    assert app is server.app
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 7171
    assert kwargs["threads"] == server.SERVER_THREADS


def test_connections_leave_room_for_every_thread():
    # Each tab keeps its stream open and polls on a second connection, so a
    # connection limit at or below the thread count would refuse tabs first.
    assert server.SERVER_CONNECTION_LIMIT >= 2 * server.SERVER_THREADS


def test_the_event_stream_sets_no_hop_by_hop_header():
    """PEP 3333 leaves hop-by-hop headers to the server. Waitress answers 500
    to a response that sets one, where the development server let it through."""
    with server.app.test_client() as client:
        stream = client.get("/events")
        hop = [name for name in stream.headers.keys() if is_hop_by_hop(name)]
        stream.close()
    assert not hop
