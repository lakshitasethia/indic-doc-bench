"""Per-field scoring with a four-way outcome, plus document-level exact match.

Outcome space, and why each one is separate:

  CORRECT      value present and equal after normalisation
  WRONG        value present and different -- the model asserted something false
  MISSING      ground truth has a value, model returned null/absent
  SPURIOUS     ground truth is null, model invented a value
  ABSENT_OK    both null (counted as correct, but tracked apart so a schema
               full of legitimately-null fields cannot inflate the headline)

Collapsing WRONG and MISSING into "incorrect" throws away the single most
operationally important distinction in the whole benchmark: an omission is
detectable downstream (the field is empty, route to a human), a hallucination
is not (the field is populated and plausible, and it enters the ledger).

SPURIOUS matters for the same reason and is specific to this domain: on an
intra-state invoice IGST genuinely does not exist. A model that writes a
number there has fabricated a tax liability. `null_equiv_zero` in the schema
lets an explicit 0.00 count as correct there, since that is a representational
choice rather than a fabrication.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional

from .align import Alignment, align
from .normalize import FUZZY_THRESHOLD, fuzzy_score, normalize
from .schema import (ALL_HEADER, FIELD_BY_NAME, LINE_FIELD_BY_NAME,
                     LINE_ITEM_FIELDS, Field)

CORRECT = "correct"
WRONG = "wrong"
MISSING = "missing"
SPURIOUS = "spurious"
ABSENT_OK = "absent_ok"

CORRECT_LIKE = (CORRECT, ABSENT_OK)


class FieldResult(NamedTuple):
    field: str
    outcome: str
    gt_raw: Any
    pred_raw: Any
    gt_norm: Any
    pred_norm: Any
    similarity: Optional[float] = None   # fuzzy fields only
    format_error: bool = False           # value present but unparseable as its type
    row: Optional[int] = None            # ground-truth line index, None for header
    critical: bool = False
    weight: float = 1.0


class DocResult(NamedTuple):
    doc_id: str
    model: str
    fields: List[FieldResult]
    alignment: Alignment
    line_structural: Dict[str, int]      # merges / splits / missing_rows / spurious_rows
    schema_violation: Optional[str]      # non-None if prediction was unusable
    exact_match: bool                    # every field correct, incl. line items
    critical_exact_match: bool           # every critical field correct


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a", "na", "-", "nil"):
        return True
    return False


def _values_equal(f: Field, g: Any, p: Any, fuzzy_threshold: int):
    """Returns (equal, similarity_or_None)."""
    if f.ftype == "fuzzy":
        s = fuzzy_score(g, p)
        return s >= fuzzy_threshold, s
    if isinstance(g, Decimal) and isinstance(p, Decimal):
        return g == p, None
    return g == p, None


def score_field(f: Field, gt_raw: Any, pred_raw: Any, row: Optional[int] = None,
                fuzzy_threshold: int = FUZZY_THRESHOLD) -> FieldResult:
    g_null, p_null = _is_null(gt_raw), _is_null(pred_raw)
    g_norm, g_ok = normalize(f.ftype, None if g_null else gt_raw, f.name)
    p_norm, p_ok = normalize(f.ftype, None if p_null else pred_raw, f.name)
    fmt_err = (not p_ok) and not p_null

    # An explicit zero where the truth is "this tax does not apply" is a
    # representation choice, not a fabrication -- but only where the schema
    # says so, and only for zero, never for another number.
    if f.null_equiv_zero:
        if g_null and isinstance(p_norm, Decimal) and p_norm == 0:
            p_null, p_norm = True, None
        if p_null and isinstance(g_norm, Decimal) and g_norm == 0:
            g_null, g_norm = True, None

    def mk(outcome, sim=None):
        return FieldResult(f.name, outcome, gt_raw, pred_raw, g_norm, p_norm,
                           sim, fmt_err, row, f.critical, f.weight)

    if g_null and p_null:
        return mk(ABSENT_OK)
    if g_null and not p_null:
        return mk(SPURIOUS)
    if p_null and not g_null:
        return mk(MISSING)
    if fmt_err:
        # Present but uninterpretable as this type: wrong, and flagged so the
        # taxonomy can separate "bad value" from "bad representation".
        return mk(WRONG)
    equal, sim = _values_equal(f, g_norm, p_norm, fuzzy_threshold)
    return mk(CORRECT if equal else WRONG, sim)


def score_document(doc_id: str, model: str, gt: Dict, pred: Optional[Dict],
                   fuzzy_threshold: int = FUZZY_THRESHOLD,
                   schema_violation: Optional[str] = None) -> DocResult:
    """Score one prediction against one ground-truth record.

    ``pred=None`` (unparseable output, refusal, API failure) is not skipped:
    every field is recorded as MISSING so the document still contributes to the
    denominator. Dropping failed documents silently is how benchmarks end up
    flattering the least reliable models."""
    fields: List[FieldResult] = []
    p = pred if isinstance(pred, dict) else {}

    for f in ALL_HEADER:
        fields.append(score_field(f, gt.get(f.name), p.get(f.name),
                                  None, fuzzy_threshold))

    gt_items = gt.get("line_items") or []
    pred_items = p.get("line_items") or []
    if not isinstance(pred_items, list):
        pred_items, schema_violation = [], schema_violation or "line_items_not_a_list"
    pred_items = [x for x in pred_items if isinstance(x, dict)]

    al = align(gt_items, pred_items)

    for (i, j) in al.pairs:
        for f in LINE_ITEM_FIELDS:
            fields.append(score_field(f, gt_items[i].get(f.name),
                                      pred_items[j].get(f.name), i, fuzzy_threshold))
    for i in al.missing_gt:
        for f in LINE_ITEM_FIELDS:
            fields.append(score_field(f, gt_items[i].get(f.name), None, i, fuzzy_threshold))
    for j in al.spurious_pred:
        for f in LINE_ITEM_FIELDS:
            # row=-1 marks "a line-item field with no ground-truth row", which
            # keeps it inside the line_items group for the split report while
            # still distinguishing it from a matched row.
            fields.append(score_field(f, None, pred_items[j].get(f.name), -1, fuzzy_threshold))

    # Rows involved in a merge or split are deliberately NOT expanded into
    # per-field outcomes. Their fields are neither right nor wrong in a way the
    # per-field metric can express, and forcing them in would double-count one
    # structural mistake as up to sixteen field errors. They are reported as
    # structural errors and they always fail document-level exact match.
    structural = {
        "merges": len(al.merges),
        "splits": len(al.splits),
        "missing_rows": len(al.missing_gt),
        "spurious_rows": len(al.spurious_pred),
        "rows_gt": len(gt_items),
        "rows_pred": len(pred_items),
    }

    structural_clean = not (al.merges or al.splits or al.missing_gt or al.spurious_pred)
    exact = (schema_violation is None and structural_clean
             and all(fr.outcome in CORRECT_LIKE for fr in fields))
    crit_exact = (schema_violation is None and structural_clean
                  and all(fr.outcome in CORRECT_LIKE for fr in fields if fr.critical))

    return DocResult(doc_id, model, fields, al, structural, schema_violation,
                     exact, crit_exact)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def field_table(results: List[DocResult]) -> Dict[str, Dict[str, int]]:
    """Per-field outcome counts across documents."""
    table: Dict[str, Dict[str, int]] = {}
    for dr in results:
        for fr in dr.fields:
            row = table.setdefault(fr.field, {CORRECT: 0, WRONG: 0, MISSING: 0,
                                              SPURIOUS: 0, ABSENT_OK: 0,
                                              "format_error": 0})
            row[fr.outcome] += 1
            if fr.format_error:
                row["format_error"] += 1
    return table


def accuracy(results: List[DocResult], field: Optional[str] = None,
             populated_only: bool = True, group: Optional[str] = None) -> float:
    """Field accuracy.

    ``group`` restricts to ``"header"`` or ``"line_items"``; None scores both.

    ``populated_only=True`` (the default and the headline definition) scores
    only field instances where ground truth is non-null, so the number answers
    "of the values actually on the document, how many were extracted
    correctly". Including the both-null cases inflates every model equally and
    makes the benchmark less discriminating."""
    num = den = 0
    for dr in results:
        for fr in dr.fields:
            if field and fr.field != field:
                continue
            # Header fields and line-item fields are different tasks: one is
            # finding labelled scalars, the other is parsing a table whose
            # length varies from 1 to 28 rows. A blended number is dominated by
            # whichever has more instances, which on a long invoice is always
            # the table -- so a system that reads the header perfectly and
            # skips the table scores near zero, and a system that does the
            # reverse scores well. Both are misleading, so both groups are
            # reported separately as well as together.
            if group == "header" and fr.row is not None:
                continue
            if group == "line_items" and fr.row is None:
                continue
            if populated_only and fr.outcome in (ABSENT_OK, SPURIOUS):
                continue
            den += 1
            if fr.outcome in CORRECT_LIKE:
                num += 1
    return num / den if den else float("nan")


def doc_exact_match_rate(results: List[DocResult], critical_only: bool = False) -> float:
    if not results:
        return float("nan")
    hits = sum(1 for r in results
               if (r.critical_exact_match if critical_only else r.exact_match))
    return hits / len(results)


def hallucination_rate(results: List[DocResult]) -> float:
    """Share of erroneous field instances where the model asserted a false
    value rather than declining to answer. This is the number a production
    engineer actually needs: it is the fraction of errors that no downstream
    null-check will ever catch."""
    wrong = sum(1 for r in results for f in r.fields
                if f.outcome in (WRONG, SPURIOUS))
    miss = sum(1 for r in results for f in r.fields if f.outcome == MISSING)
    return wrong / (wrong + miss) if (wrong + miss) else float("nan")
