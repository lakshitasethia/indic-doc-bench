"""Degradation pipeline: turn a clean render into something a real user uploads.

Nobody in production uploads a 300-dpi PDF. They photograph a printed bill on a
desk under a ceiling fan. Accuracy on clean documents is therefore the least
interesting number in the benchmark; accuracy *as a function of degradation* is
the interesting one, because models that tie when the input is clean separate
sharply when it is not.

Three severity levels, each a fixed recipe rather than a random draw, so the
same document at L1/L2/L3 differs only in degradation. That paired structure is
what licenses per-document degradation deltas and a paired significance test --
an unpaired design would need far more documents for the same power.

Every operation records its parameters in the returned manifest, so a
degradation curve can be regressed against the individual factors afterwards
(e.g. "is it the DPI or the perspective warp that actually hurts?").
"""
from __future__ import annotations

import io
import pathlib
import random
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

LEVELS = ["L0_clean", "L1_scan", "L2_photo", "L3_harsh"]

# Fixed recipes. Ranges are sampled per document from a seeded RNG so documents
# vary, but the *distribution* at each level is pinned and published.
PRESETS: Dict[str, Dict[str, Any]] = {
    "L0_clean": dict(dpi=300, rotate=(0.0, 0.0), perspective=0.0, jpeg=None,
                     shadow=0.0, gauss=0.0, sp=0.0, blur=0),
    "L1_scan":  dict(dpi=300, rotate=(-1.2, 1.2), perspective=0.002, jpeg=88,
                     shadow=0.10, gauss=3.0, sp=0.0002, blur=0),
    "L2_photo": dict(dpi=150, rotate=(-3.5, 3.5), perspective=0.012, jpeg=62,
                     shadow=0.28, gauss=7.0, sp=0.001, blur=1),
    "L3_harsh": dict(dpi=72,  rotate=(-7.5, 7.5), perspective=0.030, jpeg=34,
                     shadow=0.48, gauss=13.0, sp=0.004, blur=3),
}


