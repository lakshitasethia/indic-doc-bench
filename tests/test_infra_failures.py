"""A failed call is not a model error.

The distinction these tests protect is the difference between "the model
omitted every field" and "our card declined". Both arrive as a result file
with `record: None`; only the first one is a fact about the model.
"""
from idb.report import (infra_failure_count, is_infra_failure, score_run,
                        markdown_leaderboard)
from idb.runner import cost_summary
from tests.test_score import GT


def _ok(vid):
    return {"variant_id": vid, "doc_id": vid.split("__")[0], "model": "m",
            "record": dict(GT), "error": None, "schema_violation": None,
            "latency_s": 20.0, "input_tokens": 3000, "output_tokens": 800,
            "cost_usd": 0.001}


def _billing_failure(vid):
    return {"variant_id": vid, "doc_id": vid.split("__")[0], "model": "m",
            "record": None, "error": "HTTP 402: requires more credits",
            "schema_violation": "call_failed", "latency_s": 0.2,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _refusal(vid):
    """A genuine model behaviour: the call succeeded, the model declined."""
    return {"variant_id": vid, "doc_id": vid.split("__")[0], "model": "m",
            "record": None, "error": None, "schema_violation": "refusal",
            "refusal": True, "latency_s": 5.0, "input_tokens": 3000,
            "output_tokens": 12, "cost_usd": 0.0005}


def test_billing_failure_is_excluded_from_scoring():
    gt = {"doc0": GT, "doc1": GT}
    scored = score_run([_ok("doc0__L0_clean"), _billing_failure("doc1__L0_clean")], gt)
    assert len(scored) == 1
    assert scored[0].doc_id == "doc0__L0_clean"


def test_refusal_is_still_scored_as_the_model_failing():
    """The existing design decision, which this change must not weaken."""
    gt = {"doc0": GT}
    scored = score_run([_refusal("doc0__L0_clean")], gt)
    assert len(scored) == 1, "a refusal is model behaviour and must stay in the denominator"
    assert scored[0].schema_violation == "refusal"


def test_a_swept_model_that_never_answered_scores_nothing_rather_than_zero():
    gt = {"doc%d" % i: GT for i in range(3)}
    rows = [_billing_failure("doc%d__L0_clean" % i) for i in range(3)]
    assert score_run(rows, gt) == []
    assert infra_failure_count(rows) == 3


def test_infra_failure_detection():
    assert is_infra_failure(_billing_failure("d__L0_clean"))
    assert not is_infra_failure(_refusal("d__L0_clean"))
    assert not is_infra_failure(_ok("d__L0_clean"))


def test_cost_summary_ignores_calls_that_never_happened():
    """A 402 returns in 0.2s with no tokens; averaging it in flatters latency."""
    rows = [_ok("d0__L0_clean"), _ok("d1__L0_clean"), _billing_failure("d2__L0_clean")]
    s = cost_summary(rows)
    assert s["n_calls"] == 2
    assert s["latency_p50_s"] == 20.0
    assert s["input_tokens_mean"] == 3000


def test_leaderboard_shows_the_failed_call_count():
    rows = [{"model": "m", "n": 247, "field_accuracy": "78%", "header_accuracy": "73%",
             "line_item_accuracy": "80%", "doc_exact_match": "16%",
             "critical_exact_match": "39%", "hallucination_share": 0.61,
             "structural_errors": 1, "schema_violations": 0}]
    md = markdown_leaderboard(rows, "t", {"m": 137})
    assert "Calls failed" in md
    assert "| 247 | 137 |" in md
