"""Triage must explain what it can and refuse to guess at the rest.

The failure mode worth guarding against is not a missed suggestion -- it is a
confident wrong one. A guess written into `suggested_category` is
indistinguishable from a reviewed judgement once it reaches the published
taxonomy, so "no answer" has to stay available and has to stay common.
"""
from idb.taxonomy import CHAR_MISREAD, FIELD_CONFUSION, FORMAT_ERROR, OMISSION
from idb.triage import (AMBIGUOUS_NUMERIC, AMBIGUOUS_TEXT,
                        ENTITY_SUBSTITUTION, arithmetic_story,
                        flat_ground_truth, suggest)

GT = {
    "invoice_number": "INV/2026/0042",
    "seller_name": "Sharma Textiles Pvt Ltd",
    "buyer_name": "Verma Steels",
    "seller_gstin": "27AAPFU0939F1ZV",
    "total_taxable_value": "2500.00",
    "cgst_amount": "225.00",
    "sgst_amount": "225.00",
    "igst_amount": None,
    "grand_total": "2950.00",
    "line_items": [
        {"description": "Cotton T-Shirt", "hsn_sac": "6109", "quantity": 10,
         "unit_price": 250, "taxable_value": 2500, "tax_rate": 18},
    ],
}


def test_predicted_value_from_another_field_is_confusion_not_a_misread():
    cat, why = suggest("seller_name", "Sharma Textiles Pvt Ltd", "Verma Steels", GT)
    assert cat == FIELD_CONFUSION
    assert "buyer_name" in why


def test_a_line_quantity_reported_as_a_tax_amount_is_traced():
    """The rules baseline does this: the first number after a label wins."""
    cat, why = suggest("cgst_amount", "225.00", "10", GT)
    assert cat == FIELD_CONFUSION
    assert "quantity" in why


def test_computed_tax_is_distinguished_from_a_misread():
    """taxable 2500 x 18% / 2 = 225 is the right answer; 2500 x 28% / 2 = 350
    is a model doing arithmetic on the wrong slab rather than reading."""
    cat, why = suggest("cgst_amount", "225.00", "350.00", GT)
    assert cat == FIELD_CONFUSION
    assert "computed" in why


def test_numeric_closeness_is_measured_numerically_not_by_edit_distance():
    near = suggest("grand_total", "2950.00", "2950.01", GT)
    far = suggest("grand_total", "2950.00", "2050.00", GT)
    assert near[0] == CHAR_MISREAD
    # One digit apart as a string, wildly apart as a number.
    assert far[0] == AMBIGUOUS_NUMERIC


def test_same_digits_different_separators_is_a_format_error():
    cat, _ = suggest("grand_total", "2950.00", "2,950.00", GT)
    assert cat == FORMAT_ERROR


def test_entity_substitution_keeps_the_shape_and_invents_the_name():
    cat, why = suggest("seller_name", "Deshmukh Industries", "Redbrick Industries", GT)
    assert cat == ENTITY_SUBSTITUTION
    assert "industries" in why.lower()


def test_unrelated_text_is_left_for_a_human():
    cat, _ = suggest("seller_name", "Sharma Textiles Pvt Ltd", "from billed to invoice", GT)
    assert cat == AMBIGUOUS_TEXT


def test_empty_prediction_is_an_omission():
    cat, _ = suggest("seller_name", "Sharma Textiles Pvt Ltd", "", GT)
    assert cat == OMISSION


def test_flat_ground_truth_indexes_line_items_by_position():
    flat = flat_ground_truth(GT)
    assert "verma steels" in flat
    assert flat["cotton t-shirt"] == ["line[0].description"]
    # Nulls must not become a lookup key, or every null-predicting model
    # would appear to be quoting a real field.
    assert "" not in flat
    assert "none" not in flat


def test_arithmetic_story_returns_none_when_the_number_means_nothing():
    """The genuinely interesting case, which must reach a human."""
    assert arithmetic_story("cgst_amount", 987654.32, GT) is None


def test_a_field_is_never_confused_with_itself():
    """Same field on another line is misalignment, which taxonomy.py owns."""
    cat, _ = suggest("taxable_value", "2500", "2500", GT)
    assert cat != FIELD_CONFUSION
