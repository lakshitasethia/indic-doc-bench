"""Model registry: pinned IDs, pricing, and architecture.

Pricing is USD per million tokens, first-party API rates.
  Verified: 2026-09-01 against the Anthropic pricing reference.
  OpenRouter/open-model rates are placeholders until checked -- they change
  often, and an unverified rate in a cost table is worse than no table.

Anything in `PRICING_UNVERIFIED` must be filled in and re-dated before a sweep
whose cost numbers get published.
"""
from __future__ import annotations

from typing import Dict, List

PRICING_VERIFIED_ON = "2026-09-01"

# (input $/MTok, output $/MTok, cached-read $/MTok)
ANTHROPIC_PRICING: Dict[str, tuple] = {
    "claude-opus-5":     (5.0, 25.0, 0.50),
    "claude-sonnet-5":   (2.0, 10.0, 0.20),
    "claude-haiku-4-5":  (1.0,  5.0, 0.10),
    "claude-opus-4-8":   (5.0, 25.0, 0.50),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
}

# Everything else resolves through the cached OpenRouter rate card
# (src/idb/pricing.py), which is fetched live and timestamped.


# A third-party relay reached through an OpenAI-compatible endpoint. Requests
# are prefixed `agentrouter:` so results land in their own directory and are
# never silently pooled with first-party or OpenRouter results: the relay does
# not let us verify which model, version or system prompt actually served a
# request, and that provenance gap has to stay visible in the file layout.
AGENTROUTER_BASE_URL = "https://agentrouter.org/v1"


def build(name: str):
    """Instantiate an adapter by registry name."""
    import os

    from .adapters.rules import RulesBaseline
    from .adapters.vision import AnthropicVision, OpenRouterVision

    if name.startswith("agentrouter:"):
        upstream = name.split(":", 1)[1]
        if not upstream:
            raise SystemExit("usage: agentrouter:<model-id>")
        key = os.environ.get("AGENTROUTER_API_KEY", "").strip()
        if not key:
            raise SystemExit(
                "AGENTROUTER_API_KEY is not set.\n"
                "  add `export AGENTROUTER_API_KEY=...` to ~/.zshrc, then reopen the shell")
        base = os.environ.get("AGENTROUTER_BASE_URL", AGENTROUTER_BASE_URL).rstrip("/")
        # Credits are granted rather than billed, so cost is recorded as zero.
        # The token counts are still logged and remain the honest comparison.
        ad = OpenRouterVision(upstream, 0.0, 0.0, api_key=key, base_url=base)
        ad.name = name
        return ad

    if name in ANTHROPIC_PRICING:
        pin, pout, pcache = ANTHROPIC_PRICING[name]
        return AnthropicVision(name, pin, pout, pcache)
    if name in ("ocr-rules", "ocr-rules-v1"):
        return RulesBaseline()
    # Anything else is treated as an OpenRouter model id, priced from the
    # cached live rate card rather than a hand-typed constant.
    from . import pricing
    rate = pricing.get(name)
    if rate is None:
        raise SystemExit(
            "no pricing found for %r.\n"
            "  - run `idb pricing --refresh` to update the rate card, or\n"
            "  - check the exact model id at https://openrouter.ai/models\n"
            "Known Anthropic ids: %s" % (name, ", ".join(sorted(ANTHROPIC_PRICING))))
    return OpenRouterVision(name, rate[0], rate[1])


def registered() -> List[str]:
    return sorted(ANTHROPIC_PRICING) + ["ocr-rules", "<any openrouter model id>"]
