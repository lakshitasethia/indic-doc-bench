"""Figures. The degradation curve is the one that carries the report.

Palette and mark specs follow a validated categorical set (slots blue / orange /
aqua / yellow), checked with the six-check validator on the adjacent pairlist
for lines and stacks and on the all-pairs list for the scatter. Three of those
slots sit below 3:1 against a light surface, which obligates visible direct
labels rather than legend-only identity -- so every series is directly labelled
as well as legended, and the report's markdown tables are the table view.
"""
from __future__ import annotations

import pathlib
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter

from .degrade import LEVELS

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_MUTED = "#8a8880"
GRID = "#e6e5e1"
# Validated categorical order; assigned in fixed slot order, never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

LEVEL_LABELS = {"L0_clean": "Clean\n300 dpi", "L1_scan": "Scan\n300 dpi",
                "L2_photo": "Photo\n150 dpi", "L3_harsh": "Harsh\n72 dpi"}


def _style(ax, ylabel: str, title: str, subtitle: str = ""):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
        ax.spines[side].set_linewidth(1)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.grid(True, axis="y", color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=13, fontweight="bold",
                 loc="left", pad=22 if subtitle else 10)
    if subtitle:
        ax.text(0, 1.03, subtitle, transform=ax.transAxes, color=INK_2,
                fontsize=9.5, va="bottom")


def degradation_curve(curves: Dict[str, Dict], out: pathlib.Path,
                      title: str = "Extraction accuracy collapses with image quality"):
    """Accuracy vs severity, one line per model, with bootstrap CI bands.

    This is a curve rather than a number on purpose: models that sit within
    noise of each other on clean input routinely separate by double digits at
    the harsh end, and that separation is the finding.

    Returns None for a corpus with no synthetic severity levels. Real documents
    (Layer 3) arrive at whatever quality they were collected at, so there is no
    severity axis to plot and drawing one would invent a comparison."""
    has_levels = any(any(lv in c["levels"] for lv in LEVELS) for c in curves.values())
    if not curves or not has_levels:
        return None
    fig, ax = plt.subplots(figsize=(9.5, 5.6), dpi=170)
    xs = list(range(len(LEVELS)))

    ordered = sorted(curves.items(),
                     key=lambda kv: -(kv[1]["levels"].get("L0_clean").point
                                      if kv[1]["levels"].get("L0_clean") else 0))
    for i, (model, c) in enumerate(ordered):
        color = SERIES[i % len(SERIES)]
        pts, lo, hi, valid = [], [], [], []
        for xi, lv in enumerate(LEVELS):
            ci = c["levels"].get(lv)
            if ci is None:
                continue
            valid.append(xi)
            pts.append(ci.point)
            lo.append(ci.lo)
            hi.append(ci.hi)
        if not pts:
            continue
        ax.fill_between(valid, lo, hi, color=color, alpha=0.13, linewidth=0, zorder=2)
        ax.plot(valid, pts, color=color, linewidth=2, zorder=3,
                marker="o", markersize=5.5, markerfacecolor=color,
                markeredgecolor=SURFACE, markeredgewidth=1.6, label=model)
        # Direct label at the right end: identity never rests on color alone.
        ax.annotate(model, (valid[-1], pts[-1]), xytext=(9, 0),
                    textcoords="offset points", color=color, fontsize=9,
                    fontweight="bold", va="center")

    ax.set_xticks(xs)
    ax.set_xticklabels([LEVEL_LABELS[l] for l in LEVELS])
    ax.set_xlim(-0.25, len(LEVELS) - 1 + 0.05)
    ax.set_ylim(0, 1.02)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    _style(ax, "Field accuracy", title,
           "Shaded bands are 95% bootstrap CIs, resampled by template")
    # Below the axes, never inside them: an in-plot legend sits on top of
    # whichever series happens to be lowest, and the low series is exactly the
    # one a degradation chart exists to show.
    ax.legend(frameon=False, ncol=min(len(ordered), 4), fontsize=9,
              labelcolor=INK_2, handlelength=1.6, borderpad=0,
              loc="upper left", bbox_to_anchor=(0, -0.13))
    fig.subplots_adjust(right=0.80)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out


def error_mix(mixes: Dict[str, Dict], out: pathlib.Path):
    """Stacked composition of the three error kinds.

    The split is the point: an omission is detectable downstream, a
    hallucinated or fabricated value is not."""
    models = list(mixes)
    kinds = [("missing", "Omitted (null)"), ("wrong", "Wrong value"),
             ("spurious", "Fabricated (truth is null)")]
    fig, ax = plt.subplots(figsize=(9.5, 0.62 * len(models) + 2.4), dpi=170)
    ys = list(range(len(models)))

    left = [0.0] * len(models)
    for k, (key, label) in enumerate(kinds):
        vals = []
        for i, m in enumerate(models):
            tot = sum(mixes[m][x] for x, _ in kinds) or 1
            vals.append(mixes[m][key] / tot)
        ax.barh(ys, vals, left=left, height=0.58, color=SERIES[k],
                label=label, zorder=3,
                # 2px surface gap between stacked segments
                edgecolor=SURFACE, linewidth=1.6)
        for i, v in enumerate(vals):
            if v > 0.075:      # label selectively, never on every segment
                ax.text(left[i] + v / 2, ys[i], "%.0f%%" % (100 * v),
                        ha="center", va="center", color="#ffffff",
                        fontsize=8.5, fontweight="bold", zorder=4)
        left = [left[i] + vals[i] for i in range(len(models))]

    ax.set_yticks(ys)
    ax.set_yticklabels(models, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(False)
    ax.grid(True, axis="x", color=GRID, linewidth=1, zorder=0)
    _style(ax, "", "How each model fails, not just how often",
           "Share of all field errors. Omissions are catchable downstream; the other two are not.")
    ax.set_ylabel("")
    ax.legend(frameon=False, ncol=3, fontsize=9, labelcolor=INK_2,
              loc="upper left", bbox_to_anchor=(0, -0.10), handlelength=1.4)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out


def cost_vs_accuracy(rows: List[Dict], out: pathlib.Path):
    """Cost per 1,000 documents against accuracy.

    One hue with direct labels rather than a colour per model: every point is a
    different entity, and a scatter is the form where an eight-hue categorical
    set stops being separable."""
    fig, ax = plt.subplots(figsize=(9.5, 5.6), dpi=170)
    for r in rows:
        x = max(r["inr_per_1k"], 0.5)      # log scale needs a positive floor
        ax.scatter([x], [r["accuracy"]], s=110, color=SERIES[0], zorder=3,
                   edgecolor=SURFACE, linewidth=1.8)
        ax.annotate(r["model"], (x, r["accuracy"]), xytext=(10, 4),
                    textcoords="offset points", fontsize=9, color=INK,
                    fontweight="bold")
        if r["inr_per_1k"] <= 0:
            ax.annotate("free (local)", (x, r["accuracy"]), xytext=(10, -9),
                        textcoords="offset points", fontsize=8, color=INK_MUTED)
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, p: "₹%g" % v))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("Cost per 1,000 documents (INR, log scale)", color=INK_2, fontsize=9)
    _style(ax, "Field accuracy", "What accuracy costs",
           "Up and to the left is better. Log scale — the spread is orders of magnitude.")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out
