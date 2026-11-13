"""Which length unit a model is in, and the slice's real position (#234, #264).

The viewer can say where the slice is in millimetres only for a model whose unit
is known. Nothing guesses from the size: the same LEGO brick ships twice, ten
times apart. A model whose unit is not known stays in percent.
"""

from __future__ import annotations

import io
import struct

import numpy as np
import pytest
import trimesh

import app.db as db_module
from app import model_units, server
from app.server import app as flask_app

ROOT = model_units.REPO_ROOT
BUILTINS = ROOT / "builtin_models"
TRIANGLE = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 5.0)]


def _ascii_stl(name: str) -> bytes:
    vertices = "\n".join(f"   vertex {x} {y} {z}" for x, y, z in TRIANGLE)
    return (
        f"solid {name}\n facet normal 0 0 1\n  outer loop\n{vertices}\n"
        f"  endloop\n endfacet\nendsolid {name}\n"
    ).encode()


def _binary_stl(header: bytes) -> bytes:
    floats = [0.0, 0.0, 1.0] + [c for vertex in TRIANGLE for c in vertex]
    return header.ljust(80, b"\0") + struct.pack("<I", 1) + struct.pack("<12fH", *floats, 0)


def _write(tmp_path, data: bytes, name="part.stl"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _vertices(path):
    return trimesh.load(path, force="mesh").vertices


@pytest.mark.parametrize("data", [_ascii_stl("OpenSCAD_Model"), _binary_stl(b"OpenSCAD Model")],
                         ids=["ascii", "binary"])
def test_an_openscad_export_is_in_millimetres(tmp_path, data):
    assert model_units.units_for(_write(tmp_path, data)) == "mm"


@pytest.mark.parametrize("data", [_ascii_stl("Mesh"), _binary_stl(b"exported by something")],
                         ids=["ascii", "binary"])
def test_an_stl_that_says_nothing_has_no_known_unit(tmp_path, data):
    assert model_units.units_for(_write(tmp_path, data)) is None


def test_a_step_file_is_in_metres():
    """STEP files declare their unit, and the loader converts every one to
    metres whatever it declares."""
    assert model_units.units_for(ROOT / "src" / "models" / "brep" / "cube.step") == "m"


@pytest.mark.parametrize("name, unit", [
    ("tutorial_mug.stl", "mm"),     # scripts/build_tutorial_mug.py writes millimetres
    ("lego_3002.stl", "m"),         # 0.0238 x 0.0159 x 0.0114, a real 2x3 brick in metres
    ("lego_3002e.stl", "m"),
    ("labeled_cube.stl", "mm"),     # exported by OpenSCAD
    ("lego_2x3.stl", None),         # the same brick at 0.24 across: no real unit fits
    ("cane_tip_fitted.stl", None),
])
def test_built_in_models(name, unit):
    assert model_units.units_for(BUILTINS / name) == unit


@pytest.mark.parametrize("unit", model_units.UNITS)
@pytest.mark.parametrize("data", [_ascii_stl("Mesh"), _binary_stl(b"exported by something")],
                         ids=["ascii", "binary"])
def test_a_declared_unit_is_kept_in_the_file_without_moving_the_geometry(tmp_path, data, unit):
    path = _write(tmp_path, data)
    before = _vertices(path)
    model_units.declare_stl_units(path, unit)
    assert model_units.units_for(path) == unit
    np.testing.assert_array_equal(_vertices(path), before)


def test_without_the_mug_s_landmarks_its_unit_is_unknown_and_nothing_breaks(tmp_path, monkeypatch):
    monkeypatch.setattr(model_units, "_LANDMARKS", tmp_path / "missing.json")
    model_units._builtin_units_by_sha256.cache_clear()
    try:
        assert model_units.units_for(BUILTINS / "tutorial_mug.stl") is None
        assert model_units.units_for(BUILTINS / "lego_3002.stl") == "m"
    finally:
        model_units._builtin_units_by_sha256.cache_clear()


def test_a_declared_unit_wins_over_the_openscad_stamp(tmp_path):
    path = _write(tmp_path, _ascii_stl("OpenSCAD_Model"))
    model_units.declare_stl_units(path, "in")
    assert model_units.units_for(path) == "in"


def _render(client, model, **overrides):
    body = {
        "current_model": model, "view": "z+", "zoom": 0, "depth": 50, "renderMode": "Cut",
        "mode": "single", "target_pixel_width": 96, "target_pixel_height": 40, **overrides,
    }
    response = client.post("/render", json=body)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def test_a_render_gives_the_object_s_extent_in_millimetres():
    """The viewer works out where the slice is from this: the plane's position
    along its axis, from the object's low end, times the extent."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        bounds = _render(client, "tutorial_mug")["bounds_mm"]
    mesh = trimesh.load(BUILTINS / "tutorial_mug.stl", force="mesh")
    assert bounds == pytest.approx(list(mesh.bounds[0]) + list(mesh.bounds[1]), abs=0.05)


def test_a_model_in_metres_is_given_in_millimetres():
    """lego_3002 is a 2x3 brick in metres: 11.4 mm tall with its studs."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        bounds = _render(client, "lego_3002")["bounds_mm"]
    assert bounds[5] - bounds[2] == pytest.approx(11.4, abs=0.1)


def test_a_cached_render_still_gives_the_extent():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        first = _render(client, "tutorial_mug", depth=37)["bounds_mm"]
        again = _render(client, "tutorial_mug", depth=37)
    assert again["debug"]["phase1_quantized_cache_hit"] is True
    assert again["bounds_mm"] == first


def test_the_step_loader_converts_whatever_unit_a_file_declares_to_metres(tmp_path):
    """units_for calls every STEP file metres because the loader converts it so.
    The same file declaring millimetres instead must load a thousand times smaller."""
    source = ROOT / "src" / "models" / "brep" / "cube.step"
    text = source.read_text(encoding="utf-8")
    assert "SI_UNIT($,.METRE.)" in text
    millimetres = tmp_path / "cube_mm.step"
    millimetres.write_text(text.replace("SI_UNIT($,.METRE.)", "SI_UNIT(.MILLI.,.METRE.)"), encoding="utf-8")
    as_metres = trimesh.load(source, force="mesh").extents
    as_millimetres = trimesh.load(millimetres, force="mesh").extents
    np.testing.assert_allclose(as_millimetres * 1000.0, as_metres, rtol=1e-3)


def test_a_render_of_a_model_with_no_known_unit_gives_no_extent():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        assert _render(client, "cane_tip_fitted")["bounds_mm"] is None


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "usage.db")
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()
    db_module.init_db()
    yield
    db_module._local.__dict__.clear()
    db_module._contacts_local.__dict__.clear()


@pytest.mark.parametrize("field, value, expected", [
    ("units", "in", "in"),
    ("units", "unknown", None),
    ("units", "furlongs", None),
])
def test_an_upload_keeps_the_unit_the_person_chose(tmp_db, field, value, expected):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        response = client.post(
            "/upload",
            data={"file": (io.BytesIO(_ascii_stl("Mesh")), "measured.stl"),
                  "upload_session_id": "tab-" + "c" * 32, field: value},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert model_units.units_for(server.UPLOAD_DIR / response.get_json()["filename"]) == expected
    # Said back, so the viewer can say which unit was kept.
    assert response.get_json()["units"] == expected


def test_an_ingested_model_keeps_its_unit(tmp_db):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        response = client.post(
            "/ingest",
            data={"file": (io.BytesIO(_ascii_stl("Mesh")), "ingested.stl"), "units": "mm"},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert model_units.units_for(server.MODEL_DIR / response.get_json()["filename"]) == "mm"
