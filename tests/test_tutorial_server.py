"""The tutorial's server side: its four endpoints, and what it changes elsewhere.

The endpoints are in app/tutorial.py. app/server.py registers them on every
server, a demo station included, accepts "tutorial" analytics events, leaves
no usage-analytics row for a render sent with X-CAD-Tutorial: 1, and warms the
tutorial mug first at startup.

/tutorial/locate matters most here. It tells a blind user whether the cursor is
on the handle and whether the handle is centred, so a box that is off by a few
pins sends them to the wrong place with nothing to tell them so. It is checked
against real renders: the framing it computes against the framing render()
reports, and the boxes it returns against the pins that render raised.
"""

from __future__ import annotations

import base64
import builtins
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from app import recording, server, tutorial
from app.server import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
LANDMARKS = json.loads((ROOT / "app" / "tutorial_mug.landmarks.json").read_text(encoding="utf-8"))

# The pose every core lesson starts from, as the viewer sends it: the Right view
# (x-), cut halfway, Cut render, single layout, zoom 0, the scrollbar asked for.
DEFAULT_POSE = {
    "view": "x-",
    "orientation": {"scheme": "basis-v1", "forward": [1, 0, 0], "up": [0, 0, 1], "right": [0, 1, 0]},
    "camera_center": None,
    "zoom": 0,
    "depth": 50,
    "renderMode": "Cut",
    "projectionMode": "orthographic",
    "mode": "single",
    "move_camera_center": "none",
    "print_view": False,
    "model": "tutorial_mug",
    "current_model": "tutorial_mug",
    "compose_cursor": True,
    "cursor_col": 2,
    "cursor_row": 2,
    "cursor_state": "none",
    "compose_scrollbar": True,
    "compose_slicegraph": False,
    "show_view_info_box": False,
    "show_origin_marker": False,
    "show_axis_letters": False,
    "output_device": "monarch_hid",
    "input_source": "tutorial",
    "target_pixel_width": 96,
    "target_pixel_height": 40,
}

TUTORIAL_HEADERS = {tutorial.TUTORIAL_HEADER: "1"}


def pose(**changes):
    body = json.loads(json.dumps(DEFAULT_POSE))
    body.update(changes)
    return body


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _clear_render_caches():
    """A cached answer skips the render and every write behind it, so a test
    about what a render writes has to start from empty caches."""
    with server.quantized_render_cache_lock:
        server.quantized_render_cache.clear()
    with server.preview_payload_cache_lock:
        server.preview_payload_cache.clear()


