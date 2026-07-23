import numpy as np

import app.cad_comparison_lib as cad_lib


BASE_RGB = [128, 128, 128]
BLACK = [0, 0, 0]
WHITE = [255, 255, 255]


def _make_renderer(monkeypatch, width=30, height=20):
    """Create a renderer without loading model files."""
    renderer = cad_lib.CADComparisonRenderer.__new__(cad_lib.CADComparisonRenderer)

    renderer.screen_size = [width, height]
    renderer.shapes = [object(), object()]
    renderer.bbox = None
    renderer.view_limits = [
        [[0, 10], [0, 10]],
        [[0, 10], [0, 10]],
        [[0, 10], [0, 10]],
        [[0, 10], [0, 10]],
        [[0, 10], [0, 10]],
        [[0, 10], [0, 10]],
    ]
    renderer.view_current_camera_center = [[5, 5] for _ in range(6)]

    def fake_get_single_view(*args, screen_size=None, **kwargs):
        w, h = screen_size
        image = np.full((h, w, 4), 128, dtype=np.uint8)
        image[:, :, 3] = 255
        return image, None

    monkeypatch.setattr(cad_lib, "get_single_view", fake_get_single_view)
    return renderer


def _render_with_cursor(monkeypatch, cursor_state, cursor_col=10, cursor_row=8):
    renderer = _make_renderer(monkeypatch)

    params = {
        "view": "x+",
        "depth": 50,
        "zoom": 0,
        "renderMode": "Outline",
        "mode": "single",
        "shape": "after",
        "compose_cursor": True,
        "cursor_state": cursor_state,
        "cursor_col": cursor_col,
        "cursor_row": cursor_row,
    }

    return renderer.render(params)


def test_cursor_none_leaves_image_unchanged(monkeypatch):
    image = _render_with_cursor(monkeypatch, "none")

    assert np.all(image[:, :, :3] == BASE_RGB)


def test_crosshair_draws_local_black_center(monkeypatch):
    image = _render_with_cursor(monkeypatch, "crosshair", cursor_col=10, cursor_row=8)

    assert image[8, 10, :3].tolist() == BLACK


def test_crosshair_draws_white_clearance_without_touching_far_pixels(monkeypatch):
    image = _render_with_cursor(monkeypatch, "crosshair", cursor_col=10, cursor_row=8)

    assert image[6, 8, :3].tolist() == WHITE
    assert image[0, 0, :3].tolist() == BASE_RGB


def test_guidelines_draw_horizontal_and_vertical_lines(monkeypatch):
    image = _render_with_cursor(monkeypatch, "guidelines", cursor_col=10, cursor_row=8)

    assert image[8, 0, :3].tolist() == BLACK
    assert image[0, 10, :3].tolist() == BLACK


def test_guidelines_touch_far_edges_unlike_crosshair(monkeypatch):
    image = _render_with_cursor(monkeypatch, "guidelines", cursor_col=10, cursor_row=8)

    assert image[8, 29, :3].tolist() == BLACK
    assert image[19, 10, :3].tolist() == BLACK


def test_horizontal_line_only_draws_horizontal_axis(monkeypatch):
    image = _render_with_cursor(monkeypatch, "horizontal-line", cursor_col=10, cursor_row=8)

    assert image[8, 0, :3].tolist() == BLACK
    assert image[0, 10, :3].tolist() == BASE_RGB


def test_vertical_line_only_draws_vertical_axis(monkeypatch):
    image = _render_with_cursor(monkeypatch, "vertical-line", cursor_col=10, cursor_row=8)

    assert image[0, 10, :3].tolist() == BLACK
    assert image[8, 0, :3].tolist() == BASE_RGB


def test_cursor_position_is_clamped_to_display_bounds(monkeypatch):
    image = _render_with_cursor(monkeypatch, "crosshair", cursor_col=999, cursor_row=999)

    assert image[19, 29, :3].tolist() == BLACK
    assert image[18, 28, :3].tolist() == WHITE