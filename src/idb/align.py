"""Line-item alignment.

Predicted line items do not arrive in ground-truth order, and models routinely
merge two rows into one or split one row into two. Comparing index i to index i
therefore measures ordering luck, not extraction quality.

We build a field-similarity cost matrix and solve the assignment optimally with
the Hungarian algorithm, reject weak matches, and then run a second pass that
explains the leftovers as merges or splits — which are reported as their own
error category rather than being double-counted as one omission plus one
hallucination.
"""
from __future__ import annotations

from decimal import Decimal
from itertools import combinations
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from .normalize import fuzzy_score, normalize
from .schema import ALIGNMENT_WEIGHTS, LINE_FIELD_BY_NAME

# A pairing weaker than this is not a match at all; forcing it would invent a
# "wrong value" where the model simply never produced the row.
MIN_MATCH_SIMILARITY = 0.35
# Tolerance when testing whether merged amounts add up (rupees).
SUM_TOLERANCE = Decimal("0.05")


class Alignment(NamedTuple):
    pairs: List[Tuple[int, int]]          # (gt_index, pred_index)
    missing_gt: List[int]                 # rows the model never produced
    spurious_pred: List[int]              # rows with no ground-truth counterpart
    merges: List[Tuple[List[int], int]]   # ([gt indices], pred index)
    splits: List[Tuple[int, List[int]]]   # (gt index, [pred indices])
    cost_matrix: Any


def _num_sim(a: Optional[Decimal], b: Optional[Decimal]) -> float:
    """Relative-error similarity for numeric fields, so a 1-rupee difference on
    a 50,000-rupee line does not look like a total mismatch during *matching*
    (it is still scored as wrong)."""
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.0
    if a == b:
        return 1.0
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 1.0
    rel = float(abs(a - b) / denom)
    return max(0.0, 1.0 - min(1.0, rel))


def _field_similarity(field: str, gt_val: Any, pred_val: Any) -> float:
    f = LINE_FIELD_BY_NAME[field]
    g, _ = normalize(f.ftype, gt_val, field)
    p, _ = normalize(f.ftype, pred_val, field)
    if f.ftype == "fuzzy":
        return fuzzy_score(g, p) / 100.0
    if f.ftype in ("money", "quantity", "percent"):
        return _num_sim(g, p)
    if g is None and p is None:
        return 1.0
    if g is None or p is None:
        return 0.0
    return 1.0 if g == p else 0.0


def similarity(gt_item: Dict, pred_item: Dict) -> float:
    total_w = 0.0
    acc = 0.0
    for field, w in ALIGNMENT_WEIGHTS.items():
        acc += w * _field_similarity(field, gt_item.get(field), pred_item.get(field))
        total_w += w
    return acc / total_w if total_w else 0.0


def _taxable(item: Dict) -> Optional[Decimal]:
    v, _ = normalize("money", item.get("taxable_value"), "taxable_value")
    return v


