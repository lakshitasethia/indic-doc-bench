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

---

## What this measures

| Axis | Why it's here |
|---|---|
| Per-field accuracy, split header vs. line items | A blended number is dominated by table length, not by skill |
| **Accuracy vs. degradation severity** | Models that tie on clean PDFs separate hard on photographs |
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
.venv/bin/python -m pytest tests -q
```

`report` writes `results/report.md`, three figures into `results/figures/`, and
`results/review_queue.csv` — a stratified sample of the errors the classifier
could not categorise on its own, ready for the manual taxonomy pass.

---

## The dataset

Hybrid, in three layers, and the hybrid is the point. Two of the three are
built: 96 base documents rendered across 4 severity levels gives **384
document-variants (408 images, 626 line items)**, all hash-locked in
`data/synthetic/manifest.json`. Layer 3 is not collected yet, and every
number in this repository should be read in that light.

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
They photograph a printed bill at an angle under a ceiling light. Four severity levels applied
to the *same* documents:

| Level | DPI | Rotation | Perspective | JPEG | Lighting | Noise |
|---|---|---|---|---|---|---|
| L0 clean | 300 | — | — | — | — | — |
| L1 scan | 300 | ±1.2° | slight | q88 | mild gradient | σ3 |
| L2 photo | 150 | ±3.5° | yes | q62 | gradient + hotspot | σ7 + blur |
| L3 harsh | 72 | ±7.5° | strong | q34 | strong | σ13 + salt-pepper |

Documents are auto-cropped to the printed area first (people frame the bill, not
the desk) and composited onto a surface for the photo levels, so the warp
reveals background instead of smeared edge pixels.

Because it is the same document at every level, per-document degradation deltas
and paired significance tests are available — an unpaired design would need far
more documents for the same power.

**Layer 3 — real (~50 docs — NOT YET COLLECTED).** Genuine invoices collected
with explicit permission, personal details redacted, hand-labelled. Small, but it answers the
question that decides the project's credibility: *does synthetic performance
transfer?* If models score 91% on synthetic and 64% on real photographs, that
gap is the headline.

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
  figures.py      degradation curve, error-mix bars, cost/accuracy scatter
  models.py       pinned Anthropic IDs; everything else priced live
  pricing.py      OpenRouter rate card, fetched and timestamped
templates/        invoice layouts (add more here; this is the cheap axis)
tests/            92 tests covering the metric's tricky invariants
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
