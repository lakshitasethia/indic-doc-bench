# Methodology

The decisions below are the difference between a benchmark and a bar chart.
Each one is a place where the obvious implementation produces numbers that look
fine and mean nothing.

---

## 1. Scoring has four outcomes, not two

`correct` / `wrong` / `missing` / `spurious`, plus `absent_ok` for a field that
is legitimately null in both.

Collapsing `wrong` and `missing` into "incorrect" destroys the most useful
information in the dataset. An omission is **detectable** downstream — the field
is empty, route it to a human. A hallucination is **not** — the field is
populated, plausible, and it enters the ledger. Two models at identical accuracy
with inverted error mixes are not interchangeable products.

`spurious` is the fourth, and it is specific to this domain. On an intra-state
invoice, IGST does not exist. A model that writes a number there has fabricated
a tax liability. That is not the same failure as misreading one that was
printed, so it is not scored the same.

**The zero/null judgment call.** For the tax fields, an explicit `0.00` where the
truth is "this tax does not apply" is a *representation* choice, not a
fabrication — so the schema marks those fields `null_equiv_zero` and accepts it.
Any other number there is still `spurious`. This is a documented decision, not a
silent leniency, and it is flagged because it moves numbers: without it, every
model that writes `0.00` for inapplicable taxes takes a large and meaningless
penalty.

## 2. Header fields and line-item fields are scored separately

They are different tasks. Finding a labelled scalar is not parsing a table whose
length varies from 1 to 28 rows. A single blended number is dominated by
whichever group has more instances — which on a long invoice is always the
table. A system that reads the header perfectly and skips the table scores near
zero; one that does the reverse scores well. Both readings mislead, so all three
numbers are published.

The OCR+rules baseline makes this concrete: it scores **22% on header fields and
0% on line items**, for a blended 5.6%. Only the split is informative.

## 3. Line items are aligned, never indexed

Predicted rows do not arrive in ground-truth order, and models merge and split
rows. Comparing index *i* to index *i* measures ordering luck.

Alignment is optimal assignment (Hungarian) over a weighted field-similarity
cost matrix, with weak matches rejected rather than forced. The cost matrix
deliberately excludes tax amounts — those are the most-often-wrong values, and
aligning on them lets an extraction error cascade into a matching error.

**The subtlety that matters:** merge detection must be able to *dissolve an
existing pair*. When a model merges rows A and B, assignment still binds the
merged row to whichever of A or B it resembles more, stranding the other as a
false omission. A post-pass re-examines every pair: if the predicted amount
equals its partner's amount plus one or more unmatched ground-truth amounts, the
pair is withdrawn and the group is recorded as one merge. Without that pass, one
structural mistake is reported as one omission plus one hallucination — the two
categories the taxonomy exists to separate.

Merges and splits are their own category and are excluded from per-field
counting, because expanding one structural error into sixteen field errors would
swamp everything else. They always fail document-level exact match.

## 4. Normalisation removes representation and never repairs

`15/01/26`, `15-Jan-2026` and `2026-01-15` are the same answer; scoring them as
different measures your own bug. But correcting `O` to `0` inside a GSTIN scores
a wrong answer as right, and that is not normalisation — it is a degradation of
the benchmark.

Documented conventions:
- **Money** is `Decimal`, never `float`, throughout. Lakh-grouped commas, `₹`,
  `Rs.`, `CR`/`DR`, parenthesised negatives.
- **Dates** are day-first without exception. `05/03/2026` is 5 March. A fixed,
  stated convention beats a heuristic that silently flips on the 12th.
- **Percent** treats a value below 1 as a fraction (`0.18` → `18`), because no
  GST slab sits between 0 and 1 percent.
- **HSN granularity is preserved.** 4-digit and 8-digit codes are different
  filings; unifying them would hide a real error class.
- **Fuzzy** fields use token-set ratio at threshold 88, with a sensitivity check
  reported at 80 and 95 so the headline cannot be accused of being tuned.
- A value that is present but uninterpretable as its type is a **format
  violation**, tracked separately from a miss — "right value, wrong
  representation" is its own taxonomy row.

*A bug this caught:* routing quantities through the money normaliser rounded
them to two decimals, turning a correctly-extracted `75.177 KGS` into a scored
error. It would have inflated every model's quantity error rate identically and
invisibly. Locked by a test.

## 5. Statistics: clustered, paired, and honest about power

**Resampling is clustered by template.** 96 documents from 12 templates do not
carry 96 documents' worth of independent information about layout-sensitive
failure. Documents sharing a template fail in correlated ways. On the fixture in
`tests/test_stats.py`, the IID bootstrap interval comes out **roughly three
times too narrow** — easily enough to turn "indistinguishable" into a spurious
winner. Every published interval resamples clusters.

