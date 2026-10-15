"""Minimal Jev (TypeSafe System One) client over HTTP.

POST https://api.typesafe.ai/v1/systemone with a state and typed questions; one
answer comes back per question. See https://docs.typesafe.ai/api.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

JEV_URL = "https://api.typesafe.ai/v1/systemone"


class JevUnavailable(RuntimeError):
    """Any failure to get a usable answer: no key, timeout, HTTP error, bad payload."""


@dataclass(frozen=True)
class JevAnswer:
    model: str                               # versioned id that answered, e.g. jev-1.13.0
    choice: str
    confidence: float
    probabilities: dict[str, float]
    flags: dict[str, float] = field(default_factory=dict)   # noul id -> P(yes)
    input_tokens: int = 0


TIER_CRITERIA = {
    "fast": "Simple, well-defined work: extraction, tagging, classification, formatting, short rewrites.",
    "balanced": "Standard work: summaries, answering from provided context, routine code edits, drafting.",
    "reasoning": "Hard work: multi-step planning, difficult debugging, synthesis across many sources, novel design.",
    "human_review": "Ambiguous, harmful, or high-stakes requests a person should look at before any model answers.",
}

FLAG_QUESTIONS = {
    "needs_tools": "Does completing this request require calling tools or functions, such as running code, searching, or editing files?",
    "needs_long_context": "Does this request require reading a very long document, a whole codebase, or many files at once?",
    "needs_current_info": "Does answering require information about recent events or live data, such as today's news, prices, or schedules?",
    "is_sensitive": "Does this request involve sensitive personal, medical, financial, legal, or security-critical content that a person should review?",
}


def build_questions() -> dict[str, Any]:
    questions: dict[str, Any] = {
        "tier": {
            "type": "choice",
            "instructions": "Which model tier should handle the request in `request`? Pick the cheapest tier that will do it well.",
            "criteria": TIER_CRITERIA,
        }
    }
    for qid, text in FLAG_QUESTIONS.items():
        questions[qid] = {"type": "noul", "instructions": text}
    return questions


class JevClient:
    def __init__(
        self,
        model: str = "jev-1.13.0",
        api_key: str | None = None,
        timeout_s: float = 2.0,
        http: httpx.Client | None = None,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.http = http or httpx.Client(timeout=timeout_s)

    def decide(self, state: dict[str, Any] | str) -> JevAnswer:
        if not self.api_key:
            raise JevUnavailable("TYPESAFE_API_KEY is not set")
        body = {"model": self.model, "state": state, "questions": build_questions()}
        try:
            r = self.http.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise JevUnavailable(f"Jev request failed: {e.__class__.__name__}: {e}") from e
        if r.status_code != 200:
            raise JevUnavailable(f"Jev returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            return parse_response(r.json())
        except (KeyError, TypeError, ValueError) as e:
            raise JevUnavailable(f"unexpected Jev payload: {e}") from e


def parse_response(payload: dict[str, Any]) -> JevAnswer:
    answers = payload["answers"]
    tier = answers["tier"]
    probs = {k: float(v) for k, v in (tier.get("probabilities") or {}).items()}
    choice = tier["choice"]
    if choice not in TIER_CRITERIA:
        raise ValueError(f"unknown tier choice {choice!r}")
    flags = {qid: float(answers[qid]["noul"]) for qid in FLAG_QUESTIONS if qid in answers}
    return JevAnswer(
        model=payload.get("model", ""),
        choice=choice,
        confidence=float(tier.get("confidence", probs.get(choice, 0.0))),
        probabilities=probs,
        flags=flags,
        input_tokens=int((payload.get("usage") or {}).get("input_tokens", 0)),
    )
