"""Data feeds for the monthly refresh: OpenRouter, Artificial Analysis, LiteLLM price map."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from typing import Any

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models"
LITELLM_PRICE_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)


class FeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenRouterModel:
    model_id: str            # e.g. "google/gemini-2.5-flash-lite"
    provider: str            # e.g. "google"
    name: str
    input_usd_per_m: float
    output_usd_per_m: float
    context_window: int
    supports_tools: bool
    supports_json: bool


@dataclass(frozen=True)
class AAModel:
    slug: str
    name: str
    creator: str
    intelligence_idx: float | None
    coding_idx: float | None
    agentic_idx: float | None


def _per_m(value: Any) -> float | None:
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return None


def parse_openrouter(payload: dict[str, Any]) -> list[OpenRouterModel]:
    """Keep paid, text-output, concrete models; drop routers, aliases and :free variants."""
    out: list[OpenRouterModel] = []
    for m in payload.get("data", []):
        mid = m.get("id", "")
        if not mid or "/" not in mid or mid.startswith(("~", "openrouter/")) or ":" in mid:
            continue
        outputs = (m.get("architecture") or {}).get("output_modalities") or ["text"]
        if "text" not in outputs:
            continue
        pricing = m.get("pricing") or {}
        inp, outp = _per_m(pricing.get("prompt")), _per_m(pricing.get("completion"))
        # Negative prices mark dynamic routers; zero means a rate-limited free endpoint.
        if inp is None or outp is None or inp < 0 or outp < 0 or (inp == 0 and outp == 0):
            continue
        params = set(m.get("supported_parameters") or [])
        ctx = m.get("context_length") or (m.get("top_provider") or {}).get("context_length") or 0
        out.append(
            OpenRouterModel(
                model_id=mid,
                provider=mid.split("/", 1)[0],
                name=m.get("name", mid),
                input_usd_per_m=round(inp, 6),
                output_usd_per_m=round(outp, 6),
                context_window=int(ctx),
                supports_tools="tools" in params,
                supports_json=bool(params & {"response_format", "structured_outputs"}),
            )
        )
    return out


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_artificial_analysis(payload: dict[str, Any]) -> list[AAModel]:
    out: list[AAModel] = []
    for m in payload.get("data", []):
        ev = m.get("evaluations") or {}
        slug = m.get("slug")
        if not slug:
            continue
        out.append(
            AAModel(
                slug=slug,
                name=m.get("name", slug),
                creator=(m.get("model_creator") or {}).get("name", ""),
                intelligence_idx=_num(ev.get("artificial_analysis_intelligence_index")),
                coding_idx=_num(ev.get("artificial_analysis_coding_index")),
                agentic_idx=_num(ev.get("artificial_analysis_agentic_index")),
            )
        )
    return out


def fetch_openrouter(client: httpx.Client) -> list[OpenRouterModel]:
    r = client.get(OPENROUTER_MODELS_URL)
    if r.status_code != 200:
        raise FeedError(f"OpenRouter /models returned {r.status_code}")
    return parse_openrouter(r.json())


def fetch_artificial_analysis(client: httpx.Client, api_key: str | None = None) -> list[AAModel]:
    key = api_key or os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY")
    if not key:
        raise FeedError("ARTIFICIAL_ANALYSIS_API_KEY is not set")
    r = client.get(AA_MODELS_URL, headers={"x-api-key": key})
    if r.status_code != 200:
        raise FeedError(f"Artificial Analysis API returned {r.status_code}")
    return parse_artificial_analysis(r.json())


def fetch_litellm_price_map(client: httpx.Client | None) -> dict[str, Any]:
    """Remote price map, falling back to the copy bundled with the pinned litellm."""
    if client is not None:
        try:
            r = client.get(LITELLM_PRICE_MAP_URL)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
    bundled = resources.files("litellm") / "model_prices_and_context_window_backup.json"
    return json.loads(bundled.read_text())
