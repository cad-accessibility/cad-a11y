"""Mesh decimation must not give up silently.

Returning the mesh untouched when decimation fails is the right behaviour: it is
an optimisation, and a model that cannot be simplified should still load. The
problem was that it left no trace, so a missing or systematically failing backend
looked exactly like a renderer that is simply slow, with nothing to point at (#113).

These cover both ways it can give up, and the successful case, since "did it
actually reduce anything" is the other question that could not be answered.
"""

from __future__ import annotations

import logging
import types

import pytest

from app.cad_comparison_lib import MAX_RENDER_FACES, _decimate_for_display


class _FakeMesh:
    """Minimal stand-in: decimation only ever reads .faces and calls simplify."""

    def __init__(self, face_count, result=None, error=None):
        self.faces = [None] * face_count
        self._result = result
        self._error = error

    def simplify_quadric_decimation(self, face_count):
        if self._error is not None:
            raise self._error
        return self._result


def _big(face_count=MAX_RENDER_FACES * 2, **kwargs):
    return _FakeMesh(face_count, **kwargs)


def test_small_mesh_is_returned_untouched_and_says_nothing(caplog):
    mesh = _FakeMesh(10)
    with caplog.at_level(logging.DEBUG, logger="app.cad_comparison_lib"):
        assert _decimate_for_display(mesh) is mesh
    assert not caplog.records, "a mesh under the threshold is not worth logging about"


def test_missing_backend_is_logged_rather_than_swallowed(caplog):
    """The case the issue is about: the optional backend is not installed."""
    mesh = _big(error=ModuleNotFoundError("No module named 'fast_simplification'"))
    with caplog.at_level(logging.DEBUG, logger="app.cad_comparison_lib"):
        assert _decimate_for_display(mesh) is mesh, "the load must still succeed"

    assert caplog.records, "the failure left no trace at all"
    message = caplog.text
    assert "fast_simplification" in message, "the reason must be recoverable from the log"
    assert str(MAX_RENDER_FACES * 2) in message, "the face count is what makes it actionable"


def test_failure_is_logged_at_debug_not_higher(caplog):
    """Not something a user can act on, so it must not surface as a warning."""
    mesh = _big(error=RuntimeError("backend exploded"))
    with caplog.at_level(logging.DEBUG, logger="app.cad_comparison_lib"):
        _decimate_for_display(mesh)
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]


def test_empty_result_is_logged_and_the_original_kept(caplog):
    """The second silent path: decimation succeeds but produces nothing usable."""
    mesh = _big(result=_FakeMesh(0))
    with caplog.at_level(logging.DEBUG, logger="app.cad_comparison_lib"):
        assert _decimate_for_display(mesh) is mesh
    assert "empty" in caplog.text.lower()


def test_success_reports_the_reduction(caplog):
    """So "is decimation actually doing anything" can be answered from the log."""
    reduced = _FakeMesh(MAX_RENDER_FACES)
    mesh = _big(result=reduced)
    with caplog.at_level(logging.DEBUG, logger="app.cad_comparison_lib"):
        assert _decimate_for_display(mesh) is reduced
    assert str(MAX_RENDER_FACES * 2) in caplog.text
    assert str(MAX_RENDER_FACES) in caplog.text


@pytest.mark.parametrize("error", [ModuleNotFoundError("x"), RuntimeError("y"), ValueError("z")])
def test_no_failure_mode_breaks_the_load(error):
    """Whatever the backend raises, the mesh comes back and nothing propagates."""
    mesh = _big(error=error)
    assert _decimate_for_display(mesh) is mesh


def test_logging_is_silent_by_default(caplog):
    """Debug records must not reach a default-configured logger."""
    mesh = _big(error=RuntimeError("quiet please"))
    with caplog.at_level(logging.INFO, logger="app.cad_comparison_lib"):
        _decimate_for_display(mesh)
    assert not caplog.records


def test_fake_mesh_matches_the_real_call_signature():
    """If trimesh's method were renamed, these tests would pass while the code broke."""
    import trimesh

    assert hasattr(trimesh.Trimesh, "simplify_quadric_decimation")
    signature = types.MethodType(trimesh.Trimesh.simplify_quadric_decimation, object())
    assert "face_count" in signature.__func__.__code__.co_varnames
