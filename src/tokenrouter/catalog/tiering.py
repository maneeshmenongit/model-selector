"""Apply tier bands and the in-tier ranking rule."""

from __future__ import annotations

import hashlib
from typing import Iterable

from tokenrouter.catalog.models import CatalogRow
from tokenrouter.config import Settings, TierBand


def qualifies(row: CatalogRow, band: TierBand) -> bool:
    if row.intelligence_idx is None or row.intelligence_idx < band.min_quality:
        return False
    if band.max_blended_price is not None and row.blended_price > band.max_blended_price:
        return False
    if row.context_window < band.min_context:
        return False
    if band.tools and not row.supports_tools:
        return False
    if band.json and not row.supports_json:
        return False
    return True


def stable_hash(model_id: str) -> str:
    return hashlib.sha256(model_id.encode()).hexdigest()


def rank_key(row: CatalogRow) -> tuple:
    # Best quality per dollar first. Ties (N-T5): lower blended price, then higher score,
    # then larger context window, then a stable hash of the row id. Ranking never reads
    # the provider, the route or which keys we hold (tests/test_neutrality.py).
    return (-row.quality_per_dollar, row.blended_price, -(row.intelligence_idx or 0), -row.context_window,
            stable_hash(row.model_id))


def assign_tiers(rows: Iterable[CatalogRow], settings: Settings) -> list[CatalogRow]:
    """Return one row per (model, qualifying tier) with rank_in_tier set (1 = primary).

    Bands overlap by design (a strong cheap model qualifies for several tiers), so a
    model can appear in more than one tier, unless `exclusive_tiers` is set. Models
    we can't call are kept in the catalog unranked. Rows that qualify nowhere are
    returned once with tier=None.
    """
    rows = list(rows)
    out: list[CatalogRow] = []
    placed: set[str] = set()
    home: dict[str, str] = {}
    if settings.exclusive_tiers:
        # Each model lives only in the highest tier it qualifies for, so moving a
        # request up a tier always buys a more capable model.
        for name in settings.tier_names:
            for r in rows:
                if qualifies(r, settings.tiers[name]):
                    home[r.model_id] = name
    for name in settings.tier_names:
        band = settings.tiers[name]
        members = sorted((r for r in rows if qualifies(r, band)
                          and (not settings.exclusive_tiers or home.get(r.model_id) == name)), key=rank_key)
        # Snapshots of one model share a score entry; rank distinct models first so a
        # fallback is a different model, not a dated copy of the primary.
        seen: set[str] = set()
        unique, dupes = [], []
        for r in members:
            key = r.aa_slug or r.model_id
            (dupes if r.callable and key in seen else unique).append(r)
            if r.callable:
                seen.add(key)
        rank = 0
        for r in unique + dupes:
            placed.add(r.model_id)
            if r.callable:
                rank += 1
                out.append(r.with_(tier=name, rank_in_tier=rank))
            else:
                out.append(r.with_(tier=name, rank_in_tier=None))
    out.extend(r.with_(tier=None, rank_in_tier=None) for r in rows if r.model_id not in placed)
    return out


def top_members(rows: Iterable[CatalogRow], tier: str, top_n: int) -> list[CatalogRow]:
    ranked = [r for r in rows if r.tier == tier and r.rank_in_tier is not None]
    return sorted(ranked, key=lambda r: r.rank_in_tier)[:top_n]
