"""Provider-neutrality checks N-T1..N-T6 (design phase1b, section 1).

Ranking may read only score, blended price and capabilities. These checks prove it on
real and synthetic catalogs; they run in pytest (tests/test_neutrality.py) and in
`tokenrouter audit`. This module names vendors on purpose: it is the list to look for.
"""

from __future__ import annotations

import ast
import importlib
import io
import random
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from tokenrouter.catalog import scoring, tiering
from tokenrouter.catalog.join import effort_of
from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.tiering import stable_hash
from tokenrouter.config import Settings

# Modules whose logic decides scores, bands, tiers and ranks.
NEUTRAL_MODULES = ("tokenrouter.catalog.tiering", "tokenrouter.catalog.scoring")

# Lower-case vendor, family and gateway names. Matched as whole words, so "meta" does not hit "metadata".
VENDOR_TERMS = (
    "anthropic", "claude", "sonnet", "opus", "haiku", "fable", "openai", "chatgpt", "gpt", "codex",
    "google", "gemini", "gemma", "deepmind", "meta", "llama", "deepseek", "mistral", "mistralai", "mixtral",
    "codestral", "qwen", "alibaba", "x-ai", "xai", "grok", "cohere", "amazon", "nova", "microsoft",
    "nvidia", "nemotron", "moonshot", "kimi", "zhipu", "z-ai", "glm", "minimax", "baidu", "ernie",
    "tencent", "hunyuan", "bytedance", "doubao", "xiaomi", "mimo", "ai21", "jamba", "perplexity",
    "sonar", "upstage", "solar", "inception", "mercury", "inclusionai", "ling", "muse", "openrouter",
    "ollama", "groq",
)
_TERM_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(t) for t in VENDOR_TERMS) + r")(?![a-z])")
# Row fields that identify a vendor or a route; ranking must not read them.
LABEL_ATTRS = ("provider", "native_key", "litellm_name")
# Fields ranking may read. Rows equal on all of them are indistinguishable to a neutral ranker.
RANKING_FIELDS = ("aa_score", "intelligence_idx", "supports_reasoning", "blended_price", "context_window",
                  "supports_tools", "supports_json", "callable")

__all__ = ["CheckResult", "run_all", "stable_hash"]


@dataclass(frozen=True)
class CheckResult:
    check: str
    ok: bool
    detail: str


def latest_catalog(settings: Settings) -> list[CatalogRow]:
    """The latest refresh's rows as the ranker received them (one per model, no tier)."""
    from tokenrouter.catalog.store import CatalogStore

    if not settings.catalog_db.exists():
        return []
    store = CatalogStore(settings.catalog_db)
    try:
        rid = store.latest_refresh_id()
        rows = store.load(rid) if rid else []
    finally:
        store.close()
    seen: dict[str, CatalogRow] = {}
    for r in rows:
        seen.setdefault(r.model_id, r.with_(tier=None, rank_in_tier=None))
    return list(seen.values())


def latest_effort_gap(settings: Settings) -> float:
    """The median effort gap the latest refresh normalized with (0 for refreshes before N2)."""
    from tokenrouter.catalog.store import CatalogStore

    if not settings.catalog_db.exists():
        return 0.0
    store = CatalogStore(settings.catalog_db)
    try:
        rid = store.latest_refresh_id()
        return (store.effort_gap_median(rid) if rid else None) or 0.0
    finally:
        store.close()


def _tiers(rows: list[CatalogRow], settings: Settings, median_gap: float) -> dict[str | None, list[CatalogRow]]:
    """The whole pipeline a refresh runs: normalize scores, derive bands, tier and rank."""
    out: dict[str | None, list[CatalogRow]] = {}
    tiered, _ = scoring.tier_catalog(rows, settings, median_gap)
    for r in tiered:
        out.setdefault(r.tier, []).append(r)
    for members in out.values():
        members.sort(key=lambda r: (r.rank_in_tier is None, r.rank_in_tier or 0, r.model_id))
    return out


def _signature(r: CatalogRow) -> tuple:
    return (*(getattr(r, f) for f in RANKING_FIELDS), effort_of(r.aa_slug))


