"""Three stations at once, and none of them can feel the others.

The Heiskell session runs three tactile displays simultaneously -- a Dot Pad
brought in, plus the host's Dot Pad and Monarch -- in separate browsers, with
different people exploring different models. The event does not work if one
person changing depth, rotating, switching render mode or loading a model has any
effect on another's display.

What these pin down
-------------------
A station's state lives in its browser and arrives with each request. The server
holds nothing that says "the current model" or "the current session", so there is
no shared thing for two stations to fight over. These tests try to make them
fight anyway: divergent sequences, run interleaved and then run concurrently on
threads, each asserted against what its own sequence alone implies -- computed by
replaying that sequence on a fresh client with nobody else on the server.

The last group covers two tabs in one browser, which is where a hidden singleton
or a shared storage key shows up first: those two share a cookie jar, an origin
and a process, so anything keyed on the browser rather than on the request would
collapse them into one.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import app.recording as recording
from app.server import app as flask_app


ROOT = Path(__file__).resolve().parents[1]
DEMO_HEADERS = {recording.DEMO_HEADER: "1"}

# Two different displays, so pairing is exercised rather than assumed. A DotPad
# is 60x40 pixels of tactile cells; a Monarch is 96x40.
DOTPAD_GRID = {"target_pixel_width": 60, "target_pixel_height": 40, "output_device": "dotpad"}
MONARCH_GRID = {"target_pixel_width": 96, "target_pixel_height": 40, "output_device": "monarch"}


def _state(**overrides):
    """One station's complete view state, as its browser would send it."""
    body = {
        "view": "y-",
        "renderMode": "Filled",
        "mode": "single",
        "depth": 50,
        "zoom": "0",
        "current_model": "cube",
        "input_source": "keyboard",
    }
    body.update(DOTPAD_GRID)
    body.update(overrides)
    return body


def _render(client, state):
    response = client.post("/render", json=state, headers=DEMO_HEADERS)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _fingerprint(body):
    """What actually reached this station's display, plus where it is looking."""
    return (body["image_base64"], tuple(body["image_shape"]), tuple(body.get("camera_center") or ()))


def _new_client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# Three divergent sequences
#
# Each is a plausible few minutes at a station: a model, a depth walk, a render
# mode, a rotation, an axis switch. They differ in every one of those, so a
# leak in any single dimension shows up as a mismatch.
# ---------------------------------------------------------------------------

STATION_SEQUENCES = {
    "brought-in dotpad": [
        _state(current_model="mug", depth=0),
        _state(current_model="mug", depth=25),
        _state(current_model="mug", depth=25, renderMode="Outline"),
        _state(current_model="mug", depth=25, renderMode="Outline", view="z+"),
    ],
    "host dotpad": [
        _state(current_model="lego_2x4", depth=90, renderMode="Cut"),
        _state(current_model="lego_2x4", depth=60, renderMode="Cut", view="x-"),
        _state(current_model="lego_2x4", depth=60, renderMode="Cut", view="x-", zoom="1.5"),
    ],
    "host monarch": [
        _state(current_model="cube", depth=10, renderMode="x-ray", **MONARCH_GRID),
        _state(current_model="cube", depth=10, renderMode="x-ray", view="y+", **MONARCH_GRID),
        _state(
            current_model="cube", depth=10, renderMode="x-ray", view="y+",
            orientation={
                "scheme": "basis-v1",
                "forward": [0, -1, 0], "up": [1, 0, 0], "right": [0, 0, 1],
            },
            **MONARCH_GRID,
        ),
    ],
}


@pytest.fixture()
def alone_results():
    """What each sequence produces with nobody else on the server.

    The reference the concurrent runs are judged against. Computed first, one
    station at a time, so it cannot itself be contaminated by the interleaving it
    exists to detect.
    """
    results = {}
    for name, sequence in STATION_SEQUENCES.items():
        client = _new_client()
        for step in sequence:
            body = _render(client, step)
        results[name] = _fingerprint(body)
    return results


def test_three_interleaved_stations_each_get_their_own_sequence(alone_results):
    """Round-robin, so every station's request lands between two of somebody
    else's. This is the ordering a real room produces."""
    clients = {name: _new_client() for name in STATION_SEQUENCES}
    finals = {}

    longest = max(len(seq) for seq in STATION_SEQUENCES.values())
    for index in range(longest):
        for name, sequence in STATION_SEQUENCES.items():
            if index < len(sequence):
                finals[name] = _fingerprint(_render(clients[name], sequence[index]))

    for name, expected in alone_results.items():
        assert finals[name] == expected, (
            f"station '{name}' ended up somewhere its own commands do not explain; "
            f"another station moved it"
        )


