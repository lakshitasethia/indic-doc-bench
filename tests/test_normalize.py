"""Normalisation must remove representation and preserve meaning -- never repair."""
from decimal import Decimal

import pytest

from idb.normalize import (fuzzy_score, norm_date, norm_money, norm_percent,
                           norm_quantity, norm_state, norm_exact_alnum)


@pytest.mark.parametrize("raw,expect", [
    ("Rs. 2,950.00", "2950.00"), ("₹ 12,34,567.89", "1234567.89"),
    ("1234.5", "1234.50"), ("(450.00)", "-450.00"), ("INR 99", "99.00"),
    ("-450", "-450.00"), ("0", "0.00"), (1234.5, "1234.50"),
    (Decimal("7.005"), "7.01"),
])
def test_money(raw, expect):
    assert norm_money(raw)[0] == Decimal(expect)


def test_money_rejects_garbage_rather_than_guessing():
    val, ok = norm_money("approximately two thousand")
    assert val is None and ok is False


@pytest.mark.parametrize("raw", ["15/01/26", "15-Jan-2026", "2026-01-15",
                                 "15.01.2026", "15 January 2026", "Dated: 15/01/2026"])
def test_dates_all_reach_the_same_iso_value(raw):
    assert norm_date(raw)[0] == "2026-01-15"


def test_dates_are_day_first_not_month_first():
    # 05/03/2026 is 5 March on an Indian invoice, never 3 May.
    assert norm_date("05/03/2026")[0] == "2026-03-05"


def test_impossible_date_is_a_format_error_not_a_silent_none():
    val, ok = norm_date("31/02/2026")
    assert val is None and ok is False


def test_quantity_keeps_three_decimals():
    # Regression: routing quantities through the money parser rounded 75.177 kg
    # to 75.18 and scored a correct extraction as wrong.
    assert norm_quantity("75.177")[0] == Decimal("75.177")
    assert norm_quantity(75.177)[0] == Decimal("75.177")


@pytest.mark.parametrize("raw,expect", [("18", "18.00"), ("18%", "18.00"),
                                        ("0.18", "18.00"), ("9", "9.00")])
def test_percent(raw, expect):
    assert norm_percent(raw)[0] == Decimal(expect)


@pytest.mark.parametrize("raw", ["27-Maharashtra", "Maharashtra", "27", "MH", "maharastra"])
def test_state_resolution(raw):
    assert norm_state(raw)[0] == "27"


def test_hsn_granularity_is_preserved():
    # 4-digit and 8-digit HSN are different filings; unifying them would hide a
    # real error class.
    assert norm_exact_alnum("6109")[0] != norm_exact_alnum("61091000")[0]
    assert norm_exact_alnum(" 6109 ")[0] == "6109"


def test_legal_suffixes_do_not_count_as_a_name_difference():
    from idb.normalize import norm_fuzzy_key
    a = norm_fuzzy_key("Sharma Textiles Pvt. Ltd.")[0]
    b = norm_fuzzy_key("SHARMA TEXTILES PRIVATE LIMITED")[0]
    assert fuzzy_score(a, b) == 100.0


def test_different_companies_do_not_fuzzy_match():
    from idb.normalize import norm_fuzzy_key
    a = norm_fuzzy_key("Sharma Textiles Pvt Ltd")[0]
    b = norm_fuzzy_key("Verma Steels Pvt Ltd")[0]
    assert fuzzy_score(a, b) < 60