**Field instances are not independent either.** Fields in one document share an
image, a skew, a light source. Intervals are computed by resampling *documents*
and recomputing the field statistic — never by treating N documents × M fields
as N·M Bernoulli trials, which is how this is usually done and how intervals end
up several times too tight.

**Comparisons are paired.** The same documents go to every model, so McNemar on
the discordant pairs is the correct test; the two-proportion z-test discards the
pairing and most of the power. Exact binomial below 25 discordant pairs.

**Multiplicity.** Six models is fifteen pairwise comparisons; at α=0.05 you
expect about one spurious winner from chance alone. Holm-Bonferroni.

**Power is computed before the sweep, not after.** At n=400 the smallest
document-level gap resolvable at 80% power is roughly **14 points**. That is not
a caution, it is a design constraint: *the document-exact-match leaderboard
cannot rank models at this corpus size.* Per-field accuracy, with far more
instances, can. Planning for "statistically indistinguishable" as a legitimate
headline is more honest — and more useful — than squinting at overlapping
intervals afterwards.

## 6. Arithmetic self-consistency needs no ground truth

Ten checks run on model output alone: line items sum to the subtotal; subtotal
plus taxes plus round-off equals the total; CGST equals SGST; CGST/SGST and IGST
are mutually exclusive; quantity × price − discount equals the taxable value;
tax at the stated rate matches the stated amount; **GSTIN check digits**; place
of supply agrees with the seller's state code; round-off lies within a rupee;
the date parses.

Two reasons this earns its place. It is a benchmark metric — a model whose
output is internally coherent is more trustworthy than one at the same accuracy
whose arithmetic is incoherent. And it is directly reusable in production as the
confidence signal that decides whether a document needs human review.

The GSTIN check digit is the sharpest of the ten: a single misread character
fails it with probability **35/36** (measured in `tests/test_india.py`), so
character-level damage to the most business-critical field on the document is
detectable with no reference data at all.

**Round-off had to be in the schema for any of this to work.** Real Indian
invoices round to the rupee. Omit that line and the reconciliation check fires
spuriously on most documents, and the most novel metric in the benchmark becomes
noise.

The generator and the consistency checker were written independently. That they
agree on all 400 generated documents cross-validates both.

## 7. Reproducibility, and the thing that is *not* reproducible

- Model IDs are pinned exactly; pricing is recorded with a verification date,
  and the registry **refuses to run** a model whose rate is unverified. A cost
  table built on a guessed rate is worse than a missing one.
- The prompt is frozen, versioned and hashed; the hash is stored with every
  response. One prompt for every model — per-vendor prompt tuning would measure
  effort spent per vendor, which is the confound that makes most public
  comparisons unusable.
- The corpus is locked by a SHA-256 manifest, verified before every paid sweep.
- Every raw response is written to disk before parsing. The taxonomy is built by
  reading those files weeks later; re-running to recover text you already paid
  for is a self-inflicted budget wound.
- Runs are resumable. A sweep that dies at document 380 of 450 must not cost the
  full amount again.

**`temperature=0` is not available.** The parameter was removed on current
Claude models (Opus 5, Sonnet 5, and the 4.6+ family) and returns a 400 — the
standard determinism ritual simply does not apply. OpenRouter-hosted open models
still accept it, so the two adapters differ on purpose. Determinism is therefore
**measured, not assumed**: a subset is re-run and run-to-run variance is
reported as its own number. Any benchmark claiming deterministic frontier
results on these models is describing a parameter it did not set.

## 8. Failures that are counted, not skipped — and the one kind that is not

A refusal, a truncation, or unparseable output scores every field as `missing`
and the document still counts in the denominator. Silently dropping failed
documents flatters the least reliable model — which is exactly backwards.

There is exactly one exception, and it is not a softening of that rule but a
consequence of it. A call that **never reached the model** — a transport error,
a 401, a 402 — carries no information about the model at all. Scoring it as
`missing` does not measure the model's reliability; it measures our own, and
files the result under the model's name.

This is not hypothetical. A sweep in this repository ran the OpenRouter balance
to zero partway through, and 137 of 384 documents came back HTTP 402. Scored
naively, that is a model that omitted every field on a third of the corpus, and
the number would have gone into the leaderboard exactly that way. `runner.py`
already refused to record such a response as a schema violation; `score_run`
now likewise refuses to score it, and the count appears in its own
**`Calls failed`** column on the leaderboard.

The column is not optional decoration. A row scored on 247 documents and a row
scored on 384 are not comparable, and the only defence against quietly
comparing them is to print the difference next to the number. Exclusion is
safe *only* because it is visible; an invisible exclusion would be the same sin
as the one this section opens by warning about.

The line between the two cases is drawn at a single question: **did a model
produce this output?** A refusal is the model's answer and is scored. A 402 is
our billing system's answer and is not.

