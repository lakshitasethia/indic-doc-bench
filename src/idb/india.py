"""India-specific primitives: GSTIN/PAN construction and validation, state codes,
HSN/SAC catalogue, UQC units, and name/address generation.

Everything here is deterministic given a seed. GSTIN checksums are *real* — a
generated GSTIN passes the same check-digit test the GST portal applies — which
is what makes `gstin_is_valid()` usable as a zero-ground-truth confidence signal
on model output (see idb.consistency).
"""
from __future__ import annotations

import random
import string
from typing import Dict, List, NamedTuple, Optional

# ---------------------------------------------------------------------------
# State codes (first two digits of a GSTIN). Source: GST state code list.
# ---------------------------------------------------------------------------
STATE_CODES: Dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory",
}
STATE_NAME_TO_CODE: Dict[str, str] = {v.lower(): k for k, v in STATE_CODES.items()}

# Common aliases seen on real invoices, for the ENUM_STATE normaliser.
STATE_ALIASES: Dict[str, str] = {
    "j&k": "01", "jammu & kashmir": "01", "hp": "02", "punjab": "03",
    "uttaranchal": "05", "new delhi": "07", "delhi ncr": "07", "nct of delhi": "07",
    "up": "09", "u.p.": "09", "wb": "19", "w.b.": "19", "mp": "23", "m.p.": "23",
    "bombay": "27", "maharastra": "27", "mh": "27", "bangalore": "29",
    "karnatka": "29", "ka": "29", "tn": "33", "tamilnadu": "33", "madras": "33",
    "ap": "37", "telengana": "36", "orissa": "21", "pondicherry": "34",
    "dadra & nagar haveli": "26", "daman & diu": "26",
}

# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------
_ALPHABET = string.digits + string.ascii_uppercase  # 0-9 then A-Z -> values 0..35
_CHAR_VALUE = {c: i for i, c in enumerate(_ALPHABET)}

# PAN 4th character = holder type. Businesses that issue tax invoices are
# overwhelmingly C (company), F (firm/LLP), P (proprietor), H (HUF), A (AOP).
PAN_ENTITY_TYPES = ["C", "F", "P", "H", "A", "T", "G"]


def gstin_checksum(first14: str) -> str:
    """Return the 15th (check) character for the first 14 characters of a GSTIN.

    Weights alternate 1,2 across positions; each product is reduced by
    ``quotient + remainder`` mod 36, and the check value completes the sum to a
    multiple of 36.
    """
    if len(first14) != 14:
        raise ValueError("GSTIN prefix must be exactly 14 characters")
    total = 0
    factor = 1
    for ch in first14.upper():
        if ch not in _CHAR_VALUE:
            raise ValueError("invalid GSTIN character: %r" % ch)
        product = _CHAR_VALUE[ch] * factor
        total += product // 36 + product % 36
        factor = 2 if factor == 1 else 1
    return _ALPHABET[(36 - total % 36) % 36]


