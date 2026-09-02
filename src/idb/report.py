"""Scoring roll-up and the leaderboard/degradation/cost tables."""
from __future__ import annotations

import json
import pathlib
from typing import Dict, List, Optional

from .consistency import consistency_score
from .degrade import LEVELS
from .score import (ABSENT_OK, CORRECT, CORRECT_LIKE, MISSING, SPURIOUS, WRONG,
                    DocResult, accuracy, doc_exact_match_rate, field_table,
                    hallucination_rate, score_document)
from .stats import cluster_bootstrap, mcnemar, paired_delta_ci


def is_infra_failure(r: Dict) -> bool:
    """True when we never got a response from the model at all.

    A transport, auth or billing failure says nothing about the model. It is
    emphatically NOT the same thing as a refusal, a truncation, or malformed
    JSON -- those are model behaviour and `score_document` deliberately scores
    them as all-fields-missing so the denominator still counts them.

    The distinction is load-bearing. When this repository's OpenRouter balance
    ran out mid-sweep, 137 documents came back HTTP 402. Scored naively, that
    is 137 documents on which the model "omitted every field", and the
    published leaderboard would have reported our unpaid invoice as the
    model's error rate."""
    return bool(r.get("error"))


def infra_failure_count(results: List[Dict]) -> int:
    return sum(1 for r in results if is_infra_failure(r))


def score_run(results: List[Dict], gt_by_doc: Dict[str, Dict]) -> List[DocResult]:
    out = []
    for r in results:
        gt = gt_by_doc.get(r["doc_id"])
        if gt is None:
            continue
        if is_infra_failure(r):
            # Excluded from scoring, but never silently: the count is carried
            # into the leaderboard so a thin `n` is always visible.
            continue
        dr = score_document(r["variant_id"], r["model"], gt, r.get("record"),
                            schema_violation=r.get("schema_violation"))
        out.append(dr)
    return out


def _level_of(doc_result: DocResult) -> str:
    parts = doc_result.doc_id.split("__")
    return parts[1] if len(parts) > 1 else "L0_clean"


def _template_of(doc_result: DocResult, tpl_by_doc: Dict[str, str]) -> str:
    return tpl_by_doc.get(doc_result.doc_id.split("__")[0], "unknown")


def leaderboard(scored_by_model: Dict[str, List[DocResult]],
                tpl_by_doc: Dict[str, str], level: Optional[str] = None,
                B: int = 2000) -> List[Dict]:
    rows = []
    for model, results in scored_by_model.items():
        rs = [r for r in results if level is None or _level_of(r) == level]
        if not rs:
            continue
        cl = lambda r: _template_of(r, tpl_by_doc)
        field_ci = cluster_bootstrap(rs, lambda xs: accuracy(xs), cl, B=B)
        header_ci = cluster_bootstrap(rs, lambda xs: accuracy(xs, group="header"), cl, B=B)
        line_ci = cluster_bootstrap(rs, lambda xs: accuracy(xs, group="line_items"), cl, B=B)
        exact_ci = cluster_bootstrap(rs, lambda xs: doc_exact_match_rate(xs), cl, B=B)
        crit_ci = cluster_bootstrap(
            rs, lambda xs: doc_exact_match_rate(xs, critical_only=True), cl, B=B)
        rows.append({
            "model": model,
            "n": len(rs),
            "field_accuracy": field_ci,
            "header_accuracy": header_ci,
            "line_item_accuracy": line_ci,
            "doc_exact_match": exact_ci,
            "critical_exact_match": crit_ci,
            "hallucination_share": hallucination_rate(rs),
            "structural_errors": sum(r.line_structural["merges"] + r.line_structural["splits"]
                                     for r in rs),
            "schema_violations": sum(1 for r in rs if r.schema_violation),
        })
    rows.sort(key=lambda r: -r["field_accuracy"].point)
    return rows


