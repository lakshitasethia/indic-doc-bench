# The label was wrong, not the model

*A matched pair of the same invoice exposed an error in my ground truth, and then a
hallucination that arithmetic self-consistency cannot see.*

---

I have been building IndicDocBench, a benchmark for extracting structured data from Indian GST
invoices. Most of it is synthetic: 384 document-variants generated from seeds across 12 layouts and
four degradation levels, built that way so that no ground-truth answer could have been memorised
from a pretraining corpus. A handful of real documents sit alongside it, hand-labelled, to measure
the gap between generated invoices and the kind people actually receive.

One of those real documents turned up twice. I ate at a restaurant, photographed the printed thermal
receipt on the table (creased paper, a patterned floor behind it, one line-item amount partly eaten
by a fold), and was separately sent the born-digital PDF of the same bill over WhatsApp. Same
invoice, same ground truth, same labels. One variable: how the document was captured.

That pair was supposed to tell me something about photographs. It told me something about my
labelling instead.

## The eight-point gap

The first reading looked clean and unsurprising. Running `minimax/minimax-m3`, the photograph scored
87.8% and the PDF 95.9%. The entire difference sat in one field: the photograph run was missing
`hsn_sac` on all six line items.

This is exactly the shape of result the project was built to find. My README had, at the time,
proposed that models scoring around 91% on synthetic documents might score around 64% on real
photographs, and that this gap would be the headline. An eight-point capture penalty concentrated in
one hard-to-read field is a smaller version of that story, and it would have been very easy to write
it up as a confirmation.

## The label was wrong

The receipt prints `SAC: 996331` exactly once, in the footer, as a document-level field. It does not
repeat it per line, because it does not need to: every line on the bill is the same service.

My ground truth had six per-line SAC codes on that invoice. Nothing on the document says that.
Somebody, which is to say me, had copied the footer code onto each line during labelling, because
that is the obvious thing to do and because the benchmark's schema has a slot for it.

The benchmark's own methodology forbids exactly this. One of the rules I had written down months
earlier says that normalisation removes representation and never repairs: `15/01/26` and
`2026-01-15` are the same answer, but turning an `O` into a `0` inside a GSTIN scores a wrong answer
as right and degrades the benchmark. Inferring six line-level codes from one footer code is the
same category of move. It asserts something the document does not say.

So the model that reported no per-line SAC was reading the document correctly. My labels were
asserting six values that are not printed anywhere on it.

I corrected the labels, setting `hsn_sac` to null on lines for every document that prints a single
document-level code, and re-ran:

| Capture | Score |
|---|---|
| born-digital PDF | 95.3% (59/67) |
| **photograph** | **100.0% (67/67)** |

The photograph is now perfect. The eight-point capture gap does not exist. The other photograph in
my corpus also scores 100%.

## What the correction revealed

The interesting part is not that the gap vanished. It is what happened to the PDF's remaining
errors.

They did not just persist, they changed category. My scoring distinguishes four outcomes rather than
two: `correct`, `wrong`, `missing`, and `spurious`. A missing field and a fabricated field are
different failures. An omission is detectable downstream, because the field is empty and a pipeline
can route it to a human. A fabrication is not, because the field is populated, plausible, and it
enters the ledger.

Before the correction, the photograph's six nulls were scored `missing`. After it, those nulls were
right, and the PDF's six values became `spurious`.

Reading the clean, born-digital PDF, the model had back-filled the footer SAC onto all six line
items. Reading the creased photograph, it had not.

The hallucination was on the easy input.

## Why arithmetic cannot catch this

IndicDocBench runs ten consistency checks that need no ground truth at all, on model output alone:
line items sum to the declared subtotal; subtotal plus taxes plus round-off equals the grand total;
CGST equals SGST, which is true by construction on every intra-state invoice; CGST/SGST and IGST are
mutually exclusive, because a supply is either intra-state or inter-state; quantity times unit price
minus discount equals the taxable value; tax at the stated rate matches the stated amount; the GSTIN
check digits validate; place of supply agrees with the seller's state code; round-off lies within a
rupee; the date parses.

These are good checks. The GSTIN checksum in particular is unusually sharp: a single misread
character in a 15-character GSTIN fails the check digit with probability 35/36, so character-level
damage to the most business-critical field on the document is detectable with no reference data at
all.

