"""Command line: build -> run -> report."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Dict, List

from . import corpus as C
from . import models
from .adapters.mock import MockAdapter
from .degrade import LEVELS
from .report import (degradation_curve, error_mix, infra_failure_count, leaderboard,
                     markdown_cost, markdown_degradation, markdown_leaderboard,
                     pairwise_tests, repeat_variance, score_run)
from .runner import cost_summary, expand_repeats, load_results, sweep
from . import taxonomy as TX
from .stats import min_detectable_difference

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "data" / "synthetic"
DEFAULT_RAW = ROOT / "results" / "raw"


def cmd_build(args):
    m = C.build_corpus(args.n, pathlib.Path(args.out), levels=args.levels,
                       seed0=args.seed0, doc_prefix=args.prefix,
                       line_count_range=tuple(args.line_items) if args.line_items else None,
                       corpus_version=args.corpus_version)
    print("built %d documents x %d levels -> %s"
          % (m["n_documents"], len(m["levels"]), args.out))
    print("manifest sha256: %s" % m.get("manifest_sha256"))


def cmd_verify(args):
    m = C.load_manifest(pathlib.Path(args.manifest))
    problems = C.verify_manifest(m)
    print("OK: %d documents verified" % m["n_documents"] if not problems
          else "\n".join(problems[:50]))
    return 1 if problems else 0


def _adapters(names: List[str], manifest: Dict):
    gt = {d["doc_id"]: d["ground_truth"] for d in manifest["documents"]}
    out = []
    for n in names:
        if n.startswith("mock"):
            # mock:<error_rate> lets the harness be exercised at known error levels
            rate = float(n.split(":")[1]) if ":" in n else 0.10
            out.append(MockAdapter(gt, error_rate=rate, name=n))
        else:
            out.append(models.build(n))
    return out


def cmd_run(args):
    manifest = C.load_manifest(pathlib.Path(args.manifest))
    tasks = list(C.iter_tasks(manifest, levels=args.levels))
    if args.limit:
        tasks = tasks[:args.limit]
    tasks = expand_repeats(tasks, args.repeat)
    for ad in _adapters(args.models, manifest):
        print("running %s on %d tasks" % (ad.name, len(tasks)))
        sweep(ad, tasks, pathlib.Path(args.out), workers=args.workers,
              overwrite=args.overwrite)


def _level(dr):
    parts = dr.doc_id.split("__")
    return parts[1].split("#r")[0] if len(parts) > 1 else "L0_clean"


def cmd_report(args):
    manifest = C.load_manifest(pathlib.Path(args.manifest))
    gt_by_doc = {d["doc_id"]: d["ground_truth"] for d in manifest["documents"]}
    tpl_by_doc = {d["doc_id"]: d["template_id"] for d in manifest["documents"]}

    scored, costs, excluded = {}, {}, {}
    for model in args.models:
        raw = load_results(pathlib.Path(args.raw), model)
        if not raw:
            print("no results for %s" % model, file=sys.stderr)
            continue
        scored[model] = score_run(raw, gt_by_doc)
        costs[model] = cost_summary(raw)
        excluded[model] = infra_failure_count(raw)
        if excluded[model]:
            print("%s: %d/%d calls failed (infrastructure, excluded from scoring)"
                  % (model, excluded[model], len(raw)), file=sys.stderr)
    scored = {m: rs for m, rs in scored.items() if rs}
    if not scored:
        raise SystemExit("nothing to report: every call failed")
    if not scored:
        raise SystemExit("nothing to report")

    parts = []
    parts.append(markdown_leaderboard(leaderboard(scored, tpl_by_doc),
                                      "Overall (all severity levels)", excluded))
    parts.append("")
    parts.append(markdown_leaderboard(leaderboard(scored, tpl_by_doc, level="L0_clean"),
                                      "Clean documents only"))
    if any(excluded.values()):
        parts.append("")
        parts.append("_Calls that never reached the model (transport, auth or billing "
                     "failures) are excluded from every accuracy number above and "
                     "counted in the `Calls failed` column. They are not scored as "
                     "omissions: an unpaid invoice is not a model error. Rows with a "
                     "large failed count rest on a smaller corpus than the others and "
                     "are not comparable at face value._")
    parts.append("")
    parts.append(markdown_degradation(degradation_curve(scored, tpl_by_doc)))
    parts.append("")
    parts.append(markdown_cost(costs))
    parts.append("")
    parts.append("### Error mix\n")
    parts.append("| Model | wrong | missing | spurious | format | hallucination share |")
    parts.append("|---|---:|---:|---:|---:|---:|")
    for m, rs in scored.items():
        e = error_mix(rs)
        parts.append("| %s | %d | %d | %d | %d | %.1f%% |" % (
            m, e["wrong"], e["missing"], e["spurious"], e["format_error"],
            100 * e["hallucination_share_of_errors"]))
    parts.append("")
    parts.append("### Paired comparisons (McNemar, document-level exact match)\n")
    for t in pairwise_tests(scored, tpl_by_doc):
        parts.append("- **%s vs %s** (n=%d): %s; Δ exact match %s"
                     % (t["a"], t["b"], t["n"], t["verdict"], t["delta_exact_match"]))
    # Error taxonomy: auto-classify everything, queue the residue for review.
    all_findings = []
    for model in args.models:
        raw = {r["variant_id"]: r for r in load_results(pathlib.Path(args.raw), model)}
        for dr in scored.get(model, []):
            src = raw.get(dr.doc_id, {})
            all_findings.extend(TX.classify_document(
                dr, level=_level(dr), template_id=tpl_by_doc.get(dr.doc_id.split("__")[0], "?"),
                finish_reason=src.get("finish_reason"), refusal=bool(src.get("refusal"))))
    if all_findings:
        parts.append("")
        parts.append(TX.markdown_distribution(TX.distribution(all_findings)))
        queue = TX.sample_for_review(all_findings, per_stratum=args.review_per_stratum)
        qpath = ROOT / "results" / "review_queue.csv"
        TX.to_csv(queue, qpath)
        parts.append("")
        parts.append("_%d findings auto-classified; %d ambiguous ones sampled "
                     "(stratified by model x severity x field group) into "
                     "`results/review_queue.csv` for manual review._"
                     % (len(all_findings), len(queue)))

    var_lines = []
    for model in args.models:
        raw = load_results(pathlib.Path(args.raw), model)
        v = repeat_variance(raw, gt_by_doc)
        if v.get("n_repeated"):
            var_lines.append(
                "- **%s**: %d documents run %dx — %.1f%% of header fields differed "
                "between runs; mean per-document accuracy spread %.1f pts (max %.1f)."
                % (model, v["n_repeated"], v["runs_per_doc"],
                   100 * v["field_disagreement_rate"],
                   100 * v["mean_doc_accuracy_spread"],
                   100 * v["max_doc_accuracy_spread"]))
    if var_lines:
        parts.append("")
        parts.append("### Run-to-run variance\n")
        parts.append("_`temperature=0` was removed on current frontier models, so "
                     "determinism is measured rather than assumed._\n")
        parts.extend(var_lines)

    n_docs = len(next(iter(scored.values())))
    parts.append("")
    parts.append("_Smallest gap this corpus can resolve at 80%% power: about %.1f points._"
                 % (100 * min_detectable_difference(n_docs)))

    # Figures
    figdir = ROOT / "results" / "figures"
    try:
        from . import figures
        curves = degradation_curve(scored, tpl_by_doc)
        figures.degradation_curve(curves, figdir / "degradation.png")
        figures.error_mix({m: error_mix(rs) for m, rs in scored.items()},
                          figdir / "error_mix.png")
        figures.cost_vs_accuracy(
            [{"model": m,
              "accuracy": leaderboard({m: scored[m]}, tpl_by_doc, B=200)[0]["field_accuracy"].point,
              "inr_per_1k": costs.get(m, {}).get("cost_inr_per_1k_docs", 0.0)}
             for m in scored],
            figdir / "cost_vs_accuracy.png")
        parts.append("")
        parts.append("### Figures\n")
        for f in ("degradation.png", "error_mix.png", "cost_vs_accuracy.png"):
            parts.append("![%s](figures/%s)" % (f.replace(".png", ""), f))
    except Exception as e:
        print("figures skipped: %s: %s" % (type(e).__name__, e), file=sys.stderr)

    md = "\n".join(parts)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(md)
    print("\nwritten to %s" % out)


def cmd_triage(args):
    """Suggest categories for the residue the auto-classifier could not call."""
    from . import triage as TR

    manifest = C.load_manifest(pathlib.Path(args.manifest))
    gt_by_doc = {d["doc_id"]: d["ground_truth"] for d in manifest["documents"]}
    queue = pathlib.Path(args.queue)
    if not queue.exists():
        raise SystemExit("no review queue at %s -- run `idb report` first" % queue)

    rows = TR.triage_queue(queue, gt_by_doc, pathlib.Path(args.raw))
    TR.write(rows, pathlib.Path(args.out))

    counts = TR.summarise(rows)
    ambiguous = sum(v for k, v in counts.items() if k.startswith("AMBIGUOUS"))
    print("triaged %d rows -> %s\n" % (len(rows), args.out))
    for cat in sorted(counts, key=lambda c: -counts[c]):
        print("  %-24s %3d" % (cat, counts[cat]))
    print("\n  %d suggested, %d left for review (%.0f%%)"
          % (len(rows) - ambiguous, ambiguous, 100.0 * ambiguous / max(len(rows), 1)))
    print("\nSuggestions are suggestions: `suggestion_basis` carries the evidence, "
          "and\nnothing marked AMBIGUOUS has been guessed at.")


def cmd_pricing(args):
    from . import pricing
    if args.refresh:
        payload = pricing.refresh()
        print("fetched %d models at %s" % (payload["n_models"], payload["fetched_utc"]))
    data = pricing.load()
    vis = pricing.vision_models(max_input_price=args.max_price)
    print("rate card fetched: %s" % data["fetched_utc"])
    print("%d vision models at or below $%s/Mtok input\n" % (len(vis), args.max_price))
    rows = sorted(vis.items(), key=lambda kv: kv[1]["input_per_mtok"])
    print("%-46s %10s %10s" % ("model id", "in $/Mtok", "out $/Mtok"))
    for mid, v in rows[:args.limit]:
        print("%-46s %10.3f %10.3f" % (mid, v["input_per_mtok"], v["output_per_mtok"]))


def main(argv=None):
    ap = argparse.ArgumentParser("idb", description="Indian document extraction benchmark")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="generate + render + degrade the corpus")
    b.add_argument("--n", type=int, default=30)
    b.add_argument("--out", default=str(DEFAULT_CORPUS))
    b.add_argument("--levels", nargs="*", default=LEVELS, choices=LEVELS)
    b.add_argument("--prefix", default="syn",
                   help="document id prefix; use a distinct one for a probe corpus "
                        "so result files never collide with the main corpus")
    b.add_argument("--seed0", type=int, default=10000)
    b.add_argument("--line-items", nargs=2, type=int, metavar=("LO", "HI"),
                   help="force line-item counts into [LO,HI] instead of the natural "
                        "distribution, for probing the long-table regime (METHODOLOGY 8b)")
    b.add_argument("--corpus-version", default="v1")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="re-hash the corpus against its manifest")
    v.add_argument("--manifest", default=str(DEFAULT_CORPUS / "manifest.json"))
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("run", help="sweep models over the corpus")
    r.add_argument("--models", nargs="+", required=True)
    r.add_argument("--manifest", default=str(DEFAULT_CORPUS / "manifest.json"))
    r.add_argument("--out", default=str(DEFAULT_RAW))
    r.add_argument("--levels", nargs="*", default=None)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--overwrite", action="store_true")
    r.add_argument("--repeat", type=int, default=1,
                   help="run each document N times to measure run-to-run variance "
                        "(temperature=0 is unavailable on current frontier models)")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("triage", help="suggest categories for the review queue residue")
    t.add_argument("--queue", default=str(ROOT / "results" / "review_queue.csv"))
    t.add_argument("--manifest", default=str(DEFAULT_CORPUS / "manifest.json"))
    t.add_argument("--raw", default=str(DEFAULT_RAW))
    t.add_argument("--out", default=str(ROOT / "results" / "review_queue_triaged.csv"))
    t.set_defaults(func=cmd_triage)

    pr = sub.add_parser("pricing", help="show/refresh the OpenRouter rate card")
    pr.add_argument("--refresh", action="store_true")
    pr.add_argument("--max-price", type=float, default=1.0)
    pr.add_argument("--limit", type=int, default=30)
    pr.set_defaults(func=cmd_pricing)

    p = sub.add_parser("report", help="score and print the tables")
    p.add_argument("--models", nargs="+", required=True)
    p.add_argument("--manifest", default=str(DEFAULT_CORPUS / "manifest.json"))
    p.add_argument("--raw", default=str(DEFAULT_RAW))
    p.add_argument("--out", default=str(ROOT / "results" / "report.md"))
    p.add_argument("--review-per-stratum", type=int, default=8,
                   help="ambiguous findings to sample per (model x level x group) "
                        "for the manual taxonomy pass")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
