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
