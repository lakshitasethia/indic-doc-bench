<div align="center">

# IndicDocBench

**How accurately do vision-language models extract structured data from Indian
business documents — and what does each one cost you per 1,000 documents?**

[![tests](https://github.com/lakshitasethia/indic-doc-bench/actions/workflows/tests.yml/badge.svg)](https://github.com/lakshitasethia/indic-doc-bench/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-1f6feb?style=flat-square)](LICENSE)
[![corpus](https://img.shields.io/badge/corpus-384_variants-6e7781?style=flat-square)](data/synthetic/manifest.json)
[![tests count](https://img.shields.io/badge/tests-171_passing-2da44e?style=flat-square)](tests)
[![ground truth](https://img.shields.io/badge/ground_truth-exact,_generated-8250df?style=flat-square)](#the-dataset)

[**Results**](#the-result-people-actually-want) ·
[**Quick start**](#quick-start) ·
[**Dataset**](#the-dataset) ·
[**What it found**](#what-it-found) ·
[**Methodology**](docs/METHODOLOGY.md) ·
[**Limitations**](#limitations)

</div>

---

Nobody has published this. Every Indian fintech, accounting SaaS and lending
startup building document intake needs it, and every one of them is currently
choosing a model on vibes.

> [!IMPORTANT]
> **Status.** Harness complete · 12 templates · 96-document corpus at 4 severity
> levels (408 images, 626 line items). Two arms have swept the full corpus:
> the `ocr-rules-v1` baseline and `minimax-m3` (384/384, zero failed calls).
> Two are partial — `qwen3-vl-32b-instruct` at 247/384 and
> `gemini-2.5-flash-lite` at 24/384 — both stopped against an exhausted API
> balance rather than a bug. **Read the `Calls failed` column before quoting
> those two.** No Anthropic model has been run yet.
>
> Layer 3 is open rather than pending: 6 real documents collected,
> hand-labelled, ingested and swept, 2 of them photographs — including a
> matched pair, the same restaurant invoice as both the emailed PDF and a
> handheld photograph of the printed receipt. Every headline number below still
> comes from Layers 1 and 2.

---

## The result people actually want

Accuracy on clean documents is the least interesting number here. Accuracy as a
function of how bad the input gets is the interesting one — models that tie on a
clean PDF separate hard as the image degrades.

| Model | n | Failed | All fields (95% CI) | Header | Line items | Halluc. share | USD / 1k docs |
|---|---:|---:|:---|:---|:---|---:|---:|
| `qwen/qwen3-vl-32b-instruct` | 247 | 137 | 78.6% [74.8, 82.4] | 73.4% | 80.5% | 60.2% | $0.66 |
| `google/gemini-2.5-flash-lite` | 24 | 24 | 78.2% [66.0, 88.4] | 77.4% | 78.5% | 72.5% | $0.80 |
| `minimax/minimax-m3:free` | 384 | 0 | 75.8% [72.1, 79.1] | 72.5% | 76.9% | 37.1% | $0.00 |
| `ocr-rules-v1` *(baseline)* | 384 | 0 | 5.1% [4.1, 6.2] | 20.3% | 0.0% | 2.8% | $0.00 |

> [!WARNING]
> The top two rows rest on a fraction of the corpus and **are not comparable at
> face value.** They are sorted where they are because the table sorts by score,
> not because they won. This is exactly the kind of number a leaderboard
> normally launders — hence the `Failed` column sitting second from the left.

<table>
<tr>
<td width="50%" valign="top">

**Accuracy vs. severity**

![degradation curve](results/figures/degradation.png)

</td>
<td width="50%" valign="top">

**Cost vs. accuracy**

![cost against accuracy](results/figures/cost_vs_accuracy.png)

</td>
</tr>
</table>

Full tables, paired significance tests and the error taxonomy:
[`results/report.md`](results/report.md).

---

## What this measures

| Axis | Why it's here |
|---|---|
| Per-field accuracy, header vs. line items | A blended number is dominated by table length, not by skill |
| **Accuracy vs. degradation severity** | Models that tie on clean input separate hard as it degrades |
| **Error taxonomy** | *How* a model fails decides whether you can ship it |
| Hallucination vs. omission | An omission is detectable downstream; a hallucination is not |
| Arithmetic self-consistency | Needs no ground truth, so it works in production too |
| Cost per 1,000 docs, latency p50/p95 | The table a CTO actually wants and cannot currently find |

> [!NOTE]
> **Out of scope by design: Aadhaar, PAN, voter ID and passports — real or
> synthetic.** Unauthorised handling of Aadhaar data carries criminal liability
> under the Aadhaar Act, and the DPDP Act 2023 governs personal data broadly.
> Generating realistic fake ID documents is its own problem with zero upside
> here. The interesting engineering is in invoices anyway.

---

## Quick start

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install anthropic
.venv/bin/playwright install chromium
brew install tesseract          # only for the OCR+rules baseline

.venv/bin/idb build --n 96                       # generate, render, degrade
.venv/bin/idb verify                             # re-hash against the manifest
.venv/bin/idb run --models mock:0.08 ocr-rules   # free: harness + baseline
.venv/bin/idb report --models ocr-rules-v1
```

<details>
<summary><b>Every command</b></summary>

```bash
.venv/bin/idb build --n 96                       # generate, render, degrade
.venv/bin/idb verify                             # re-hash against the manifest
.venv/bin/idb pricing --refresh                  # live OpenRouter rate card

.venv/bin/idb run --models mock:0.08 ocr-rules            # free
.venv/bin/idb run --models qwen/qwen3-vl-32b-instruct     # needs OPENROUTER_API_KEY
.venv/bin/idb run --models claude-opus-5                  # needs ANTHROPIC_API_KEY
.venv/bin/idb run --models claude-opus-5 --repeat 3 --levels L0_clean   # variance

.venv/bin/idb report --models claude-opus-5 ocr-rules-v1
.venv/bin/idb triage                             # narrow the review residue
.venv/bin/idb capture-dpi bill.jpg --media thermal80   # place a photo on the ladder

.venv/bin/idb label-template --line-items 8      # Layer 3: blank label
.venv/bin/idb redaction-checklist                # Layer 3: what to redact
.venv/bin/idb ingest                             # Layer 3: validate and register

.venv/bin/python -m pytest tests -q
```

`report` writes [`results/report.md`](results/report.md), three figures into
`results/figures/`, and `results/review_queue.csv` — a stratified sample of the
errors the classifier could not categorise on its own, ready for the manual
taxonomy pass.

`triage` then attacks that residue with signals the classifier does not use:
whether the predicted value is verbatim some *other* field of the same document,
whether a wrong amount is a right amount from elsewhere (a line quantity, or a
tax the model computed instead of reading), and numeric rather than string
distance. It writes `results/review_queue_triaged.csv` with a suggestion, the
evidence behind it, and the record's ground-truth-free self-consistency score.
**It suggests; it does not decide.** Rows it cannot explain are marked
`AMBIGUOUS-*` and left for a human, because a guess written into that column
would be indistinguishable from a reviewed judgement.

</details>

---

## The dataset

Hybrid, in three layers, and the hybrid is the point.

<table>
<tr>
<th align="left" width="34%">Layer 1 — synthetic</th>
<th align="left" width="33%">Layer 2 — degraded</th>
<th align="left" width="33%">Layer 3 — real</th>
</tr>
<tr valign="top">
<td>

**96 documents · built**

12 Jinja templates → Chromium → PDF/PNG.

Ground truth is free and **exact**: we generated the values, so there is no
annotator and therefore no annotator error.

</td>
<td>

**288 variants · built**

The same documents at four severity levels, so per-document deltas and paired
significance tests are available.

An unpaired design would need far more documents for the same power.

</td>
<td>

**6 documents · open**

Genuine invoices, collected with permission, redacted, hand-labelled.

4 born-digital, 2 photographs, 1 matched pair. Not committed — they carry real
names and addresses.

Collection workflow:
[`data/real/README.md`](data/real/README.md)

</td>
</tr>
</table>

Layers 1 and 2 carry the weight: 96 base documents across 4 levels gives **384
document-variants (408 images, 626 line items)**, all hash-locked in
`data/synthetic/manifest.json`. Layer 3 is real and small, which is enough to
falsify a claim and nowhere near enough to establish one.

<details>
<summary><b>Layer 1 — what makes the synthetic documents defensible</b></summary>

Twelve layouts modelled on what real Indian invoicing software produces:
Tally-style exports, SaaS invoices, handmade bill books, e-invoices with IRN and
QR, wide landscape grids, minimal-whitespace layouts, dense multi-page tables,
bilingual Hindi/English forms, thermal receipts, watermarked letterheads, boxed
government-style forms, and raw spreadsheet exports.

Populated with checksum-valid GSTINs carrying correct state codes, real HSN/SAC
codes at their real GST slabs, state-appropriate PIN codes, and tax arithmetic
that reconciles to the paisa including the round-off line.

**On contamination.** These specific documents and their values cannot be in any
model's training data — they were generated from seeds. That is a real
methodological edge over benchmarks built from public documents. It is *not* a
claim that nothing about them is familiar: the layouts deliberately imitate
widely-used software, and the HSN codes and commodity names are real. The
precise claim is that **the answers cannot have been memorised.**

</details>

### The severity ladder

Nobody uploads a clean PDF. They photograph a printed bill at an angle under a
ceiling light. Four levels, applied to the *same* documents:

| Level | Render DPI | Rotation | Perspective | JPEG | Lighting | Noise |
|---|---:|---|---|---|---|---|
| `L0_clean` | 300 | — | — | — | — | — |
| `L1_scan` | 300 | ±1.2° | slight | q88 | mild gradient | σ3 |
| `L2_photo` | 150 | ±3.5° | yes | q62 | gradient + hotspot | σ7 + blur |
| `L3_harsh` | 72 | ±7.5° | strong | q34 | strong | σ13 + salt-pepper |

Documents are auto-cropped to the printed area first — people frame the bill,
not the desk — and composited onto a surface for the photo levels, so the warp
reveals background instead of smeared edge pixels.

> [!CAUTION]
> **This is a stress ladder, not a sample of real captures.** `idb capture-dpi`
> measures a photograph in the ladder's own units — pixels per inch of paper —
> and the two real photographs in the corpus come out at **174** and **432**
> effective DPI. One sits between `L2_photo` and `L1_scan`; the other is above
> `L0_clean`. So `L3_harsh` at 72 DPI is **2.4× below the lower of two ordinary
> phone captures.** Read the L0→L3 drop as a stress response, not as an estimate
> of what production traffic costs you. → [METHODOLOGY §8c.4](docs/METHODOLOGY.md)

### A second corpus, for one specific question

`data/long_probe/` — 24 documents forced to 23–28 line items, built with
`idb build --prefix lng --line-items 23 28` — exists because the residue review
found that **table length, not image quality, is what breaks header totals.**
On clean renders `minimax-m3` scores 95.5% on header fields at the natural
median of 5 line items and **68.7%** at 23–28.
→ [METHODOLOGY §8b](docs/METHODOLOGY.md)

---

## What it found

The results worth reading are the ones that cost something.

<details open>
<summary><b>A hallucination that arithmetic self-consistency structurally cannot catch</b></summary>

<br>

One restaurant invoice exists in the corpus twice: the emailed PDF, and a
handheld photograph of the printed receipt. Identical ground truth, one
variable.

Reading the clean PDF, the model copied a footer-printed `SAC: 996331` onto all
six line items. Reading the photograph, it did not. That is one hallucination
per line — and the ground-truth-free self-consistency check, the one that also
works in production, **cannot see it**, because the fabricated values are
internally consistent.

First hallucination pattern here found on a real document, and invisible until a
matched pair made the model disagree with itself.

</details>

<details>
<summary><b>The paired design found an error in the ground truth, not in the model</b></summary>

<br>

Same pair, first reading: the photograph scored 87.8% against the PDF's 95.9%,
missing `hsn_sac` on every line — a tidy 8-point capture gap, exactly the story
this project expected.

**The label was wrong.** The receipt prints the code once, in the footer, as a
document-level field. Copying it onto each line is inference, and
[METHODOLOGY §4](docs/METHODOLOGY.md) forbids exactly that. Corrected:

| Capture | Score |
|---|---|
| born-digital PDF | 95.3% (59/67) |
| **photograph** | **100.0% (67/67)** |

No model improvement can fix a typo in the answer key.

</details>

<details>
<summary><b>A metadata field that named a parameter the function never applied</b></summary>

<br>

Running real documents through the degradation pipeline produced 78.9% at
`L3_harsh` against 20.4% for matched synthetic documents. A 58-point gap reading
as "real documents are far more robust to degradation" — and entirely false.

`degrade_image` recorded the target DPI as metadata but never resampled to it,
so the most destructive component of the recipe never ran. The obvious control
— matching on line-item count — changed nothing, because complexity was not the
cause. Only comparing output pixel dimensions exposed it.

*A metadata field naming a parameter the code does not apply will eventually be
read as evidence that it did.*
→ [METHODOLOGY §8c.3](docs/METHODOLOGY.md)

</details>

<details>
<summary><b>The headline hypothesis, retired</b></summary>

<br>

This project was built on the expectation that models scoring ~91% on synthetic
documents would score ~64% on real photographs, and that the gap would be the
headline.

Both real photographs score **100%**, better than every born-digital PDF in the
corpus. Two easy captures refute nothing — so the claim is now stated as
*unmeasured* rather than demonstrated, and the missing document is named: a hard
photograph, at arm's length, in poor light, slightly out of focus.

</details>

---

## Repository map

<details>
<summary><b>src/idb — what each module is responsible for</b></summary>

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

</details>

---

## Limitations

> [!WARNING]
> **Read [`docs/METHODOLOGY.md#limitations`](docs/METHODOLOGY.md#limitations)
> before citing any number from this repository.** Ten of them, in full, in
> writing. The short version: 12 templates is 12 layouts; the main corpus
> under-samples long tables; document-level exact match cannot rank models at
> n=400; determinism is not guaranteed; prices change; every extracted value is
> Latin script; and all of it is under one prompt.

---

## Licence

MIT — see [`LICENSE`](LICENSE).

The synthetic corpus contains no real business data: every GSTIN, address and
invoice value is generated. HSN/SAC codes and commodity names are real, being
matters of public record. No Aadhaar, PAN, voter ID or passport data appears
anywhere in this repository, by deliberate design rather than by omission.

<div align="center">
<sub>Built by <a href="https://github.com/lakshitasethia">Lakshita Sethia</a> ·
<a href="docs/METHODOLOGY.md">Methodology</a> ·
<a href="results/report.md">Full results</a></sub>
</div>
