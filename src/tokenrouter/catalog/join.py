"""Join OpenRouter models to Artificial Analysis scores and resolve LiteLLM names."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.sources import AAModel, OpenRouterModel

# OpenRouter provider prefix -> (LiteLLM provider, env var holding its key)
NATIVE_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "OPENAI_API_KEY"),
    "google": ("gemini", "GEMINI_API_KEY"),
    "mistralai": ("mistral", "MISTRAL_API_KEY"),
    "deepseek": ("deepseek", "DEEPSEEK_API_KEY"),
    "x-ai": ("xai", "XAI_API_KEY"),
}
OPENROUTER_KEY = "OPENROUTER_API_KEY"
_DATE_SUFFIX = re.compile(r"-(?:20\d{6}|20\d{2}-\d{2}-\d{2})$")


def normalize(name: str) -> str:
    """'google/Gemini-2.5-Flash_Lite:beta' -> 'gemini-2-5-flash-lite'."""
    s = name.lower().rsplit("/", 1)[-1].split(":", 1)[0]
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def load_aliases(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return raw.get("aliases") or {}


def build_native_index(price_map: dict[str, Any]) -> dict[tuple[str, str], str]:
    """(litellm_provider, normalized name) -> LiteLLM model string, chat models only."""
    providers = {p for p, _ in NATIVE_PROVIDERS.values()}
    index: dict[tuple[str, str], str] = {}
    for key, info in price_map.items():
        if not isinstance(info, dict) or info.get("mode") != "chat":
            continue
        prov = info.get("litellm_provider")
        if prov not in providers:
            continue
        name = key if "/" in key else f"{prov}/{key}"
        norm = normalize(key)
        index.setdefault((prov, norm), name)
        # 'claude-sonnet-4-20250514' should also answer for OpenRouter's 'claude-sonnet-4'.
        undated = _DATE_SUFFIX.sub("", norm)
        if undated != norm:
            index.setdefault((prov, undated), name)
    return index


@dataclass
class JoinResult:
    rows: list[CatalogRow] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)   # OpenRouter ids with no AA score


def join(
    openrouter: Iterable[OpenRouterModel],
    aa: Iterable[AAModel],
    price_map: dict[str, Any],
    aliases: dict[str, dict[str, Any]],
    available_keys: set[str],
    input_weight: float = 0.75,
    output_weight: float = 0.25,
    refreshed_at: str = "",
) -> JoinResult:
    aa_by_slug = {m.slug: m for m in aa}
    aa_by_norm: dict[str, AAModel] = {}
    for m in aa_by_slug.values():
        aa_by_norm.setdefault(normalize(m.slug), m)
    native_index = build_native_index(price_map)

    result = JoinResult()
    for m in openrouter:
        alias = aliases.get(m.model_id) or {}
        if alias.get("skip"):
            continue
        score = aa_by_slug.get(alias["aa_slug"]) if alias.get("aa_slug") else aa_by_norm.get(normalize(m.model_id))
        if score is None or score.intelligence_idx is None:
            result.unmatched.append(m.model_id)
            continue

        litellm_name, callable_, native = _resolve_litellm(m, alias, native_index, available_keys)
        result.rows.append(
            CatalogRow(
                model_id=m.model_id,
                provider=m.provider,
                litellm_name=litellm_name,
                input_usd_per_m=m.input_usd_per_m,
                output_usd_per_m=m.output_usd_per_m,
                context_window=m.context_window,
                supports_tools=m.supports_tools,
                supports_json=m.supports_json,
                intelligence_idx=score.intelligence_idx,
                coding_idx=score.coding_idx,
                agentic_idx=score.agentic_idx,
                aa_slug=score.slug,
                blended_price=round(input_weight * m.input_usd_per_m + output_weight * m.output_usd_per_m, 6),
                callable=callable_,
                native_key=native,
                refreshed_at=refreshed_at,
            )
        )
    return result


def _resolve_litellm(
    m: OpenRouterModel,
    alias: dict[str, Any],
    native_index: dict[tuple[str, str], str],
    keys: set[str],
) -> tuple[str, bool, bool]:
    """Prefer the provider's own API when we hold its key; else go through OpenRouter."""
    via_openrouter = f"openrouter/{m.model_id}"
    if alias.get("litellm_name"):
        name = alias["litellm_name"]
        if name.startswith("openrouter/"):
            return name, OPENROUTER_KEY in keys, False
        env = _env_for_litellm_name(name)
        return name, env is None or env in keys, env is not None and env in keys

    native = NATIVE_PROVIDERS.get(m.provider)
    if native:
        prov, env = native
        name = native_index.get((prov, normalize(m.model_id)))
        if name and env in keys:
            return name, True, True
    return via_openrouter, OPENROUTER_KEY in keys, False


def _env_for_litellm_name(name: str) -> str | None:
    prefix = name.split("/", 1)[0]
    for prov, env in NATIVE_PROVIDERS.values():
        if prov == prefix:
            return env
    return None  # e.g. ollama/ needs no key
