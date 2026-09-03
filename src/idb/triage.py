"""Narrow the `wrong_value` residue before a human looks at it.

`taxonomy.py` auto-classifies what it can and queues the rest. That residue is
labelled `wrong_value`, which says only "this is not the right answer" -- the
reviewer still has to work out *why*, one row at a time, against a document
they have to go and open.

This module attacks the residue with three signals the classifier does not use,
chosen because each maps to a failure mode that is genuinely different in
production:

  1. **Cross-field match.** If the predicted value is, verbatim, some *other*
     real value from the same document, the model did not misread anything --
     it read the wrong box. That is `field_confusion`, and it behaves nothing
     like a character error: it survives a re-scan at higher resolution.

  2. **GST arithmetic.** Invoice numbers are related, so a wrong amount is
     often a *right* amount from somewhere else -- a line quantity, a unit
     price, or the tax the model computed instead of reading. Distinguishing
     "misread the digits" from "computed 18%/2 and trusted its own
     arithmetic" matters, because only the first one gets better with a
     sharper image.

  3. **Numeric distance rather than string distance.** `1562897.00` against
     `1562896.93` is a rounding artefact; `266.78` against `240.72` is a
     different number entirely. Levenshtein rates them as similar edits and is
     simply the wrong tool on a numeral.

What it deliberately does not do is decide. Every suggestion carries the
evidence that produced it in `suggestion_basis`, and anything the signals do
not settle is marked `AMBIGUOUS-*` and left alone. A triage step that quietly
guessed would be worse than no triage step, because the guesses would be
indistinguishable from reviewed judgements in the published taxonomy.
"""
from __future__ import annotations

import csv
import json
import math
import pathlib
import re
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .consistency import consistency_score

# Suggestions this module can make. `AMBIGUOUS-*` is not a category -- it is an
# explicit refusal to guess, and those rows are the reviewer's actual work.
AMBIGUOUS_NUMERIC = "AMBIGUOUS-numeric"
AMBIGUOUS_TEXT = "AMBIGUOUS-text"
ENTITY_SUBSTITUTION = "entity_substitution"

GST_SLABS = (5, 12, 18, 28)
HEADER_AMOUNTS = ("total_taxable_value", "cgst_amount", "sgst_amount",
                  "igst_amount", "cess_amount", "grand_total", "round_off")
LINE_NUMERICS = ("taxable_value", "quantity", "unit_price", "tax_rate", "discount")


def _norm(v) -> str:
    return str(v).strip().lower() if v is not None else ""


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _close(a: Optional[float], b: Optional[float], tol: float = 0.02) -> bool:
    """Equal to the paisa, with a relative allowance on large sums."""
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol, abs(b) * 1e-4)


def flat_ground_truth(gt: Dict) -> Dict[str, List[str]]:
    """Every ground-truth value in a document, indexed by where it came from.

    Keyed by value so a prediction can be looked up directly: the question is
    always "does this value exist somewhere in the document", never "what is
    field X".
    """
    out: Dict[str, List[str]] = {}
    for k, v in gt.items():
        if k == "line_items" or v is None or not str(v).strip():
            continue
        out.setdefault(_norm(v), []).append(k)
    for i, item in enumerate(gt.get("line_items") or []):
        for k, v in item.items():
            if v is None or not str(v).strip():
                continue
            out.setdefault(_norm(v), []).append("line[%d].%s" % (i, k))
    return out


def arithmetic_story(field: str, predicted: float, gt: Dict) -> Optional[str]:
    """Explain a wrong number as a right number from somewhere else.

    Returns a human-readable provenance string, or None when the number
    corresponds to nothing in the document -- which is the genuinely
    interesting case, and the one left for review.
    """
    items = gt.get("line_items") or []
    candidates: List[Tuple[str, Optional[float]]] = []

    for k in HEADER_AMOUNTS:
        if k != field:
            candidates.append((k, _num(gt.get(k))))

    line_total = sum(_num(x.get("taxable_value")) or 0.0 for x in items)
    if line_total:
        candidates.append(("sum(line taxable)", line_total))

    for i, item in enumerate(items):
        for k in LINE_NUMERICS:
            if k != field:
                candidates.append(("line[%d].%s" % (i, k), _num(item.get(k))))

    for label, value in candidates:
        if _close(predicted, value):
            return label

    # Computed rather than read. A model that derives CGST as taxable x rate/2
    # produces a number that is internally defensible and still wrong, and it
    # will not improve with a better photograph.
    taxable = _num(gt.get("total_taxable_value"))
    if taxable:
        for rate in GST_SLABS:
            if _close(predicted, taxable * rate / 200.0):
                return "taxable x %d%%/2 (computed CGST/SGST)" % rate
            if _close(predicted, taxable * rate / 100.0):
                return "taxable x %d%% (computed total tax)" % rate
    return None


