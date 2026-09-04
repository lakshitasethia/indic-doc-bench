"""Malformed model output must degrade to a score, never take down the run.

Every input here is something a model can actually emit. The distinction that
matters is between a bad *answer*, which must be scored, and a crash, which
loses the whole sweep -- including the documents that were extracted fine.
"""
import json

import pytest

from idb.align import align
from idb.consistency import consistency_score
from idb.normalize import norm_money, norm_quantity
from idb.report import error_mix, leaderboard, score_run
from idb.runner import cost_summary
from idb.score import score_document

GT = {
    "invoice_number": "INV/1", "seller_name": "A Ltd", "buyer_name": "B Ltd",
    "seller_gstin": "27AAPFU0939F1ZV", "buyer_gstin": None,
    "total_taxable_value": "100.00", "cgst_amount": "9.00",
    "sgst_amount": "9.00", "igst_amount": None, "grand_total": "118.00",
    "line_items": [{"description": "X", "hsn_sac": "1234", "quantity": 1,
                    "unit": "PCS", "unit_price": 100, "taxable_value": 100,
                    "tax_rate": 18}],
}

MALFORMED = [
    ("none", None), ("empty dict", {}), ("a list", []), ("a string", "nope"),
    ("line_items None", {**GT, "line_items": None}),
    ("line_items a string", {**GT, "line_items": "nope"}),
    ("line_items [None]", {**GT, "line_items": [None]}),
    ("line_items ['str']", {**GT, "line_items": ["str"]}),
    ("line_items [[]]", {**GT, "line_items": [[]]}),
    ("dict inside a field", {**GT, "seller_name": {"a": 1}}),
    ("list inside a field", {**GT, "grand_total": [1, 2]}),
    ("devanagari value", {**GT, "seller_name": "शर्मा टेक्सटाइल्स"}),
    ("100k-char value", {**GT, "seller_name": "x" * 100_000}),
    ("negative total", {**GT, "grand_total": "-118.00"}),
]


@pytest.mark.parametrize("label,pred", MALFORMED, ids=[m[0] for m in MALFORMED])
def test_malformed_output_scores_rather_than_crashes(label, pred):
    result = score_document("d__L0_clean", "m", GT, pred)
    assert result.fields, label
    consistency_score(pred)


# json.loads accepts these bare tokens, so a model can put them in a record
# that parses cleanly and then explodes in Decimal.quantize().
NON_FINITE = [float("inf"), float("-inf"), float("nan"),
              "NaN", "Infinity", "-Infinity", "-inf", "1e400"]


@pytest.mark.parametrize("value", NON_FINITE)
def test_non_finite_amounts_are_rejected_not_crashed_on(value):
    pred = {**GT, "grand_total": value, "quantity": value}
    score_document("d__L0_clean", "m", GT, pred)
    consistency_score(pred)
    for fn in (norm_money, norm_quantity):
        parsed, ok = fn(value)
        assert parsed is None, "%r must not parse to a usable number" % (value,)
        assert ok is False


def test_json_really_does_accept_nan_and_infinity():
    """The premise of the test above; if this ever changes, that guard is
    still correct but its justification has moved."""
    loaded = json.loads('{"a": NaN, "b": Infinity, "c": -Infinity}')
    assert loaded["a"] != loaded["a"]          # NaN
    assert loaded["b"] == float("inf")


@pytest.mark.parametrize("gt_items,pred_items", [
    ([], []), (GT["line_items"], []), ([], GT["line_items"]),
    (GT["line_items"], [None]), (GT["line_items"], ["str"]),
    (GT["line_items"], [{}]),
])
def test_align_survives_malformed_line_items(gt_items, pred_items):
    align(gt_items, pred_items)


def test_empty_inputs_do_not_break_the_report_layer():
    assert score_run([], {}) == []
    assert leaderboard({}, {}) == []
    assert leaderboard({"m": []}, {}) == []
    assert error_mix([])["wrong"] == 0
    assert cost_summary([]) == {}
    # Every call failed: no cost or latency can be reported, and inventing a
    # zero would put a 0.00s latency row in the table.
    assert cost_summary([{"error": "402", "latency_s": 0.2}]) == {}
