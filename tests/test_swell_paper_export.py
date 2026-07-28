"""Exported sheets must survive a swell paper fuser; the preview must not change.

Swell paper rises where the sheet is black, and how high a line rises depends on
how wide it is drawn. The tactile render is built for a 96x40 pin grid where a
feature is one pixel, so exporting it unchanged gives lines too fine to feel
(#49). The export is thickened to a physical width; the on-screen preview is
left alone, because it is judged by eye at screen scale.

The trade-off worth watching is separation. Capsule paper needs roughly 1/4 inch
between components, so a wider line leaves less gap before neighbouring features
merge into one. test_merge_distance_by_line_width measures where that happens
rather than assuming it does not.
"""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from app.server import (
    MM_PER_INCH,
    SWELL_EXPORT_WIDTH_PX,
    SWELL_PRINT_WIDTH_MM,
    SWELL_TARGET_LINE_MM,
    _img_to_base64_png,
    _thicken_for_swell_paper,
)

EXPORT_WIDTH = SWELL_EXPORT_WIDTH_PX

# US Letter, landscape.
LETTER_LONG_MM = 279.4
LETTER_SHORT_MM = 215.9


def _thicken(mask, line_mm=SWELL_TARGET_LINE_MM, width_px=EXPORT_WIDTH):
    return _thicken_for_swell_paper(
        mask,
        export_width_px=width_px,
        print_width_mm=SWELL_PRINT_WIDTH_MM,
        target_line_mm=line_mm,
    )


def _hairline(height=60, width=EXPORT_WIDTH, row=30):
    mask = np.zeros((height, width), dtype=bool)
    mask[row, :] = True
    return mask


def _run_lengths(column):
    """Lengths of consecutive raised runs down a single column."""
    runs, current = [], 0
    for value in column:
        if value:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


# --- The physical width actually produced ---------------------------------