def _detect_merges_splits(gt_items, pred_items, pairs, missing_gt, spurious_pred):
    """Explain leftovers using amount arithmetic.

    A merge is one predicted row whose taxable value equals the sum of two or
    three ground-truth rows. A split is the mirror image. Only subsets of size
    2-3 are considered: larger combinations start finding coincidental sums,
    and in hand review those were almost always spurious.

    Crucially this pass may *dissolve an existing pair*. When a model merges
    rows A and B, the assignment step will still bind the merged row to
    whichever of A or B it resembles more, stranding the other as a false
    omission. So each pair is re-examined: if the predicted amount equals its
    partner's amount plus one or more unmatched ground-truth amounts, the pair
    is withdrawn and the whole group is recorded as one merge.
    """
    merges: List[Tuple[List[int], int]] = []
    splits: List[Tuple[int, List[int]]] = []

    remaining_gt = list(missing_gt)
    remaining_pred = list(spurious_pred)
    kept_pairs = list(pairs)

    # Pass 0: absorb stranded rows into an already-matched predicted row.
    for (g_idx, p_idx) in list(kept_pairs):
        if not remaining_gt:
            break
        p_amt = _taxable(pred_items[p_idx])
        g_amt = _taxable(gt_items[g_idx])
        if p_amt is None or g_amt is None:
            continue
        found = None
        for size in (1, 2):
            for combo in combinations(list(remaining_gt), size):
                amts = [_taxable(gt_items[i]) for i in combo]
                if any(a is None for a in amts):
                    continue
                if abs(g_amt + sum(amts) - p_amt) <= SUM_TOLERANCE:
                    found = list(combo)
                    break
            if found:
                break
        if found:
            merges.append((sorted([g_idx] + found), p_idx))
            kept_pairs.remove((g_idx, p_idx))
            for i in found:
                remaining_gt.remove(i)

    # Mirror image: one ground-truth row split across a matched predicted row
    # plus one or more stranded predicted rows.
    for (g_idx, p_idx) in list(kept_pairs):
        if not remaining_pred:
            break
        p_amt = _taxable(pred_items[p_idx])
        g_amt = _taxable(gt_items[g_idx])
        if p_amt is None or g_amt is None:
            continue
        found = None
        for size in (1, 2):
            for combo in combinations(list(remaining_pred), size):
                amts = [_taxable(pred_items[j]) for j in combo]
                if any(a is None for a in amts):
                    continue
                if abs(p_amt + sum(amts) - g_amt) <= SUM_TOLERANCE:
                    found = list(combo)
                    break
            if found:
                break
        if found:
            splits.append((g_idx, sorted([p_idx] + found)))
            kept_pairs.remove((g_idx, p_idx))
            for j in found:
                remaining_pred.remove(j)

    for p_idx in list(remaining_pred):
        p_amt = _taxable(pred_items[p_idx])
        if p_amt is None:
            continue
        found = None
        for size in (2, 3):
            for combo in combinations([i for i in remaining_gt], size):
                amts = [_taxable(gt_items[i]) for i in combo]
                if any(a is None for a in amts):
                    continue
                if abs(sum(amts) - p_amt) <= SUM_TOLERANCE:
                    found = list(combo)
                    break
            if found:
                break
        if found:
            merges.append((found, p_idx))
            remaining_pred.remove(p_idx)
            for i in found:
                remaining_gt.remove(i)

    for g_idx in list(remaining_gt):
        g_amt = _taxable(gt_items[g_idx])
        if g_amt is None:
            continue
        found = None
        for size in (2, 3):
            for combo in combinations([i for i in remaining_pred], size):
                amts = [_taxable(pred_items[i]) for i in combo]
                if any(a is None for a in amts):
                    continue
                if abs(sum(amts) - g_amt) <= SUM_TOLERANCE:
                    found = list(combo)
                    break
            if found:
                break
        if found:
            splits.append((g_idx, found))
            remaining_gt.remove(g_idx)
            for i in found:
                remaining_pred.remove(i)

    return kept_pairs, merges, splits, remaining_gt, remaining_pred


def align(gt_items: List[Dict], pred_items: List[Dict]) -> Alignment:
    n, m = len(gt_items), len(pred_items)
    if n == 0 or m == 0:
        return Alignment([], list(range(n)), list(range(m)), [], [],
                         np.zeros((n, m)))

    sim = np.zeros((n, m), dtype=float)
    for i in range(n):
        for j in range(m):
            sim[i, j] = similarity(gt_items[i], pred_items[j])

    row_ind, col_ind = linear_sum_assignment(1.0 - sim)

    pairs, missing, spurious = [], [], []
    matched_gt, matched_pred = set(), set()
    for i, j in zip(row_ind, col_ind):
        if sim[i, j] >= MIN_MATCH_SIMILARITY:
            pairs.append((int(i), int(j)))
            matched_gt.add(int(i))
            matched_pred.add(int(j))
    missing = [i for i in range(n) if i not in matched_gt]
    spurious = [j for j in range(m) if j not in matched_pred]

    pairs, merges, splits, missing, spurious = _detect_merges_splits(
        gt_items, pred_items, pairs, missing, spurious)

    return Alignment(pairs, missing, spurious, merges, splits, sim)
