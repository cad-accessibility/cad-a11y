"""Two windows using the app at once must not move each other.

Reported from a workshop (#123): a participant's viewer showed somebody else's
model, or moved off the one they were working on. The same is reachable on the
normal viewer, because a renderer is shared by every window looking at a model
and it used to keep the camera on itself.

The rule these pin down: a render is a function of what the request carries. The
window owns where it is looking, sends that every time, and gets it back.
"""

from __future__ import annotations

import numpy as np
import pytest

import app.cad_comparison_lib as cad_lib
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
    source = cad_lib.__file__
    text = open(source, encoding="utf-8").read()

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