def _by_fields(members: list[CatalogRow]) -> list[CatalogRow]:
    """Ranked members by rank, then unranked ones in field order: no label decides the order."""
    ranked = [r for r in members if r.rank_in_tier is not None]
    rest = sorted((r for r in members if r.rank_in_tier is None), key=lambda r: repr(_signature(r)))
    return ranked + rest


def _label(r: CatalogRow) -> str:
    return f"{r.model_id} (q={r.intelligence_idx:g}, ${r.blended_price:.3f}/M)"


def label_shuffle_check(rows: list[CatalogRow], settings: Settings, seeds: Iterable[int] = range(20),
                        median_gap: float = 0.0) -> list[str]:
    """N-T1: permute provider/model_id/litellm_name across rows; tiers must not change.

    Compared by the fields ranking may read: rows equal on all of them are true ties,
    which the id hash orders, so their order may swap without any label being read.
    """
    rows = list(rows)
    base = _tiers(rows, settings, median_gap)
    problems = []
    for seed in seeds:
        labels = [(r.provider, r.model_id, r.litellm_name) for r in rows]
        random.Random(seed).shuffle(labels)
        shuffled = [r.with_(provider=p, model_id=m, litellm_name=n) for r, (p, m, n) in zip(rows, labels)]
        got = _tiers(shuffled, settings, median_gap)
        for tier in sorted(set(base) | set(got), key=str):
            want, have = _by_fields(base.get(tier, [])), _by_fields(got.get(tier, []))
            key = lambda rows: [(r.rank_in_tier, _signature(r)) for r in rows]  # noqa: E731
            if key(want) != key(have):
                i = next((k for k, (a, b) in enumerate(zip(key(want), key(have))) if a != b),
                         min(len(want), len(have)))
                was = _label(want[i]) if i < len(want) else "nothing"
                now = _label(have[i]) if i < len(have) else "nothing"
                problems.append(f"seed {seed}: tier {tier} position {i + 1} was {was}, now {now}")
                break
    return problems


def provider_rename_check(rows: list[CatalogRow], settings: Settings, seeds: Iterable[int] = range(5),
                          median_gap: float = 0.0) -> list[str]:
    """N-T2: replace every provider string with a random token; no tier or rank may change."""
    rows = list(rows)
    base = {t: [(r.rank_in_tier, r.model_id) for r in m] for t, m in _tiers(rows, settings, median_gap).items()}
    problems = []
    for seed in seeds:
        rng = random.Random(seed)
        tokens = {p: f"vendor-{rng.getrandbits(32):08x}" for p in sorted({r.provider for r in rows})}
        renamed = [r.with_(provider=tokens[r.provider]) for r in rows]
        got = {t: [(r.rank_in_tier, r.model_id) for r in m] for t, m in _tiers(renamed, settings, median_gap).items()}
        for tier in sorted(set(base) | set(got), key=str):
            if base.get(tier) != got.get(tier):
                problems.append(f"seed {seed}: tier {tier} changed: {base.get(tier)} -> {got.get(tier)}")
                break
    return problems


def _module_paths() -> list[Path]:
    return [Path(importlib.import_module(m).__file__) for m in NEUTRAL_MODULES]


def _terms(text: str) -> list[str]:
    return sorted(set(_TERM_RE.findall(text.lower())))


def static_check(paths: Iterable[Path] | None = None) -> list[str]:
    """N-T3: no vendor names in string literals or comments, and no reads of label fields."""
    problems = []
    for path in paths if paths is not None else _module_paths():
        src = Path(path).read_text()
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT or tok.type == tokenize.STRING or tok.type == getattr(
                    tokenize, "FSTRING_MIDDLE", -1):
                for term in _terms(tok.string):
                    problems.append(f"{path}:{tok.start[0]}: vendor name {term!r}")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute) and node.attr in LABEL_ATTRS:
                problems.append(f"{path}:{node.lineno}: reads .{node.attr}")
    return problems


def tier_config_check(raw_config: dict[str, Any]) -> list[str]:
    """N-T3 for config: the tier bands name no vendor or model."""
    text = yaml.safe_dump(raw_config.get("tiers") or {})
    return [f"tier config names {t!r}" for t in _terms(text)]


def extra_members_check(settings: Settings) -> list[str]:
    """N-T4: `extra_members` may pin local models only."""
    return [f"extra_members.{tier}: {m.get('litellm_name')!r} is not a local model "
            f"(allowed prefixes: {', '.join(settings.local_model_prefixes)})"
            for tier, members in settings.extra_members.items() for m in members
            if not str(m.get("litellm_name", "")).startswith(settings.local_model_prefixes)]


