"""A failing mesh simplifier says so instead of only making renders slow (#113).

Simplification is an optimisation, so a model that can't be simplified still
loads at full detail. Without a word, a missing backend looked exactly like a
slow renderer: a step on a 1.3M-face upload took about 4 s instead of 0.15 s.
"""

from __future__ import annotations

import sys

import numpy as np
import trimesh

import app.cad_comparison_lib as cad_lib
from app import server


def _dense_mesh(faces: int) -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=np.random.default_rng(0).random((faces + 2, 3)),
        faces=np.array([[i, i + 1, i + 2] for i in range(faces)]),
        process=False,
    )


def test_a_failed_simplification_keeps_the_mesh_and_says_why(monkeypatch, capfd):
    mesh = _dense_mesh(cad_lib.MAX_RENDER_FACES + 1)

    def broken(self, face_count):
        raise ImportError("No module named 'fast_simplification'")

    monkeypatch.setattr(trimesh.Trimesh, "simplify_quadric_decimation", broken)
    assert cad_lib._decimate_for_display(mesh) is mesh
    # On the real stderr, past quiet mode's redirect.
    err = capfd.readouterr().err
    assert "fast_simplification" in err and str(cad_lib.MAX_RENDER_FACES + 1) in err


def test_the_stubbed_method_is_the_one_trimesh_has():
    assert callable(getattr(trimesh.Trimesh, "simplify_quadric_decimation", None))


def test_startup_warns_when_the_simplifier_cannot_be_imported(monkeypatch, capsys):
    """Printed even in quiet mode, which is how the container runs."""
    monkeypatch.setattr(server, "QUIET_MODE", True)
    monkeypatch.setitem(sys.modules, "fast_simplification", None)
    server._warn_if_decimation_unavailable()
    out = capsys.readouterr().out
    assert "fast_simplification" in out and str(cad_lib.MAX_RENDER_FACES) in out


def test_startup_is_quiet_when_the_simplifier_is_there(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "fast_simplification", object())
    server._warn_if_decimation_unavailable()
    assert capsys.readouterr().out == ""
