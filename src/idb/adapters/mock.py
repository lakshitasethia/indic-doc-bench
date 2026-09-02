"""A deterministic fake model.

Not a baseline and never reported as a result. It exists so the whole harness --
runner, scorer, alignment, taxonomy, statistics, figures -- can be exercised and
debugged end to end at zero API cost, with error rates dialled to known values.
Debugging a scorer against a paid frontier model is how a project burns its
budget before it has produced a single number.
"""
from __future__ import annotations

import json
import pathlib
import random
import time
from decimal import Decimal
from typing import Dict, Optional

from ..generate import to_json_safe
from .base import Adapter, ModelResponse


class MockAdapter(Adapter):
    architecture = "mock"

    price_in_per_mtok = 3.0
    price_out_per_mtok = 15.0

    def __init__(self, ground_truth: Dict[str, Dict], error_rate: float = 0.10,
                 seed: int = 0, name: str = "mock-v1"):
        self.gt = ground_truth
        self.error_rate = error_rate
        self.seed = seed
        self.name = name

    def _corrupt(self, rec: Dict, rng: random.Random) -> Dict:
        out = json.loads(json.dumps(to_json_safe(rec)))
        keys = [k for k in out if k != "line_items"]
        for k in keys:
            if rng.random() >= self.error_rate:
                continue
            mode = rng.random()
            if mode < 0.35:                       # omission
                out[k] = None
            elif mode < 0.75 and out[k] is not None:   # character misread
                s = str(out[k])
                i = rng.randrange(len(s))
                sub = {"0": "O", "O": "0", "1": "l", "5": "S", "S": "5", "8": "B"}
                out[k] = s[:i] + sub.get(s[i], s[i]) + s[i + 1:]
            elif out[k] is None:                  # spurious value on a null field
                out[k] = "0.00"
        items = out.get("line_items") or []
        if items and rng.random() < self.error_rate:
            roll = rng.random()
            if roll < 0.4:
                items.pop(rng.randrange(len(items)))                    # dropped row
            elif roll < 0.7 and len(items) > 1:                          # merged rows
                a = items.pop(0)
                b = items[0]
                b["description"] = "%s / %s" % (a["description"], b["description"])
                b["taxable_value"] = str(Decimal(a["taxable_value"]) + Decimal(b["taxable_value"]))
            else:
                rng.shuffle(items)                                       # reordered
        out["line_items"] = items
        return out

    # Error rate rises with degradation severity. This is not a claim about
    # any real model -- it exists so that the degradation-curve code path
    # produces a curve during harness testing instead of a flat line, which
    # would leave a bug in the plotting or aggregation invisible until the
    # first paid sweep.
    LEVEL_MULTIPLIER = {"L0_clean": 1.0, "L1_scan": 1.4, "L2_photo": 2.2,
                        "L3_harsh": 3.6}

    def extract(self, image_path: pathlib.Path) -> ModelResponse:
        doc_id = pathlib.Path(image_path).stem
        base_id = doc_id.split("__")[0]
        level = doc_id.split("__")[1] if "__" in doc_id else "L0_clean"
        gt = self.gt.get(base_id)
        t0 = time.time()
        if gt is None:
            return ModelResponse(None, "", 0, 0, 0, time.time() - t0,
                                 error="no ground truth for %s" % base_id)
        rng = random.Random("%s|%d" % (doc_id, self.seed))
        saved = self.error_rate
        self.error_rate = min(0.95, saved * self.LEVEL_MULTIPLIER.get(level, 1.0))
        rec = self._corrupt(gt, rng)
        self.error_rate = saved
        raw = json.dumps(rec)
        time.sleep(0.0)
        # Token counts and latency are stand-ins with the right order of
        # magnitude for a vision call, so the cost/latency tables can be
        # exercised before any real spend.
        in_tok = {"L0_clean": 2400, "L1_scan": 2400, "L2_photo": 1600,
                  "L3_harsh": 1100}.get(level, 1800)
        return ModelResponse(rec, raw, in_tok, len(raw) // 4, 0,
                             time.time() - t0 + rng.uniform(0.8, 4.0),
                             finish_reason="stop")