def test_three_concurrent_stations_each_get_their_own_sequence(alone_results):
    """The same, on threads, with no coordination between them at all."""
    finals: dict[str, tuple] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(STATION_SEQUENCES))

    def run(name, sequence):
        try:
            client = _new_client()
            barrier.wait(timeout=30)  # start together, so the requests really overlap
            for step in sequence:
                body = _render(client, step)
            finals[name] = _fingerprint(body)
        except BaseException as error:  # noqa: BLE001 - re-raised in the assertion below
            errors.append(error)

    threads = [
        threading.Thread(target=run, args=(name, sequence), daemon=True)
        for name, sequence in STATION_SEQUENCES.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    assert not errors, f"a station failed under concurrency: {errors!r}"
    assert set(finals) == set(STATION_SEQUENCES), "a station never finished"
    for name, expected in alone_results.items():
        assert finals[name] == expected, (
            f"station '{name}' diverged under concurrency; state is being shared"
        )


# ---------------------------------------------------------------------------
# The specific things that must not leak
# ---------------------------------------------------------------------------

def test_two_stations_on_the_same_model_at_different_depths_do_not_collide():
    """Same model, both live, different planes. Content-addressed caching means
    they legitimately share work; they must not share a *position*."""
    a, b = _new_client(), _new_client()

    a_first = _render(a, _state(current_model="mug", depth=20))
    b_first = _render(b, _state(current_model="mug", depth=80))
    assert a_first["image_base64"] != b_first["image_base64"], "two depths rendered the same picture"

    a_again = _render(a, _state(current_model="mug", depth=20))
    b_again = _render(b, _state(current_model="mug", depth=80))
    assert a_again["image_base64"] == a_first["image_base64"]
    assert b_again["image_base64"] == b_first["image_base64"]


def test_two_stations_on_the_same_model_at_the_same_depth_are_fine():
    """The other half: identical requests must succeed and agree, not conflict."""
    a, b = _new_client(), _new_client()
    a_body = _render(a, _state(current_model="mug", depth=50))
    b_body = _render(b, _state(current_model="mug", depth=50))
    assert a_body["image_base64"] == b_body["image_base64"]


def test_one_stations_rotation_does_not_turn_another():
    a, b = _new_client(), _new_client()
    upright = _state(current_model="lego_2x4", depth=50)
    before = _render(b, upright)

    _render(a, _state(
        current_model="lego_2x4", depth=50,
        orientation={"scheme": "basis-v1", "forward": [1, 0, 0], "up": [0, 0, 1], "right": [0, -1, 0]},
    ))

    after = _render(b, upright)
    assert after["image_base64"] == before["image_base64"], "the other station rotated"


def test_one_stations_render_mode_does_not_change_another():
    a, b = _new_client(), _new_client()
    filled = _state(current_model="cube", depth=40, renderMode="Filled")
    before = _render(b, filled)

    for mode in ("Outline", "Cut", "x-ray"):
        _render(a, _state(current_model="cube", depth=40, renderMode=mode))

    assert _render(b, filled)["image_base64"] == before["image_base64"]


def test_each_station_gets_frames_at_its_own_displays_size():
    """Display pairing. A Monarch station and a DotPad station asking at the same
    instant must each get their own grid, not whichever was configured last."""
    dotpad, monarch = _new_client(), _new_client()

    dotpad_body = _render(dotpad, _state(current_model="cube", **DOTPAD_GRID))
    monarch_body = _render(monarch, _state(current_model="cube", **MONARCH_GRID))

    assert dotpad_body["image_shape"] == [40, 60], dotpad_body["image_shape"]
    assert monarch_body["image_shape"] == [40, 96], monarch_body["image_shape"]
    # And the Monarch station gets the packed cells it needs; the DotPad one does
    # not, because it does not send them over Web HID.
    assert "monarch_cells_hex" not in dotpad_body


def test_a_stations_pan_stays_in_that_station():
    """The failure #123 was reported for, re-checked with three stations rather
    than two: a pan is relative to where *this* window already was."""
    a, b, c = _new_client(), _new_client(), _new_client()
    base = _state(current_model="mug", zoom="2.0")

    b_before = _render(b, base)
    c_before = _render(c, base)

    panned = _render(a, {**base, "move_camera_center": "left", "camera_center": b_before["camera_center"]})
    assert panned["camera_center"] != b_before["camera_center"], "the pan did nothing"

    assert _render(b, base)["camera_center"] == b_before["camera_center"]
    assert _render(c, base)["camera_center"] == c_before["camera_center"]


# ---------------------------------------------------------------------------
# Two tabs in one browser
#
# One cookie jar, one origin, one process. Anything the server keyed on the
# browser rather than on the request would merge these two, and anything the
# page kept in localStorage would be shared between them.
# ---------------------------------------------------------------------------

def test_two_tabs_in_one_browser_keep_separate_state():
    browser = _new_client()  # one client == one cookie jar == one browser

    tab_one = _state(current_model="mug", depth=15, renderMode="Outline")
    tab_two = _state(current_model="lego_2x4", depth=85, renderMode="Cut", view="z-")

    one_first = _render(browser, tab_one)
    two_first = _render(browser, tab_two)
    assert one_first["image_base64"] != two_first["image_base64"]

    # Interleave them the way two tabs actually interleave.
    for _ in range(3):
        assert _render(browser, tab_one)["image_base64"] == one_first["image_base64"]
        assert _render(browser, tab_two)["image_base64"] == two_first["image_base64"]


def test_the_server_sets_no_cookie_that_two_tabs_would_share_on_the_demo_path():
    browser = _new_client()
    for path in ("/demo", "/demo/status"):
        response = browser.get(path)
        assert "Set-Cookie" not in response.headers, f"{path} would key two tabs together"


def test_the_demo_page_replaces_browser_storage_rather_than_sharing_it():
    """localStorage is per-origin, not per-tab: two demo tabs in one browser would
    otherwise read and write each other's settings through it. The shim gives each
    tab its own in-memory store, which closes that and satisfies the separate rule
    that nothing may survive the tab closing."""
    bootstrap = (ROOT / "static" / "js" / "demo-bootstrap.js").read_text(encoding="utf-8")
    for name in ("localStorage", "sessionStorage"):
        assert f"'{name}'" in bootstrap, f"{name} is not replaced on the demo path"
    assert "memoryStorage" in bootstrap
    assert "__CAD_DEMO_SEALED__" in bootstrap


# ---------------------------------------------------------------------------
# No shared server-side session, and no shared display connection
# ---------------------------------------------------------------------------

SERVER_SOURCE = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
VIEWER_SOURCE = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
DOTPAD_SOURCE = (ROOT / "static" / "js" / "dotpad-integration.js").read_text(encoding="utf-8")
MONARCH_SOURCE = (ROOT / "static" / "js" / "monarch-hid.js").read_text(encoding="utf-8")


def test_the_server_holds_no_current_model_or_current_view():
    """A render is a function of what the request carries. A process-wide "current"
    anything is a station's state living where another station can reach it."""
    for forbidden in ("current_model_index", "CURRENT_MODEL", "current_view =", "CURRENT_VIEW"):
        assert forbidden not in SERVER_SOURCE, f"{forbidden} is process-wide station state"


def test_the_display_connection_lives_in_the_browser_not_on_the_server():
    """Each station pairs with its own display over Web Bluetooth / Web HID, from
    its own tab. There is no server-side connection object for three stations to
    queue behind."""
    for term in ("bleak", "pydbus", "bluetooth.", "hid.device", "serial.Serial"):
        assert term not in SERVER_SOURCE, f"{term} suggests a server-side display connection"

    # The connection handles are module-scoped inside each page's own script, so
    # every tab that loads them gets its own.
    assert "let connectedDevice = null;" in DOTPAD_SOURCE
    assert "let monarchHidDevice = null;" in MONARCH_SOURCE
    # ...and nothing hangs them off a shared global where another tab could find
    # them. (window is per-document, but a same-origin opener could reach across.)
    assert "window.connectedDevice" not in DOTPAD_SOURCE
    assert "window.monarchHidDevice" not in MONARCH_SOURCE


def test_a_demo_station_derives_no_identity_from_a_shared_counter():
    """Nothing about a demo station's identity may come from the study's
    participant sequence, or from anything else the server increments."""
    for forbidden in ("preview_next_code", "next_sequence_preview", "create_participant"):
        assert forbidden not in VIEWER_SOURCE, f"the viewer reaches {forbidden}"

    # The one handle a demo tab keeps is generated in the browser and held in
    # memory; on the demo path even the storage it would use is the in-memory shim.
    assert "getUploadSessionId" in VIEWER_SOURCE
    assert "Math.random()" in VIEWER_SOURCE
