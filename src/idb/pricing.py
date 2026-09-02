"""Live pricing for OpenRouter-hosted models.

Hand-typing rate cards is how a cost table goes stale without anyone noticing.
OpenRouter publishes current per-token prices on a free metadata endpoint, so
the rates are fetched, cached with a timestamp, and committed to the repo. The
cache file is the audit trail: anyone reading the report can see exactly which
rates produced the cost-per-1000-documents column and when they were true.

Anthropic first-party rates stay hand-verified in models.py -- there is no
equivalent endpoint, and a wrong rate there is worth catching by hand.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
from typing import Dict, Optional, Tuple

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "openrouter_pricing.json"
ENDPOINT = "https://openrouter.ai/api/v1/models"


def _key() -> str:
    # A key pasted into a shell profile usually carries a trailing newline,
    # which httpx rejects with an opaque LocalProtocolError rather than a 401.
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def refresh(timeout: float = 30.0) -> Dict:
    """Fetch the full catalogue and cache prices in USD per million tokens."""
    headers = {"Authorization": "Bearer %s" % _key()} if _key() else {}
    r = httpx.get(ENDPOINT, headers=headers, timeout=timeout)
    r.raise_for_status()
    out: Dict[str, Dict] = {}
    for m in r.json().get("data", []):
        p = m.get("pricing") or {}
        arch = m.get("architecture") or {}
        # Check list membership, not substring-of-the-whole-dict: the loose form
        # matched audio and music models whose metadata merely mentions images.
        mods = arch.get("input_modalities")
        if isinstance(mods, (list, tuple)):
            is_vision = "image" in mods
        else:
            is_vision = "image" in str(arch.get("modality") or "")
        try:
            pin = float(p.get("prompt", 0)) * 1e6
            pout = float(p.get("completion", 0)) * 1e6
        except (TypeError, ValueError):
            continue
        # Router sentinels (openrouter/auto) publish a large negative price.
        # They are not real models and must never reach a cost table.
        if pin < 0 or pout < 0:
            continue
        out[m["id"]] = {
            "input_per_mtok": round(pin, 6),
            "output_per_mtok": round(pout, 6),
            "vision": is_vision,
            "context": m.get("context_length"),
        }
    payload = {
        "source": ENDPOINT,
        "fetched_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "n_models": len(out),
        "models": out,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, indent=1, sort_keys=True))
    return payload


def load(auto_refresh: bool = True) -> Dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    if auto_refresh:
        return refresh()
    raise FileNotFoundError("no pricing cache; run `idb pricing --refresh`")


def get(model_id: str) -> Optional[Tuple[float, float]]:
    entry = (load().get("models") or {}).get(model_id)
    if entry is None:
        return None
    return entry["input_per_mtok"], entry["output_per_mtok"]


def vision_models(max_input_price: Optional[float] = None) -> Dict[str, Dict]:
    models = {k: v for k, v in (load().get("models") or {}).items() if v["vision"]}
    if max_input_price is not None:
        models = {k: v for k, v in models.items() if v["input_per_mtok"] <= max_input_price}
    return models
