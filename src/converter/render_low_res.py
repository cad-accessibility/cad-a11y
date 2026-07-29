import io

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

# A downsampled render stores per-pixel ink coverage: 0 is fully covered, 255 is
# untouched. A pixel counts as raised when it is more than half covered. The
# braille payload and the outline detection below both key off this single value
# so the outline silhouette and the filled edge cannot drift apart.
RAISED_INK_THRESHOLD = 128

# Below a few percent coverage a pixel is antialiasing spill rather than a
# feature, so it is never worth rescuing. Without this floor an essentially
# white pixel survives and the hairline guard then grows it into a visible blob.
FAINT_INK_FLOOR = 240


def dilate_mask(mask):
    """One-pixel 4-connected dilation of a boolean mask."""
    grown = mask.copy()
    grown[1:, :] |= mask[:-1, :]
    grown[:-1, :] |= mask[1:, :]
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    return grown


def _neighbour_min(gray):
    """Heaviest ink (lowest gray value) among each pixel's four neighbours."""
    lowest = np.full_like(gray, 255)
    lowest[1:, :] = np.minimum(lowest[1:, :], gray[:-1, :])
    lowest[:-1, :] = np.minimum(lowest[:-1, :], gray[1:, :])
    lowest[:, 1:] = np.minimum(lowest[:, 1:], gray[:, :-1])
    lowest[:, :-1] = np.minimum(lowest[:, :-1], gray[:, 1:])
    return lowest


def raised_ink_mask(gray):
    """Which pixels of a downsampled render a tactile display should raise.

    Majority coverage wins, so a thin line straddling a pixel boundary raises
    one pin rather than doubling onto both. Majority alone would silently delete
    any feature thinner than half a pixel (a thin cross-section wall, a rib),
    and dilation could not recover it because nothing would be left to dilate,
    so ink that no majority pixel represents is rescued as well.

    That rescue is deliberately narrow. It ignores anything under FAINT_INK_FLOOR
    coverage, which is spill rather than geometry, and within a faint cluster it
    keeps only the heaviest pixel. Otherwise a thin feature would smear across
    its own antialiased fringe and land on three pins instead of one, which is
    the doubling the majority rule exists to prevent. The faint outer edge of a
    solid shape still drops out, because it always has a majority pixel adjacent.

    This is the single definition of "raised" for the whole pipeline: the
    braille payload and the outline detection both use it, so the outline
    silhouette and the filled edge cannot drift apart.
    """
    strong = gray < RAISED_INK_THRESHOLD
    orphan = (gray < FAINT_INK_FLOOR) & ~dilate_mask(strong)
    heaviest = gray <= _neighbour_min(gray)
    return strong | (orphan & heaviest)


# A raised pin is drawn one millimetre across by default. The file is vector, so
# this only decides the size it opens at; rescaling in a drawing program is the
# point of exporting vector in the first place.
DEFAULT_PIN_PITCH_MM = 1.0


def render_payload_as_vector(payload, target=None, *, fmt="svg", pin_pitch_mm=DEFAULT_PIN_PITCH_MM):
    """Trace a braille payload into a vector drawing, one square per raised pin.

    ``payload`` is the 2-D array the tactile display receives, where 255 means
    raised. Passing the RGBA render instead is the mistake that silently broke
    the print path: it is 3-D, so unpacking two dimensions raised and the route's
    catch-all swallowed it. The guard below turns that into a clear error.

    Squares rather than dots: adjacent raised pins share an edge and read as one
    solid region under a finger, where circles would read as texture. A lone pin
    is still a single mark.

    Nothing is thickened. One raised pin becomes one square, so the drawing is
    exactly what the display raises, and any line weight for a particular fuser
    or paper is a decision for whoever prints it.

    ``target`` is a path or file-like object; omitted, the drawing is returned as
    a string (or bytes for a binary format).
    """
    array = np.asarray(payload)
    if array.ndim != 2:
        raise ValueError(
            f"expected the 2-D braille payload, got an array with shape {array.shape}. "
            "An RGBA render needs converting to a payload first."
        )

    height, width = array.shape
    fig = plt.figure(figsize=(width * pin_pitch_mm / 25.4, height * pin_pitch_mm / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")

    ax.add_patch(Rectangle((0, 0), width, height, facecolor="white", edgecolor="none"))

    # Row 0 of the payload is the top of the display, but y grows upward here.
    for y, x in zip(*np.nonzero(array == 255)):
        ax.add_patch(
            Rectangle((x, height - y - 1), 1, 1, facecolor="black", edgecolor="none")
        )

    try:
        if target is None:
            buffer = io.BytesIO()
            fig.savefig(buffer, format=fmt, pad_inches=0)
            data = buffer.getvalue()
            return data.decode("utf-8") if fmt == "svg" else data
        fig.savefig(target, format=fmt, pad_inches=0)
        return None
    finally:
        plt.close(fig)


def save_binary_array_as_vector_pdf(array, filename="low_res.pdf"):
    """Backwards-compatible wrapper for the PDF the P key writes."""
    render_payload_as_vector(array, filename, fmt="pdf")

# vectorized version
def get_outlines(img_np):
    h, w = img_np.shape[:2]

    #white = np.all(img_np == [255, 255, 255, 255], axis=-1)
    #non_white = img_np[..., 0] < 255
    non_white = raised_ink_mask(img_np[..., 0])
    white = ~non_white
    white_padded = np.pad(white, 1, mode='constant', constant_values=False)

    # white mask neighbors
    neighbor_white = (
        white_padded[0:h,     0:w]   | 
        white_padded[0:h,     1:w+1] | 
        white_padded[0:h,     2:w+2] | 
        white_padded[1:h+1,   0:w]   | 
        white_padded[1:h+1,   2:w+2] | 
        white_padded[2:h+2,   0:w]   | 
        white_padded[2:h+2,   1:w+1] | 
        white_padded[2:h+2,   2:w+2]    
    )

    outline_mask = non_white & neighbor_white

    outline_pixels_rgba = np.empty((h, w, 4), dtype=np.uint8)
    outline_pixels_rgba[outline_mask] = [0, 0, 0, 255]
    outline_pixels_rgba[~outline_mask] = [255, 255, 255, 255]

    return outline_pixels_rgba, outline_mask

if __name__ == "__main__":
    print("test")