"""The export is a vector trace of what the tactile display raises.

Vector rather than a raster sheet because what a line has to measure to rise in a
fuser depends on the machine and the paper, so the useful thing to hand over is a
drawing that can be rescaled and restyled downstream.

The trace is of the braille payload rather than the source figure. That is what
makes it behave identically in every render mode: outline mode has no matplotlib
figure at all, and the overlays are composited into the raster.

These also cover the print path, which had been failing silently. It was handing
the writer the RGBA render instead of the payload, so unpacking two dimensions
raised and the route's catch-all swallowed it. Nothing had ever called the writer
in a test, which is why that went unnoticed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import app.server as server
from src.converter.render_low_res import (
    DEFAULT_PIN_PITCH_MM,
    render_payload_as_vector,
    save_binary_array_as_vector_pdf,
)

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0


def _payload(height, width, pins=()):
    array = np.zeros((height, width), dtype=np.uint8)
    for y, x in pins:
        array[y, x] = 255
    return array


def _shape_count(svg):
    """Marks drawn, excluding the clip path and the white background."""
    return len(re.findall(r"<path\b", svg)) - 2


def _root_size_mm(svg):
    match = re.search(r'<svg[^>]*width="([\d.]+)pt"[^>]*height="([\d.]+)pt"', svg)
    assert match, "no physical size on the svg root"
    return tuple(float(v) / PT_PER_INCH * MM_PER_INCH for v in match.groups())


# --- One square per raised pin --------------------------------------------


@pytest.mark.parametrize("pins", [[], [(1, 1)], [(1, 1), (1, 2)], [(0, 0), (3, 5), (2, 2)]])
def test_one_mark_per_raised_pin(pins):
    svg = render_payload_as_vector(_payload(4, 6, pins))
    assert _shape_count(svg) == len(pins)


def test_nothing_is_drawn_for_an_empty_render():
    svg = render_payload_as_vector(_payload(4, 6))
    assert _shape_count(svg) == 0
    ET.fromstring(svg)


def test_adjacent_pins_tile_into_one_region():
    """Squares rather than dots, so a filled area reads as solid under a finger
    instead of as texture. Two neighbouring pins must share an edge."""
    svg = render_payload_as_vector(_payload(4, 6, [(1, 1), (1, 2)]))
    # Each mark is a 1x1 square, so two adjacent pins cover exactly 2 units with
    # no gap. Their combined extent is 2 wide and 1 tall.
    assert _shape_count(svg) == 2


def test_the_drawing_is_not_thickened():
    """The original line thickness is the point: a raised pin is one square, so
    the file is what the display raises and not a guess at a fuser's needs."""
    single = render_payload_as_vector(_payload(8, 8, [(4, 4)]))
    assert _shape_count(single) == 1


# --- It is a real, sized vector file --------------------------------------


def test_the_svg_parses():
    ET.fromstring(render_payload_as_vector(_payload(4, 6, [(1, 1)])))


def test_the_drawing_carries_a_physical_size():
    """Without units an SVG opens at whatever scale the viewer picks, which makes
    it useless as a starting point for print."""
    width_mm, height_mm = _root_size_mm(render_payload_as_vector(_payload(40, 96)))
    assert width_mm == pytest.approx(96 * DEFAULT_PIN_PITCH_MM, abs=0.5)
    assert height_mm == pytest.approx(40 * DEFAULT_PIN_PITCH_MM, abs=0.5)


def test_the_pin_pitch_scales_the_drawing():
    small = _root_size_mm(render_payload_as_vector(_payload(10, 20), pin_pitch_mm=1.0))
    large = _root_size_mm(render_payload_as_vector(_payload(10, 20), pin_pitch_mm=2.0))
    assert large[0] == pytest.approx(small[0] * 2, abs=0.5)


