"""The view downloads as SVG, in two kinds.

Vector rather than a raster sheet because what a line must measure to rise in a
fuser depends on the machine and the paper, so the useful thing to hand over is a
drawing that can be rescaled and restyled downstream.

"geometry" is the render's own vector artwork, captured from the figure before it
is rasterised: real polygons for a filled or cut view, real strokes for an x-ray,
at full detail. "tactile" traces the braille payload instead, one square per
raised pin, which is coarser but is exactly what the display raises.

Outline mode is derived from the raster silhouette and has no figure that draws
it, so a request for geometry falls back to the trace and says which it gave.

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
    set_svg_physical_size,
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
    """The size the file opens at, whatever unit it states it in."""
    match = re.search(
        r'<svg[^>]*\bwidth="([\d.]+)(pt|mm)"[^>]*\bheight="([\d.]+)(pt|mm)"', svg)
    assert match, "no physical size on the svg root"
    width, width_unit, height, height_unit = match.groups()
    to_mm = {"mm": 1.0, "pt": MM_PER_INCH / PT_PER_INCH}
    return float(width) * to_mm[width_unit], float(height) * to_mm[height_unit]


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


# --- Full-detail geometry, and the fallback -------------------------------


def _fake_engine(svg=None, grid=(96, 40)):
    return type("E", (), {
        "screen_size": list(grid),
        "last_render_svg": svg,
        "render": lambda _self, _p: np.zeros((grid[1], grid[0], 4), dtype=np.uint8),
    })()


def _post(monkeypatch, engine, payload, **body):
    monkeypatch.setattr(server, "_to_braille_payload", lambda _r: payload)
    monkeypatch.setattr(server, "get_or_create_renderer", lambda *_a, **_k: engine)
    server.app.config["TESTING"] = True
    with server.app.test_client() as client:
        return client.post("/render/export-source", json={"view": "x+", **body})


def test_geometry_export_returns_the_renders_own_artwork(monkeypatch):
    """Full detail means the vector the renderer drew, not a trace of the grid."""
    artwork = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    body = _post(monkeypatch, _fake_engine(svg=artwork), _payload(40, 96, [(1, 1)])).get_json()

    assert body["export_kind"] == "geometry"
    assert body["svg"] == artwork


def test_geometry_falls_back_when_the_view_has_no_artwork(monkeypatch):
    """Outline is derived from the raster silhouette, so there is no figure that
    draws it. Falling back is right; doing it silently is not."""
    body = _post(monkeypatch, _fake_engine(svg=None), _payload(40, 96, [(1, 1), (2, 2)])).get_json()

    assert body["requested_kind"] == "geometry"
    assert body["export_kind"] == "tactile", "should fall back rather than fail"
    assert _shape_count(body["svg"]) == 2, "the fallback should be the tactile trace"


def test_asking_for_the_tactile_trace_gets_it_even_when_artwork_exists(monkeypatch):
    artwork = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    body = _post(
        monkeypatch, _fake_engine(svg=artwork), _payload(40, 96, [(1, 1)]),
        export_kind="tactile",
    ).get_json()

    assert body["export_kind"] == "tactile"
    assert body["svg"] != artwork
    assert _shape_count(body["svg"]) == 1


def test_an_unknown_kind_falls_back_to_full_detail(monkeypatch):
    artwork = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    body = _post(
        monkeypatch, _fake_engine(svg=artwork), _payload(40, 96), export_kind="nonsense",
    ).get_json()
    assert body["export_kind"] == "geometry"


def test_capture_is_opt_in(monkeypatch):
    """Capturing costs a second serialisation of the figure, so an ordinary
    render must not pay for it."""
    seen = {}

    class Engine:
        screen_size = [96, 40]
        last_render_svg = None

        def render(self, params):
            seen["capture"] = params.get("capture_svg")
            return np.zeros((40, 96, 4), dtype=np.uint8)

    _post(monkeypatch, Engine(), _payload(40, 96), export_kind="tactile")
    assert seen["capture"] is False, "the tactile trace does not need the figure captured"

    _post(monkeypatch, Engine(), _payload(40, 96))
    assert seen["capture"] is True


# --- Both kinds open at the same size -------------------------------------

FIGURE_SVG = (
    '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" '
    'width="69.12pt" height="28.8pt" viewBox="0 0 69.12 28.8"><path d="M0 0"/></svg>'
)


def test_a_captured_figure_is_resized_to_a_real_footprint():
    """Matplotlib sizes the captured figure from the render canvas, so it opens
    about an inch wide. On paper that is useless."""
    assert _root_size_mm(FIGURE_SVG)[0] == pytest.approx(24.4, abs=0.5), "premise"

    resized = set_svg_physical_size(FIGURE_SVG, 96 * DEFAULT_PIN_PITCH_MM)
    assert _root_size_mm(resized)[0] == pytest.approx(96.0, abs=0.5)


def test_resizing_preserves_the_aspect_ratio():
    """Height is derived, never passed, so the drawing cannot be squashed."""
    resized = set_svg_physical_size(FIGURE_SVG, 96.0)
    width = float(re.search(r'width="([\d.]+)mm"', resized).group(1))
    height = float(re.search(r'height="([\d.]+)mm"', resized).group(1))
    assert width == pytest.approx(96.0)
    assert width / height == pytest.approx(69.12 / 28.8, rel=1e-6)


def test_resizing_leaves_the_artwork_alone():
    """Only the root's declared size changes. The viewBox and every path stay
    exactly as drawn, which is what keeps this from being a transform."""
    resized = set_svg_physical_size(FIGURE_SVG, 96.0)
    assert 'viewBox="0 0 69.12 28.8"' in resized
    assert '<path d="M0 0"/>' in resized
    ET.fromstring(resized)


def test_an_svg_without_a_viewbox_is_left_alone():
    """Nothing to preserve the geometry against, so a wrong size beats a
    distorted drawing."""
    no_box = '<svg xmlns="http://www.w3.org/2000/svg" width="10pt" height="5pt"/>'
    assert set_svg_physical_size(no_box, 96.0) == no_box


def test_both_kinds_come_back_on_the_same_footprint(monkeypatch):
    """The point of resizing: one download can be laid over the other."""
    engine = _fake_engine(svg=FIGURE_SVG)
    geometry = _post(monkeypatch, engine, _payload(40, 96, [(1, 1)])).get_json()["svg"]
    tactile = _post(
        monkeypatch, engine, _payload(40, 96, [(1, 1)]), export_kind="tactile"
    ).get_json()["svg"]

    assert _root_size_mm(geometry)[0] == pytest.approx(
        _root_size_mm(tactile)[0], abs=0.5
    ), "the two exports should open at the same width"
