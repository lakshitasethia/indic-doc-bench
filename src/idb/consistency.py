"""Ground-truth-free validity checks on an extracted invoice record.

Every check here runs on model output alone. That makes them usable two ways:

  1. As a benchmark metric -- a model whose output is internally coherent is
     more trustworthy than one with the same field accuracy and incoherent
     arithmetic, and this separates models that "read" an invoice from models
     that pattern-match plausible numbers into slots.
  2. As a production confidence signal -- these are exactly the checks a real
     pipeline would run to decide whether a document needs human review. That
     makes the benchmark's output directly reusable, which is unusual.

The GSTIN checksum check is the sharpest of these: a single misread character
in a 15-character GSTIN fails the check digit with probability 35/36, so
character-level OCR damage on the most business-critical field is detectable
with no reference data at all.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional

from .india import gstin_is_valid, gstin_state_code
from .normalize import norm_money, norm_percent, norm_quantity, norm_state, norm_date

# Invoice totals are rounded to the rupee, and line-level rounding accumulates,
# so an exact-equality check would fire constantly on genuine documents. One
# paisa per line item plus one rupee of round-off is the documented tolerance.
BASE_TOLERANCE = Decimal("0.05")
ROUNDOFF_TOLERANCE = Decimal("1.00")


class Check(NamedTuple):
    name: str
    passed: Optional[bool]      # None = not applicable (inputs absent)
    detail: str = ""


def _m(rec: Dict, key: str) -> Optional[Decimal]:
    v, ok = norm_money(rec.get(key))
    return v if ok else None


def _z(v: Optional[Decimal]) -> Decimal:
    return v if v is not None else Decimal("0")


def check_record(rec: Optional[Dict]) -> List[Check]:
    if not isinstance(rec, dict):
        return [Check("parseable", False, "output was not a JSON object")]

    checks: List[Check] = [Check("parseable", True)]
    items = rec.get("line_items") or []
    items = [i for i in items if isinstance(i, dict)]

    # 1. Line items sum to the declared taxable total.
    subtotal = _m(rec, "total_taxable_value")
    line_vals = [norm_money(i.get("taxable_value"))[0] for i in items]
    if subtotal is not None and items and all(v is not None for v in line_vals):
        tol = BASE_TOLERANCE * max(1, len(items))
        diff = abs(sum(line_vals) - subtotal)
        checks.append(Check("line_items_sum_to_subtotal", diff <= tol,
                            "sum=%s declared=%s" % (sum(line_vals), subtotal)))
    else:
        checks.append(Check("line_items_sum_to_subtotal", None))

    # 2. subtotal + taxes + round-off == grand total.
    grand = _m(rec, "grand_total")
    if subtotal is not None and grand is not None:
        computed = (subtotal + _z(_m(rec, "cgst_amount")) + _z(_m(rec, "sgst_amount"))
                    + _z(_m(rec, "igst_amount")) + _z(_m(rec, "cess_amount"))
                    + _z(_m(rec, "round_off")))
        checks.append(Check("totals_reconcile",
                            abs(computed - grand) <= ROUNDOFF_TOLERANCE,
                            "computed=%s declared=%s" % (computed, grand)))
    else:
        checks.append(Check("totals_reconcile", None))

    # 3. CGST and SGST are equal by construction, always, on every intra-state
    #    invoice. A model reporting different values has misread one of them.
    c, sg = _m(rec, "cgst_amount"), _m(rec, "sgst_amount")
    if c is not None and sg is not None and (c or sg):
        checks.append(Check("cgst_equals_sgst", abs(c - sg) <= BASE_TOLERANCE,
                            "cgst=%s sgst=%s" % (c, sg)))
    else:
        checks.append(Check("cgst_equals_sgst", None))

    # 4. CGST/SGST and IGST are mutually exclusive: a supply is either
    #    intra-state or inter-state, never both.
    ig = _m(rec, "igst_amount")
    if ig is not None and (c is not None or sg is not None):
        both = ig > 0 and (_z(c) > 0 or _z(sg) > 0)
        checks.append(Check("tax_type_exclusive", not both,
                            "igst=%s cgst=%s sgst=%s" % (ig, c, sg)))
    else:
        checks.append(Check("tax_type_exclusive", None))

    # 5. Per line: quantity x unit price - discount == taxable value.
    ok_lines = bad_lines = 0
    for i in items:
        q = norm_quantity(i.get("quantity"))[0]
        up = norm_money(i.get("unit_price"))[0]
        tv = norm_money(i.get("taxable_value"))[0]
        if q is None or up is None or tv is None:
            continue
        disc = _z(norm_money(i.get("discount"))[0])
        if abs((q * up - disc) - tv) <= Decimal("1.00"):
            ok_lines += 1
        else:
            bad_lines += 1
    checks.append(Check("line_arithmetic",
                        None if (ok_lines + bad_lines) == 0 else bad_lines == 0,
                        "%d ok / %d bad" % (ok_lines, bad_lines)))

    # 6. Tax computed at the stated rate matches the stated tax amount.
    rate_ok = rate_bad = 0
    for i in items:
        tv = norm_money(i.get("taxable_value"))[0]
        rate = norm_percent(i.get("tax_rate"))[0]
        if tv is None or rate is None:
            continue
        expected = (tv * rate / Decimal("100")).quantize(Decimal("0.01"))
        got = (_z(norm_money(i.get("cgst_amount"))[0])
               + _z(norm_money(i.get("sgst_amount"))[0])
               + _z(norm_money(i.get("igst_amount"))[0]))
        if got == 0:
            continue
        (rate_ok, rate_bad) = ((rate_ok + 1, rate_bad) if abs(expected - got) <= Decimal("1.00")
                               else (rate_ok, rate_bad + 1))
    checks.append(Check("line_tax_matches_rate",
                        None if (rate_ok + rate_bad) == 0 else rate_bad == 0,
                        "%d ok / %d bad" % (rate_ok, rate_bad)))

    # 7. GSTIN check digits. Catches character-level misreads with no reference.
    for who in ("seller_gstin", "buyer_gstin"):
        v = rec.get(who)
        checks.append(Check("%s_checksum" % who,
                            None if not v else gstin_is_valid(str(v)), str(v)))

    # 8. Place of supply consistency: on an intra-state invoice (CGST+SGST) the
    #    seller's state must equal the place of supply.
    pos, _ = norm_state(rec.get("place_of_supply"))
    seller_state = gstin_state_code(rec.get("seller_gstin"))
    if pos and seller_state and (_z(c) > 0 or _z(sg) > 0):
        checks.append(Check("intrastate_pos_matches_seller", pos == seller_state,
                            "pos=%s seller=%s" % (pos, seller_state)))
    elif pos and seller_state and _z(ig) > 0:
        checks.append(Check("interstate_pos_differs_from_seller", pos != seller_state,
                            "pos=%s seller=%s" % (pos, seller_state)))
    else:
        checks.append(Check("intrastate_pos_matches_seller", None))

    # 9. Round-off is a rounding adjustment, so it lives in (-1, 1) rupees.
    ro = _m(rec, "round_off")
    checks.append(Check("round_off_plausible",
                        None if ro is None else abs(ro) < Decimal("1.00"), str(ro)))

    # 10. Date parses at all.
    d, ok = norm_date(rec.get("invoice_date"))
    checks.append(Check("date_parseable", None if rec.get("invoice_date") is None else ok,
                        str(rec.get("invoice_date"))))

    return checks


def consistency_score(rec: Optional[Dict]) -> float:
    """Fraction of applicable checks passed. NaN when nothing applies."""
    cs = [c for c in check_record(rec) if c.passed is not None]
    return (sum(1 for c in cs if c.passed) / len(cs)) if cs else float("nan")