def suggest(field: str, ground_truth, predicted, gt_doc: Dict) -> Tuple[str, str]:
    """Suggest a category for one residue row, with the evidence for it."""
    from .taxonomy import (CHAR_MISREAD, FIELD_CONFUSION, FORMAT_ERROR,
                           OMISSION)

    g, p = _norm(ground_truth), _norm(predicted)
    if not p:
        return OMISSION, "predicted empty"

    flat = flat_ground_truth(gt_doc)
    for value, fields in flat.items():
        # `not any(field in f)` so a line-item field is not "confused" with
        # the same field on another row -- that is misalignment, which
        # taxonomy.py already detects as a merge or split.
        if value == p and not any(field in f for f in fields):
            return FIELD_CONFUSION, "value belongs to %s" % ",".join(sorted(set(fields))[:2])

    gn, pn = _num(g), _num(p)
    if gn is not None and pn is not None:
        # Digit-identical first: values that differ only in separators are a
        # formatting difference, and calling them a misread would blame the
        # model's eyesight for a comma.
        if re.sub(r"[^0-9]", "", g) == re.sub(r"[^0-9]", "", p):
            return FORMAT_ERROR, "same digits, different separators"
        if _close(pn, gn, tol=abs(gn) * 1e-3):
            return CHAR_MISREAD, "within 0.1%% (%.2f vs %.2f)" % (gn, pn)
        story = arithmetic_story(field, pn, gt_doc)
        if story:
            return FIELD_CONFUSION, "predicted = %s" % story
        ratio = pn / gn if gn else 0.0
        if 8.5 <= ratio <= 11.0 or 0.09 <= ratio <= 0.11:
            return CHAR_MISREAD, "digit slip 10x (%.2f vs %.2f)" % (gn, pn)
        rel = abs(gn - pn) / max(abs(gn), 1e-9)
        return AMBIGUOUS_NUMERIC, "unrelated number (%.2f vs %.2f, %.0f%% off)" % (gn, pn, 100 * rel)

    distance = Levenshtein.distance(g, p)
    if distance <= 2:
        return CHAR_MISREAD, "edit distance %d" % distance
    similarity = fuzz.ratio(g, p)
    if similarity >= 80:
        return CHAR_MISREAD, "fuzzy %d" % similarity
    shared = set(g.split()) & set(p.split())
    if shared and len(shared) < max(len(g.split()), len(p.split())):
        # "deshmukh industries" -> "redbrick industries": the model kept the
        # shape of the field and invented the identifying part. That is much
        # closer to fabrication than to a misread.
        return ENTITY_SUBSTITUTION, "kept %r" % (" ".join(sorted(shared))[:30])
    return AMBIGUOUS_TEXT, "unrelated (fuzzy %d)" % similarity


def triage_queue(queue_path: pathlib.Path, gt_by_doc: Dict[str, Dict],
                 raw_dir: pathlib.Path) -> List[Dict]:
    """Enrich a review queue with a suggestion, its basis, and self-consistency.

    `self_consistency` is the fraction of ground-truth-free arithmetic checks
    the model's own record passes. A wrong answer that still reconciles is the
    dangerous one -- it survives validation at intake -- so the reviewer should
    see it flagged rather than have to compute it.
    """
    rows = list(csv.DictReader(open(queue_path)))
    for r in rows:
        doc_id = r["variant_id"].split("__")[0]
        gt_doc = gt_by_doc.get(doc_id, {})
        category, basis = suggest(r["field"], r.get("ground_truth"),
                                  r.get("predicted"), gt_doc)
        r["suggested_category"] = category
        r["suggestion_basis"] = basis

        path = raw_dir / r["model"].replace("/", "_") / ("%s.json" % r["variant_id"])
        score = float("nan")
        if path.exists():
            score = consistency_score(json.loads(path.read_text()).get("record"))
        r["self_consistency"] = "" if math.isnan(score) else "%.2f" % score
    return rows


def write(rows: List[Dict], out_path: pathlib.Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarise(rows: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["suggested_category"]] = counts.get(r["suggested_category"], 0) + 1
    return counts
