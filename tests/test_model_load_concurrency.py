"""Two people opening two different new models don't wait for each other (#168).

Building a renderer loads the mesh, which takes seconds for a large model. It
used to happen under models_lock, the lock that also guards the model list, so a
second person opening some other new model waited for the first load to finish,
and so did anything that only wanted the model list. Two requests for the same
new model must still build it once.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import pytest

import app.server as server

WAIT = 5.0


class _SlowRenderer:
    """Stands in for CADComparisonRenderer: construction waits until released."""

    built: list[str] = []
    started: threading.Event
    release: threading.Event
    both_loading: threading.Barrier | None = None

    def __init__(self, before_path, _after_path):
        type(self).built.append(before_path)
        self.cache_path = before_path + ".cache"
        type(self).started.set()
        if type(self).both_loading is not None:
            type(self).both_loading.wait(WAIT)
        assert type(self).release.wait(WAIT), "the test never released the load"


@pytest.fixture()
def models(tmp_path, monkeypatch):
    """Four model files the server lists, opened by name the way a request opens one."""
    paths = {}
    for name in ("first", "second", "same", "gone"):
        paths[name] = tmp_path / f"{name}.stl"
        paths[name].write_text("solid x\nendsolid x\n")
    monkeypatch.setattr(server, "AVAILABLE_MODELS", list(paths.values()))
    monkeypatch.setattr(server, "_resolve_model_stem", lambda stem: stem)
    return paths


@pytest.fixture()
def slow_renderer(monkeypatch, models):
    _SlowRenderer.built = []
    _SlowRenderer.started = threading.Event()
    _SlowRenderer.release = threading.Event()
    _SlowRenderer.both_loading = None
    monkeypatch.setattr(server, "CADComparisonRenderer", _SlowRenderer)
    monkeypatch.setattr(server, "renderers_by_model", OrderedDict())
    return _SlowRenderer


def _in_thread(target, *args):
    result: dict = {}

    def run():
        try:
            result["value"] = target(*args)
        except BaseException as error:  # handed back to the test thread
            result["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def _finish(thread, result):
    thread.join(WAIT)
    assert not thread.is_alive(), "a load never finished"
    if "error" in result:
        raise result["error"]
    return result["value"]


def test_two_different_new_models_load_at_the_same_time(slow_renderer, models):
    first, second = models["first"], models["second"]
    slow_renderer.both_loading = threading.Barrier(2)
    slow_renderer.release.set()

    runs = [_in_thread(server.get_or_create_renderer, path.stem) for path in (first, second)]
    engines = [_finish(*run) for run in runs]

    assert not slow_renderer.both_loading.broken, "the second load waited for the first to finish"
    assert {e.cache_path for e in engines} == {str(first) + ".cache", str(second) + ".cache"}


def test_the_model_list_is_not_held_up_by_a_load(slow_renderer, models):
    run = _in_thread(server.get_or_create_renderer, "first")
    assert slow_renderer.started.wait(WAIT)

    got_it = server.models_lock.acquire(timeout=1.0)
    if got_it:
        server.models_lock.release()
    slow_renderer.release.set()
    _finish(*run)
    assert got_it, "models_lock was held for the whole mesh load"


def test_two_requests_for_the_same_new_model_build_it_once(slow_renderer, models):
    path = models["same"]
    first = _in_thread(server.get_or_create_renderer, "same")
    assert slow_renderer.started.wait(WAIT)
    second = _in_thread(server.get_or_create_renderer, "same")

    slow_renderer.release.set()
    engines = [_finish(*first), _finish(*second)]

    assert slow_renderer.built == [str(path)]
    assert engines[0] is engines[1]
    assert server.renderers_by_model[str(path)] is engines[0]


def test_a_model_deleted_while_it_loads_is_not_cached(slow_renderer, models):
    """Deleting used to wait for the load, since both held models_lock. Now it
    doesn't, so the load must not put the deleted model back in the cache."""
    path = models["gone"]
    run = _in_thread(server.get_or_create_renderer, "gone")
    assert slow_renderer.started.wait(WAIT)

    path.unlink()  # what the delete route does, then it forgets the renderer
    with server.models_lock:
        server._forget_renderer(path)
    slow_renderer.release.set()
    engine = _finish(*run)

    assert str(path) not in server.renderers_by_model
    assert engine._discarded, "its slice precompute would write a cache file for a deleted model"


def test_a_model_replaced_while_it_loads_is_not_cached_as_the_old_file(slow_renderer, models):
    """Deleted and uploaded again under the same name during a slow load: the
    renderer for the old file must not be kept for the new one."""
    path = models["gone"]
    run = _in_thread(server.get_or_create_renderer, "gone")
    assert slow_renderer.started.wait(WAIT)

    path.unlink()
    with server.models_lock:
        server._forget_renderer(path)
    path.write_text("solid corrected\nendsolid corrected\n")
    slow_renderer.release.set()
    engine = _finish(*run)

    assert str(path) not in server.renderers_by_model
    assert engine._discarded
