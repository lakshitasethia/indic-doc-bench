# Indian Document Extraction Benchmark

How accurately do vision-language models extract structured data from Indian
business documents — and what does each one cost you per 1,000 documents?

Nobody has published this. Every Indian fintech, accounting SaaS, and lending
startup building document intake needs it.

**Status:** harness complete; 12 templates; 96-document corpus at 4 severity
levels (408 images, 626 line items). Two arms have swept the full corpus —
the `ocr-rules-v1` baseline and `minimax-m3` (384/384, zero failed calls).
Two more are partial: `qwen3-vl-32b-instruct` at 247/384 and
`gemini-2.5-flash-lite` at 24/384, both stopped against an exhausted API
balance rather than a bug. Read the `Calls failed` column in
`results/report.md` before quoting those two — they rest on less data and
are not comparable at face value. No Anthropic model has been run yet.

Layer 3 is open rather than pending: 6 real documents collected, hand-labelled,
ingested and swept, 2 of them photographs — including a matched pair, the same
restaurant invoice as both the emailed PDF and a handheld photograph of the
printed receipt. Every headline number below still comes from Layers 1 and 2.

---

## What this measures

| Axis | Why it's here |
|---|---|
| Per-field accuracy, split header vs. line items | A blended number is dominated by table length, not by skill |
| **Accuracy vs. degradation severity** | Models that tie on clean input separate hard as it degrades |
| **Error taxonomy** | *How* a model fails decides whether you can ship it |
| Hallucination vs. omission | An omission is detectable downstream; a hallucination is not |
| Arithmetic self-consistency | Needs no ground truth, so it works in production too |
| Cost per 1,000 documents, latency p50/p95 | The table a CTO actually wants and cannot currently find |

Two document types are deliberately excluded: **Aadhaar, PAN, voter ID and
passports — real or synthetic.** Unauthorised handling of Aadhaar data carries
criminal liability under the Aadhaar Act, and the DPDP Act 2023 governs personal
data broadly. Generating realistic fake ID documents is its own problem with
zero upside here. The interesting engineering is in invoices anyway.

---

## Quick start

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install anthropic
.venv/bin/playwright install chromium
brew install tesseract          # only for the OCR+rules baseline

.venv/bin/idb build --n 96                       # generate, render, degrade
.venv/bin/idb verify                             # re-hash against the manifest
.venv/bin/idb pricing --refresh                  # live OpenRouter rate card

.venv/bin/idb run --models mock:0.08 ocr-rules   # free: harness + baseline
.venv/bin/idb run --models qwen/qwen3-vl-32b-instruct     # needs OPENROUTER_API_KEY
.venv/bin/idb run --models claude-opus-5                  # needs ANTHROPIC_API_KEY
.venv/bin/idb run --models claude-opus-5 --repeat 3 --levels L0_clean  # variance

.venv/bin/idb report --models claude-opus-5 ocr-rules-v1
.venv/bin/idb triage                             # narrow the review residue
.venv/bin/idb capture-dpi bill.jpg --media thermal80   # place a photo on the ladder
.venv/bin/python -m pytest tests -q
```

`report` writes `results/report.md`, three figures into `results/figures/`, and
`results/review_queue.csv` — a stratified sample of the errors the classifier
could not categorise on its own, ready for the manual taxonomy pass.

A second corpus, `data/long_probe/` — 24 documents forced to 23–28 line items,
built with `idb build --prefix lng --line-items 23 28` — exists because the
residue review found that table length, not image quality, is what breaks
header totals. On clean renders `minimax-m3` scores 95.5% on header fields at
the natural median of 5 line items and **68.7%** at 23–28. See
[`METHODOLOGY.md#8b`](docs/METHODOLOGY.md).

`triage` then attacks that residue with signals the classifier does not use —
whether the predicted value is verbatim some *other* field of the same
document, whether a wrong amount is a right amount from elsewhere (a line
quantity, or a tax the model computed instead of reading), and numeric rather
than string distance. It writes `results/review_queue_triaged.csv` with a
suggestion, the evidence behind it, and the record's ground-truth-free
self-consistency score. It suggests; it does not decide. Rows it cannot
explain are marked `AMBIGUOUS-*` and left for a human, because a guess written
into that column would be indistinguishable from a reviewed judgement.

