"""Input hardware belongs to the browser window using it, not to the server.

The server used to poll its own serial ports for a WitMotion orientation cube and
a Trinkey slider, then push every reading to every connected browser. The client
registry is a list of queues with nothing attached to say who is who, so there
was no way to address a reading to one window: a cube turned at the server
changed the view in every open window, anywhere.

Both devices already had browser-side drivers that talk to them directly and
drive only their own window. Those are now the only implementation.
"""

from __future__ import annotations

import pathlib

import pytest

import app.server as server
from app.server import app as flask_app

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SOURCE = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
VIEWER_SOURCE = (ROOT / "static" / "js" / "viewer.js").read_text(encoding="utf-8")


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@pytest.mark.parametrize("name", [
    "_slider_worker", "_witmotion_worker", "start_optional_hardware_watchers",
    "_orientation_to_view", "_euler_to_rotation_matrix",
])
def test_the_server_no_longer_reads_hardware(name):
    assert not hasattr(server, name), f"{name} is still on the server"
    assert name not in SERVER_SOURCE


def test_the_server_does_not_open_serial_ports():
    """Reading a port on the server is what made a device belong to everybody."""
    assert "import serial" not in SERVER_SOURCE
    assert "list_ports" not in SERVER_SOURCE
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pyserial" not in requirements, "an unused dependency invites the code back"


def test_no_hardware_state_is_kept_for_everyone():
    assert "cube_value" not in SERVER_SOURCE
    assert "slider_value" not in SERVER_SOURCE


def test_neither_channel_carries_hardware_readings(client):
    """Two channels feed the same client-side reducer. Removing the readings from
    the event stream alone would have left the identical crossover on the
    five-second poll, just slower and harder to spot."""
    payload = client.get("/get_data").get_json()
    assert "cube_value" not in payload
    assert "slider_value" not in payload

    stream = SERVER_SOURCE[SERVER_SOURCE.index("def sse_events"):]
    stream = stream[:stream.index("\n@app.route")]
    assert "cube_value" not in stream and "slider_value" not in stream


def test_the_viewer_does_not_act_on_pushed_hardware():
    assert "cube_value" not in VIEWER_SOURCE
    assert "slider_value" not in VIEWER_SOURCE


@pytest.mark.parametrize("driver,api,call", [
    ("witmotion-imu.js", "navigator.bluetooth", "updateView"),
    ("trinkey-slider.js", "navigator.serial", "updateSliceDepth"),
])
def test_each_device_is_driven_by_the_window_using_it(driver, api, call):
    """Connected by this browser, applied to this window, never sent anywhere."""
    source = (ROOT / "static" / "js" / driver).read_text(encoding="utf-8")
    assert api in source, f"{driver} does not open the device from the browser"
    assert call in source, f"{driver} does not drive its own window"


@pytest.mark.parametrize("button", [
    "witmotion-connect-btn", "trinkey-connect-btn",
])
def test_there_is_a_way_to_connect_them(button):
    """The server used to find the device by itself, so with that gone the page
    has to offer the connection."""
    html = (ROOT / "accessible-3d-viewer.html").read_text(encoding="utf-8")
    assert f'id="{button}"' in html
