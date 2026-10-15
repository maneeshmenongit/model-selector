"""Hard rules enforced in code before and after Jev: data classes, budget, context fit.

Jev is weak on numbers, so token counts, budgets and context limits never go to it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from tokenrouter.catalog.tiers_file import TierTable

# Cheap detectors for obvious credentials; callers can also tag data classes explicitly.
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key id
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),   # Anthropic / OpenAI keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),             # GitHub tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),           # Slack tokens
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                  # Google API keys
]


class BudgetExceeded(RuntimeError):
    def __init__(self, project_id: str, spent: float, cap: float):
        super().__init__(f"project {project_id!r} spent ${spent:.4f} of ${cap:.2f} this month")
        self.project_id, self.spent, self.cap = project_id, spent, cap


def messages_text(messages: list[dict[str, Any]]) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):  # OpenAI-style content blocks
            parts.extend(b.get("text", "") for b in content if isinstance(b, dict))
    return "\n".join(parts)


def detect_data_classes(text: str) -> set[str]:
    return {"secrets"} if any(p.search(text) for p in _SECRET_PATTERNS) else set()


def blocked_classes(declared: Iterable[str], text: str, blocked: frozenset[str]) -> set[str]:
    return (set(declared) | detect_data_classes(text)) & blocked


def count_tokens(messages: list[dict[str, Any]], model: str | None = None) -> int:
    import litellm

    try:
        return int(litellm.token_counter(model=model or "gpt-4o", messages=messages))
    except Exception:
        # Rough fallback: ~4 characters per token.
        return max(1, len(messages_text(messages)) // 4)


def check_budget(project_id: str, cap: float | None, spent: float) -> None:
    if cap is not None and spent >= cap:
        raise BudgetExceeded(project_id, spent, cap)


def tier_fits(
    table: TierTable,
    tier: str,
    needed_context: int,
    needs_tools: bool,
) -> tuple[bool, str]:
    """Does the tier's primary model fit the request? Returns (ok, reason-if-not)."""
    primary = table.primary(tier)
    if primary is None:
        return False, f"{tier}: no models"
    if primary.context_window and primary.context_window < needed_context:
        return False, f"{tier}: {primary.litellm_name} context {primary.context_window} < {needed_context}"
    if needs_tools and not primary.supports_tools:
        return False, f"{tier}: {primary.litellm_name} has no tool support"
    return True, ""
