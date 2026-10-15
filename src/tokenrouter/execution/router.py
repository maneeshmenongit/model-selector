"""LiteLLM Router wrapper: one model group per tier, ordered members, tier fallbacks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.config import Settings


class RoutingError(RuntimeError):
    """Every model in the tier and its fallbacks failed."""


@dataclass
class Execution:
    response: Any
    tier: str
    model: str                # litellm model string that answered
    deployment_id: str | None
    fallback_used: bool
    prompt_tokens: int
    completion_tokens: int
    usd: float
    llm_ms: float


def usage_cost(table: TierTable, model: str, prompt_tokens: int, completion_tokens: int,
               response: Any = None) -> float:
    """Cost from the catalog prices in tiers.yaml, else LiteLLM's own price map."""
    dep = table.find(model)
    if dep is not None:
        return dep.input_cost_per_token * prompt_tokens + dep.output_cost_per_token * completion_tokens
    import litellm

    try:
        if response is not None:
            return float(litellm.completion_cost(completion_response=response))
        p, c = litellm.cost_per_token(model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return float(p + c)
    except Exception:
        return 0.0


class TierRouter:
    def __init__(self, settings: Settings, table: TierTable):
        import litellm  # deferred: heavy import

        self.table = table
        self.router = litellm.Router(
            model_list=table.model_list,
            fallbacks=table.fallbacks,
            num_retries=settings.num_retries,
            timeout=settings.timeout_s,
        )

    def complete(self, tier: str, messages: list[dict[str, Any]], **kwargs: Any) -> Execution:
        t0 = time.perf_counter()
        try:
            resp = self.router.completion(model=tier, messages=messages, **kwargs)
        except Exception as e:
            raise RoutingError(f"all deployments failed for tier {tier!r}: {e.__class__.__name__}: {e}") from e
        llm_ms = (time.perf_counter() - t0) * 1000

        hidden = getattr(resp, "_hidden_params", {}) or {}
        deployment_id = hidden.get("model_id")
        model = self._deployment_model(deployment_id) or getattr(resp, "model", "") or ""
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        return Execution(
            response=resp,
            tier=deployment_id.rsplit("-", 1)[0] if deployment_id else tier,
            model=model,
            deployment_id=deployment_id,
            fallback_used=deployment_id != f"{tier}-1",
            prompt_tokens=pt,
            completion_tokens=ct,
            usd=usage_cost(self.table, model, pt, ct, resp),
            llm_ms=llm_ms,
        )

    def _deployment_model(self, deployment_id: str | None) -> str | None:
        for d in self.table.model_list:
            if (d.get("model_info") or {}).get("id") == deployment_id:
                return d["litellm_params"]["model"]
        return None