def tie_break_check() -> list[str]:
    """N-T5: ties go lower price -> higher score -> larger context -> id hash, never to a provider or route."""

    def row(mid, price, q, ctx=200_000, provider="p", native=False, name=None):
        return CatalogRow(model_id=mid, provider=provider, litellm_name=name or mid, input_usd_per_m=price,
                          output_usd_per_m=price, context_window=ctx, supports_tools=True, supports_json=True,
                          intelligence_idx=q, blended_price=price, callable=True, native_key=native)

    def order(rows):
        return [r.model_id for r in sorted(rows, key=tiering.rank_key)]

    problems = []
    if order([row("a", 2.0, 80), row("b", 1.0, 40)]) != ["b", "a"]:
        problems.append("equal score per $ does not go to the lower price")
    if order([row("a", 0.0, 30), row("b", 0.0, 50), row("c", 0.0, 50, ctx=1_000_000)]) != ["c", "b", "a"]:
        problems.append("equal price does not go to higher score, then larger context")
    tied = [row(f"v{i}/m", 1.0, 50, provider=f"v{i}", native=i % 2 == 0) for i in range(6)]
    want = sorted((r.model_id for r in tied), key=stable_hash)
    flipped = [r.with_(provider="x", native_key=not r.native_key, litellm_name="openrouter/" + r.model_id) for r in tied]
    if order(tied) != want or order(flipped) != want:
        problems.append("a full tie is not broken by the id hash alone (provider, key or route changes the order)")
    return problems


def _latest_diff_text(settings: Settings) -> str | None:
    from tokenrouter.catalog.store import CatalogStore

    if not settings.catalog_db.exists():
        return None
    store = CatalogStore(settings.catalog_db)
    try:
        rid = store.latest_refresh_id()
    finally:
        store.close()
    found = sorted(settings.diffs_dir.glob(f"*-refresh-{rid}.md")) if rid else []
    return found[-1].read_text() if found else None


def run_all(
    settings: Settings,
    rows: list[CatalogRow] | None = None,
    diff_text: str | None = None,
    raw_config: dict[str, Any] | None = None,
    median_gap: float | None = None,
) -> list[CheckResult]:
    """Every check, pass or fail, for `tokenrouter audit` and the close-out's neutrality section."""
    if rows is None:
        rows = latest_catalog(settings)
        if median_gap is None:
            median_gap = latest_effort_gap(settings)
    gap = median_gap or 0.0
    if raw_config is None and settings.config_path and settings.config_path.exists():
        raw_config = yaml.safe_load(settings.config_path.read_text()) or {}
    if diff_text is None:
        diff_text = _latest_diff_text(settings)

    def result(check: str, problems: list[str], ok_detail: str) -> CheckResult:
        return CheckResult(check, not problems, "; ".join(problems[:5]) if problems else ok_detail)

    out = []
    if rows:
        out.append(result("N-T1", label_shuffle_check(rows, settings, median_gap=gap),
                          f"{len(rows)} rows, 20 shuffles through normalize -> bands -> rank, tiers unchanged"))
        out.append(result("N-T2", provider_rename_check(rows, settings, median_gap=gap),
                          f"{len(rows)} rows, 5 renames, tiers and ranks unchanged"))
    else:
        out += [CheckResult(c, False, "no catalog snapshot; run `tokenrouter refresh`") for c in ("N-T1", "N-T2")]
    out.append(result("N-T3", static_check() + tier_config_check(raw_config or {}),
                      f"{', '.join(NEUTRAL_MODULES)} and the tier bands name no vendor and read no label field"))
    out.append(result("N-T4", extra_members_check(settings),
                      f"extra_members are all local ({', '.join(settings.local_model_prefixes)})"))
    out.append(result("N-T5", tie_break_check(), "price -> score -> context -> id hash"))
    if diff_text is None:
        out.append(CheckResult("N-T6", False, "no diff for the latest refresh"))
    else:
        out.append(result("N-T6", [] if "## Provider share" in diff_text else
                          ["latest refresh diff has no Provider share section; re-run `tokenrouter refresh`"],
                          "latest refresh diff has the Provider share section"))
    return out
