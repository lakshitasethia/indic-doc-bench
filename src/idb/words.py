"""Indian-system number-to-words (lakh / crore), for the 'amount in words' line.

Present on essentially every Indian invoice and worth scoring: it is the only
field that requires the model to *understand* the total rather than copy it,
and it is where the lakh/crore grouping trips models trained on Western
number words.
"""
from __future__ import annotations

from decimal import Decimal

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def _under_thousand(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
        if n:
            out.append("and")
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
    if n:
        out.append(_ONES[n])
    return " ".join(out)


def number_to_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for div, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if n >= div:
            parts.append(number_to_words(n // div) + " " + label)
            n %= div
    if n:
        parts.append(_under_thousand(n))
    return " ".join(p for p in parts if p)


def rupees_in_words(amount: Decimal) -> str:
    whole = int(amount)
    paise = int((amount - whole) * 100)
    s = "Rupees " + number_to_words(whole)
    if paise:
        s += " and " + number_to_words(paise) + " Paise"
    return s + " Only"