def degradation_curve(scored_by_model: Dict[str, List[DocResult]],
                      tpl_by_doc: Dict[str, str], B: int = 1000) -> Dict[str, Dict]:
    """Accuracy per severity level per model -- the headline figure.

    Reported as a curve because models that are within noise of each other on
    clean input routinely separate by double digits at L3, and a single
    clean-document number hides exactly the behaviour that matters in
    production."""
    out: Dict[str, Dict] = {}
    for model, results in scored_by_model.items():
        per_level = {}
        for lv in LEVELS:
            rs = [r for r in results if _level_of(r) == lv]
            if not rs:
                continue
            per_level[lv] = cluster_bootstrap(
                rs, lambda xs: accuracy(xs), lambda r: _template_of(r, tpl_by_doc), B=B)
        if per_level:
            clean = per_level.get("L0_clean")
            harsh = per_level.get("L3_harsh")
            out[model] = {
                "levels": per_level,
                "drop_clean_to_harsh": (None if not (clean and harsh)
                                        else clean.point - harsh.point),
            }
    return out


def pairwise_tests(scored_by_model: Dict[str, List[DocResult]],
                   tpl_by_doc: Dict[str, str], level: Optional[str] = None) -> List[Dict]:
    """McNemar on document-level exact match, over the intersection of documents
    both models actually produced a result for."""
    models = sorted(scored_by_model)
    out = []
    for i in range(len(models)):
        for j in range(i + 1, len(models)):
            a, b = models[i], models[j]
            am = {r.doc_id: r for r in scored_by_model[a]
                  if level is None or _level_of(r) == level}
            bm = {r.doc_id: r for r in scored_by_model[b]
                  if level is None or _level_of(r) == level}
            common = sorted(set(am) & set(bm))
            if not common:
                continue
            av = [am[k].exact_match for k in common]
            bv = [bm[k].exact_match for k in common]
            mc = mcnemar(av, bv)
            delta = paired_delta_ci([float(x) for x in av], [float(x) for x in bv],
                                    [_template_of(am[k], tpl_by_doc) for k in common],
                                    B=1000)
            out.append({"a": a, "b": b, "n": len(common), "mcnemar": mc,
                        "delta_exact_match": delta, "verdict": mc.verdict()})
    return out


def error_mix(results: List[DocResult]) -> Dict[str, float]:
    t = field_table(results)
    tot = {k: 0 for k in (CORRECT, WRONG, MISSING, SPURIOUS, ABSENT_OK, "format_error")}
    for row in t.values():
        for k in tot:
            tot[k] += row.get(k, 0)
    errs = tot[WRONG] + tot[MISSING] + tot[SPURIOUS]
    return {
        "wrong": tot[WRONG], "missing": tot[MISSING], "spurious": tot[SPURIOUS],
        "format_error": tot["format_error"],
        "hallucination_share_of_errors": (tot[WRONG] + tot[SPURIOUS]) / errs if errs else float("nan"),
    }


def markdown_leaderboard(rows: List[Dict], title: str = "Leaderboard",
                        excluded: Optional[Dict[str, int]] = None) -> str:
    """`excluded` maps model -> calls dropped as infrastructure failures.

    It is rendered as its own column rather than a footnote: a row scored on
    247 of 384 documents must not look like a row scored on all of them."""
    # Counts are corpus-wide, so the column is rendered only on a corpus-wide
    # table. A level-filtered table showing "0 failed" would claim something
    # about that level that these numbers do not support.
    col = "" if excluded is None else " Calls failed |"
    sep = "" if excluded is None else "---:|"
    out = ["### %s\n" % title,
           "| Model | n |%s All fields (95%% CI) | Header fields | Line-item fields | Doc exact match | Critical-field exact | Halluc. share of errors | Struct. err | Schema viol. |" % col,
           "|---|---:|%s---|---|---|---|---|---:|---:|---:|" % sep]
    for r in rows:
        cell = "" if excluded is None else " %d |" % excluded.get(r["model"], 0)
        out.append("| %s | %d |%s %s | %s | %s | %s | %s | %.1f%% | %d | %d |" % (
            r["model"], r["n"], cell,
            r["field_accuracy"], r["header_accuracy"],
            r["line_item_accuracy"], r["doc_exact_match"],
            r["critical_exact_match"], 100 * r["hallucination_share"],
            r["structural_errors"], r["schema_violations"]))
    return "\n".join(out)


