"""Every model is processed up front, not when someone first opens it.

Loading a mesh and precomputing its slice graphs is seconds of work. Doing it
lazily meant whoever opened a model first paid for it, which on a tactile display
is a silence with nothing to explain it.

The constraint that shapes the design: this work is CPU-bound and Flask serves
renders from other threads, so it has to yield. One model at a time, not a pool.
"""

from __future__ import annotations

import pathlib
import queue
import threading

import pytest

import app.server as server
from app.server import app as flask_app


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_health_reports_progress_so_a_slow_start_is_not_a_mystery(client):
    body = client.get("/health").get_json()
    warmup = body["checks"]["warmup"]
    for field in ("total", "processed", "pending", "current", "complete", "started"):
        assert field in warmup, f"/health does not report {field}"


def test_warming_up_does_not_make_the_server_unhealthy(client, monkeypatch):
    """A server still warming answers renders perfectly well. Reporting degraded
    would take a deployment out of rotation for doing exactly what it should."""
    monkeypatch.setattr(server, "_warmup_state",
                        {"total": 16, "processed": 3, "current": "mug", "started": True})
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["checks"]["warmup"]["complete"] is False


def test_every_model_is_queued_at_startup(monkeypatch):
    queued = []
    monkeypatch.setattr(server, "enqueue_model_for_warmup", queued.append)
    monkeypatch.setattr(server, "_warmup_state", {"total": 0, "processed": 0,
                                                  "current": None, "started": False})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: None})())

    server.start_model_warmup()
    assert set(queued) == set(server.AVAILABLE_MODELS), "not every model was queued"


def test_starting_twice_does_not_queue_everything_again(monkeypatch):
    queued = []
    monkeypatch.setattr(server, "enqueue_model_for_warmup", queued.append)
    monkeypatch.setattr(server, "_warmup_state", {"total": 0, "processed": 0,
                                                  "current": None, "started": False})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: None})())

    server.start_model_warmup()
    first = len(queued)
    server.start_model_warmup()
    assert len(queued) == first


def test_a_model_that_fails_does_not_stop_the_rest(monkeypatch):
    """One unreadable file must not leave every later model cold, and must not
    take the worker down with it."""
    processed = []

    def explode(model_path):
        processed.append(model_path)
        raise RuntimeError("bad mesh")

    monkeypatch.setattr(server, "_warm_one_model", explode)
    monkeypatch.setattr(server, "_warmup_state", {"total": 2, "processed": 0,
                                                  "current": None, "started": True})
    # Its own queue: the module-level one is shared, and any test that uploads a
    # file legitimately leaves an entry on it for the real worker.
    monkeypatch.setattr(server, "_warmup_queue", queue.Queue())

    for model in server.AVAILABLE_MODELS[:2]:
        server._warmup_queue.put(pathlib.Path(model))

    worker = threading.Thread(target=server._warmup_worker, daemon=True)
    worker.start()
    server._warmup_queue.join()

    assert len(processed) == 2, "the worker stopped after the first failure"
    assert server._warmup_snapshot()["processed"] == 2


def test_the_work_yields_so_renders_are_not_starved():
    """The precompute holds the GIL otherwise, and Flask serves renders from
    other threads. This is why it is one worker rather than a pool."""
    import app.cad_comparison_lib as cad_lib

    source = pathlib.Path(cad_lib.__file__).read_text(encoding="utf-8")
    assert "_PRECOMPUTE_YIELD_EVERY" in source
    assert "time.sleep(self._PRECOMPUTE_YIELD_SECONDS)" in source


def test_an_upload_is_queued_so_the_uploader_does_not_pay_for_it():
    assert "enqueue_model_for_warmup(dest)" in pathlib.Path(server.__file__).read_text(
        encoding="utf-8"), "an uploaded model is never warmed"


def test_deleting_a_model_takes_its_cache_with_it():
    """Cache files are keyed by signature, so one left behind can never match
    again. Upload and delete churn would otherwise fill the volume."""
    source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    forget = source[source.index("def _forget_renderer"):]
    forget = forget[:forget.index("\ndef ")]
    assert "cache_path" in forget and "unlink" in forget