---

## The dataset

Hybrid, in three layers, and the hybrid is the point. Layers 1 and 2 carry the
weight: 96 base documents rendered across 4 severity levels gives **384
document-variants (408 images, 626 line items)**, all hash-locked in
`data/synthetic/manifest.json`. Layer 3 is real and small — 6 documents — which
is enough to falsify a claim and nowhere near enough to establish one. Every
leaderboard number in this repository comes from Layers 1 and 2, and should be
read in that light.

**Layer 1 — synthetic (96 docs, built).** Twelve Jinja templates modelled on the
layouts real Indian invoicing software produces — Tally-style exports, SaaS
invoices, handmade bill books, e-invoices with IRN and QR, wide landscape
grids, minimal whitespace layouts, dense multi-page tables, bilingual
Hindi/English forms, thermal receipts, watermarked letterheads, boxed
government-style forms, and raw spreadsheet exports — rendered through
Chromium. Populated
with checksum-valid GSTINs carrying correct state codes, real HSN/SAC codes at
their real GST slabs, state-appropriate PIN codes, and tax arithmetic that
reconciles to the paisa including the round-off line.

Ground truth is free and exact: we generated the values, so there is no
annotator and therefore no annotator error.

*On contamination:* these specific documents and their values cannot be in any
model's training data — they were generated from seeds. That is a real
methodological edge over benchmarks built from public documents. It is not a
claim that nothing about them is familiar: the layouts deliberately imitate
widely-used software, and the HSN codes and commodity names are real. The
precise claim is that the answers cannot have been memorised.

**Layer 2 — degraded (288 variants, built).** Nobody uploads a clean PDF.
They photograph a printed bill at an angle under a ceiling light. Four severity
levels applied to the *same* documents:

| Level | Render DPI | Rotation | Perspective | JPEG | Lighting | Noise |
|---|---|---|---|---|---|---|
| L0 clean | 300 | — | — | — | — | — |
| L1 scan | 300 | ±1.2° | slight | q88 | mild gradient | σ3 |
| L2 photo | 150 | ±3.5° | yes | q62 | gradient + hotspot | σ7 + blur |
| L3 harsh | 72 | ±7.5° | strong | q34 | strong | σ13 + salt-pepper |

Documents are auto-cropped to the printed area first (people frame the bill, not
the desk) and composited onto a surface for the photo levels, so the warp
reveals background instead of smeared edge pixels.

**This is a stress ladder, not a sample of real captures,** and Layer 3 is what
made that measurable. `idb capture-dpi` measures a photograph in the ladder's
own units — pixels per inch of paper — and the two real photographs in the
corpus come out at **174** and **432** effective DPI. One sits between L2_photo
and L1_scan; the other is above L0_clean. So L3_harsh at 72 DPI is 2.4× below
the *lower* of two ordinary phone captures. Read the L0→L3 drop as a stress
response, not as an estimate of what production traffic costs you. See
[`METHODOLOGY.md#8c4`](docs/METHODOLOGY.md).

Because it is the same document at every level, per-document degradation deltas
and paired significance tests are available — an unpaired design would need far
more documents for the same power.

**Layer 3 — real (6 docs collected, 2 photographs, ~50 target).**
Genuine invoices collected with explicit permission, personal details redacted,
hand-labelled. The ingestion path exists and is tested — `idb label-template`,
`idb redaction-checklist`, `idb ingest` — so documents can be added one at a
time and swept with the same commands as the synthetic corpus. See
[`data/real/README.md`](data/real/README.md).

In: a marketplace invoice, three restaurant bills and a cinema ticket — five
documents, six corpus entries, because one restaurant invoice is in twice, as
the emailed PDF *and* as a handheld photograph of the printed receipt.
Identical ground truth, one variable. Four entries are born-digital; two are
photographs. The documents themselves are not in this repository: they carry
real names and addresses.

