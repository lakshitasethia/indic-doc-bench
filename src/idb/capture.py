"""Where a real capture actually sits on the severity ladder.

The ladder in `degrade.py` is defined in *nominal render DPI* -- the resolution
a synthetic page is rasterised at before the recipe is applied. That number is
exact for the synthetic corpus and meaningless for a photograph, which has no
render step. Yet the two get compared constantly ("is L3 what a phone upload
looks like?"), and until there was a way to measure a photograph the comparison
was made by eye.

The measurement here is deliberately the crudest one that answers the question:

    effective DPI = pixels across the document / physical width in inches

That is exactly what the ladder's DPI means -- pixels per inch of original
paper -- so the two are directly comparable. The physical width has to come
from the caller, because no pixel measurement can recover it: an 80mm thermal
roll and a 58mm one photographed to fill the same frame are the same image.
Standard Indian restaurant rolls are 80mm and 58mm; A4 is 210mm.

**Effective DPI is an upper bound on quality, not a measure of it.** A
photograph at 400 effective DPI still carries motion blur, focus falloff,
perspective, folds and uneven light, none of which resolution captures. What it
does establish is a floor: a capture measured at 174 DPI cannot be *as bad as*
a 72 DPI render, whatever else is wrong with it. That one-directional claim is
what the ladder recalibration needs (METHODOLOGY 8c.4).

The detector finds the span of the high-detail region, which is the paper when
the background contrasts with it (a receipt on a dark floor) and the ink block
when it does not (a receipt on a white wall). The caller states which one they
are anchoring the physical width to; for the two photographs in this corpus the
span locks onto the paper edge.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict

import cv2
import numpy as np

from .degrade import PRESETS

MM_PER_INCH = 25.4

# A pixel belongs to the document if the local standard deviation around it is
# high. Print and paper edges both clear this; flat wall, flat floor and flat
# paper do not.
DETAIL_WINDOW = 9
DETAIL_THRESHOLD = 18.0

# Drop the outermost 1% of detail mass from each end before measuring. Fingers
# holding the paper, a patterned floor and JPEG mosquito noise all deposit a
# little detail well outside the document; untrimmed they inflated the measured
# span of the two photographs here by about 8%.
#
# Trimming lands a few percent *inside* the true paper edge, and that direction
# is deliberate: an under-measured span under-states effective DPI, which keeps
# the one-directional claim ("this capture is not as low-resolution as L3")
# conservative rather than flattered by the detector.
TRIM = 0.01

# Standard media, for the caller's convenience.
PAPER_WIDTHS_MM = {
    "thermal80": 80.0,   # the common 3-inch restaurant roll
    "thermal58": 58.0,   # the 2-inch roll, on smaller handheld printers
    "a4": 210.0,
    "a5": 148.0,
    "letter": 215.9,
}


def detail_map(img: np.ndarray) -> np.ndarray:
    """Local standard deviation, the cheap stand-in for 'there is something here'."""
    grey = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grey = grey.astype(np.float32)
    mean = cv2.blur(grey, (DETAIL_WINDOW, DETAIL_WINDOW))
    meansq = cv2.blur(grey * grey, (DETAIL_WINDOW, DETAIL_WINDOW))
    return np.sqrt(np.maximum(meansq - mean * mean, 0.0))


def _trimmed_span(profile: np.ndarray, trim: float) -> tuple:
    total = profile.sum()
    if total <= 0:
        return 0, int(profile.size - 1)
    cum = np.cumsum(profile) / total
    lo = int(np.searchsorted(cum, trim))
    hi = int(np.searchsorted(cum, 1.0 - trim))
    return lo, max(hi, lo + 1)


def document_span(img: np.ndarray, trim: float = TRIM) -> Dict[str, int]:
    """Pixel extent of the document in the frame."""
    detail = (detail_map(img) > DETAIL_THRESHOLD).astype(np.float32)
    x0, x1 = _trimmed_span(detail.mean(axis=0), trim)
    y0, y1 = _trimmed_span(detail.mean(axis=1), trim)
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "width_px": x1 - x0, "height_px": y1 - y0,
            "frame_width_px": int(img.shape[1]),
            "frame_height_px": int(img.shape[0])}


def place_on_ladder(dpi: float) -> str:
    """Name the rungs a measured capture falls between.

    Phrased as a range rather than a nearest rung on purpose: 'between L2 and
    L1' is a claim the measurement supports, 'is an L2 capture' is not.
    """
    rungs = sorted(((v["dpi"], k) for k, v in PRESETS.items()))
    lowest_dpi, lowest = rungs[0]
    highest_dpi = rungs[-1][0]
    if dpi < lowest_dpi:
        return "below %s (%d DPI)" % (lowest, lowest_dpi)
    if dpi >= highest_dpi:
        names = "/".join(n for d, n in rungs if d == highest_dpi)
        return "at or above %s (%d DPI)" % (names, highest_dpi)
    below = max(d for d, _ in rungs if d <= dpi)
    above = min(d for d, _ in rungs if d > dpi)
    lo = "/".join(n for d, n in rungs if d == below)
    hi = "/".join(n for d, n in rungs if d == above)
    return "between %s (%d DPI) and %s (%d DPI)" % (lo, below, hi, above)


def effective_dpi(img: np.ndarray, paper_width_mm: float,
                  trim: float = TRIM) -> Dict[str, Any]:
    """Measure a capture's resolution over the paper, in the ladder's units."""
    if paper_width_mm <= 0:
        raise ValueError("paper_width_mm must be positive")
    span = document_span(img, trim=trim)
    inches = paper_width_mm / MM_PER_INCH
    dpi = span["width_px"] / inches
    out: Dict[str, Any] = dict(span)
    out.update({
        "paper_width_mm": paper_width_mm,
        "effective_dpi": round(dpi, 1),
        "ladder": place_on_ladder(dpi),
        # DPI scales inversely with the assumed width, so one alternative
        # anchor is enough to show how much the assumption is carrying.
        "if_58mm_roll": round(span["width_px"] / (58.0 / MM_PER_INCH), 1),
        "if_80mm_roll": round(span["width_px"] / (80.0 / MM_PER_INCH), 1),
    })
    return out


def measure_file(path: pathlib.Path, paper_width_mm: float,
                 trim: float = TRIM) -> Dict[str, Any]:
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit("cannot read image: %s" % path)
    out = effective_dpi(img, paper_width_mm, trim=trim)
    out["path"] = str(path)
    return out


def format_report(rows, paper_width_mm: float) -> str:
    lines = ["Effective resolution over the paper, assuming %.0fmm media:"
             % paper_width_mm, ""]
    for r in rows:
        lines.append("  %s" % r["path"])
        lines.append("    frame %dx%d px, document spans %d px across"
                     % (r["frame_width_px"], r["frame_height_px"], r["width_px"]))
        lines.append("    effective DPI %.0f  ->  %s" % (r["effective_dpi"], r["ladder"]))
        lines.append("    sensitivity: %.0f DPI if an 80mm roll, %.0f if a 58mm roll"
                     % (r["if_80mm_roll"], r["if_58mm_roll"]))
        lines.append("")
    lines.append("Resolution is an upper bound on quality: blur, focus, perspective")
    lines.append("and lighting are not measured here. A capture measured above a rung")
    lines.append("is not necessarily easier to read than that rung -- but it is not")
    lines.append("harder for the one reason the ladder's DPI describes.")
    return "\n".join(lines)
