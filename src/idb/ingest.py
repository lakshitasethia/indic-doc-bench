"""Bring real documents into the benchmark (Layer 3).

Layers 1 and 2 are generated, so their ground truth is exact by construction --
there is no annotator and therefore no annotator error. Layer 3 inverts that.
The documents are real and the *labels* become the new error source, so the
work here is mostly about not trusting them.

Three things are different from the synthetic path and each one shapes the code:

  **There is no seed.** A real document cannot be regenerated, so the image is
  the artifact and it must be version-controlled or backed up. Synthetic
  renders are gitignored precisely because they rebuild from seeds; that
  reasoning does not transfer, and losing a real corpus means re-collecting it.

  **There are no severity levels.** A photograph of a bill is already at
  whatever quality it is. Inventing an `L0_clean` for it would be a lie, so
  real documents carry a single `real` level and are excluded from the
  degradation curve rather than pretending to sit on it.

  **The labels can be wrong.** `validate_label` runs every hand-label through
  the schema *and* through the same ground-truth-free arithmetic checks used on
  model output. A label whose CGST and SGST do not reconcile to the taxable
  value is far more likely to be a labelling slip than a genuine oddity, and
  catching it here costs minutes. Not catching it means every model is scored
  as wrong on that field forever, and the benchmark reports a model failure
  that is really a typing failure.

Nothing in this module obtains documents, secures permission, or decides what
is personal data. Those are human judgements and deliberately absent.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .consistency import check_record, consistency_score
from .schema import (ALL_HEADER, LINE_ITEM_FIELDS, SCHEMA_VERSION,
                     empty_record)

REAL_LEVEL = "real"

# Fields that most often carry personal data on a real invoice. Used only to
# generate a checklist -- redaction itself is a human decision and this list is
# a prompt for that decision, never a substitute for reading the document.
REDACTION_PROMPTS = {
    "seller_name": "trading name is usually fine; a sole proprietor's personal name may not be",
    "buyer_name": "a B2C buyer is an individual -- consider replacing with a placeholder",
    "seller_address": "check for a residential address",
    "buyer_address": "check for a residential address",
    "seller_gstin": "a real GSTIN identifies a real taxpayer",
    "buyer_gstin": "same, and B2C invoices may carry a personal phone or email nearby",
}


def label_template(n_line_items: int = 1) -> Dict:
    """A blank label with every field present, ready to fill in.

    Every key is emitted, including the nullable ones, because a labeller who
    has to remember which fields exist will omit the ones the document does not
    obviously show -- and an omitted key is indistinguishable from a field
    genuinely absent from the document.
    """
    rec = empty_record()
    rec["line_items"] = [{f.name: None for f in LINE_ITEM_FIELDS}
                         for _ in range(max(1, n_line_items))]
    return rec


def validate_label(record: Optional[Dict], strict_arithmetic: bool = False
                   ) -> Tuple[List[str], List[str]]:
    """Check one hand-label. Returns (errors, warnings).

    Errors are structural and block ingestion. Warnings are arithmetic: a real
    invoice can genuinely fail to reconcile -- rounding conventions vary, and
    some suppliers really do print totals that do not add up -- so a failed
    check is a prompt to look again, not proof of a mistake. `strict_arithmetic`
    promotes them for a corpus where that has been ruled out.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(record, dict):
        return ["label is not a JSON object"], []

    known = {f.name for f in ALL_HEADER} | {"line_items"}
    for key in record:
        if key.startswith("_"):
            continue          # `_meta` and friends: labeller notes, not schema
        if key not in known:
            errors.append("unknown field %r (schema is %s)" % (key, SCHEMA_VERSION))
    for f in ALL_HEADER:
        if f.name not in record:
            errors.append("missing field %r -- emit null rather than omitting it, "
                          "so 'absent from the document' is distinguishable from "
                          "'forgot to label'" % f.name)
        elif record[f.name] is None and not f.nullable:
            errors.append("field %r is null but the schema says it cannot be" % f.name)

    items = record.get("line_items")
    if not isinstance(items, list):
        errors.append("line_items must be a list")
    elif not items:
        warnings.append("no line items -- correct for some receipts, worth confirming")
    else:
        line_known = {f.name for f in LINE_ITEM_FIELDS}
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append("line_items[%d] is not an object" % i)
                continue
            for key in item:
                if key not in line_known:
                    errors.append("line_items[%d]: unknown field %r" % (i, key))
            for f in LINE_ITEM_FIELDS:
                if f.name not in item:
                    errors.append("line_items[%d]: missing field %r" % (i, f.name))

    if not errors:
        # Only meaningful once the shape is right.
        for check in check_record(record):
            if check.passed is False:
                (errors if strict_arithmetic else warnings).append(
                    "arithmetic check %r failed: %s" % (check.name, check.detail))
                if check.name == "line_arithmetic":
                    warnings.extend("  -> %s" % h
                                    for h in diagnose_line_arithmetic(record))
    return errors, warnings