On the four born-digital entries `minimax-m3` scores 95.1% against 94.7% on
synthetic clean renders. That holds capture constant and varies only document
realism, so it says the synthetic layouts are not so unrepresentative that a
model collapses on genuine ones — and nothing at all about photographs
(§8c.2).

This layer was built to answer the question that decides the project's
credibility — *does synthetic performance transfer?* — and the honest report at
n=6 is that it did not answer it, and did something more useful instead.

**The expected result did not appear.** This README used to predict ~91% on
synthetic against ~64% on real photographs, with the gap as the headline. Both
photographs score **100%**, better than every born-digital PDF in the corpus.
That is not a refutation — two easy captures cannot refute anything — but it
does mean the gap is *unmeasured*, not *demonstrated*, and the specific missing
document is now named: a hard photograph, at arm's length, in poor light,
slightly out of focus.

**The matched pair found an error in the ground truth, not in the model.** On
first reading, the photograph scored 87.8% against the PDF's 95.9%, missing
`hsn_sac` on all six line items — a tidy 8-point capture gap. The label was
wrong. The receipt prints `SAC: 996331` once, in the footer, as a
document-level field; copying it onto each line is inference, which
[`METHODOLOGY.md`](docs/METHODOLOGY.md) §4 forbids. Corrected, the photograph
reads 100% and the PDF 95.3% — and the PDF's errors change category from
*missing* to *spurious*. Reading the clean PDF, the model back-filled the
footer SAC onto all six lines. Reading the photograph, it did not.

**That back-filling is the finding that does not depend on capture at all.** A
model copying a document-level code into every line item produces one
hallucination per line, and arithmetic self-consistency cannot catch it,
because the fabricated values are internally consistent. It is the first
hallucination pattern here found on a real document, and it was invisible until
a matched pair made the model disagree with itself. See
[`METHODOLOGY.md`](docs/METHODOLOGY.md) sections 8c.1–8c.5.

---

## Repository map

```
src/idb/
  schema.py       the contract: fields, types, nullability, criticality
  normalize.py    type-aware normalisation (removes representation, never repairs)
  india.py        GSTIN checksums, state codes, HSN catalogue, PIN ranges
  generate.py     synthetic invoices whose tax math reconciles exactly
  render.py       Jinja -> HTML -> Chromium -> PDF/PNG
  degrade.py      the degradation pipeline
  align.py        Hungarian line-item alignment + merge/split detection
  score.py        four-outcome per-field scoring, document exact match
  consistency.py  ground-truth-free validity checks
  stats.py        clustered bootstrap, McNemar, Holm-Bonferroni, power
  corpus.py       corpus build + SHA-256 manifest lock
  runner.py       sweep with cost/latency/raw-response logging
  report.py       leaderboard, degradation curves, cost tables
  taxonomy.py     auto-classifies errors; queues the ambiguous residue
  triage.py       narrows that residue; refuses to guess at what is left
  ingest.py       Layer 3: validates hand-labels, registers real documents
  capture.py      effective DPI of a real photograph, in the ladder's units
  figures.py      degradation curve, error-mix bars, cost/accuracy scatter
  models.py       pinned Anthropic IDs; everything else priced live
  pricing.py      OpenRouter rate card, fetched and timestamped
templates/        invoice layouts (add more here; this is the cheap axis)
tests/            171 tests covering the metric's tricky invariants
docs/             METHODOLOGY.md -- the decisions behind the numbers
data/synthetic/   manifest.json (ground truth, version-controlled); images are
                  regenerated from seeds and are not
results/raw/      every model response, kept verbatim; the primary artifact
```

See [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) for the decisions that make the
numbers mean something, and the ones that would quietly break them.

## Limitations

Read [`docs/METHODOLOGY.md#limitations`](docs/METHODOLOGY.md#limitations) before
citing any number from this repository.

## Licence

MIT — see [`LICENSE`](LICENSE).

The synthetic corpus contains no real business data: every GSTIN, address and
invoice value is generated. HSN/SAC codes and commodity names are real, being
matters of public record. No Aadhaar, PAN, voter ID or passport data appears
anywhere in this repository, by deliberate design rather than by omission.
