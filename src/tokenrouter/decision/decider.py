"""Per-request tier decision: hard rules -> one Jev call -> escalation -> context floor."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.config import HUMAN_REVIEW, Settings
from tokenrouter.decision import rules
from tokenrouter.decision.jev import JevClient, JevUnavailable


@dataclass
class Decision:
    tier: str
    source: str                          # "jev" | "default" | "rule"
    prompt_tokens: int
    reasons: list[str] = field(default_factory=list)
    jev_version: str | None = None
    jev_choice: str | None = None
    jev_confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    flags: dict[str, float] = field(default_factory=dict)
    flags_set: set[str] = field(default_factory=set)
    decision_ms: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)   # one entry per pipeline step

    @property
    def needs_human(self) -> bool:
        return self.tier == HUMAN_REVIEW


def _size_word(tokens: int) -> str:
    # Words, not numbers: Jev is weak on numeric reasoning.
    if tokens < 500:
        return "short"
    if tokens < 4000:
        return "medium"
    if tokens < 32000:
        return "long"
    return "very long"


def build_state(
    messages: list[dict[str, Any]],
    project_id: str,
    prompt_tokens: int,
    send_raw_prompt: bool,
    summary: str | None,
    max_chars: int,
    tools_offered: bool,
) -> dict[str, Any]:
    text = rules.messages_text([m for m in messages if m.get("role") != "system"])
    if send_raw_prompt:
        request = text if len(text) <= max_chars else text[: max_chars // 2] + "\n...\n" + text[-max_chars // 2 :]
    else:
        request = summary or "(content withheld for this project; decide from the metadata only)"
    state: dict[str, Any] = {
        "project": project_id,
        "request": request,
        "request_size": _size_word(prompt_tokens),
        "conversation_turns": "multi-turn" if sum(m.get("role") == "user" for m in messages) > 1 else "single-turn",
        "tools_offered": "yes" if tools_offered else "no",
        "contains_code": "yes" if "```" in text or "def " in text or "function " in text else "no",
    }
    if summary and send_raw_prompt:
        state["summary"] = summary
    return state


class Decider:
    def __init__(
        self,
        settings: Settings,
        table: TierTable,
        jev: JevClient | None = None,
        spend_lookup: Callable[[str], float] | None = None,
    ):
        self.s = settings
        self.table = table
        self.jev = jev if jev is not None else JevClient(settings.jev_model, timeout_s=settings.jev_timeout_s)
        self.spend_lookup = spend_lookup or (lambda _pid: 0.0)
        # Load litellm + the tokenizer now so the first request doesn't pay ~1 s for it.
        rules.count_tokens([{"role": "user", "content": "warm up"}])

    def decide(
        self,
        messages: list[dict[str, Any]],
        project_id: str = "default",
        data_classes: tuple[str, ...] | list[str] = (),
        summary: str | None = None,
        tools: list | None = None,
        max_output_tokens: int | None = None,
    ) -> Decision:
        t0 = time.perf_counter()
        project = self.s.project(project_id)
        prompt_tokens = rules.count_tokens(messages)
        text = rules.messages_text(messages)
        trace: list[dict[str, Any]] = []

        def step(key: str, outcome: str, summary: str, tier: str | None, **data: Any) -> None:
            trace.append({"step": key, "outcome": outcome, "summary": summary, "tier": tier, "data": data})

        def done(d: Decision) -> Decision:
            d.trace = trace
            d.decision_ms = (time.perf_counter() - t0) * 1000
            return d

        # 1. Hard rules that stop the request outright.
        declared = sorted(set(data_classes))
        detected = sorted(rules.detect_data_classes(text))
        blocked = rules.blocked_classes(data_classes, text, self.s.blocked_data_classes)
        if blocked:
            why = f"blocked data class: {', '.join(sorted(blocked))}"
            step("hard_rules", "stop", why, HUMAN_REVIEW, declared=declared, detected=detected,
                 blocked=sorted(blocked), prompt_tokens=prompt_tokens)
            return done(Decision(HUMAN_REVIEW, "rule", prompt_tokens, [why]))
        cap, spent = project.monthly_budget_usd, self.spend_lookup(project_id)
        try:
            rules.check_budget(project_id, cap, spent)
        except rules.BudgetExceeded as e:
            step("hard_rules", "stop", str(e), None, declared=declared, detected=detected,
                 budget_cap=cap, month_spend=spent, prompt_tokens=prompt_tokens)
            e.decision = done(Decision(HUMAN_REVIEW, "rule", prompt_tokens, [str(e)]))
            raise
        step("hard_rules", "pass", "no blocked data, within budget", None, declared=declared,
             detected=detected, budget_cap=cap, month_spend=spent, prompt_tokens=prompt_tokens)

        # 2. One Jev call names the tier and the flags.
        state = build_state(messages, project_id, prompt_tokens, project.send_raw_prompt, summary,
                            self.s.state_prompt_chars, bool(tools))
        t_jev = time.perf_counter()
        try:
            ans = self.jev.decide(state)
            d = Decision(ans.choice, "jev", prompt_tokens, jev_version=ans.model, jev_choice=ans.choice,
                         jev_confidence=ans.confidence, probabilities=ans.probabilities, flags=ans.flags)
            d.flags_set = {k for k, p in ans.flags.items() if p >= self.s.flag_threshold}
            step("jev", "pass", f"Jev chose {ans.choice} ({ans.confidence:.0%} confident)", d.tier,
                 state=state, version=ans.model, choice=ans.choice, confidence=ans.confidence,
                 probabilities=ans.probabilities, flags=ans.flags, flag_threshold=self.s.flag_threshold,
                 ms=(time.perf_counter() - t_jev) * 1000)
        except JevUnavailable as e:
            d = Decision(self.s.default_tier, "default", prompt_tokens, [f"jev unavailable: {e}"])
            step("jev", "fallback", f"Jev unavailable, default tier {self.s.default_tier}", d.tier,
                 state=state, error=str(e), ms=(time.perf_counter() - t_jev) * 1000)
        if tools:
            d.flags_set.add("needs_tools")

        # 3. Sensitive -> human review; low confidence -> one tier up.
        before = d.tier
        threshold = self.s.confidence_thresholds.get(d.tier)
        if d.flags.get("is_sensitive", 0.0) >= self.s.sensitive_threshold:
            d.reasons.append(f"is_sensitive={d.flags['is_sensitive']:.2f}")
            d.tier = HUMAN_REVIEW
            step("escalation", "changed", f"is_sensitive {d.flags['is_sensitive']:.0%} >= "
                 f"{self.s.sensitive_threshold:.0%}: human review", d.tier, before=before,
                 sensitive=d.flags["is_sensitive"], sensitive_threshold=self.s.sensitive_threshold)
        elif d.source == "jev" and d.tier != HUMAN_REVIEW:
            threshold = threshold or 0.0
            if (d.jev_confidence or 0.0) < threshold:
                new = self.s.next_tier(d.tier)
                d.reasons.append(f"confidence {d.jev_confidence:.2f} < {threshold} on {d.tier} -> {new}")
                d.tier = new
                step("escalation", "changed", f"confidence {d.jev_confidence:.0%} below {threshold:.0%} "
                     f"for {before}: one tier up to {new}", d.tier, before=before,
                     confidence=d.jev_confidence, threshold=threshold)
            else:
                step("escalation", "kept", f"confidence {d.jev_confidence:.0%} clears {threshold:.0%} "
                     f"for {before}", d.tier, before=before, confidence=d.jev_confidence, threshold=threshold)
        else:
            step("escalation", "skipped", "no Jev confidence to check" if d.source != "jev"
                 else "Jev chose human review", d.tier, before=before)
        if "needs_current_info" in d.flags_set:
            d.reasons.append("needs_current_info set (logged; no live-data tier in Phase 1)")

        # 4. Context / capability floor, computed in code. Flags Jev inferred are soft:
        # if no tier satisfies them, retry with only what the request provably needs.
        if d.tier != HUMAN_REVIEW:
            before = d.tier
            hard_context = prompt_tokens + (max_output_tokens or self.s.max_output_tokens_default)
            soft_context = hard_context
            if "needs_long_context" in d.flags_set:
                soft_context = max(hard_context, self.s.long_context_tokens)
            hard_tools = bool(tools)
            soft_tools = "needs_tools" in d.flags_set
            tier, why = self._floor(d.tier, soft_context, soft_tools)
            relaxed = False
            if tier == HUMAN_REVIEW and (soft_context, soft_tools) != (hard_context, hard_tools):
                tier, why = self._floor(d.tier, hard_context, hard_tools)
                why.insert(0, "inferred long-context/tools requirement unsatisfiable; relaxed")
                relaxed = True
            d.tier = tier
            d.reasons.extend(why)
            checks = [{"tier": t, "ok": ok, "why": w} for t, ok, w in self._checks(
                before, hard_context if relaxed else soft_context, hard_tools if relaxed else soft_tools)]
            step("context_floor", "changed" if tier != before else "kept",
                 f"{before} fits" if tier == before else f"{before} cannot serve it: moved to {tier}", tier,
                 before=before, needed_context=hard_context if relaxed else soft_context,
                 needs_tools=hard_tools if relaxed else soft_tools, relaxed=relaxed, checks=checks)
        else:
            step("context_floor", "skipped", "routed to human review", d.tier)

        return done(d)

    def _checks(self, start: str, needed_context: int, needs_tools: bool) -> list[tuple[str, bool, str]]:
        names = self.s.tier_names
        out = []
        for t in names[names.index(start):] if start in names else []:
            ok, why = rules.tier_fits(self.table, t, needed_context, needs_tools)
            out.append((t, ok, why))
            if ok:
                break
        return out

    def _floor(self, tier: str, needed_context: int, needs_tools: bool) -> tuple[str, list[str]]:
        reasons: list[str] = []
        while tier != HUMAN_REVIEW:
            ok, why = rules.tier_fits(self.table, tier, needed_context, needs_tools)
            if ok:
                break
            new = self.s.next_tier(tier)
            reasons.append(f"{why} -> {new}")
            tier = new
        return tier, reasons