Tracked separately: **refusal** (some models decline documents resembling
identity papers — a real production failure mode that nobody reports),
**truncation** (`finish_reason == "length"`, which appears on long line-item
tables and correlates with cost), and **schema violation** (valid JSON, wrong
shape). The response parser is forgiving about *packaging* — code fences, a
leading sentence — and completely unforgiving about *content*: no key renaming,
no value repair. A model returning valid JSON with the wrong keys has committed
a schema violation and is scored as one.

## 8b. Table length degrades extraction, and header totals worst

The residue review turned up a mechanism the severity axis was not built to
catch. It was found on four documents, so it was then tested properly on a
purpose-built corpus.

**The signal.** Every unexplained numeric error on a *clean* document -- 28 of
28 -- landed on one of the four documents carrying 23 or more line items. Those
four are 4% of the corpus and absorbed 100% of those errors. All 28 were header
totals; not one was a line-item field.

**The test.** `data/long_probe/` is 24 documents built by the same generator
with the same round-robin template assignment, differing in exactly one
respect: line counts forced into 23-28 instead of drawn from the natural
distribution (median 5). Built with `idb build --prefix lng --line-items 23 28`
as its own corpus, since the main manifest is immutable by design. 612 line
items, all 12 templates, swept clean by `minimax-m3` and the rules baseline.

Clean 300 DPI renders, where every digit is legible. Clustered bootstrap,
template as the resampling unit:

| Model | Field group | Short (n=92) | Long (n=24) | Delta |
|---|---|---|---|---:|
| minimax-m3 | header | 95.5% [92.7, 97.9] | **68.7%** [61.4, 76.7] | **-26.8** |
| minimax-m3 | line items | 96.3% [93.3, 98.9] | **79.7%** [71.7, 87.9] | **-16.6** |
| ocr-rules-v1 | header | 30.0% [24.1, 35.6] | 19.9% [14.2, 25.7] | -10.1 |

The header estimate replicated almost exactly: 68.3% on the original four
documents, 68.7% on twenty-four independent ones. Confidence intervals do not
overlap. Table length degrades header extraction on clean, perfectly legible
images, and image quality explains none of it.

**A correction.** The four-document reading suggested header fields fell about
three times as far as line items. At n=24 that ratio is 1.6, not 3 -- the
line-item drop of 16.6 points is much larger than the four-document sample
implied. The earlier gloss that models "are still reading the rows and only
losing the aggregate" was too strong and is withdrawn. Long tables degrade
*everything*; header totals simply degrade worst. That the small sample got the
headline number right and the secondary comparison wrong is a fair illustration
of what n=4 can and cannot support.

The rules baseline degrades too (-10.1 points), which is worth noting because
its mechanism cannot be attention over a long context -- it is a regex. Long
documents are harder for unrelated reasons as well, so the VLM result should
not be attributed entirely to context handling.

**What it means for the corpus.** A benchmark whose documents have a median of
5 line items under-measures the case that breaks extraction hardest. The probe
addresses this for the two free arms; the main corpus is unchanged, so the
headline leaderboard still reflects the natural length distribution, which is
the right default for a realistic cost-per-1000-documents claim. Reporting both
is the honest arrangement: natural distribution for what a pipeline will
actually see, and the probe for the regime that breaks it.

## 8c. Layer 3 inverts where the error comes from

Layers 1 and 2 have no annotator, so they have no annotator error. Layer 3 has
both, and the whole design of `ingest.py` follows from that one inversion.

A mistyped label is not a small problem. It is scored as a model error on every
model, forever, and no amount of model improvement can fix it -- the benchmark
simply reports a failure that never happened. So every hand-label is run through
the schema *and* through the same ground-truth-free arithmetic checks used on
model output before it is allowed into the corpus. A label whose CGST and SGST
do not reconcile against the taxable value is far more likely to be a typing
slip than a genuine oddity.

Those arithmetic failures are warnings rather than errors, because real invoices
sometimes genuinely do not reconcile -- rounding conventions vary and some
suppliers really do print totals that do not add up. `--strict-arithmetic`
promotes them once that has been ruled out for a given corpus.

Three further consequences:

**Document ids come from filenames, never from a counter.** A real corpus grows
one document at a time, and a positional counter renumbers every later document
the moment one is added earlier in the sort -- silently invalidating every
result already keyed by the old ids.

**Real documents have no severity levels.** A photograph arrives at whatever
quality it was taken at. Assigning it an `L0_clean` would invent a comparison,
so real documents carry a single `real` level and are excluded from the
degradation curve rather than plotted as a point on it.

**Layout is declared, not inferred.** Several bills from one supplier share a
template and are not independent draws, exactly as with the synthetic corpus
where template is the resampling unit. Only the labeller knows which documents
came off the same layout, so `_meta.layout_group` declares it. The default --
one cluster per document -- assumes independence, which is the right default and
is still an assumption.

