"""The extraction schema (v1) for Indian GST tax invoices.

This module is the single source of truth. It defines, per field:
  * the normalisation/comparison type used at scoring time
  * whether the field may legitimately be null
  * whether an explicit 0 is equivalent to null (matters enormously for
    CGST/SGST/IGST: on an intra-state invoice IGST is genuinely absent, and a
    model returning 0.00 must not be marked wrong for it)
  * the scoring weight (all 1.0 by default; kept explicit so a weighted
    "business-critical fields" view can be reported alongside the flat one)

Schema changes are versioned. Never mutate v1 in place once a sweep has run
against it — add v2 and re-score, otherwise published numbers stop being
comparable.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

SCHEMA_VERSION = "gst-invoice-v1"


class FieldType(object):
    EXACT_UPPER = "exact_upper"     # strip ws, uppercase, then exact
    EXACT_ALNUM = "exact_alnum"     # additionally drop non-alphanumerics
    DATE = "date"                   # parse to ISO-8601, then exact
    MONEY = "money"                 # Decimal, 2dp, exact
    QUANTITY = "quantity"           # Decimal, 3dp, exact
    PERCENT = "percent"             # Decimal, 2dp, exact ("18", "18%", "18.00")
    FUZZY = "fuzzy"                 # token-set ratio >= threshold
    STATE = "state"                 # resolve to a GST state code, then exact
    BOOL = "bool"
    LINE_ITEMS = "line_items"       # aligned via Hungarian matching


class Field(NamedTuple):
    name: str
    ftype: str
    nullable: bool = False
    null_equiv_zero: bool = False
    weight: float = 1.0
    critical: bool = False   # fields a business cannot post to the ledger without
    description: str = ""


HEADER_FIELDS: List[Field] = [
    Field("invoice_number", FieldType.EXACT_UPPER, critical=True,
          description="Document/serial number exactly as printed."),
    Field("invoice_date", FieldType.DATE, critical=True,
          description="Invoice date, ISO-8601 (YYYY-MM-DD)."),
    Field("seller_name", FieldType.FUZZY, critical=True,
          description="Legal name of the supplier."),
    Field("seller_gstin", FieldType.EXACT_UPPER, critical=True,
          description="15-character GSTIN of the supplier."),
    Field("seller_address", FieldType.FUZZY, nullable=True,
          description="Full supplier address as printed."),
    Field("buyer_name", FieldType.FUZZY, critical=True,
          description="Legal name of the recipient."),
    Field("buyer_gstin", FieldType.EXACT_UPPER, nullable=True, critical=True,
          description="GSTIN of the recipient; null for B2C/unregistered."),
    Field("buyer_address", FieldType.FUZZY, nullable=True,
          description="Billing address of the recipient."),
    Field("place_of_supply", FieldType.STATE, nullable=True,
          description="State of supply; determines CGST+SGST vs IGST."),
    Field("reverse_charge", FieldType.BOOL, nullable=True,
          description="Whether tax is payable under reverse charge."),
    Field("irn", FieldType.EXACT_UPPER, nullable=True,
          description="64-char e-invoice reference number, if present."),
]

TOTAL_FIELDS: List[Field] = [
    Field("total_taxable_value", FieldType.MONEY, critical=True,
          description="Sum of taxable values before tax."),
    Field("cgst_amount", FieldType.MONEY, nullable=True, null_equiv_zero=True, critical=True),
    Field("sgst_amount", FieldType.MONEY, nullable=True, null_equiv_zero=True, critical=True),
    Field("igst_amount", FieldType.MONEY, nullable=True, null_equiv_zero=True, critical=True),
    Field("cess_amount", FieldType.MONEY, nullable=True, null_equiv_zero=True),
    Field("round_off", FieldType.MONEY, nullable=True, null_equiv_zero=True,
          description="Rounding adjustment; may be negative."),
    Field("grand_total", FieldType.MONEY, critical=True,
          description="Final payable amount including all taxes."),
    Field("amount_in_words", FieldType.FUZZY, nullable=True),
]

LINE_ITEM_FIELDS: List[Field] = [
    Field("description", FieldType.FUZZY, weight=1.0),
    Field("hsn_sac", FieldType.EXACT_ALNUM, nullable=True, critical=True),
    Field("quantity", FieldType.QUANTITY, nullable=True),
    Field("unit", FieldType.EXACT_UPPER, nullable=True),
    Field("unit_price", FieldType.MONEY, nullable=True),
    Field("discount", FieldType.MONEY, nullable=True, null_equiv_zero=True),
    Field("taxable_value", FieldType.MONEY, critical=True),
    Field("tax_rate", FieldType.PERCENT, nullable=True, critical=True,
          description="Total GST rate for the line, in percent (e.g. 18)."),
]

ALL_HEADER = HEADER_FIELDS + TOTAL_FIELDS
FIELD_BY_NAME: Dict[str, Field] = {f.name: f for f in ALL_HEADER}
LINE_FIELD_BY_NAME: Dict[str, Field] = {f.name: f for f in LINE_ITEM_FIELDS}

# Fields used to build the line-item alignment cost matrix, with weights.
# Deliberately excludes tax amounts: those are the most-often-wrong values, and
# aligning on them would let a scoring error cascade into a matching error.
ALIGNMENT_WEIGHTS: Dict[str, float] = {
    "description": 0.40,
    "hsn_sac": 0.20,
    "taxable_value": 0.25,
    "quantity": 0.075,
    "unit_price": 0.075,
}


def json_schema() -> dict:
    """JSON Schema served to models in the prompt and published in the repo."""
    def prop(f: Field) -> dict:
        base = {
            FieldType.EXACT_UPPER: {"type": "string"},
            FieldType.EXACT_ALNUM: {"type": "string"},
            FieldType.DATE: {"type": "string", "format": "date",
                             "description": "ISO-8601 YYYY-MM-DD"},
            FieldType.MONEY: {"type": "number"},
            FieldType.QUANTITY: {"type": "number"},
            FieldType.PERCENT: {"type": "number"},
            FieldType.FUZZY: {"type": "string"},
            FieldType.STATE: {"type": "string"},
            FieldType.BOOL: {"type": "boolean"},
        }[f.ftype]
        out = dict(base)
        if f.description:
            out["description"] = f.description
        if f.nullable:
            out["type"] = [out["type"], "null"]
        return out

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": [f.name for f in ALL_HEADER] + ["line_items"],
        "properties": dict(
            [(f.name, prop(f)) for f in HEADER_FIELDS]
            + [("line_items", {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [f.name for f in LINE_ITEM_FIELDS],
                    "properties": {f.name: prop(f) for f in LINE_ITEM_FIELDS},
                },
            })]
            + [(f.name, prop(f)) for f in TOTAL_FIELDS]
        ),
    }


def empty_record() -> dict:
    rec = {f.name: None for f in ALL_HEADER}
    rec["line_items"] = []
    return rec