def gstin_is_valid(gstin: Optional[str]) -> bool:
    """Structural + checksum validity. Used as a ground-truth-free signal."""
    if not gstin:
        return False
    g = gstin.strip().upper().replace(" ", "")
    if len(g) != 15:
        return False
    if g[0:2] not in STATE_CODES:
        return False
    pan = g[2:12]
    if not (pan[0:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha()):
        return False
    if g[12] not in _ALPHABET or g[13] not in _ALPHABET:
        return False
    try:
        return g[14] == gstin_checksum(g[:14])
    except ValueError:
        return False


def gstin_state_code(gstin: Optional[str]) -> Optional[str]:
    if not gstin or len(gstin.strip()) < 2:
        return None
    code = gstin.strip()[:2]
    return code if code in STATE_CODES else None


def make_pan(rng: random.Random, surname_initial: str, entity: str = "C") -> str:
    letters = string.ascii_uppercase
    head = "".join(rng.choice(letters) for _ in range(3))
    return "%s%s%s%04d%s" % (
        head, entity, surname_initial.upper(),
        rng.randint(0, 9999), rng.choice(letters),
    )


def make_gstin(rng: random.Random, state_code: str, surname_initial: str,
               entity: str = "C") -> str:
    """Build a structurally valid, checksum-correct GSTIN.

    These are synthetic: the PAN body is random, so the number is well-formed
    but (with overwhelming probability) not allotted to any real taxpayer.
    """
    pan = make_pan(rng, surname_initial, entity)
    reg = rng.choice("123456789")   # registration number within state
    default_z = "Z"                 # 14th char is 'Z' by default
    prefix = "%s%s%s%s" % (state_code, pan, reg, default_z)
    return prefix + gstin_checksum(prefix)


# ---------------------------------------------------------------------------
# HSN / SAC catalogue — real codes with their common GST slab.
# ---------------------------------------------------------------------------
class Commodity(NamedTuple):
    hsn: str
    description: str
    rate: float          # total GST %
    unit: str
    price_low: float
    price_high: float


GOODS: List[Commodity] = [
    Commodity("1006", "Basmati Rice (Broken 5%)", 5, "KGS", 60, 140),
    Commodity("0902", "Assam CTC Tea Leaf", 5, "KGS", 180, 420),
    Commodity("1701", "Refined Sugar S-30", 5, "KGS", 38, 52),
    Commodity("1905", "Glucose Biscuits 200g", 18, "BOX", 220, 480),
    Commodity("2106", "Instant Drink Premix", 18, "PCS", 95, 260),
    Commodity("3004", "Paracetamol IP 650mg Tablets", 12, "BOX", 18, 65),
    Commodity("3401", "Toilet Soap 100g", 18, "PCS", 28, 62),
    Commodity("3402", "Detergent Powder 1kg", 18, "PCS", 85, 180),
    Commodity("3208", "Synthetic Enamel Paint 4L", 18, "LTR", 320, 690),
    Commodity("3923", "HDPE Packing Bags", 18, "KGS", 110, 185),
    Commodity("4819", "Corrugated Carton Box 5-Ply", 18, "NOS", 22, 78),
    Commodity("4820", "Hard Bound Register 200 Pages", 18, "NOS", 45, 120),
    Commodity("5208", "Cotton Woven Fabric 44 inch", 5, "MTR", 78, 210),
    Commodity("6109", "Cotton Round Neck T-Shirt", 5, "PCS", 180, 420),
    Commodity("6403", "Leather Formal Shoes", 18, "PRS", 890, 2400),
    Commodity("7214", "TMT Steel Bar Fe-500 12mm", 18, "TON", 48000, 62000),
    Commodity("7308", "MS Fabricated Structure", 18, "KGS", 82, 140),
    Commodity("7318", "MS Hex Bolt with Nut M12", 18, "NOS", 12, 42),
    Commodity("8215", "Stainless Steel Cutlery Set", 18, "SET", 240, 780),
    Commodity("8413", "Monoblock Water Pump 1HP", 18, "NOS", 3400, 7800),
    Commodity("8443", "Multifunction Laser Printer", 18, "NOS", 14500, 28900),
    Commodity("8471", "Laptop Computer i5 16GB", 18, "NOS", 48000, 92000),
    Commodity("8517", "Smartphone 6GB/128GB", 18, "NOS", 12999, 42999),
    Commodity("8544", "PVC Insulated Copper Cable 2.5sqmm", 18, "MTR", 32, 88),
    Commodity("9403", "Office Workstation Table", 18, "NOS", 6200, 18500),
    Commodity("9404", "Foam Mattress 72x36", 18, "NOS", 3800, 11500),
    Commodity("2523", "OPC 53 Grade Cement 50kg", 28, "BAG", 350, 460),
    Commodity("4011", "Radial Tyre 195/65 R15", 28, "NOS", 4800, 9200),
]

SERVICES: List[Commodity] = [
    Commodity("998313", "Information Technology Consulting Services", 18, "NOS", 15000, 180000),
    Commodity("998721", "Repair Services of Computers and Peripherals", 18, "NOS", 800, 6500),
    Commodity("997212", "Rental Services of Commercial Property", 18, "NOS", 22000, 145000),
    Commodity("996511", "Road Transport Services of Goods", 5, "NOS", 2500, 38000),
    Commodity("999293", "Commercial Training and Coaching Services", 18, "NOS", 5000, 45000),
    Commodity("998399", "Other Professional and Technical Services", 18, "NOS", 8000, 90000),
]

# GST Unit Quantity Codes actually used on e-invoices.
UQC = ["NOS", "PCS", "KGS", "LTR", "MTR", "BOX", "SET", "BAG", "BTL", "TON",
       "SQM", "DOZ", "PRS", "BDL", "CTN"]

# ---------------------------------------------------------------------------
# Business names, addresses
# ---------------------------------------------------------------------------
_NAME_HEADS = [
    "Sharma", "Verma", "Patel", "Reddy", "Iyer", "Nair", "Banerjee", "Gupta",
    "Agarwal", "Deshmukh", "Rao", "Menon", "Chauhan", "Joshi", "Kulkarni",
    "Bhatt", "Sethi", "Mehra", "Pillai", "Ganguly", "Mahajan", "Naidu",
    "Chowdhury", "Trivedi", "Shetty", "Kaur", "Ahluwalia", "Fernandes",
]
_NAME_MIDS = [
    "Enterprises", "Industries", "Trading Co.", "Traders", "Agencies",
    "Distributors", "Solutions", "Technologies", "Textiles", "Engineering Works",
    "Sales Corporation", "& Sons", "Marketing", "Overseas", "Steel & Alloys",
    "Polymers", "Infratech", "Logistics", "Pharma", "Electricals",
]
_SUFFIX = ["Private Limited", "Pvt. Ltd.", "LLP", "", "", "Limited"]

_CITIES = {
    "27": ["Mumbai", "Pune", "Nagpur", "Nashik", "Thane"],
    "29": ["Bengaluru", "Mysuru", "Hubballi", "Mangaluru"],
    "07": ["New Delhi", "Delhi"],
    "24": ["Ahmedabad", "Surat", "Rajkot", "Vadodara"],
    "33": ["Chennai", "Coimbatore", "Madurai", "Salem"],
    "36": ["Hyderabad", "Warangal"],
    "09": ["Noida", "Ghaziabad", "Kanpur", "Lucknow", "Agra"],
    "19": ["Kolkata", "Howrah", "Siliguri"],
    "32": ["Kochi", "Thiruvananthapuram", "Kozhikode"],
    "06": ["Gurugram", "Faridabad", "Panipat"],
    "08": ["Jaipur", "Jodhpur", "Udaipur"],
    "23": ["Indore", "Bhopal", "Jabalpur"],
    "03": ["Ludhiana", "Amritsar", "Jalandhar"],
    "21": ["Bhubaneswar", "Cuttack"],
    "10": ["Patna", "Muzaffarpur"],
}
_AREA = [
    "Industrial Estate", "MIDC Phase II", "Sector 63", "GIDC Estate",
    "Peenya 2nd Stage", "Ambattur Industrial Estate", "Okhla Phase I",
    "Focal Point", "SIDCO Complex", "Udyog Vihar", "Naraina Vihar",
    "Kalbadevi Road", "Chandni Chowk", "Ring Road", "MG Road",
]
_STREET = ["Plot No.", "Shop No.", "Unit No.", "Gala No.", "Door No.", "Khasra No."]


def company_name(rng: random.Random) -> str:
    head = rng.choice(_NAME_HEADS)
    mid = rng.choice(_NAME_MIDS)
    suf = rng.choice(_SUFFIX)
    name = ("%s %s %s" % (head, mid, suf)).strip()
    return " ".join(name.split())


# PIN prefixes by state. A wrong PIN is a tell: a model can infer state from
# the PIN, and a Delhi address with a 7-series PIN is a document that could not
# exist, which quietly makes the corpus easier than reality.
_PIN_PREFIX: Dict[str, List[int]] = {
    "01": [180, 194], "02": [171, 177], "03": [140, 160], "04": [160, 160],
    "05": [244, 263], "06": [121, 136], "07": [110, 110], "08": [301, 345],
    "09": [201, 285], "10": [800, 855], "11": [737, 737], "12": [790, 792],
    "13": [797, 798], "14": [795, 795], "15": [796, 796], "16": [799, 799],
    "17": [793, 794], "18": [781, 788], "19": [700, 743], "20": [813, 835],
    "21": [751, 770], "22": [490, 497], "23": [450, 488], "24": [360, 396],
    "26": [396, 396], "27": [400, 445], "29": [560, 591], "30": [403, 403],
    "31": [682, 682], "32": [670, 695], "33": [600, 643], "34": [605, 609],
    "35": [744, 744], "36": [500, 509], "37": [515, 535], "38": [194, 194],
    "97": [110, 110],
}


def pincode(rng: random.Random, state_code: str) -> str:
    lo, hi = _PIN_PREFIX.get(state_code, [110, 799])
    return "%03d%03d" % (rng.randint(lo, hi), rng.randint(1, 999))


def address(rng: random.Random, state_code: str) -> Dict[str, str]:
    city = rng.choice(_CITIES.get(state_code, ["Industrial Area"]))
    line1 = "%s %d, %s" % (rng.choice(_STREET), rng.randint(1, 480), rng.choice(_AREA))
    pin = pincode(rng, state_code)
    return {
        "line1": line1,
        "city": city,
        "state": STATE_CODES[state_code],
        "state_code": state_code,
        "pincode": pin,
        "full": "%s, %s, %s - %s" % (line1, city, STATE_CODES[state_code], pin),
    }


BUSY_STATES = ["27", "29", "07", "24", "33", "36", "09", "19", "32", "06",
               "08", "23", "03", "21", "10"]
