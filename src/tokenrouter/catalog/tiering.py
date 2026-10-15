"""Apply tier bands and the in-tier ranking rule."""

from __future__ import annotations

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


def rank_key(row: CatalogRow) -> tuple:
    # Best quality per dollar first; ties go to providers we hold a native key for,
    # then higher absolute quality, then id for a stable order.
    return (-row.quality_per_dollar, not row.native_key, -(row.intelligence_idx or 0), row.model_id)


def assign_tiers(rows: Iterable[CatalogRow], settings: Settings) -> list[CatalogRow]:
    """Return one row per (model, qualifying tier) with rank_in_tier set (1 = primary).

    Bands overlap by design (a strong cheap model qualifies for several tiers), so a
    model can appear in more than one tier. Models we can't call are kept in the
    catalog unranked. Rows that qualify nowhere are returned once with tier=None.
    """
    rows = list(rows)
    out: list[CatalogRow] = []
    placed: set[str] = set()
    for name in settings.tier_names:
        band = settings.tiers[name]
        members = sorted((r for r in rows if qualifies(r, band)), key=rank_key)
        rank = 0
        for r in members:
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
