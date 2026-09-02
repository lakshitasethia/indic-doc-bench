"""The outcome space is the point: wrong, missing and spurious are not the same."""
from idb.schema import FIELD_BY_NAME
from idb.score import (ABSENT_OK, CORRECT, MISSING, SPURIOUS, WRONG,
                       score_document, score_field)

GT = {
    "invoice_number": "INV/2026/0042", "invoice_date": "2026-01-15",
    "seller_name": "Sharma Textiles Pvt Ltd", "seller_gstin": "27AAPFU0939F1ZV",
    "seller_address": "Plot 1, Mumbai", "buyer_name": "Verma Steels",
    "buyer_gstin": None, "buyer_address": "Plot 2, Pune",
    "place_of_supply": "27-Maharashtra", "reverse_charge": False, "irn": None,
    "total_taxable_value": "2500.00", "cgst_amount": "225.00",
    "sgst_amount": "225.00", "igst_amount": None, "cess_amount": None,
    "round_off": "0.00", "grand_total": "2950.00", "amount_in_words": None,
    "line_items": [{"description": "Cotton T-Shirt", "hsn_sac": "6109",
                    "quantity": 10, "unit": "PCS", "unit_price": 250,
                    "discount": None, "taxable_value": 2500, "tax_rate": 18}],
}


def _f(name):
    return FIELD_BY_NAME[name]


def test_omission_and_hallucination_are_different_outcomes():
    assert score_field(_f("seller_gstin"), "27AAPFU0939F1ZV", None).outcome == MISSING
    assert score_field(_f("seller_gstin"), "27AAPFU0939F1ZV",
                       "27AAPFU0939F1Z5").outcome == WRONG


def test_inventing_a_gstin_for_an_unregistered_buyer_is_spurious():
    assert score_field(_f("buyer_gstin"), None, "27AAPFU0939F1ZV").outcome == SPURIOUS


def test_zero_igst_on_an_intrastate_invoice_is_not_a_hallucination():
    # The tax genuinely does not apply; writing 0.00 is a representation choice.
    assert score_field(_f("igst_amount"), None, 0).outcome == ABSENT_OK
    # ...but writing a number is fabricating a tax liability.
    assert score_field(_f("igst_amount"), None, 450).outcome == SPURIOUS


def test_representation_differences_are_not_errors():
    assert score_field(_f("invoice_date"), "2026-01-15", "15/01/26").outcome == CORRECT
    assert score_field(_f("grand_total"), "2950.00", "Rs. 2,950.00").outcome == CORRECT
    assert score_field(_f("seller_name"), "Sharma Textiles Pvt Ltd",
                       "SHARMA TEXTILES PRIVATE LIMITED").outcome == CORRECT


def test_a_perfect_prediction_is_an_exact_match():
    r = score_document("d", "m", GT, GT)
    assert r.exact_match and r.critical_exact_match


def test_a_failed_call_still_counts_against_the_model():
    # Silently dropping failed documents flatters the least reliable model.
    r = score_document("d", "m", GT, None, schema_violation="call_failed")
    assert not r.exact_match
    assert any(f.outcome == MISSING for f in r.fields)
    assert r.line_structural["missing_rows"] == 1


def test_document_exact_match_is_stricter_than_field_accuracy():
    from idb.score import accuracy, doc_exact_match_rate
    pred = dict(GT, invoice_number="INV/2026/0043")
    r = score_document("d", "m", GT, pred)
    assert accuracy([r]) > 0.9
    assert doc_exact_match_rate([r]) == 0.0
