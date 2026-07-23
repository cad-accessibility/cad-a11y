"""Tests for how a downsampled render becomes raised pins.

The render is area-averaged down to display resolution, so each pixel value is
an ink-coverage fraction rather than a yes/no. Three behaviors matter and are
easy to regress into each other:

  * majority coverage wins, so a line straddling a pixel boundary raises one pin
    instead of doubling onto both;
  * a feature too thin to clear 50% anywhere is still kept, rather than silently
    disappearing off the display;
  * a frame with nothing two pixels thick is thickened so it stays feelable.
"""

import numpy as np

import app.server as server
from src.converter.render_low_res import get_outlines, raised_ink_mask


def _render(coverage_rows):
    """Build an RGBA render from 2-D gray values (0 = full ink, 255 = blank)."""
    gray = np.array(coverage_rows, dtype=np.uint8)
    rgba = np.zeros(gray.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        rgba[:, :, channel] = gray
    rgba[:, :, 3] = 255
    return rgba


def test_line_straddling_a_boundary_raises_one_pin_not_two():
    """60/40 split across two pixels raises only the majority side."""
    payload = server._to_braille_payload(_render([
        [255, 102, 153, 255],
        [255, 102, 153, 255],
        [255, 102, 153, 255],
    ]))

    assert payload[:, 1].tolist() == [255, 255, 255], "majority side must be raised"
    assert payload[:, 2].tolist() == [0, 0, 0], "faint side must not double the line"


def test_faint_edge_of_a_solid_shape_stays_down():
    """A solid region's partially covered outer edge is dropped, keeping edges tight."""
    payload = server._to_braille_payload(_render([
        [0, 0, 200, 255],
        [0, 0, 200, 255],
    ]))

    assert payload[:, 0].tolist() == [255, 255]
    assert payload[:, 1].tolist() == [255, 255]
    assert payload[:, 2].tolist() == [0, 0], "faint edge beside solid ink must stay down"


def test_subpixel_feature_is_not_silently_lost():
    """A wall too thin to clear 50% anywhere still reaches the display.

    A bare majority rule would drop this entirely, and dilation could not bring
    it back because nothing would be left to dilate.
    """
    payload = server._to_braille_payload(_render([
        [255, 200, 255, 255],
        [255, 200, 255, 255],
        [255, 200, 255, 255],
    ]))

    assert payload[:, 1].any(), "sub-pixel feature must not vanish"


def test_hairline_frame_is_thickened():
    """Nothing two pixels thick anywhere, so the content is grown to stay feelable."""
    payload = server._to_braille_payload(_render([
        [255, 255, 255, 255],
        [255, 0, 255, 255],
        [255, 255, 255, 255],
    ]))

    assert int(np.count_nonzero(payload)) > 1


def test_frame_with_thick_content_is_left_alone():
    """Crisp detail beside a thick region is not re-thickened."""
    payload = server._to_braille_payload(_render([
        [0, 0, 255, 255],
        [0, 0, 255, 255],
        [255, 255, 255, 255],
    ]))

    assert payload[0, 0] == 255 and payload[1, 1] == 255
    assert payload[2, 3] == 0, "empty area must stay empty"


def test_blank_render_stays_blank():
    payload = server._to_braille_payload(_render([[255, 255], [255, 255]]))

    assert not payload.any()


def test_rescued_feature_is_one_pin_wide():
    """A faint feature with no majority pixel lands on a single pin.

    Rescuing its antialiased fringe as well would spread it over three, which is
    exactly the doubling the majority rule exists to prevent.
    """
    payload = server._to_braille_payload(_render([
        [255, 210, 140, 200, 255],
        [255, 210, 140, 200, 255],
        [255, 210, 140, 200, 255],
        [255, 210, 140, 200, 255],
    ]))

    assert payload[0].tolist() == [0, 0, 255, 0, 0]


def test_near_white_speck_is_not_raised():
    """Antialiasing spill is not a feature and must not reach the display.

    With no coverage floor the speck survives the rescue and the hairline guard
    then grows it into a multi-pin blob.
    """
    payload = server._to_braille_payload(_render([
        [255, 255, 255],
        [255, 254, 255],
        [255, 255, 255],
    ]))

    assert not payload.any()


def test_outline_never_sits_outside_what_the_display_raises():
    """Outline detection and the payload share one boundary definition, so the
    silhouette cannot land on pixels the filled render leaves down."""
    gray = np.array([
        [255, 255, 255, 255, 255],
        [255, 0, 0, 200, 255],
        [255, 0, 0, 200, 255],
        [255, 255, 255, 255, 255],
    ], dtype=np.uint8)
    image = np.zeros(gray.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        image[:, :, channel] = gray
    image[:, :, 3] = 255

    _, outline_mask = get_outlines(image)

    assert not (outline_mask & ~raised_ink_mask(gray)).any(), "outline must stay within raised ink"