def diagnose_line_arithmetic(record: Dict) -> List[str]:
    """Explain *why* a line fails `qty x unit_price - discount == taxable_value`.

    The bare check reports "0 ok / 1 bad", which tells a labeller nothing. Most
    failures are one of a few recurring conventions, and naming the convention
    turns a mystery into an instruction.

    The common one, found on the first real invoice ingested: marketplaces
    print a tax-INCLUSIVE line price. Meesho shows Gross 544.00, Discount
    29.00, Taxable 490.48 -- because 544 - 29 = 515 is the tax-inclusive total
    and 515 / 1.05 = 490.48. Copy "Gross" into `unit_price` and the line is off
    by 24.52, which is exactly the tax. The schema defines `unit_price` as
    tax-exclusive, so the fix is to label the exclusive figure.

    That give-away -- off by precisely the tax -- is invisible unless something
    checks for it, and the resulting label would otherwise be scored as a model
    error on every model forever.
    """
    from .normalize import norm_money, norm_percent, norm_quantity

    hints: List[str] = []
    items = record.get("line_items")
    if not isinstance(items, list):
        return hints

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        qty = norm_quantity(item.get("quantity"))[0]
        price = norm_money(item.get("unit_price"))[0]
        disc = norm_money(item.get("discount"))[0] or Decimal("0")
        taxable = norm_money(item.get("taxable_value"))[0]
        rate = norm_percent(item.get("tax_rate"))[0]
        if qty is None or price is None or taxable is None:
            continue

        computed = qty * price - disc
        if abs(computed - taxable) <= Decimal("1.00"):
            continue

        gap = computed - taxable
        if rate and rate > 0:
            tax = (taxable * rate / Decimal("100"))
            if abs(gap - tax) <= Decimal("1.00"):
                hints.append(
                    "line %d: off by %.2f, which is exactly the tax at %s%%. This "
                    "invoice prints tax-INCLUSIVE line prices; the schema wants "
                    "unit_price tax-exclusive. Label unit_price=%.2f (and move any "
                    "discount into that figure rather than repeating it)."
                    % (idx + 1, gap, rate.normalize(), taxable / qty))
                continue
        if qty > 1 and abs(price - taxable) <= Decimal("1.00"):
            hints.append(
                "line %d: unit_price equals the line total. It is the price of ONE "
                "unit; label %.2f, not %.2f." % (idx + 1, taxable / qty, price))
            continue
        if disc and abs((qty * price) - taxable) <= Decimal("1.00"):
            hints.append(
                "line %d: reconciles only if the discount is dropped -- it is "
                "probably already reflected in the printed taxable value, so "
                "labelling it again subtracts it twice." % (idx + 1))
            continue
        hints.append("line %d: qty x unit_price - discount = %.2f but taxable_value "
                     "is %.2f (off by %.2f)." % (idx + 1, computed, taxable, gap))
    return hints