Every one of them passes on the back-filled output.

`996331` is a real SAC code. It appears on the document. It is correctly formatted. It is not an
addend in any total, so no sum changes. Copying it into six slots produces an output that is
internally coherent in every way the checker knows how to test. The arithmetic is untouched, the
schema is satisfied, and the value is one a human reviewer skimming the extraction would accept
without hesitation.

This is the failure mode that worries me most in document extraction, and it is not the dramatic
one. It is not a model inventing a company that does not exist. It is a model taking something true
about the document and asserting it in a place the document does not assert it. The output stays
plausible, so every check that examines a single output in isolation stays quiet.

## The matched pair is the detector

Here is the part I think generalises.

Because the two captures are the same invoice, any difference between the two outputs is a property
of the model rather than of the document. The document is held constant by construction. So
disagreement between the two runs is a signal, and computing it **requires no ground truth at all**.

That matters more than it first sounds. Ground truth tells you a model is wrong, but ground truth is
expensive: it is hand-labelling, and as this whole episode demonstrates, hand-labelling is itself
error-prone in ways that are invisible until something contradicts it. Cross-modal disagreement tells
you that something is wrong without telling you what the right answer is, and it costs only a second
rendering of a document you already have.

For a benchmark that generates its own corpus, that second rendering is nearly free. The same seed
produces a clean render and a degraded one. The pair comes out of the pipeline whether you ask for
it or not.

I want to be precise about what this check does and does not catch.

**It catches** failures that depend on how the document was presented. Back-filling is one, because
whatever made the model reach for the footer on a crisp render did not fire on a creased photograph.
Anything where visual quality changes which region the model attends to will surface here.

**It misses** failures that are stable across renderings. If the model had back-filled the footer SAC
on both the PDF and the photograph, the two outputs would agree perfectly and the check would say
nothing. A consistent hallucination is invisible to a consistency check. This is a real limitation
and not a small one.

## What this does not establish

Two photographs is not a sample.

Both are close-up, in focus, under ordinary indoor light, and both are short thermal receipts of two
and six line items. I measured their effective resolution, pixels per inch of actual paper, at 174
and 432 DPI. My synthetic degradation ladder's harshest rung renders at 72 DPI, so the lower of the
two real photographs sits about 2.4 times above it. These are easy captures: the photograph a person
takes deliberately, of a bill they intend to read.

The hard photograph, taken at arm's length in poor light and slightly out of focus, of a dense A4
invoice rather than a short receipt, remains uncollected. That is the document this corpus is
missing, and I now have a tool that checks whether a candidate qualifies before it is labelled,
rather than a feeling about it.

I also want to be clear that the direction of this result contradicts the hypothesis the project was
built on. I expected real photographs to be much harder than synthetic renders. At n=2 there is no
gap in that direction at all. Read that as unmeasured, not as disproved. But I am not going to keep
stating the original expectation as though these two documents had not happened.

The back-filling finding itself does not depend on the photograph question. It is the first
hallucination pattern in the repository found on a real document, and it was invisible until a
matched pair made the model contradict itself.

## What I would take from this

Three things, in increasing order of confidence.

**Least confident:** that back-filling document-level fields onto line items is a common failure mode
in document extraction. I have one document. It is a suggestive one, because the mechanism is
obvious once seen, but it is one document.

**More confident:** that separating `missing` from `spurious` is worth the extra machinery. The
entire finding here is a category change from one to the other. Under a two-outcome scoring system,
correcting the labels would have moved six errors from one model run to the other and revealed
nothing about what kind of error they were.

**Most confident:** that a paired design earns its place before it tells you anything about a model,
because the first thing it is likely to catch is an error in your own ground truth. Mine had one.
The pair found it, and it found it by producing a result that looked plausible and publishable, which
is the kind of error that otherwise survives.

---

*IndicDocBench is open source: [github.com/lakshitasethia/indic-doc-bench](https://github.com/lakshitasethia/indic-doc-bench).
The full methodology, including the statistical choices and a longer limitations section, is in
[docs/METHODOLOGY.md](https://github.com/lakshitasethia/indic-doc-bench/blob/main/docs/METHODOLOGY.md).*
