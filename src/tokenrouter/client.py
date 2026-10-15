"""TokenRouter: the library entry point an agent calls instead of litellm.completion."""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.config import HUMAN_REVIEW, Settings, load_settings
from tokenrouter.decision.decider import Decider, Decision
from tokenrouter.decision.jev import JevClient
from tokenrouter.decision.rules import messages_text
from tokenrouter.execution.router import Execution, RoutingError, TierRouter, usage_cost
from tokenrouter.log import DecisionLog, RequestRecord, prompt_hash

log = logging.getLogger("tokenrouter")


class HumanReviewRequired(RuntimeError):
    def __init__(self, request_id: int, reasons: list[str], decision: Decision | None = None):
        super().__init__(f"request {request_id} queued for human review: {'; '.join(reasons)}")
        self.request_id, self.reasons, self.decision = request_id, reasons, decision


@dataclass
class RouteResult:
    request_id: int
    response: Any                 # litellm ModelResponse
    decision: Decision
    tier: str                     # tier that served (live) or would have served (shadow)
    model: str                    # model that produced `response`
    usd: float
    latency_ms: float
    fallback_used: bool = False

    @property
    def text(self) -> str:
        return self.response.choices[0].message.content or ""


def _as_messages(prompt: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt


class TokenRouter:
    """Route each request to the cheapest adequate tier.

    mode="live":   Jev picks the tier and LiteLLM serves it.
    mode="shadow": the fixed `baseline_model` serves every request; the decision runs
                   alongside and is logged with the routed tier's estimated cost. With
                   `shadow_execute_rate` > 0 a sample is also sent to the routed tier and
                   both answers are stored for grading.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        mode: str = "live",
        baseline_model: str | None = None,
        shadow_execute_rate: float = 0.0,
        baseline_kwargs: dict[str, Any] | None = None,
        log_mode: str | None = None,
        jev: JevClient | None = None,
        table: TierTable | None = None,
        decision_log: DecisionLog | None = None,
        router: TierRouter | None = None,
    ):
        if mode not in ("live", "shadow"):
            raise ValueError("mode must be 'live' or 'shadow'")
        if mode == "shadow" and not baseline_model:
            raise ValueError("shadow mode needs baseline_model")
        self.s = settings or load_settings()
        self.mode = mode
        self.baseline_model = baseline_model
        self.shadow_execute_rate = shadow_execute_rate
        self.baseline_kwargs = baseline_kwargs or {}  # e.g. api_base for the baseline model
        self.log_mode = log_mode or mode                # e.g. "sim" for playground runs with mock models
        self.table = table or TierTable.load(self.s.tiers_file)
        self.log = decision_log or DecisionLog(self.s.log_db)
        self.decider = Decider(self.s, self.table, jev=jev, spend_lookup=self.log.month_spend)
        self.router = router or TierRouter(self.s, self.table)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tokenrouter")

    def decide(self, prompt: str | list[dict[str, Any]], project_id: str = "default", **kw: Any) -> Decision:
        """Dry run: the tier decision only, no model call, nothing logged."""
        return self.decider.decide(_as_messages(prompt), project_id, **kw)

    def complete(
        self,
        prompt: str | list[dict[str, Any]],
        project_id: str = "default",
        data_classes: tuple[str, ...] | list[str] = (),
        summary: str | None = None,
        **litellm_kwargs: Any,
    ) -> RouteResult:
        messages = _as_messages(prompt)
        decide_kw = dict(project_id=project_id, data_classes=data_classes, summary=summary,
                         tools=litellm_kwargs.get("tools"), max_output_tokens=litellm_kwargs.get("max_tokens"))
        if self.mode == "shadow":
            return self._shadow(messages, decide_kw, litellm_kwargs)
        return self._live(messages, decide_kw, litellm_kwargs)

    # -- live ---------------------------------------------------------------------

    def _live(self, messages: list[dict], decide_kw: dict, kwargs: dict) -> RouteResult:
        t0 = time.perf_counter()
        decision = self.decider.decide(messages, **decide_kw)  # BudgetExceeded propagates
        rec = self._record(messages, decide_kw["project_id"], decision)

        if decision.needs_human:
            rec.latency_ms = (time.perf_counter() - t0) * 1000
            rid = self.log.write(rec)
            self.log.enqueue_review(rid, decide_kw["project_id"], decision.reasons, messages)
            raise HumanReviewRequired(rid, decision.reasons, decision)

        try:
            ex = self.router.complete(decision.tier, messages, **kwargs)
        except RoutingError as e:
            rec.error = str(e)[:500]
            rec.latency_ms = (time.perf_counter() - t0) * 1000
            self.log.write(rec)
            e.decision = decision
            raise
        self._fill_execution(rec, ex)
        rec.baseline_model = self.s.baseline_model
        rec.baseline_usd = self.s.baseline_cost(ex.prompt_tokens, ex.completion_tokens)
        rec.latency_ms = (time.perf_counter() - t0) * 1000
        rid = self.log.write(rec)
        return RouteResult(rid, ex.response, decision, ex.tier, ex.model, ex.usd, rec.latency_ms, ex.fallback_used)

    # -- shadow -------------------------------------------------------------------

    def _shadow(self, messages: list[dict], decide_kw: dict, kwargs: dict) -> RouteResult:
        import litellm

        t0 = time.perf_counter()
        # The decision runs in parallel and can never break the baseline call.
        fut = self._pool.submit(self._safe_decide, messages, decide_kw)
        resp = litellm.completion(model=self.baseline_model, messages=messages, **{**kwargs, **self.baseline_kwargs})
        llm_ms = (time.perf_counter() - t0) * 1000
        decision = fut.result()

        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        if self.baseline_model == self.s.baseline_model:
            baseline_usd = self.s.baseline_cost(pt, ct)
        else:
            baseline_usd = usage_cost(self.table, self.baseline_model, pt, ct, resp)

        rec = self._record(messages, decide_kw["project_id"], decision, mode="shadow")
        rec.model = self.baseline_model
        rec.prompt_tokens, rec.completion_tokens = pt, ct
        rec.usd = baseline_usd
        rec.baseline_model, rec.baseline_usd = self.baseline_model, baseline_usd
        rec.llm_ms = llm_ms
        primary = self.table.primary(decision.tier) if not decision.needs_human else None
        if primary is not None:
            rec.routed_est_usd = primary.input_cost_per_token * pt + primary.output_cost_per_token * ct
        rec.latency_ms = (time.perf_counter() - t0) * 1000

        routed: Execution | None = None
        if primary is not None and random.random() < self.shadow_execute_rate:
            try:
                routed = self.router.complete(decision.tier, messages, **kwargs)
                rec.routed_est_usd = routed.usd
            except RoutingError as e:
                rec.error = f"shadow routed call failed: {e}"[:500]
        rid = self.log.write(rec)
        if routed is not None:
            self.log.add_eval_pair(
                rid, messages_text(messages), self.baseline_model, resp.choices[0].message.content or "",
                decision.tier, routed.model, routed.response.choices[0].message.content or "",
            )
        return RouteResult(rid, resp, decision, decision.tier, self.baseline_model, baseline_usd, rec.latency_ms)

    def _safe_decide(self, messages: list[dict], decide_kw: dict) -> Decision:
        try:
            return self.decider.decide(messages, **decide_kw)
        except Exception as e:  # shadow mode must not fail the real request
            log.warning("shadow decision failed: %s", e)
            return Decision(HUMAN_REVIEW, "rule", 0, [f"decision error: {e}"])

    # -- helpers ------------------------------------------------------------------

    def _record(self, messages: list[dict], project_id: str, d: Decision, mode: str | None = None) -> RequestRecord:
        primary = self.table.primary(d.tier) if not d.needs_human else None
        return RequestRecord(
            project_id=project_id,
            mode=mode or self.log_mode,
            prompt_hash=prompt_hash(messages),
            prompt_tokens=d.prompt_tokens,
            jev_version=d.jev_version,
            jev_choice=d.jev_choice,
            jev_confidence=d.jev_confidence,
            probabilities=d.probabilities,
            flags=d.flags,
            decision_source=d.source,
            reasons=d.reasons,
            tier=d.tier,
            primary_model=primary.litellm_name if primary else None,
            decision_ms=d.decision_ms,
        )

    @staticmethod
    def _fill_execution(rec: RequestRecord, ex: Execution) -> None:
        rec.model = ex.model
        rec.tier = ex.tier
        rec.fallback_used = ex.fallback_used
        rec.prompt_tokens = ex.prompt_tokens or rec.prompt_tokens
        rec.completion_tokens = ex.completion_tokens
        rec.usd = ex.usd
        rec.llm_ms = ex.llm_ms

    def close(self, close_log: bool = True) -> None:
        self._pool.shutdown(wait=False)
        if close_log:
            self.log.close()
