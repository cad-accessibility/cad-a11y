"""The test suite leaves nothing behind in the repo's real data/ folder.

It used to: every run left its fixture models in data/models and data/uploads
(the /viewer model list filled up with ingest_* stems), a slice cache per model
in data/renders, and lines in the real braille log and usage database.
tests/conftest.py points the app at a temporary directory instead; these check
that it took, and that the paths a render, an upload and an ingest actually
write to are the temporary ones.

/health still checks that data/renders and data/logs are writable by writing a
file there and deleting it at once. That is the check working, not litter.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import app.cad_comparison_lib as cad_lib
import app.db as analytics_db
from app import server, study_db
from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
REAL_DATA = (ROOT / "data").resolve()

STL = (
    b"solid test\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n   vertex 1 0 0\n"
    b"   vertex 0 1 0\n  endloop\n endfacet\nendsolid test\n"
)


def _inside_real_data(path) -> bool:
    resolved = Path(path).resolve()
    return resolved == REAL_DATA or REAL_DATA in resolved.parents


def _slice_cache_path():
    """Where a renderer would write its slice cache, rather than the variable
    that is meant to move it."""
    cube = str(ROOT / "builtin_models" / "cube.stl")
    return cad_lib.CADComparisonRenderer(cube, cube).cache_path


WHERE_THE_APP_WRITES = {
    "built-in models": lambda: server.MODEL_DIR,
    "uploads": lambda: server.UPLOAD_DIR,
    "slice cache": _slice_cache_path,
    "braille log": lambda: server.BRAILLE_LOG_PATH,
    "usage database": lambda: analytics_db.DB_PATH,
    "study database": lambda: study_db.DB_PATH,
    "study logs": lambda: study_db.LOG_DIR,
}


@pytest.mark.parametrize("name", WHERE_THE_APP_WRITES)
def test_the_app_under_test_keeps_its_files_out_of_data(name):
    path = WHERE_THE_APP_WRITES[name]()
    assert not _inside_real_data(path), f"the {name} is in the repo's data/: {path}"


def test_the_built_in_models_are_still_there_to_test_with():
    """Moving the model directory must not leave the tests without models: the
    server seeds the built-ins into whichever directory it uses."""
    assert (server.MODEL_DIR / "mug.stl").exists()


def _snapshot():
    if not REAL_DATA.exists():
        return {}
    return {
        str(p): p.stat().st_mtime_ns
        for p in REAL_DATA.rglob("*")
        if p.is_file()
    }


def test_an_upload_an_ingest_and_a_render_touch_nothing_in_data():
    """The three things that used to leave files behind, through the real routes."""
    before = _snapshot()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        uploaded = client.post(
            "/upload",
            data={"file": (io.BytesIO(STL), "suite_upload.stl")},
            content_type="multipart/form-data",
        )
        ingested = client.post("/ingest?filename=suite_ingest.stl", data=STL,
                               content_type="application/octet-stream")
        rendered = client.post("/render", json={
            "view": "z+", "renderMode": "Cut", "mode": "single", "depth": 50, "zoom": 0,
            "current_model": 0, "target_pixel_width": 96, "target_pixel_height": 40,
        })
    assert uploaded.status_code == 200, uploaded.get_data(as_text=True)
    assert ingested.status_code in (200, 201), ingested.get_data(as_text=True)
    assert rendered.status_code == 200

    after = _snapshot()
    written = sorted(p for p in after if before.get(p) != after[p])
    assert not written, f"the test run wrote into the repo's data/: {written[:5]}"
