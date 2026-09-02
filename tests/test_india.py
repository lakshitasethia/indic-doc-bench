import random

from idb.india import GOODS, SERVICES, gstin_checksum, gstin_is_valid, make_gstin


def test_checksum_matches_known_valid_gstins():
    # Publicly-published, well-formed GSTINs used purely as check-digit vectors.
    for g in ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "24AAACC1206D1ZM"]:
        assert gstin_is_valid(g), g


def test_single_character_damage_is_detected():
    """The property the consistency metric depends on: a one-character misread
    in a GSTIN fails the check digit almost always."""
    rng = random.Random(0)
    good = make_gstin(rng, "27", "S")
    caught = total = 0
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i in range(14):
        for ch in alphabet:
            if ch == good[i]:
                continue
            total += 1
            if not gstin_is_valid(good[:i] + ch + good[i + 1:]):
                caught += 1
    assert caught / total > 0.95


def test_generated_gstins_are_always_valid():
    rng = random.Random(1)
    for _ in range(500):
        assert gstin_is_valid(make_gstin(rng, rng.choice(["27", "29", "07", "33"]), "A"))


def test_catalogue_rates_are_real_gst_slabs():
    for c in GOODS + SERVICES:
        assert c.rate in (0, 5, 12, 18, 28), c