def autocrop(img: np.ndarray, margin: float = 0.03) -> np.ndarray:
    """Crop to the printed area plus a margin.

    An A4 render of a five-line invoice is two-thirds empty paper. Degrading
    that whole page makes the text smaller than any real photograph would --
    people frame the bill, not the desk. Without this, the DPI ladder is
    measuring framing rather than resolution."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (grey < 245).astype(np.uint8)
    if ink.sum() == 0:
        return img
    ys, xs = np.nonzero(ink)
    h, w = grey.shape
    my, mx = int(h * margin), int(w * margin)
    y0, y1 = max(0, ys.min() - my), min(h, ys.max() + my)
    x0, x1 = max(0, xs.min() - mx), min(w, xs.max() + mx)
    return img[y0:y1, x0:x1]


def _desk(shape, rng: random.Random) -> np.ndarray:
    """A plausible surface for the document to sit on, so the geometric warp
    exposes background instead of smearing edge pixels outward."""
    h, w = shape[:2]
    base = np.array(rng.choice([(78, 88, 104), (52, 58, 66), (118, 126, 138),
                                (66, 82, 96), (96, 96, 96)]), dtype=np.float32)
    bg = np.ones((h, w, 3), dtype=np.float32) * base
    nprng = np.random.default_rng(rng.randrange(2 ** 31))
    bg += nprng.normal(0, 7, bg.shape)
    return np.clip(bg, 0, 255).astype(np.uint8)


def _rotate(img: np.ndarray, deg: float, border) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def _perspective(img: np.ndarray, strength: float, rng: random.Random,
                 border=None) -> np.ndarray:
    """Simulate a phone held off-axis: displace the four corners independently."""
    if strength <= 0:
        return img
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    jitter = lambda: rng.uniform(-strength, strength)
    dst = np.float32([
        [w * jitter(), h * jitter()],
        [w * (1 + jitter()), h * jitter()],
        [w * (1 + jitter()), h * (1 + jitter())],
        [w * jitter(), h * (1 + jitter())],
    ])
    dst -= dst.min(axis=0)
    nw, nh = int(dst[:, 0].max()), int(dst[:, 1].max())
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, (max(nw, 1), max(nh, 1)),
                               flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                               borderValue=border if border is not None else (255, 255, 255))


def _shadow(img: np.ndarray, strength: float, rng: random.Random) -> np.ndarray:
    """Uneven lighting: a low-frequency multiplicative gradient plus one soft
    specular hotspot, which is what a ceiling light over a glossy bill does."""
    if strength <= 0:
        return img
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ang = rng.uniform(0, 2 * np.pi)
    ramp = (np.cos(ang) * xx / w + np.sin(ang) * yy / h)
    ramp = (ramp - ramp.min()) / (np.ptp(ramp) + 1e-6)
    field = 1.0 - strength * ramp

    cx, cy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.2, 0.8) * h
    r = rng.uniform(0.25, 0.5) * max(h, w)
    hot = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
    field = field * (1.0 + 0.35 * strength * hot)

    out = img.astype(np.float32) * field[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def _noise(img: np.ndarray, sigma: float, sp: float, rng: random.Random) -> np.ndarray:
    out = img.astype(np.float32)
    if sigma > 0:
        nprng = np.random.default_rng(rng.randrange(2 ** 31))
        out += nprng.normal(0, sigma, out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if sp > 0:
        nprng = np.random.default_rng(rng.randrange(2 ** 31))
        mask = nprng.random(out.shape[:2])
        out[mask < sp / 2] = 0
        out[mask > 1 - sp / 2] = 255
    return out


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return img
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def degrade_image(img: np.ndarray, level: str, seed: int) -> Tuple[np.ndarray, Dict]:
    """Apply one severity recipe. Order matters and mirrors physical reality:
    geometry (the camera pose) happens before optics (lighting), which happens
    before the sensor (noise), which happens before the codec (JPEG)."""
    if level not in PRESETS:
        raise ValueError("unknown level %r" % level)
    p = PRESETS[level]
    rng = random.Random(seed)
    applied: Dict[str, Any] = {"level": level, "seed": seed}

    img = autocrop(img)
    applied["cropped_size"] = [int(img.shape[1]), int(img.shape[0])]

    # L0/L1 stand in for a scanner or a native export: paper on white, square
    # to the sensor. L2/L3 stand in for a handheld photograph, which means a
    # surface behind the page.
    photo = level in ("L2_photo", "L3_harsh")
    border = tuple(int(x) for x in _desk((1, 1), rng)[0, 0]) if photo else (255, 255, 255)
    applied["background"] = "desk" if photo else "white"

    deg = rng.uniform(*p["rotate"]) if p["rotate"] != (0.0, 0.0) else 0.0
    if abs(deg) > 1e-6:
        img = _rotate(img, deg, border)
    applied["rotation_deg"] = round(deg, 3)

    if p["perspective"] > 0:
        img = _perspective(img, p["perspective"], rng, border)
    applied["perspective"] = p["perspective"]

    if p["shadow"] > 0:
        img = _shadow(img, p["shadow"], rng)
    applied["shadow"] = p["shadow"]

    if p["blur"] > 0:
        k = 2 * p["blur"] + 1
        img = cv2.GaussianBlur(img, (k, k), 0)
    applied["blur_px"] = p["blur"]

    if p["gauss"] > 0 or p["sp"] > 0:
        img = _noise(img, p["gauss"], p["sp"], rng)
    applied["gauss_sigma"] = p["gauss"]
    applied["salt_pepper"] = p["sp"]

    if p["jpeg"]:
        img = _jpeg(img, p["jpeg"])
    applied["jpeg_quality"] = p["jpeg"]
    applied["dpi"] = p["dpi"]
    applied["out_size"] = [int(img.shape[1]), int(img.shape[0])]
    return img, applied


def degrade_pdf(pdf_path: pathlib.Path, out_path: pathlib.Path, level: str,
                seed: int) -> Dict:
    """Rasterise at the level's DPI, degrade, and write. Returns the manifest."""
    from .render import pdf_to_png
    p = PRESETS[level]
    tmp = out_path.with_suffix(".raw.png")
    pages = pdf_to_png(pdf_path, tmp, dpi=p["dpi"])

    outputs: List[str] = []
    manifest: Dict[str, Any] = {}
    for i, page_png in enumerate(pages):
        img = cv2.imread(str(page_png))
        img, manifest = degrade_image(img, level, seed + i)
        dest = (out_path if len(pages) == 1
                else out_path.with_name("%s_p%d%s" % (out_path.stem, i + 1, out_path.suffix)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        # JPEG output for L2/L3: the artefacts must survive to disk, and
        # re-encoding into PNG would preserve them anyway but misrepresent what
        # a phone upload actually is.
        cv2.imwrite(str(dest), img)
        outputs.append(str(dest))
        page_png.unlink(missing_ok=True)

    manifest["pages"] = outputs
    manifest["source_pdf"] = str(pdf_path)
    return manifest