## 9. The taxonomy is mostly automatic

Three categories fall out with no judgement required, and they are the ones a
human reviewer is worst at spotting:

* **Field confusion** — the predicted value for field A is *exactly* the ground
  truth of a different field B on the same document. This catches buyer/seller
  GSTIN swaps, billing/shipping address transposition, and CGST/SGST/IGST
  mix-ups mechanically. It matters more than its frequency suggests: every
  value is present and well-formed, so no downstream null-check or format check
  will ever flag it. Run against the OCR baseline the detector immediately
  surfaced systematic seller/buyer GSTIN swaps on two-column layouts, where OCR
  interleaves the columns and the first GSTIN on the page is the buyer's.
* **Character-level misread** — small edit distance at equal length, with a
  separate flag when every substitution is a known glyph confusion (0/O, 1/l/I,
  5/S, 8/B, 2/Z, 6/G).
* **Structural** — merges and splits, already identified during alignment.

Refusals, truncations and schema violations come straight off the response
metadata. What is left for a human is the genuinely ambiguous residue —
separating a hallucinated value from a plausible misreading, which requires
looking at the image. On the first sweep that residue was about a quarter of
all findings, and it is sampled *stratified* by model × severity × field group
rather than at random: a random draw over a corpus that is three-quarters
degraded returns almost nothing from clean documents, and clean-document
failures are the ones that say something about the model rather than about the
camera.

## 10. Corpus realism decisions that quietly matter

- **PIN codes match their state.** A model can infer state from a PIN, and a
  Delhi address with a 7-series PIN is a document that could not exist — which
  makes the corpus easier than reality in a way that is invisible in the output.
- **Line quantities are derived from a target line value**, not drawn
  independently. Drawn independently, every high-unit-price line lands in the
  crores and the corpus contains no three- or four-digit amounts at all, so
  short-number parsing is never tested.
- **Line-item counts are long-tailed** (1–28). Truncation and merge/split errors
  only appear on long tables; a corpus of three-line invoices would never
  surface them.
- **Documents are auto-cropped before degradation.** An A4 render of a five-line
  invoice is two-thirds empty paper; degrading the whole page makes the text
  smaller than any real photograph, so the DPI ladder would be measuring framing
  rather than resolution.
- ~15% of invoices are B2C with a genuinely absent buyer GSTIN. These are the
  documents that separate a model returning `null` from one that invents a
  number.

---

## Limitations

Read this before citing any number here.

1. **Synthetic documents are not real documents.** Layer 3 exists precisely to
   measure that gap, and until it is collected the synthetic numbers are an
   upper bound. Expect real photographed invoices to score materially worse.
2. **The main corpus under-samples long tables.** Median 5 line items, and only
   4 documents of 96 carry 23 or more — yet those four absorb every unexplained
   numeric error on clean renders. `data/long_probe/` (24 documents, 23–28
   items) now covers that regime and confirms a 26.8-point header collapse
   (§8b), but only for the two arms that could be swept for free. The headline
   leaderboard still reflects the natural length distribution.
3. **Twelve templates is twelve layouts.** Template is the resampling unit for
   a reason; conclusions do not extend to layouts unlike these. Adding
   templates remains the cheapest way to strengthen the benchmark. The twelve
   span Tally-style exports, SaaS invoices, handmade bill books, e-invoices
   with IRN, wide landscape grids, minimal whitespace layouts, dense multi-page
   tables, bilingual forms, thermal receipts, watermarked letterheads, boxed
   government-style forms, and raw spreadsheet exports.
4. **The degradation model is synthetic.** Real phone photographs bring motion
   blur, rolling shutter, focus falloff, fingers, staples and folds. The
   pipeline covers geometry, lighting, noise and compression.
5. **Document-level exact match cannot rank models at n=400** (§5). Treat that
   column as a difficulty gauge, not a leaderboard.
6. **Determinism is not guaranteed** (§7). Variance is measured, not eliminated.
7. **Prices change.** The cost table is valid as of its stated verification date
   and no longer.
8. **The rules baseline is deliberately crude** — labelled-field regexes and a
   GSTIN pattern. It is a floor, not a serious contender. Its collapse on
   two-column layouts, where OCR interleaves the columns and the first GSTIN
   found belongs to the buyer, is a genuine property of the approach rather than
   an artefact of the implementation.
9. **Latin-script values only.** One template (`t08`) carries bilingual
   Hindi/English *labels*, which is how a large share of north Indian invoices
   are actually issued, but every extracted *value* is Latin script. Documents
   whose values are in Devanagari or another Indic script are not covered, and
   a model could plausibly do much worse on those.
10. **One prompt.** Results describe these models under this prompt. Structured
   output modes would likely eliminate schema violations entirely and are a
   separate axis, deliberately not mixed in.
