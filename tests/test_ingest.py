"""Layer 3 ingestion: the labels are the new error source, so distrust them.

On synthetic documents ground truth is exact by construction. On real ones a
human types it, and a typo becomes a permanent 'model error' that no amount of
model improvement can fix. These tests are mostly about catching that.
"""
import json

import pytest

from idb.ingest import (build_real_manifest, label_template,
                        redaction_checklist, validate_label)
from idb.schema import ALL_HEADER, LINE_ITEM_FIELDS
from tests.test_score import GT


def _good_label():
    rec = {f.name: GT.get(f.name) for f in ALL_HEADER}
    rec["line_items"] = [{f.name: GT["line_items"][0].get(f.name)
                          for f in LINE_ITEM_FIELDS}]
    return rec


def test_label_template_emits_every_field_including_nullable_ones():
    """An omitted key is indistinguishable from 'absent on the document'."""
    t = label_template(n_line_items=3)
    for f in ALL_HEADER:
        assert f.name in t, f.name
    assert len(t["line_items"]) == 3
    for f in LINE_ITEM_FIELDS:
        assert f.name in t["line_items"][0], f.name


def test_a_good_label_validates_clean():
    errors, warnings = validate_label(_good_label())
    assert errors == []


def test_omitted_field_is_an_error_not_a_silent_null():
    rec = _good_label(); rec.pop("seller_gstin")
    errors, _ = validate_label(rec)
    assert any("seller_gstin" in e for e in errors)


def test_unknown_field_is_rejected():
    rec = _good_label(); rec["vendor_name"] = "X"
    errors, _ = validate_label(rec)
    assert any("vendor_name" in e for e in errors)


def test_underscore_keys_are_labeller_metadata_not_schema_violations():
    rec = _good_label(); rec["_meta"] = {"layout_group": "supplier_a"}
    errors, _ = validate_label(rec)
    assert errors == []


def test_null_in_a_non_nullable_field_is_an_error():
    rec = _good_label(); rec["invoice_number"] = None
    errors, _ = validate_label(rec)
    assert any("invoice_number" in e for e in errors)


def test_broken_arithmetic_warns_but_does_not_block():
    """A real invoice can genuinely fail to reconcile, so this is a prompt to
    look again rather than proof of a mistake."""
    rec = _good_label(); rec["grand_total"] = "999999.00"
    errors, warnings = validate_label(rec)
    assert errors == []
    assert any("arithmetic" in w for w in warnings)


def test_strict_arithmetic_promotes_it_to_an_error():
    rec = _good_label(); rec["grand_total"] = "999999.00"
    errors, _ = validate_label(rec, strict_arithmetic=True)
    assert any("arithmetic" in e for e in errors)


def test_non_object_label_is_rejected():
    for bad in (None, [], "text", 3):
        errors, _ = validate_label(bad)
        assert errors


def _inbox(tmp_path, files):
    for name, content in files.items():
        p = tmp_path / name
        if name.endswith(".json"):
            p.write_text(json.dumps(content))
        else:
            p.write_bytes(b"\xff\xd8\xff")      # a stub image; only hashed
    return tmp_path


def test_document_ids_come_from_filenames_not_enumeration_order(tmp_path):
    """A counter renumbers every later document when one is added earlier in
    the sort, silently invalidating results keyed by the old ids -- and a real
    corpus grows one document at a time."""
    good = _good_label()
    box = _inbox(tmp_path, {"bill_b.jpg": None, "bill_b.json": good})
    m1, _ = build_real_manifest(box, tmp_path)
    first_id = m1["documents"][0]["doc_id"]

    # Add a document that sorts BEFORE the existing one.
    _inbox(tmp_path, {"bill_a.jpg": None, "bill_a.json": good})
    m2, _ = build_real_manifest(box, tmp_path)
    ids = {d["source_file"]: d["doc_id"] for d in m2["documents"]}
    assert ids["bill_b.jpg"] == first_id, "existing id must not shift"
    assert len(set(ids.values())) == 2


def test_unlabelled_document_is_reported_not_skipped(tmp_path):
    box = _inbox(tmp_path, {"orphan.jpg": None})
    manifest, report = build_real_manifest(box, tmp_path)
    assert manifest["documents"] == []
    assert "orphan.jpg" in report["rejected"]


def test_rejected_count_is_documents_not_error_messages(tmp_path):
    """One bad label produces several messages; reporting those as several
    rejected documents overstates the damage."""
    bad = _good_label(); bad.pop("seller_gstin"); bad.pop("invoice_date")
    box = _inbox(tmp_path, {"b.jpg": None, "b.json": bad})
    _, report = build_real_manifest(box, tmp_path)
    assert len(report["rejected"]) == 1
    assert len(report["errors"]) > 1


def test_real_documents_carry_no_severity_levels(tmp_path):
    box = _inbox(tmp_path, {"b.jpg": None, "b.json": _good_label()})
    manifest, _ = build_real_manifest(box, tmp_path)
    assert manifest["levels"] == ["real"]
    assert manifest["documents"][0]["source"] == "real"


def test_layout_group_sets_the_resampling_cluster(tmp_path):
    """Bills from one supplier share a layout and are not independent draws."""
    a = _good_label(); a["_meta"] = {"layout_group": "supplier_x"}
    b = _good_label(); b["_meta"] = {"layout_group": "supplier_x"}
    box = _inbox(tmp_path, {"one.jpg": None, "one.json": a,
                            "two.jpg": None, "two.json": b})
    manifest, _ = build_real_manifest(box, tmp_path)
    assert {d["template_id"] for d in manifest["documents"]} == {"supplier_x"}
    # and _meta must not leak into the scored ground truth
    assert "_meta" not in manifest["documents"][0]["ground_truth"]


def test_default_cluster_is_the_document_itself(tmp_path):
    box = _inbox(tmp_path, {"solo.jpg": None, "solo.json": _good_label()})
    manifest, _ = build_real_manifest(box, tmp_path)
    d = manifest["documents"][0]
    assert d["template_id"] == d["doc_id"]


def test_redaction_checklist_names_the_out_of_scope_documents():
    text = redaction_checklist()
    for term in ("Aadhaar", "PAN", "voter", "passport"):
        assert term.lower() in text.lower()
