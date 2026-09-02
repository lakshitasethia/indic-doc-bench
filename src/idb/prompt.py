"""The extraction prompt. Frozen and versioned: changing it invalidates results.

One prompt for every model. Per-model prompt tuning would turn the benchmark
into a measure of how much effort was spent on each vendor, which is exactly
the confound that makes most public model comparisons unusable.

The instruction block is identical across every call and sits first, so it is
cache-eligible on providers that support prompt caching -- which is also why
cost is reported both cached and uncached.
"""
from __future__ import annotations

import json

from .schema import SCHEMA_VERSION, json_schema

PROMPT_VERSION = "p1"

SYSTEM = """You are a document data extraction system. You extract structured data from Indian GST tax invoices and return JSON. You never explain, never apologise, and never wrap the JSON in prose."""

_RULES = """Extract the fields defined by the JSON Schema below from the invoice image.

Rules:
1. Return a single JSON object and nothing else. No markdown fences, no commentary.
2. Transcribe what is printed. Do not compute, correct, or infer a value that is
   not on the document.
3. If a field is not present on the document, return null for it. Returning null
   is always better than guessing. A wrong value is worse than no value.
4. Amounts: digits only, no currency symbol and no thousands separators
   (write 1234567.89, not Rs. 12,34,567.89).
5. Dates: ISO-8601, YYYY-MM-DD. Indian invoices are day-first, so 05/03/2026 is
   5 March 2026.
6. tax_rate is the total GST percentage for the line (write 18, not 0.18 and not 9).
7. Include every line item, in the order printed. Do not merge two printed rows
   into one, and do not split one printed row into two.
8. On an intra-state invoice IGST does not exist: return null for igst_amount,
   not 0 and not a made-up figure. Likewise CGST and SGST on an inter-state
   invoice.
9. buyer_gstin is null when the buyer is unregistered (the document may say
   "URP", "Unregistered", or leave it blank).

JSON Schema:
"""


def build_prompt() -> str:
    return _RULES + json.dumps(json_schema(), indent=2)


def prompt_fingerprint() -> dict:
    """Recorded with every run. If any of these change, the run is not
    comparable with earlier runs and the analysis must say so."""
    import hashlib
    body = SYSTEM + build_prompt()
    return {
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
    }
