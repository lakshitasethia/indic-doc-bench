"""Placing a real photograph on a ladder defined in render DPI.

The ladder's rungs are nominal rasterisation DPI, which a photograph does not
have. These tests pin the one property that makes the comparison legitimate:
the measurement is pixels per inch of paper, so it moves exactly as the ladder's
DPI does when resolution changes, and it says nothing at all about anything
else.
"""
import cv2
import numpy as np
import pytest

from idb.capture import (MM_PER_INCH, PAPER_WIDTHS_MM, document_span,
                         effective_dpi, place_on_ladder)
from idb.degrade import PRESETS


def _page(width_px=600, height_px=1200, frame=(1000, 1600), ink_rows=14):
    """A dark 'document' of known pixel width, centred on a flat pale ground."""
    canvas = np.full((frame[1], frame[0], 3), 235, np.uint8)
    x0 = (frame[0] - width_px) // 2
    y0 = (frame[1] - height_px) // 2
    cv2.rectangle(canvas, (x0, y0), (x0 + width_px, y0 + height_px), (252, 252, 252), -1)
    step = height_px // (ink_rows + 1)
    for i in range(1, ink_rows + 1):
        y = y0 + i * step
        cv2.rectangle(canvas, (x0 + 20, y), (x0 + width_px - 20, y + step // 4),
                      (20, 20, 20), -1)
    return canvas, (x0, y0)


def test_measured_span_recovers_the_document_width_slightly_conservatively():
    """Trimming lands just inside the true edge, and that direction is chosen.

    An under-measured span under-states effective DPI, so the claim the ladder
    recalibration makes -- 'this capture is not as low-resolution as L3' --
    stays conservative rather than being flattered by the detector.
    """
    img, (x0, _) = _page(width_px=600)
    span = document_span(img)
    assert 0.90 * 600 <= span["width_px"] <= 600, span
    assert abs(span["x0"] - x0) < 0.08 * 600, span


def test_effective_dpi_is_pixels_per_inch_of_paper():
    """The definition, and the only reason it is comparable to a rung."""
    img, _ = _page(width_px=600)
    out = effective_dpi(img, paper_width_mm=80.0)
    expected = out["width_px"] / (80.0 / MM_PER_INCH)
    assert out["effective_dpi"] == pytest.approx(expected, abs=0.1)


def test_halving_resolution_halves_the_measured_dpi():
    """The property the ladder rests on: same paper, half the pixels, half the DPI.

    If this did not hold, comparing a photograph's number to a rung's number
    would be comparing two different quantities that happen to share a unit.
    """
    img, _ = _page(width_px=600)
    small = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    full = effective_dpi(img, 80.0)["effective_dpi"]
    half = effective_dpi(small, 80.0)["effective_dpi"]
    assert half == pytest.approx(full / 2, rel=0.08)


def test_a_narrower_assumed_paper_width_raises_the_dpi_proportionally():
    """The physical width is an assumption, and it is doing real work."""
    img, _ = _page(width_px=600)
    wide = effective_dpi(img, PAPER_WIDTHS_MM["thermal80"])
    narrow = effective_dpi(img, PAPER_WIDTHS_MM["thermal58"])
    assert narrow["effective_dpi"] > wide["effective_dpi"]
    assert narrow["effective_dpi"] == pytest.approx(
        wide["effective_dpi"] * 80.0 / 58.0, rel=0.01)
    # Both anchors are reported so the assumption cannot hide.
    assert wide["if_58mm_roll"] == pytest.approx(narrow["effective_dpi"], rel=0.01)


def test_fingers_and_floor_pattern_do_not_inflate_the_span():
    """Untrimmed, a speck of background detail inflated both real photographs.

    A hand holding the paper and a patterned floor both deposit detail outside
    the document. Measured to the outermost detail pixel, the two photographs
    in Layer 3 came out about 8% wider than the paper, and effective DPI is
    linear in that error.
    """
    img, (x0, y0) = _page(width_px=600)
    cv2.circle(img, (30, 40), 12, (0, 0, 0), -1)              # a fingertip
    cv2.circle(img, (img.shape[1] - 30, img.shape[0] - 40), 12, (0, 0, 0), -1)
    trimmed = document_span(img)
    untrimmed = document_span(img, trim=0.0)
    assert 0.90 * 600 <= trimmed["width_px"] <= 600, trimmed
    assert untrimmed["width_px"] > trimmed["width_px"] + 100


def test_ladder_placement_is_a_range_between_real_rungs():
    for level, preset in PRESETS.items():
        assert str(preset["dpi"]) in place_on_ladder(preset["dpi"] * 1.5) \
            or preset["dpi"] == max(p["dpi"] for p in PRESETS.values())
    below = place_on_ladder(min(p["dpi"] for p in PRESETS.values()) - 1)
    assert below.startswith("below")
    above = place_on_ladder(max(p["dpi"] for p in PRESETS.values()) + 1)
    assert above.startswith("at or above")


def test_a_capture_at_l3_resolution_is_not_reported_above_l3():
    """The claim the recalibration actually makes is one-directional."""
    l3 = PRESETS["L3_harsh"]["dpi"]
    assert "L3_harsh" in place_on_ladder(l3 + 1)
    assert place_on_ladder(l3 - 1).startswith("below L3_harsh")


def test_paper_width_must_be_positive():
    img, _ = _page()
    with pytest.raises(ValueError):
        effective_dpi(img, paper_width_mm=0)


def test_a_blank_frame_does_not_divide_by_zero():
    blank = np.full((400, 300, 3), 255, np.uint8)
    out = effective_dpi(blank, 80.0)
    assert out["effective_dpi"] >= 0
