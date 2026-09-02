"""Adapter contract.

Every backend -- frontier vision API, open model via a router, OCR feeding a
text model, or a pure rules baseline -- returns the same ModelResponse, so the
scorer never knows or cares which architecture produced a record. The two
architectures (native vision vs OCR-then-text) differ enormously in cost and in
failure mode, and putting them behind one interface is what makes that
comparison possible at all.
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from typing import Any, Dict, NamedTuple, Optional


class ModelResponse(NamedTuple):
    record: Optional[Dict]        # parsed JSON, or None if unparseable
    raw: str                      # verbatim text returned; ALWAYS stored
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    latency_s: float
    error: Optional[str] = None   # transport/API failure
    refusal: bool = False         # model declined to process the document
    finish_reason: Optional[str] = None   # 'stop' | 'length' (truncation) | ...
    extra: Optional[Dict[str, Any]] = None


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json_response(text: str):
    """Recover a JSON object from model output.

    Deliberately forgiving about *packaging* (code fences, a leading sentence)
    and completely unforgiving about *content*: no key renaming, no value
    repair. A model that returns valid JSON with the wrong keys has committed a
    schema violation and must be scored as one, not quietly rescued.

    Returns (record_or_None, violation_or_None).
    """
    if text is None:
        return None, "empty_response"
    s = _FENCE.sub("", text.strip())
    try:
        obj = json.loads(s)
    except Exception:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            return None, "no_json_object"
        try:
            obj = json.loads(s[start:end + 1])
        except Exception as e:
            # Unbalanced braces here almost always mean the response was cut
            # off mid-object, which is a truncation, not a formatting quirk.
            return None, "malformed_json:%s" % type(e).__name__
    if not isinstance(obj, dict):
        return None, "json_not_an_object"
    return obj, None


class Adapter(object):
    """Subclasses implement `extract`. `name` must pin an exact model version."""

    name = "abstract"
    architecture = "unknown"      # 'native_vision' | 'ocr_text' | 'rules'
    price_in_per_mtok = 0.0       # USD per million input tokens
    price_out_per_mtok = 0.0
    price_cached_in_per_mtok = 0.0

    def extract(self, image_path: pathlib.Path) -> ModelResponse:
        raise NotImplementedError

    def cost_usd(self, r: ModelResponse) -> float:
        fresh = max(0, r.input_tokens - r.cached_input_tokens)
        return (fresh * self.price_in_per_mtok
                + r.cached_input_tokens * self.price_cached_in_per_mtok
                + r.output_tokens * self.price_out_per_mtok) / 1_000_000
