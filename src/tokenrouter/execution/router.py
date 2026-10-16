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


def is_empty_truncated(resp: Any) -> bool:
    """No text and no tool call because the output limit was hit.

    Reasoning models can spend the whole max_tokens budget thinking and return nothing.
    """
    try:
        choice = resp.choices[0]
    except (AttributeError, IndexError):
        return False
    msg = choice.message
    return (choice.finish_reason == "length" and not (msg.content or "").strip()
            and not getattr(msg, "tool_calls", None))


class TierRouter:
    def __init__(self, settings: Settings, table: TierTable):
        import litellm  # deferred: heavy import

        self.table = table
        self.tier_names = settings.tier_names
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
        deployment_id = (getattr(resp, "_hidden_params", {}) or {}).get("model_id")
        ex = self._execution(tier, resp, deployment_id, (time.perf_counter() - t0) * 1000)
        if is_empty_truncated(resp):
            ex = self._continue_after_empty(tier, ex, messages, kwargs, t0)
        return ex

    def _continue_after_empty(self, tier: str, first: Execution, messages: list[dict[str, Any]],
                              kwargs: dict[str, Any], t0: float) -> Execution:
        """An empty, truncated answer is a failure: try the rest of the chain in order."""
        import litellm

        wasted = first.usd
        chain = self.chain(tier)
        ids = [d["model_info"]["id"] for d in chain]
        start = ids.index(first.deployment_id) + 1 if first.deployment_id in ids else len(ids)
        for d in chain[start:]:
            params = {k: v for k, v in d["litellm_params"].items() if k != "order"}
            try:
                resp = litellm.completion(messages=messages, **{**params, **kwargs})
            except Exception:
                continue
            ex = self._execution(tier, resp, d["model_info"]["id"], (time.perf_counter() - t0) * 1000)
            if is_empty_truncated(resp):
                wasted += ex.usd
                continue
            ex.fallback_used = True
            ex.usd += wasted
            return ex
        raise RoutingError(
            f"every model from {first.deployment_id} on returned an empty answer cut off at max_tokens; "
            "raise max_tokens (reasoning models spend output tokens thinking)")

    def chain(self, tier: str) -> list[dict[str, Any]]:
        """Deployments in the order they are tried: the tier by rank, then each tier up."""
        names = self.tier_names
        tiers = names[names.index(tier):] if tier in names else [tier]
        out: list[dict[str, Any]] = []
        for t in tiers:
            out += sorted((d for d in self.table.model_list if d["model_name"] == t),
                          key=lambda d: d["litellm_params"].get("order", 99))
        return out

    def _execution(self, tier: str, resp: Any, deployment_id: str | None, llm_ms: float) -> Execution:
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
