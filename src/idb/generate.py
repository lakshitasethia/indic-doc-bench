"""Synthetic GST invoice generator.

Produces (ground_truth_record, render_context) pairs. Ground truth is exact by
construction -- there is no annotator and therefore no annotator error, and
because the documents are generated they cannot appear in any model's training
corpus. Both properties are stated in the README as methodological claims, so
they must hold literally: nothing in here reads from an external document.

The tax arithmetic reconciles exactly, including the round-off line that real
Indian invoices carry. Getting round-off right matters more than it looks: omit
it and every self-consistency check fires spuriously on ~70% of documents,
which would make the most novel metric in the benchmark meaningless.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from .india import (BUSY_STATES, GOODS, SERVICES, STATE_CODES, address,
                    company_name, make_gstin)
from .words import rupees_in_words

Q2 = Decimal("0.01")
Q3 = Decimal("0.001")


def _q(v, q=Q2) -> Decimal:
    return Decimal(v).quantize(q, rounding=ROUND_HALF_UP)


# Invoice-number styles seen in the wild: Tally sequential, financial-year
# prefixed, branch-coded, and plain.
_INV_STYLES = [
    lambda r, n: "INV-%04d" % n,
    lambda r, n: "%s/%02d-%02d/%04d" % (r.choice(["INV", "TI", "GST", "SI"]), 25, 26, n),
    lambda r, n: "%s%04d" % (r.choice(["MH", "KA", "DL", "TN", "GJ"]), n),
    lambda r, n: "%04d" % n,
    lambda r, n: "%s-%s-%04d" % (r.choice(["SALE", "TAX"]), r.choice(["A", "B", "C"]), n),
    lambda r, n: "2025-26/%04d" % n,
]


def _line_count(rng: random.Random) -> int:
    """Skewed toward short invoices, with a long tail. The tail is deliberate:
    long line-item tables are where output truncation and merge/split errors
    appear, and a corpus of 3-line invoices would never surface them."""
    roll = rng.random()
    if roll < 0.45:
        return rng.randint(1, 3)
    if roll < 0.80:
        return rng.randint(4, 8)
    if roll < 0.95:
        return rng.randint(9, 16)
    return rng.randint(17, 28)


def generate_invoice(seed: int, template_id: str = "t01",
                     force_interstate: Optional[bool] = None,
                     force_line_count: Optional[int] = None) -> Tuple[Dict, Dict]:
    """`force_line_count` overrides the natural length distribution.

    Used to build a targeted long-table probe. The natural distribution puts
    only ~5% of documents above 16 line items, which is realistic but leaves
    the regime that breaks header totals (METHODOLOGY 8b) too thinly sampled
    to measure. Everything else about generation is unchanged, so a forced
    document differs from a natural one in exactly one respect."""
    rng = random.Random(seed)

    seller_state = rng.choice(BUSY_STATES)
    interstate = (rng.random() < 0.40) if force_interstate is None else force_interstate
    if interstate:
        buyer_state = rng.choice([s for s in BUSY_STATES if s != seller_state])
    else:
        buyer_state = seller_state

    seller = company_name(rng)
    buyer = company_name(rng)
    seller_addr = address(rng, seller_state)
    buyer_addr = address(rng, buyer_state)
    seller_gstin = make_gstin(rng, seller_state, seller[0], rng.choice(["C", "F", "P"]))

    # ~15% of invoices are B2C / unregistered buyers: buyer GSTIN legitimately
    # absent. These are the cases that separate a model that returns null from
    # one that fabricates a GSTIN.
    b2c = rng.random() < 0.15
    buyer_gstin = None if b2c else make_gstin(rng, buyer_state, buyer[0],
                                              rng.choice(["C", "F", "P"]))

    inv_date = date(2025, 4, 1) + timedelta(days=rng.randint(0, 364))
    inv_no = rng.choice(_INV_STYLES)(rng, rng.randint(1, 9999))

    is_service = rng.random() < 0.18
    catalogue = SERVICES if is_service else GOODS
    n_lines = 1 if is_service and rng.random() < 0.6 else _line_count(rng)
    if force_line_count is not None:
        # Drawn from the same rng either way, so the override changes the
        # table length without shifting every later random draw.
        n_lines = force_line_count

    items: List[Dict[str, Any]] = []
    chosen = rng.sample(catalogue, min(n_lines, len(catalogue)))
    while len(chosen) < n_lines:
        chosen.append(rng.choice(catalogue))

    for c in chosen:
        price = _q(rng.uniform(c.price_low, c.price_high))
        # Quantity is derived from a target line value rather than drawn
        # independently, so a line of cement and a line of laptops land in
        # comparable rupee ranges. Drawing quantity independently pushes every
        # high-unit-price line into the crores and leaves the corpus with no
        # three- or four-digit amounts at all -- which would silently stop the
        # benchmark from ever testing short-number parsing.
        target = Decimal(str(10 ** rng.uniform(2.7, 5.6)))   # ~Rs 500 - 4,00,000
        raw_qty = target / price
        if c.unit in ("KGS", "MTR", "LTR", "TON", "SQM"):
            qty = max(Decimal("0.100"), _q(raw_qty, Q3))
        else:
            qty = Decimal(max(1, min(9999, int(raw_qty.to_integral_value()))))
        gross = _q(qty * price)
        discount = _q(gross * Decimal(rng.choice([0, 0, 0, 0.02, 0.05, 0.10]))) if rng.random() < 0.25 else Decimal("0.00")
        taxable = _q(gross - discount)
        rate = Decimal(str(c.rate))
        if interstate:
            cgst = sgst = Decimal("0.00")
            igst = _q(taxable * rate / 100)
        else:
            cgst = sgst = _q(taxable * rate / 200)
            igst = Decimal("0.00")
        items.append({
            "description": c.description,
            "hsn_sac": c.hsn,
            "quantity": qty,
            "unit": c.unit,
            "unit_price": price,
            "discount": discount if discount > 0 else None,
            "taxable_value": taxable,
            "tax_rate": rate,
            "_cgst": cgst, "_sgst": sgst, "_igst": igst,
            "_gross": gross,
        })

    subtotal = _q(sum(i["taxable_value"] for i in items))
    cgst_total = _q(sum(i["_cgst"] for i in items))
    sgst_total = _q(sum(i["_sgst"] for i in items))
    igst_total = _q(sum(i["_igst"] for i in items))
    pre_round = _q(subtotal + cgst_total + sgst_total + igst_total)
    grand = Decimal(int(pre_round.to_integral_value(rounding=ROUND_HALF_UP)))
    round_off = _q(grand - pre_round)
    grand = _q(grand)

    has_irn = rng.random() < 0.30
    irn = "".join(rng.choice("0123456789abcdef") for _ in range(64)) if has_irn else None
    reverse_charge = rng.random() < 0.07

    record: Dict[str, Any] = {
        "invoice_number": inv_no,
        "invoice_date": inv_date.isoformat(),
        "seller_name": seller,
        "seller_gstin": seller_gstin,
        "seller_address": seller_addr["full"],
        "buyer_name": buyer,
        "buyer_gstin": buyer_gstin,
        "buyer_address": buyer_addr["full"],
        "place_of_supply": "%s-%s" % (buyer_state, STATE_CODES[buyer_state]),
        "reverse_charge": reverse_charge,
        "irn": irn,
        "line_items": [{k: v for k, v in i.items() if not k.startswith("_")}
                       for i in items],
        "total_taxable_value": subtotal,
        "cgst_amount": cgst_total if not interstate else None,
        "sgst_amount": sgst_total if not interstate else None,
        "igst_amount": igst_total if interstate else None,
        "cess_amount": None,
        "round_off": round_off,
        "grand_total": grand,
        "amount_in_words": rupees_in_words(grand),
    }

    context: Dict[str, Any] = {
        "record": record,
        "items": items,
        "seller_addr": seller_addr,
        "buyer_addr": buyer_addr,
        "interstate": interstate,
        "b2c": b2c,
        "is_service": is_service,
        "bank": {
            "name": rng.choice(["HDFC Bank", "ICICI Bank", "State Bank of India",
                                "Axis Bank", "Kotak Mahindra Bank", "Bank of Baroda"]),
            "account": "".join(rng.choice("0123456789") for _ in range(rng.choice([11, 14, 16]))),
            "ifsc": "%s0%06d" % ("".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4)),
                                 rng.randint(0, 999999)),
            "branch": buyer_addr["city"],
        },
        "meta": {
            "seed": seed,
            "template_id": template_id,
            "interstate": interstate,
            "b2c": b2c,
            "is_service": is_service,
            "n_line_items": len(items),
            "has_irn": has_irn,
            "reverse_charge": reverse_charge,
            "grand_total": str(grand),
        },
    }
    return record, context


def to_json_safe(rec: Dict) -> Dict:
    """Decimals -> strings, so ground truth round-trips through JSON without
    ever becoming a float. Money is never a float anywhere in this project."""
    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v
    return conv(rec)
