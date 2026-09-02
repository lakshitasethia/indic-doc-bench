"""Error taxonomy: automatic pre-classification, then human review.

The taxonomy is the part of this benchmark people will cite. The accuracy
table is table stakes; *how* a model fails is what decides whether it can be
put in front of customers.

Most of the manual work is mechanical and can be done by the machine. Three
categories in particular fall out automatically and are the ones reviewers are
worst at spotting by eye:

* **field confusion** -- the predicted value for field A is exactly the ground
  truth of some *other* field B on the same document. This catches buyer/seller
  GSTIN swaps, billing/shipping address swaps, and CGST/SGST/IGST transposition
  with no judgement required, and those are among the most consequential errors
  a production system can make: every value is present and well-formed, so
  nothing downstream flags it.
* **character-level misread** -- small edit distance on a same-length string,
  with an extra flag when every substitution is a known OCR confusion pair
  (0/O, 1/l/I, 5/S, 8/B, 2/Z, 6/G).
* **structural** -- merges and splits, already identified during alignment.

What is left for a human is the genuinely ambiguous residue, mostly
distinguishing a hallucinated value from a plausible misreading. `sample_for_review`
draws that residue stratified by model and severity so the manual pass covers
the space instead of whatever happened to sort first.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Dict, List, NamedTuple, Optional

from .score import (MISSING, SPURIOUS, WRONG, DocResult)

# Categories. Order matters: the first matching rule wins, most specific first.
REFUSAL = "refusal"
SCHEMA_VIOLATION = "schema_violation"
TRUNCATION = "truncated_output"
LINE_MERGE = "line_item_merge"
LINE_SPLIT = "line_item_split"
FIELD_CONFUSION = "field_confusion"
CHAR_MISREAD = "character_misread"
FORMAT_ERROR = "format_error"
OMISSION = "omission"
FABRICATION = "fabricated_on_null"
WRONG_VALUE = "wrong_value"          # residue: needs a human to call it

CATEGORIES = [REFUSAL, SCHEMA_VIOLATION, TRUNCATION, LINE_MERGE, LINE_SPLIT,
              FIELD_CONFUSION, CHAR_MISREAD, FORMAT_ERROR, OMISSION,
              FABRICATION, WRONG_VALUE]

# Glyph pairs that OCR and vision models actually confuse.
OCR_CONFUSIONS = {
    frozenset("0O"), frozenset("0D"), frozenset("1I"), frozenset("1l"),
    frozenset("Il"), frozenset("5S"), frozenset("8B"), frozenset("2Z"),
    frozenset("6G"), frozenset("7T"), frozenset("9g"), frozenset("VY"),
    frozenset("UV"), frozenset("cC"), frozenset("rn"),
}


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_char_misread(gt: object, pred: object) -> Optional[bool]:
    """Returns None if not a misread, True if every difference is a known OCR
    confusion pair, False if it is a small edit of some other kind."""
    if gt is None or pred is None:
        return None
    g, p = str(gt), str(pred)
    if not g or not p or abs(len(g) - len(p)) > 1:
        return None
    d = _edit_distance(g, p)
    if d == 0 or d > 2:
        return None
    if len(g) == len(p):
        subs = [(a, b) for a, b in zip(g, p) if a != b]
        if subs and all(frozenset((a, b)) in OCR_CONFUSIONS for a, b in subs):
            return True
    return False


def _confused_with(field: str, pred_norm: object, gt_record: Dict,
                   normalized: Dict[str, object]) -> Optional[str]:
    """Is this predicted value the ground truth of a *different* field?"""
    if pred_norm is None:
        return None
    for other, val in normalized.items():
        if other == field or val is None:
            continue
        if val == pred_norm:
            return other
    return None


class Finding(NamedTuple):
    model: str
    variant_id: str
    level: str
    template_id: str
    field: str
    row: Optional[int]
    category: str
    detail: str
    gt: object
    pred: object
    needs_review: bool


def classify_document(dr: DocResult, level: str, template_id: str,
                      finish_reason: Optional[str] = None,
                      refusal: bool = False) -> List[Finding]:
    findings: List[Finding] = []

    def add(field, row, cat, detail, gt, pred, review=False):
        findings.append(Finding(dr.model, dr.doc_id, level, template_id, field,
                                row, cat, detail, gt, pred, review))

    if refusal:
        add("*", None, REFUSAL, "model declined the document", None, None)
        return findings
    if dr.schema_violation == "truncated_output" or finish_reason == "length":
        add("*", None, TRUNCATION, "output hit the token cap", None, None)
    elif dr.schema_violation:
        add("*", None, SCHEMA_VIOLATION, dr.schema_violation, None, None)

    for n in range(dr.line_structural["merges"]):
        add("line_items", None, LINE_MERGE, "predicted row spans several printed rows", None, None)
    for n in range(dr.line_structural["splits"]):
        add("line_items", None, LINE_SPLIT, "printed row split across predictions", None, None)

    # Ground-truth values of every header field, for confusion detection.
    header_norm = {fr.field: fr.gt_norm for fr in dr.fields if fr.row is None}

    for fr in dr.fields:
        if fr.outcome not in (WRONG, MISSING, SPURIOUS):
            continue
        if fr.outcome == MISSING:
            add(fr.field, fr.row, OMISSION, "returned null where a value is printed",
                fr.gt_norm, None)
            continue
        if fr.outcome == SPURIOUS:
            other = _confused_with(fr.field, fr.pred_norm, {}, header_norm)
            if other:
                add(fr.field, fr.row, FIELD_CONFUSION,
                    "value belongs to '%s'" % other, None, fr.pred_norm)
            else:
                add(fr.field, fr.row, FABRICATION,
                    "invented a value where the truth is null", None, fr.pred_norm)
            continue
        # WRONG
        if fr.format_error:
            add(fr.field, fr.row, FORMAT_ERROR, "unparseable as its type",
                fr.gt_norm, fr.pred_raw)
            continue
        other = _confused_with(fr.field, fr.pred_norm, {}, header_norm)
        if other:
            add(fr.field, fr.row, FIELD_CONFUSION, "value belongs to '%s'" % other,
                fr.gt_norm, fr.pred_norm)
            continue
        mis = is_char_misread(fr.gt_norm, fr.pred_norm)
        if mis is not None:
            add(fr.field, fr.row, CHAR_MISREAD,
                "known glyph confusion" if mis else "small edit distance",
                fr.gt_norm, fr.pred_norm)
            continue
        # Everything else needs a human to separate a hallucination from a
        # misreading -- the machine cannot tell whether the value appears
        # somewhere on the page.
        add(fr.field, fr.row, WRONG_VALUE, "needs review", fr.gt_norm,
            fr.pred_norm, review=True)
    return findings


def distribution(findings: List[Finding]) -> Dict[str, Dict[str, int]]:
    """Per-model category counts -- the table people will cite."""
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: {c: 0 for c in CATEGORIES})
    for f in findings:
        out[f.model][f.category] += 1
    return dict(out)


def sample_for_review(findings: List[Finding], per_stratum: int = 8,
                      seed: int = 0) -> List[Finding]:
    """Stratified sample of the residue, by (model, severity, field group).

    Stratified rather than random because the interesting failures cluster: a
    plain random draw over a corpus that is 75% degraded returns almost nothing
    from clean documents, and the clean-document failures are the ones that say
    something about the model rather than about the camera."""
    rng = random.Random(seed)
    strata: Dict[tuple, List[Finding]] = defaultdict(list)
    for f in findings:
        if not f.needs_review:
            continue
        strata[(f.model, f.level, "line" if f.row is not None else "header")].append(f)
    out: List[Finding] = []
    for key in sorted(strata):
        pool = strata[key]
        rng.shuffle(pool)
        out.extend(pool[:per_stratum])
    return out


def markdown_distribution(dist: Dict[str, Dict[str, int]]) -> str:
    models = sorted(dist)
    rows = ["### Error taxonomy\n",
            "| Category | " + " | ".join(models) + " |",
            "|---|" + "---:|" * len(models)]
    for c in CATEGORIES:
        counts = [dist[m].get(c, 0) for m in models]
        if not any(counts):
            continue
        rows.append("| %s | %s |" % (c.replace("_", " "),
                                     " | ".join(str(x) for x in counts)))
    totals = [sum(dist[m].values()) for m in models]
    rows.append("| **total findings** | %s |" % " | ".join("**%d**" % t for t in totals))
    return "\n".join(rows)


def to_csv(findings: List[Finding], path) -> None:
    """Write the review queue. Two blank columns are for the human pass, so the
    file can be opened in a spreadsheet and filled in directly."""
    import csv
    import pathlib
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "variant_id", "level", "template_id", "field", "row",
                    "auto_category", "detail", "ground_truth", "predicted",
                    "reviewed_category", "reviewer_note"])
        for f in findings:
            w.writerow([f.model, f.variant_id, f.level, f.template_id, f.field,
                        "" if f.row is None else f.row, f.category, f.detail,
                        f.gt, f.pred, "", ""])