class CountingRecorder:
    """Stands in for the persistent recorder and notes every call, so a test
    can say which writes a request asked for without any being made."""

    records = True
    name = "counting"

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, method):
        if method.startswith("_"):
            raise AttributeError(method)

        def record(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            if method == "attach_session_cookie":
                return args[0]
            return None

        return record

    def names(self):
        return [name for name, _args, _kwargs in self.calls]


@pytest.fixture()
def counting(monkeypatch):
    recorder = CountingRecorder()
    monkeypatch.setattr(recording, "_process_recorder", recorder)
    _clear_render_caches()
    return recorder


def _raised(response_body) -> np.ndarray:
    """The pins a /render response raised, as a boolean (rows, cols) array."""
    image = Image.open(io.BytesIO(base64.b64decode(response_body["image_base64"])))
    return np.asarray(image) > 0


def _inside(box, rows, cols):
    col0, row0, col1, row1 = box
    return (cols >= col0) & (cols <= col1) & (rows >= row0) & (rows <= row1)


# ---------------------------------------------------------------------------
# The blueprint
# ---------------------------------------------------------------------------

TUTORIAL_ROUTES = {"/tutorial/lessons", "/tutorial/test-pattern", "/tutorial/locate", "/tutorial/prints.zip"}


def test_the_tutorial_routes_are_registered():
    registered = {rule.rule for rule in flask_app.url_map.iter_rules()}
    assert TUTORIAL_ROUTES <= registered


def test_the_tutorial_is_not_closed_to_demo_clients():
    """A demo station runs the tutorial too, and nothing in it writes."""
    assert not any(route.startswith(closed) for route in TUTORIAL_ROUTES for closed in server._CLOSED_TO_DEMO)


def test_a_demo_station_serves_the_tutorial(tmp_path, data_env):
    """Checked the way a station launches, because routes are decided at import:
    CAD_A11Y_DEMO drops the study routes and must not drop these."""
    result = subprocess.run(
        [sys.executable, "-c",
         ("from app.server import app;"
          "print(sorted(str(r) for r in app.url_map.iter_rules() if str(r).startswith('/tutorial')))")],
        cwd=ROOT,
        env={"CAD_A11Y_DEMO": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "MPLBACKEND": "Agg", **data_env},
        capture_output=True,
        text=True,
        # Not checked: this environment's CAD kernels abort the interpreter as
        # it exits, after the routes have been printed.
        check=False,
    )
    served = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert served == repr(sorted(TUTORIAL_ROUTES)), (result.stdout, result.stderr)


def test_the_tutorial_endpoints_record_nothing_for_a_demo_client(client, counting):
    """No row, no cookie and no call to any recorder, the null one included:
    these endpoints have nothing to record. A demo render sent by the tutorial
    reaches only the null recorder, as every demo render does."""
    demo = {recording.DEMO_HEADER: "1"}
    null_before = recording.null_recorder().writes
    responses = [
        client.get("/tutorial/lessons?display=dotpad", headers=demo),
        client.post("/tutorial/test-pattern", json={"corner": "br", "width": 60, "height": 40}, headers=demo),
        client.post("/tutorial/locate", json=pose(), headers=demo),
        client.get("/tutorial/prints.zip", headers=demo),
    ]
    for response in responses:
        if response.request.path == "/tutorial/lessons" and response.status_code == 503:
            continue  # the lessons module is someone else's and may be absent
        assert response.status_code == 200, (response.request.path, response.get_data(as_text=True)[:200])
        assert "Set-Cookie" not in response.headers
    assert recording.null_recorder().writes == null_before, "a tutorial endpoint reached a recorder"

    rendered = client.post("/render", json=pose(depth=52), headers={**demo, **TUTORIAL_HEADERS})
    assert rendered.status_code == 200
    assert "Set-Cookie" not in rendered.headers
    assert counting.calls == [], f"a demo request wrote: {counting.names()}"


# ---------------------------------------------------------------------------
# /tutorial/lessons
# ---------------------------------------------------------------------------

lessons_missing = importlib.util.find_spec("app.tutorial_lessons") is None


@pytest.mark.skipif(lessons_missing, reason="app/tutorial_lessons.py (the lesson content) is not in this tree yet")
@pytest.mark.parametrize("display", ["monarch", "dotpad", "none"])
def test_the_lessons_come_with_the_landmarks(client, display):
    response = client.get(f"/tutorial/lessons?display={display}")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    body = response.get_json()
    for field in ("version", "lessons", "keys", "key_help", "landmarks", "model"):
        assert field in body, f"the lessons payload has no {field}"
    assert body["model"] == "tutorial_mug"
    assert isinstance(body["lessons"], list) and body["lessons"]
    assert body["landmarks"]["stl_sha256"] == LANDMARKS["stl_sha256"]
    names = {band["name"] for bands in body["landmarks"]["axes"].values() for band in bands}
    for required in ("x.handle_loop", "y.handle_arms", "z.floor", "z.above_floor", "z.handle_beside_ring"):
        assert required in names


def test_an_unknown_display_is_refused(client):
    assert client.get("/tutorial/lessons?display=braille-note").status_code == 400


def test_missing_lessons_are_reported_rather_than_crashing(client, monkeypatch):
    """The blueprint must load, and say so plainly, even without the lessons."""
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        tutorial.importlib.util, "find_spec",
        lambda name, *a: None if name == "app.tutorial_lessons" else real_find_spec(name, *a),
    )
    response = client.get("/tutorial/lessons")
    assert response.status_code == 503
    assert response.get_json()["status"] == "error"


# ---------------------------------------------------------------------------
# /tutorial/test-pattern
# ---------------------------------------------------------------------------

# The Monarch packing, as braille_display._pixels_to_braille_cells documents it:
# bit -> (row, column) within a 4x2 cell.
MONARCH_BITS = {0: (0, 0), 1: (1, 0), 2: (2, 0), 3: (0, 1), 4: (1, 1), 5: (2, 1), 6: (3, 0), 7: (3, 1)}
# The DotPad packing, column by column (_pixels_to_braille_cells_dotpad).
DOTPAD_BITS = {0: (0, 0), 1: (1, 0), 2: (2, 0), 3: (3, 0), 4: (0, 1), 5: (1, 1), 6: (2, 1), 7: (3, 1)}


