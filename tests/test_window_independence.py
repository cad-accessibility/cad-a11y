"""Two windows using the app at once must not move each other.

Reported from a workshop (#123): a participant's viewer showed somebody else's
model, or moved off the one they were working on. The same is reachable on the
normal viewer, because a renderer is shared by every window looking at a model
and it used to keep the camera on itself.

The rule these pin down: a render is a function of what the request carries. The
window owns where it is looking, sends that every time, and gets it back.
"""

from __future__ import annotations

import pathlib
from collections import OrderedDict

import pytest

import app.cad_comparison_lib as cad_lib
import app.server
from app.server import app as flask_app


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _params(**overrides):
    body = {
        "view": "y-", "renderMode": "Filled", "mode": "single",
        "depth": 0, "zoom": "0", "current_model": 0,
        "target_pixel_width": 96, "target_pixel_height": 40,
    }
    body.update(overrides)
    return body


def _render(client, **overrides):
    response = client.post("/render", json=_params(**overrides))
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


# --- The window owns where it is looking -----------------------------------


def test_a_render_says_where_it_ended_up_looking(client):
    """Without this the window has nothing to send back, which is exactly why
    every window sent a null centre and the shared renderer decided for them."""
    body = _render(client)
    assert "camera_center" in body, "the response does not report the camera centre"
    centre = body["camera_center"]
    assert isinstance(centre, list) and len(centre) == 2
    assert all(isinstance(v, float) for v in centre)


def test_panning_moves_only_the_window_that_panned(client):
    """The failure this exists for. Two windows, one model. One pans; the other
    must render exactly what it rendered before."""
    other = flask_app.test_client()

    before = _render(other)
    assert _render(client, move_camera_center="left")["camera_center"] != before["camera_center"]

    after = _render(other)
    assert after["camera_center"] == before["camera_center"], "the other window's centre moved"
    assert after["image_base64"] == before["image_base64"], "the other window's image changed"


def test_pans_accumulate_for_the_window_doing_them(client):
    """A held arrow key has to keep going. When the centre came back from the
    request rather than the renderer, each press started from the same place and
    the model never moved past one step."""
    centre = _render(client)["camera_center"]
    seen = [centre]
    for _ in range(3):
        centre = _render(client, camera_center=centre, move_camera_center="left")["camera_center"]
        seen.append(centre)

    xs = [c[0] for c in seen]
    assert xs == sorted(xs, reverse=True), f"pans did not keep moving: {xs}"
    assert len(set(xs)) == len(xs), "a pan produced no movement"


def test_a_window_that_names_no_centre_gets_the_default_not_someone_elses(client):
    """A fresh window sends no centre. It must be framed on the model, not on
    wherever the last person to pan happened to leave things."""
    other = flask_app.test_client()
    fresh_default = _render(other)["camera_center"]

    panned = _render(client, move_camera_center="left")["camera_center"]
    assert panned != fresh_default

    newcomer = flask_app.test_client()
    assert _render(newcomer)["camera_center"] == fresh_default


def test_the_centre_belongs_to_the_view_it_was_measured_in(client):
    """Different views frame different extents, so a centre from one is not
    meaningful in another."""
    front = _render(client, view="y-")["camera_center"]
    top = _render(client, view="z+")["camera_center"]
    assert _render(client, view="y-")["camera_center"] == front
    assert top is not None


# --- The renderer keeps nothing per request --------------------------------


def test_the_renderer_holds_no_camera_or_framing_state():
    """Named for what it is: a default to start from, never written per request.
    The mutable version was the direct cause of pans crossing windows."""
    text = pathlib.Path(cad_lib.__file__).read_text(encoding="utf-8")

    assert "view_current_camera_center" not in text, "per-request camera state is back"
    assert "_orientation_frame_key" not in text, "the framing memo is back"
    assert "view_default_camera_center" in text, "the default table went missing"


def test_a_grid_passed_to_one_render_is_not_visible_to_the_next(client):
    """screen_size used to be swapped onto the shared renderer and restored, so a
    concurrent render could read another window's temporary size."""
    small = _render(client, target_pixel_width=60, target_pixel_height=40)
    large = _render(client, target_pixel_width=96, target_pixel_height=40)
    again = _render(client, target_pixel_width=60, target_pixel_height=40)

    assert small["image_shape"] == again["image_shape"]
    assert small["image_shape"] != large["image_shape"]
    assert again["image_base64"] == small["image_base64"], "the grid leaked between renders"


def test_framing_a_turned_model_does_not_reframe_it_for_everyone(client):
    """Reframing wrote view_limits in place and memoized, so two windows at
    different orientations fought over the window being drawn."""
    turned = {"scheme": "basis-v1", "forward": [0, 1, 0], "up": [1, 0, 0], "right": [0, 0, -1]}
    upright = _render(client)
    _render(client, orientation=turned)
    assert _render(client)["image_base64"] == upright["image_base64"]


def test_a_pan_is_never_served_to_a_window_that_did_not_pan(client):
    """The subtlest form of the leak. "move left" means move from wherever this
    window already was, and no cache key carries that starting point or the verb,
    so a pan and a non-pan of otherwise identical params key the same.

    Reads were guarded against this and writes were not, so a pan stored its
    result under the unpanned key and the next window to render got somebody
    else's panned view back from cache.
    """
    other = flask_app.test_client()

    settled = _render(client)
    _render(client, move_camera_center="left")

    assert _render(other)["image_base64"] == settled["image_base64"], (
        "a pan leaked into the cache and was served to a window that did not pan"
    )
    assert _render(other)["camera_center"] == settled["camera_center"]


# --- What the cache key has to carry ---------------------------------------


def _quantized_key(**overrides):
    from app.server import _build_quantized_render_key
    return _build_quantized_render_key(_params(**overrides), 0)


