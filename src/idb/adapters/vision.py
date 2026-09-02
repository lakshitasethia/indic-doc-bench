"""Native-vision adapters: the image goes straight to the model.

Model IDs are pinned exactly and pricing is recorded next to them. Both are
inputs to a published number, so both live in version control rather than in a
config file someone edits between runs.

PRICING MUST BE VERIFIED against each provider's current page before a sweep
and the check date recorded -- a cost-per-1000-documents table is worthless if
its rate card is a guess, and that table is the most-cited thing in the report.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import pathlib
import time
from typing import Optional

import httpx

from ..prompt import SYSTEM, build_prompt
from .base import Adapter, ModelResponse, parse_json_response

MAX_OUTPUT_TOKENS = 8192      # long line-item tables truncate below this
TIMEOUT_S = 180.0


def _b64(path: pathlib.Path):
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return mime, base64.standard_b64encode(path.read_bytes()).decode()


REFUSAL_MARKERS = (
    "i can't help", "i cannot help", "i can't assist", "i cannot assist",
    "i'm unable to", "i am unable to", "cannot process", "can't process",
    "identity document", "personally identifiable",
)


def looks_like_refusal(text: str) -> bool:
    """Some models decline documents that resemble identity papers. It is a
    real production failure mode, essentially unreported, and it needs to be
    counted separately from a parse failure."""
    t = (text or "").strip().lower()[:600]
    return bool(t) and not t.startswith("{") and any(m in t for m in REFUSAL_MARKERS)


class AnthropicVision(Adapter):
    """Claude vision via the official Anthropic SDK.

    Two things here are easy to get wrong and both are load-bearing:

    * **There is no temperature=0.** `temperature` was removed on Opus 5,
      Sonnet 5 and the 4.6+ family -- sending it returns a 400. The usual
      benchmark ritual of "set temperature to 0 for determinism" is not
      available on these models, so determinism is *measured* instead of
      assumed: see `repeat_runs` in the methodology, which re-runs a subset and
      reports run-to-run variance as its own number.
    * **Adaptive thinking is on by default on Opus 5.** That is left at the
      default rather than disabled, because disabling it is both a quality
      regression and a documented source of tool-call and tag leakage. The
      effort level is recorded with every response so a run is reproducible.
    """

    architecture = "native_vision"

    def __init__(self, model: str, price_in: float, price_out: float,
                 price_cached_in: Optional[float] = None,
                 api_key: Optional[str] = None, use_cache: bool = True,
                 effort: Optional[str] = None, max_tokens: int = MAX_OUTPUT_TOKENS):
        self.name = model
        self.model = model
        self.price_in_per_mtok = price_in
        self.price_out_per_mtok = price_out
        # Cache reads bill at roughly a tenth of the input rate; cache writes at
        # about 1.25x. The instruction block is identical on every call, so this
        # materially changes the cost-per-1000-documents table.
        self.price_cached_in_per_mtok = (price_cached_in if price_cached_in is not None
                                         else price_in * 0.1)
        self.use_cache = use_cache
        self.effort = effort
        self.max_tokens = max_tokens
        import anthropic
        api_key = (api_key or "").strip() or None
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def extract(self, image_path: pathlib.Path) -> ModelResponse:
        import anthropic
        mime, data = _b64(pathlib.Path(image_path))
        system = [{"type": "text", "text": SYSTEM + "\n\n" + build_prompt()}]
        if self.use_cache:
            system[0]["cache_control"] = {"type": "ephemeral"}

        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": mime, "data": data}},
                {"type": "text", "text": "Extract this invoice."},
            ]}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}

        t0 = time.time()
        try:
            msg = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            return ModelResponse(None, "", 0, 0, 0, time.time() - t0,
                                 error="APIStatusError %s: %s" % (e.status_code, e.message))
        except Exception as e:
            return ModelResponse(None, "", 0, 0, 0, time.time() - t0,
                                 error="%s: %s" % (type(e).__name__, e))
        latency = time.time() - t0

        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        rec, _ = parse_json_response(text)

        # A safety decline arrives as stop_reason='refusal' with HTTP 200, not
        # as an exception. Counting it as a parse failure would hide one of the
        # more interesting findings in the taxonomy.
        refusal = msg.stop_reason == "refusal" or looks_like_refusal(text)
        details = getattr(msg, "stop_details", None)

        return ModelResponse(
            rec, text,
            (u.input_tokens or 0) + cache_read + cache_write,
            u.output_tokens or 0, cache_read, latency,
            refusal=refusal,
            finish_reason=("length" if msg.stop_reason == "max_tokens" else msg.stop_reason),
            extra={"stop_reason": msg.stop_reason,
                   "refusal_category": getattr(details, "category", None),
                   "cache_creation_input_tokens": cache_write,
                   "effort": self.effort,
                   "request_id": getattr(msg, "_request_id", None)})


class OpenRouterVision(Adapter):
    """One code path for every OpenAI-compatible endpoint, which is how the
    open-weight models (Qwen2.5-VL, InternVL, ...) get evaluated without
    needing GPUs."""

    architecture = "native_vision"

    def __init__(self, model: str, price_in: float, price_out: float,
                 api_key: Optional[str] = None,
                 base_url: str = "https://openrouter.ai/api/v1"):
        self.name = model
        self.model = model
        self.price_in_per_mtok = price_in
        self.price_out_per_mtok = price_out
        self.base_url = base_url
        self.api_key = (api_key or os.environ.get("OPENROUTER_API_KEY", "")).strip()

    def extract(self, image_path: pathlib.Path) -> ModelResponse:
        mime, data = _b64(pathlib.Path(image_path))
        # temperature=0 is kept here and absent from the Anthropic adapter on
        # purpose: the open-weight and third-party models reached through
        # OpenRouter still accept it, while current Claude models reject it with
        # a 400. The asymmetry is a fact about the providers, not an oversight,
        # and it is one more reason run-to-run variance gets measured rather
        # than assumed.
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": build_prompt()},
                    {"type": "image_url",
                     "image_url": {"url": "data:%s;base64,%s" % (mime, data)}},
                ]},
            ],
        }
        t0 = time.time()
        headers = {"Authorization": "Bearer %s" % self.api_key,
                   "content-type": "application/json"}
        # 429 (rate limit) and 402 (in-flight budget exhausted) are transient
        # and are what a long sweep actually hits. Retry with backoff, honouring
        # Retry-After; a hard 402 with no credit at all will exhaust the retries
        # and surface as an error, which is the correct outcome.
        last_err = None
        for attempt in range(4):
            try:
                r = httpx.post(self.base_url + "/chat/completions", json=body,
                               timeout=TIMEOUT_S, headers=headers)
                if r.status_code in (402, 429, 502, 503, 529):
                    wait = float(r.headers.get("retry-after") or (2 ** attempt) * 5)
                    last_err = "HTTP %d: %s" % (r.status_code, r.text[:200])
                    if attempt < 3:
                        time.sleep(min(wait, 60))
                        continue
                    return ModelResponse(None, "", 0, 0, 0, time.time() - t0,
                                         error=last_err)
                r.raise_for_status()
                j = r.json()
                break
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                if attempt < 3:
                    time.sleep((2 ** attempt) * 3)
                    continue
                return ModelResponse(None, "", 0, 0, 0, time.time() - t0, error=last_err)

        # OpenRouter can return HTTP 200 with an error object and no choices.
        if isinstance(j, dict) and j.get("error") and not j.get("choices"):
            return ModelResponse(None, "", 0, 0, 0, time.time() - t0,
                                 error="provider error: %s" % str(j["error"])[:200])
        latency = time.time() - t0
        ch = (j.get("choices") or [{}])[0]
        text = (ch.get("message") or {}).get("content") or ""
        u = j.get("usage") or {}
        rec, _ = parse_json_response(text)
        return ModelResponse(
            rec, text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
            0, latency, refusal=looks_like_refusal(text),
            finish_reason=ch.get("finish_reason"),
            extra={"finish_reason": ch.get("finish_reason")})
