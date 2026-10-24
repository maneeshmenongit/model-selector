"""N2: effort normalization (Q2) and percentile tier bands (D1).

Ranking-side code: it reads scores, prices, capabilities and the effort suffix of a model's
score entry, never who makes or serves the model (N-T3 scans this file).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Iterable

from tokenrouter.catalog.join import build_aa_families, effort_of
from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.sources import AAModel
from tokenrouter.catalog.tiering import assign_tiers, qualifies
from tokenrouter.config import Settings, TierBand

MEASURED, EFFORT_UNVERIFIED, NO_EFFORT_PARAM = "measured", "effort_unverified", "no_effort_param"


def percentile(values: Iterable[float], p: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    xs = sorted(values)
    if not xs:
        raise ValueError("percentile of no values")
    k = (len(xs) - 1) * p / 100
    lo = math.floor(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


@dataclass(frozen=True)
class EffortGap:
    """Max-effort (plain slug) vs medium-effort score, over every AA family that publishes both."""
    families: tuple[tuple[str, float, float, float], ...] = ()   # (family, plain, medium, gap fraction)

    @property
    def count(self) -> int:
        return len(self.families)

    def _pct(self, p: float) -> float:
        return percentile([f[3] for f in self.families], p) if self.families else 0.0

    @property
    def median(self) -> float:
        return self._pct(50)

    @property
    def p25(self) -> float:
        return self._pct(25)

    @property
    def p75(self) -> float:
        return self._pct(75)


def effort_gap(aa: Iterable[AAModel]) -> EffortGap:
    out = []
    for base, variants in sorted(build_aa_families(aa).items()):
        plain, medium = variants.get(""), variants.get("medium")
        if (plain and medium and plain.intelligence_idx and medium.intelligence_idx is not None):
            gap = (plain.intelligence_idx - medium.intelligence_idx) / plain.intelligence_idx
            out.append((base, plain.intelligence_idx, medium.intelligence_idx, gap))
    return EffortGap(tuple(out))


def normalize(rows: Iterable[CatalogRow], median_gap: float) -> list[CatalogRow]:
    """Put every score on the medium-effort footing, the same way for every row.

    A score from the plain slug is a max-effort score. If the model accepts a reasoning
    parameter it is lowered by the median gap (and called at medium effort); otherwise it
    is kept. Scores AA measured at a named effort are used as published.
    """
    out = []
    for r in rows:
        published = r.aa_score if r.aa_score is not None else r.intelligence_idx
        if published is None:
            out.append(r)
            continue
        if effort_of(r.aa_slug):
            score, basis = published, MEASURED
        elif r.supports_reasoning:
            score, basis = round(published * (1 - median_gap), 2), EFFORT_UNVERIFIED
        else:
            score, basis = published, NO_EFFORT_PARAM
        out.append(r.with_(intelligence_idx=score, aa_score=published, score_basis=basis))
    return out


@dataclass(frozen=True)
class DerivedBands:
    bands: dict[str, TierBand]
    floors: dict[str, float] | None = None              # None: fixed floors from config
    n_scores: int = 0
    ceiling_excludes: dict[str, int] = field(default_factory=dict)   # clear the floor, over the price ceiling


def derive_bands(rows: Iterable[CatalogRow], settings: Settings) -> DerivedBands:
    """Floors = configured percentiles of callable models' normalized scores, one per score entry."""
    if not settings.band_percentiles:
        return DerivedBands(bands=settings.tiers)
    rows = list(rows)
    scores: dict[str, float] = {}
    for r in rows:
        if r.callable and r.intelligence_idx is not None:
            scores.setdefault(r.aa_slug or r.model_id, r.intelligence_idx)
    if not scores:
        return DerivedBands(bands=settings.tiers)
    floors = {t: round(percentile(scores.values(), p), 2)
              for t, p in settings.band_percentiles.items() if t in settings.tiers}
    bands = {t: replace(b, min_quality=floors.get(t, b.min_quality)) for t, b in settings.tiers.items()}
    excludes = {}
    for t, b in bands.items():
        unpriced = replace(b, max_blended_price=None)
        excludes[t] = len({r.aa_slug or r.model_id for r in rows
                           if r.callable and qualifies(r, unpriced) and not qualifies(r, b)})
    return DerivedBands(bands=bands, floors=floors, n_scores=len(scores), ceiling_excludes=excludes)


def tier_catalog(rows: Iterable[CatalogRow], settings: Settings, median_gap: float) -> tuple[list[CatalogRow], DerivedBands]:
    """Normalize scores, derive the bands from them, then tier and rank."""
    normalized = normalize(rows, median_gap)
    derived = derive_bands(normalized, settings)
    return assign_tiers(normalized, replace(settings, tiers=derived.bands)), derived
