<div align="center">

# IndicDocBench

### A Reproducible Benchmark for Structured Extraction from Indian Business Documents

<br/>

**A number you can defend in a room.**

IndicDocBench measures how accurately vision-language models pull structured data out of Indian GST
documents — and what each one costs per 1,000 documents. Not a single accuracy figure, but the
things that decide whether you can ship: *how* a model fails, how it degrades as the image gets
worse, and whether the gap between two models is real or is noise.

<br/>

### **[ Read the full results → ](results/report.md)**

<br/>

![Python](https://img.shields.io/badge/Python-3.12-1f1f1f?style=flat-square)
![OpenCV](https://img.shields.io/badge/OpenCV-degradation_pipeline-1f1f1f?style=flat-square)
![SciPy](https://img.shields.io/badge/SciPy-bootstrap_·_McNemar_·_power-1f1f1f?style=flat-square)
![Chromium](https://img.shields.io/badge/Jinja_·_Chromium-12_layouts-1f1f1f?style=flat-square)
![Corpus](https://img.shields.io/badge/Corpus-384_variants_·_hash_locked-1f1f1f?style=flat-square)
[![Tests](https://github.com/lakshitasethia/indic-doc-bench/actions/workflows/tests.yml/badge.svg)](https://github.com/lakshitasethia/indic-doc-bench/actions/workflows/tests.yml)
![Licence](https://img.shields.io/badge/licence-MIT-1f1f1f?style=flat-square)
![Status](https://img.shields.io/badge/ground_truth-exact,_generated-7C63D9?style=flat-square)

</div>

<br/>

![Accuracy against degradation severity](results/figures/degradation.png)

<br/>

**Contents** · [The problem](#the-problem) · [The approach](#indicdocbench-does-not-start-with-the-score) · [One corpus](#one-corpus-every-question) · [What is measured](#what-is-measured) · [Beyond a leaderboard](#where-indicdocbench-goes-beyond-a-leaderboard) · [What it found](#what-it-found) · [Architecture](#how-indicdocbench-is-built) · [Scoring principles](#exact-first-judgement-last) · [Technology](#technology-stack) · [Verified state](#verified-state) · [Built by](#built-by)

---

# The Problem

An Indian fintech building document intake has to answer one question before it writes any code: *which model reads a GST invoice well enough to trust, and what does it cost at volume?*

There is no public answer. The document extraction benchmarks that exist are built on English and American forms — FUNSD, CORD, DocVQA — and an Indian tax invoice is not one of those. It carries a GSTIN with a state code and a checksum, HSN and SAC codes at slab rates, a CGST/SGST split that becomes IGST across a state line, a round-off line that has to reconcile, and a line-item table whose length varies from one row to twenty-eight. It arrives as a born-digital PDF from Tally, or as a photograph of a thermal receipt taken on a phone.

So the decision gets made on vibes, on a vendor demo, or on a blog post that reports one accuracy number over a handful of documents.

And a single accuracy number is the wrong instrument even when it is honest, for three reasons:

**A blended score is dominated by table length, not by skill.** A model that nails every header field and drops half a twenty-row table can outscore one that does the opposite, purely because line items outnumber header fields.

**How a model fails decides whether you can ship it.** An omission is detectable downstream — a null is a null, and a validation rule catches it. A hallucination is a well-formed, plausible, wrong value that no format check will ever flag. Two models at 78% are not interchangeable if one of them invents 60% of its errors and the other omits them.

**Most reported gaps cannot be resolved by the corpus that reported them.** With 96 documents clustered into 12 layouts, the smallest difference this benchmark can detect at 80% power is about **7.0 points**. Any gap narrower than that is a coin flip being reported as a finding — and almost nobody publishes the number that tells you where the floor is.

---

# IndicDocBench Does Not Start With the Score

The design decision underneath the whole project is a shift in *what gets built first*.

A benchmark usually starts with a leaderboard and adds caveats afterwards, in a paragraph most readers skip. IndicDocBench starts with the machinery that decides whether a number means anything — exact ground truth, an error taxonomy, paired statistics, a stated power floor — and the leaderboard is what falls out at the end.

That ordering has a consequence worth stating up front: this repository publishes its own failures. A resolution bug that faked a 58-point finding, a ground-truth label error that faked an 8-point capture gap, a severity rung described as something it was not, and the project's own headline hypothesis, retired when the data contradicted it. All four are in [What it found](#what-it-found), because a benchmark that catches its author is a stronger claim than any figure in the table.

---

# One Corpus, Every Question

Everything reads from a single hash-locked manifest. Three layers, and the hybrid is the point.

<table>
<tr>
<th align="left" width="34%">Layer 1 — Synthetic</th>
<th align="left" width="33%">Layer 2 — Degraded</th>
<th align="left" width="33%">Layer 3 — Real</th>
</tr>
<tr valign="top">
<td>

**96 documents · built**

12 Jinja templates → Chromium → PDF/PNG.

Ground truth is free and **exact**: the values were generated, so there is no annotator and therefore no annotator error.

</td>
<td>

**288 variants · built**

The *same* documents at four severity levels, which licenses per-document deltas and paired significance tests.

An unpaired design would need far more documents for the same power.

</td>
<td>

**6 documents · open**

Genuine invoices, collected with permission, redacted, hand-labelled.

4 born-digital, 2 photographs, 1 matched pair. Never committed — they carry real names and addresses.

</td>
</tr>
</table>

96 base documents across 4 levels gives **384 document-variants — 408 images, 626 line items** — all SHA-256 locked in `data/synthetic/manifest.json`. The images are not committed; they regenerate from their seeds, and `idb verify` re-hashes them against the manifest.

Layer 3 is real and small. Six documents is enough to falsify a claim and nowhere near enough to establish one, and every headline number on this page comes from Layers 1 and 2.

<details>
<summary><b>What makes the synthetic documents defensible</b></summary>

<br/>

Twelve layouts modelled on what real Indian invoicing software actually produces: Tally-style exports, SaaS invoices, handmade bill books, e-invoices with IRN and QR, wide landscape grids, minimal-whitespace layouts, dense multi-page tables, bilingual Hindi/English forms, thermal receipts, watermarked letterheads, boxed government-style forms, and raw spreadsheet exports.

Populated with checksum-valid GSTINs carrying correct state codes, real HSN/SAC codes at their real GST slabs, state-appropriate PIN codes, and tax arithmetic that reconciles to the paisa including the round-off line.

Four realism decisions that quietly matter, each fixing a way the corpus would otherwise have been easier than reality in a way invisible from the output:

- **PIN codes match their state.** A model can infer state from a PIN, and a Delhi address with a 7-series PIN is a document that could not exist.
- **Line quantities are derived from a target line value,** not drawn independently. Drawn independently, every high-unit-price line lands in the crores and the corpus contains no three- or four-digit amounts at all — so short-number parsing is never tested.
- **Line-item counts are long-tailed (1–28).** Truncation and merge/split errors only appear on long tables.
- **~15% of invoices are B2C with a genuinely absent buyer GSTIN.** These are the documents that separate a model returning `null` from one that invents a number.

**On contamination.** These specific documents and their values cannot be in any model's training data — they were generated from seeds. That is a real methodological edge over benchmarks built from public documents. It is *not* a claim that nothing about them is familiar: the layouts deliberately imitate widely-used software, and the HSN codes and commodity names are real. The precise claim is that **the answers cannot have been memorised.**

</details>

---

# What Is Measured

| Axis | Why it is here |
| --- | --- |
| Per-field accuracy, header vs. line items | A blended number is dominated by table length, not by skill |
| **Accuracy vs. degradation severity** | Models that tie on clean input separate hard as it degrades |
| **Error taxonomy** | *How* a model fails decides whether you can ship it |
| Hallucination vs. omission | An omission is detectable downstream; a hallucination is not |
| Arithmetic self-consistency | Needs no ground truth, so the same check runs in production |
| Cost per 1,000 documents, latency p50/p95 | The table a CTO actually wants and cannot currently find |
| Smallest resolvable difference | The number that says which gaps on this page are real |

### The leaderboard, with its own uncertainty attached

| Model | n | Failed | All fields (95% CI) | Header | Line items | Halluc. share | USD / 1k |
| --- | ---: | ---: | :--- | :--- | :--- | ---: | ---: |
| `qwen/qwen3-vl-32b-instruct` | 247 | 137 | 78.6% [74.8, 82.4] | 73.4% | 80.5% | 60.2% | $0.66 |
| `google/gemini-2.5-flash-lite` | 24 | 24 | 78.2% [66.0, 88.4] | 77.4% | 78.5% | 72.5% | $0.80 |
| `minimax/minimax-m3:free` | 384 | 0 | 75.8% [72.1, 79.1] | 72.5% | 76.9% | 37.1% | $0.00 |
| `ocr-rules-v1` *(baseline)* | 384 | 0 | 5.1% [4.1, 6.2] | 20.3% | 0.0% | 2.8% | $0.00 |

> **Read the `Failed` column before quoting the top two rows.** They rest on a fraction of the corpus — both stopped against an exhausted API balance rather than a bug — and are not comparable at face value. They sit at the top because the table sorts by score, not because they won. Calls that never reached the model are excluded from every accuracy figure rather than scored as omissions: an unpaid invoice is not a model error.

Two arms have swept the full corpus: the `ocr-rules-v1` baseline and `minimax-m3`, at 384/384 with zero failed calls. No Anthropic model has been run yet.

---

# Where IndicDocBench Goes Beyond a Leaderboard

Everything below reads from the same corpus and the same scoring code. None of it is a separate tool with its own notion of correctness.

<br/>

## Four Outcomes, Not Two

**A field is not right or wrong. It is right, wrong, absent, or invented.**

Scoring a prediction against ground truth produces one of four outcomes, and collapsing them into a single accuracy figure throws away the distinction that decides deployability:

| Outcome | Ground truth | Prediction | Why it is its own category |
| --- | --- | --- | --- |
| `CORRECT` | a value | the same value | — |
| `WRONG` | a value | a different value | A misreading, recoverable by a better model |
| `MISSING` | a value | `null` | Detectable downstream — a null check catches it |
| `SPURIOUS` | `null` | a value | **Undetectable** — well-formed, plausible, invented |

A fifth state, `ABSENT_OK`, covers a field legitimately null in both. And one documented leniency: for tax fields an explicit `0.00` where the truth is "this tax does not apply" is a representation choice rather than a fabrication, so the schema marks those `null_equiv_zero` and accepts it. Any *other* number there is still `SPURIOUS`. That decision is flagged because it moves numbers — without it, every model that writes `0.00` for inapplicable taxes takes a large and meaningless penalty.

Header fields and line-item fields are scored separately, because a document with 24 line items contributes 24× the line-item weight of a document with one. Line items are **aligned, never indexed** — Hungarian assignment against a similarity matrix, with merge and split detection — because a model that drops row three is not wrong about rows four through twenty.

Normalisation is type-aware and **removes representation without ever repairing content**. `₹1,23,456.00` and `123456` are the same number. `27AAACP4526D1ZQ` with a stray space is the same GSTIN. But a wrong value stays wrong: normalisation that fixes a checksum, infers a missing state code or fills a blank from context is scoring the normaliser instead of the model.

<br/>

## The Severity Ladder

**Nobody uploads a clean PDF.** They photograph a printed bill at an angle under a ceiling light. Four fixed recipes, applied to the same documents:

| Level | Render DPI | Rotation | Perspective | JPEG | Lighting | Noise |
| --- | ---: | --- | --- | --- | --- | --- |
| `L0_clean` | 300 | — | — | — | — | — |
| `L1_scan` | 300 | ±1.2° | slight | q88 | mild gradient | σ3 |
| `L2_photo` | 150 | ±3.5° | yes | q62 | gradient + hotspot | σ7 + blur |
| `L3_harsh` | 72 | ±7.5° | strong | q34 | strong | σ13 + salt-pepper |

Documents are auto-cropped to the printed area before degradation — people frame the bill, not the desk — and composited onto a surface for the photo levels, so the perspective warp reveals background instead of smeared edge pixels. Every operation records its parameters into the manifest, so a degradation curve can afterwards be regressed against the individual factors: *is it the DPI, or is it the warp?*

> **Current state:** this is a **stress ladder, not a sample of real captures.** `idb capture-dpi` measures a photograph in the ladder's own units — pixels per inch of paper — and the two real photographs in the corpus come out at **174** and **432** effective DPI. One sits between `L2_photo` and `L1_scan`; the other is above `L0_clean`. `L3_harsh` at 72 DPI is **2.4× below the lower of two ordinary phone captures**, so the L0→L3 drop is a stress response, not an estimate of what production traffic costs you.

<br/>

## Statistics That Say When They Cannot Tell

Template is the resampling unit, not the document. Twelve layouts with eight documents each is not 96 independent draws — documents from one template share a layout, and treating them as independent narrows every confidence interval that follows.

So: **clustered bootstrap** for intervals, **McNemar** for paired model comparisons on the same documents, **Holm–Bonferroni** across the comparison family, and a published **power floor**. The smallest difference this corpus can resolve at 80% power is about 7.0 points, which is stated on the report next to the table rather than in a footnote.

Document-level exact match is reported and explicitly labelled as a difficulty gauge rather than a leaderboard, because at n=400 it cannot rank models.

<br/>

## Arithmetic Self-Consistency

The only check here that needs no ground truth — which is why it is the one that also runs in production, on documents nobody has labelled.

An invoice is an arithmetic object. `quantity × unit_price − discount` should equal the line's taxable value; the line values should sum to the header total; CGST plus SGST should equal the tax, or IGST should carry it alone; the round-off should close the gap to the grand total. A prediction that fails its own arithmetic is suspect without anyone knowing the right answer.

> It is also the check with a **structural blind spot**, and one of this repository's findings is a hallucination it provably cannot catch — see [What it found](#what-it-found).

<br/>

## Error Taxonomy, and the Residue It Refuses to Guess At

32,905 findings are auto-classified. Three categories fall out mechanically, and they are the ones a human reviewer is worst at spotting:

- **Field confusion** — the predicted value for field A is *exactly* the ground truth of a different field B on the same document. Buyer/seller GSTIN swaps, billing/shipping transposition, CGST/SGST/IGST mix-ups. Every value is present and well-formed, so no null check or format check downstream will ever flag one. Run against the OCR baseline, the detector immediately surfaced systematic seller/buyer GSTIN swaps on two-column layouts, where OCR interleaves the columns and the first GSTIN on the page belongs to the buyer.
- **Character-level misread** — small edit distance at equal length, with a separate flag when every substitution is a known glyph confusion (`0/O`, `1/l/I`, `5/S`, `8/B`, `2/Z`, `6/G`).
- **Structural** — merges and splits, already identified during alignment.

What is left is genuine ambiguity: separating a hallucinated value from a plausible misreading requires looking at the image. That residue is sampled **stratified by model × severity × field group**, because a random draw over a corpus that is three-quarters degraded returns almost nothing from clean documents — and clean-document failures are the ones that say something about the model rather than about the camera.

`idb triage` then narrows the residue using evidence the classifier does not use: whether a predicted value is verbatim some *other* field of the same document, whether a wrong amount is a right amount from elsewhere (a line quantity, or a tax the model computed instead of reading), and numeric rather than string distance. It writes a suggestion, the evidence behind it, and the record's ground-truth-free consistency score.

**It suggests; it does not decide.** Rows it cannot explain are marked `AMBIGUOUS-*` and left for a human, because a guess written into that column would be indistinguishable from a reviewed judgement.

<br/>

## Layer 3 — Where the Error Moves

On synthetic documents the ground truth is exact by construction. On a real invoice a human types it, and **the label becomes the new error source.** Every part of the Layer 3 workflow exists to catch a labelling mistake before it is scored as a model failure forever.

`idb redaction-checklist` states what has to be removed from the *image*, not just the label. `idb label-template` emits a blank with every field present, because an omitted key cannot be distinguished from "this document has no such field". `idb ingest` validates each label against the schema *and* the arithmetic, and diagnoses three recurring labelling conventions by their signature — a line off by exactly the tax at the stated rate means the invoice prints tax-inclusive pricing, and it names the figure to write instead.

> **Current state:** 6 documents ingested and swept — a marketplace invoice, three restaurant bills and a cinema ticket, one of which is in twice as both an emailed PDF and a photograph of the printed receipt. Target is ~50. The documents, their labels, their manifest and every model response on them are gitignored: model output echoes the document back, names and addresses included.

---

# What It Found

The results worth reading are the ones that cost something.

<br/>

## A hallucination that self-consistency structurally cannot catch

One restaurant invoice exists in the corpus twice: the emailed PDF, and a handheld photograph of the printed receipt. Identical ground truth, one variable — the paired design the severity ladder uses, applied to capture instead.

Reading the clean PDF, the model copied a footer-printed `SAC: 996331` onto all six line items. Reading the photograph, it did not.

That is one hallucination per line, and it is precisely the failure mode arithmetic self-consistency **cannot see**, because the fabricated values are internally consistent — six identical codes break no sum. It is the first hallucination pattern here found on a real document, and it was invisible until a matched pair made the model disagree with itself.

<br/>

## The paired design found the error in the ground truth, not in the model

Same pair, first reading: the photograph scored 87.8% against the PDF's 95.9%, missing `hsn_sac` on every line. A tidy 8-point capture gap — exactly the story this project expected to find.

**The label was wrong.** The receipt prints the code once, in the footer, as a document-level field. Copying it onto each line is inference, and the methodology forbids exactly that. Corrected across every document that prints a single document-level code:

| Capture | Score |
| --- | --- |
| born-digital PDF | 95.3% (59/67) |
| **photograph** | **100.0% (67/67)** |

The photograph is perfect and the PDF is not — and the PDF's errors changed category from *missing* to *spurious*. No model improvement can fix a typo in the answer key.

<br/>

## A metadata field that named a parameter the function never applied

Running real documents through the degradation pipeline produced 78.9% at `L3_harsh` against 20.4% for synthetic documents matched on table length. A 58-point gap reading as *"real documents are far more robust to degradation"* — and entirely false.

`degrade_pdf` rasterises at the level's DPI and then degrades. `degrade_image` never resized; it only recorded the DPI as metadata. Feeding it 200 DPI renders applied L3's rotation, shadow, noise and JPEG at full resolution while stamping the output 72 DPI. The single most destructive component of the recipe never ran.

The bug survived the obvious control — matching on line-item count changed nothing, because complexity was not the cause. Only comparing pixel dimensions between the synthetic and real outputs exposed it. Corrected, real content degrades essentially as synthetic content does, collapse at L3 included.

*A metadata field naming a parameter the code does not apply will eventually be read as evidence that it did.*

<br/>

## Table length, not image quality, is what breaks header totals

The manual review residue suggested that unexplained numeric errors on *clean* renders were concentrated in long tables. Rather than assert that from four documents, a second corpus was built to test it: 24 documents forced to 23–28 line items.

On clean renders, `minimax-m3` scores **95.5%** on header fields at the natural median of 5 line items, and **68.7%** at 23–28. A 26.8-point collapse driven by a variable no leaderboard controls for — and one that only appears if the corpus is long-tailed enough to contain the regime at all.

<br/>

## The headline hypothesis, retired

This project was built on the expectation that models scoring ~91% on synthetic documents would score ~64% on real photographs, and that the gap would be the headline.

Both real photographs score **100%**, better than every born-digital PDF in the corpus. Two easy captures refute nothing — both are close, in focus, under ordinary light, both short thermal receipts measuring between L1 and L2 on the ladder. So the claim is now stated as *unmeasured* rather than demonstrated, and the missing document is named precisely: a hard photograph, at arm's length, in poor light, slightly out of focus.

The easy photograph — the one a person takes deliberately, of a bill they intend to read — is not a hard case for a current vision model. The hard photograph is still uncollected.

---

# Exact First, Judgement Last

The most consequential constraint in the codebase is a rule about *when* a judgement call is allowed to enter a number.

```text
exact value         →  string equality after type-aware normalisation
       ↓                 (if this resolves it, stop)
structural match    →  Hungarian line-item alignment, merge/split detection
       ↓                 (if this resolves it, stop)
mechanical rule     →  field confusion, glyph-confusion edit distance, numeric distance
       ↓                 (if this resolves it, stop)
human               →  only genuine ambiguity — stratified, sampled, and never guessed
```

Each rung resolves what it can and stops. Nothing falls through to a human that a rule could have settled, and nothing is settled by a rule that genuinely needs an eye on the image. The residue that reaches the bottom is labelled `AMBIGUOUS-*` and left there.

Two rules are enforced everywhere in this repository, and they are the reason the ladder holds:

1. **Normalisation removes representation and never repairs content.** The moment a normaliser fixes a checksum or infers a missing field, the benchmark is measuring the normaliser.
2. **Nothing automated writes into the answer key.** Triage produces suggestions with the evidence attached. A guess written into a reviewed column would be indistinguishable from a judgement.

### What is automatic, and what is left to a person

| The system produces | A human does | What is never automated |
| --- | --- | --- |
| Four-outcome per-field score | — | — |
| Line-item alignment, merge/split detection | — | — |
| Field confusion, glyph misread, structural errors | — | — |
| Refusals, truncations, schema violations | — | — |
| Stratified sample of the ambiguous residue | Reviews it against the image | The residue is never auto-labelled |
| Triage suggestion + evidence + consistency score | Accepts, edits or rejects | `AMBIGUOUS-*` rows are never resolved by the tool |
| Label validation + arithmetic diagnosis on real docs | Writes and corrects the label | The label is always hand-entered |
| Effective-DPI measurement of a capture | States the physical paper width | The width is never inferred from pixels |

---

# How IndicDocBench Is Built

A Python package with a ten-subcommand CLI. Documents are generated from seeds, rendered through headless Chromium, degraded with OpenCV, swept against model adapters, and scored — with every stage writing its parameters into a manifest that `idb verify` re-hashes.

```text
seeded generation  →  Jinja templates  →  Chromium  →  PDF/PNG
                                                          ↓
                                            OpenCV degradation ladder
                                                          ↓
                          model adapters  →  raw responses, kept verbatim
                                                          ↓
              normalise  →  align  →  score  →  taxonomy  →  triage
                                                          ↓
                    report.md · figures · review queue · triaged residue
```

A few decisions worth naming:

**Raw model responses are the primary artifact.** `results/raw/` is version-controlled — every response, verbatim, including the malformed ones. Re-running to recover them costs real money, and any scoring change can be re-applied to the originals rather than re-purchased.

**The corpus images are not committed; the manifest is.** Images regenerate from their seeds. What must survive is the ground truth and the SHA-256 of every file it describes, so a fresh clone can prove it is looking at the same corpus.

**Malformed model output degrades to a score, never to a crash.** A refusal, a truncation, a JSON fragment or a schema violation is a *finding*, categorised and counted. The one thing that is not scored is a call that never reached the model — transport, auth or billing failures are excluded and reported in their own column.

**A schema is never mutated after a sweep.** The single documented exception was proven inert at scoring time and verified by regenerating the report byte-identically.

<details>
<summary><b>Repository layout</b></summary>

```text
src/idb/                23 modules · 4 model adapters · 10 CLI subcommands
├── schema.py           the contract: fields, types, nullability, criticality
├── normalize.py        type-aware normalisation (removes representation, never repairs)
├── india.py            GSTIN checksums, state codes, HSN catalogue, PIN ranges
├── generate.py         synthetic invoices whose tax math reconciles exactly
├── render.py           Jinja → HTML → Chromium → PDF/PNG
├── degrade.py          the degradation ladder
├── capture.py          effective DPI of a real photograph, in the ladder's units
├── align.py            Hungarian line-item alignment + merge/split detection
├── score.py            four-outcome per-field scoring, document exact match
├── consistency.py      ground-truth-free arithmetic validity checks
├── stats.py            clustered bootstrap · McNemar · Holm–Bonferroni · power
├── corpus.py           corpus build + SHA-256 manifest lock
├── runner.py           sweep with cost / latency / raw-response logging
├── taxonomy.py         auto-classifies errors; queues the ambiguous residue
├── triage.py           narrows that residue; refuses to guess at what is left
├── ingest.py           Layer 3: validates hand-labels, registers real documents
├── report.py           leaderboard, degradation curves, cost tables
├── figures.py          degradation curve, error-mix bars, cost/accuracy scatter
├── pricing.py          OpenRouter rate card, fetched and timestamped
└── adapters/           vision · rules baseline · mock · base contract

templates/              12 invoice layouts — the cheapest axis to extend
tests/                  11 suites · 171 tests over the metric's tricky invariants
docs/METHODOLOGY.md     the decisions behind the numbers, and the ones that break them
data/synthetic/         manifest.json — ground truth, version-controlled
results/raw/            every model response, verbatim; the primary artifact
```

</details>

---

# Quick Start

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e .
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
.venv/bin/idb capture-dpi bill.jpg --media thermal80     # place a photo on the ladder

.venv/bin/idb label-template --line-items 8      # Layer 3: blank label
.venv/bin/idb redaction-checklist                # Layer 3: what to redact
.venv/bin/idb ingest                             # Layer 3: validate and register

.venv/bin/python -m pytest tests -q
```

A second corpus lives at `data/long_probe/` — 24 documents forced to 23–28 line items, built with
`idb build --prefix lng --line-items 23 28` — because table length turned out to be what breaks
header totals.

</details>

---

# Privacy and Scope

- **Aadhaar, PAN, voter ID and passports are out of scope entirely** — real or synthetic. Unauthorised handling of Aadhaar data carries criminal liability under the Aadhaar Act, and the DPDP Act 2023 governs personal data broadly. Generating realistic fake ID documents is its own problem with zero upside here. If one appears inside a collected invoice, that invoice does not enter the corpus.
- **No real business data in the synthetic corpus.** Every GSTIN, address and invoice value is generated. HSN/SAC codes and commodity names are real, being matters of public record.
- **Real documents are never committed.** `data/real/` — the documents, their labels and the manifest — is gitignored, and so is `results/raw_real/`. The rule that keeps `results/raw/` in the repository must not extend to those: model output echoes the document back, names, addresses and phone numbers included.
- **Redaction covers the image, not just the label.** The image is what a model is sent.
- **Collection requires explicit permission** from whoever owns the document, recorded outside this repository.

---

# Technology Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Generation | Jinja2, seeded RNG | 12 invoice layouts, exact ground truth |
| Rendering | Playwright / headless Chromium, PyMuPDF | HTML → PDF → PNG at a stated DPI |
| Degradation | OpenCV, NumPy, Pillow | Geometry, lighting, noise, compression, resampling |
| Alignment | SciPy (`linear_sum_assignment`) | Hungarian line-item matching, merge/split detection |
| Similarity | RapidFuzz | Fuzzy normalisation thresholds, triage edit distance |
| Statistics | SciPy, NumPy | Clustered bootstrap · McNemar · Holm–Bonferroni · power |
| Model access | httpx | Anthropic · OpenRouter · AgentRouter adapters |
| Baseline | Tesseract + labelled-field regexes | A deliberate floor, not a contender |
| Reporting | pandas, matplotlib | Leaderboard, degradation curves, cost/accuracy scatter |
| Integrity | SHA-256 manifest, `idb verify` | A fresh clone can prove it has the same corpus |
| Tests | pytest | 11 suites, 171 tests, no network required |

No vector database, no embedding model, no LLM framework, and no judge model anywhere in the scoring path. Every score is computed by code that can be read.

---

# Verified State

Claims on this page are drawn from the implementation. Where something is incomplete, it is said so above rather than omitted. For the record:

| Check | Result |
| --- | --- |
| Test suite | **171 passed** (`pytest tests -q`), no network required — and green in CI on every push, not asserted by a badge that cannot fail |
| Corpus integrity | `idb verify` re-hashes 408 images against the manifest |
| Full sweeps | 2 arms at 384/384 with zero failed calls — `minimax-m3` and `ocr-rules-v1` |
| Partial sweeps | `qwen3-vl-32b-instruct` 247/384 · `gemini-2.5-flash-lite` 24/384 — exhausted API balance, reported in the `Calls failed` column |
| Findings classified | 32,905 auto-classified; 145 ambiguous ones stratified into the review queue |
| Power floor | ~7.0 points at 80% power, published beside the table it qualifies |
| Layer 3 | 6 documents ingested with no validation errors; none committed |

Known gaps, stated plainly: no Anthropic model has been run; two of four arms rest on partial sweeps; Layer 3 holds 6 documents against a ~50 target and the hard photograph is uncollected; every extracted value is Latin script, so documents whose *values* are in Devanagari are not covered; and all results describe these models under one prompt, with structured-output modes deliberately not mixed in as a separate axis.

> **Read [`docs/METHODOLOGY.md#limitations`](docs/METHODOLOGY.md#limitations) before citing any number from this repository.** Ten limitations, in full, in writing.

---

# Built By

[@lakshitasethia](https://github.com/lakshitasethia) · MIT licensed, see [`LICENSE`](LICENSE)

Methodology: [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) · Full results: [`results/report.md`](results/report.md) · Layer 3 workflow: [`data/real/README.md`](data/real/README.md)

---

<div align="center">

IndicDocBench is built on one idea: **a benchmark's job is not to rank models, it is to be wrong in ways you can find.**

</div>