def _sha256(path: pathlib.Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(p: pathlib.Path, root: pathlib.Path) -> str:
    p = pathlib.Path(p).resolve()
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def render_pdf(pdf: pathlib.Path, out_dir: pathlib.Path, dpi: int = 200) -> List[pathlib.Path]:
    """Rasterise a PDF so a vision model can actually be sent it.

    Real documents arrive as PDFs far more often than as photographs -- a
    marketplace invoice, a restaurant bill emailed by the POS. But the image
    APIs take image/*, and `mimetypes.guess_type` on a .pdf yields
    application/pdf, which they reject. Left unrendered the sweep would fail
    every real document with a transport error and, thanks to the infrastructure
    -failure rule, score nothing at all.

    Rendered at 200 DPI: high enough that the smallest printed tax line stays
    legible, low enough to stay well inside per-image size limits. Every page is
    kept, because a two-page bill really does carry the tax summary on page two.
    """
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    pages: List[pathlib.Path] = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            dest = out_dir / ("%s_p%d.png" % (pdf.stem, i + 1))
            page.get_pixmap(dpi=dpi).save(dest)
            pages.append(dest)
    return pages


def discover(inbox: pathlib.Path) -> List[Tuple[pathlib.Path, pathlib.Path]]:
    """Find (image, label) pairs in an inbox directory, matched by stem.

    A document with no label is reported rather than skipped silently: an
    unlabelled file in the inbox is almost always work in progress, and
    quietly ignoring it is how a document goes missing from a corpus nobody
    can regenerate.
    """
    inbox = pathlib.Path(inbox)
    images, pairs = {}, []
    for p in sorted(inbox.iterdir()) if inbox.exists() else []:
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf", ".webp", ".tif", ".tiff"):
            images[p.stem] = p
    for stem, img in sorted(images.items()):
        pairs.append((img, inbox / ("%s.json" % stem)))
    return pairs


def _doc_id_for(img: pathlib.Path, prefix: str) -> str:
    """Stable id from the source filename, safe for use in a result filename."""
    import re
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", img.stem).strip("_") or "doc"
    return stem if stem.startswith(prefix) else "%s_%s" % (prefix, stem)


def build_real_manifest(inbox: pathlib.Path, root: pathlib.Path,
                        strict_arithmetic: bool = False,
                        doc_prefix: str = "real") -> Tuple[Dict, Dict[str, List[str]]]:
    """Validate and register every labelled document in `inbox`.

    Returns (manifest, report) where report has "errors", "warnings" and
    "rejected" (document names, not message counts -- one bad label produces
    several messages and reporting those as several rejections is misleading).
    A manifest is produced only from documents that validated: a corpus that
    silently contains a malformed label is worse than a smaller one.
    """
    docs: List[Dict] = []
    problems: List[str] = []
    warn_lines: List[str] = []
    rejected: List[str] = []
    seen_ids: Dict[str, str] = {}
    for i, (img, label_path) in enumerate(discover(inbox)):
        if not label_path.exists():
            problems.append("%s: no label file (%s)" % (img.name, label_path.name))
            rejected.append(img.name)
            continue
        try:
            record = json.loads(label_path.read_text())
        except json.JSONDecodeError as e:
            problems.append("%s: label is not valid JSON (%s)" % (label_path.name, e))
            rejected.append(img.name)
            continue

        errors, warnings = validate_label(record, strict_arithmetic)
        for w in warnings:
            warn_lines.append("%s: %s" % (img.name, w))
        if errors:
            problems.extend("%s: %s" % (img.name, e) for e in errors)
            rejected.append(img.name)
            continue

        # Derived from the filename, never from enumeration order. A counter
        # renumbers every later document the moment one is added earlier in the
        # sort, which silently invalidates every result already keyed by the
        # old ids -- and a real corpus grows one document at a time, so that
        # would happen constantly.
        doc_id = _doc_id_for(img, doc_prefix)
        # A PDF is rendered to page images; anything already an image is used
        # as it is. `source_file` keeps the pointer back to what was collected.
        if img.suffix.lower() == ".pdf":
            try:
                files = render_pdf(img, inbox / "rendered")
            except Exception as e:
                problems.append("%s: could not render PDF (%s: %s)"
                                % (img.name, type(e).__name__, e))
                rejected.append(img.name)
                continue
        else:
            files = [img]
        if doc_id in seen_ids:
            problems.append("%s: document id %r already taken by %s -- rename one"
                            % (img.name, doc_id, seen_ids[doc_id]))
            rejected.append(img.name)
            continue
        seen_ids[doc_id] = img.name
        meta = record.pop("_meta", {}) if isinstance(record.get("_meta"), dict) else {}
        record = {k: v for k, v in record.items() if not k.startswith("_")}

        # Several bills from one supplier share a layout and are NOT
        # independent draws. Template is the resampling unit for the synthetic
        # corpus for exactly that reason; the real corpus needs the same
        # treatment, but only the labeller knows which documents came off the
        # same template. Declaring `_meta.layout_group` groups them; the
        # default of one-group-per-document assumes independence, which is the
        # right default but is an assumption, not a fact.
        docs.append({
            "doc_id": doc_id,
            "source": "real",
            "source_file": img.name,
            "schema_version": SCHEMA_VERSION,
            "template_id": meta.get("layout_group") or doc_id,
            "meta": meta,
            "consistency_score": consistency_score(record),
            "ground_truth": record,
            "variants": {REAL_LEVEL: {
                "files": [_rel(f, root) for f in files],
                "sha256": [_sha256(f) for f in files],
                "params": {"note": "as collected; no synthetic degradation applied",
                           "rendered_from_pdf": img.suffix.lower() == ".pdf",
                           "n_pages": len(files)},
            }},
        })

    manifest = {
        "corpus_version": "real-v1",
        "n_documents": len(docs),
        "levels": [REAL_LEVEL],
        "templates": [],
        "documents": docs,
    }
    return manifest, {"errors": problems, "warnings": warn_lines,
                      "rejected": rejected}


def redaction_checklist() -> str:
    lines = ["Redaction checklist -- read the document, do not trust this list.", ""]
    for name, why in REDACTION_PROMPTS.items():
        lines.append("  [ ] %-16s %s" % (name, why))
    lines += [
        "",
        "  [ ] phone numbers, email addresses and bank/UPI details anywhere on the page",
        "  [ ] signatures and stamps",
        "  [ ] any handwriting in the margins",
        "",
        "Aadhaar, PAN, voter ID and passport documents are out of scope entirely.",
        "If one appears inside an invoice, that document does not enter the corpus.",
        "",
        "Redact the IMAGE, not just the label: the image is what a model is sent.",
    ]
    return "\n".join(lines)
