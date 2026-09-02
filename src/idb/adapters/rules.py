"""OCR + regex/rules baseline. Non-negotiable, and the most useful row in the table.

It grounds every other number. If rules match a frontier model on clean
synthetic invoices at a hundredth of the cost and only collapse under
degradation, that is a more useful finding for a practitioner than any ranking
of frontier models -- and it is a finding that only exists if the baseline is
actually built.

Deliberately unsophisticated: labelled-field regexes, a GSTIN pattern, a date
pattern, and a "largest amount near the word total" heuristic. Making it
cleverer would turn it into an unlabelled research project and stop it being a
floor.

Requires an OCR engine:  brew install tesseract  (or set OCR_ENGINE=paddle)
"""
from __future__ import annotations

import os
import pathlib
import re
import time
from decimal import Decimal
from typing import Dict, List, Optional

from ..india import STATE_CODES, gstin_is_valid
from ..normalize import norm_date, norm_money
from .base import Adapter, ModelResponse

GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z][Z][0-9A-Z]\b")
DATE_RE = re.compile(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
                     r"|\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{2,4}"
                     r"|\d{4}-\d{2}-\d{2})\b")
INV_RE = re.compile(r"(?:invoice|bill|document)\s*(?:no|number|#)\s*[:.\-]?\s*"
                    r"([A-Z0-9][A-Z0-9/\-]{2,24})", re.IGNORECASE)
# The comma-grouped alternative must come first AND require at least one comma.
# With `,\d{2,3})*` (zero or more) the first branch matched a bare "400" out of
# "4001500.00" and won, truncating every un-grouped amount to three digits --
# which would have made the baseline look far worse than the approach deserves.
AMOUNT_RE = re.compile(
    r"(?:(?:\u20b9|Rs\.?|INR)\s*)?(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
HSN_RE = re.compile(r"\b(\d{4}|\d{6}|\d{8})\b")


def ocr_text(path: pathlib.Path) -> str:
    engine = os.environ.get("OCR_ENGINE", "tesseract")
    if engine == "tesseract":
        import subprocess
        out = subprocess.run(["tesseract", str(path), "stdout", "--psm", "6"],
                             capture_output=True, text=True, timeout=180)
        return out.stdout
    if engine == "paddle":
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        res = ocr.ocr(str(path), cls=True)
        return "\n".join(line[1][0] for page in (res or []) for line in (page or []))
    raise ValueError("unknown OCR_ENGINE %r" % engine)


def _amount_after(text: str, labels: List[str]) -> Optional[str]:
    for lab in labels:
        for m in re.finditer(re.escape(lab), text, re.IGNORECASE):
            tail = text[m.end():m.end() + 60]
            a = AMOUNT_RE.search(tail)
            if a:
                v, ok = norm_money(a.group(1))
                if ok and v is not None:
                    return str(v)
    return None


class RulesBaseline(Adapter):
    name = "ocr-rules-v1"
    architecture = "rules"
    price_in_per_mtok = 0.0
    price_out_per_mtok = 0.0

    def extract(self, image_path: pathlib.Path) -> ModelResponse:
        t0 = time.time()
        try:
            text = ocr_text(pathlib.Path(image_path))
        except Exception as e:
            return ModelResponse(None, "", 0, 0, 0, time.time() - t0,
                                 error="%s: %s" % (type(e).__name__, e))

        gstins = [g for g in GSTIN_RE.findall(text)]
        # Order of appearance: the supplier's GSTIN is printed first on every
        # layout in this corpus. A crude rule, and stating it crudely is the
        # point -- this is the floor, not a contender.
        seller = gstins[0] if gstins else None
        buyer = next((g for g in gstins[1:] if g != seller), None)

        dates = DATE_RE.findall(text)
        inv_date = None
        for d in dates:
            iso, ok = norm_date(d)
            if ok:
                inv_date = iso
                break

        inv_no = None
        m = INV_RE.search(text)
        if m:
            inv_no = m.group(1).strip(" .:-")

        state_code = seller[:2] if seller and seller[:2] in STATE_CODES else None
        rec: Dict = {
            "invoice_number": inv_no,
            "invoice_date": inv_date,
            "seller_name": (text.strip().splitlines() or [None])[0],
            "seller_gstin": seller,
            "seller_address": None,
            "buyer_name": None,
            "buyer_gstin": buyer,
            "buyer_address": None,
            "place_of_supply": STATE_CODES.get(state_code) if state_code else None,
            "reverse_charge": None,
            "irn": None,
            "line_items": [],
            "total_taxable_value": _amount_after(text, ["taxable value", "sub total",
                                                        "subtotal", "total value"]),
            "cgst_amount": _amount_after(text, ["cgst"]),
            "sgst_amount": _amount_after(text, ["sgst"]),
            "igst_amount": _amount_after(text, ["igst"]),
            "cess_amount": None,
            "round_off": _amount_after(text, ["round off", "rounding", "round-off"]),
            "grand_total": _amount_after(text, ["grand total", "net payable",
                                                "total invoice value", "amount payable"]),
            "amount_in_words": None,
        }
        raw = text
        return ModelResponse(rec, raw, 0, 0, 0, time.time() - t0,
                             finish_reason="stop",
                             extra={"ocr_chars": len(text), "gstins_found": len(gstins)})