def _unpack(hex_string, width, height, bits):
    cells = bytes.fromhex(hex_string)
    cols = width // 2
    pixels = np.zeros((height, width), dtype=bool)
    for index, value in enumerate(cells):
        line, col = divmod(index, cols)
        for bit, (row, dx) in bits.items():
            if value & (1 << bit):
                pixels[line * 4 + row, col * 2 + dx] = True
    return pixels


@pytest.mark.parametrize("width,height", [(96, 40), (60, 40), (78, 40)])
@pytest.mark.parametrize("corner", ["tl", "tr", "bl", "br"])
def test_the_test_pattern_is_a_frame_and_a_block_in_the_named_corner(client, width, height, corner):
    response = client.post("/tutorial/test-pattern", json={"corner": corner, "width": width, "height": height})
    assert response.status_code == 200
    body = response.get_json()
    cells = (width // 2) * (height // 4)
    assert body["status"] == "success"
    assert (body["corner"], body["width"], body["height"]) == (corner, width, height)
    assert len(body["monarch_cells_hex"]) == 2 * cells
    assert len(body["dotpad_graphic_hex"]) == 2 * cells
    assert body["monarch_cells_hex"] == body["monarch_cells_hex"].lower()
    assert body["dotpad_graphic_hex"] == body["dotpad_graphic_hex"].upper()

    for hex_string, bits in ((body["monarch_cells_hex"], MONARCH_BITS), (body["dotpad_graphic_hex"], DOTPAD_BITS)):
        pixels = _unpack(hex_string, width, height, bits)
        # The frame, all the way round.
        assert pixels[0, :].all() and pixels[-1, :].all() and pixels[:, 0].all() and pixels[:, -1].all()
        # The block: 3x3, its outer corner 3 pins in from the named corner, with
        # nothing else raised inside the frame.
        top = 3 if corner in ("tl", "tr") else height - 6
        left = 3 if corner in ("tl", "bl") else width - 6
        inner = pixels[1:-1, 1:-1].copy()
        assert inner[top - 1:top + 2, left - 1:left + 2].all(), f"no block at the {corner} corner"
        inner[top - 1:top + 2, left - 1:left + 2] = False
        assert not inner.any(), "something besides the frame and the block is raised"


@pytest.mark.parametrize(
    "body",
    [
        {"corner": "tl", "width": 95, "height": 40},   # not whole cells
        {"corner": "tl", "width": 96, "height": 42},   # not whole lines
        {"corner": "tl", "width": 202, "height": 40},  # larger than 200
        {"corner": "tl", "width": 96, "height": 204},
        {"corner": "tl", "width": 8, "height": 8},     # too small for the pattern
        {"corner": "middle", "width": 96, "height": 40},
        {"corner": "tl", "width": "wide", "height": 40},
    ],
)
def test_a_test_pattern_for_no_real_display_is_refused(client, body):
    assert client.post("/tutorial/test-pattern", json=body).status_code == 400


# ---------------------------------------------------------------------------
# /tutorial/locate
# ---------------------------------------------------------------------------

FRAMINGS = {
    "default pose": pose(),
    "zoomed, scrollbar composed": pose(zoom=0.5),
    "zoomed and panned": pose(zoom=0.3, camera_center=[0.05, 0.02], move_camera_center="left"),
    "top view on a DotPad": pose(
        view="z+", zoom=1.0, target_pixel_width=60, target_pixel_height=40,
        orientation={"scheme": "basis-v1", "forward": [0, 0, 1], "up": [0, 1, 0], "right": [1, 0, 0]},
    ),
    "turned off the named view": pose(
        zoom=0.2, orientation={"scheme": "basis-v1", "forward": [1, 0, 0], "up": [0, 1, 0], "right": [0, 0, -1]},
    ),
    "slice graph, no display": pose(
        mode="slice-graph", compose_slicegraph=True, zoom=0.2, target_pixel_width=78, target_pixel_height=40,
    ),
}


@pytest.mark.parametrize("name", FRAMINGS)
def test_locate_frames_a_request_exactly_as_render_does(name):
    """The window /locate maps into must be the one render() drew. render()
    reports it as framing_bounds, so the two are compared directly."""
    params, stem, _pan, _fp = server._prepare_render_params(FRAMINGS[name])
    engine = server.get_or_create_renderer(stem)
    grid = server._target_grid(params)
    with server.render_lock:
        result = engine.render(params, screen_size=list(grid) if grid else None)
    framing = tutorial._framing(engine, params, grid)
    np.testing.assert_allclose(np.asarray(framing["limits"]), np.asarray(result.framing_bounds), rtol=0, atol=1e-12)
    assert list(result.camera_center) == pytest.approx(
        [(framing["limits"][0][0] + framing["limits"][0][1]) / 2, (framing["limits"][1][0] + framing["limits"][1][1]) / 2]
    )


@pytest.mark.parametrize(
    "body",
    [
        pose(),
        pose(zoom=0.5),
        pose(zoom=0.3, move_camera_center="left"),
        pose(renderMode="Filled", depth=30),
    ],
    ids=["default pose", "zoomed", "panned", "filled"],
)
def test_the_boxes_hold_the_pins_the_render_raised(client, body):
    """Rendered through /render, as the display gets it, then located. Every
    raised pin of the model lies in the handle box or the body box, and each
    box has raised pins in it: the boxes describe the picture on the pins."""
    rendered = client.post("/render", json=body, headers=TUTORIAL_HEADERS)
    assert rendered.status_code == 200
    raised = _raised(rendered.get_json())

    located = client.post("/tutorial/locate", json=body)
    assert located.status_code == 200, located.get_data(as_text=True)
    answer = located.get_json()
    width, height = answer["grid"]["width"], answer["grid"]["height"]
    assert (answer["display"]["width"], answer["display"]["height"]) == (96, 40)

    # Only the drawable area: with the scrollbar composed, its column and row
    # are raised too and belong to no landmark.
    model_pins = raised[:height, :width]
    rows, cols = np.nonzero(model_pins)
    handle, body_part = answer["landmarks"]["handle"], answer["landmarks"]["body"]
    assert handle["present"] and body_part["present"]
    in_handle = _inside(handle["box"], rows, cols)
    in_body = _inside(body_part["box"], rows, cols)
    assert in_handle.sum() > 20, "the handle box holds almost no raised pins"
    assert in_body.sum() > 20, "the body box holds almost no raised pins"
    stray = [(int(r), int(c)) for r, c in zip(rows, cols) if not (_inside(handle["box"], r, c) or _inside(body_part["box"], r, c))]
    assert not stray, f"raised pins outside both boxes: {stray[:10]}"


def test_in_the_default_pose_the_handle_is_left_of_the_body(client):
    """x- is the Right view: +Y runs to the right, so a handle toward -Y is on
    the left, which is what the lessons say."""
    answer = client.post("/tutorial/locate", json=pose()).get_json()
    handle, body = answer["landmarks"]["handle"], answer["landmarks"]["body"]
    assert handle["visible"] and body["visible"]
    assert handle["centre"][0] < body["centre"][0]
    assert handle["box"][0] < body["box"][0]
    assert answer["grid"] == {"width": 96, "height": 40}


def test_zooming_in_moves_the_handle_toward_the_left_edge(client):
    """Side is where on the display, by thirds. At zoom 0 the whole mug sits in
    the middle; zoomed in, its handle reaches the left third."""
    near = client.post("/tutorial/locate", json=pose(zoom=0.5)).get_json()["landmarks"]["handle"]
    assert near["side"] == "left"


def test_a_handle_panned_off_the_display_is_off_that_side(client):
    """Zoomed in and looking well to the right of the mug, the handle, on the
    mug's left, has gone off the left edge."""
    params, stem, _pan, _fp = server._prepare_render_params(pose(zoom=2.0))
    framing = tutorial._framing(server.get_or_create_renderer(stem), params, (96, 40))
    (x_lo, x_hi), (y_lo, y_hi) = framing["limits"]
    body = pose(zoom=2.0, camera_center=[(x_lo + x_hi) / 2 + 0.9 * (x_hi - x_lo), (y_lo + y_hi) / 2])
    handle = client.post("/tutorial/locate", json=body).get_json()["landmarks"]["handle"]
    assert handle["present"]
    assert not handle["visible"]
    assert handle["side"] == "off-left"


def test_a_cut_that_misses_the_handle_says_so(client):
    """Cut at 20% from the right, well clear of the handle, the Cut render raises
    nothing of it, and there is no box to put a cursor in."""
    handle = client.post("/tutorial/locate", json=pose(depth=20)).get_json()["landmarks"]["handle"]
    assert handle == {"present": False, "visible": False, "box": None, "centre": None, "side": None}


def test_the_slice_graph_rows_are_not_counted_as_showing(client):
    answer = client.post("/tutorial/locate", json=pose(mode="slice-graph", compose_slicegraph=True)).get_json()
    assert answer["graph_rows"] == 11


@pytest.mark.parametrize(
    "body,status",
    [
        (pose(model="cube", current_model="cube"), 400),
        (pose(mode="side-by-side"), 400),
    ],
    ids=["another model", "side by side"],
)
def test_locate_refuses_what_it_cannot_describe(client, body, status):
    response = client.post("/tutorial/locate", json=body)
    assert response.status_code == status
    assert response.get_json()["status"] == "error"


def test_locate_refuses_a_mug_that_does_not_match_its_landmarks(client, monkeypatch):
    """A model directory seeded from an older build keeps its old copy. Boxes
    measured on a different mesh would be wrong without any sign of it."""
    monkeypatch.setattr(tutorial, "_file_sha256", lambda path: "0" * 64)
    response = client.post("/tutorial/locate", json=pose())
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# /tutorial/prints.zip
# ---------------------------------------------------------------------------

EXPECTED_PRINTS = {"tutorial_mug.stl", "README.txt"} | {f"plaque_z{p:02d}.stl" for p in range(5, 100, 10)}


@pytest.fixture()
def no_disk_writes(monkeypatch):
    """Fails the test if anything opens a file for writing while it runs."""
    writes: list[str] = []
    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in "wax+"):
            writes.append(f"open({file!r}, {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in "wax+"):
            writes.append(f"io.open({file!r}, {mode!r})")
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            writes.append(f"os.open({path!r})")
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    return writes


def test_the_prints_are_built_in_memory(client, monkeypatch, no_disk_writes):
    monkeypatch.setattr(tutorial, "_prints_zip", None)  # build it now, under the guard
    response = client.get("/tutorial/prints.zip")
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert "attachment" in response.headers.get("Content-Disposition", "")
    assert not no_disk_writes, f"building the prints wrote to disk: {no_disk_writes}"

    again = client.get("/tutorial/prints.zip")
    assert again.get_data() == response.get_data(), "the second request did not get the same archive"


def test_the_prints_are_the_mug_ten_plaques_and_a_readme(client):
    archive = zipfile.ZipFile(io.BytesIO(client.get("/tutorial/prints.zip").get_data()))
    assert set(archive.namelist()) == EXPECTED_PRINTS
    assert archive.read("tutorial_mug.stl") == (ROOT / "builtin_models" / "tutorial_mug.stl").read_bytes()

    for percent in range(5, 100, 10):
        plaque = trimesh.load(io.BytesIO(archive.read(f"plaque_z{percent:02d}.stl")), file_type="stl")
        assert plaque.is_watertight, f"plaque_z{percent:02d} is not closed, so it will not slice"
        assert plaque.volume > 0, f"plaque_z{percent:02d} is inside out"
        low, high = plaque.bounds
        assert low[2] == pytest.approx(0.0) and high[2] == pytest.approx(3.0)
        # It is the mug's own cross-section, so it sits inside the mug's outline.
        assert low[0] >= LANDMARKS["bbox_mm"]["x"][0] - 1e-3 and high[0] <= LANDMARKS["bbox_mm"]["x"][1] + 1e-3
        assert low[1] >= LANDMARKS["bbox_mm"]["y"][0] - 1e-3 and high[1] <= LANDMARKS["bbox_mm"]["y"][1] + 1e-3

    # A plaque with the handle beside the ring reaches out toward -Y; the floor
    # plaque does not.
    beside = trimesh.load(io.BytesIO(archive.read("plaque_z45.stl")), file_type="stl")
    floor = trimesh.load(io.BytesIO(archive.read("plaque_z05.stl")), file_type="stl")
    assert beside.bounds[0][1] < -1.3 * beside.bounds[1][1]
    assert floor.bounds[0][1] == pytest.approx(-floor.bounds[1][1], abs=0.5)

    readme = archive.read("README.txt").decode("utf-8")
    assert "handle" in readme and "nearest you" in readme
    assert "\u2014" not in readme and "\u2013" not in readme
    for percent in range(5, 100, 10):
        assert f"plaque_z{percent:02d}.stl" in readme


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def test_a_tutorial_event_is_accepted_and_cut_down(client, counting):
    response = client.post(
        "/events/track",
        json={"event_type": "tutorial",
              "event_data": {"action": "lesson_completed", "lesson": "depth", "keys": ["pageup"]}},
    )
    assert response.status_code == 200
    events = [args for name, args, _kwargs in counting.calls if name == "page_event"]
    assert events == [(None, "tutorial", {"action": "lesson_completed", "lesson": "depth"})]


@pytest.mark.parametrize(
    "event_data",
    [
        {"action": "pressed_key", "lesson": "depth"},
        {"action": "loaded", "lesson": "Lesson <b>4</b>"},
        None,
    ],
)
def test_a_tutorial_event_that_says_something_else_is_refused(client, counting, event_data):
    response = client.post("/events/track", json={"event_type": "tutorial", "event_data": event_data})
    assert response.status_code == 400
    assert "page_event" not in counting.names()


def test_every_tutorial_action_is_allowed(client, counting):
    for action in ("loaded", "lesson_completed", "lesson_skipped", "exited", "completed", "redo", "resumed"):
        response = client.post("/events/track", json={"event_type": "tutorial", "event_data": {"action": action, "lesson": None}})
        assert response.status_code == 200, action


def test_a_tutorial_render_leaves_no_analytics_row_and_nothing_else_changes(client, counting):
    """Only the usage-analytics render row is skipped. The braille log and the
    study's own record are written exactly as for any other render."""
    assert client.post("/render", json=pose(depth=47), headers=TUTORIAL_HEADERS).status_code == 200
    tutorial_calls = counting.names()
    counting.calls.clear()
    _clear_render_caches()
    assert client.post("/render", json=pose(depth=47)).status_code == 200
    plain_calls = counting.names()

    assert "render" not in tutorial_calls
    assert "render" in plain_calls
    assert sorted(name for name in plain_calls if name != "render") == sorted(tutorial_calls)
    assert "braille_event" in tutorial_calls


def test_only_the_exact_header_value_counts():
    class Request:
        def __init__(self, value):
            self.headers = {tutorial.TUTORIAL_HEADER: value} if value is not None else {}

    assert tutorial.is_tutorial_request(Request("1"))
    assert tutorial.is_tutorial_request(Request(" 1 "))
    assert not tutorial.is_tutorial_request(Request("0"))
    assert not tutorial.is_tutorial_request(Request(None))


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------

def test_startup_warms_the_tutorial_mug_first(monkeypatch):
    import threading

    queued = []
    monkeypatch.setattr(server, "enqueue_model_for_warmup", queued.append)
    monkeypatch.setattr(server, "_warmup_state", {"total": 0, "processed": 0, "current": None, "started": False})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(server, "STARTUP_WARMUP_LIMIT", 1)
    assert server.AVAILABLE_MODELS[0].stem != "tutorial_mug", "test needs the mug not to be first already"

    server.start_model_warmup()
    assert [p.stem for p in queued] == ["tutorial_mug"]


def test_seeding_replaces_a_stale_tutorial_mug_and_nothing_else():
    """The tutorial mug is generated and its landmarks ship with the code, so an
    older copy left on a models volume is replaced; every other built-in is
    still left alone once present (an operator may have edited it)."""
    from app.server import BUILTIN_SOURCE_DIR, MODEL_DIR, _seed_builtin_models

    _seed_builtin_models()
    mug = MODEL_DIR / "tutorial_mug.stl"
    other = MODEL_DIR / "mug.stl"
    mug_bytes, other_bytes = mug.read_bytes(), other.read_bytes()
    try:
        mug.write_bytes(b"solid stale\nendsolid stale\n")
        other.write_bytes(b"solid edited\nendsolid edited\n")
        assert _seed_builtin_models() == 1
        assert mug.read_bytes() == (BUILTIN_SOURCE_DIR / "tutorial_mug.stl").read_bytes()
        assert other.read_bytes() == b"solid edited\nendsolid edited\n"
        assert _seed_builtin_models() == 0
    finally:
        mug.write_bytes(mug_bytes)
        other.write_bytes(other_bytes)
