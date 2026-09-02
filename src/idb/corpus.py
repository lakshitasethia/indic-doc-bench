"""Corpus construction and the manifest that locks it.

The manifest carries a SHA-256 of every image plus the exact ground truth. It
is written once and then treated as immutable: the point is that a frontier
sweep, an open-model sweep run a week later, and the rules baseline all saw
byte-identical inputs. Regenerating the corpus with a different library version
and re-running one model is how a benchmark quietly stops being a comparison.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Dict, Iterable, List, Optional

from .degrade import LEVELS, degrade_pdf
from .generate import generate_invoice, to_json_safe
from .render import Renderer, build_html, template_ids

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _rel(p: pathlib.Path) -> str:
    """Manifest paths are repo-relative so the corpus can be moved, zipped, or
    uploaded to HuggingFace without every path in it going stale."""
    p = pathlib.Path(p).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_corpus(n: int, out_dir: pathlib.Path, levels: Optional[List[str]] = None,
                 seed0: int = 10000, dpi_clean: int = 300) -> Dict:
    """Generate n invoices, render each, and produce every requested severity.

    Documents are assigned to templates round-robin so template is balanced by
    construction rather than by luck -- an unbalanced template mix would
    confound every per-template analysis downstream.
    """
    levels = levels or LEVELS
    tids = template_ids()
    out_dir = pathlib.Path(out_dir).resolve()
    (out_dir / "pdf").mkdir(parents=True, exist_ok=True)

    docs: List[Dict] = []
    with Renderer() as R:
        for i in range(n):
            tid = tids[i % len(tids)]
            seed = seed0 + i
            doc_id = "syn%05d" % i
            record, ctx = generate_invoice(seed, tid)
            html = build_html(record, ctx, tid)
            pdf = out_dir / "pdf" / ("%s.pdf" % doc_id)
            R.render(html, pdf, None, dpi=dpi_clean)

            variants = {}
            for lv in levels:
                ext = ".png" if lv == "L0_clean" else ".jpg"
                dest = out_dir / lv / ("%s__%s%s" % (doc_id, lv, ext))
                man = degrade_pdf(pdf, dest, lv, seed)
                pages = [pathlib.Path(p) for p in man["pages"]]
                variants[lv] = {
                    "files": [_rel(p) for p in pages],
                    "sha256": [sha256_file(p) for p in pages],
                    "params": {k: v for k, v in man.items() if k not in ("pages", "source_pdf")},
                }

            docs.append({
                "doc_id": doc_id,
                "seed": seed,
                "template_id": tid,
                "source": "synthetic",
                "meta": ctx["meta"],
                "ground_truth": to_json_safe(record),
                "pdf": _rel(pdf),
                "variants": variants,
            })

    manifest = {
        "corpus_version": "v1",
        "n_documents": len(docs),
        "levels": levels,
        "templates": tids,
        "documents": docs,
    }
    mpath = out_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=1))
    manifest["manifest_sha256"] = sha256_file(mpath)
    mpath.write_text(json.dumps(manifest, indent=1))
    return manifest


def load_manifest(path: pathlib.Path) -> Dict:
    return json.loads(pathlib.Path(path).read_text())


def verify_manifest(manifest: Dict) -> List[str]:
    """Re-hash every file. Run this before any paid sweep: a corpus that
    changed under you invalidates every number computed against it."""
    problems = []
    for d in manifest["documents"]:
        for lv, v in d["variants"].items():
            for rel, expect in zip(v["files"], v["sha256"]):
                p = ROOT / rel
                if not p.exists():
                    problems.append("missing: %s" % rel)
                elif sha256_file(p) != expect:
                    problems.append("hash mismatch: %s" % rel)
    return problems


def iter_tasks(manifest: Dict, levels: Optional[List[str]] = None) -> Iterable[Dict]:
    for d in manifest["documents"]:
        for lv, v in d["variants"].items():
            if levels and lv not in levels:
                continue
            yield {
                "doc_id": d["doc_id"],
                "variant_id": "%s__%s" % (d["doc_id"], lv),
                "level": lv,
                "template_id": d["template_id"],
                "files": [str(ROOT / f) for f in v["files"]],
                "ground_truth": d["ground_truth"],
                "meta": d["meta"],
            }
