"""The generator and the consistency checker were written to be independent.
That they agree on every document is a real cross-validation of both."""
import pytest

from idb.consistency import check_record, consistency_score
from idb.generate import generate_invoice, to_json_safe
from idb.india import gstin_is_valid
from idb.schema import ALL_HEADER


@pytest.mark.parametrize("seed", range(0, 200, 7))
def test_generated_invoices_are_internally_consistent(seed):
    rec, _ = generate_invoice(seed)
    j = to_json_safe(rec)
    failed = [c.name for c in check_record(j) if c.passed is False]
    assert not failed, failed
    assert consistency_score(j) == 1.0


def test_generated_gstins_pass_the_check_digit():
    for seed in range(50):
        rec, _ = generate_invoice(seed)
        assert gstin_is_valid(rec["seller_gstin"])
        if rec["buyer_gstin"]:
            assert gstin_is_valid(rec["buyer_gstin"])


def test_ground_truth_covers_every_schema_field():
    rec, _ = generate_invoice(1)
    for f in ALL_HEADER:
        assert f.name in rec, f.name


def test_intrastate_and_interstate_taxes_are_mutually_exclusive():
    for seed in range(60):
        rec, ctx = generate_invoice(seed)
        if ctx["interstate"]:
            assert rec["igst_amount"] is not None
            assert rec["cgst_amount"] is None and rec["sgst_amount"] is None
        else:
            assert rec["igst_amount"] is None
            assert rec["cgst_amount"] == rec["sgst_amount"]


def test_corrupted_output_is_detected_without_ground_truth():
    rec, _ = generate_invoice(3)
    j = to_json_safe(rec)
    j["seller_gstin"] = j["seller_gstin"][:-1] + ("A" if j["seller_gstin"][-1] != "A" else "B")
    assert consistency_score(j) < 1.0


def test_force_line_count_overrides_the_natural_distribution():
    """The long-table probe (METHODOLOGY 8b) depends on this override."""
    from idb.generate import generate_invoice
    for n in (1, 23, 28):
        rec, _ = generate_invoice(90001, "t01", force_line_count=n)
        assert len(rec["line_items"]) == n


def test_forced_documents_still_reconcile_arithmetically():
    """A probe corpus whose tax maths did not add up would measure nothing:
    every model would 'fail' on ground truth that was itself wrong."""
    from idb.consistency import consistency_score
    from idb.generate import generate_invoice, to_json_safe
    for seed in range(90000, 90008):
        rec, _ = generate_invoice(seed, "t01", force_line_count=25)
        assert consistency_score(to_json_safe(rec)) == 1.0


def test_forcing_length_changes_only_the_length():
    """The override must not perturb the rest of the document, or long and
    short corpora would differ in more ways than the one under test."""
    from idb.generate import generate_invoice
    base, _ = generate_invoice(90002, "t03")
    forced, _ = generate_invoice(90002, "t03", force_line_count=25)
    for field in ("seller_name", "buyer_name", "seller_gstin", "invoice_date",
                  "place_of_supply", "invoice_number"):
        assert base[field] == forced[field], field
