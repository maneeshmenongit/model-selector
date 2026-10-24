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
    """'acme/Chat-2.5-Flash_Lite:beta' -> 'chat-2-5-flash-lite'."""
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
        # 'acme-chat-4-20250514' should also answer for OpenRouter's 'acme-chat-4'.
        undated = _DATE_SUFFIX.sub("", norm)
        if undated != norm:
            index.setdefault((prov, undated), name)
    return index


# Artificial Analysis lists one slug per reasoning-effort setting. The plain slug is
# the model at its *maximum* effort, which overstates what a default call delivers.
EFFORT_SUFFIXES = ("non-reasoning", "reasoning", "thinking", "adaptive", "minimal",
                   "xhigh", "high", "medium", "low", "max")
_EFFORT_RE = re.compile(r"-(" + "|".join(EFFORT_SUFFIXES) + r")$")
# Efforts LiteLLM can request via `reasoning_effort`; other variants send nothing.
REQUESTABLE_EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT_PREFERENCE = ("medium", "high", "", "non-reasoning", "low", "xhigh", "reasoning",
                             "thinking", "adaptive", "minimal", "max")
_SHORT_DATE = re.compile(r"-\d{4}$")     # OpenRouter snapshot suffix, e.g. -0813


def split_effort(slug: str) -> tuple[str, str]:
    """'acme-chat-5-high' -> ('acme-chat-5', 'high'); plain slugs -> (slug, '')."""
    m = _EFFORT_RE.search(slug)
    return (slug[: m.start()], m.group(1)) if m else (slug, "")


def build_aa_families(aa: Iterable[AAModel]) -> dict[str, dict[str, AAModel]]:
    """normalized base slug -> {effort variant: model}; '' is the plain slug."""
    families: dict[str, dict[str, AAModel]] = {}
    for m in aa:
        base, effort = split_effort(normalize(m.slug))
        families.setdefault(base, {}).setdefault(effort, m)
    return families


# D4: ratified by the design thread (lenient matching beyond the alias map).
def candidate_keys(model_id: str) -> list[str]:
    """Names to try for an OpenRouter id, most specific first."""
    n = normalize(model_id)
    keys = [n, _DATE_SUFFIX.sub("", n), _SHORT_DATE.sub("", n)]
    if n.endswith("-0"):                     # 'mistral-large-4-0' -> 'mistral-large-4'
        keys.append(n[:-2])
    keys.append(f"{n}-preview")              # 'acme-chat-3-1-lite' -> '...-preview'
    out: list[str] = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def pick_variant(family: dict[str, AAModel], preference: Iterable[str]) -> AAModel | None:
    scored = {e: m for e, m in family.items() if m.intelligence_idx is not None}
    for effort in preference:
        if effort in scored:
            return scored[effort]
    return next(iter(scored.values()), None)


def effort_of(aa_slug: str | None) -> str:
    return split_effort(normalize(aa_slug))[1] if aa_slug else ""


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
    effort_preference: Iterable[str] = DEFAULT_EFFORT_PREFERENCE,
) -> JoinResult:
    aa = list(aa)
    aa_by_slug = {m.slug: m for m in aa}
    families = build_aa_families(aa)
    preference = tuple(effort_preference)
    native_index = build_native_index(price_map)

    result = JoinResult()
    for m in openrouter:
        alias = aliases.get(m.model_id) or {}
        if alias.get("skip"):
            continue
        if alias.get("aa_slug"):
            score = aa_by_slug.get(alias["aa_slug"])
        else:
            family = next((families[k] for k in candidate_keys(m.model_id) if k in families), None)
            score = pick_variant(family, preference) if family else None
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
                aa_score=score.intelligence_idx,
                supports_reasoning=m.supports_reasoning,
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


def gateway(litellm_name: str, local_prefixes: Iterable[str] = ("ollama/",)) -> str:
    """How a deployment is reached: 'openrouter', 'local', or 'direct' (the provider's own API)."""
    if litellm_name.startswith("openrouter/"):
        return "openrouter"
    if litellm_name.startswith(tuple(local_prefixes)):
        return "local"
    return "direct"


def _env_for_litellm_name(name: str) -> str | None:
    prefix = name.split("/", 1)[0]
    for prov, env in NATIVE_PROVIDERS.values():
        if prov == prefix:
            return env
    return None  # e.g. ollama/ needs no key
