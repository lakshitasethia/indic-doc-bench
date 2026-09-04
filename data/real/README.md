# Layer 3 — real documents

Synthetic ground truth is exact by construction. Here a human types it, so the
**labels become the new error source**. Everything in this workflow exists to
catch a labelling mistake before it is scored as a model failure forever.

## Workflow

1. **Collect** with explicit permission from whoever owns the document.
   Record who gave it and when — not in this repo, just somewhere you can
   point to later.

2. **Redact the image, not just the label.** The image is what a model is sent.

   ```
   idb redaction-checklist
   ```

   Aadhaar, PAN, voter ID and passports are out of scope entirely. If one
   appears inside an invoice, that invoice does not enter the corpus.

3. **Label.** One JSON beside each image, matched by filename:

   ```
   data/real/inbox/bill001.jpg
   data/real/inbox/bill001.json
   ```

   Start from a blank with every field present:

   ```
   idb label-template --line-items 8 > data/real/inbox/bill001.json
   ```

   Emit `null` for a field the document does not show. **Never delete the key** —
   an omitted key cannot be distinguished from "this document has no such field".

   If several bills share a layout (common when they come from one supplier),
   say so — they are not independent draws and the statistics need to know:

   ```json
   { "_meta": { "layout_group": "supplier_a" }, "invoice_number": "..." }
   ```

4. **Ingest.** Validates every label against the schema *and* the arithmetic
   checks, then registers what passes:

   ```
   idb ingest
   ```

   Arithmetic failures are warnings, not errors: real invoices sometimes
   genuinely do not reconcile. Look at each one before accepting it. Use
   `--strict-arithmetic` once you have ruled that out.

5. **Sweep and report** exactly as for the synthetic corpus:

   ```
   idb run    --models <model> --manifest data/real/manifest.json --out results/raw_real
   idb report --models <model> --manifest data/real/manifest.json --raw results/raw_real
   ```

## Two things that differ from the synthetic corpus

**The images cannot be regenerated.** Synthetic renders are gitignored because
they rebuild from seeds. These do not. They are the artifact — back them up.
They are gitignored here only because they are personal data that should not be
pushed without a deliberate decision.

**There are no severity levels.** A photograph is already at whatever quality it
was taken at. Real documents carry a single `real` level and sit outside the
degradation curve rather than pretending to be a point on it.

## Why this layer decides the project

Everything else measures documents we generated. This answers the question a
reader asks first: *does that transfer?* If models score 95% synthetic and 64%
on real photographs, **that gap is the headline** — and it cannot be estimated
from Layers 1 and 2 at all.

Even five documents say something. Fifty says it well.