@pytest.mark.parametrize("field,a,b", [
    # Drawn onto the image. This one is reachable from the viewer: the checkbox
    # was ignored whenever the render came from cache, even in a single window.
    ("show_view_info_box", False, True),
    # Decides whether the response carries the cells a Monarch needs, so a cached
    # answer made for another device arrived without them.
    ("output_device", "dotpad", "monarch_hid"),
    # Not reachable from the viewer today. Keyed so that stays true by design
    # rather than by accident if a caller ever starts sending them.
    ("shape", "after", "before"),
    ("superpositionMode", "outline", "intersection"),
])
def test_anything_that_changes_the_answer_changes_the_key(field, a, b):
    assert _quantized_key(**{field: a}) != _quantized_key(**{field: b}), (
        f"{field} is missing from the key, so one window's render is served for another's"
    )


def test_the_info_box_checkbox_is_honoured_even_on_a_cache_hit(client):
    """The live version of the gap above: render twice with the box off so the
    second is a cache hit, then turn it on and check the image actually changes."""
    off = _render(client, show_view_info_box=False)
    assert _render(client, show_view_info_box=False)["image_base64"] == off["image_base64"]

    on = _render(client, show_view_info_box=True)
    assert on["image_base64"] != off["image_base64"], "the info box was ignored"


def test_there_is_no_single_render_slot_left(client):
    """One entry for the whole server: two windows in different states evicted
    each other on every request, so neither ever hit it."""
    text = pathlib.Path(app.server.__file__).read_text(encoding="utf-8")
    assert "last_render_fingerprint" not in text
    assert "last_render_response" not in text


# --- The renderer registry -------------------------------------------------


def test_renderers_are_keyed_by_file_not_by_list_position():
    """An upload or delete renumbers the discovered list. An index-keyed entry
    then means a different model, which is why every entry used to be discarded
    on any change, costing every window a full mesh reload."""
    import app.server as server

    assert isinstance(server.renderers_by_model, OrderedDict)
    server.get_or_create_renderer(0)
    assert all(isinstance(k, str) for k in server.renderers_by_model), (
        "the registry is not keyed by path"
    )
    assert str(server.AVAILABLE_MODELS[0]) in server.renderers_by_model


def test_one_upload_does_not_cost_every_window_its_renderer(client, tmp_path):
    """The blanket clear meant one visitor uploading reloaded every mesh for
    everyone still working."""
    import io
    import struct

    import app.server as server

    server.get_or_create_renderer(0)
    warm = dict(server.renderers_by_model)
    assert warm, "nothing was warmed, so this test would pass vacuously"

    triangle = b"\0" * 80 + struct.pack("<I", 1) + struct.pack(
        "<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    response = client.post("/upload", content_type="multipart/form-data",
                           data={"file": (io.BytesIO(triangle), "registry_probe.stl")})
    assert response.status_code == 200, response.get_data(as_text=True)

    try:
        for key, renderer in warm.items():
            assert server.renderers_by_model.get(key) is renderer, (
                f"{key} was evicted by an unrelated upload"
            )
    finally:
        (server.UPLOAD_DIR / response.get_json()["filename"]).unlink(missing_ok=True)


def test_the_registry_is_bounded():
    """Nothing clears it wholesale now, so a long session would otherwise hold
    every mesh anyone had ever opened."""
    import app.server as server

    assert server.RENDERER_CACHE_MAX >= 1
    assert "popitem(last=False)" in pathlib.Path(server.__file__).read_text(encoding="utf-8"), (
        "no least-recently-used eviction, so the registry grows without limit"
    )


# --- Models are named, not numbered ----------------------------------------


def test_an_upload_does_not_renumber_a_model_under_an_open_window(client):
    """The reported workshop symptom. A model used to be addressed by position in
    a list rebuilt on every upload, so a window holding a number silently began
    rendering somebody else's model."""
    import io
    import struct

    import app.server as server

    mine = _render(client, model="mug")

    triangle = b"\0" * 80 + struct.pack("<I", 1) + struct.pack(
        "<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    uploaded = []
    try:
        # Names that sort before "mug" are exactly what used to shift its index.
        for name in ("aaa_first.stl", "aab_second.stl"):
            response = client.post("/upload", content_type="multipart/form-data",
                                   data={"file": (io.BytesIO(triangle), name)})
            assert response.status_code == 200
            uploaded.append(response.get_json()["filename"])

        assert _render(client, model="mug")["image_base64"] == mine["image_base64"]
    finally:
        for filename in uploaded:
            (server.UPLOAD_DIR / filename).unlink(missing_ok=True)


def test_an_unknown_model_falls_back_rather_than_borrowing_one(client):
    """There is no process-wide "current model" to fall back to any more, which
    is what used to hand over whatever another window had selected."""
    assert _render(client, model="no_such_model_at_all")["status"] == "success"
    assert _render(client, model=None)["status"] == "success"


def test_a_numeric_model_still_resolves(client):
    """A browser holding an older viewer.js keeps working until it reloads."""
    assert _render(client, model=None, current_model=0)["status"] == "success"


def test_the_server_keeps_no_current_model_for_everyone():
    text = pathlib.Path(app.server.__file__).read_text(encoding="utf-8")
    assert "current_model_index" not in text
    assert "RuntimeState" not in text, "process-wide render state is back"


def test_the_dropdown_selects_models_by_name():
    """Its option values were server list positions, so the selection meant a
    different model after anyone uploaded."""
    viewer = pathlib.Path(app.server.__file__).parents[1] / "static" / "js" / "viewer.js"
    source = viewer.read_text(encoding="utf-8")
    assert "option.value = stem;" in source
    assert "option.value = i;" not in source