def markdown_degradation(curves: Dict[str, Dict]) -> str:
    out = ["### Accuracy by degradation severity\n",
           "| Model | " + " | ".join(LEVELS) + " | Drop L0→L3 |", "|---|" + "---|" * (len(LEVELS) + 1)]
    for model, c in sorted(curves.items(), key=lambda kv: -(kv[1]["levels"].get("L0_clean").point if kv[1]["levels"].get("L0_clean") else 0)):
        cells = []
        for lv in LEVELS:
            ci = c["levels"].get(lv)
            cells.append(str(ci) if ci else "—")
        drop = c["drop_clean_to_harsh"]
        out.append("| %s | %s | %s |" % (model, " | ".join(cells),
                                         "—" if drop is None else "%.1f pts" % (100 * drop)))
    return "\n".join(out)


def markdown_cost(cost_by_model: Dict[str, Dict]) -> str:
    out = ["### Cost and latency\n",
           "| Model | Mean in-tok | Mean out-tok | Latency p50 | Latency p95 | USD / 1k docs | INR / 1k docs |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for m, c in sorted(cost_by_model.items(), key=lambda kv: kv[1].get("cost_usd_per_1k_docs", 0)):
        if not c:
            continue
        out.append("| %s | %.0f | %.0f | %.2fs | %.2fs | $%.2f | ₹%.0f |" % (
            m, c["input_tokens_mean"], c["output_tokens_mean"], c["latency_p50_s"],
            c["latency_p95_s"], c["cost_usd_per_1k_docs"], c["cost_inr_per_1k_docs"]))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Run-to-run variance
# ---------------------------------------------------------------------------
def repeat_variance(results: List[Dict], gt_by_doc: Dict[str, Dict]) -> Dict:
    """Disagreement across repeated calls on the identical image.

    Reports two things: how often repeated runs of the same model on the same
    image produce a different value for a field, and the spread in per-document
    accuracy across runs. Both are lower bounds on how much of any gap between
    two models is noise rather than skill."""
    from collections import defaultdict

    groups = defaultdict(list)
    for r in results:
        base = r["variant_id"].split("#r")[0]
        groups[base].append(r)
    groups = {k: v for k, v in groups.items() if len(v) > 1}
    if not groups:
        return {"n_repeated": 0}

    field_disagree = field_total = 0
    doc_spreads = []
    for base, runs in groups.items():
        gt = gt_by_doc.get(runs[0]["doc_id"])
        if gt is None:
            continue
        runs = [r for r in runs if not is_infra_failure(r)]
        if len(runs) < 2:
            continue
        scored = [score_document(base, runs[0]["model"], gt, r.get("record"),
                                 schema_violation=r.get("schema_violation"))
                  for r in runs]
        accs = [accuracy([s]) for s in scored]
        doc_spreads.append(max(accs) - min(accs))
        # Compare header fields across runs on the raw normalised values.
        by_field = defaultdict(set)
        for s in scored:
            for fr in s.fields:
                if fr.row is None:
                    by_field[fr.field].add(repr(fr.pred_norm))
        for vals in by_field.values():
            field_total += 1
            if len(vals) > 1:
                field_disagree += 1

    return {
        "n_repeated": len(groups),
        "runs_per_doc": len(next(iter(groups.values()))),
        "field_disagreement_rate": field_disagree / field_total if field_total else float("nan"),
        "mean_doc_accuracy_spread": (sum(doc_spreads) / len(doc_spreads)) if doc_spreads else float("nan"),
        "max_doc_accuracy_spread": max(doc_spreads) if doc_spreads else float("nan"),
    }