def test_a_hairline_reaches_the_target_width():
    """One pixel at export scale is about a quarter of a millimetre, which is
    below what swell paper can raise usefully."""
    mask, info = _thicken(_hairline())
    thickness = _run_lengths(mask[:, EXPORT_WIDTH // 2])
    assert len(thickness) == 1
    achieved_mm = thickness[0] / info["pixels_per_mm"]
    assert achieved_mm == pytest.approx(info["achieved_line_mm"], abs=0.01)
    assert achieved_mm >= 1.0, f"{achieved_mm:.2f}mm will barely rise in a fuser"


def test_reported_width_matches_what_was_drawn():
    """The report is what a user checks against their machine, so it must
    describe the sheet rather than the request."""
    mask, info = _thicken(_hairline(), line_mm=2.0)
    measured = _run_lengths(mask[:, 500])[0]
    assert measured == 1 + 2 * info["dilation_passes"]
    assert info["achieved_line_mm"] == pytest.approx(measured / info["pixels_per_mm"], abs=0.01)


def test_dpi_matches_the_stated_sheet_width():
    _, info = _thicken(_hairline())
    assert info["dpi"] == pytest.approx(info["pixels_per_mm"] * MM_PER_INCH, abs=0.1)
    assert EXPORT_WIDTH / info["dpi"] * MM_PER_INCH == pytest.approx(SWELL_PRINT_WIDTH_MM, abs=0.5)


@pytest.mark.parametrize("line_mm", [0.5, 1.0, 2.0, 3.0])
def test_wider_requests_produce_wider_lines(line_mm):
    mask, info = _thicken(_hairline(), line_mm=line_mm)
    measured_mm = _run_lengths(mask[:, 500])[0] / info["pixels_per_mm"]
    # Within one pixel, since growing happens a whole pixel at a time.
    assert abs(measured_mm - line_mm) <= 1.0 / info["pixels_per_mm"] + 1e-6


def test_a_smaller_sheet_needs_fewer_pixels_for_the_same_width():
    """The same millimetre target on a narrower sheet is fewer pixels. Getting
    this backwards would silently produce sheets at the wrong scale."""
    _, wide = _thicken_for_swell_paper(
        _hairline(), export_width_px=1000, print_width_mm=257.0, target_line_mm=2.0
    )
    _, narrow = _thicken_for_swell_paper(
        _hairline(), export_width_px=1000, print_width_mm=514.0, target_line_mm=2.0
    )
    assert narrow["dilation_passes"] < wide["dilation_passes"]


# --- Separation, which is what thickening costs ---------------------------


@pytest.mark.parametrize("line_mm", [1.0, 2.0, 3.0])
def test_merge_distance_by_line_width(line_mm):
    """How close can two features be before thickening fuses them?

    Capsule paper wants roughly 6 mm between components, so merging below that
    is not itself a failure. What must hold is that the gap lost is explained by
    the width added, not more.
    """
    _, info = _thicken(_hairline(), line_mm=line_mm)
    grown_each_side = info["dilation_passes"]

    merged_at = None
    for gap_px in range(1, 40):
        mask = np.zeros((80, EXPORT_WIDTH), dtype=bool)
        mask[30, :] = True
        mask[30 + gap_px, :] = True
        thick, _ = _thicken(mask, line_mm=line_mm)
        if len(_run_lengths(thick[:, 500])) == 1:
            merged_at = gap_px

    # Each line grows by `grown_each_side` pixels towards the other, and two runs
    # read as one as soon as they are adjacent rather than only when they
    # overlap, hence the extra pixel. Anything beyond this would mean thickening
    # is bleeding further than the width asked for.
    assert merged_at == 2 * grown_each_side + 1, (
        f"at {line_mm}mm, lines merge up to {merged_at}px apart but only "
        f"{2 * grown_each_side + 1}px of closure was added"
    )

    # The number that matters in practice: capsule paper wants about 6mm between
    # components, so merging must stay well inside that or the thickening is
    # destroying separation the reader depends on.
    merge_mm = merged_at / info["pixels_per_mm"]
    assert merge_mm < 6.0, (
        f"at {line_mm}mm lines, features closer than {merge_mm:.1f}mm fuse, which "
        "is inside the separation capsule paper needs"
    )


def test_thickening_never_removes_raised_content():
    """Growing must only ever add. Losing a feature would be worse than a thin one."""
    rng = np.random.default_rng(0)
    mask = rng.random((60, 200)) > 0.9
    thick, _ = _thicken(mask, width_px=200)
    assert np.all(thick[mask]), "a raised pixel was lost"


def test_empty_render_stays_empty():
    mask, _ = _thicken(np.zeros((40, EXPORT_WIDTH), dtype=bool))
    assert not mask.any()


# --- The preview must be untouched ----------------------------------------


def test_preview_pipeline_does_not_thicken():
    """#49 asks for the preview to keep looking the same. If the tactile payload
    itself were thickened, every display and preview would change too."""
    import inspect

    from app.server import _to_braille_payload

    source = inspect.getsource(_to_braille_payload)
    assert "_thicken_for_swell_paper" not in source, (
        "thickening leaked into the shared payload path; it belongs to the export only"
    )


# --- The file that actually gets saved ------------------------------------


def test_png_carries_the_dpi_so_it_prints_at_the_right_size():
    """Without this the sheet prints at whatever scale the print dialog picks,
    and the millimetre width the rest of this file is about becomes meaningless."""
    mask, info = _thicken(_hairline())
    encoded = _img_to_base64_png(np.where(mask, 255, 0).astype(np.uint8), dpi=info["dpi"])
    image = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert image.info.get("dpi") is not None
    assert image.info["dpi"][0] == pytest.approx(info["dpi"], abs=0.5)


def test_png_without_dpi_still_saves():
    encoded = _img_to_base64_png(np.zeros((10, 10), dtype=np.uint8))
    assert Image.open(io.BytesIO(base64.b64decode(encoded))).size == (10, 10)


# --- Which way round the ink goes -----------------------------------------


def test_raised_areas_are_black_on_white():
    """Swell paper expands where the sheet is black, so the model must be the
    black part. The payload uses the opposite convention, 255 for a raised pin,
    which is right for a display and exactly wrong on paper: written straight out
    it would raise the whole background and leave the model flat.
    """
    from app.server import _render_export_sheet

    mask = np.zeros((40, EXPORT_WIDTH), dtype=bool)
    mask[20, 100:200] = True
    sheet = _render_export_sheet(mask)

    assert sheet[20, 150] == 0, "the model should be black so it rises"
    assert sheet[0, 0] == 255, "the background should be white so it stays flat"
    assert (sheet == 255).sum() > (sheet == 0).sum(), (
        "most of a sheet is background; if black dominates, the polarity is inverted "
        "and the whole page would swell"
    )


def test_export_matches_the_preview_convention():
    """The preview already draws raised content black on white. The exported sheet
    should look like what was on screen, not its negative."""
    from app.server import _render_export_sheet

    payload = np.zeros((20, 40), dtype=np.uint8)
    payload[10, 10:20] = 255  # payload convention: 255 is raised

    preview = np.where(payload > 0, 0, 255).astype(np.uint8)  # as the preview builds it
    exported = _render_export_sheet(payload > 0)

    assert np.array_equal(exported, preview)


# --- Fitting a sheet of paper ---------------------------------------------


def test_sheet_fits_a_letter_page():
    _, info = _thicken(_hairline())
    width_mm = info["print_width_mm"]
    height_mm = width_mm * (40 / 96)  # the tactile aspect

    assert width_mm <= LETTER_LONG_MM, "wider than a Letter sheet in landscape"
    assert height_mm <= LETTER_SHORT_MM, "taller than a Letter sheet in landscape"
    margin = LETTER_LONG_MM - width_mm
    assert margin >= 20, f"only {margin:.0f}mm of margin across the whole sheet"


def test_export_resolution_extracts_what_the_renderer_draws():
    """The renderer draws onto a capped canvas and downsamples. Exporting below
    that cap throws away detail already paid for; above it invents none."""
    from src.converter.single_view_stl import MAX_CANVAS_PX

    assert SWELL_EXPORT_WIDTH_PX == MAX_CANVAS_PX, (
        "the export size should track the canvas cap it is extracting from"
    )


def test_export_resolution_is_a_sensible_print_dpi():
    dpi = SWELL_EXPORT_WIDTH_PX / SWELL_PRINT_WIDTH_MM * MM_PER_INCH
    assert 300 <= dpi <= 400, f"{dpi:.0f} dpi is outside the range worth printing at"
