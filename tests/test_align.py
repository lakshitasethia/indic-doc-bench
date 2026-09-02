"""Line-item alignment must survive reordering, merges, splits and omissions."""
from idb.align import align

GT = [
    {"description": "Cotton Round Neck T-Shirt", "hsn_sac": "6109", "quantity": 10,
     "unit_price": 250, "taxable_value": 2500},
    {"description": "Leather Formal Shoes", "hsn_sac": "6403", "quantity": 2,
     "unit_price": 1200, "taxable_value": 2400},
    {"description": "OPC 53 Grade Cement 50kg", "hsn_sac": "2523", "quantity": 20,
     "unit_price": 400, "taxable_value": 8000},
]


def test_reordering_is_not_an_error():
    pred = [dict(GT[2]), dict(GT[0]), dict(GT[1])]
    a = align(GT, pred)
    assert len(a.pairs) == 3
    assert not a.missing_gt and not a.spurious_pred


def test_merge_is_reported_as_a_merge_not_as_omission_plus_hallucination():
    pred = [dict(GT[2]),
            {"description": "T-Shirt and Shoes", "hsn_sac": "6109",
             "quantity": 12, "taxable_value": "4900.00"}]
    a = align(GT, pred)
    assert len(a.merges) == 1
    assert sorted(a.merges[0][0]) == [0, 1]
    assert not a.missing_gt and not a.spurious_pred


def test_split_is_reported_as_a_split():
    pred = [dict(GT[0]), dict(GT[1]),
            {"description": "OPC Cement (1/2)", "hsn_sac": "2523", "taxable_value": 4800},
            {"description": "OPC Cement (2/2)", "hsn_sac": "2523", "taxable_value": 3200}]
    a = align(GT, pred)
    assert len(a.splits) == 1 and a.splits[0][0] == 2


def test_a_genuinely_dropped_row_is_still_an_omission():
    a = align(GT, [dict(GT[0]), dict(GT[2])])
    assert a.missing_gt == [1] and not a.merges and not a.splits


def test_empty_prediction_makes_every_row_missing():
    a = align(GT, [])
    assert a.missing_gt == [0, 1, 2] and not a.pairs
