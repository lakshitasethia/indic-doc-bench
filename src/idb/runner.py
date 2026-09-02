"""Model sweep with cost, latency, and raw-response logging.

Every raw response is written to disk before anything is parsed or scored. The
error taxonomy is built by reading those files weeks later, and re-running a
sweep to recover text you already paid for is a self-inflicted budget wound.

Runs are resumable: an existing result file is skipped. A sweep that dies at
document 380 of 450 must not cost the full amount again.
"""
from __future__ import annotations

import json
import pathlib
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional

from .adapters.base import Adapter, parse_json_response
from .prompt import prompt_fingerprint

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _result_path(out_dir: pathlib.Path, model: str, variant_id: str) -> pathlib.Path:
    return out_dir / model.replace("/", "_") / ("%s.json" % variant_id)


def run_one(adapter: Adapter, task: Dict, out_dir: pathlib.Path,
            overwrite: bool = False) -> Dict:
    path = _result_path(out_dir, adapter.name, task["variant_id"])
    if path.exists() and not overwrite:
        cached = json.loads(path.read_text())
        # Resume must not treat an infrastructure failure as finished work.
        # Otherwise a transient rate limit or an exhausted balance is baked
        # into the results permanently and every later re-run skips it.
        if not cached.get("error"):
            return cached

    started = time.time()
    try:
        resp = adapter.extract(pathlib.Path(task["files"][0]))
    except Exception as e:                       # transport failure, not a model error
        rec = {
            "variant_id": task["variant_id"], "doc_id": task["doc_id"],
            "level": task["level"], "template_id": task["template_id"],
            "model": adapter.name, "architecture": adapter.architecture,
            "error": "%s: %s" % (type(e).__name__, e),
            "traceback": traceback.format_exc()[-2000:],
            "raw": "", "record": None, "schema_violation": "call_failed",
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "latency_s": time.time() - started, "cost_usd": 0.0,
            "prompt": prompt_fingerprint(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=1))
        return rec

    # A transport or billing failure is NOT a model error, and must never be
    # parsed or labelled as one. Before this guard, a 402 from the provider
    # produced an empty body, which parse_json_response then stamped as
    # `no_json_object` -- so "your account is out of credits" was recorded as
    # "this model emits malformed JSON", and it would have gone into the
    # published taxonomy exactly that way. Any adapter-reported error short
    # circuits here and is counted as an infrastructure failure instead.
    if resp.error:
        record, violation = None, "call_failed"
    else:
        record, violation = (resp.record, None)
        if record is None:
            record, violation = parse_json_response(resp.raw)
        if resp.refusal:
            violation = "refusal"
        if resp.finish_reason == "length" and violation is None:
            violation = "truncated_output"

    rec = {
        "variant_id": task["variant_id"], "doc_id": task["doc_id"],
        "level": task["level"], "template_id": task["template_id"],
        "model": adapter.name, "architecture": adapter.architecture,
        "record": record, "raw": resp.raw, "schema_violation": violation,
        "error": resp.error, "refusal": resp.refusal,
        "finish_reason": resp.finish_reason,
        "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
        "cached_input_tokens": resp.cached_input_tokens,
        "latency_s": round(resp.latency_s, 4),
        "cost_usd": adapter.cost_usd(resp),
        "prompt": prompt_fingerprint(),
        "extra": resp.extra,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1))
    return rec


def expand_repeats(tasks: List[Dict], repeat: int) -> List[Dict]:
    """Duplicate each task `repeat` times under distinct variant ids.

    Needed because `temperature=0` is not available on current frontier models
    -- the parameter was removed. Determinism therefore cannot be assumed, so
    it is measured: the same image is sent several times and the disagreement
    between runs is reported as its own number. A benchmark that claims
    deterministic results on these models is describing a setting it did not
    apply."""
    if repeat <= 1:
        return tasks
    out = []
    for t in tasks:
        for i in range(repeat):
            u = dict(t)
            if i:
                u["variant_id"] = "%s#r%d" % (t["variant_id"], i)
            out.append(u)
    return out


def sweep(adapter: Adapter, tasks: List[Dict], out_dir: pathlib.Path,
          workers: int = 4, overwrite: bool = False, progress: bool = True) -> List[Dict]:
    out: List[Dict] = []
    if workers <= 1:
        for i, t in enumerate(tasks):
            out.append(run_one(adapter, t, out_dir, overwrite))
            if progress and (i + 1) % 25 == 0:
                print("  %s: %d/%d" % (adapter.name, i + 1, len(tasks)))
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, adapter, t, out_dir, overwrite): t for t in tasks}
        for i, f in enumerate(as_completed(futs)):
            out.append(f.result())
            if progress and (i + 1) % 25 == 0:
                print("  %s: %d/%d" % (adapter.name, i + 1, len(tasks)))
    return out


def load_results(out_dir: pathlib.Path, model: str) -> List[Dict]:
    d = pathlib.Path(out_dir) / model.replace("/", "_")
    if not d.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def cost_summary(results: List[Dict]) -> Dict:
    """Per-model cost and latency, projected to the unit a CTO actually asks
    about: rupees per thousand documents."""
    # Failed calls are excluded: a 402 returns in milliseconds with zero
    # tokens, which would drag latency percentiles down and understate cost
    # per thousand documents. Cost and latency describe calls that happened.
    results = [r for r in results if not r.get("error")]
    if not results:
        return {}
    n = len(results)
    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    lat = sorted(r.get("latency_s", 0.0) for r in results)
    usd_inr = 83.0
    return {
        "n_calls": n,
        "input_tokens_mean": sum(r.get("input_tokens", 0) for r in results) / n,
        "output_tokens_mean": sum(r.get("output_tokens", 0) for r in results) / n,
        "latency_p50_s": lat[n // 2],
        "latency_p95_s": lat[min(n - 1, int(0.95 * n))],
        "cost_usd_total": total_cost,
        "cost_usd_per_1k_docs": total_cost / n * 1000,
        "cost_inr_per_1k_docs": total_cost / n * 1000 * usd_inr,
        "usd_inr_rate_assumed": usd_inr,
    }