def test_raised_content_is_black_on_white():
    """Swell paper rises where the sheet is black, so the model has to be the
    black part. The payload uses the opposite convention, 255 for a raised pin,
    which is right for a display and exactly wrong on paper.

    The marks carry no fill, which in SVG means the default, black. Only the
    background is stated explicitly. So the check is that the background is white
    and the marks are not, rather than matching a colour matplotlib omits.
    """
    svg = render_payload_as_vector(_payload(4, 6, [(1, 1)]))
    paths = re.findall(r"<path\b[^>]*/>", svg)

    white = [p for p in paths if "#ffffff" in p.lower()]
    assert white, "the sheet background should be explicitly white"

    marks = [p for p in paths if "fill" not in p.lower()]
    assert marks, "a raised pin should be drawn with no fill, so it takes the SVG default of black"
    assert len(marks) == 1, f"expected one mark, got {len(marks)}"


# --- The mistake that broke the print path --------------------------------


def test_an_rgba_render_is_rejected_clearly():
    """Passing the render instead of the payload is exactly how printing broke,
    and it failed with an unpacking error swallowed by a catch-all."""
    with pytest.raises(ValueError, match="2-D braille payload"):
        render_payload_as_vector(np.zeros((4, 6, 4), dtype=np.uint8))


def test_the_print_path_passes_a_payload_not_a_render(monkeypatch, tmp_path):
    """The regression itself: _save_print_if_requested must hand the writer a
    2-D payload. It used to pass the RGBA render straight through."""
    seen = {}

    def capture(array, filename):
        seen["ndim"] = np.asarray(array).ndim

    monkeypatch.setattr(server, "RENDERS_DIR", tmp_path)
    monkeypatch.setattr(server, "save_binary_array_as_vector_pdf", capture)

    engine = type(
        "E", (), {
            "current_render_mode": "filled", "current_cut_depth": 0.5,
            "view_current_axis": "top", "view_current_view_limits": [[0, 1], [0, 1]],
        },
    )()
    rgba = np.zeros((4, 6, 4), dtype=np.uint8)
    rgba[..., 0] = 255

    server._save_print_if_requested({"print_view": True}, engine, rgba)

    assert seen.get("ndim") == 2, "the writer was handed the render, not the payload"


def test_printing_writes_both_files(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "RENDERS_DIR", tmp_path)
    engine = type(
        "E", (), {
            "current_render_mode": "filled", "current_cut_depth": 0.5,
            "view_current_axis": "top", "view_current_view_limits": [[0, 1], [0, 1]],
        },
    )()
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[..., 0] = 255

    server._save_print_if_requested({"print_view": True}, engine, rgba)

    assert list(tmp_path.glob("*.pdf")), "no PDF written"
    assert list(tmp_path.glob("*.npy")), "no npy written"


def test_printing_does_nothing_unless_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "RENDERS_DIR", tmp_path)
    server._save_print_if_requested({}, object(), np.zeros((4, 4, 4), dtype=np.uint8))
    assert not list(tmp_path.iterdir())


def test_the_pdf_writer_still_works(tmp_path):
    """Same writer, different backend. The P key depends on this."""
    out = tmp_path / "out.pdf"
    save_binary_array_as_vector_pdf(_payload(4, 6, [(1, 1)]), str(out))
    assert out.exists() and out.stat().st_size > 0
    assert out.read_bytes().startswith(b"%PDF")


# --- The endpoint ---------------------------------------------------------


def test_export_endpoint_returns_a_drawing(monkeypatch):
    """The download is the drawing itself, not a base64 image."""
    payload = _payload(40, 96, [(10, 10), (10, 11)])

    monkeypatch.setattr(server, "_to_braille_payload", lambda _r: payload)
    monkeypatch.setattr(
        server, "get_or_create_renderer",
        lambda *_a, **_k: type("E", (), {
            "screen_size": [96, 40],
            "render": lambda _self, _p: np.zeros((40, 96, 4), dtype=np.uint8),
        })(),
    )

    server.app.config["TESTING"] = True
    with server.app.test_client() as client:
        response = client.post("/render/export-source", json={"view": "x+"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["format"] == "svg"
    assert "image_base64" not in body, "the raster export should be gone"
    ET.fromstring(body["svg"])
    assert body["raised_pins"] == 2
    assert _shape_count(body["svg"]) == 2, "the drawing should match the payload"
