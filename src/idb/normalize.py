"""Type-aware normalisation, applied identically to ground truth and prediction.

Design rule: a normaliser may only remove *representational* difference. It must
never repair a value. Stripping the rupee sign is normalisation; correcting
"O" to "0" inside a GSTIN would be scoring a wrong answer as right, so it is a
degradation of the benchmark, not a feature.

Every normaliser returns ``(value, ok)``. ``ok=False`` means the raw string
could not be interpreted as that type at all — recorded as a *format violation*
rather than silently treated as a miss, because "correct value, unparseable
representation" is its own error class in the taxonomy.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Tuple

from rapidfuzz import fuzz

from .india import STATE_ALIASES, STATE_CODES, STATE_NAME_TO_CODE

# Default fuzzy threshold. Reported in the paper together with a sensitivity
# check at FUZZY_SENSITIVITY, so the headline number cannot be accused of being
# tuned to a favourable cut-point.
FUZZY_THRESHOLD = 88
FUZZY_SENSITIVITY = (80, 95)

_WS = re.compile(r"\s+")
_CURRENCY = re.compile(r"[₹\u20a8$]|\bRs\.?|\bINR\b|\brupees?\b", re.IGNORECASE)
_NEG_PAREN = re.compile(r"^\((.*)\)$")

# Corporate-suffix noise: present or absent on the same company across
# documents, and never a semantic difference. Removed before fuzzy comparison.
_LEGAL_SUFFIX = re.compile(
    r"\b(private limited|pvt\.? ?ltd\.?|p\.? ?ltd\.?|limited|ltd\.?|llp|"
    r"llc|inc\.?|co\.?|company|corporation|corp\.?|& sons|and sons|"
    r"enterprises?|traders?)\b", re.IGNORECASE)

_ADDRESS_NOISE = re.compile(
    r"\b(plot|shop|unit|gala|door|khasra|no\.?|number|floor|flr|road|rd\.?|"
    r"street|st\.?|near|opp\.?|opposite|behind|dist\.?|district|india|"
    r"pin ?code|pin)\b", re.IGNORECASE)


def _clean_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace(" ", " ")
    return _WS.sub(" ", s).strip()


def norm_exact_upper(v: Any) -> Tuple[Optional[str], bool]:
    if v is None:
        return None, True
    s = _clean_text(v).upper().replace(" ", "")
    return (s or None), True


def norm_exact_alnum(v: Any) -> Tuple[Optional[str], bool]:
    """For HSN/SAC: 8-digit and 4-digit forms are different values and are NOT
    unified here — reporting HSN at the wrong granularity is a real error that
    breaks GSTR filing. Only punctuation and whitespace are dropped."""
    if v is None:
        return None, True
    s = re.sub(r"[^0-9A-Za-z]", "", _clean_text(v)).upper()
    return (s or None), True


def _parse_decimal(v: Any) -> Tuple[Optional[Decimal], bool]:
    """Parse a numeric value to an unrounded Decimal.

    Kept separate from the quantising wrappers because quantity fields carry
    three decimal places (74.177 KGS) and money two. Routing quantities through
    the money parser silently rounds them to the paisa, which reads as a model
    error on every fractional-unit line -- a bug that would inflate every
    model's quantity error rate identically and invisibly.
    """
    if v is None:
        return None, True
    if isinstance(v, Decimal):
        return v, True
    if isinstance(v, bool):
        return None, False
    if isinstance(v, (int, float)):
        d = Decimal(str(v))
        # NaN and +/-Infinity parse happily into Decimal and then explode in
        # quantize(). They reach us for real: json.loads accepts the bare
        # tokens NaN, Infinity and -Infinity by default, so a model emitting
        # one produces a record that parses cleanly and crashes scoring. An
        # invoice total is never non-finite, so treat it as unparseable.
        return (d, True) if d.is_finite() else (None, False)
    s = _clean_text(v)
    s = _CURRENCY.sub("", s).strip()
    # A stripped symbol can leave stray punctuation ("Rs. 2,950" -> ". 2,950").
    s = s.lstrip(".:/ ").rstrip(" .:").strip() if s not in (".", "") else s
    neg = False
    m = _NEG_PAREN.match(s)
    if m:
        neg, s = True, m.group(1)
    s = re.sub(r"\b(CR|DR)\b", "", s, flags=re.IGNORECASE).strip()
    s = s.replace(",", "").replace(" ", "")
    if s.startswith("-"):
        neg, s = True, s[1:]
    if s in ("", "-", "."):
        return None, True
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, False
    if not d.is_finite():          # the strings "NaN", "Infinity", "-inf"
        return None, False
    if neg:
        d = -d
    return d, True


def _quantize(d: Decimal, places: str) -> Tuple[Optional[Decimal], bool]:
    """Quantise, or report the value as unusable.

    `Decimal("1e400")` is perfectly finite, so the is_finite() guard in
    `_parse_decimal` lets it through -- and then quantize() raises, because the
    expanded result exceeds the context precision. An invoice amount with 400
    digits is not a number anyone can use, so it is rejected here rather than
    allowed to abort the run.
    """
    try:
        return d.quantize(Decimal(places), rounding=ROUND_HALF_UP), True
    except InvalidOperation:
        return None, False


def norm_money(v: Any) -> Tuple[Optional[Decimal], bool]:
    """Decimal, never float. Handles the rupee sign, 'Rs.', lakh-style comma
    grouping (12,34,567.89), trailing CR/DR, and parenthesised negatives."""
    d, ok = _parse_decimal(v)
    if d is None:
        return None, ok
    q, qok = _quantize(d, "0.01")
    return q, (ok and qok)


def norm_quantity(v: Any) -> Tuple[Optional[Decimal], bool]:
    d, ok = _parse_decimal(v)
    if d is None:
        return None, ok
    q, qok = _quantize(d, "0.001")
    return q, (ok and qok)


def norm_percent(v: Any) -> Tuple[Optional[Decimal], bool]:
    """'18', '18%', '18.00', '0.18' -> Decimal('18.00').

    The 0.18 case is a genuine ambiguity: a rate below 1 is read as a fraction,
    because no Indian GST slab sits between 0 and 1 percent. Documented, and
    exercised by a unit test."""
    if v is None:
        return None, True
    if isinstance(v, str):
        v = v.replace("%", "")
    d, ok = _parse_decimal(v)
    if d is None:
        return None, ok
    if Decimal("0") < d < Decimal("1"):
        d = d * 100
    q, qok = _quantize(d, "0.01")
    return q, qok


_DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %m %Y",
    "%d-%m-%y", "%d/%m/%y", "%d.%m.%y",
    "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y",
    "%d-%b-%y", "%d %b %y", "%b %d, %Y", "%B %d, %Y",
    "%Y/%m/%d", "%Y.%m.%d",
]


def norm_date(v: Any) -> Tuple[Optional[str], bool]:
    """Parse to ISO-8601.

    Indian documents are day-first essentially without exception, so ambiguous
    forms like 05/03/2026 are resolved day-first. US-style month-first parsing
    is never attempted; a fixed, documented convention beats a heuristic that
    silently flips on the 12th of the month."""
    if v is None:
        return None, True
    if isinstance(v, (date, datetime)):
        d = v.date() if isinstance(v, datetime) else v
        return d.isoformat(), True
    s = _clean_text(v)
    s = re.sub(r"^(dated?|invoice date|dt\.?)\s*[:\-]?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    s = s.replace(",", " ")
    s = _WS.sub(" ", s).strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if "%y" in fmt and "%Y" not in fmt:
            # Two-digit years: 00-79 -> 2000s, 80-99 -> 1900s.
            pass  # strptime already applies the POSIX 69/70 pivot; keep it explicit
        return dt.date().isoformat(), True
    return None, False


def norm_state(v: Any) -> Tuple[Optional[str], bool]:
    """Resolve a place-of-supply string to a two-digit GST state code.
    Accepts '27-Maharashtra', 'Maharashtra', '27', 'MH'."""
    if v is None:
        return None, True
    s = _clean_text(v).lower()
    m = re.match(r"^\s*(\d{2})\b", s)
    if m and m.group(1) in STATE_CODES:
        return m.group(1), True
    s = re.sub(r"^\d{1,2}\s*[-–:]\s*", "", s).strip()
    s = re.sub(r"[^a-z& .]", "", s).strip()
    if s in STATE_NAME_TO_CODE:
        return STATE_NAME_TO_CODE[s], True
    if s in STATE_ALIASES:
        return STATE_ALIASES[s], True
    for name, code in STATE_NAME_TO_CODE.items():
        if s and (s in name or name in s):
            return code, True
    return None, False


_TRUE = {"true", "yes", "y", "1", "applicable", "reverse charge applicable"}
_FALSE = {"false", "no", "n", "0", "not applicable", "n/a", "na", "nil", "-"}


def norm_bool(v: Any) -> Tuple[Optional[bool], bool]:
    if v is None:
        return None, True
    if isinstance(v, bool):
        return v, True
    s = _clean_text(v).lower()
    if s in _TRUE:
        return True, True
    if s in _FALSE:
        return False, True
    return None, False


def norm_fuzzy_key(v: Any, kind: str = "name") -> Tuple[Optional[str], bool]:
    """Lower-case, strip punctuation, and drop semantically empty tokens
    (legal suffixes for names, street furniture for addresses)."""
    if v is None:
        return None, True
    s = _clean_text(v).lower()
    s = s.replace("\n", " ")
    if kind == "name":
        s = _LEGAL_SUFFIX.sub(" ", s)
    else:
        s = _ADDRESS_NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _WS.sub(" ", s).strip()
    return (s or None), True


def fuzzy_score(a: Optional[str], b: Optional[str]) -> float:
    """Normalised token-set ratio in [0, 100]. Order-insensitive, so
    'Sharma Textiles Pvt Ltd' and 'Pvt Ltd Sharma Textiles' agree."""
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b))


NORMALIZERS = {
    "exact_upper": lambda v: norm_exact_upper(v),
    "exact_alnum": lambda v: norm_exact_alnum(v),
    "date": lambda v: norm_date(v),
    "money": lambda v: norm_money(v),
    "quantity": lambda v: norm_quantity(v),
    "percent": lambda v: norm_percent(v),
    "state": lambda v: norm_state(v),
    "bool": lambda v: norm_bool(v),
    "fuzzy": lambda v: norm_fuzzy_key(v, "name"),
}


def normalize(ftype: str, value: Any, field_name: str = "") -> Tuple[Any, bool]:
    if ftype == "fuzzy":
        kind = "address" if "address" in field_name else "name"
        return norm_fuzzy_key(value, kind)
    return NORMALIZERS[ftype](value)
