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

        # 1. Hard rules that stop the request outright.
        blocked = rules.blocked_classes(data_classes, text, self.s.blocked_data_classes)
        if blocked:
            d = Decision(HUMAN_REVIEW, "rule", prompt_tokens, [f"blocked data class: {', '.join(sorted(blocked))}"])
            d.decision_ms = (time.perf_counter() - t0) * 1000
            return d
        rules.check_budget(project_id, project.monthly_budget_usd, self.spend_lookup(project_id))

        # 2. One Jev call names the tier and the flags.
        state = build_state(messages, project_id, prompt_tokens, project.send_raw_prompt, summary,
                            self.s.state_prompt_chars, bool(tools))
        try:
            ans = self.jev.decide(state)
            d = Decision(ans.choice, "jev", prompt_tokens, jev_version=ans.model, jev_choice=ans.choice,
                         jev_confidence=ans.confidence, probabilities=ans.probabilities, flags=ans.flags)
            d.flags_set = {k for k, p in ans.flags.items() if p >= self.s.flag_threshold}
        except JevUnavailable as e:
            d = Decision(self.s.default_tier, "default", prompt_tokens, [f"jev unavailable: {e}"])
        if tools:
            d.flags_set.add("needs_tools")

        # 3. Sensitive -> human review; low confidence -> one tier up.
        if d.flags.get("is_sensitive", 0.0) >= self.s.sensitive_threshold:
            d.reasons.append(f"is_sensitive={d.flags['is_sensitive']:.2f}")
            d.tier = HUMAN_REVIEW
        elif d.source == "jev" and d.tier != HUMAN_REVIEW:
            threshold = self.s.confidence_thresholds.get(d.tier, 0.0)
            if (d.jev_confidence or 0.0) < threshold:
                new = self.s.next_tier(d.tier)
                d.reasons.append(f"confidence {d.jev_confidence:.2f} < {threshold} on {d.tier} -> {new}")
                d.tier = new
        if "needs_current_info" in d.flags_set:
            d.reasons.append("needs_current_info set (logged; no live-data tier in Phase 1)")

        # 4. Context / capability floor, computed in code. Flags Jev inferred are soft:
        # if no tier satisfies them, retry with only what the request provably needs.
        if d.tier != HUMAN_REVIEW:
            hard_context = prompt_tokens + (max_output_tokens or self.s.max_output_tokens_default)
            soft_context = hard_context
            if "needs_long_context" in d.flags_set:
                soft_context = max(hard_context, self.s.long_context_tokens)
            hard_tools = bool(tools)
            soft_tools = "needs_tools" in d.flags_set
            tier, why = self._floor(d.tier, soft_context, soft_tools)
            if tier == HUMAN_REVIEW and (soft_context, soft_tools) != (hard_context, hard_tools):
                tier, why = self._floor(d.tier, hard_context, hard_tools)
                why.insert(0, "inferred long-context/tools requirement unsatisfiable; relaxed")
            d.tier = tier
            d.reasons.extend(why)

        d.decision_ms = (time.perf_counter() - t0) * 1000
        return d

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
