"""Catalog row: one per model (per qualifying tier) per refresh."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True)
class CatalogRow:
    model_id: str
    provider: str
    litellm_name: str
    input_usd_per_m: float
    output_usd_per_m: float
    context_window: int
    supports_tools: bool
    supports_json: bool
    intelligence_idx: float | None = None
    coding_idx: float | None = None          # D7: stored for a possible coding tier, pending design thread
    agentic_idx: float | None = None
    aa_slug: str | None = None
    blended_price: float = 0.0
    callable: bool = False      # we hold a key that can reach litellm_name
    native_key: bool = False    # that key is the provider's own (tie-break preference)
    tier: str | None = None
    rank_in_tier: int | None = None
    refreshed_at: str = ""
    extra_params: dict[str, Any] | None = None  # litellm_params for extra members

    @property
    def quality_per_dollar(self) -> float:
        if self.intelligence_idx is None:
            return 0.0
        if self.blended_price <= 0:
            return float("inf")
        return self.intelligence_idx / self.blended_price

    def with_(self, **kw: Any) -> "CatalogRow":
        return replace(self, **kw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
